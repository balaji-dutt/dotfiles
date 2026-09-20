from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support import powershell


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "assets" / "run-tests.py"
POSIX_WRAPPER = REPO_ROOT / "assets" / "run-tests.sh"
POWERSHELL_WRAPPER = REPO_ROOT / "assets" / "run-tests.ps1"
REGISTRY = REPO_ROOT / "configs" / "test-suites.json"


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
            [
                sys.executable,
                str(RUNNER),
                "--repo-root",
                str(self.root),
                "--registry",
                str(self.registry),
                *args,
            ],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


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

    def test_posix_wrapper_delegates_to_runner(self) -> None:
        result = subprocess.run(
            ["bash", str(POSIX_WRAPPER), "--list", "fast"],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Selected steps:", result.stdout)

    def test_powershell_wrapper_delegates_to_runner(self) -> None:
        executable = powershell.resolve_powershell_runtime()
        if executable is None:
            self.skipTest("PowerShell is not installed")
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


class PowerShellSupportTests(unittest.TestCase):
    def test_runtime_prefers_local_pwsh_then_wsl_executable(self) -> None:
        def choose(command: str, *, path: str | None = None) -> str | None:
            del path
            return {"pwsh": None, "pwsh.exe": "/mnt/c/pwsh.exe"}.get(command)

        with mock.patch.object(powershell.shutil, "which", side_effect=choose):
            actual = powershell.resolve_powershell_runtime(
                environ={"PATH": "/fixture", "WSL_DISTRO_NAME": "Fixture"}
            )

        self.assertEqual(actual, "/mnt/c/pwsh.exe")

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
