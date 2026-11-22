#!/usr/bin/env python3
"""Rehearse a Beads or Dolt upgrade on a cold copy of the live database.

The live Dolt server is stopped only long enough to copy `.beads`. Every bd and
dolt command after that runs against the copy, in a scratch project with its
own HOME, port and git repository, and with no Dolt remotes."""

from __future__ import annotations

import argparse
import contextlib
import csv
import datetime
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


ASSETS = Path(__file__).resolve().parent
REPO_ROOT = ASSETS.parent
IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")
VERSION_LINE = r"(?m)^{name} version\s+v?([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)"
COPIED_FILES = ("metadata.json", "config.yaml", ".local_version", ".gitignore")
SKIPPED_DIRECTORIES = frozenset({"git-remote-cache"})
SERVER_STATE_FILES = frozenset({"sql-server.info", "sql-server.lock"})
LOCAL_HOSTS = frozenset({"", "127.0.0.1", "localhost"})
RESTORE_MARKER = "beads-rehearsal-restore-point.json"
HOOK_TIMEOUT_SECONDS = 5.0
COMMAND_TIMEOUT = 900
LIVE_STOP_WAIT = 60.0
SCRATCH_STOP_WAIT = 20.0
STOP_SERVER_REFUSAL = (
    "the live Dolt server is running. A consistent copy needs it stopped for the "
    "duration of the copy; rerun with --stop-server to allow that. bd commands in "
    "other sessions fail or wait while it is down."
)
PROBE_TITLE = "upgrade rehearsal write probe"
PROBE_REASON = "Fixed with commit(s) rehearsal"


class Refused(Exception):
    """The rehearsal cannot start or must stop before touching more state."""


def say(message: str, *, error: bool = False) -> None:
    """Print without failing when the terminal is gone, so cleanup and the report still run."""
    stream = sys.stderr if error else sys.stdout
    try:
        print(message, file=stream, flush=True)
    except (OSError, ValueError):
        with contextlib.suppress(OSError, ValueError, AttributeError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), stream.fileno())


def load_helper(name: str, module: str) -> Any:
    spec = importlib.util.spec_from_file_location(module, ASSETS / name)
    if spec is None or spec.loader is None:
        raise Refused(f"cannot load {name}")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


@dataclass
class Report:
    checks: list[dict[str, str]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def record(self, name: str, ok: bool, detail: str = "") -> bool:
        status = "PASS" if ok else "FAIL"
        self.checks.append({"name": name, "status": status, "detail": detail})
        say(f"{status}: {name}" + (f": {detail}" if detail else ""))
        return ok

    def info(self, message: str) -> None:
        say(f"INFO: {message}")

    @property
    def failed(self) -> bool:
        return any(check["status"] == "FAIL" for check in self.checks)


@dataclass
class Completed:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float

    @property
    def tail(self) -> str:
        text = (self.stderr.strip() or self.stdout.strip()).splitlines()
        return " | ".join(text[-3:])[:600]


TERMINATION_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP) if os.name != "nt" else ()


def default_signals() -> None:
    for number in TERMINATION_SIGNALS:
        signal.signal(number, signal.SIG_DFL)


def run(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float = COMMAND_TIMEOUT,
    stdin: str | None = None, detached: bool = False,
) -> Completed:
    """Run a command. `detached` gives it its own session and default signal handling."""
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv, cwd=cwd, env=env, capture_output=True, encoding="utf-8",
            errors="replace", timeout=timeout,
            input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
            start_new_session=detached, preexec_fn=default_signals if detached else None,
        )
    except subprocess.TimeoutExpired:
        return Completed(argv, 124, "", f"timed out after {timeout:g}s", time.monotonic() - started)
    except OSError as error:
        return Completed(argv, 127, "", str(error), time.monotonic() - started)
    return Completed(
        argv, completed.returncode, completed.stdout, completed.stderr,
        time.monotonic() - started,
    )


def resolve_installed(tool: str, project: Path) -> Path:
    home = Path.home()
    candidates = [
        shutil.which(tool),
        str(home / ".local/bin" / tool),
        str(Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "mise/shims" / tool),
    ]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK) and Path(candidate).is_file():
            return unshim(tool, Path(candidate), project)
    raise Refused(f"{tool} is not installed on PATH or in the managed locations")


