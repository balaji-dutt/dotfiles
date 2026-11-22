from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "assets/beads-upgrade-rehearsal.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("beads_upgrade_rehearsal", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


REHEARSAL = load_module()
POSIX_ONLY = unittest.skipIf(os.name == "nt", "the rehearsal runs on linux and macOS only")

FAKE_BD = """#!/bin/sh
beads="$PWD/.beads"
echo "$*" >> "$beads/bd.log"
case "$1 $2" in
  "dolt stop")
    [ -e "$beads/refuse-stop" ] && exit 1
    [ -e "$beads/ignore-stop" ] && exit 0
    if [ -f "$beads/dolt-server.pid" ]; then
      kill "$(cat "$beads/dolt-server.pid")" 2>/dev/null
      rm -f "$beads/dolt-server.pid"
    fi
    ;;
  "dolt start")
    [ -e "$beads/refuse-start" ] && exit 1
    if [ -f "$beads/foreign-pid" ]; then
      cp "$beads/foreign-pid" "$beads/dolt-server.pid"
    else
      code='import time; time.sleep(600)'
      [ -e "$beads/stubborn" ] && code="import signal, sys; signal.signal(signal.SIGTERM, signal.SIG_IGN); open(sys.argv[1] + '/.beads/stubborn-ready', 'w').close(); $code"
      "$FAKE_PYTHON" -c "$code" "$PWD" >/dev/null 2>&1 &
      echo $! > "$beads/dolt-server.pid"
      echo $! >> "$beads/spawned"
    fi
    cat "$beads/next-port" > "$beads/dolt-server.port"
    ;;
  "list --limit")
    [ -e "$beads/refuse-list" ] && exit 1
    [ -f "$beads/dolt-server.pid" ] || exit 1
    ;;
esac
exit 0
"""

FAKE_DOLT = """#!/bin/sh
case "$1 $2" in
  "remote -v")
    [ -f remotes.txt ] && cat remotes.txt
    ;;
  "remote remove")
    [ -e sticky ] && exit 0
    grep -v "^$3 " remotes.txt > remotes.tmp
    mv remotes.tmp remotes.txt
    ;;
  *)
    echo "count(*)"
    cat "$PWD/.beads/remote-count" 2>/dev/null || echo 0
    ;;
esac
exit 0
"""


