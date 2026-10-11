from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.support.fixtures import load_script_module


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "assets" / "unittest-case-report.py"
RUNNER = REPO_ROOT / "assets" / "run-tests.py"
runner = load_script_module(RUNNER, "run_tests_case_report")

MODULES = {
    "test_pass": """
        import unittest

        class PassTests(unittest.TestCase):
            def test_ok(self):
                pass
    """,
    "test_fail": """
        import unittest

        class FailTests(unittest.TestCase):
            def test_broken(self):
                self.fail("broken")
    """,
    "test_skipped": """
        import unittest

        @unittest.skipUnless(False, "fixture platform absent")
        class SkippedTests(unittest.TestCase):
            def test_one(self):
                pass

            def test_two(self):
                pass
    """,
    "test_empty": """
        VALUE = 1
    """,
    "test_mixed": """
        import unittest

        class MixedTests(unittest.TestCase):
            def test_subtests(self):
                for value in (1, 2, 3):
                    with self.subTest(value=value):
                        if value == 2:
                            self.fail("two")
                        if value == 3:
                            self.skipTest("three skipped")

            def test_subtests_error(self):
                with self.subTest(step="boom"):
                    raise RuntimeError("boom")

            def test_subtests_pass_and_skip(self):
                with self.subTest(value=1):
                    pass
                with self.subTest(value=2):
                    self.skipTest("later")

            def test_subtests_all_skipped(self):
                with self.subTest(value=1):
                    self.skipTest("absent")

            @unittest.expectedFailure
            def test_expected(self):
                self.fail("known")

            @unittest.expectedFailure
            def test_unexpected(self):
                pass

            def test_skip_reason(self):
                self.skipTest("no fixture tool")

            def test_error(self):
                raise RuntimeError("bad")
    """,
    "test_fixtures": """
        import unittest

        class BrokenSetupTests(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("setup failed")

            def test_never(self):
                pass

        class SkippedSetupTests(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest("class prerequisite absent")

            def test_never(self):
                pass
    """,
}


class CaseReportFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        package = root / "casepkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        for name, source in MODULES.items():
            (package / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")

    def adapter(self, *selectors: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        sidecar = self.root / "out" / "cases.json"
        sidecar.unlink(missing_ok=True)
        completed = subprocess.run(
            [sys.executable, str(ADAPTER), "--case-report", str(sidecar), *selectors],
            cwd=self.root, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return completed, sidecar

    def unittest(self, *selectors: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "unittest", *selectors],
            cwd=self.root, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )


class CaseReportAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="case-report-")
        self.addCleanup(temporary.cleanup)
        self.fixture = CaseReportFixture(Path(temporary.name))

    def cases(self, *selectors: str) -> dict[str, dict]:
        completed, sidecar = self.fixture.adapter(*selectors)
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertTrue(payload["complete"], completed.stderr)
        return {case["id"]: case for case in payload["cases"]}

    def test_exit_codes_match_python_m_unittest(self) -> None:
        for selectors in (
            ("casepkg.test_pass",),
            ("-v", "casepkg.test_fail"),
            ("casepkg.test_skipped",),
            ("casepkg.test_empty",),
            ("casepkg.test_missing",),
            ("casepkg.test_mixed",),
            ("casepkg.test_fixtures",),
        ):
            with self.subTest(selectors=selectors):
                expected = self.fixture.unittest(*selectors)
                actual, sidecar = self.fixture.adapter(*selectors)
                self.assertEqual(actual.returncode, expected.returncode, actual.stderr)
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
                self.assertEqual(payload["exit_code"], expected.returncode)
                self.assertTrue(payload["complete"])
        self.assertIn("test_ok (casepkg.test_pass.PassTests", self.fixture.adapter("-v", "casepkg.test_pass")[0].stderr)

    def test_case_ids_outcomes_and_durations(self) -> None:
        cases = self.cases("casepkg.test_pass", "casepkg.test_fail")
        self.assertEqual(set(cases), {"casepkg.test_pass.PassTests.test_ok",
                                      "casepkg.test_fail.FailTests.test_broken"})
        self.assertEqual(cases["casepkg.test_pass.PassTests.test_ok"]["outcome"], "passed")
        self.assertEqual(cases["casepkg.test_fail.FailTests.test_broken"]["outcome"], "failed")
        for case in cases.values():
            self.assertGreaterEqual(case["duration_seconds"], 0)
            self.assertEqual(case["subtests"], [])

    def test_subtests_nest_under_their_parent(self) -> None:
        cases = self.cases("casepkg.test_mixed")
        parent = cases["casepkg.test_mixed.MixedTests.test_subtests"]
        self.assertEqual(parent["outcome"], "failed")
        self.assertEqual(parent["subtests"], [
            {"description": "(value=1)", "outcome": "passed", "reason": None},
            {"description": "(value=2)", "outcome": "failed", "reason": None},
            {"description": "(value=3)", "outcome": "skipped", "reason": "three skipped"},
        ])
        errored = cases["casepkg.test_mixed.MixedTests.test_subtests_error"]
        self.assertEqual(errored["outcome"], "error")
        self.assertEqual([child["outcome"] for child in errored["subtests"]], ["error"])
        self.assertFalse(any(" (" in case_id for case_id in cases))
        mixed = cases["casepkg.test_mixed.MixedTests.test_subtests_pass_and_skip"]
        self.assertEqual((mixed["outcome"], [child["outcome"] for child in mixed["subtests"]]),
                         ("passed", ["passed", "skipped"]))
        skipped = cases["casepkg.test_mixed.MixedTests.test_subtests_all_skipped"]
        self.assertEqual((skipped["outcome"], skipped["subtests"][0]["reason"]), ("skipped", "absent"))

    def test_expected_failures_skips_and_errors(self) -> None:
        cases = self.cases("casepkg.test_mixed")
        prefix = "casepkg.test_mixed.MixedTests."
        self.assertEqual(cases[prefix + "test_expected"]["outcome"], "expected_failure")
        self.assertEqual(cases[prefix + "test_unexpected"]["outcome"], "unexpected_success")
        self.assertEqual(cases[prefix + "test_error"]["outcome"], "error")
        self.assertEqual(cases[prefix + "test_skip_reason"]["outcome"], "skipped")
        self.assertEqual(cases[prefix + "test_skip_reason"]["reason"], "no fixture tool")

    def test_class_fixture_errors_and_class_skips(self) -> None:
        cases = self.cases("casepkg.test_fixtures")
        self.assertEqual(cases["setUpClass (casepkg.test_fixtures.BrokenSetupTests)"]["outcome"], "fixture_error")
        skipped = cases["setUpClass (casepkg.test_fixtures.SkippedSetupTests)"]
        self.assertEqual((skipped["outcome"], skipped["reason"]), ("skipped", "class prerequisite absent"))
        self.assertNotIn("casepkg.test_fixtures.BrokenSetupTests.test_never", cases)

    def test_class_level_skip_marks_each_case(self) -> None:
        cases = self.cases("casepkg.test_skipped")
        self.assertEqual({case["outcome"] for case in cases.values()}, {"skipped"})
        self.assertEqual({case["reason"] for case in cases.values()}, {"fixture platform absent"})

    def test_import_failure_is_an_error_case(self) -> None:
        cases = self.cases("casepkg.test_missing")
        self.assertEqual([case["outcome"] for case in cases.values()], ["error"])
        self.assertEqual(list(cases), ["unittest.loader._FailedTest.test_missing"])

    def test_sidecar_write_failure_keeps_unittest_exit_code(self) -> None:
        blocker = self.fixture.root / "blocker"
        blocker.write_text("", encoding="utf-8")
        for module, expected in (("casepkg.test_pass", 0), ("casepkg.test_fail", 1)):
            with self.subTest(module=module):
                completed = subprocess.run(
                    [sys.executable, str(ADAPTER), "--case-report", str(blocker / "cases.json"), module],
                    cwd=self.fixture.root, check=False, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                self.assertEqual(completed.returncode, expected, completed.stderr)
                self.assertIn("WARNING: cannot write case report", completed.stderr)

    @unittest.skipIf(sys.version_info < (3, 11), "-P needs Python 3.11")
    def test_safe_path_mode_matches_python_m_unittest(self) -> None:
        sidecar = self.fixture.root / "out" / "safe.json"
        commands = (
            [sys.executable, "-P", "-m", "unittest", "casepkg.test_pass"],
            [sys.executable, "-P", str(ADAPTER), "--case-report", str(sidecar), "casepkg.test_pass"],
        )
        expected, actual = (
            subprocess.run(command, cwd=self.fixture.root, check=False, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, "PYTHONPATH": ""})
            for command in commands
        )
        self.assertNotEqual(expected.returncode, 0, expected.stderr)
        self.assertEqual(actual.returncode, expected.returncode, actual.stderr)

    def test_usage_error_without_sidecar_path(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ADAPTER), "casepkg.test_pass"],
            cwd=self.fixture.root, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("usage:", completed.stderr)


class CaseReportSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="case-summary-")
        self.addCleanup(temporary.cleanup)
        self.sidecar = Path(temporary.name) / "cases.json"

    def summarize(self, payload: object) -> dict:
        self.sidecar.write_text(json.dumps(payload), encoding="utf-8")
        return runner.read_case_report(self.sidecar)

    def test_zero_tests_and_all_skipped_flags(self) -> None:
        empty = self.summarize({"complete": True, "cases": []})
        self.assertEqual((empty["status"], empty["zero_tests"], empty["all_skipped"]), ("complete", True, False))
        fixture_only = self.summarize({"complete": True, "cases": [{"outcome": "fixture_error"}]})
        self.assertTrue(fixture_only["zero_tests"])
        skipped = self.summarize({"complete": True, "cases": [{"outcome": "skipped"}, {"outcome": "skipped"}]})
        self.assertEqual((skipped["zero_tests"], skipped["all_skipped"]), (False, True))
        self.assertEqual(skipped["totals"], {"skipped": 2})
        mixed = self.summarize({"complete": True, "cases": [{"outcome": "skipped"}, {"outcome": "passed"}]})
        self.assertFalse(mixed["all_skipped"])

    def test_incomplete_missing_and_invalid_sidecars(self) -> None:
        self.assertEqual(self.summarize({"complete": False, "cases": []})["status"], "incomplete")
        self.assertEqual(runner.read_case_report(self.sidecar.with_name("absent.json")), {"status": "missing"})
        for payload in ("{", {"cases": []}, {"complete": "yes", "cases": []}, {"complete": True, "cases": [{}]}):
            with self.subTest(payload=payload):
                if isinstance(payload, str):
                    self.sidecar.write_text(payload, encoding="utf-8")
                    summary = runner.read_case_report(self.sidecar)
                else:
                    summary = self.summarize(payload)
                self.assertEqual(summary["status"], "invalid")
                self.assertIn("error", summary)

    def test_adapter_command_only_rewrites_plain_unittest_selectors(self) -> None:
        sidecar = Path("cases.json")
        self.assertEqual(
            runner.case_report_command(["py", "-m", "unittest", "-v", "tests.test_x"], sidecar),
            ["py", str(runner.CASE_REPORT_ADAPTER), "--case-report", "cases.json", "-v", "tests.test_x"],
        )
        for command in (["py", "tests/x.py"], ["py", "-m", "unittest"], ["py", "-m", "unittest", "-k", "x"]):
            with self.subTest(command=command):
                self.assertIsNone(runner.case_report_command(command, sidecar))