def unshim(tool: str, binary: Path, project: Path) -> Path:
    """A mise shim needs the user's HOME; the scratch project must link the real binary."""
    if binary.resolve().name != "mise":
        return binary
    located = run(
        [str(binary.resolve()), "which", tool], cwd=project, env=dict(os.environ), timeout=60
    )
    real = Path(located.stdout.strip())
    if located.returncode != 0 or not real.is_absolute() or not os.access(real, os.X_OK):
        raise Refused(f"cannot resolve the mise shim for {tool}: {located.tail}")
    return real


def tool_version(tool: str, binary: Path, env: dict[str, str], cwd: Path) -> str:
    completed = run([str(binary), "version"], cwd=cwd, env=env, timeout=60)
    match = re.search(VERSION_LINE.format(name=re.escape(tool)), completed.stdout)
    if completed.returncode != 0 or match is None:
        raise Refused(f"{binary} version did not report a version: {completed.tail}")
    return match.group(1)


def unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "schema_version" in payload and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]
    return payload


def find_project(start: Path) -> Path:
    start = start.resolve()
    if (start / ".beads" / "dolt").is_dir():
        return start
    completed = run(
        ["git", "-C", str(start), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=start, env=dict(os.environ), timeout=30,
    )
    main = Path(completed.stdout.strip()).parent if completed.returncode == 0 else None
    if main and (main / ".beads" / "dolt").is_dir():
        return main
    raise Refused(
        f"no .beads/dolt under {start} or its main worktree; run this on the machine "
        "that hosts the Dolt database, or pass --project"
    )


def read_metadata(project: Path) -> tuple[str, str]:
    try:
        metadata = json.loads((project / ".beads" / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Refused(f"cannot read .beads/metadata.json: {error}") from error
    database = str(metadata.get("dolt_database") or "")
    user = str(metadata.get("dolt_server_user") or "root")
    if metadata.get("dolt_mode") != "server":
        raise Refused("this Beads project is not in Dolt server mode")
    if str(metadata.get("dolt_server_host") or "") not in LOCAL_HOSTS:
        raise Refused("metadata.json names a non-local Dolt server; refusing to copy it")
    if not IDENTIFIER.fullmatch(database) or not IDENTIFIER.fullmatch(user):
        raise Refused("unexpected dolt_database or dolt_server_user in metadata.json")
    if not (project / ".beads" / "dolt" / database / ".dolt").is_dir():
        raise Refused(f"no Dolt database named {database} under .beads/dolt")
    return database, user


def server_pid(beads: Path) -> int | None:
    try:
        pid = int((beads / "dolt-server.pid").read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid


def server_port(beads: Path) -> int | None:
    try:
        return int((beads / "dolt-server.port").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def directory_bytes(path: Path) -> int:
    total = 0
    for root, directories, files in os.walk(path):
        directories[:] = [name for name in directories if name not in SKIPPED_DIRECTORIES]
        for name in files:
            with contextlib.suppress(OSError):
                total += (Path(root) / name).lstat().st_size
    return total


def refuse_unsafe_source(dolt_dir: Path) -> None:
    """A symlink would make the copy share files with the live database."""
    for root, directories, files in os.walk(dolt_dir):
        directories[:] = [name for name in directories if name not in SKIPPED_DIRECTORIES]
        for name in (*directories, *files):
            if (Path(root) / name).is_symlink():
                raise Refused(f"{Path(root) / name} is a symlink; refusing to copy the database")


def copy_beads(source: Path, destination: Path) -> None:
    """Copy the database and the state files bd needs to recognise the workspace."""
    refuse_unsafe_source(source / "dolt")
    destination.mkdir(parents=True, mode=0o700)
    shutil.copytree(
        source / "dolt", destination / "dolt", symlinks=True,
        ignore=lambda _directory, names: [
            name for name in names if name in SKIPPED_DIRECTORIES | SERVER_STATE_FILES
        ],
    )
    for name in COPIED_FILES:
        if (source / name).is_file():
            shutil.copy2(source / name, destination / name)


@contextlib.contextmanager
def live_server_stopped(
    project: Path, bd: Path, allow_stop: bool, report: Report
) -> Iterator[None]:
    """Hold the live server down for a copy and always bring it back if it was up."""
    beads = project / ".beads"
    env = {**os.environ, "BD_NO_REMOTE_ADOPT": "1", "BD_EXPORT_GIT_ADD": "false"}
    was_running = server_pid(beads) is not None
    if was_running and not allow_stop:
        raise Refused(STOP_SERVER_REFUSAL)
    try:
        if was_running:
            stopped = run([str(bd), "dolt", "stop"], cwd=project, env=env, timeout=120)
            deadline = time.monotonic() + LIVE_STOP_WAIT
            while server_pid(beads) is not None and time.monotonic() < deadline:
                time.sleep(0.5)
            if server_pid(beads) is not None:
                raise Refused(f"could not stop the live Dolt server: {stopped.tail}")
            report.info("live Dolt server stopped for the copy")
        yield
        if server_pid(beads) is not None:
            raise Refused(
                "the live Dolt server started again during the copy, so the copy may "
                "be inconsistent; rerun when no other session is using bd"
            )
    finally:
        if was_running:
            held = {
                number: signal.signal(number, signal.SIG_IGN) for number in TERMINATION_SIGNALS
            }
            try:
                started = (
                    run(
                        [str(bd), "dolt", "start"], cwd=project, env=env, timeout=180,
                        detached=True,
                    )
                    if server_pid(beads) is None
                    else Completed([], 0, "", "", 0.0)
                )
                answered = run(
                    [str(bd), "list", "--limit", "1"], cwd=project, env=env, timeout=180,
                    detached=True,
                )
                ok = answered.returncode == 0 and server_pid(beads) is not None
                if not report.record(
                    "live Dolt server restarted and answering", ok,
                    "" if ok else
                    f"start it by hand with: (cd {project} && bd dolt start). "
                    f"{started.tail} {answered.tail}",
                ):
                    report.facts["live_restart_failed"] = True
            finally:
                for number, handler in held.items():
                    signal.signal(number, handler)


def take_restore_point(project: Path, state: Path, stamp: str, manifest: dict[str, Any]) -> Path:
    """Copy `.beads` aside. The marker is written first so a failed copy is still prunable."""
    root = state / "restore-points"
    target = root / stamp
    target.mkdir(parents=True)
    marker = target / RESTORE_MARKER
    marker.write_text(json.dumps({**manifest, "complete": False}) + "\n", encoding="utf-8")
    copy_beads(project / ".beads", target / ".beads")
    marker.write_text(
        json.dumps({**manifest, "complete": True}, indent=2) + "\n", encoding="utf-8"
    )
    return target


def discard_untrusted_copy(target: Path, project: Path) -> None:
    """Delete the restore point at target only if its marker names this project and run."""
    marker = target / RESTORE_MARKER
    try:
        manifest = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if (
        isinstance(manifest, dict)
        and manifest.get("project") == str(project)
        and manifest.get("taken") == target.name
    ):
        with contextlib.suppress(OSError):
            marker.unlink()
        shutil.rmtree(target, ignore_errors=True)


def prune_restore_points(state: Path, keep: Path, project: Path) -> None:
    """Delete this project's older restore points; anything unrecognised is left alone."""
    for older in sorted((state / "restore-points").iterdir()):
        if older == keep or older.is_symlink() or not older.is_dir():
            continue
        try:
            manifest = json.loads((older / RESTORE_MARKER).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(manifest, dict) and manifest.get("project") == str(project):
            shutil.rmtree(older)


def strip_remotes(dolt: Path, beads: Path, env: dict[str, str]) -> None:
    """Remove every Dolt remote from the copy and refuse to continue if one remains."""
    for database in sorted((beads / "dolt").iterdir()):
        if not (database / ".dolt").is_dir():
            continue
        if any((database / ".dolt").rglob("sql-server.info")):
            raise Refused(
                f"the copy of {database.name} carries a running server's state file; "
                "refusing to run dolt against it"
            )
        listed = run([str(dolt), "remote", "-v"], cwd=database, env=env, timeout=120)
        if listed.returncode != 0:
            raise Refused(f"cannot list Dolt remotes in the copy: {listed.tail}")
        for name in sorted({line.split()[0] for line in listed.stdout.splitlines() if line.split()}):
            removed = run([str(dolt), "remote", "remove", name], cwd=database, env=env, timeout=120)
            if removed.returncode != 0:
                raise Refused(f"cannot remove Dolt remote {name} from the copy: {removed.tail}")
        remaining = run([str(dolt), "remote", "-v"], cwd=database, env=env, timeout=120)
        if remaining.returncode != 0 or remaining.stdout.strip():
            raise Refused(
                f"the copy of {database.name} still has a Dolt remote; refusing to run "
                "an upgrade rehearsal that could push"
            )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Scratch:
    """A throwaway Beads project around the copy, driven by one pair of binaries."""

    def __init__(self, run_dir: Path, database: str, user: str, live_port: int | None) -> None:
        self.run_dir = run_dir
        self.project = run_dir / "project"
        self.beads = self.project / ".beads"
        self.database = database
        self.user = user
        self.live_port = live_port
        self.bin: Path | None = None
        self.pid: int | None = None

    def env(self) -> dict[str, str]:
        if self.bin is None:
            raise Refused("the scratch project has no binaries selected yet")
        return {
            "HOME": str(self.run_dir / "home"),
            "TMPDIR": str(self.run_dir / "tmp"),
            "PATH": os.pathsep.join((str(self.bin), "/usr/bin", "/bin")),
            "LANG": "C.UTF-8",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "BD_NO_REMOTE_ADOPT": "1",
            "BD_EXPORT_GIT_ADD": "false",
            "BD_DISABLE_METRICS": "1",
        }

    def use(self, label: str, bd: Path, dolt: Path) -> None:
        self.bin = self.run_dir / f"bin-{label}"
        self.bin.mkdir()
        links = {"bd": bd, "dolt": dolt, "python3": Path(sys.executable)}
        for helper in ("bash", "git"):
            found = shutil.which(helper)
            if found:
                links[helper] = Path(found)
        for name, target in links.items():
            (self.bin / name).symlink_to(target.resolve())

    def prepare(self) -> None:
        for name in ("home/.dolt", "tmp"):
            (self.run_dir / name).mkdir(parents=True)
        (self.run_dir / "home/.dolt/config_global.json").write_text(
            json.dumps({"metrics.disabled": "true"}), encoding="utf-8"
        )
        (self.project / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copy2(ASSETS / "beads-sync.sh", self.project / "assets" / "beads-sync.sh")
        (self.beads / "config.local.yaml").write_text(
            f"dolt.port: {free_port()}\n", encoding="utf-8"
        )
        for argv in (
            ["git", "init", "-q", "-b", "main", "."],
            [
                "git", "-c", "user.name=Rehearsal", "-c", "user.email=rehearsal@example.invalid",
                "commit", "-q", "--allow-empty", "-m", "rehearsal scratch project",
            ],
        ):
            done = self.run(argv)
            if done.returncode != 0:
                raise Refused(f"cannot create the scratch git repository: {done.tail}")

    def run(self, argv: list[str], **kwargs: Any) -> Completed:
        return run(argv, cwd=self.project, env=self.env(), **kwargs)

    def bd(self, *args: str, **kwargs: Any) -> Completed:
        return self.run(["bd", *args], **kwargs)

    def is_scratch_server(self, pid: int) -> bool | None:
        """True or False when ps describes the pid; None when it gives no answer."""
        command = run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            cwd=self.run_dir, env=self.env(), timeout=30,
        )
        if command.returncode != 0 or not command.stdout.strip():
            return None
        return str(self.project) in command.stdout

    def start_server(self) -> None:
        """Start the scratch server and prove it is not the live one before using it."""
        started = self.bd("dolt", "start", timeout=180)
        pid, port = server_pid(self.beads), server_port(self.beads)
        ours = self.is_scratch_server(pid) if pid is not None else False
        if ours:
            self.pid = pid
        if pid is not None and not ours:
            raise Refused(
                f"the scratch project resolved a Dolt server (pid {pid}, port {port}) "
                "that could not be shown to serve the scratch directory. Nothing was "
                "stopped; check that the live server is still healthy."
            )
        if started.returncode != 0 or pid is None or port is None:
            self.stop_server()
            raise Refused(f"the scratch Dolt server did not start: {started.tail}")
        if port == self.live_port:
            self.stop_server()
            raise Refused(
                f"the scratch Dolt server came up on the live port {port}; it was "
                "stopped and the proposed binaries were not run against it"
            )
        remotes = self.scalar("select count(*) from dolt_remotes")
        if remotes != "0":
            self.stop_server()
            raise Refused(
                f"the scratch database reports {remotes or 'an unreadable number of'} "
                "Dolt remote(s) after start; refusing to continue"
            )

    def stop_server(self) -> bool:
        """Stop the server this object started, killing it if bd cannot; never any other."""
        pid = self.pid
        if pid is None:
            return True
        self.bd("dolt", "stop", timeout=120)
        for sig in (None, signal.SIGTERM, signal.SIGKILL):
            if sig is not None and self.is_scratch_server(pid):
                with contextlib.suppress(OSError):
                    os.kill(pid, sig)
            deadline = time.monotonic() + SCRATCH_STOP_WAIT
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except OSError:
                    self.pid = None
                    return True
                if self.is_scratch_server(pid) is False:
                    self.pid = None
                    return True
                time.sleep(0.5)
        return False

    def sql(self, query: str) -> list[list[str]]:
        port = server_port(self.beads)
        done = self.run(
            [
                "dolt", "--host", "127.0.0.1", "--port", str(port), "--user", self.user,
                "--password", "", "--no-tls", "--use-db", self.database,
                "sql", "-r", "csv", "-q", query,
            ],
            timeout=300,
        )
        if done.returncode != 0:
            raise Refused(f"query failed on the scratch copy ({query[:60]}): {done.tail}")
        return list(csv.reader(io.StringIO(done.stdout)))[1:]

    def scalar(self, query: str) -> str:
        rows = self.sql(query)
        return rows[0][0] if rows and rows[0] else ""

    def snapshot(self, export_to: Path) -> dict[str, Any]:
        exported = self.bd("export", "--all", "-o", str(export_to))
        if exported.returncode != 0 or not export_to.is_file():
            raise Refused(f"bd export failed on the scratch copy: {exported.tail}")
        tables = [
            row[0]
            for row in self.sql(
                "select table_name from information_schema.tables "
                f"where table_schema = '{self.database}' and table_type = 'BASE TABLE' "
                "order by table_name"
            )
            if row and IDENTIFIER.fullmatch(row[0])
        ]
        return {
            "bd_version": self.scalar(
                "select value from local_metadata where `key` = 'bd_version'"
            ),
            "schema_version": self.scalar("select max(version) from schema_migrations"),
            "rows": {
                table: int(self.scalar(f"select count(*) from `{table}`")) for table in tables
            },
            "foreign_keys": sorted(
                f"{row[0]}.{row[1]}"
                for row in self.sql(
                    "select table_name, constraint_name "
                    "from information_schema.table_constraints "
                    f"where constraint_schema = '{self.database}' "
                    "and constraint_type = 'FOREIGN KEY'"
                )
                if len(row) == 2
            ),
        }


def load_export(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                raise ValueError(f"{path.name} line {number} is not an issue record")
            records[record["id"]] = record
    return records


def compare_exports(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]], limit: int = 40
) -> tuple[list[str], int]:
    """Return (differences that lose or change data, count of fields only added)."""
    problems: list[str] = []
    added = 0
    for issue in sorted(set(before) - set(after)):
        problems.append(f"{issue} is missing after the upgrade")
    for issue in sorted(set(after) - set(before)):
        problems.append(f"{issue} appeared after the upgrade")
    for issue in sorted(set(before) & set(after)):
        old, new = before[issue], after[issue]
        added += len(set(new) - set(old))
        for key in sorted(old):
            if key not in new:
                problems.append(f"{issue}.{key} was dropped (was {json.dumps(old[key])[:120]})")
            elif old[key] != new[key]:
                problems.append(
                    f"{issue}.{key} changed: {json.dumps(old[key])[:120]} -> "
                    f"{json.dumps(new[key])[:120]}"
                )
    if len(problems) > limit:
        problems = problems[:limit] + [f"... {len(problems) - limit} more difference(s)"]
    return problems, added


def compare_rows(before: dict[str, int], after: dict[str, int]) -> tuple[list[str], list[str]]:
    """Return (tables lost or shrunk, tables added or grown)."""
    losses = [f"table {table} is gone" for table in sorted(set(before) - set(after))]
    gains = [f"table {table} is new" for table in sorted(set(after) - set(before))]
    for table in sorted(set(before) & set(after)):
        if after[table] < before[table]:
            losses.append(f"{table} shrank from {before[table]} to {after[table]} rows")
        elif after[table] > before[table]:
            gains.append(f"{table} grew from {before[table]} to {after[table]} rows")
    return losses, gains


def expected_clone_local_fks() -> set[str]:
    guard = load_helper("check-beads-release-notes.py", "beads_release_guard_for_rehearsal")
    specs = guard.read_sync_clone_local_fks(REPO_ROOT, Path("assets/beads-sync.sh"))
    return {f"{spec[0]}.{spec[1]}" for spec in specs}


def write_probe(scratch: Scratch, report: Report) -> None:
    created = scratch.bd("create", PROBE_TITLE, "--actor", "Rehearsal", "--json")
    try:
        issue = unwrap(json.loads(created.stdout))["id"]
    except (ValueError, KeyError, TypeError):
        report.record("write round-trip", False, f"bd create failed: {created.tail}")
        return
    state = scratch.run_dir / "probe-state.json"
    state.write_text(json.dumps({"id": issue}), encoding="utf-8")
    gate = [sys.executable, str(REPO_ROOT / ".claude/hooks/lib/beads_state.py"), "check",
            "--state-file", str(state)]
    live = scratch.run(gate, timeout=60)
    report.record(
        "plan-approval gate reads an open issue",
        live.returncode == 0, "" if live.returncode == 0 else f"exit {live.returncode}: {live.tail}",
    )
    noted = scratch.bd(
        "update", issue, "--append-notes", "rehearsal note", "--actor", "Rehearsal"
    )
    closed = scratch.bd("close", issue, "--reason", PROBE_REASON, "--actor", "Rehearsal")
    shown = scratch.bd("show", issue, "--json")
    try:
        record = unwrap(json.loads(shown.stdout))
        ok = (
            noted.returncode == 0 and closed.returncode == 0
            and record["status"] == "closed" and record.get("close_reason") == PROBE_REASON
            and "rehearsal note" in (record.get("notes") or "")
        )
        detail = "" if ok else f"read back {json.dumps(record)[:300]}"
    except (ValueError, KeyError, TypeError):
        ok, detail = False, f"bd show failed: {shown.tail}"
    report.record("write round-trip (create, note, close, read back)", ok, detail)
    stale = scratch.run(gate, timeout=60)
    report.record(
        "plan-approval gate reads a closed issue",
        stale.returncode == 10,
        "" if stale.returncode == 10 else f"exit {stale.returncode}: {stale.tail}",
    )


def rehearse(args: argparse.Namespace, report: Report) -> None:
    toolchain = load_helper("beads-toolchain.py", "beads_toolchain_for_rehearsal")
    try:
        pins = (
            toolchain.read_pins(args.repo_root.resolve(), args.ref)
            if not (args.bd and args.dolt)
            else {}
        )
        proposed = {
            tool: toolchain.require_version(chosen or pins[tool], tool)
            for tool, chosen in (("bd", args.bd), ("dolt", args.dolt))
        }
    except toolchain.ToolchainError as error:
        raise Refused(str(error)) from error
    project = find_project(args.project)
    database, user = read_metadata(project)
    installed = {tool: resolve_installed(tool, project) for tool in ("bd", "dolt")}
    live_env = dict(os.environ)
    current = {
        tool: tool_version(tool, binary, live_env, project) for tool, binary in installed.items()
    }
    report.facts.update(project=str(project), database=database, current=current, proposed=proposed)
    report.info(f"live project {project} (database {database})")
    for tool in ("bd", "dolt"):
        change = "unchanged" if current[tool] == proposed[tool] else f"-> {proposed[tool]}"
        report.info(f"{tool} {current[tool]} {change}")
    if current == proposed:
        raise Refused("the proposed pins equal the installed versions; nothing to rehearse")
    if server_pid(project / ".beads") is not None and not args.stop_server:
        raise Refused(STOP_SERVER_REFUSAL)

    state = Path(
        args.state_dir
        or Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        / "dotfiles/beads-rehearsal"
    ).resolve()
    cache = Path(
        args.toolchain_dir
        or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        / "dotfiles/beads-toolchain"
    ).resolve()
    if state.is_relative_to(project):
        raise Refused("the state directory must be outside the Beads project")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stamp = f"{stamp}-{os.getpid()}"
    run_dir = state / "runs" / stamp
    run_dir.mkdir(parents=True)
    report.facts["run_dir"] = str(run_dir)

    binaries = dict(installed)
    for tool in ("bd", "dolt"):
        if proposed[tool] != current[tool]:
            try:
                binaries[tool] = toolchain.install(
                    toolchain.TOOLS[tool], proposed[tool],
                    cache / f"{tool}-{proposed[tool]}", args.timeout,
                )
            except toolchain.ToolchainError as error:
                raise Refused(str(error)) from error

    size = directory_bytes(project / ".beads" / "dolt")
    copies = 1 if args.no_restore_point else 2
    available = shutil.disk_usage(state).free
    if available < size * copies * 1.1:
        raise Refused(
            f"{state} has {available // 2**20} MiB free; {copies} cop"
            f"{'y' if copies == 1 else 'ies'} of a {size // 2**20} MiB database need more"
        )

    scratch = Scratch(run_dir, database, user, None)
    restore: Path | None = None
    manifest = {"taken": stamp, "project": str(project), "database": database, "versions": current}
    try:
        with live_server_stopped(project, installed["bd"], args.stop_server, report):
            if args.no_restore_point:
                copy_beads(project / ".beads", scratch.beads)
            else:
                restore = take_restore_point(project, state, stamp, manifest)
    except BaseException:
        discard_untrusted_copy(state / "restore-points" / stamp, project)
        raise
    if report.facts.get("live_restart_failed"):
        return
    scratch.live_port = server_port(project / ".beads")
    if restore is not None:
        prune_restore_points(state, restore, project)
        report.facts["restore_point"] = str(restore)
        copy_beads(restore / ".beads", scratch.beads)
        report.info(f"restore point kept at {restore}")

    recorded = ""
    try:
        scratch.use("current", installed["bd"], installed["dolt"])
        scratch.prepare()
        strip_remotes(installed["dolt"], scratch.beads, scratch.env())
        report.record("copy has no Dolt remotes", True)
        scratch.start_server()
        control_open = scratch.bd("list", "--limit", "1")
        if control_open.returncode != 0:
            raise Refused(
                f"the installed bd cannot open the copy, so the copy is not a valid "
                f"baseline: {control_open.tail}"
            )
        before = scratch.snapshot(run_dir / "baseline.jsonl")
        recorded = before["bd_version"]
        if not scratch.stop_server():
            raise Refused("the scratch Dolt server did not stop after the baseline pass")
        report.info(
            f"baseline from the copy: bd_version {before['bd_version']}, schema "
            f"{before['schema_version']}, {len(before['rows'])} tables"
        )
        scratch.use("proposed", binaries["bd"], binaries["dolt"])
        try:
            proposed_pass(scratch, report, before, proposed, run_dir)
        except (Refused, ValueError) as error:
            report.record("proposed pass ran to completion", False, str(error))
    finally:
        report.record("scratch Dolt server stopped", scratch.stop_server())
        check_live_untouched(project, installed["dolt"], database, user, recorded, report)


def proposed_pass(
    scratch: Scratch, report: Report, before: dict[str, Any],
    proposed: dict[str, str], run_dir: Path,
) -> None:
    scratch.start_server()
    first = scratch.bd("list", "--limit", "1", "--json")
    report.record(
        "proposed bd opens the store", first.returncode == 0,
        f"{first.seconds:.1f}s" if first.returncode == 0 else first.tail,
    )
    if first.returncode != 0:
        return
    if first.seconds > HOOK_TIMEOUT_SECONDS:
        report.info(
            f"the first open took {first.seconds:.1f}s, longer than the "
            f"{HOOK_TIMEOUT_SECONDS:g}s plan-approval hook timeout; expect one "
            "[BEADS_GATE: unverified] prompt right after the real upgrade"
        )
    migrated = scratch.bd("migrate", "schema")
    report.record(
        "bd migrate schema is clean", migrated.returncode == 0,
        "" if migrated.returncode == 0 else migrated.tail,
    )
    version = scratch.bd("version", "--json")
    try:
        reported = unwrap(json.loads(version.stdout))["version"]
    except (ValueError, KeyError, TypeError):
        reported = ""
    report.record(
        "bd reports the proposed version", reported == proposed["bd"],
        f"{reported or version.tail}",
    )
    after = scratch.snapshot(run_dir / "after.jsonl")
    report.record(
        "store records the proposed bd version",
        after["bd_version"] == proposed["bd"],
        f"local_metadata.bd_version {before['bd_version']} -> {after['bd_version']}",
    )
    report.info(f"schema version {before['schema_version']} -> {after['schema_version']}")

    exported = load_export(run_dir / "after.jsonl")
    problems, added = compare_exports(load_export(run_dir / "baseline.jsonl"), exported)
    report.record(
        "every issue exports unchanged", not problems,
        "; ".join(problems) if problems else f"{len(exported)} issues",
    )
    if added:
        report.info(f"the proposed bd exports {added} field value(s) the baseline did not have")
    losses, gains = compare_rows(before["rows"], after["rows"])
    report.record("no table lost rows", not losses, "; ".join(losses))
    for gain in gains:
        report.info(gain)
    known = expected_clone_local_fks()
    expected = known & set(before["foreign_keys"])
    missing = sorted(expected - set(after["foreign_keys"]))
    report.record(
        "clone-local foreign keys survive", not missing,
        ", ".join(missing) if missing else f"{len(expected)} present",
    )
    if known and not expected:
        report.info(
            "the baseline had none of beads-sync's clone-local foreign keys, so "
            "their survival was not tested"
        )
    write_probe(scratch, report)
    status = scratch.run(["bash", "assets/beads-sync.sh", "status"], timeout=300)
    report.record(
        "beads-sync status runs", status.returncode == 0,
        "" if status.returncode == 0 else status.tail,
    )
    doctor = scratch.bd("doctor", timeout=300)
    (run_dir / "doctor.txt").write_text(doctor.stdout + doctor.stderr, encoding="utf-8")
    report.info(f"bd doctor exited {doctor.returncode}; output saved to {run_dir / 'doctor.txt'}")


def check_live_untouched(
    project: Path, dolt: Path, database: str, user: str, recorded: str, report: Report
) -> None:
    """Re-read the version the live store records; it must match the copy's baseline."""
    port = server_port(project / ".beads")
    if port is None or server_pid(project / ".beads") is None:
        report.info("live Dolt server is not running; its recorded bd version was not re-read")
        return
    checked = run(
        [
            str(dolt), "--host", "127.0.0.1", "--port", str(port), "--user", user,
            "--password", "", "--no-tls", "--use-db", database, "sql", "-r", "csv", "-q",
            "select value from local_metadata where `key` = 'bd_version'",
        ],
        cwd=project, env=dict(os.environ), timeout=120,
    )
    rows = checked.stdout.splitlines()
    live = rows[1].strip() if checked.returncode == 0 and len(rows) > 1 else ""
    if not recorded:
        report.info(f"live store records bd {live or 'an unreadable version'}; no baseline to compare")
        return
    report.record(
        "live store still records the installed bd version",
        bool(live) and live == recorded,
        live or checked.tail,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy the live Beads database, run the proposed bd and dolt on the "
            "copy, and report whether the data and this repo's helpers survive."
        )
    )
    parser.add_argument("--ref", help="git ref whose .chezmoidata.yaml holds the proposed pins")
    parser.add_argument("--bd", help="proposed bd version, overriding the pin at --ref")
    parser.add_argument("--dolt", help="proposed dolt version, overriding the pin at --ref")
    parser.add_argument(
        "--stop-server", action="store_true",
        help="allow stopping the live Dolt server while the database is copied",
    )
    parser.add_argument(
        "--no-restore-point", action="store_true",
        help="copy the database once and keep no untouched copy for recovery",
    )
    parser.add_argument("--keep", action="store_true", help="keep the scratch project on success")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Beads project to copy")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--state-dir", type=Path, help="where runs and the restore point live")
    parser.add_argument("--toolchain-dir", type=Path, help="where fetched binaries are cached")
    parser.add_argument("--timeout", type=float, default=300.0, help="download timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (args.ref or (args.bd and args.dolt)):
        say("ERROR: pass --ref, or both --bd and --dolt", error=True)
        return 2
    if os.name == "nt":
        say("ERROR: native Windows is a Beads client; run this in WSL2", error=True)
        return 2
    handled = (signal.SIGTERM, signal.SIGHUP)
    previous = {number: signal.getsignal(number) for number in handled}

    def terminate(received: int, _frame: Any) -> None:
        for number in handled:
            signal.signal(number, signal.SIG_IGN)
        sys.exit(128 + received)

    for number in handled:
        signal.signal(number, terminate)
    report = Report()
    refused = ""
    crashed: int | None = None
    try:
        rehearse(args, report)
    except Refused as error:
        refused = str(error)
        say(f"ERROR: {refused}", error=True)
    except KeyboardInterrupt:
        refused, crashed = "interrupted", 130
        say("ERROR: interrupted", error=True)
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) and error.code else 1
        refused, crashed = f"terminated (exit {code})", code
        say(f"ERROR: {refused}", error=True)
    except Exception as error:  # noqa: BLE001
        refused, crashed = f"{type(error).__name__}: {error}", 1
        say(f"ERROR: unexpected failure: {refused}", error=True)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    run_dir = Path(report.facts["run_dir"]) if "run_dir" in report.facts else None
    passed = not refused and not report.failed and bool(report.checks)
    if run_dir is not None:
        with contextlib.suppress(OSError):
            (run_dir / "report.json").write_text(
                json.dumps(
                    {"result": "PASS" if passed else "FAIL", "refused": refused,
                     "facts": report.facts, "checks": report.checks},
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
        if passed and not args.keep:
            shutil.rmtree(run_dir / "project", ignore_errors=True)
        say(f"INFO: report and exports in {run_dir}")
    if crashed is not None:
        return crashed
    if refused:
        return 1 if report.failed else 2
    say("RESULT: PASS" if passed else "RESULT: FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
