#!/usr/bin/env python3
"""Regression tests for opencode-notifier-bridge process detection."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bin" / "executable_opencode-notifier-bridge"

DIRECT_OWNER = """
import subprocess
import sys

process = subprocess.Popen(sys.argv[1:], start_new_session=True)
raise SystemExit(process.wait())
"""

INTERMEDIARY_OWNER = f"""
import os
import subprocess
import sys

code = {DIRECT_OWNER!r}
command = [os.environ["PYTHON_EXECUTABLE"], "-c", code, *sys.argv[1:]]
raise SystemExit(subprocess.run(command, check=False).returncode)
"""


class OpenCodeNotifierBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("bash") is None or shutil.which("ps") is None:
            raise unittest.SkipTest("bash and ps are required")

        probe = subprocess.run(
            [
                "ps",
                "-o",
                "ppid=",
                "-o",
                "comm=",
                "-o",
                "args=",
                "-p",
                str(os.getpid()),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            raise unittest.SkipTest("ps does not support the required fields")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp_path = Path(self.temp_dir.name)
        self.fake_bin = self.temp_path / "bin"
        self.fake_bin.mkdir()
        self.notifier_log = self.temp_path / "terminal-notifier.log"

        self._write_executable(
            "uname",
            "#!/bin/sh\nprintf '%s\\n' Darwin\n",
        )
        self._write_executable(
            "terminal-notifier",
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$NOTIFIER_LOG\"\n",
        )

    def _write_executable(self, name: str, content: str) -> None:
        file_path = self.fake_bin / name
        file_path.write_text(content, encoding="utf-8")
        file_path.chmod(0o755)

    def _run_bridge(
        self,
        event: str,
        *,
        owner_args: tuple[str, ...] = (),
        intermediary: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.notifier_log.unlink(missing_ok=True)
        env = os.environ.copy()
        env.pop("AOE_INSTANCE_ID", None)
        env.pop("TMUX", None)
        env.update(
            {
                "NOTIFIER_LOG": str(self.notifier_log),
                "PATH": f"{self.fake_bin}{os.pathsep}{env['PATH']}",
                "PYTHON_EXECUTABLE": sys.executable,
            }
        )
        if extra_env:
            env.update(extra_env)

        bridge_args = [
            "bash",
            str(BRIDGE),
            "--title",
            f"OpenCode - {event}",
            "--message",
            "test message",
            "--event",
            event,
        ]
        owner_code = INTERMEDIARY_OWNER if intermediary else DIRECT_OWNER
        command = ["opencode", "-c", owner_code, *bridge_args, *owner_args]
        return subprocess.run(
            command,
            executable=sys.executable,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    def _assert_delivered(self) -> None:
        self.assertTrue(self.notifier_log.exists())
        self.assertIn("test message", self.notifier_log.read_text(encoding="utf-8"))

    def _assert_suppressed(self) -> None:
        self.assertFalse(self.notifier_log.exists())

    def test_auto_permission_is_suppressed(self) -> None:
        result = self._run_bridge("permission", owner_args=("--auto",))

        self.assertEqual(result.returncode, 0, result.stderr)
        self._assert_suppressed()

    def test_non_auto_permission_is_delivered(self) -> None:
        result = self._run_bridge("permission")

        self.assertEqual(result.returncode, 0, result.stderr)
        self._assert_delivered()

    def test_auto_question_is_delivered(self) -> None:
        result = self._run_bridge("question", owner_args=("--auto",))

        self.assertEqual(result.returncode, 0, result.stderr)
        self._assert_delivered()

    def test_auto_non_permission_event_is_delivered(self) -> None:
        result = self._run_bridge("session_completed", owner_args=("--auto",))

        self.assertEqual(result.returncode, 0, result.stderr)
        self._assert_delivered()

    def test_lookalike_auto_argument_does_not_suppress(self) -> None:
        result = self._run_bridge("permission", owner_args=("--automatic",))

        self.assertEqual(result.returncode, 0, result.stderr)
        self._assert_delivered()

    def test_intermediary_process_is_tolerated(self) -> None:
        result = self._run_bridge(
            "permission",
            owner_args=("--auto",),
            intermediary=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self._assert_suppressed()

    def test_process_inspection_failure_fails_open(self) -> None:
        self._write_executable("ps", "#!/bin/sh\nexit 1\n")
        result = self._run_bridge("permission", owner_args=("--auto",))

        self.assertEqual(result.returncode, 0, result.stderr)
        self._assert_delivered()

    def test_aoe_suppression_is_unchanged(self) -> None:
        result = self._run_bridge(
            "question",
            extra_env={"AOE_INSTANCE_ID": "test-instance"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self._assert_suppressed()


if __name__ == "__main__":
    unittest.main()
