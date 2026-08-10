"""Regression tests for Beads shell dispatch and sync safety guards."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHELL_HELPERS = {
    "bash": ROOT / "dot_local/share/beads-helpers.bash",
    "zsh": ROOT / "dot_local/share/beads-helpers.zsh",
}


def write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class BeadsShellDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp = Path(self.temp_dir.name)
        self.bin_dir = self.temp / "bin"
        self.bin_dir.mkdir()
        self.repo = self.temp / "repo"
        self.repo.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", str(self.repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.other_repo = self.temp / "other"
        self.other_repo.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", str(self.other_repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.log = self.temp / "calls.log"
        write_executable(
            self.bin_dir / "bd",
            """
            #!/bin/sh
            printf 'native:%s\n' "$*" >> "$BD_TEST_LOG"
            printf 'native:%s\n' "$*"
            exit "${BD_TEST_NATIVE_EXIT:-0}"
            """,
        )
        assets = self.repo / "assets"
        assets.mkdir()
        (self.repo / ".beads").mkdir()
        (self.repo / ".beads/metadata.json").write_text(
            '{"dolt_database":"dots"}\n', encoding="utf-8"
        )
        write_executable(
            assets / "beads-sync.sh",
            """
            #!/bin/sh
            printf 'sync:%s\n' "$*" >> "$BD_TEST_LOG"
            exit "${BD_TEST_SYNC_EXIT:-0}"
            """,
        )
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{self.env['PATH']}",
                "BD_TEST_LOG": str(self.log),
                "BD_FILTER_AUTO_IMPORT_NOISE": "0",
            }
        )

    def run_wrapper(
        self,
        shell: str,
        *args: str,
        cwd: Path | None = None,
        env_updates: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = self.env.copy()
        if env_updates:
            env.update(env_updates)
        return subprocess.run(
            [
                shell,
                "-c",
                'source "$1"; shift; bd "$@"',
                "beads-helper-test",
                str(SHELL_HELPERS[shell]),
                *args,
            ],
            cwd=cwd or self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_direct(self, shell: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                shell,
                "-c",
                'source "$1"; shift; command bd "$@"',
                "beads-helper-test",
                str(SHELL_HELPERS[shell]),
                *args,
            ],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def read_log(self) -> list[str]:
        if not self.log.exists():
            return []
        return self.log.read_text(encoding="utf-8").splitlines()

    def clear_log(self) -> None:
        self.log.unlink(missing_ok=True)

    def test_pull_and_push_redirect_in_bash_and_zsh(self) -> None:
        for shell in SHELL_HELPERS:
            for action in ("pull", "push"):
                with self.subTest(shell=shell, action=action):
                    self.clear_log()
                    result = self.run_wrapper(shell, "dolt", action)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(f"redirecting `bd dolt {action}`", result.stderr)
                    self.assertEqual(self.read_log(), [f"sync:{action}"])

    def test_sync_exit_code_is_propagated(self) -> None:
        for shell in SHELL_HELPERS:
            with self.subTest(shell=shell):
                self.clear_log()
                result = self.run_wrapper(
                    shell,
                    "dolt",
                    "push",
                    env_updates={"BD_TEST_SYNC_EXIT": "23"},
                )
                self.assertEqual(result.returncode, 23)
                self.assertEqual(self.read_log(), ["sync:push"])

    def test_redirect_refuses_extra_and_global_arguments(self) -> None:
        cases = (("dolt", "push", "--force"), ("--quiet", "dolt", "pull"))
        for shell in SHELL_HELPERS:
            for args in cases:
                with self.subTest(shell=shell, args=args):
                    self.clear_log()
                    result = self.run_wrapper(shell, *args)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("refusing redirected", result.stderr)
                    self.assertEqual(self.read_log(), [])

    def test_directory_selectors_choose_the_sync_repo(self) -> None:
        selectors = (("-C", str(self.repo)), (f"--directory={self.repo}",))
        for shell in SHELL_HELPERS:
            for selector in selectors:
                with self.subTest(shell=shell, selector=selector):
                    self.clear_log()
                    result = self.run_wrapper(
                        shell,
                        *selector,
                        "dolt",
                        "pull",
                        cwd=self.other_repo,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.read_log(), ["sync:pull"])

    def test_other_dolt_commands_pass_through(self) -> None:
        for shell in SHELL_HELPERS:
            with self.subTest(shell=shell):
                self.clear_log()
                result = self.run_wrapper(shell, "dolt", "status")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("native:dolt status", result.stdout)
                self.assertEqual(self.read_log(), ["native:dolt status"])

    def test_pull_and_push_pass_through_without_sync_asset(self) -> None:
        for shell in SHELL_HELPERS:
            for action in ("pull", "push"):
                with self.subTest(shell=shell, action=action):
                    self.clear_log()
                    result = self.run_wrapper(
                        shell, "dolt", action, cwd=self.other_repo
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.read_log(), [f"native:dolt {action}"])

    def test_command_bd_bypasses_the_wrapper(self) -> None:
        for shell in SHELL_HELPERS:
            with self.subTest(shell=shell):
                self.clear_log()
                result = self.run_direct(shell, "dolt", "push")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("redirecting", result.stderr)
                self.assertEqual(self.read_log(), ["native:dolt push"])

    def test_create_arguments_are_unchanged_and_snapshot_once(self) -> None:
        cases = (
            (("create", "No owner"), "native:create No owner"),
            (
                ("create", "Explicit owner", "--assignee", "someone"),
                "native:create Explicit owner --assignee someone",
            ),
        )
        for shell in SHELL_HELPERS:
            for args, native_call in cases:
                with self.subTest(shell=shell, args=args):
                    self.clear_log()
                    result = self.run_wrapper(shell, *args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        self.read_log(), [native_call, "sync:snapshot --if-due"]
                    )
                    self.assertNotIn("balaji", "\n".join(self.read_log()))

    def test_mutation_classifier_distinguishes_mixed_commands(self) -> None:
        writes = (
            ("update", "dots-1", "--title", "changed"),
            ("comments", "add", "dots-1", "note"),
            ("dep", "add", "dots-1", "dots-2"),
            ("label", "remove", "dots-1", "old"),
            ("restore", "snapshot.jsonl", "--apply"),
            ("remember", "fact"),
        )
        reads = (
            ("list",),
            ("show", "dots-1"),
            ("comments", "dots-1"),
            ("dep", "list", "dots-1"),
            ("label", "list-all"),
            ("epic", "status", "dots-1"),
            ("gate", "show", "dots-1"),
            ("merge-slot", "check", "dots-1"),
            ("todo", "list", "dots-1"),
            ("restore", "snapshot.jsonl"),
        )
        for shell in SHELL_HELPERS:
            for args in writes:
                with self.subTest(shell=shell, kind="write", args=args):
                    self.clear_log()
                    result = self.run_wrapper(shell, *args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.read_log()[-1], "sync:snapshot --if-due")
                    self.assertEqual(len(self.read_log()), 2)
            for args in reads:
                with self.subTest(shell=shell, kind="read", args=args):
                    self.clear_log()
                    result = self.run_wrapper(shell, *args)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.read_log(), [f"native:{' '.join(args)}"])

    def test_snapshot_hook_skips_failure_help_hook_opt_out_and_other_db(self) -> None:
        for shell in SHELL_HELPERS:
            with self.subTest(shell=shell, case="native failure"):
                self.clear_log()
                result = self.run_wrapper(
                    shell, "update", "dots-1", env_updates={"BD_TEST_NATIVE_EXIT": "31"}
                )
                self.assertEqual(result.returncode, 31)
                self.assertEqual(self.read_log(), ["native:update dots-1"])

            for name, env_updates in (
                ("hook", {"BD_GIT_HOOK": "1"}),
                ("opt out", {"BD_AUTO_SNAPSHOT": "0"}),
            ):
                with self.subTest(shell=shell, case=name):
                    self.clear_log()
                    result = self.run_wrapper(
                        shell, "update", "dots-1", env_updates=env_updates
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.read_log(), ["native:update dots-1"])

            with self.subTest(shell=shell, case="help"):
                self.clear_log()
                result = self.run_wrapper(shell, "update", "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.read_log(), ["native:update --help"])

            with self.subTest(shell=shell, case="snapshot failure"):
                self.clear_log()
                result = self.run_wrapper(
                    shell, "update", "dots-1", env_updates={"BD_TEST_SYNC_EXIT": "44"}
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("snapshot failed", result.stderr)

            with self.subTest(shell=shell, case="other database"):
                self.clear_log()
                metadata = self.repo / ".beads/metadata.json"
                metadata.write_text('{"dolt_database":"other"}\n', encoding="utf-8")
                try:
                    result = self.run_wrapper(shell, "update", "other-1")
                finally:
                    metadata.write_text('{"dolt_database":"dots"}\n', encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.read_log(), ["native:update other-1"])

    def test_direct_mutation_bypasses_snapshot_wrapper(self) -> None:
        for shell in SHELL_HELPERS:
            with self.subTest(shell=shell):
                self.clear_log()
                result = self.run_direct(shell, "update", "dots-1")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.read_log(), ["native:update dots-1"])


class BeadsSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp = Path(self.temp_dir.name)
        self.repo = self.temp / "repo"
        (self.repo / "assets").mkdir(parents=True)
        (self.repo / ".beads").mkdir()
        (self.repo / ".beads/metadata.json").write_text(
            '{"dolt_database":"dots"}\n', encoding="utf-8"
        )
        shutil.copy2(ROOT / "assets/beads-sync.sh", self.repo / "assets/beads-sync.sh")
        self.bin_dir = self.temp / "bin"
        self.bin_dir.mkdir()
        self.log = self.temp / "calls.log"
        write_executable(
            self.bin_dir / "bd",
            """
            #!/bin/sh
            printf 'bd:%s\n' "$*" >> "$BD_TEST_LOG"
            if [ "$1" != export ]; then exit 0; fi
            mode="${BD_TEST_EXPORT_MODE:-success}"
            [ "$mode" != fail ] || exit 17
            shift
            output=""
            while [ "$#" -gt 0 ]; do
              if [ "$1" = -o ]; then output="$2"; shift 2; else shift; fi
            done
            case "$mode" in
              success) printf '%s\n' '{"id":"dots-1"}' '{"id":"dots-2"}' > "$output" ;;
              invalid) printf '%s\n' '{not-json}' > "$output" ;;
              empty) : > "$output" ;;
            esac
            """,
        )
        self.root = self.temp / "share"
        self.root.mkdir()
        self.cache = self.temp / "cache"
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{self.env['PATH']}",
                "BD_TEST_LOG": str(self.log),
                "BD_SNAPSHOT_ROOT": str(self.root),
                "BD_SNAPSHOT_MACHINE": "Work Station/Ubuntu-24.04",
                "BD_SNAPSHOT_INTERVAL_SECONDS": "600",
                "BD_SNAPSHOT_RETENTION": "10",
                "BD_SNAPSHOT_DEADLINE_SECONDS": "2",
                "XDG_CACHE_HOME": str(self.cache),
            }
        )

    def run_sync(
        self, *args: str, env_updates: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = self.env.copy()
        if env_updates:
            env.update(env_updates)
        return subprocess.run(
            [str(self.repo / "assets/beads-sync.sh"), *args],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def snapshots(self) -> list[Path]:
        return sorted(self.root.glob("beads-snapshots/dots/*/*.jsonl"))

    def clear_attempt_marker(self) -> None:
        for marker in self.cache.glob("beads-snapshots/*.attempt"):
            marker.unlink()

    def test_forced_snapshot_normalizes_machine_and_validates_export(self) -> None:
        result = self.run_sync("snapshot")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SNAPSHOT:", result.stdout)
        snapshots = self.snapshots()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].parent.name, "work-station-ubuntu-24.04")
        self.assertRegex(snapshots[0].name, r"^dots-work-station-ubuntu-24\.04-.*-2\.jsonl$")
        self.assertEqual(len(snapshots[0].read_text(encoding="utf-8").splitlines()), 2)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("bd:export --all -o "))
        self.assertFalse(Path(calls[0].removeprefix("bd:export --all -o ")).exists())

    def test_due_snapshot_throttles_before_export_and_at_destination(self) -> None:
        first = self.run_sync("snapshot", "--if-due")
        self.assertEqual(first.returncode, 0, first.stderr)
        first_calls = self.log.read_text(encoding="utf-8").splitlines()

        second = self.run_sync("snapshot", "--if-due")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("attempt is still inside", second.stderr)
        self.assertEqual(self.log.read_text(encoding="utf-8").splitlines(), first_calls)

        self.clear_attempt_marker()
        third = self.run_sync("snapshot", "--if-due")
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertIn("newest snapshot is still inside", third.stdout)
        self.assertEqual(len(self.snapshots()), 1)
        self.assertEqual(len(self.log.read_text(encoding="utf-8").splitlines()), 2)

    def test_retention_prunes_only_completed_snapshots_for_this_machine(self) -> None:
        other_dir = self.root / "beads-snapshots/dots/another-machine"
        other_dir.mkdir(parents=True)
        other_snapshot = other_dir / "dots-another-machine-old-1.jsonl"
        other_snapshot.write_text('{"id":"other-1"}\n', encoding="utf-8")

        for _ in range(3):
            result = self.run_sync(
                "snapshot", env_updates={"BD_SNAPSHOT_RETENTION": "2"}
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        own = list(
            (self.root / "beads-snapshots/dots/work-station-ubuntu-24.04").glob(
                "*.jsonl"
            )
        )
        self.assertEqual(len(own), 2)
        self.assertTrue(other_snapshot.exists())
        self.assertFalse(list(self.root.rglob("*.tmp")))

    def test_export_failures_never_publish_and_due_mode_preserves_success(self) -> None:
        for mode in ("fail", "invalid", "empty"):
            with self.subTest(mode=mode, forced=True):
                result = self.run_sync(
                    "snapshot", env_updates={"BD_TEST_EXPORT_MODE": mode}
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.snapshots())

            self.clear_attempt_marker()
            with self.subTest(mode=mode, forced=False):
                result = self.run_sync(
                    "snapshot",
                    "--if-due",
                    env_updates={"BD_TEST_EXPORT_MODE": mode},
                )
                self.assertEqual(result.returncode, 0)
                self.assertIn("no snapshot published", result.stderr)
                self.assertFalse(self.snapshots())
            self.clear_attempt_marker()

    def test_worker_lock_recovery_and_hard_deadline(self) -> None:
        machine_dir = self.root / "beads-snapshots/dots/work-station-ubuntu-24.04"
        lock_dir = machine_dir / ".snapshot.lock"
        lock_dir.mkdir(parents=True)

        blocked = self.run_sync("snapshot")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("already active", blocked.stderr)
        self.assertFalse(self.snapshots())

        old = time.time() - 1201
        os.utime(lock_dir, (old, old))
        recovered = self.run_sync("snapshot")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(len(self.snapshots()), 1)
        self.assertFalse(lock_dir.exists())

        self.clear_attempt_marker()
        started = time.monotonic()
        timed_out = self.run_sync(
            "snapshot",
            env_updates={
                "BD_SNAPSHOT_DEADLINE_SECONDS": "1",
                "BD_SNAPSHOT_TEST_DELAY_SECONDS": "5",
            },
        )
        elapsed = time.monotonic() - started
        self.assertEqual(timed_out.returncode, 124)
        self.assertLess(elapsed, 3)
        self.assertIn("exceeded 1s deadline", timed_out.stderr)
        self.assertFalse(list(self.root.rglob("*.tmp")))

    def test_missing_root_and_automatic_opt_out_are_safe(self) -> None:
        missing = self.temp / "missing-share"
        forced = self.run_sync(
            "snapshot", env_updates={"BD_SNAPSHOT_ROOT": str(missing)}
        )
        self.assertNotEqual(forced.returncode, 0)
        self.assertIn("snapshot root is unavailable", forced.stderr)

        self.clear_attempt_marker()
        automatic = self.run_sync(
            "snapshot",
            "--if-due",
            env_updates={"BD_SNAPSHOT_ROOT": str(missing)},
        )
        self.assertEqual(automatic.returncode, 0)
        self.assertIn("automatic Beads JSONL snapshot skipped", automatic.stderr)

        self.clear_attempt_marker()
        before = len(self.log.read_text(encoding="utf-8").splitlines())
        disabled = self.run_sync(
            "snapshot", "--if-due", env_updates={"BD_AUTO_SNAPSHOT": "0"}
        )
        self.assertEqual(disabled.returncode, 0, disabled.stderr)
        self.assertIn("disabled", disabled.stderr)
        self.assertEqual(len(self.log.read_text(encoding="utf-8").splitlines()), before)


class BeadsSyncRemoteGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp = Path(self.temp_dir.name)
        self.repo = self.temp / "repo"
        (self.repo / "assets").mkdir(parents=True)
        (self.repo / ".beads").mkdir()
        (self.repo / ".beads/metadata.json").write_text(
            '{"dolt_database":"test","dolt_server_host":"127.0.0.1",'
            '"dolt_server_user":"root"}',
            encoding="utf-8",
        )
        (self.repo / ".beads/dolt-server.port").write_text("3307\n", encoding="utf-8")
        self.bin_dir = self.temp / "bin"
        self.bin_dir.mkdir()
        self.log = self.temp / "calls.log"
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{self.env['PATH']}",
                "BD_TEST_LOG": str(self.log),
            }
        )

    def test_posix_push_refuses_missing_remote_before_bd_calls(self) -> None:
        shutil.copy2(ROOT / "assets/beads-sync.sh", self.repo / "assets/beads-sync.sh")
        write_executable(
            self.bin_dir / "bd",
            """
            #!/bin/sh
            printf 'bd:%s\n' "$*" >> "$BD_TEST_LOG"
            exit 0
            """,
        )
        write_executable(
            self.bin_dir / "dolt",
            """
            #!/bin/sh
            printf 'dolt:%s\n' "$*" >> "$BD_TEST_LOG"
            printf 'name\n'
            exit 0
            """,
        )

        result = subprocess.run(
            [str(self.repo / "assets/beads-sync.sh"), "push", "--backup"],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("no Dolt remote configured; refusing push", result.stderr)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(line.startswith("dolt:") for line in calls))
        self.assertFalse(any(line.startswith("bd:") for line in calls))

    def test_posix_pull_and_push_snapshot_before_server_restart(self) -> None:
        shutil.copy2(ROOT / "assets/beads-sync.sh", self.repo / "assets/beads-sync.sh")
        write_executable(
            self.bin_dir / "bd",
            """
            #!/bin/sh
            printf 'bd:%s\n' "$*" >> "$BD_TEST_LOG"
            if [ "$1" = export ]; then
              shift
              while [ "$#" -gt 0 ]; do
                if [ "$1" = -o ]; then
                  printf '%s\n' '{"id":"test-1"}' > "$2"
                  break
                fi
                shift
              done
            fi
            exit 0
            """,
        )
        write_executable(
            self.bin_dir / "dolt",
            """
            #!/bin/sh
            printf 'dolt:%s\n' "$*" >> "$BD_TEST_LOG"
            case "$*" in
              *dolt_remotes*) printf 'name\norigin\n' ;;
              *active_branch*) printf 'branch\nmain\n' ;;
              *dolt_status*) printf 'table_name,is_ignored\n' ;;
            esac
            exit 0
            """,
        )
        share = self.temp / "share"
        share.mkdir()
        env = self.env.copy()
        env.update(
            {
                "BD_SNAPSHOT_ROOT": str(share),
                "BD_SNAPSHOT_MACHINE": "test-host",
                "XDG_CACHE_HOME": str(self.temp / "cache"),
            }
        )

        for action in ("pull", "push"):
            with self.subTest(action=action):
                self.log.unlink(missing_ok=True)
                result = subprocess.run(
                    [
                        str(self.repo / "assets/beads-sync.sh"),
                        action,
                        "--backup",
                    ],
                    cwd=self.repo,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.log.read_text(encoding="utf-8").splitlines()
                export_index = next(
                    index for index, call in enumerate(calls) if call.startswith("bd:export")
                )
                stop_index = calls.index("bd:dolt stop")
                self.assertLess(export_index, stop_index)

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed")
    def test_powershell_push_refuses_missing_remote_before_bd_calls(self) -> None:
        shutil.copy2(ROOT / "assets/beads-sync.ps1", self.repo / "assets/beads-sync.ps1")
        write_executable(
            self.bin_dir / "bd",
            """
            #!/bin/sh
            printf 'bd:%s\n' "$*" >> "$BD_TEST_LOG"
            exit 0
            """,
        )
        write_executable(
            self.bin_dir / "dolt",
            """
            #!/bin/sh
            printf 'dolt:%s\n' "$*" >> "$BD_TEST_LOG"
            printf 'name\n'
            exit 0
            """,
        )

        result = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-File",
                str(self.repo / "assets/beads-sync.ps1"),
                "push",
                "-Backup",
            ],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("no Dolt remote configured; refusing push", result.stderr)
        calls = self.log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(line.startswith("dolt:") for line in calls))
        self.assertFalse(any(line.startswith("bd:") for line in calls))


@unittest.skipUnless(shutil.which("pwsh"), "pwsh is not installed")
class PowerShellBeadsDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp = Path(self.temp_dir.name)
        self.bin_dir = self.temp / "bin"
        self.bin_dir.mkdir()
        self.repo = self.temp / "repo"
        (self.repo / "assets").mkdir(parents=True)
        subprocess.run(
            ["git", "init", "--quiet", str(self.repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.other_repo = self.temp / "other"
        self.other_repo.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", str(self.other_repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.log = self.temp / "calls.log"
        (self.repo / ".beads").mkdir()
        (self.repo / ".beads/metadata.json").write_text(
            '{"dolt_database":"dots"}\n', encoding="utf-8"
        )
        (self.bin_dir / "bd.ps1").write_text(
            "Add-Content -LiteralPath $env:BD_TEST_LOG -Value "
            "('native:' + ($args -join ' '))\n"
            "exit [int]$env:BD_TEST_NATIVE_EXIT\n",
            encoding="utf-8",
        )
        (self.repo / "assets/beads-sync.ps1").write_text(
            "param([string]$Action)\n"
            "Add-Content -LiteralPath $env:BD_TEST_LOG -Value ('sync:' + $Action)\n"
            "exit [int]$env:BD_TEST_SYNC_EXIT\n",
            encoding="utf-8",
        )
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bin_dir}{os.pathsep}{self.env['PATH']}",
                "HOME": str(self.temp / "home"),
                "USERPROFILE": str(self.temp / "home"),
                "BD_TEST_LOG": str(self.log),
                "BD_TEST_SYNC_EXIT": "0",
                "BD_TEST_NATIVE_EXIT": "0",
            }
        )

    def run_wrapper(self, *args: str) -> subprocess.CompletedProcess[str]:
        encoded_args = ",".join("'" + arg.replace("'", "''") + "'" for arg in args)
        command = (
            f". '{ROOT / 'private_Documents/PowerShell/Microsoft.PowerShell_profile.ps1'}'; "
            f"bd @({encoded_args}); exit $global:LASTEXITCODE"
        )
        return subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_direct(self, *args: str) -> subprocess.CompletedProcess[str]:
        encoded_args = ",".join("'" + arg.replace("'", "''") + "'" for arg in args)
        command = (
            f". '{ROOT / 'private_Documents/PowerShell/Microsoft.PowerShell_profile.ps1'}'; "
            "$nativeBd = (Get-Command bd -CommandType Application, ExternalScript | "
            "Select-Object -First 1).Source; "
            f"& $nativeBd @({encoded_args}); exit $global:LASTEXITCODE"
        )
        return subprocess.run(
            ["pwsh", "-NoProfile", "-Command", command],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_redirect_and_status_propagation(self) -> None:
        self.env["BD_TEST_SYNC_EXIT"] = "19"
        result = self.run_wrapper("dolt", "push")
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertIn("redirecting", result.stderr)
        self.assertEqual(self.log.read_text(encoding="utf-8").splitlines(), ["sync:push"])

    def test_rejection_and_passthrough(self) -> None:
        result = self.run_wrapper("dolt", "pull", "--force")
        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing redirected", result.stderr)
        self.assertFalse(self.log.exists())

        result = self.run_wrapper("dolt", "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(encoding="utf-8").splitlines(), ["native:dolt status"])

    def test_direct_native_resolution_bypasses_the_wrapper(self) -> None:
        result = self.run_direct("dolt", "push")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("redirecting", result.stderr)
        self.assertEqual(self.log.read_text(encoding="utf-8").splitlines(), ["native:dolt push"])

    def test_mutation_snapshot_preserves_native_exit_and_arguments(self) -> None:
        self.env["BD_TEST_SYNC_EXIT"] = "27"
        result = self.run_wrapper("create", "No owner")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("automatic Beads JSONL snapshot failed", result.stderr)
        self.assertEqual(
            self.log.read_text(encoding="utf-8").splitlines(),
            ["native:create No owner", "sync:snapshot"],
        )

        self.log.unlink(missing_ok=True)
        result = self.run_direct("update", "dots-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.log.read_text(encoding="utf-8").splitlines(),
            ["native:update dots-1"],
        )

    def test_directory_selectors_and_other_repo_passthrough(self) -> None:
        for selector in (("-C", str(self.repo)), (f"--directory={self.repo}",)):
            with self.subTest(selector=selector):
                self.log.unlink(missing_ok=True)
                result = self.run_wrapper(*selector, "dolt", "pull")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.log.read_text(encoding="utf-8").splitlines(),
                    ["sync:pull"],
                )

        self.log.unlink(missing_ok=True)
        result = self.run_wrapper("-C", str(self.other_repo), "dolt", "push")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.log.read_text(encoding="utf-8").splitlines(),
            [f"native:-C {self.other_repo} dolt push"],
        )


if __name__ == "__main__":
    unittest.main()
