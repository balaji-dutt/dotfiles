from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.chezmoi_render import CHEZMOI, render_template
from tests.support.fixtures import (
    isolated_environment,
    load_script_module,
    read_json_lines,
    write_executable,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "assets/cbm-prune-orphans.py"
HOOK = ".chezmoiscripts/run_after_zz-prune-codebase-memory-mcp.sh.tmpl"

FAKE_CBM = """\
import json, pathlib, sys
state = pathlib.Path({state!r})
log = pathlib.Path({log!r})
args = sys.argv[1:]
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
failure = state.with_name("fail")
if failure.exists():
    sys.stderr.write(failure.read_text(encoding="utf-8"))
    sys.exit(1)
projects = json.loads(state.read_text(encoding="utf-8"))
tool = args[2]
flags = dict(zip(args[3::2], args[4::2]))
def reply(content, error=False):
    print(json.dumps({{"content": [], "structuredContent": content, "isError": error}}))
    sys.exit(1 if error else 0)
if tool == "list_projects":
    limit, offset = int(flags["--limit"]), int(flags["--offset"])
    page = projects[offset:offset + limit]
    more = offset + limit < len(projects)
    reply({{"projects": page, "total": len(projects), "has_more": more,
           "next_offset": offset + limit if more else None}})
if tool == "delete_project":
    name = flags["--project"]
    remaining = [p for p in projects if p["name"] != name]
    if len(remaining) == len(projects):
        reply({{"project": name, "status": "not_found"}}, error=True)
    state.write_text(json.dumps(remaining), encoding="utf-8")
    reply({{"project": name, "status": "deleted"}})
sys.exit(2)
"""


class Fixture:
    def __init__(self, env_fixture, *, sessions_active: bool = False) -> None:
        self.env_fixture = env_fixture
        self.root = env_fixture.root
        self.state = self.root / "cbm-state" / "projects.json"
        self.log = self.root / "cbm.jsonl"
        self.state.parent.mkdir()
        self.cbm = write_executable(
            self.root / "cbm-install" / "codebase-memory-mcp",
            f"#!{sys.executable}\n" + FAKE_CBM.format(state=str(self.state), log=str(self.log)),
        )
        write_executable(
            env_fixture.fake_bin / "pgrep",
            f"#!/bin/sh\nexit {0 if sessions_active else 1}\n",
        )
        self.projects: list[dict[str, str]] = []

    def add(self, name: str, root: Path) -> None:
        self.projects.append({"name": name, "root_path": str(root)})
        self.state.write_text(json.dumps(self.projects), encoding="utf-8")

    def fail_with(self, message: str) -> None:
        self.state.with_name("fail").write_text(message, encoding="utf-8")

    def remaining(self) -> list[str]:
        return [p["name"] for p in json.loads(self.state.read_text(encoding="utf-8"))]

    def calls(self) -> list[list[str]]:
        return read_json_lines(self.log) if self.log.exists() else []

    def run(self, *args: str, cbm: bool = True, env: dict[str, str] | None = None):
        argv = [sys.executable, "-I", str(HELPER), *args]
        if cbm:
            argv += ["--cbm", str(self.cbm)]
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            env=env if env is not None else self.env_fixture.env,
            cwd=self.root,
            check=False,
        )


