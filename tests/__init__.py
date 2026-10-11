"""Give direct unittest runs the same synthetic environment as assets/run-tests.py."""

from __future__ import annotations

import atexit
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path


SANDBOX_MARKER = "DOTFILES_TEST_SANDBOX"


def _enter_runner_sandbox() -> None:
    if os.environ.get(SANDBOX_MARKER):
        return
    repo_root = Path(__file__).resolve().parents[1]
    name = "_dotfiles_run_tests"
    spec = importlib.util.spec_from_file_location(name, repo_root / "assets" / "run-tests.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load assets/run-tests.py to isolate this test run")
    runner = importlib.util.module_from_spec(spec)
    sys.modules[name] = runner
    spec.loader.exec_module(runner)
    sandbox = Path(tempfile.mkdtemp(prefix="dotfiles-tests-"))
    atexit.register(shutil.rmtree, sandbox, True)
    environment = runner.isolated_environment(repo_root, sandbox)
    os.environ.clear()
    os.environ.update(environment)
    tempfile.tempdir = None


_enter_runner_sandbox()
