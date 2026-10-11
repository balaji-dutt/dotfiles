from __future__ import annotations

import ast
import contextlib
import errno
import io
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.support import powershell


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "assets" / "run-tests.py"
POSIX_WRAPPER = REPO_ROOT / "assets" / "run-tests.sh"
POWERSHELL_WRAPPER = REPO_ROOT / "assets" / "run-tests.ps1"
REGISTRY = REPO_ROOT / "configs" / "test-suites.json"


PWSH_NAMES = {"pwsh", "pwsh.exe"}


def bypassed_pwsh_lookups(source: str) -> list[int]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            first = node.args[0] if node.args else None
            if name == "which" and isinstance(first, ast.Constant) and first.value in PWSH_NAMES:
                found.append(node.lineno)
        elif isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value in PWSH_NAMES:
                found.append(node.lineno)
    return sorted(found)


def unscoped_mock_assertions(source: str) -> list[int]:
    found = []
    start: int | None = None
    logical = ""
    for number, line in enumerate(source.splitlines(), 1):
        start = number if start is None else start
        if line.rstrip().endswith("`"):
            logical += line.rstrip()[:-1] + " "
            continue
        logical += line
        if (not logical.lstrip().startswith("#")
                and re.search(r"\bAssert-MockCalled\b", logical, re.IGNORECASE)
                and not re.search(r"\s-Scope\s+\w", logical, re.IGNORECASE)):
            found.append(start)
        start, logical = None, ""
    return found


def resolve_wrapper_bash() -> str | None:
    """Return a bash that can see this checkout, or None.

    Windows ships a bash in the system directory that launches WSL2, where a
    drive-letter path does not exist. It answers both a bare argv name and a
    PATH lookup, so the candidate has to be probed rather than trusted.
    """
    candidate = shutil.which("bash")
    if candidate is None:
        return None
    probe = subprocess.run(
        [candidate, "-c", f'test -f "{POSIX_WRAPPER.as_posix()}"'],
        check=False,
        capture_output=True,
    )
    return candidate if probe.returncode == 0 else None


BASH = resolve_wrapper_bash()


class RunnerFixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "configs").mkdir()
        (self.root / "tests").mkdir()
        self.write_script("pass.py", "raise SystemExit(0)\n")
        self.steps = [self.step("alpha", ["fast", "integration"], "pass.py")]
        self.capabilities: dict[str, dict[str, object]] = {}
        self.write_registry()

    def cleanup(self) -> None:
        self.temporary.cleanup()

    @property
    def registry(self) -> Path:
        return self.root / "configs" / "test-suites.json"

    def write_script(self, name: str, content: str) -> Path:
        path = self.root / "tests" / name
        path.write_text(content, encoding="utf-8")
        return path

    def step(
        self,
        step_id: str,
        suites: list[str],
        script: str,
        **extra: object,
    ) -> dict[str, object]:
        value: dict[str, object] = {
            "id": step_id,
            "suites": suites,
            "argv": ["{python}", f"{{repo}}/tests/{script}"],
            "covers": [f"tests/{script}"],
        }
        value.update(extra)
        return value

    def write_registry(self) -> None:
        self.registry.write_text(
            json.dumps(
                {
                    "$schema": "./schemas/test-suites.v1.schema.json",
                    "schema_version": 1,
                    "capabilities": self.capabilities,
                    "steps": self.steps,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command(*args),
            env=env,
            check=False,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def command(self, *args: str) -> list[str]:
        return [sys.executable, str(RUNNER), "--repo-root", str(self.root),
                "--registry", str(self.registry), *args]

    def supervised(self, *args: str) -> subprocess.CompletedProcess[str]:
        with (tempfile.TemporaryFile(mode="w+t") as output,
              tempfile.TemporaryFile(mode="w+t") as errors):
            process = subprocess.Popen(self.command(*args), stdout=output, stderr=errors,
                                       text=True, start_new_session=True)
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
                raise AssertionError("runner exceeded its outer watchdog")
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
            output.seek(0)
            errors.seek(0)
            return subprocess.CompletedProcess(process.args, process.returncode,
                                               output.read(), errors.read())


@unittest.skipIf(os.name == "nt", "POSIX process groups")
class RunnerSupervisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RunnerFixture()
        self.addCleanup(self.fixture.cleanup)
        self.pids: list[Path] = []
        self.addCleanup(self.stop_fixture_groups)

    def stop_fixture_groups(self) -> None:
        for pid_file in self.pids:
            if not pid_file.exists():
                continue
            pid = int(pid_file.read_text(encoding="utf-8"))
            try:
                group = os.getpgid(pid)
            except ProcessLookupError:
                continue
            command = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                     text=True, capture_output=True, check=False)
            if command.returncode == 0 and str(self.fixture.root) in command.stdout:
                os.killpg(group, signal.SIGKILL)

    def fixture_pid(self, name: str) -> Path:
        path = self.fixture.root / name
        self.pids.append(path)
        return path

    def assert_stopped(self, pid_file: Path) -> None:
        pid = int(pid_file.read_text(encoding="utf-8"))
        for _ in range(30):
            status = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                    text=True, capture_output=True, check=False)
            if status.returncode != 0 or status.stdout.lstrip().startswith("Z"):
                return
            time.sleep(0.05)
        self.fail(f"fixture process {pid} survived")

    def test_timeout_validation_and_live_override(self) -> None:
        for invalid in (True, False, 0, -1, "1", 1.5, None, {}):
            with self.subTest(value=invalid):
                self.fixture.steps[0]["timeout_seconds"] = invalid
                self.fixture.write_registry()
                result = self.fixture.supervised("--list")
                self.assertEqual(result.returncode, 2)
                self.assertIn("timeout_seconds must be a positive integer", result.stderr)
        self.fixture.steps[0]["timeout_seconds"] = 1
        self.fixture.write_registry()
        self.assertEqual(self.fixture.supervised("--list").returncode, 0)
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        live = next(step for step in registry["steps"] if step["id"] == "devcontainer-smoke-live")
        self.assertEqual(live["timeout_seconds"], 1200)
        schema = json.loads((REPO_ROOT / "configs/schemas/test-suites.v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$defs"]["step"]["properties"]["timeout_seconds"],
                         {"type": "integer", "minimum": 1})

    def test_hung_step_fails_then_next_step_runs_and_report_is_written(self) -> None:
        pid_file = self.fixture_pid("hung.pid")
        self.fixture.write_script("pass.py", "import os, pathlib, time\n"
                                  f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
                                  "print('hung diagnostic', flush=True)\n"
                                  "time.sleep(30)\n")
        self.fixture.write_script("second.py", "raise SystemExit(0)\n")
        self.fixture.steps[0]["timeout_seconds"] = 1
        self.fixture.steps.append(self.fixture.step("beta", ["fast"], "second.py"))
        self.fixture.write_registry()
        report = self.fixture.root / "result.json"
        result = self.fixture.supervised("--report-file", str(report))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("hung diagnostic", result.stdout)
        self.assertIn("FAIL alpha: timeout after 1 seconds", result.stdout)
        self.assertNotIn("cleanup failed", result.stdout)
        self.assertIn("PASS beta", result.stdout)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(payload["exit_code"], 1)
        self.assertEqual(payload["steps"][0]["exit_code"], None)
        self.assertEqual(payload["steps"][0]["reason"], "timeout after 1 seconds")
        self.assert_stopped(pid_file)

    def test_parent_exits_with_pipe_holding_descendant(self) -> None:
        child_pid = self.fixture_pid("descendant.pid")
        self.fixture.write_script("pass.py", "import pathlib, subprocess, sys, time\n"
                                  f"pid_path = pathlib.Path({str(child_pid)!r})\n"
                                  "child = subprocess.Popen([sys.executable, '-c', "
                                  "'import time; time.sleep(30)', str(pid_path)])\n"
                                  "pid_path.write_text(str(child.pid))\n"
                                  "print('parent diagnostic', flush=True)\n")
        self.fixture.steps[0]["timeout_seconds"] = 1
        self.fixture.write_registry()
        result = self.fixture.supervised()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("parent diagnostic", result.stdout)
        self.assertIn("timeout after 1 seconds", result.stdout)
        self.assertNotIn("cleanup failed", result.stdout)
        self.assert_stopped(child_pid)

    def test_term_resistant_descendant_is_killed(self) -> None:
        child_pid = self.fixture_pid("resistant.pid")
        self.fixture.write_script("pass.py", "import pathlib, subprocess, sys, time\n"
                                  f"pid_path = pathlib.Path({str(child_pid)!r})\n"
                                  "child = subprocess.Popen([sys.executable, '-c', "
                                  "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)', "
                                  "str(pid_path)])\n"
                                  "pid_path.write_text(str(child.pid))\n"
                                  "time.sleep(30)\n")
        self.fixture.steps[0]["timeout_seconds"] = 1
        self.fixture.write_registry()
        result = self.fixture.supervised()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("timeout after 1 seconds", result.stdout)
        self.assertNotIn("cleanup failed", result.stdout)
        self.assert_stopped(child_pid)

    def test_detached_pipe_holder_reports_cleanup_failure_without_hanging(self) -> None:
        child_pid = self.fixture_pid("detached.pid")
        self.fixture.write_script("pass.py", "import pathlib, subprocess, sys, time\n"
                                  f"pid_path = pathlib.Path({str(child_pid)!r})\n"
                                  "child = subprocess.Popen([sys.executable, '-c', "
                                  "'import os, time; os.setsid(); time.sleep(30)', "
                                  "str(pid_path)])\n"
                                  "pid_path.write_text(str(child.pid))\n")
        self.fixture.steps[0]["timeout_seconds"] = 1
        self.fixture.write_registry()
        result = self.fixture.supervised()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("timeout after 1 seconds; cleanup failed:", result.stdout)
        self.assertIn("step output did not drain", result.stdout)
        self.stop_fixture_groups()
        self.assert_stopped(child_pid)

    def test_interruption_stops_the_step_group(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=signum):
                self.check_interruption(signum)

    def check_interruption(self, signum: int) -> None:
        pid_file = self.fixture_pid(f"interrupt-{signum}.pid")
        self.fixture.write_script("pass.py", "import os, pathlib, time\n"
                                  f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
                                  "time.sleep(30)\n")
        self.fixture.write_registry()
        with (tempfile.TemporaryFile(mode="w+t") as output,
              tempfile.TemporaryFile(mode="w+t") as errors):
            runner = subprocess.Popen(self.fixture.command(), stdout=output, stderr=errors,
                                      start_new_session=True)
            try:
                deadline = time.monotonic() + 5
                while not pid_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(pid_file.exists(), "step did not start")
                os.kill(runner.pid, signum)
                runner.wait(timeout=6)
                self.assertNotEqual(runner.returncode, 0)
                self.assert_stopped(pid_file)
            finally:
                if runner.poll() is None:
                    os.killpg(runner.pid, signal.SIGKILL)
                    runner.wait(timeout=2)


class TestRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RunnerFixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_default_suite_and_all_are_deterministic(self) -> None:
        default = self.fixture.run()
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertIn("PASS alpha", default.stdout)
        self.assertIn("SUMMARY pass=1 skip=0 fail=0", default.stdout)

        self.fixture.write_script("second.py", "raise SystemExit(0)\n")
        self.fixture.steps.append(self.fixture.step("beta", ["integration"], "second.py"))
        self.fixture.write_registry()
        all_result = self.fixture.run("all")
        self.assertEqual(all_result.returncode, 0, all_result.stderr)
        self.assertEqual(all_result.stdout.count("PASS alpha"), 1)
        self.assertLess(all_result.stdout.index("PASS alpha"), all_result.stdout.index("PASS beta"))

    def test_list_validates_without_executing(self) -> None:
        self.fixture.write_script("pass.py", "raise SystemExit(19)\n")
        result = self.fixture.run("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("alpha\tfast,integration", result.stdout)
        self.assertIn("Selected steps: 1", result.stdout)
        self.assertNotIn("PASS alpha", result.stdout)

    def test_child_failure_returns_one(self) -> None:
        self.fixture.write_script("pass.py", "raise SystemExit(19)\n")
        result = self.fixture.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL alpha: exit 19", result.stdout)
        self.assertIn("SUMMARY pass=0 skip=0 fail=1", result.stdout)

    def test_utf8_child_output_preserves_results_and_report(self) -> None:
        report = self.fixture.root / "report.json"
        self.fixture.write_script(
            "pass.py",
            "import sys\n"
            "sys.stdout.buffer.write('stdout — ü\\n'.encode('utf-8'))\n"
            "sys.stderr.buffer.write('stderr — ü\\n'.encode('utf-8'))\n",
        )
        self.fixture.write_script(
            "fail.py",
            "import sys\n"
            "sys.stdout.buffer.write('failed — ü\\n'.encode('utf-8'))\n"
            "sys.stderr.buffer.write('diagnostic — ü\\n'.encode('utf-8'))\n"
            "raise SystemExit(23)\n",
        )
        self.fixture.steps.append(self.fixture.step("beta", ["fast"], "fail.py"))
        self.fixture.write_registry()

        result = self.fixture.run(
            "--report-file", str(report),
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        for value in ("stdout — ü", "failed — ü"):
            self.assertIn(value, result.stdout)
        for value in ("stderr — ü", "diagnostic — ü"):
            self.assertIn(value, result.stderr)
        self.assertIn("PASS alpha", result.stdout)
        self.assertIn("FAIL beta: exit 23", result.stdout)
        self.assertIn("SUMMARY pass=1 skip=0 fail=1", result.stdout)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(payload["totals"], {"fail": 1, "pass": 1, "skip": 0})
        self.assertEqual([step["status"] for step in payload["steps"]], ["pass", "fail"])

    def test_invalid_registry_returns_two(self) -> None:
        self.fixture.steps[0]["argv"] = ["{python}", "{unsupported}"]
        self.fixture.write_registry()
        result = self.fixture.run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("ERROR:", result.stderr)
        self.assertIn("unsupported placeholder", result.stderr)

        self.fixture.steps[0]["argv"] = ["{}"]
        self.fixture.write_registry()
        empty = self.fixture.run()
        self.assertEqual(empty.returncode, 2)
        self.assertIn("unsupported placeholder", empty.stderr)

    def test_duplicate_ids_and_missing_covered_paths_fail_validation(self) -> None:
        self.fixture.steps.append(dict(self.fixture.steps[0]))
        self.fixture.write_registry()
        duplicate = self.fixture.run()
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("duplicates", duplicate.stderr)

        self.fixture.steps = [self.fixture.step("alpha", ["fast"], "missing.py")]
        self.fixture.write_registry()
        missing = self.fixture.run()
        self.assertEqual(missing.returncode, 2)
        self.assertIn("missing file", missing.stderr)

    def test_missing_capability_skips_locally_and_fails_when_required(self) -> None:
        self.fixture.capabilities = {"absent": {"command": "definitely-not-a-real-command"}}
        self.fixture.steps[0]["requires"] = ["absent"]
        self.fixture.write_registry()
        local = self.fixture.run()
        self.assertEqual(local.returncode, 0, local.stderr)
        self.assertIn("SKIP alpha: missing capability absent", local.stdout)
        strict = self.fixture.run("--require-capabilities")
        self.assertEqual(strict.returncode, 1)
        self.assertIn("FAIL alpha: missing capability absent", strict.stdout)

    def test_guard_shim_does_not_satisfy_bd_capability(self) -> None:
        empty_bin = self.fixture.root / "empty-bin"
        empty_bin.mkdir()
        self.fixture.capabilities = {"bd": {"command": "bd", "probe": ["bd", "--version"]}}
        self.fixture.steps[0]["requires"] = ["bd"]
        self.fixture.write_registry()
        env = {**os.environ, "PATH": str(empty_bin), "BASH_FUNC_bd%%": "() { return 0; }"}

        skipped = self.fixture.run(env=env)
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn("SKIP alpha: missing capability bd (bd)", skipped.stdout)
        self.assertNotIn("RUN  alpha", skipped.stdout)

        strict = self.fixture.run("--require-capability", "bd", env=env)
        self.assertEqual(strict.returncode, 1, strict.stderr)
        self.assertIn("FAIL alpha: missing capability bd (bd)", strict.stdout)
        self.assertNotIn("RUN  alpha", strict.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_isolated_beads_step_receives_real_executable_without_unblocking_path(self) -> None:
        fake_bin = self.fixture.root / "fake-bin"
        fake_bin.mkdir()
        fake_bd = fake_bin / "bd"
        fake_bd.write_text("#!/bin/sh\n[ \"$1\" = --version ]\n", encoding="utf-8")
        fake_bd.chmod(0o755)
        output = self.fixture.root / "bd-paths.json"
        self.fixture.write_script(
            "pass.py",
            "import json, os, pathlib, shutil, subprocess\n"
            "bd = os.environ['DOTFILES_TEST_BD']\n"
            "guard = shutil.which('bd')\n"
            "result = subprocess.run([guard, '--version'], capture_output=True)\n"
            f"pathlib.Path({str(output)!r}).write_text(json.dumps({{"
            "'bd': bd, 'guard': guard, 'guard_exit': result.returncode, "
            "'bd_exit': subprocess.run([bd, '--version']).returncode}))\n",
        )
        self.fixture.capabilities = {"bd": {"command": "bd", "probe": ["bd", "--version"]}}
        self.fixture.steps = [
            self.fixture.step("beads-isolated-worktree", ["fast"], "pass.py", requires=["bd"])
        ]
        self.fixture.write_registry()
        env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', os.defpath)}"}
        env["BASH_FUNC_bd%%"] = "() { return 88; }"

        result = self.fixture.run(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS beads-isolated-worktree", result.stdout)
        actual = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(actual["bd"], str(fake_bd.resolve()))
        self.assertEqual(actual["bd_exit"], 0)
        self.assertEqual(actual["guard_exit"], 97)
        self.assertNotEqual(actual["guard"], actual["bd"])

        self.fixture.steps = [self.fixture.step("alpha", ["fast"], "pass.py")]
        self.fixture.write_script(
            "pass.py",
            "import os, shutil\n"
            "assert 'DOTFILES_TEST_BD' not in os.environ\n"
            "assert 'guard-bin' in (shutil.which('bd') or '')\n",
        )
        self.fixture.write_registry()
        other = self.fixture.run(env=env)
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertIn("PASS alpha", other.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX executable fixture")
    def test_bd_probe_failure_skips_before_running_step(self) -> None:
        fake_bin = self.fixture.root / "fake-bin"
        fake_bin.mkdir()
        fake_bd = fake_bin / "bd"
        self.fixture.capabilities = {"bd": {"command": "bd", "probe": ["bd", "--version"]}}
        self.fixture.steps[0]["requires"] = ["bd"]
        self.fixture.write_registry()
        env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', os.defpath)}"}

        for script, reason in (
            ("#!/bin/sh\nexit 97\n", "exit 97"),
            ("#!/nonexistent-bd-test-interpreter\n", "cannot execute"),
        ):
            with self.subTest(reason=reason):
                fake_bd.write_text(script, encoding="utf-8")
                fake_bd.chmod(0o755)
                result = self.fixture.run(env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"SKIP alpha: capability probe failed for bd ({reason})", result.stdout)
                self.assertNotIn("RUN  alpha", result.stdout)

    def test_capability_uses_first_available_command_alternative(self) -> None:
        self.fixture.capabilities = {
            "python-runtime": {
                "commands": ["definitely-not-a-real-command", "{python}"],
                "probe": ["ignored", "-c", "raise SystemExit(0)"],
            }
        }
        self.fixture.steps[0]["requires"] = ["python-runtime"]
        self.fixture.write_registry()

        result = self.fixture.run("--require-capability", "python-runtime")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS alpha", result.stdout)

    def test_capability_alternatives_remain_visible_in_strict_diagnostic(self) -> None:
        self.fixture.capabilities = {
            "absent": {"commands": ["missing-first", "missing-second"]}
        }
        self.fixture.steps[0]["requires"] = ["absent"]
        self.fixture.write_registry()

        result = self.fixture.run("--require-capabilities")

        self.assertEqual(result.returncode, 1)
        self.assertIn("missing-first or missing-second", result.stdout)

    def test_capability_rejects_mixed_forms_and_unknown_placeholders(self) -> None:
        self.fixture.capabilities = {
            "mixed": {"command": "python", "commands": ["python3"]}
        }
        self.fixture.write_registry()
        mixed = self.fixture.run()
        self.assertEqual(mixed.returncode, 2)
        self.assertIn("only one of command or commands", mixed.stderr)

        self.fixture.capabilities = {
            "invalid": {"commands": ["{unsupported}"]}
        }
        self.fixture.write_registry()
        invalid = self.fixture.run()
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("unsupported placeholder", invalid.stderr)

    def test_selected_capability_requirement_preserves_other_skips(self) -> None:
        self.fixture.capabilities = {
            "absent": {"command": "definitely-not-a-real-command"},
            "optional": {"command": "another-command-that-does-not-exist"},
        }
        self.fixture.steps[0]["requires"] = ["absent"]
        self.fixture.write_script("second.py", "raise SystemExit(0)\n")
        self.fixture.steps.append(
            self.fixture.step(
                "beta", ["fast"], "second.py", requires=["optional"]
            )
        )
        self.fixture.write_registry()

        required = self.fixture.run("--require-capability", "absent")
        self.assertEqual(required.returncode, 1)
        self.assertIn("FAIL alpha: missing capability absent", required.stdout)
        self.assertIn("SKIP beta: missing capability optional", required.stdout)

        optional = self.fixture.run("--require-capability", "optional")
        self.assertEqual(optional.returncode, 1)
        self.assertIn("SKIP alpha: missing capability absent", optional.stdout)
        self.assertIn("FAIL beta: missing capability optional", optional.stdout)

    def test_unknown_selected_capability_returns_two(self) -> None:
        result = self.fixture.run("--require-capability", "unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown capability", result.stderr)

    def test_report_records_success_and_replaces_existing_file(self) -> None:
        report = self.fixture.root / "reports" / "result.json"
        report.parent.mkdir()
        report.write_text("stale\n", encoding="utf-8")

        result = self.fixture.run("--report-file", str(report))

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["selected_suites"], ["fast"])
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["totals"], {"fail": 0, "pass": 1, "skip": 0})
        self.assertEqual(
            payload["steps"],
            [
                {
                    "covers": ["tests/pass.py"],
                    "exit_code": 0,
                    "id": "alpha",
                    "reason": None,
                    "status": "pass",
                    "suites": ["fast", "integration"],
                }
            ],
        )
        self.assertTrue(report.read_text(encoding="utf-8").endswith("\n"))

    def test_report_records_capability_skip_and_child_failure(self) -> None:
        report = self.fixture.root / "report.json"
        self.fixture.capabilities = {"absent": {"command": "not-a-real-command"}}
        self.fixture.steps[0]["requires"] = ["absent"]
        self.fixture.write_registry()

        skipped = self.fixture.run("--report-file", str(report))
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        skipped_payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(skipped_payload["steps"][0]["status"], "skip")
        self.assertIsNone(skipped_payload["steps"][0]["exit_code"])

        self.fixture.capabilities = {}
        self.fixture.steps[0].pop("requires")
        self.fixture.write_script("pass.py", "raise SystemExit(23)\n")
        self.fixture.write_registry()
        failed = self.fixture.run("--report-file", str(report))
        self.assertEqual(failed.returncode, 1)
        failed_payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(failed_payload["exit_code"], 1)
        self.assertEqual(failed_payload["steps"][0]["status"], "fail")
        self.assertEqual(failed_payload["steps"][0]["exit_code"], 23)

    def test_verbose_unittest_skip_remains_a_passing_report_step(self) -> None:
        self.fixture.write_script(
            "skip.py",
            "import unittest\n"
            "class Example(unittest.TestCase):\n"
            "    @unittest.skip('fixture runtime unavailable')\n"
            "    def test_optional_runtime(self): pass\n"
            "unittest.main()\n",
        )
        self.fixture.steps[0]["argv"] = ["{python}", "{repo}/tests/skip.py", "-v"]
        self.fixture.write_registry()
        report = self.fixture.root / "report.json"
        result = self.fixture.run("--require-capabilities", "--report-file", str(report))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipped 'fixture runtime unavailable'", result.stdout + result.stderr)
        step = json.loads(report.read_text())["steps"][0]
        self.assertEqual(set(step), {"id", "suites", "covers", "status", "reason", "exit_code"})
        self.assertEqual(step["status"], "pass")
        self.assertIsNone(step["reason"])
        self.assertEqual(step["exit_code"], 0)

    def test_platform_mismatch_is_a_skip_even_when_capabilities_are_required(self) -> None:
        other = "windows" if os.name != "nt" else "linux"
        self.fixture.steps[0]["platforms"] = [other]
        self.fixture.write_registry()
        result = self.fixture.run("--require-capabilities")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SKIP alpha: requires platform", result.stdout)

    def test_child_argv_is_not_shell_interpolated(self) -> None:
        output = self.fixture.root / "argv.json"
        self.fixture.write_script(
            "pass.py",
            "import json, pathlib, sys\n"
            f"pathlib.Path({str(output)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        )
        marker = self.fixture.root / "must-not-exist"
        self.fixture.steps[0]["argv"].append(f"literal;touch {marker}")
        self.fixture.write_registry()
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), [f"literal;touch {marker}"])
        self.assertFalse(marker.exists())

    def test_repeated_argv_tokens_are_allowed(self) -> None:
        self.fixture.steps[0]["argv"].extend(["repeat", "repeat"])
        self.fixture.write_registry()
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS alpha", result.stdout)

    def test_child_environment_is_synthetic_and_blocks_beads_commands(self) -> None:
        output = self.fixture.root / "environment.json"
        self.fixture.write_script(
            "pass.py",
            "import json, os, pathlib, shutil, subprocess\n"
            "blocked = shutil.which('bd')\n"
            "if blocked is None: raise SystemExit('bd guard command was not found')\n"
            "result = subprocess.run([blocked, '--version'], check=False, text=True, capture_output=True)\n"
            f"pathlib.Path({str(output)!r}).write_text(json.dumps({{"
            "'home': os.environ['HOME'], 'userprofile': os.environ['USERPROFILE'], "
            "'tmp': os.environ['TMPDIR'], 'secret': os.environ.get('FIXTURE_SECRET_TOKEN'), "
            "'git_global': os.environ['GIT_CONFIG_GLOBAL'], 'bd_exit': result.returncode, "
            "'bd_error': result.stderr}), encoding='utf-8')\n",
        )
        env = os.environ.copy()
        env["FIXTURE_SECRET_TOKEN"] = "must-not-leak"
        result = self.fixture.run(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(output.read_text(encoding="utf-8"))
        self.assertNotEqual(actual["home"], str(Path.home()))
        self.assertEqual(actual["home"], actual["userprofile"])
        self.assertIsNone(actual["secret"])
        self.assertEqual(actual["bd_exit"], 97)
        self.assertIn("blocked real bd invocation", actual["bd_error"])
        self.assertTrue(actual["git_global"].startswith(str(Path(actual["home"]).parent)))
        self.assertTrue(actual["tmp"].startswith(str(Path(actual["home"]).parent)))

    def test_devcontainer_smoke_opt_in_and_artifact_settings_are_passed_through(self) -> None:
        output = self.fixture.root / "smoke-environment.json"
        self.fixture.write_script(
            "pass.py",
            "import json, os, pathlib\n"
            f"pathlib.Path({str(output)!r}).write_text(json.dumps({{"
            "'enabled': os.environ.get('DEVCONTAINER_SMOKE'), "
            "'artifacts': os.environ.get('DEVCONTAINER_SMOKE_ARTIFACTS'), "
            "'timeout': os.environ.get('DEVCONTAINER_SMOKE_TIMEOUT_SECONDS'), "
            "'secret': os.environ.get('DEVCONTAINER_SMOKE_SECRET')}), encoding='utf-8')\n",
        )
        env = os.environ.copy()
        env.update(
            {
                "DEVCONTAINER_SMOKE": "1",
                "DEVCONTAINER_SMOKE_ARTIFACTS": "ci-artifacts/smoke",
                "DEVCONTAINER_SMOKE_TIMEOUT_SECONDS": "240",
                "DEVCONTAINER_SMOKE_SECRET": "must-not-leak",
            }
        )

        result = self.fixture.run(env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(output.read_text(encoding="utf-8")),
            {
                "enabled": "1",
                "artifacts": "ci-artifacts/smoke",
                "timeout": "240",
                "secret": None,
            },
        )

    def test_current_registry_covers_every_top_level_test(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        covered = {path for step in payload["steps"] for path in step["covers"]}
        tracked = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                ":(glob)tests/test_*.py",
            ],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines()
        self.assertEqual(covered, set(tracked))
        agent = next(step for step in payload["steps"] if step["id"] == "agent-worktree-merge")
        self.assertEqual(agent["argv"][-1], "tests.test_agent_wt_merge")
        self.assertEqual(agent["covers"], ["tests/test_agent_wt_merge.py"])

    def test_fast_node_steps_and_shell_resolver_declare_capabilities(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        fast = {step["id"]: step for step in payload["steps"] if "fast" in step["suites"]}
        for step_id in ("feature-ci-reminders", "managed-ai-adapters", "repo-opencode-plugins"):
            with self.subTest(step=step_id):
                self.assertIn("node", fast[step_id]["requires"])
        self.assertIn("sh", fast["resolve-python3"]["requires"])
        self.assertEqual(payload["capabilities"]["node"], {"command": "node"})

    def test_attestation_dependencies_follow_the_native_wrapper_platform(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        steps = {step["id"]: step for step in payload["steps"]}
        posix = steps["agent-attestation"]
        windows = steps["agent-attestation-windows"]
        self.assertEqual(set(posix["platforms"]), {"linux", "macos", "wsl2"})
        self.assertEqual(windows["platforms"], ["windows"])
        self.assertEqual(set(posix["requires"]), {"node", "git", "bash", "jq"})
        self.assertEqual(set(windows["requires"]), {"node", "git", "pwsh"})
        self.assertEqual(posix["argv"], windows["argv"])
        self.assertEqual(posix["covers"], windows["covers"])
        self.assertEqual(posix["suites"], windows["suites"])
        self.assertEqual(payload["capabilities"]["jq"], {"command": "jq"})

    def test_attestation_fails_in_strict_mode_when_jq_is_missing(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        step = next(step for step in payload["steps"] if step["id"] == "agent-attestation")
        self.fixture.capabilities = {
            name: {"command": "definitely-not-a-real-jq" if name == "jq" else sys.executable}
            for name in step["requires"]
        }
        self.fixture.steps = [
            self.fixture.step(step["id"], ["fast"], "pass.py", requires=step["requires"])
        ]
        self.fixture.write_registry()
        result = self.fixture.run("fast", "--require-capabilities")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("FAIL agent-attestation: missing capability jq", result.stdout)
        self.assertNotIn("RUN  agent-attestation", result.stdout)

    def test_fast_node_steps_fail_in_strict_mode_when_node_is_missing(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        node_steps = [
            step for step in payload["steps"]
            if "fast" in step["suites"] and "node" in step.get("requires", [])
        ]
        self.assertTrue(node_steps)
        self.fixture.capabilities = {"node": {"command": "definitely-not-a-real-node"}}
        self.fixture.steps = [
            self.fixture.step(step["id"], ["fast"], "pass.py", requires=["node"])
            for step in node_steps
        ]
        self.fixture.write_registry()
        result = self.fixture.run("fast", "--require-capabilities")
        self.assertEqual(result.returncode, 1, result.stderr)
        for step in node_steps:
            self.assertIn(f"FAIL {step['id']}: missing capability node", result.stdout)
        self.assertNotIn("SKIP ", result.stdout)
        self.assertNotIn("RUN  ", result.stdout)

    def test_posix_wrapper_delegates_to_runner(self) -> None:
        if BASH is None:
            self.skipTest("no bash that can reach this checkout")
        result = subprocess.run(
            [BASH, str(POSIX_WRAPPER), "--list", "fast"],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Selected steps:", result.stdout)

    def test_powershell_wrapper_delegates_to_runner(self) -> None:
        diagnostics: list[str] = []
        executable = powershell.resolve_powershell_runtime(diagnostics=diagnostics)
        if executable is None:
            self.skipTest("PowerShell runtime unavailable: " + "; ".join(diagnostics))
        wrapper = powershell.powershell_path(POWERSHELL_WRAPPER, executable)
        result = subprocess.run(
            [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", wrapper, "--list", "fast"],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Selected steps:", result.stdout)

        invalid = subprocess.run(
            [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", wrapper, "not-a-suite"],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(invalid.returncode, 2, invalid.stderr)
        self.assertIn("invalid choice", invalid.stderr)

    def test_powershell_wrapper_skip_reports_probe_diagnostics(self) -> None:
        reason = "Windows PowerShell/WSL interop candidate '/mnt/c/pwsh.exe': probe exited 1"

        def unavailable(*, diagnostics):
            diagnostics.append(reason)
            return None

        with mock.patch.object(powershell, "resolve_powershell_runtime", side_effect=unavailable):
            with self.assertRaisesRegex(unittest.SkipTest, "WSL interop candidate.*probe exited 1"):
                self.test_powershell_wrapper_delegates_to_runner()

    def test_powershell_wrapper_failure_after_probe_is_not_skipped(self) -> None:
        with (
            mock.patch.object(powershell, "resolve_powershell_runtime", return_value="/fixture/pwsh"),
            mock.patch.object(powershell, "powershell_path", return_value="wrapper.ps1"),
            mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "wrapper failed")),
        ):
            with self.assertRaisesRegex(AssertionError, "wrapper failed"):
                self.test_powershell_wrapper_delegates_to_runner()

    def test_peer_sensitive_steps_are_verbose_without_broad_runtime_gates(self) -> None:
        steps = {step["id"]: step for step in json.loads(REGISTRY.read_text())["steps"]}
        for step_id, required in (("repo-ops", "bash"), ("test-runner", "git")):
            with self.subTest(step=step_id):
                self.assertEqual(steps[step_id]["argv"],
                                 ["{python}", "-m", "unittest", "-v", f"tests.test_{step_id.replace('-', '_')}"])
                self.assertEqual(steps[step_id]["requires"], [required])


class PowerShellSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        powershell._PROBED_RUNTIMES.clear()
        self.addCleanup(powershell._PROBED_RUNTIMES.clear)

    def test_runtime_prefers_local_pwsh_and_probes_supplied_environment(self) -> None:
        env = {"PATH": "/fixture", "WSL_DISTRO_NAME": "Fixture"}
        with (
            mock.patch.object(powershell.shutil, "which", return_value="/fixture/pwsh") as which,
            mock.patch.object(Path, "is_file") as is_file,
            mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run,
        ):
            actual = powershell.resolve_powershell_runtime(environ=env)
        self.assertEqual(actual, "/fixture/pwsh")
        which.assert_called_once_with("pwsh", path="/fixture")
        is_file.assert_not_called()
        run.assert_called_once_with(
            ["/fixture/pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "exit 0"],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=10,
        )

    def test_runtime_windows_fallbacks(self) -> None:
        standard = str(Path("/mnt/c/Program Files/PowerShell/7/pwsh.exe"))
        for native, expected in (("/mnt/c/pwsh.exe", "/mnt/c/pwsh.exe"), (None, standard)):
            with (
                self.subTest(native=native),
                mock.patch.object(powershell.shutil, "which", side_effect=[None, native]),
                mock.patch.object(Path, "is_file", return_value=True),
                mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run,
            ):
                self.assertEqual(powershell.resolve_powershell_runtime(
                    environ={"PATH": "/fixture", "WSL_DISTRO_NAME": "Fixture"}), expected)
                self.assertEqual(run.call_args.args[0][0], expected)

    def test_runtime_not_found_does_not_probe(self) -> None:
        with (
            mock.patch.object(powershell.shutil, "which", return_value=None),
            mock.patch.object(Path, "is_file", return_value=False),
            mock.patch.object(subprocess, "run") as run,
        ):
            diagnostics: list[str] = []
            self.assertIsNone(powershell.resolve_powershell_runtime(
                environ={"PATH": "/fixture", "WSL_DISTRO_NAME": "Fixture"}, diagnostics=diagnostics))
            self.assertIn("not found", diagnostics[0])
            run.assert_not_called()

    def test_runtime_rejects_execution_failure_exit_and_timeout(self) -> None:
        failures = (
            (OSError(errno.EINVAL, "Invalid argument"), "cannot execute (errno 22"),
            (subprocess.CompletedProcess([], 7, b"private output", b"private error"), "probe exited 7"),
            (subprocess.TimeoutExpired("pwsh.exe", 10), "timed out after 10 seconds"),
        )
        for failure, reason in failures:
            with (
                self.subTest(reason=reason),
                mock.patch.object(powershell.os, "name", "posix"),
                mock.patch.object(powershell.shutil, "which", side_effect=[None, "/mnt/c/pwsh.exe"]),
                mock.patch.object(Path, "is_file", return_value=False),
                mock.patch.object(subprocess, "run", side_effect=[failure]) as run,
            ):
                diagnostics: list[str] = []
                self.assertIsNone(powershell.resolve_powershell_runtime(
                    environ={"PATH": "/fixture", "WSL_INTEROP": "/fixture/socket"}, diagnostics=diagnostics))
                self.assertEqual(run.call_count, 1)
                self.assertIn("Windows PowerShell/WSL interop candidate", diagnostics[0])
                self.assertIn(reason, diagnostics[0])
                self.assertNotIn("private", diagnostics[0])

    def test_runtime_tries_later_candidate_after_failure(self) -> None:
        with (
            mock.patch.object(powershell.shutil, "which", side_effect=["/fixture/pwsh", "/mnt/c/pwsh.exe"]),
            mock.patch.object(Path, "is_file", return_value=False),
            mock.patch.object(subprocess, "run", side_effect=[OSError(errno.EINVAL, "Invalid argument"),
                                                            subprocess.CompletedProcess([], 0)]) as run,
        ):
            diagnostics: list[str] = []
            self.assertEqual(powershell.resolve_powershell_runtime(
                environ={"PATH": "/fixture", "WSL_DISTRO_NAME": "Fixture"}, diagnostics=diagnostics),
                "/mnt/c/pwsh.exe")
            self.assertEqual(run.call_count, 2)
            self.assertIn("cannot execute", diagnostics[0])

    def test_runtime_tries_standard_path_after_path_candidate_failure(self) -> None:
        standard = str(Path("/mnt/c/Program Files/PowerShell/7/pwsh.exe"))
        with (
            mock.patch.object(powershell.shutil, "which", side_effect=[None, "/mnt/c/pwsh.exe"]),
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(subprocess, "run", side_effect=[subprocess.CompletedProcess([], 1),
                                                            subprocess.CompletedProcess([], 0)]) as run,
        ):
            self.assertEqual(powershell.resolve_powershell_runtime(
                environ={"PATH": "/fixture", "WSL_DISTRO_NAME": "Fixture"}), standard)
            self.assertEqual([call.args[0][0] for call in run.call_args_list],
                             ["/mnt/c/pwsh.exe", standard])

    def test_runtime_non_wsl_posix_does_not_consider_windows_executable(self) -> None:
        with (
            mock.patch.object(powershell.os, "name", "posix"),
            mock.patch.object(powershell.shutil, "which", return_value=None) as which,
            mock.patch.object(Path, "is_file") as is_file,
            mock.patch.object(subprocess, "run") as run,
        ):
            self.assertIsNone(powershell.resolve_powershell_runtime(environ={"PATH": "/fixture"}))
            which.assert_called_once_with("pwsh", path="/fixture")
            is_file.assert_not_called()
            run.assert_not_called()

    def test_runtime_deduplicates_candidates(self) -> None:
        standard = str(Path("/mnt/c/Program Files/PowerShell/7/pwsh.exe"))
        with (
            mock.patch.object(powershell.shutil, "which", return_value=standard),
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run,
        ):
            self.assertIsNone(powershell.resolve_powershell_runtime(
                environ={"PATH": "/fixture", "WSL_DISTRO_NAME": "Fixture"}))
            self.assertEqual(run.call_count, 1)

    def test_runtime_forbid_skips_interop_candidates_without_probing(self) -> None:
        with (
            mock.patch.object(powershell.os, "name", "posix"),
            mock.patch.object(powershell.shutil, "which", side_effect=[None, "/mnt/c/pwsh.exe"]),
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(subprocess, "run") as run,
        ):
            standard = str(Path("/mnt/c/Program Files/PowerShell/7/pwsh.exe"))
            diagnostics: list[str] = []
            self.assertIsNone(powershell.resolve_powershell_runtime(
                environ={"PATH": "/fixture", "WSL_DISTRO_NAME": "Fixture"},
                diagnostics=diagnostics, interop="forbid"))
            run.assert_not_called()
        self.assertEqual(len(diagnostics), 2)
        for candidate, message in zip(("/mnt/c/pwsh.exe", standard), diagnostics):
            self.assertIn(repr(candidate), message)
            self.assertIn("skipped, caller passes POSIX paths", message)

    def test_runtime_forbid_keeps_local_pwsh_on_wsl(self) -> None:
        with (
            mock.patch.object(powershell.os, "name", "posix"),
            mock.patch.object(powershell.shutil, "which", side_effect=["/usr/bin/pwsh", "/mnt/c/pwsh.exe"]),
            mock.patch.object(Path, "is_file", return_value=False),
            mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run,
        ):
            self.assertEqual(powershell.resolve_powershell_runtime(
                environ={"PATH": "/fixture", "WSL_DISTRO_NAME": "Fixture"}, interop="forbid"),
                "/usr/bin/pwsh")
            self.assertEqual(run.call_args.args[0][0], "/usr/bin/pwsh")

    def test_runtime_forbid_accepts_native_windows_pwsh_exe(self) -> None:
        native = "C:/Program Files/PowerShell/7/pwsh.exe"
        with (
            mock.patch.object(powershell.os, "name", "nt"),
            mock.patch.object(powershell.shutil, "which", side_effect=[native, native]),
            mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run,
        ):
            self.assertEqual(powershell.resolve_powershell_runtime(
                environ={"PATH": "C:/fixture"}, interop="forbid"), native)
            self.assertEqual(run.call_count, 1)

    def test_runtime_require_selects_interop_after_local_pwsh(self) -> None:
        with (
            mock.patch.object(powershell.os, "name", "posix"),
            mock.patch.object(powershell.shutil, "which", side_effect=["/usr/bin/pwsh", "/mnt/c/pwsh.exe"]),
            mock.patch.object(Path, "is_file", return_value=False),
            mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run,
        ):
            diagnostics: list[str] = []
            self.assertEqual(powershell.resolve_powershell_runtime(
                environ={"PATH": "/fixture", "WSL_INTEROP": "/fixture/socket"},
                diagnostics=diagnostics, interop="require"), "/mnt/c/pwsh.exe")
            self.assertEqual([call.args[0][0] for call in run.call_args_list], ["/mnt/c/pwsh.exe"])
        self.assertIn("'/usr/bin/pwsh': skipped, caller needs WSL interop", diagnostics[0])

    def test_runtime_require_fails_off_wsl(self) -> None:
        with (
            mock.patch.object(powershell.os, "name", "posix"),
            mock.patch.object(powershell.shutil, "which", return_value="/usr/bin/pwsh"),
            mock.patch.object(subprocess, "run") as run,
        ):
            diagnostics: list[str] = []
            self.assertIsNone(powershell.resolve_powershell_runtime(
                environ={"PATH": "/fixture"}, diagnostics=diagnostics, interop="require"))
            run.assert_not_called()
        self.assertEqual(diagnostics, ["PowerShell candidate '/usr/bin/pwsh': skipped, caller needs WSL interop"])

    def test_runtime_rejects_unknown_interop_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "allow, forbid, require"):
            powershell.resolve_powershell_runtime(environ={"PATH": "/fixture"}, interop="native")

    def test_runtime_reuses_successful_probe_but_reprobes_failures(self) -> None:
        env = {"PATH": "/fixture"}
        with (
            mock.patch.object(powershell.os, "name", "posix"),
            mock.patch.object(powershell.shutil, "which", return_value="/fixture/pwsh"),
            mock.patch.object(subprocess, "run", side_effect=[subprocess.CompletedProcess([], 1),
                                                            subprocess.CompletedProcess([], 0)]) as run,
        ):
            self.assertIsNone(powershell.resolve_powershell_runtime(environ=env))
            self.assertEqual(powershell.resolve_powershell_runtime(environ=env), "/fixture/pwsh")
            self.assertEqual(powershell.resolve_powershell_runtime(environ=env), "/fixture/pwsh")
            self.assertEqual(run.call_count, 2)

    def test_interop_path_translation_failure_is_an_error(self) -> None:
        failures = (
            OSError(errno.ENOENT, "No such file or directory"),
            subprocess.TimeoutExpired("wslpath", 10),
            subprocess.CompletedProcess([], 1, "", "bad path"),
            subprocess.CompletedProcess([], 0, "\n", ""),
        )
        for failure in failures:
            with (
                self.subTest(failure=type(failure).__name__),
                mock.patch.object(powershell.os, "name", "posix"),
                mock.patch.object(subprocess, "run", side_effect=[failure]),
            ):
                with self.assertRaisesRegex(RuntimeError, "wslpath cannot translate '/fixture/a.ps1'"):
                    powershell.powershell_path("/fixture/a.ps1", "/mnt/c/pwsh.exe")

    def test_interop_path_translation_only_applies_to_interop_runtime(self) -> None:
        with (
            mock.patch.object(powershell.os, "name", "posix"),
            mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "\\\\wsl.localhost\\Fixture\\a.ps1\n", "")) as run,
        ):
            self.assertEqual(powershell.powershell_path("/fixture/a.ps1", "/usr/bin/pwsh"), "/fixture/a.ps1")
            self.assertEqual(powershell.powershell_path("/fixture/a.ps1", None), "/fixture/a.ps1")
            run.assert_not_called()
            self.assertEqual(powershell.powershell_path("/fixture/a.ps1", "/mnt/c/pwsh.exe"),
                             "\\\\wsl.localhost\\Fixture\\a.ps1")
            self.assertEqual(run.call_args.args[0], ["wslpath", "-w", "-a", "/fixture/a.ps1"])

    def test_probe_runtime_cli_maps_modes_and_reports_diagnostics(self) -> None:
        def unavailable(*, diagnostics, interop):
            diagnostics.append(f"fixture diagnostic for {interop}")
            return None

        for flag, mode in (([], "allow"), (["--native"], "forbid"), (["--interop"], "require")):
            with self.subTest(mode=mode):
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    mock.patch.object(powershell, "resolve_powershell_runtime",
                                      return_value="/fixture/pwsh") as resolve,
                    contextlib.redirect_stdout(stdout),
                ):
                    self.assertEqual(powershell.main(["probe-runtime", *flag]), 0)
                self.assertEqual(resolve.call_args.kwargs["interop"], mode)
                self.assertEqual(stdout.getvalue(), "/fixture/pwsh\n")
                with (
                    mock.patch.object(powershell, "resolve_powershell_runtime", side_effect=unavailable),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(powershell.main(["probe-runtime", *flag]), 1)
                self.assertEqual(stderr.getvalue(), f"fixture diagnostic for {mode}\n")

    def test_runtime_mode_flags_are_rejected_for_other_actions(self) -> None:
        for action in ("probe-parser", "probe-pester", "run-pester"):
            with self.subTest(action=action), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    powershell.main([action, "--native"])
                self.assertEqual(raised.exception.code, 2)

    def test_pwsh_capability_probes_the_native_runtime(self) -> None:
        payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(payload["capabilities"]["pwsh"]["probe"], [
            "{python}", "{repo}/tests/support/powershell.py", "probe-runtime", "--native",
            "--repo-root", "{repo}",
        ])

    def test_bypassed_pwsh_lookup_scanner_flags_direct_lookups(self) -> None:
        source = (
            "import shutil\n"
            "a = shutil.which('pwsh')\n"
            "b = which('pwsh.exe')\n"
            "c = ['pwsh', '-NoProfile']\n"
            "d = ('pwsh.exe', '-File', 'x')\n"
            "e = ['docker', '--entrypoint', 'pwsh']\n"
            "f = shutil.which('git')\n"
        )
        self.assertEqual(bypassed_pwsh_lookups(source), [2, 3, 4, 5])

    def test_tests_resolve_pwsh_through_the_support_module(self) -> None:
        resolver = REPO_ROOT / "tests" / "support" / "powershell.py"
        offenders = []
        for source in sorted((REPO_ROOT / "tests").rglob("*.py")):
            if source == resolver:
                continue
            for line in bypassed_pwsh_lookups(source.read_text(encoding="utf-8")):
                offenders.append(f"{source.relative_to(REPO_ROOT).as_posix()}:{line}")
        self.assertEqual(offenders, [], "use tests.support.powershell.resolve_powershell_runtime")

    def test_unscoped_mock_assertion_scanner(self) -> None:
        source = (
            "Assert-MockCalled Foo -Times 1 -Exactly\n"
            "Assert-MockCalled Foo -Scope It -Times 1\n"
            "assert-mockcalled Foo -scope Describe\n"
            "Assert-MockCalled Foo `\n"
            "    -Scope It\n"
            "Assert-MockCalled Foo `\n"
            "    -Times 0\n"
            "# Assert-MockCalled in a comment\n"
            "Assert-MockCalled Foo -ParameterFilter { $Scope -eq 'User' }\n"
        )
        self.assertEqual(unscoped_mock_assertions(source), [1, 6, 9])

    def test_pester_mock_assertions_are_scoped(self) -> None:
        offenders = []
        for source in sorted((REPO_ROOT / "tests" / "powershell").rglob("*.ps1")):
            for line in unscoped_mock_assertions(source.read_text(encoding="utf-8")):
                offenders.append(f"{source.relative_to(REPO_ROOT).as_posix()}:{line}")
        self.assertEqual(offenders, [], "Pester 3 counts unscoped mock calls across the whole Describe")

    def test_parser_prefers_runtime_without_container(self) -> None:
        with (
            mock.patch.object(
                powershell, "resolve_powershell_runtime", return_value="/usr/bin/pwsh"
            ),
            mock.patch.object(powershell, "_container_runtime") as container_runtime,
        ):
            resolved = powershell.powershell_parser_command(
                REPO_ROOT,
                "fixture.ps1",
                environ={"PATH": "/fixture"},
            )

        self.assertIsNotNone(resolved)
        command, _ = resolved or ([], {})
        self.assertEqual(command[0], "/usr/bin/pwsh")
        container_runtime.assert_not_called()

    def test_parser_container_is_static_evidence_only(self) -> None:
        with (
            mock.patch.object(powershell, "resolve_powershell_runtime", return_value=None),
            mock.patch.object(powershell, "_container_runtime", return_value="/usr/bin/docker"),
        ):
            resolved = powershell.powershell_parser_command(
                REPO_ROOT,
                "fixture.ps1",
                environ={"PATH": "/fixture"},
                provision_container=False,
            )

        self.assertIsNotNone(resolved)
        command, _ = resolved or ([], {})
        self.assertIn("--network=none", command)
        self.assertIn(powershell.POWERSHELL_AUDIT_IMAGE, command)
        self.assertNotIn("Invoke-Pester", " ".join(command))

    def test_parser_probe_does_not_provision_container(self) -> None:
        with (
            mock.patch.object(powershell, "resolve_powershell_runtime", return_value=None),
            mock.patch.object(
                powershell,
                "_container_runtime",
                return_value="/usr/bin/docker",
            ),
            mock.patch.object(powershell, "_ensure_audit_image") as ensure_image,
        ):
            result = powershell.main(
                ["probe-parser", "--repo-root", str(REPO_ROOT)]
            )

        self.assertEqual(result, 0)
        ensure_image.assert_not_called()

    def test_container_parser_does_not_make_pester_available(self) -> None:
        with (
            mock.patch.object(powershell, "resolve_powershell_runtime", return_value=None),
            mock.patch.object(powershell, "_container_runtime", return_value="/usr/bin/docker"),
            mock.patch.object(powershell.subprocess, "run") as run,
        ):
            version = powershell.pester_version(environ={"PATH": "/fixture"})

        self.assertIsNone(version)
        run.assert_not_called()

    def test_pester_launcher_adapts_versions_without_installing(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "ok", "")
        for version, parameter in (((3, 4, 0), "-Script"), ((5, 7, 1), "-Path")):
            with (
                self.subTest(version=version),
                mock.patch.object(
                    powershell,
                    "resolve_powershell_runtime",
                    return_value="/usr/bin/pwsh",
                ),
                mock.patch.object(powershell, "pester_version", return_value=version),
                mock.patch.object(
                    powershell.subprocess, "run", return_value=completed
                ) as run,
            ):
                result = powershell.run_pester(
                    [Path("/fixture/Example.Tests.ps1")],
                    repo_root=REPO_ROOT,
                    environ={"PATH": "/fixture"},
                )

            self.assertEqual(result.returncode, 0)
            command = run.call_args.args[0]
            self.assertIn(parameter, command[-1])
            self.assertNotIn("Install-Module", command[-1])

    def test_pester_launcher_rejects_versions_before_3_4(self) -> None:
        with (
            mock.patch.object(
                powershell,
                "resolve_powershell_runtime",
                return_value="/usr/bin/pwsh",
            ),
            mock.patch.object(powershell, "pester_version", return_value=(3, 3, 0)),
        ):
            with self.assertRaisesRegex(RuntimeError, "Pester 3.4 or newer"):
                powershell.run_pester(
                    [Path("tests/powershell/Example.Tests.ps1")],
                    repo_root=REPO_ROOT,
                    environ={"PATH": "/fixture"},
                )


if __name__ == "__main__":
    unittest.main()