class CbmPruneOrphansTests(unittest.TestCase):
    def layout(self, fixture: Fixture) -> dict[str, Path]:
        worktrees = fixture.root / "worktrees"
        live = worktrees / "live"
        live.mkdir(parents=True)
        paths = {
            "live": live,
            "gone": worktrees / "gone",
            "unmounted": fixture.root / "mnt" / "devdrive" / "repo" / "worktrees" / "x",
        }
        for name, path in paths.items():
            fixture.add(name, path)
        return paths

    def test_dry_run_reports_orphans_without_deleting(self) -> None:
        with isolated_environment(prefix="cbm-prune-dry-") as env:
            fixture = Fixture(env)
            self.layout(fixture)
            result = fixture.run()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("would delete gone", result.stdout)
            self.assertNotIn("would delete live", result.stdout)
            self.assertEqual(fixture.remaining(), ["live", "gone", "unmounted"])
            self.assertFalse(any("delete_project" in call for call in fixture.calls()))

    def test_apply_deletes_only_missing_roots_with_present_parent(self) -> None:
        with isolated_environment(prefix="cbm-prune-apply-") as env:
            fixture = Fixture(env)
            self.layout(fixture)
            result = fixture.run("--apply")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture.remaining(), ["live", "unmounted"])
            self.assertIn("keeping unmounted", result.stdout)
            self.assertIn("volume may be unmounted", result.stdout)
            self.assertIn("1 of 3 indexes deleted", result.stdout)
            deletes = [call for call in fixture.calls() if "delete_project" in call]
            self.assertEqual(deletes, [["cli", "--json", "delete_project", "--project", "gone"]])

    def test_keep_path_is_never_deleted(self) -> None:
        with isolated_environment(prefix="cbm-prune-keep-") as env:
            fixture = Fixture(env)
            paths = self.layout(fixture)
            result = fixture.run("--apply", "--keep", str(paths["gone"]))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture.remaining(), ["live", "gone", "unmounted"])

    def test_root_under_empty_mount_point_is_kept(self) -> None:
        with isolated_environment(prefix="cbm-prune-mountpoint-") as env:
            fixture = Fixture(env)
            mount_point = fixture.root / "mnt" / "devdrive"
            mount_point.mkdir(parents=True)
            fixture.add("on-share", mount_point / "agentic-tooling")
            result = fixture.run("--apply")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture.remaining(), ["on-share"])
            self.assertIn("parent directory is empty", result.stdout)

    @unittest.skipIf(not hasattr(os, "geteuid") or os.geteuid() == 0,
                     "needs a non-root POSIX user for permission checks")
    def test_root_that_cannot_be_checked_is_kept(self) -> None:
        with isolated_environment(prefix="cbm-prune-locked-") as env:
            fixture = Fixture(env)
            locked = fixture.root / "locked"
            (locked / "sibling").mkdir(parents=True)
            fixture.add("hidden", locked / "repo")
            locked.chmod(0)
            try:
                result = fixture.run("--apply")
            finally:
                locked.chmod(stat.S_IRWXU)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture.remaining(), ["hidden"])
            self.assertIn("root cannot be checked", result.stdout)

    def test_relative_or_empty_root_is_kept(self) -> None:
        with isolated_environment(prefix="cbm-prune-relative-") as env:
            fixture = Fixture(env)
            fixture.projects = [
                {"name": "relative", "root_path": "worktrees/gone"},
                {"name": "empty", "root_path": ""},
                {"name": "absent"},
            ]
            fixture.state.write_text(json.dumps(fixture.projects), encoding="utf-8")
            result = fixture.run("--apply")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture.remaining(), ["relative", "empty", "absent"])

    def test_pages_through_every_project(self) -> None:
        with isolated_environment(prefix="cbm-prune-pages-") as env:
            fixture = Fixture(env)
            live = fixture.root / "live"
            live.mkdir()
            fixture.projects = [{"name": f"live-{i}", "root_path": str(live)} for i in range(1001)]
            fixture.add("gone", fixture.root / "gone")
            result = fixture.run("--apply")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("gone", fixture.remaining())
            offsets = [call[call.index("--offset") + 1] for call in fixture.calls()
                       if "list_projects" in call]
            self.assertEqual(offsets, ["0", "500", "1000"])

    def test_cli_failure_while_sessions_are_active_warns_and_exits_zero(self) -> None:
        with isolated_environment(prefix="cbm-prune-busy-") as env:
            fixture = Fixture(env, sessions_active=True)
            fixture.add("gone", fixture.root / "gone")
            fixture.fail_with("daemon admission gate is held")
            result = fixture.run("--apply")

            self.assertEqual(result.returncode, 0)
            self.assertIn("WARNING:", result.stderr)
            self.assertIn("not pruned this run", result.stderr)
            self.assertIn("daemon admission gate is held", result.stderr)

    def test_cli_failure_without_sessions_is_an_error(self) -> None:
        with isolated_environment(prefix="cbm-prune-broken-") as env:
            fixture = Fixture(env, sessions_active=False)
            fixture.add("gone", fixture.root / "gone")
            fixture.fail_with("cache is corrupt")
            result = fixture.run("--apply")

            self.assertEqual(result.returncode, 1)
            self.assertIn("ERROR:", result.stderr)
            self.assertIn("cache is corrupt", result.stderr)
            self.assertNotIn("WARNING:", result.stderr)

    def test_missing_binary_is_skipped(self) -> None:
        with isolated_environment(prefix="cbm-prune-nobin-") as env:
            fixture = Fixture(env)
            restricted = {**env.env, "PATH": str(env.fake_bin)}
            result = fixture.run("--apply", cbm=False, env=restricted)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("not available; skipping", result.stdout)
            self.assertEqual(fixture.calls(), [])

    def test_wsl_rejects_windows_executables(self) -> None:
        module = load_script_module(HELPER, "cbm_prune_orphans")
        with isolated_environment(prefix="cbm-prune-native-") as env:
            exe = write_executable(env.root / "codebase-memory-mcp.exe", "#!/bin/sh\n")
            native = write_executable(env.root / "codebase-memory-mcp", "#!/bin/sh\n")

            self.assertFalse(module.is_native_candidate(str(exe), True))
            self.assertTrue(module.is_native_candidate(str(exe), False))
            self.assertTrue(module.is_native_candidate(str(native), True))
            self.assertFalse(module.is_native_candidate(str(env.root / "absent"), True))
        self.assertTrue(module.is_windows_path("/mnt/c/Users/me/codebase-memory-mcp"))
        self.assertTrue(module.is_windows_path("/home/me/bin/CODEBASE-MEMORY-MCP.EXE"))
        self.assertFalse(module.is_windows_path("/mnt/devdrive/bin/codebase-memory-mcp"))
        self.assertFalse(module.is_windows_path("/mnt"))

    def test_hung_cli_becomes_a_cbm_error(self) -> None:
        module = load_script_module(HELPER, "cbm_prune_orphans")
        with isolated_environment(prefix="cbm-prune-hung-") as env:
            hung = write_executable(env.root / "codebase-memory-mcp", "#!/bin/sh\nexec sleep 30\n")
            original = module.CLI_TIMEOUT_SECONDS
            module.CLI_TIMEOUT_SECONDS = 1
            try:
                with self.assertRaisesRegex(module.CbmError, "did not finish within 1 seconds"):
                    module.run_tool(str(hung), "list_projects")
            finally:
                module.CLI_TIMEOUT_SECONDS = original


