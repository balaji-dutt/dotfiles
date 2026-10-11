#!/usr/bin/env python3
"""Check that registered unittest selectors collect every concrete test class once per platform."""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple


REGISTRY_PATH = Path("configs/test-suites.json")
UNITTEST_PREFIX = ["{python}", "-m", "unittest"]
STEP_PLATFORMS = ("linux", "macos", "windows", "wsl2")
TESTCASE_BASES = frozenset({"unittest.TestCase", "unittest.IsolatedAsyncioTestCase"})
EXCEPTION_KINDS = frozenset({"duplicate", "unselected"})
DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class ReviewedException:
    selector: str
    kind: str
    reason: str
    work_item: str


REVIEWED_EXCEPTIONS = (
    ReviewedException(
        selector="tests.test_macos_shell_startup_smoke",
        kind="unselected",
        reason="manual-only native macOS smoke: it needs DOTFILES_MACOS_SMOKE=1 in a "
        "dedicated account, which the runner never passes",
        work_item="dots-4jy.10.19.4",
    ),
)


class CheckFailure(Exception):
    """Raised when a source or registry construct cannot be analyzed safely."""


@dataclass(frozen=True)
class Violation:
    kind: str
    selector: str
    message: str


@dataclass(frozen=True)
class CheckResult:
    errors: tuple[str, ...]
    concrete_classes: tuple[str, ...]
    modules: tuple[str, ...]
    steps: int


class ParsedModule(NamedTuple):
    classes: dict[str, ast.ClassDef]
    bindings: dict[str, tuple[str, ...]]
    has_load_tests: bool