def write_script(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def gone(pid: int, timeout: float = 10.0) -> bool:
    """True once a process that is not this test's child has exited."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="beads-rehearsal-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.spawned: list[subprocess.Popen[bytes]] = []
        self.addCleanup(self.reap)

    def reap(self) -> None:
        for process in self.spawned:
            with contextlib.suppress(OSError):
                process.kill()
            process.wait()

    def spawn(self, *extra: str) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)", *extra]
        )
        self.spawned.append(process)
        return process

    def kill_recorded_server(self, beads: Path) -> None:
        recorded: list[str] = []
        for name in ("dolt-server.pid", "spawned"):
            with contextlib.suppress(OSError):
                recorded += (beads / name).read_text(encoding="utf-8").split()
        for pid in recorded:
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(pid), signal.SIGKILL)


class ComparisonTests(unittest.TestCase):
    def test_exports_flag_lost_changed_and_dropped_data_but_not_added_fields(self) -> None:
        before = {
            "a-1": {"id": "a-1", "title": "one", "notes": "n"},
            "a-2": {"id": "a-2", "title": "two"},
            "a-3": {"id": "a-3", "title": "three"},
        }
        after = {
            "a-1": {"id": "a-1", "title": "ONE", "extra": 1},
            "a-2": {"id": "a-2", "title": "two", "extra": 2},
            "a-4": {"id": "a-4", "title": "four"},
        }

        problems, added = REHEARSAL.compare_exports(before, after)

        self.assertEqual(added, 2)
        self.assertEqual(
            problems,
            [
                "a-3 is missing after the upgrade",
                "a-4 appeared after the upgrade",
                'a-1.notes was dropped (was "n")',
                'a-1.title changed: "one" -> "ONE"',
            ],
        )
        self.assertEqual(REHEARSAL.compare_exports(before, dict(before)), ([], 0))

        many = {f"a-{n}": {"id": f"a-{n}"} for n in range(60)}
        capped, _ = REHEARSAL.compare_exports(many, {})
        self.assertEqual(len(capped), 41)
        self.assertEqual(capped[-1], "... 20 more difference(s)")

    def test_rows_flag_missing_and_shrunk_tables_only(self) -> None:
        losses, gains = REHEARSAL.compare_rows(
            {"issues": 5, "events": 9, "gone": 1, "same": 2},
            {"issues": 4, "events": 12, "same": 2, "fresh": 0},
        )
        self.assertEqual(losses, ["table gone is gone", "issues shrank from 5 to 4 rows"])
        self.assertEqual(gains, ["table fresh is new", "events grew from 9 to 12 rows"])

    def test_export_loader_rejects_records_without_an_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.jsonl"
            path.write_text('{"id": "a-1"}\n\n{"id": "a-2", "k": 1}\n', encoding="utf-8")
            self.assertEqual(sorted(REHEARSAL.load_export(path)), ["a-1", "a-2"])
            path.write_text('{"title": "no id"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 1 is not an issue record"):
                REHEARSAL.load_export(path)

    def test_bd_json_shapes_are_unwrapped(self) -> None:
        record = {"id": "a-1"}
        for payload in (record, [record], {"schema_version": 1, "data": [record]},
                        {"schema_version": 1, "data": record}):
            self.assertEqual(REHEARSAL.unwrap(payload), record)

    def test_expected_foreign_keys_come_from_beads_sync(self) -> None:
        keys = REHEARSAL.expected_clone_local_fks()
        self.assertIn("events.fk_events_issue", keys)
        self.assertTrue(all(key.count(".") == 1 for key in keys))


class CopyTests(TempCase):
    def make_source(self) -> Path:
        beads = self.root / "live" / ".beads"
        database = beads / "dolt" / "dots" / ".dolt"
        (database / "noms").mkdir(parents=True)
        (database / "noms" / "manifest").write_text("data", encoding="utf-8")
        (database / "sql-server.info").write_text("123:3306", encoding="utf-8")
        (database / "git-remote-cache" / "abc").mkdir(parents=True)
        (database / "git-remote-cache" / "abc" / "config").write_text("url", encoding="utf-8")
        for name, text in (
            ("metadata.json", "{}"), ("config.yaml", "a: b"), (".local_version", "1.3.2"),
            ("config.local.yaml", "sync.remote: secret"), ("dolt-server.pid", "1"),
            ("in-progress-claude.json", "{}"),
        ):
            (beads / name).write_text(text, encoding="utf-8")
        return beads

    def test_copy_takes_the_database_and_state_files_and_nothing_private(self) -> None:
        beads = self.make_source()
        target = self.root / "copy" / ".beads"

        REHEARSAL.copy_beads(beads, target)

        copied = sorted(
            str(path.relative_to(target)) for path in target.rglob("*") if path.is_file()
        )
        self.assertEqual(
            copied,
            [".local_version", "config.yaml", "dolt/dots/.dolt/noms/manifest", "metadata.json"],
        )

    @POSIX_ONLY
    def test_copy_refuses_a_database_reached_through_a_symlink(self) -> None:
        beads = self.make_source()
        (beads / "dolt" / "dots" / ".dolt" / "linked").symlink_to(self.root)
        with self.assertRaisesRegex(REHEARSAL.Refused, "is a symlink"):
            REHEARSAL.copy_beads(beads, self.root / "copy" / ".beads")
        self.assertFalse((self.root / "copy").exists())

    def test_restore_points_are_pruned_only_for_the_same_project(self) -> None:
        beads = self.make_source()
        project = beads.parent
        state = self.root / "state"
        manifest = {"project": str(project), "database": "dots"}
        first = REHEARSAL.take_restore_point(project, state, "20260101T000000Z-1", manifest)
        points = state / "restore-points"
        other = points / "other-project"
        other.mkdir()
        (other / REHEARSAL.RESTORE_MARKER).write_text(
            json.dumps({"project": "/somewhere/else"}), encoding="utf-8"
        )
        unmarked = points / "unmarked"
        unmarked.mkdir()
        (unmarked / "keep-me").write_text("x", encoding="utf-8")
        second = REHEARSAL.take_restore_point(project, state, "20260102T000000Z-2", manifest)

        REHEARSAL.prune_restore_points(state, second, project)

        self.assertEqual(
            sorted(path.name for path in points.iterdir()),
            ["20260102T000000Z-2", "other-project", "unmarked"],
        )
        self.assertFalse(first.exists())
        marker = json.loads((second / REHEARSAL.RESTORE_MARKER).read_text(encoding="utf-8"))
        self.assertTrue(marker["complete"])
        self.assertTrue((second / ".beads" / "dolt" / "dots" / ".dolt" / "noms" / "manifest").is_file())

    def test_metadata_must_describe_a_local_server_mode_database(self) -> None:
        beads = self.make_source()
        project = beads.parent
        good = {"dolt_mode": "server", "dolt_database": "dots", "dolt_server_host": "127.0.0.1"}
        (beads / "metadata.json").write_text(json.dumps(good), encoding="utf-8")
        self.assertEqual(REHEARSAL.read_metadata(project), ("dots", "root"))
        self.assertEqual(REHEARSAL.find_project(project), project)

        for change, message in (
            ({"dolt_mode": "embedded"}, "not in Dolt server mode"),
            ({"dolt_server_host": "db.example.com"}, "non-local Dolt server"),
            ({"dolt_database": "dots; drop"}, "unexpected dolt_database"),
            ({"dolt_server_user": "root'--"}, "unexpected dolt_database or dolt_server_user"),
            ({"dolt_database": "absent"}, "no Dolt database named absent"),
        ):
            with self.subTest(change=change):
                (beads / "metadata.json").write_text(
                    json.dumps({**good, **change}), encoding="utf-8"
                )
                with self.assertRaisesRegex(REHEARSAL.Refused, message):
                    REHEARSAL.read_metadata(project)

        with self.assertRaisesRegex(REHEARSAL.Refused, "no .beads/dolt under"):
            REHEARSAL.find_project(self.root)


@POSIX_ONLY
class LiveServerTests(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = self.root / "live"
        self.beads = self.project / ".beads"
        self.beads.mkdir(parents=True)
        (self.beads / "next-port").write_text("3307\n", encoding="utf-8")
        self.bd = write_script(self.root / "bd", FAKE_BD)
        self.report = REHEARSAL.Report()
        environment = mock.patch.dict(os.environ, {"FAKE_PYTHON": sys.executable})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.kill_recorded_server, self.beads)
        waits = mock.patch.object(REHEARSAL, "LIVE_STOP_WAIT", 1.5)
        waits.start()
        self.addCleanup(waits.stop)

    def start_live(self) -> subprocess.Popen[bytes]:
        server = self.spawn(str(self.project))
        (self.beads / "dolt-server.pid").write_text(f"{server.pid}\n", encoding="utf-8")
        return server

    def held(self, allow: bool = True) -> Any:
        return REHEARSAL.live_server_stopped(self.project, self.bd, allow, self.report)

    def commands(self) -> list[str]:
        log = self.beads / "bd.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def quiet(self) -> Any:
        return contextlib.redirect_stdout(io.StringIO())

    def test_running_server_is_not_stopped_without_permission(self) -> None:
        server = self.start_live()
        with self.assertRaisesRegex(REHEARSAL.Refused, "rerun with --stop-server"):
            with self.held(allow=False):
                self.fail("the body must not run")
        self.assertIsNone(server.poll())
        self.assertEqual(self.commands(), [])

    def test_server_is_down_during_the_copy_and_back_afterwards(self) -> None:
        server = self.start_live()
        with self.quiet(), self.held():
            self.assertIsNone(REHEARSAL.server_pid(self.beads))
            self.assertIsNotNone(server.wait(timeout=10))
        self.assertIsNotNone(REHEARSAL.server_pid(self.beads))
        self.assertEqual(self.commands(), ["dolt stop", "dolt start", "list --limit 1"])
        self.assertEqual(
            [(check["name"], check["status"]) for check in self.report.checks],
            [("live Dolt server restarted and answering", "PASS")],
        )

    def test_server_is_restarted_when_the_copy_fails_or_is_interrupted(self) -> None:
        for error in (OSError("disk full"), KeyboardInterrupt(), SystemExit(143)):
            with self.subTest(error=type(error).__name__):
                self.kill_recorded_server(self.beads)
                self.start_live()
                with self.quiet(), self.assertRaises(type(error)):
                    with self.held():
                        raise error
                self.assertIsNotNone(REHEARSAL.server_pid(self.beads))

    def test_a_server_that_will_not_stop_is_left_running_and_reported(self) -> None:
        server = self.start_live()
        (self.beads / "ignore-stop").write_text("", encoding="utf-8")
        with self.quiet(), self.assertRaisesRegex(REHEARSAL.Refused, "could not stop the live"):
            with self.held():
                self.fail("the body must not run")
        self.assertIsNone(server.poll())
        self.assertEqual(REHEARSAL.server_pid(self.beads), server.pid)
        self.assertEqual(self.commands(), ["dolt stop", "list --limit 1"])

    def test_a_server_restarted_by_another_session_invalidates_the_copy(self) -> None:
        self.start_live()
        with self.quiet(), self.assertRaisesRegex(REHEARSAL.Refused, "started again during"):
            with self.held():
                subprocess.run([str(self.bd), "dolt", "start"], cwd=self.project, check=True)
        self.assertIsNotNone(REHEARSAL.server_pid(self.beads))

    def test_a_stopped_server_is_copied_as_is_and_not_started(self) -> None:
        with self.held(allow=False):
            pass
        self.assertEqual(self.commands(), [])
        self.assertEqual(self.report.checks, [])

    def test_a_failed_restart_is_a_failed_check_with_the_manual_command(self) -> None:
        self.start_live()
        (self.beads / "refuse-start").write_text("", encoding="utf-8")
        with self.quiet(), self.held():
            pass
        self.assertTrue(self.report.failed)
        self.assertTrue(self.report.facts["live_restart_failed"])
        self.assertIn("bd dolt start", self.report.checks[0]["detail"])

    def test_termination_signals_are_held_off_while_the_server_restarts(self) -> None:
        self.start_live()
        before = {number: signal.getsignal(number) for number in REHEARSAL.TERMINATION_SIGNALS}
        seen: list[dict[int, Any]] = []
        real_run = REHEARSAL.run

        def watching(argv: list[str], **kwargs: Any) -> Any:
            if argv[1:] == ["dolt", "start"]:
                seen.append({n: signal.getsignal(n) for n in REHEARSAL.TERMINATION_SIGNALS})
                self.assertTrue(kwargs.get("detached"))
            return real_run(argv, **kwargs)

        with mock.patch.object(REHEARSAL, "run", watching), self.quiet(), \
                self.assertRaises(OSError):
            with self.held():
                raise OSError("copy failed")
        self.assertEqual(
            seen, [{number: signal.SIG_IGN for number in REHEARSAL.TERMINATION_SIGNALS}]
        )
        self.assertEqual(
            {n: signal.getsignal(n) for n in REHEARSAL.TERMINATION_SIGNALS}, before
        )
        self.assertIsNotNone(REHEARSAL.server_pid(self.beads))

    def test_detached_commands_get_default_signals_in_their_own_session(self) -> None:
        probe = (
            "import os, signal; print(os.getsid(0) == os.getpid(), "
            "signal.getsignal(signal.SIGTERM) is signal.SIG_DFL, "
            "signal.getsignal(signal.SIGHUP) is signal.SIG_DFL)"
        )
        for number in (signal.SIGHUP, signal.SIGTERM):
            self.addCleanup(signal.signal, number, signal.signal(number, signal.SIG_IGN))
        environment = dict(os.environ)
        detached = REHEARSAL.run(
            [sys.executable, "-c", probe], cwd=self.root, env=environment, detached=True
        )
        attached = REHEARSAL.run([sys.executable, "-c", probe], cwd=self.root, env=environment)
        self.assertEqual(detached.stdout.split(), ["True", "True", "True"])
        self.assertEqual(attached.stdout.split(), ["False", "False", "False"])

    def test_a_restarted_server_that_does_not_answer_is_a_failed_check(self) -> None:
        self.start_live()
        (self.beads / "refuse-list").write_text("", encoding="utf-8")
        with self.quiet(), self.held():
            pass
        self.assertIsNotNone(REHEARSAL.server_pid(self.beads))
        self.assertTrue(self.report.facts["live_restart_failed"])

    def test_a_stale_pid_file_counts_as_a_stopped_server(self) -> None:
        finished = self.spawn()
        finished.kill()
        finished.wait()
        (self.beads / "dolt-server.pid").write_text(f"{finished.pid}\n", encoding="utf-8")
        with self.held(allow=False):
            pass
        self.assertEqual(self.commands(), [])


@POSIX_ONLY
class ScratchServerTests(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.run_dir = self.root / "run"
        self.scratch = REHEARSAL.Scratch(self.run_dir, "dots", "root", 3306)
        self.scratch.beads.mkdir(parents=True)
        (self.scratch.beads / "next-port").write_text("40123\n", encoding="utf-8")
        self.scratch.use(
            "test", write_script(self.root / "bd", FAKE_BD), write_script(self.root / "dolt", FAKE_DOLT)
        )
        environment = mock.patch.dict(os.environ, {"FAKE_PYTHON": sys.executable})
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.kill_recorded_server, self.scratch.beads)
        waits = mock.patch.object(REHEARSAL, "SCRATCH_STOP_WAIT", 5.0)
        waits.start()
        self.addCleanup(waits.stop)
        original = self.scratch.env
        self.scratch.env = lambda: {**original(), "FAKE_PYTHON": sys.executable}  # type: ignore[method-assign]

    def test_environment_is_private_and_carries_no_live_settings(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"BEADS_DOLT_SERVER_PORT": "26647", "BEADS_DOLT_CLI_DIR": "/live",
             "DOLT_ROOT_PATH": "/live", "SSH_AUTH_SOCK": "/live.sock"},
        ):
            env = REHEARSAL.Scratch(self.run_dir, "dots", "root", None)
            env.bin = self.scratch.bin
            values = env.env()
        self.assertEqual(values["HOME"], str(self.run_dir / "home"))
        self.assertEqual(
            values["PATH"], os.pathsep.join((str(self.scratch.bin), "/usr/bin", "/bin"))
        )
        self.assertNotIn("FAKE_PYTHON", values)
        self.assertEqual(values["BD_NO_REMOTE_ADOPT"], "1")
        self.assertEqual(values["BD_DISABLE_METRICS"], "1")
        self.assertFalse([key for key in values if key.startswith(("BEADS_", "DOLT_", "SSH_"))])
        self.assertEqual(
            sorted(path.name for path in self.scratch.bin.iterdir() if path.name in ("bd", "dolt")),
            ["bd", "dolt"],
        )

    def test_started_server_is_stopped_even_when_bd_cannot_stop_it(self) -> None:
        self.scratch.start_server()
        pid = self.scratch.pid
        self.assertIsNotNone(pid)
        self.assertTrue(self.scratch.stop_server())
        self.assertTrue(gone(pid))

        self.scratch.start_server()
        pid = self.scratch.pid
        (self.scratch.beads / "ignore-stop").write_text("", encoding="utf-8")
        self.assertTrue(self.scratch.stop_server())
        self.assertTrue(gone(pid))
        self.assertIsNone(self.scratch.pid)

    def test_a_server_ignoring_sigterm_is_killed(self) -> None:
        (self.scratch.beads / "stubborn").write_text("", encoding="utf-8")
        (self.scratch.beads / "ignore-stop").write_text("", encoding="utf-8")
        self.scratch.start_server()
        pid = self.scratch.pid
        deadline = time.monotonic() + 15
        while not (self.scratch.beads / "stubborn-ready").exists():
            self.assertLess(time.monotonic(), deadline, "the stubborn server never became ready")
            time.sleep(0.05)
        with mock.patch.object(REHEARSAL, "SCRATCH_STOP_WAIT", 1.5):
            self.assertTrue(self.scratch.stop_server())
        self.assertTrue(gone(pid))

    def test_a_server_that_is_not_the_scratch_one_is_refused_and_left_alone(self) -> None:
        foreign = self.spawn("/some/other/project")
        (self.scratch.beads / "foreign-pid").write_text(f"{foreign.pid}\n", encoding="utf-8")
        with self.assertRaisesRegex(REHEARSAL.Refused, "could not be shown to serve"):
            self.scratch.start_server()
        self.assertIsNone(self.scratch.pid)
        self.assertTrue(self.scratch.stop_server())
        with self.assertRaises(subprocess.TimeoutExpired):
            foreign.wait(timeout=0.5)
        self.assertNotIn(
            "dolt stop", (self.scratch.beads / "bd.log").read_text(encoding="utf-8")
        )

    def test_the_live_port_is_refused_even_for_a_scratch_process(self) -> None:
        (self.scratch.beads / "next-port").write_text("3306\n", encoding="utf-8")
        with self.assertRaisesRegex(REHEARSAL.Refused, "live port 3306; it was stopped"):
            self.scratch.start_server()
        self.assertIsNone(self.scratch.pid)
        started = int((self.scratch.beads / "spawned").read_text(encoding="utf-8").split()[-1])
        self.assertTrue(gone(started))

    def test_a_server_left_by_a_failed_start_command_is_still_stopped(self) -> None:
        bd = self.root / "bd"
        bd.write_text(
            FAKE_BD.replace('    cat "$beads/next-port" > "$beads/dolt-server.port"',
                            '    cat "$beads/next-port" > "$beads/dolt-server.port"\n    exit 124'),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(REHEARSAL.Refused, "did not start"):
            self.scratch.start_server()
        self.assertIsNone(self.scratch.pid)
        started = int((self.scratch.beads / "spawned").read_text(encoding="utf-8").split()[-1])
        self.assertTrue(gone(started))

    def test_a_failing_ps_never_counts_as_the_server_being_gone(self) -> None:
        self.scratch.start_server()
        pid = self.scratch.pid
        (self.scratch.beads / "ignore-stop").write_text("", encoding="utf-8")
        with mock.patch.object(REHEARSAL, "SCRATCH_STOP_WAIT", 0.6), \
                mock.patch.object(self.scratch, "is_scratch_server", return_value=None):
            self.assertFalse(self.scratch.stop_server())
        self.assertEqual(self.scratch.pid, pid)
        self.assertFalse(gone(pid, timeout=0.3))
        self.assertTrue(self.scratch.stop_server())
        self.assertTrue(gone(pid))

        with mock.patch.object(self.scratch, "is_scratch_server", return_value=None):
            with self.assertRaisesRegex(REHEARSAL.Refused, "could not be shown to serve"):
                self.scratch.start_server()
        self.assertIsNone(self.scratch.pid)

    def test_a_remote_appearing_after_start_stops_the_server_and_refuses(self) -> None:
        (self.scratch.beads / "remote-count").write_text("1\n", encoding="utf-8")
        with self.assertRaisesRegex(REHEARSAL.Refused, "reports 1 Dolt remote"):
            self.scratch.start_server()
        self.assertIsNone(self.scratch.pid)
        started = int((self.scratch.beads / "spawned").read_text(encoding="utf-8").split()[-1])
        self.assertTrue(gone(started))

    def test_remotes_are_removed_and_a_surviving_remote_is_refused(self) -> None:
        database = self.scratch.beads / "dolt" / "dots"
        (database / ".dolt").mkdir(parents=True)
        (self.scratch.beads / "dolt" / ".doltcfg").mkdir()
        remotes = database / "remotes.txt"
        remotes.write_text("origin git+ssh://host/repo {}\nbackup file:///x {}\n", encoding="utf-8")
        dolt = self.root / "dolt"

        REHEARSAL.strip_remotes(dolt, self.scratch.beads, self.scratch.env())
        self.assertEqual(remotes.read_text(encoding="utf-8"), "")

        remotes.write_text("origin git+ssh://host/repo {}\n", encoding="utf-8")
        (database / "sticky").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(REHEARSAL.Refused, "still has a Dolt remote"):
            REHEARSAL.strip_remotes(dolt, self.scratch.beads, self.scratch.env())

        (database / ".dolt" / "sql-server.info").write_text("1:3306", encoding="utf-8")
        with self.assertRaisesRegex(REHEARSAL.Refused, "running server's state file"):
            REHEARSAL.strip_remotes(dolt, self.scratch.beads, self.scratch.env())


class MainTests(TempCase):
    def run_main(self, *argv: str, error: BaseException | None = None) -> tuple[int, str, str]:
        def rehearse(args: Any, report: Any) -> None:
            del args
            report.facts["run_dir"] = str(self.root)
            if error is not None:
                raise error
            report.record("a check", True)

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(REHEARSAL, "rehearse", rehearse), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = REHEARSAL.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def restore_signals(self) -> None:
        for number in (signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
            self.addCleanup(signal.signal, number, signal.getsignal(number))

    def test_arguments_must_name_the_proposed_versions(self) -> None:
        for argv in ((), ("--bd", "1.3.4"), ("--dolt", "2.7.3")):
            with self.subTest(argv=argv):
                code, _, stderr = self.run_main(*argv)
                self.assertEqual(code, 2)
                self.assertIn("pass --ref, or both --bd and --dolt", stderr)

    @POSIX_ONLY
    def test_malformed_versions_are_refused_before_anything_is_touched(self) -> None:
        self.restore_signals()
        for argv in (
            ("--bd", "../escape", "--dolt", "2.7.3"),
            ("--bd", "1.3.4", "--dolt", "v2.7.3"),
            ("--ref=--upload-pack=x",),
        ):
            with self.subTest(argv=argv):
                stdout, stderr = io.StringIO(), io.StringIO()
                with mock.patch.object(REHEARSAL, "find_project", side_effect=AssertionError), \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = REHEARSAL.main([*argv, "--state-dir", str(self.root / "state")])
                self.assertEqual(code, 2, stderr.getvalue())
                self.assertRegex(stderr.getvalue(), "invalid (bd|dolt) version|refusing git ref")
                self.assertFalse((self.root / "state").exists())

    @POSIX_ONLY
    def test_exit_codes_and_report_for_pass_refusal_and_crash(self) -> None:
        self.restore_signals()
        cases = (
            (None, 0, "PASS", "RESULT: PASS"),
            (REHEARSAL.Refused("no thanks"), 2, "FAIL", "ERROR: no thanks"),
            (ValueError("bad int"), 1, "FAIL", "unexpected failure: ValueError: bad int"),
            (KeyboardInterrupt(), 130, "FAIL", "ERROR: interrupted"),
            (SystemExit(143), 143, "FAIL", "ERROR: terminated (exit 143)"),
        )
        for error, expected, result, text in cases:
            with self.subTest(error=error):
                (self.root / "report.json").unlink(missing_ok=True)
                code, stdout, stderr = self.run_main("--ref", "HEAD", error=error)
                self.assertEqual(code, expected)
                self.assertIn(text, stdout + stderr)
                self.assertNotIn("Traceback", stderr)
                report = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["result"], result)

    @POSIX_ONLY
    def test_a_refusal_after_a_failed_check_exits_one(self) -> None:
        self.restore_signals()

        def rehearse(args: Any, report: Any) -> None:
            del args
            report.record("live Dolt server restarted and answering", False, "down")
            raise REHEARSAL.Refused("stopping here")

        with mock.patch.object(REHEARSAL, "rehearse", rehearse), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(REHEARSAL.main(["--ref", "HEAD"]), 1)

    @POSIX_ONLY
    def test_termination_signals_unwind_through_cleanup_and_still_report(self) -> None:
        self.restore_signals()
        for number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number.name):
                (self.root / "report.json").unlink(missing_ok=True)
                before = signal.getsignal(number)
                unwound: list[str] = []

                def rehearse(args: Any, report: Any, number: int = number) -> None:
                    del args
                    report.facts["run_dir"] = str(self.root)
                    try:
                        os.kill(os.getpid(), number)
                        time.sleep(5)
                    finally:
                        unwound.append("finally ran")

                stderr = io.StringIO()
                with mock.patch.object(REHEARSAL, "rehearse", rehearse), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(stderr):
                    code = REHEARSAL.main(["--ref", "HEAD"])
                self.assertEqual(code, 128 + number)
                self.assertEqual(unwound, ["finally ran"])
                self.assertIn(f"terminated (exit {128 + number})", stderr.getvalue())
                report = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["result"], "FAIL")
                self.assertIs(signal.getsignal(number), before)


if __name__ == "__main__":
    unittest.main()
