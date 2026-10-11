from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.support.fixtures import load_script_module


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "assets" / "check-test-selection.py"
selection = load_script_module(CHECKER, "check_test_selection")

ALPHA = """
    import unittest


    class AlphaTests(unittest.TestCase):
        def test_alpha(self):
            pass
"""


class SelectionFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "tests").mkdir()
        (root / "configs").mkdir()

    def module(self, name: str, source: str) -> None:
        path = self.root.joinpath(*name.split(".")).with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")

    def step(self, step_id: str, *selectors: str, platforms: list[str] | None = None,
             covers: list[str] | None = None) -> dict:
        modules = sorted({"/".join(item.split(".")[:2]) + ".py" for item in selectors if item != "-v"})
        step = {
            "id": step_id,
            "suites": ["fast"],
            "argv": ["{python}", "-m", "unittest", *selectors],
            "covers": modules if covers is None else covers,
        }
        if platforms is not None:
            step["platforms"] = platforms
        return step

    def check(self, *steps: dict, exceptions: tuple = ()) -> list[str]:
        (self.root / "configs" / "test-suites.json").write_text(
            json.dumps({"steps": list(steps)}), encoding="utf-8"
        )
        return list(selection.check_selection(self.root, exceptions=exceptions).errors)


class TestSelectionCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="test-selection-")
        self.addCleanup(temporary.cleanup)
        self.fixture = SelectionFixture(Path(temporary.name))
        self.fixture.module("tests.test_alpha", ALPHA)

    def assertSingleError(self, errors: list[str], fragment: str) -> None:
        self.assertEqual(len(errors), 1, errors)
        self.assertIn(fragment, errors[0])

    def test_current_registry_passes_through_the_cli(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CHECKER)],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"^Test selection OK: \d+ concrete classes in \d+ modules")

    def test_cli_reports_errors_and_exits_nonzero(self) -> None:
        self.fixture.check()
        result = subprocess.run(
            [sys.executable, str(CHECKER), "--repo-root", str(self.fixture.root)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR: tests.test_alpha.AlphaTests is not selected by any step", result.stderr)

    def test_module_and_class_selectors_account_for_every_class(self) -> None:
        self.fixture.module("tests.test_beta", ALPHA.replace("Alpha", "Beta").replace("alpha", "beta"))
        self.assertEqual(self.fixture.check(
            self.fixture.step("alpha", "-v", "tests.test_alpha"),
            self.fixture.step("beta", "tests.test_beta.BetaTests"),
        ), [])

    def test_newly_added_class_is_reported_as_unselected(self) -> None:
        self.fixture.module("tests.test_alpha", ALPHA + """

    class AddedLaterTests(unittest.TestCase):
        def test_added(self):
            pass
""")
        errors = self.fixture.check(self.fixture.step("alpha", "tests.test_alpha.AlphaTests"))
        self.assertSingleError(errors, "tests.test_alpha.AddedLaterTests is not selected by any step")

    def test_inherited_mixin_tests_make_a_class_concrete(self) -> None:
        self.fixture.module("tests.support.mixins", """
            class SharedContract:
                def test_shared(self):
                    pass
        """)
        self.fixture.module("tests.test_alpha", """
            import unittest as ut
            from tests.support.mixins import SharedContract


            class Base(ut.TestCase):
                pass


            class PosixTests(SharedContract, Base):
                pass
        """)
        self.assertEqual(self.fixture.check(self.fixture.step("alpha", "tests.test_alpha.PosixTests")), [])
        self.assertSingleError(
            self.fixture.check(
                self.fixture.step("alpha", "tests.test_alpha.PosixTests"),
                self.fixture.step("base", "tests.test_alpha.Base"),
            ),
            "selector 'tests.test_alpha.Base' selects no concrete test class",
        )

    def test_assigned_test_attribute_counts_as_a_test(self) -> None:
        self.fixture.module("tests.test_alpha", """
            from unittest import TestCase


            def make():
                return lambda self: None


            class GeneratedTests(TestCase):
                test_generated = make()
        """)
        errors = self.fixture.check()
        self.assertSingleError(errors, "tests.test_alpha.GeneratedTests is not selected")

    def test_duplicate_selection_on_shared_platform_is_reported(self) -> None:
        errors = self.fixture.check(
            self.fixture.step("whole", "tests.test_alpha", platforms=["linux", "wsl2"]),
            self.fixture.step("single", "tests.test_alpha.AlphaTests"),
        )
        self.assertSingleError(errors, "selected by both 'whole' and 'single' on linux, wsl2")

    def test_duplicate_selection_on_disjoint_platforms_is_allowed(self) -> None:
        self.assertEqual(self.fixture.check(
            self.fixture.step("posix", "tests.test_alpha", platforms=["linux", "macos", "wsl2"]),
            self.fixture.step("windows", "tests.test_alpha.AlphaTests", platforms=["windows"]),
        ), [])

    def test_reviewed_exceptions_excuse_matching_violations_only(self) -> None:
        manual = selection.ReviewedException("tests.test_alpha", "unselected", "manual only", "dots-x")
        self.assertEqual(self.fixture.check(exceptions=(manual,)), [])
        errors = self.fixture.check(self.fixture.step("alpha", "tests.test_alpha"), exceptions=(manual,))
        self.assertSingleError(errors, "stale reviewed exception 'tests.test_alpha' (unselected)")
        wrong_kind = selection.ReviewedException("tests.test_alpha", "duplicate", "repeat", "dots-x")
        errors = self.fixture.check(exceptions=(wrong_kind,))
        self.assertEqual(len(errors), 2, errors)
        self.assertIn("is not selected by any step", errors[0])
        self.assertIn("stale reviewed exception", errors[1])
        incomplete = selection.ReviewedException("tests.test_alpha", "unselected", "", "dots-x")
        self.assertSingleError(self.fixture.check(exceptions=(incomplete,)), "needs a known kind")

    def test_dangling_selectors_are_reported(self) -> None:
        for selector, fragment in (
            ("tests.test_missing", "names a missing module"),
            ("tests.test_alpha.MissingTests", "selects no concrete test class"),
            ("tests.test_alpha.AlphaTests.test_alpha", "must name tests.test_<module>[.<Class>]"),
            ("other.test_alpha", "must name tests.test_<module>[.<Class>]"),
        ):
            with self.subTest(selector=selector):
                errors = self.fixture.check(
                    self.fixture.step("alpha", "tests.test_alpha"),
                    self.fixture.step("dangling", selector),
                )
                self.assertSingleError(errors, fragment)

    def test_unsupported_argv_fails_closed(self) -> None:
        for argv, fragment in (
            (["node", "--test"], "argv must start with {python} -m unittest"),
            (["{python}", "-m", "unittest", "--failfast"], "unsupported unittest option '--failfast'"),
        ):
            with self.subTest(argv=argv):
                step = self.fixture.step("odd", "tests.test_alpha", covers=[])
                step["argv"] = argv
                errors = self.fixture.check(step, self.fixture.step("real", "tests.test_alpha"))
                self.assertSingleError(errors, fragment)

    def test_covers_must_match_selector_modules(self) -> None:
        errors = self.fixture.check(
            self.fixture.step("alpha", "tests.test_alpha", covers=["tests/test_other.py"])
        )
        self.assertSingleError(errors, "must match its selector modules ['tests/test_alpha.py']")

    def test_load_tests_hook_fails_closed(self) -> None:
        for hook in (
            "def load_tests(loader, tests, pattern):\n    return tests\n",
            "load_tests = None\n",
            "from tests.support.hooks import load_tests\n",
        ):
            with self.subTest(hook=hook):
                self.fixture.module("tests.test_alpha", textwrap.dedent(ALPHA) + "\n\n" + hook)
                errors = self.fixture.check(self.fixture.step("alpha", "tests.test_alpha"))
                self.assertSingleError(errors, "defines load_tests")

    def test_conditional_definitions_fail_closed(self) -> None:
        for block in (
            "if True:\n    class HiddenTests(unittest.TestCase):\n        def test_hidden(self):\n            pass\n",
            "try:\n    def load_tests(loader, tests, pattern):\n        return tests\nexcept ImportError:\n    pass\n",
        ):
            with self.subTest(block=block.splitlines()[0]):
                self.fixture.module("tests.test_alpha", textwrap.dedent(ALPHA) + "\n\n" + block)
                errors = self.fixture.check(self.fixture.step("alpha", "tests.test_alpha"))
                self.assertIn("inside a module-level block", errors[0])
                self.assertEqual(len(errors), 2, errors)
                self.assertIn("selects no concrete test class", errors[1])

    def test_unresolvable_base_fails_closed(self) -> None:
        for header, base, fragment in (
            ("", "make_base()", "cannot resolve base 'make_base()'"),
            ("from .helpers import Base\n", "Base", "cannot resolve relative import of 'Base'"),
            ("", "Mystery, unittest.TestCase", "cannot resolve base 'Mystery'"),
            ("import tests.support.missing as missing\n", "missing.Base", "cannot resolve class tests.support.missing.Base"),
        ):
            with self.subTest(base=base):
                self.fixture.module(
                    "tests.test_alpha",
                    f"import unittest\n{header}\n\nclass OddTests({base}):\n    def test_one(self):\n        pass\n",
                )
                errors = self.fixture.check(self.fixture.step("alpha", "tests.test_alpha"))
                self.assertIn(fragment, errors[0])
                self.assertEqual(len(errors), 2, errors)
                self.assertIn("selects no concrete test class", errors[1])

    def test_imported_concrete_class_is_reported(self) -> None:
        self.fixture.module("tests.support.shared", ALPHA.replace("Alpha", "Shared"))
        self.fixture.module("tests.test_beta", """
            import unittest
            from tests.support.shared import SharedTests


            class BetaTests(unittest.TestCase):
                def test_beta(self):
                    pass
        """)
        errors = self.fixture.check(
            self.fixture.step("alpha", "tests.test_alpha"),
            self.fixture.step("beta", "tests.test_beta"),
        )
        self.assertSingleError(
            errors, "tests.test_beta: imports concrete test class tests.support.shared.SharedTests"
        )

    def test_module_without_concrete_classes_is_reported(self) -> None:
        self.fixture.module("tests.test_empty", """
            import unittest


            class HelperBase(unittest.TestCase):
                pass
        """)
        errors = self.fixture.check(self.fixture.step("alpha", "tests.test_alpha"))
        self.assertSingleError(errors, "tests.test_empty: defines no concrete test class")

    def test_unreadable_registry_is_an_error(self) -> None:
        (self.fixture.root / "configs" / "test-suites.json").write_text("{", encoding="utf-8")
        errors = list(selection.check_selection(self.fixture.root).errors)
        self.assertSingleError(errors, "cannot read steps")

    def test_shipped_exceptions_are_reviewed_and_minimal(self) -> None:
        self.assertEqual(
            [(item.selector, item.kind) for item in selection.REVIEWED_EXCEPTIONS],
            [("tests.test_macos_shell_startup_smoke", "unselected")],
        )


if __name__ == "__main__":
    unittest.main()