class SourceIndex:
    """Resolve class bases across repository modules without importing them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._modules: dict[str, ParsedModule] = {}
        self._testcase: dict[tuple[str, str], bool] = {}
        self._has_tests: dict[tuple[str, str], bool] = {}

    def module_path(self, module: str) -> Path:
        return self.root.joinpath(*module.split(".")).with_suffix(".py")

    def has_module(self, module: str) -> bool:
        return self.module_path(module).is_file()

    def module(self, module: str) -> ParsedModule:
        if module not in self._modules:
            path = self.module_path(module)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError, ValueError) as error:
                raise CheckFailure(f"{module}: cannot parse source: {error}") from error
            classes: dict[str, ast.ClassDef] = {}
            bindings: dict[str, tuple[str, ...]] = {}
            has_load_tests = False
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    classes[node.name] = node
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    has_load_tests = has_load_tests or node.name == "load_tests"
                elif any(isinstance(child, DEFINITIONS) for child in ast.walk(node)):
                    raise CheckFailure(
                        f"{module}: line {node.lineno} defines a class or function inside a "
                        "module-level block, which this check cannot follow"
                    )
                elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    has_load_tests = has_load_tests or any(
                        isinstance(target, ast.Name) and target.id == "load_tests" for target in targets
                    )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.asname:
                            bindings[alias.asname] = ("module", alias.name)
                        else:
                            root_name = alias.name.split(".")[0]
                            bindings[root_name] = ("module", root_name)
                elif isinstance(node, ast.ImportFrom):
                    source = ("." * node.level) + (node.module or "")
                    for alias in node.names:
                        bindings[alias.asname or alias.name] = ("attr", source, alias.name)
            has_load_tests = has_load_tests or "load_tests" in bindings
            self._modules[module] = ParsedModule(classes, bindings, has_load_tests)
        return self._modules[module]

    def resolve(self, module: str, expression: ast.expr) -> tuple[str, ...]:
        """Return ("class", module, name) for repository classes or ("external", dotted)."""
        classes, bindings, _ = self.module(module)
        if isinstance(expression, ast.Name):
            name = expression.id
            if name in classes:
                return ("class", module, name)
            binding = bindings.get(name)
            if binding and binding[0] == "attr":
                return self._resolve_attribute(module, binding[1], binding[2])
            if binding is None and hasattr(builtins, name):
                return ("external", f"builtins.{name}")
        elif isinstance(expression, ast.Attribute):
            parts = [expression.attr]
            value = expression.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name) and bindings.get(value.id, ("",))[0] == "module":
                dotted = ".".join([bindings[value.id][1], *reversed(parts)])
                owner, _, name = dotted.rpartition(".")
                return self._resolve_attribute(module, owner, name)
        raise CheckFailure(f"{module}: cannot resolve base {ast.unparse(expression)!r}")

    def _resolve_attribute(self, module: str, source: str, name: str) -> tuple[str, ...]:
        if source.startswith("."):
            raise CheckFailure(f"{module}: cannot resolve relative import of {name!r}")
        if source == "tests" or source.startswith("tests."):
            ref = self.find_class(source, name)
            if ref is None:
                raise CheckFailure(f"{module}: cannot resolve class {source}.{name}")
            return ref
        return ("external", f"{source}.{name}")

    def find_class(self, source: str, name: str) -> tuple[str, ...] | None:
        """Follow re-exports to a repository class, or return None for any other name."""
        if not self.has_module(source):
            return None
        classes, bindings, _ = self.module(source)
        if name in classes:
            return ("class", source, name)
        binding = bindings.get(name)
        if binding and binding[0] == "attr" and (
            binding[1] == "tests" or binding[1].startswith("tests.")
        ):
            return self.find_class(binding[1], binding[2])
        return None

    def is_testcase(self, ref: tuple[str, ...]) -> bool:
        if ref[0] == "external":
            return ref[1] in TESTCASE_BASES
        key = (ref[1], ref[2])
        if key not in self._testcase:
            self._testcase[key] = False
            node = self.module(ref[1]).classes[ref[2]]
            self._testcase[key] = any(
                self.is_testcase(self.resolve(ref[1], base)) for base in node.bases
            )
        return self._testcase[key]

    def has_tests(self, ref: tuple[str, ...]) -> bool:
        if ref[0] == "external":
            return False
        key = (ref[1], ref[2])
        if key not in self._has_tests:
            self._has_tests[key] = False
            node = self.module(ref[1]).classes[ref[2]]
            self._has_tests[key] = _defines_test_attribute(node) or any(
                self.has_tests(self.resolve(ref[1], base)) for base in node.bases
            )
        return self._has_tests[key]

    def concrete_classes(self, module: str) -> list[str]:
        classes = self.module(module).classes
        found = []
        for name in classes:
            ref = ("class", module, name)
            if self.is_testcase(ref) and self.has_tests(ref):
                found.append(f"{module}.{name}")
        return found


def _defines_test_attribute(node: ast.ClassDef) -> bool:
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test"):
            return True
        targets = item.targets if isinstance(item, ast.Assign) else (
            [item.target] if isinstance(item, ast.AnnAssign) else []
        )
        if any(isinstance(target, ast.Name) and target.id.startswith("test") for target in targets):
            return True
    return False


def discover_modules(root: Path) -> list[str]:
    return sorted(f"tests.{path.stem}" for path in (root / "tests").glob("test_*.py"))


def step_selectors(step: dict, index: SourceIndex) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    label = f"step {step.get('id')!r}"
    argv = step.get("argv", [])
    if argv[: len(UNITTEST_PREFIX)] != UNITTEST_PREFIX:
        return [], [f"{label}: argv must start with {' '.join(UNITTEST_PREFIX)}"]
    selectors = []
    for item in argv[len(UNITTEST_PREFIX):]:
        if item == "-v":
            continue
        parts = item.split(".")
        if item.startswith("-"):
            errors.append(f"{label}: unsupported unittest option {item!r}")
        elif len(parts) not in (2, 3) or parts[0] != "tests" or not parts[1].startswith("test_"):
            errors.append(f"{label}: selector {item!r} must name tests.test_<module>[.<Class>]")
        elif not index.has_module(".".join(parts[:2])):
            errors.append(f"{label}: selector {item!r} names a missing module")
        else:
            selectors.append(item)
    return selectors, errors


def check_selection(
    root: Path,
    registry_path: Path | None = None,
    exceptions: tuple[ReviewedException, ...] = REVIEWED_EXCEPTIONS,
) -> CheckResult:
    root = root.resolve()
    registry_file = registry_path or REGISTRY_PATH
    if not registry_file.is_absolute():
        registry_file = root / registry_file
    try:
        steps = json.loads(registry_file.read_text(encoding="utf-8"))["steps"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return CheckResult((f"{registry_file}: cannot read steps: {error}",), (), (), 0)

    index = SourceIndex(root)
    errors: list[str] = []
    modules = discover_modules(root)
    concrete: dict[str, list[str]] = {}
    for module in modules:
        try:
            classes, bindings, has_load_tests = index.module(module)
            concrete[module] = index.concrete_classes(module)
            for name, binding in bindings.items():
                if binding[0] != "attr" or name in classes:
                    continue
                ref = index.find_class(binding[1], binding[2])
                if ref is not None and index.is_testcase(ref) and index.has_tests(ref):
                    errors.append(
                        f"{module}: imports concrete test class {ref[1]}.{ref[2]}, "
                        "which unittest would also collect here"
                    )
        except CheckFailure as error:
            errors.append(str(error))
            concrete.setdefault(module, [])
            continue
        if has_load_tests:
            errors.append(f"{module}: defines load_tests, which this check cannot follow")
        if not concrete[module]:
            errors.append(f"{module}: defines no concrete test class")

    selected: dict[str, list[tuple[str, frozenset[str]]]] = {}
    for step in steps:
        selectors, step_errors = step_selectors(step, index)
        errors.extend(step_errors)
        platforms = frozenset(step.get("platforms") or STEP_PLATFORMS)
        modules_named = set()
        for selector in selectors:
            module = ".".join(selector.split(".")[:2])
            modules_named.add(module)
            members = concrete.get(module, [])
            hits = members if selector == module else ([selector] if selector in members else [])
            if not hits:
                errors.append(f"step {step.get('id')!r}: selector {selector!r} selects no concrete test class")
            for name in hits:
                selected.setdefault(name, []).append((step.get("id"), platforms))
        expected_covers = sorted(index.module_path(module).relative_to(root).as_posix() for module in modules_named)
        if not step_errors and sorted(step.get("covers", [])) != expected_covers:
            errors.append(
                f"step {step.get('id')!r}: covers {sorted(step.get('covers', []))} "
                f"must match its selector modules {expected_covers}"
            )

    violations: list[Violation] = []
    for module in modules:
        for name in concrete.get(module, []):
            if name not in selected:
                violations.append(Violation("unselected", name, f"{name} is not selected by any step"))
    for name, owners in sorted(selected.items()):
        for first in range(len(owners)):
            for second in range(first + 1, len(owners)):
                overlap = owners[first][1] & owners[second][1]
                if overlap:
                    violations.append(Violation(
                        "duplicate",
                        name,
                        f"{name} is selected by both {owners[first][0]!r} and {owners[second][0]!r} "
                        f"on {', '.join(sorted(overlap))}",
                    ))

    used: set[ReviewedException] = set()
    for violation in violations:
        match = next((
            exception for exception in exceptions
            if exception.kind == violation.kind
            and (violation.selector == exception.selector
                 or violation.selector.startswith(exception.selector + "."))
        ), None)
        if match is None:
            errors.append(violation.message)
        else:
            used.add(match)
    for exception in exceptions:
        if exception.kind not in EXCEPTION_KINDS or not exception.reason or not exception.work_item:
            errors.append(f"reviewed exception {exception.selector!r} needs a known kind, reason and work item")
        elif exception not in used:
            errors.append(
                f"stale reviewed exception {exception.selector!r} ({exception.kind}): "
                "nothing it excuses remains"
            )

    names = tuple(sorted(name for members in concrete.values() for name in members))
    return CheckResult(tuple(errors), names, tuple(modules), len(steps))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: parent of assets/)",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        help="suite registry path, absolute or relative to --repo-root",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = check_selection(args.repo_root, args.registry)
    if result.errors:
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        f"Test selection OK: {len(result.concrete_classes)} concrete classes in "
        f"{len(result.modules)} modules across {result.steps} steps "
        f"({len(REVIEWED_EXCEPTIONS)} reviewed exceptions)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
