#!/usr/bin/env python3
"""Run unittest selectors like `python -m unittest` and record per-case results as JSON.

Usage: unittest-case-report.py --case-report PATH [unittest arguments...]
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path


SIDECAR_SCHEMA_VERSION = 1


class CaseResult(unittest.TextTestResult):
    """Record one entry per test method, with subtests nested under it."""

    latest: CaseResult | None = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cases: dict[str, dict] = {}
        self._started: dict[str, float] = {}
        CaseResult.latest = self

    def _case(self, case_id: str) -> dict:
        return self.cases.setdefault(
            case_id,
            {"id": case_id, "outcome": None, "reason": None, "duration_seconds": None, "subtests": []},
        )

    def _record(self, test, outcome: str, reason: str | None = None) -> None:
        if not isinstance(test, unittest.TestCase):
            case = self._case(test.id())
            case["outcome"] = "skipped" if outcome == "skipped" else "fixture_error"
            case["reason"] = reason
            return
        parent = getattr(test, "test_case", None)
        if isinstance(parent, unittest.TestCase):
            self._case(parent.id())["subtests"].append({
                "description": test.id().removeprefix(parent.id()).strip(),
                "outcome": outcome,
                "reason": reason,
            })
            return
        case = self._case(test.id())
        case["outcome"] = outcome
        case["reason"] = reason

    def startTest(self, test) -> None:
        super().startTest(test)
        self._case(test.id())
        self._started[test.id()] = time.perf_counter()

    def stopTest(self, test) -> None:
        super().stopTest(test)
        started = self._started.pop(test.id(), None)
        case = self._case(test.id())
        if started is not None:
            case["duration_seconds"] = round(time.perf_counter() - started, 6)
        if case["outcome"] is None:
            children = {child["outcome"] for child in case["subtests"]}
            if "error" in children:
                case["outcome"] = "error"
            elif "failed" in children:
                case["outcome"] = "failed"
            elif "skipped" in children:
                case["outcome"] = "passed" if "passed" in children else "skipped"
            else:
                case["outcome"] = "incomplete"

    def addSuccess(self, test) -> None:
        super().addSuccess(test)
        self._record(test, "passed")

    def addFailure(self, test, err) -> None:
        super().addFailure(test, err)
        self._record(test, "failed")

    def addError(self, test, err) -> None:
        super().addError(test, err)
        self._record(test, "error")

    def addSkip(self, test, reason) -> None:
        super().addSkip(test, reason)
        self._record(test, "skipped", reason)

    def addExpectedFailure(self, test, err) -> None:
        super().addExpectedFailure(test, err)
        self._record(test, "expected_failure")

    def addUnexpectedSuccess(self, test) -> None:
        super().addUnexpectedSuccess(test)
        self._record(test, "unexpected_success")

    def addSubTest(self, test, subtest, err) -> None:
        super().addSubTest(test, subtest, err)
        if err is None:
            outcome = "passed"
        elif issubclass(err[0], test.failureException):
            outcome = "failed"
        else:
            outcome = "error"
        self._record(subtest, outcome)


class CaseRunner(unittest.TextTestRunner):
    resultclass = CaseResult


def write_sidecar(path: Path, exit_code: int | None) -> None:
    result = CaseResult.latest
    payload = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "complete": result is not None and exit_code is not None,
        "exit_code": exit_code,
        "tests_run": result.testsRun if result else 0,
        "cases": list(result.cases.values()) if result else [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "--case-report":
        print("usage: unittest-case-report.py --case-report PATH [unittest arguments...]", file=sys.stderr)
        return 2
    sidecar = Path(argv[1])
    if not getattr(sys.flags, "safe_path", False):
        sys.path[0] = os.getcwd()
    exit_code: int | None = None
    try:
        unittest.main(module=None, argv=["python -m unittest", *argv[2:]], testRunner=CaseRunner)
    except SystemExit as stop:
        exit_code = stop.code if isinstance(stop.code, int) else int(stop.code is not None)
        raise
    finally:
        try:
            write_sidecar(sidecar, exit_code)
        except OSError as error:
            print(f"WARNING: cannot write case report {sidecar}: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
