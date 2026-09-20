"""Regression tests for the repository direnv port policy."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENVRC = ROOT / ".envrc"
BASH = shutil.which("bash")


class EnvrcHarness(unittest.TestCase):
    """Evaluates .envrc in a throwaway checkout and reports the exported port."""

    def setUp(self) -> None:
        # .envrc writes a dolt.port pin relative to $PWD, so every case runs in
        # a throwaway checkout rather than the working tree it is testing.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)

    @property
    def local_config(self) -> Path:
        return self.workdir / ".beads" / "config.local.yaml"

    def write_local_config(self, contents: str) -> None:
        self.local_config.parent.mkdir(parents=True, exist_ok=True)
        self.local_config.write_text(contents, encoding="utf-8")

    def source_envrc(
        self,
        *,
        wsl_distro: str | None = None,
        port: str | None = None,
        cwd: Path | None = None,
    ) -> str:
        env = {"PATH": os.environ["PATH"]}
        if wsl_distro is not None:
            env["WSL_DISTRO_NAME"] = wsl_distro
        if port is not None:
            env["BEADS_DOLT_SERVER_PORT"] = port

        result = subprocess.run(
            [
                BASH,
                "-c",
                """
                PATH_add() { :; }
                source "$1"
                if [ "${BEADS_DOLT_SERVER_PORT+x}" = x ]; then
                  printf 'set:%s\n' "$BEADS_DOLT_SERVER_PORT"
                else
                  printf 'unset\n'
                fi
                """,
                "envrc-test",
                str(ENVRC),
            ],
            cwd=cwd or self.workdir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()


@unittest.skipUnless(BASH, "bash is required to evaluate .envrc")
class EnvrcPortTests(EnvrcHarness):
    def test_non_wsl_does_not_generate_port(self) -> None:
        for wsl_distro in (None, ""):
            with self.subTest(wsl_distro=wsl_distro):
                self.assertEqual(self.source_envrc(wsl_distro=wsl_distro), "unset")

    def test_wsl_generates_stable_high_port(self) -> None:
        first = self.source_envrc(wsl_distro="Debian")
        second = self.source_envrc(wsl_distro="Debian")

        self.assertTrue(first.startswith("set:"), first)
        self.assertEqual(first, second)
        port = int(first.removeprefix("set:"))
        self.assertGreaterEqual(port, 20000)
        self.assertLessEqual(port, 39999)

    def test_explicit_port_is_preserved(self) -> None:
        for wsl_distro in (None, "Debian"):
            with self.subTest(wsl_distro=wsl_distro):
                self.assertEqual(
                    self.source_envrc(wsl_distro=wsl_distro, port="45678"),
                    "set:45678",
                )


@unittest.skipUnless(BASH, "bash is required to evaluate .envrc")
class EnvrcDoltPortPinTests(EnvrcHarness):
    def test_pin_is_written_when_beads_dir_exists(self) -> None:
        (self.workdir / ".beads").mkdir()

        result = self.source_envrc(wsl_distro="Debian")

        port = result.removeprefix("set:")
        self.assertEqual(
            self.local_config.read_text(encoding="utf-8"),
            f"dolt.port: {port}\n",
        )

    def test_existing_pin_wins_over_the_computed_port(self) -> None:
        self.write_local_config('sync:\n  remote: "git+ssh://example"\ndolt.port: 31234\n')

        self.assertEqual(self.source_envrc(wsl_distro="Debian"), "set:31234")

    def test_pin_is_not_duplicated_on_repeated_evaluation(self) -> None:
        (self.workdir / ".beads").mkdir()

        first = self.source_envrc(wsl_distro="Debian")
        second = self.source_envrc(wsl_distro="Debian")

        self.assertEqual(first, second)
        contents = self.local_config.read_text(encoding="utf-8")
        self.assertEqual(contents.count("dolt.port:"), 1, contents)

    def test_existing_content_is_preserved(self) -> None:
        original = 'sync:\n  remote: "git+ssh://example"\n'
        self.write_local_config(original)

        self.source_envrc(wsl_distro="Debian")

        contents = self.local_config.read_text(encoding="utf-8")
        self.assertTrue(contents.startswith(original), contents)
        self.assertEqual(contents.count("dolt.port:"), 1, contents)

    def test_no_pin_without_a_beads_dir(self) -> None:
        self.assertTrue(self.source_envrc(wsl_distro="Debian").startswith("set:"))
        self.assertFalse(self.local_config.exists())

    def test_non_wsl_never_writes_a_pin(self) -> None:
        (self.workdir / ".beads").mkdir()

        self.assertEqual(self.source_envrc(), "unset")
        self.assertFalse(self.local_config.exists())

    def test_explicit_port_never_writes_a_pin(self) -> None:
        (self.workdir / ".beads").mkdir()

        self.assertEqual(
            self.source_envrc(wsl_distro="Debian", port="45678"), "set:45678"
        )
        self.assertFalse(self.local_config.exists())


@unittest.skipUnless(BASH, "bash is required to evaluate .envrc")
class EnvrcIntegrationTests(EnvrcHarness):
    def test_repo_paths_and_sync_hint_are_exported(self) -> None:
        path_log = self.workdir / "path-add.log"
        env = {
            "HOME": str(self.workdir),
            "PATH": os.environ["PATH"],
            "PATH_LOG": str(path_log),
        }
        result = subprocess.run(
            [
                BASH,
                "-c",
                r"""
                PATH_add() { printf '%s\n' "$1" > "$PATH_LOG"; }
                source "$1"
                printf '%s\n%s\n' "$DOTFILES_DEVCONTAINER_SYNC_ALL_CMD" "$BEADS_DOLT_CLI_DIR"
                """,
                "envrc-test",
                str(ENVRC),
            ],
            cwd=self.workdir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path_log.read_text(encoding="utf-8"), f"{self.workdir}/assets\n")
        self.assertEqual(
            result.stdout.splitlines(),
            ["sync-devcontainer-all.sh", f"{self.workdir}/.beads/dolt"],
        )


if __name__ == "__main__":
    unittest.main()
