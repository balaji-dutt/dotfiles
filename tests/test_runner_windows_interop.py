"""Exercise the runner's Windows step supervision through WSL2 interop.

This is Windows evidence from a \\\\wsl.localhost checkout, not from a native Windows checkout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from tests.support.powershell import is_wsl, powershell_path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "assets" / "run-tests.py"
PY = shutil.which("py.exe")
TASKLIST = shutil.which("tasklist.exe")
TASKKILL = shutil.which("taskkill.exe")


@unittest.skipUnless(is_wsl() and PY and TASKLIST and TASKKILL, "WSL2 with Windows Python interop is required")
class RunnerWindowsInteropTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="runner-windows-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "tests").mkdir()
        (self.root / "configs").mkdir()
        self.pid_files: list[Path] = []
        self.addCleanup(self.stop_recorded_processes)

    def windows(self, path: Path) -> str:
        return powershell_path(path, PY)

    def pid_file(self, name: str) -> Path:
        path = self.root / name
        self.pid_files.append(path)
        return path

    def alive(self, pid: int) -> bool:
        listing = subprocess.run(
            [TASKLIST, "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30,
        )
        return f'"{pid}"' in listing.stdout

    def stop_recorded_processes(self) -> None:
        for path in self.pid_files:
            if path.exists():
                pid = int(path.read_text(encoding="utf-8").strip())
                if self.alive(pid):
                    subprocess.run([TASKKILL, "/PID", str(pid), "/F"], check=False,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)

    def wait_for(self, path: Path, timeout: float = 30) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and path.read_text(encoding="utf-8").strip():
                return int(path.read_text(encoding="utf-8").strip())
            time.sleep(0.2)
        self.fail(f"{path.name} was not written")

    def test_runner_kills_owned_tree_and_records_outcomes(self) -> None:
        step_pid, grandchild_pid = self.pid_file("step.pid"), self.pid_file("grandchild.pid")
        sentinel_pid = self.pid_file("sentinel.pid")
        (self.root / "tests" / "hang.py").write_text(textwrap.dedent(f"""
            import os, pathlib, subprocess, sys, time
            grandchild = (
                "import os, pathlib, sys, time; "
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                "time.sleep(120)"
            )
            marker = pathlib.Path({self.windows(grandchild_pid)!r})
            subprocess.Popen([sys.executable, "-c", grandchild, str(marker)])
            deadline = time.monotonic() + 15
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            pathlib.Path({self.windows(step_pid)!r}).write_text(str(os.getpid()))
            time.sleep(120)
        """), encoding="utf-8")
        (self.root / "tests" / "ok.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        registry = self.root / "configs" / "test-suites.json"
        registry.write_text(json.dumps({
            "$schema": "./schemas/test-suites.v1.schema.json",
            "schema_version": 1,
            "capabilities": {},
            "steps": [
                {"id": "a-hang", "suites": ["fast"], "argv": ["{python}", "{repo}/tests/hang.py"],
                 "covers": ["tests/hang.py"], "timeout_seconds": 20},
                {"id": "b-missing", "suites": ["fast"], "argv": ["{repo}/tests/missing-tool.exe"],
                 "covers": ["tests/ok.py"]},
                {"id": "c-ok", "suites": ["fast"], "argv": ["{python}", "{repo}/tests/ok.py"],
                 "covers": ["tests/ok.py"]},
            ],
        }), encoding="utf-8")
        sentinel = subprocess.Popen(
            [PY, "-3", "-c",
             f"import os, pathlib, time; pathlib.Path({self.windows(sentinel_pid)!r})"
             ".write_text(str(os.getpid())); time.sleep(120)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(sentinel.wait, 30)
        self.addCleanup(sentinel.kill)
        sentinel_windows_pid = self.wait_for(sentinel_pid)

        report = self.root / "report.json"
        result = subprocess.run(
            [PY, "-3", self.windows(RUNNER), "--repo-root", self.windows(self.root),
             "--registry", self.windows(registry), "fast", "--case-report",
             "--report-file", self.windows(report)],
            check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(payload["state"], "complete")
        steps = {step["id"]: step for step in payload["steps"]}
        self.assertEqual((steps["a-hang"]["outcome"], steps["a-hang"]["reason"]),
                         ("timeout", "timeout after 20 seconds"))
        self.assertEqual(steps["a-hang"]["termination"]["kind"], "timeout")
        self.assertIsNone(steps["a-hang"]["termination"]["cleanup_error"])
        self.assertEqual(steps["b-missing"]["outcome"], "launch_error")
        self.assertEqual(steps["c-ok"]["outcome"], "passed")

        for path in (step_pid, grandchild_pid):
            pid = self.wait_for(path, timeout=1)
            self.assertFalse(self.alive(pid), f"{path.name} process {pid} survived")
        self.assertTrue(self.alive(sentinel_windows_pid), "unrelated Windows process was stopped")

    def test_orphaned_pipe_holder_is_reported_without_hanging(self) -> None:
        orphan_pid = self.pid_file("orphan.pid")
        (self.root / "tests" / "orphan.py").write_text(textwrap.dedent(f"""
            import pathlib, subprocess, sys, time
            sleeper = (
                "import os, pathlib, sys, time; "
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                "time.sleep(120)"
            )
            marker = pathlib.Path({self.windows(orphan_pid)!r})
            subprocess.Popen([sys.executable, "-c", sleeper, str(marker)])
            deadline = time.monotonic() + 15
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            print("parent exiting", flush=True)
        """), encoding="utf-8")
        registry = self.root / "configs" / "test-suites.json"
        registry.write_text(json.dumps({
            "$schema": "./schemas/test-suites.v1.schema.json",
            "schema_version": 1,
            "capabilities": {},
            "steps": [{"id": "orphan", "suites": ["fast"], "argv": ["{python}", "{repo}/tests/orphan.py"],
                       "covers": ["tests/orphan.py"], "timeout_seconds": 20}],
        }), encoding="utf-8")
        report = self.root / "orphan-report.json"
        started = time.monotonic()
        result = subprocess.run(
            [PY, "-3", self.windows(RUNNER), "--repo-root", self.windows(self.root),
             "--registry", self.windows(registry), "fast", "--case-report",
             "--report-file", self.windows(report)],
            check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
        )
        self.assertLess(time.monotonic() - started, 90)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        step = json.loads(report.read_text(encoding="utf-8"))["steps"][0]
        self.assertEqual(step["outcome"], "timeout")
        self.assertIn("step output did not drain", step["termination"]["cleanup_error"])
        self.assertTrue(self.alive(self.wait_for(orphan_pid, timeout=1)),
                        "the orphan should be out of taskkill's reach; cleanup stops it")


if __name__ == "__main__":
    unittest.main()
