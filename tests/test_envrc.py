"""Regression tests for the repository direnv port policy."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENVRC = ROOT / ".envrc"
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "bash is required to evaluate .envrc")
class EnvrcPortTests(unittest.TestCase):
    def source_envrc(
        self,
        *,
        wsl_distro: str | None = None,
        port: str | None = None,
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
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

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


if __name__ == "__main__":
    unittest.main()
