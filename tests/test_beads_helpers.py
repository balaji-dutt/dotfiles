"""Regression tests for Beads shell dispatch and sync safety guards."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
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
        (self.bin_dir / "bd.ps1").write_text(
            "Add-Content -LiteralPath $env:BD_TEST_LOG -Value "
            "('native:' + ($args -join ' '))\n",
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