class RunnerCaseReportTests(unittest.TestCase):
    V1_ROOT_KEYS = {"exit_code", "platform", "schema_version", "selected_suites", "steps", "totals"}
    V1_STEP_KEYS = {"covers", "exit_code", "id", "reason", "status", "suites"}

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="case-runner-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "configs").mkdir()
        self.write("test_good.py", MODULES["test_pass"])
        self.write("test_bad.py", MODULES["test_fail"])
        self.write("test_skipped.py", MODULES["test_skipped"])
        self.write("test_vanish.py", """
            import os
            import unittest

            class VanishTests(unittest.TestCase):
                def test_exit_before_report(self):
                    os._exit(0)
        """)
        self.write("plain.py", "raise SystemExit(0)\n")
        other = "macos" if runner.current_platform() != "macos" else "linux"
        self.steps = [
            self.step("bad", "-m", "unittest", "tests.test_bad"),
            self.step("good", "-m", "unittest", "-v", "tests.test_good"),
            self.step("other-platform", "-m", "unittest", "tests.test_good", platforms=[other]),
            self.step("plain", "{repo}/tests/plain.py", covers="tests/plain.py"),
            self.step("skipped", "-m", "unittest", "tests.test_skipped"),
            self.step("vanish", "-m", "unittest", "tests.test_vanish"),
        ]
        self.report = self.root / "report.json"

    def write(self, name: str, source: str) -> None:
        (self.root / "tests" / name).write_text(textwrap.dedent(source), encoding="utf-8")

    def step(self, step_id: str, *argv: str, covers: str | None = None, platforms: list[str] | None = None) -> dict:
        module = next((item for item in argv if item.startswith("tests.")), "")
        value = {
            "id": step_id,
            "suites": ["fast"],
            "argv": ["{python}", *argv],
            "covers": [covers or module.replace(".", "/") + ".py"],
        }
        if platforms:
            value["platforms"] = platforms
        return value

    def run_runner(self, *args: str) -> subprocess.CompletedProcess[str]:
        registry = self.root / "configs" / "test-suites.json"
        registry.write_text(json.dumps({
            "$schema": "./schemas/test-suites.v1.schema.json",
            "schema_version": 1,
            "capabilities": {},
            "steps": self.steps,
        }), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(RUNNER), "--repo-root", str(self.root), "--registry", str(registry), *args],
            check=False, text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def test_case_report_requires_report_file(self) -> None:
        result = self.run_runner("--case-report")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--case-report requires --report-file", result.stderr)

    def test_report_v2_records_cases_without_changing_step_status(self) -> None:
        result = self.run_runner("--case-report", "--report-file", str(self.report))
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["state"], "complete")
        self.assertEqual(set(payload["context"]),
                         {"dirty", "platform", "python", "revision", "selected_steps", "suite"})
        self.assertEqual(payload["context"]["selected_steps"],
                         ["bad", "good", "other-platform", "plain", "skipped", "vanish"])
        self.assertEqual(payload["context"]["platform"], payload["platform"])
        self.assertEqual(payload["context"]["suite"], "fast")
        self.assertEqual(payload["totals"], {"fail": 1, "pass": 4, "skip": 1})
        steps = {step["id"]: step for step in payload["steps"]}
        expected = {
            "bad": ("fail", "failed", "complete"),
            "good": ("pass", "passed", "complete"),
            "other-platform": ("skip", "skipped_platform", None),
            "plain": ("pass", "passed", "unsupported-argv"),
            "skipped": ("pass", "passed", "complete"),
            "vanish": ("pass", "passed", "missing"),
        }
        for step_id, (status, outcome, report_status) in expected.items():
            with self.subTest(step=step_id):
                step = steps[step_id]
                self.assertEqual((step["status"], step["outcome"]), (status, outcome))
                self.assertEqual((step["case_report"] or {}).get("status"), report_status)
                self.assertTrue(self.V1_STEP_KEYS <= set(step))
        self.assertEqual(steps["good"]["termination"],
                         {"kind": "exited", "exit_code": 0, "signal": None, "cleanup_error": None})
        self.assertGreaterEqual(steps["good"]["duration_seconds"], 0)
        self.assertEqual([case["id"] for case in steps["good"]["case_report"]["cases"]],
                         ["tests.test_good.PassTests.test_ok"])
        self.assertTrue(steps["skipped"]["case_report"]["all_skipped"])
        self.assertEqual(steps["bad"]["termination"]["exit_code"], 1)
        self.assertIsNone(steps["other-platform"]["termination"])

    @unittest.skipIf(os.name == "nt", "POSIX signals")
    def test_signal_death_is_a_crash(self) -> None:
        self.write("test_crash.py", """
            import os
            import signal
            import unittest

            class CrashTests(unittest.TestCase):
                def test_crash(self):
                    os.kill(os.getpid(), signal.SIGKILL)
        """)
        self.steps = [self.step("crash", "-m", "unittest", "tests.test_crash")]
        self.run_runner("--case-report", "--report-file", str(self.report))
        step = json.loads(self.report.read_text(encoding="utf-8"))["steps"][0]
        self.assertEqual((step["status"], step["outcome"]), ("fail", "crash"))
        self.assertEqual(step["termination"]["kind"], "signal")
        self.assertEqual(step["termination"]["signal"], 9)
        self.assertEqual(step["case_report"]["status"], "missing")

    def test_without_flag_report_stays_version_one(self) -> None:
        result = self.run_runner("--report-file", str(self.report))
        self.assertEqual(result.returncode, 1, result.stderr)
        payload = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(set(payload), self.V1_ROOT_KEYS)
        for step in payload["steps"]:
            self.assertEqual(set(step), self.V1_STEP_KEYS)
        self.assertNotIn("case-report", result.stdout)


if __name__ == "__main__":
    unittest.main()