@unittest.skipUnless(CHEZMOI and sys.platform != "win32", "chezmoi and a POSIX shell are required")
class CbmPruneHookTests(unittest.TestCase):
    def write_python(self, fake_bin: Path, log: Path, *, exit_code: int = 0) -> None:
        write_executable(
            fake_bin / "python3",
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['-c']:\n"
            "    sys.exit(0)\n"
            f"with pathlib.Path({str(log)!r}).open('a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(args) + '\\n')\n"
            f"sys.exit({exit_code})\n",
        )

    def render(self, source: Path, working_tree: Path, env: dict[str, str]) -> str:
        def configure(data: dict[str, object]) -> None:
            data["chezmoi"]["sourceDir"] = str(source)
            data["chezmoi"]["workingTree"] = str(working_tree)

        return render_template(HOOK, "linux", configure, environment=env)

    def run_hook(self, script: str, cwd: Path, env: dict[str, str]):
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, cwd=cwd, env=env, check=False
        )

    def test_hook_dispatches_apply_and_protects_working_tree(self) -> None:
        with isolated_environment(prefix="cbm-prune-hook-") as env:
            source = env.root / "source dir"
            working_tree = env.root / "working tree"
            log = env.root / "python.jsonl"
            restricted = {**env.env, "PATH": f"{env.fake_bin}:/usr/bin:/bin"}
            script = self.render(source, working_tree, env.env)

            self.write_python(env.fake_bin, log)
            ok = self.run_hook(script, env.root, restricted)
            self.write_python(env.fake_bin, log, exit_code=1)
            failed = self.run_hook(script, env.root, restricted)

            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(failed.returncode, 1)
            self.assertEqual(read_json_lines(log)[0], [
                "-I",
                str(source / "assets" / "cbm-prune-orphans.py"),
                "--apply",
                "--keep",
                str(working_tree),
            ])

    def test_hook_skips_without_python(self) -> None:
        with isolated_environment(prefix="cbm-prune-hook-nopy-") as env:
            script = self.render(env.root / "src", env.root / "wt", env.env)
            bash = subprocess.run(["sh", "-c", "command -v bash"], capture_output=True,
                                  text=True, check=True).stdout.strip()
            only_bash = env.root / "only-bash"
            only_bash.mkdir()
            (only_bash / "bash").symlink_to(bash)
            result = subprocess.run(
                [bash, "-c", script], capture_output=True, text=True, cwd=env.root,
                env={**env.env, "PATH": str(only_bash)}, check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("WARNING: Python 3.9 or newer is unavailable", result.stderr)


if __name__ == "__main__":
    unittest.main()
