#!/usr/bin/env python3
"""Run the repository's registered test suites in an isolated environment."""

from __future__ import annotations

import argparse
import collections
import json
import os
import platform as python_platform
import re
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Callable


SUITES = ("fast", "integration", "render", "provenance", "platform")
SCHEMA_REF = "./schemas/test-suites.v1.schema.json"
PLATFORMS = ("linux", "macos", "windows", "wsl2")
STEP_PLACEHOLDERS = {"python", "repo"}
DEFAULT_STEP_TIMEOUT_SECONDS = 600
SANDBOX_MARKER = "DOTFILES_TEST_SANDBOX"
STEP_STOP_GRACE_SECONDS = 1
CASE_REPORT_ADAPTER = Path(__file__).resolve().parent / "unittest-case-report.py"
STEP_OUTPUT_LIMIT_BYTES = 1024 * 1024
WINDOWS_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
WINDOWS_TASKKILL_TIMEOUT_SECONDS = 10
WINDOWS_TASKKILL_NOT_FOUND = 128
WINDOWS_NTSTATUS_ERROR = 0xC0000000
IS_WINDOWS = os.name == "nt"
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?$")
PASSTHROUGH_ENV = {
    "COMSPEC",
    "DEVCONTAINER_SMOKE",
    "DEVCONTAINER_SMOKE_ARTIFACTS",
    "DEVCONTAINER_SMOKE_TIMEOUT_SECONDS",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TERM",
    "TZ",
    "WINDIR",
    "WSL_DISTRO_NAME",
    "WSL_INTEROP",
}


class ConfigurationError(ValueError):
    """Raised when the suite registry is invalid."""


@dataclass(frozen=True)
class Capability:
    name: str
    commands: tuple[str, ...]
    probe: tuple[str, ...]


@dataclass(frozen=True)
class Step:
    step_id: str
    suites: tuple[str, ...]
    argv: tuple[str, ...]
    covers: tuple[str, ...]
    platforms: tuple[str, ...]
    requires: tuple[str, ...]
    timeout_seconds: int = DEFAULT_STEP_TIMEOUT_SECONDS


@dataclass(frozen=True)
class Registry:
    capabilities: dict[str, Capability]
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class StepResult:
    step: Step
    status: str
    reason: str | None
    exit_code: int | None
    outcome: str = ""
    duration_seconds: float | None = None
    termination: dict[str, object] | None = None
    case_report: dict[str, object] | None = None


@dataclass(frozen=True)
class RunResult:
    platform: str
    steps: tuple[StepResult, ...]
    state: str = "complete"
    forced_exit_code: int | None = None

    @property
    def exit_code(self) -> int:
        if self.forced_exit_code is not None:
            return self.forced_exit_code
        return 1 if any(result.status == "fail" for result in self.steps) else 0


def _string_list(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
    allow_duplicates: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ConfigurationError(f"{label} must be a non-empty array of strings")
    if not all(isinstance(item, str) and item for item in value):
        raise ConfigurationError(f"{label} must contain non-empty strings")
    if not allow_duplicates and len(value) != len(set(value)):
        raise ConfigurationError(f"{label} must not contain duplicates")
    return tuple(value)


def _validate_placeholders(value: str, label: str, allowed: set[str]) -> None:
    try:
        fields = {field for _, field, _, _ in Formatter().parse(value) if field is not None}
    except ValueError as error:
        raise ConfigurationError(f"{label} has invalid placeholder syntax: {error}") from error
    unknown = fields - allowed
    if unknown:
        raise ConfigurationError(f"{label} has unsupported placeholder(s): {', '.join(sorted(unknown))}")


def load_registry(repo_root: Path, registry_path: Path) -> Registry:
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"cannot read registry {registry_path}: {error}") from error
    if not isinstance(payload, dict):
        raise ConfigurationError("registry root must be an object")
    if payload.get("$schema") != SCHEMA_REF:
        raise ConfigurationError(f"$schema must be {SCHEMA_REF!r}")
    if payload.get("schema_version") != 1:
        raise ConfigurationError("schema_version must be 1")

    raw_capabilities = payload.get("capabilities")
    if not isinstance(raw_capabilities, dict):
        raise ConfigurationError("capabilities must be an object")
    capabilities: dict[str, Capability] = {}
    for name, raw in raw_capabilities.items():
        if not isinstance(name, str) or not ID_PATTERN.fullmatch(name):
            raise ConfigurationError(f"invalid capability name: {name!r}")
        if not isinstance(raw, dict):
            raise ConfigurationError(f"capabilities.{name} must be an object")
        command = raw.get("command")
        commands_value = raw.get("commands")
        if command is not None and commands_value is not None:
            raise ConfigurationError(
                f"capabilities.{name} must define only one of command or commands"
            )
        if command is not None:
            if not isinstance(command, str) or not command:
                raise ConfigurationError(f"capabilities.{name}.command must be a non-empty string")
            commands = (command,)
        else:
            commands = _string_list(commands_value, f"capabilities.{name}.commands")
        for command_index, command_value in enumerate(commands):
            _validate_placeholders(
                command_value,
                f"capabilities.{name}.commands[{command_index}]",
                STEP_PLACEHOLDERS,
            )
        probe = _string_list(
            raw.get("probe", []),
            f"capabilities.{name}.probe",
            allow_empty=True,
            allow_duplicates=True,
        )
        for probe_index, probe_value in enumerate(probe):
            _validate_placeholders(
                probe_value,
                f"capabilities.{name}.probe[{probe_index}]",
                STEP_PLACEHOLDERS,
            )
        capabilities[name] = Capability(name, commands, probe)

    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ConfigurationError("steps must be a non-empty array")
    steps: list[Step] = []
    seen_ids: set[str] = set()
    previous_id = ""
    for index, raw in enumerate(raw_steps):
        label = f"steps[{index}]"
        if not isinstance(raw, dict):
            raise ConfigurationError(f"{label} must be an object")
        step_id = raw.get("id")
        if not isinstance(step_id, str) or not ID_PATTERN.fullmatch(step_id):
            raise ConfigurationError(f"{label}.id is invalid: {step_id!r}")
        if step_id in seen_ids:
            raise ConfigurationError(f"{label}.id duplicates {step_id!r}")
        if previous_id and step_id < previous_id:
            raise ConfigurationError(f"{label}.id must sort after {previous_id!r}")
        previous_id = step_id
        seen_ids.add(step_id)

        suites = _string_list(raw.get("suites"), f"{label}.suites")
        invalid_suites = set(suites) - set(SUITES)
        if invalid_suites:
            raise ConfigurationError(f"{label}.suites has unsupported value(s): {', '.join(sorted(invalid_suites))}")
        argv = _string_list(raw.get("argv"), f"{label}.argv", allow_duplicates=True)
        for item_index, item in enumerate(argv):
            _validate_placeholders(item, f"{label}.argv[{item_index}]", STEP_PLACEHOLDERS)
        covers = _string_list(raw.get("covers"), f"{label}.covers")
        for path_text in covers:
            path = Path(path_text)
            if path.is_absolute() or ".." in path.parts:
                raise ConfigurationError(f"{label}.covers contains unsafe path {path_text!r}")
            if not (repo_root / path).is_file():
                raise ConfigurationError(f"{label}.covers references missing file {path_text!r}")
        platforms = _string_list(
            raw.get("platforms", list(PLATFORMS)),
            f"{label}.platforms",
        )
        invalid_platforms = set(platforms) - set(PLATFORMS)
        if invalid_platforms:
            raise ConfigurationError(
                f"{label}.platforms has unsupported value(s): {', '.join(sorted(invalid_platforms))}"
            )
        requires = _string_list(raw.get("requires", []), f"{label}.requires", allow_empty=True)
        unknown_capabilities = set(requires) - set(capabilities)
        if unknown_capabilities:
            raise ConfigurationError(
                f"{label}.requires references unknown capability(s): "
                f"{', '.join(sorted(unknown_capabilities))}"
            )
        timeout_seconds = raw.get("timeout_seconds", DEFAULT_STEP_TIMEOUT_SECONDS)
        if type(timeout_seconds) is not int or timeout_seconds < 1:
            raise ConfigurationError(f"{label}.timeout_seconds must be a positive integer")
        steps.append(Step(step_id, suites, argv, covers, platforms, requires, timeout_seconds))

    return Registry(capabilities, tuple(steps))


def current_platform(environ: dict[str, str] | os._Environ[str] = os.environ) -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if environ.get("WSL_DISTRO_NAME") or environ.get("WSL_INTEROP"):
        return "wsl2"
    return "linux"


def select_steps(registry: Registry, suite: str) -> tuple[Step, ...]:
    if suite == "all":
        return registry.steps
    return tuple(step for step in registry.steps if suite in step.suites)


def _write_guard_shims(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for command in ("bd", "dolt"):
        posix = directory / command
        posix.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' 'ERROR: blocked real {command} invocation in test sandbox' >&2\n"
            "exit 97\n",
            encoding="utf-8",
        )
        posix.chmod(0o755)
        (directory / f"{command}.cmd").write_text(
            f"@echo ERROR: blocked real {command} invocation in test sandbox 1>&2\r\n"
            "@exit /b 97\r\n",
            encoding="utf-8",
        )


def isolated_environment(repo_root: Path, sandbox: Path) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name.upper() in PASSTHROUGH_ENV}
    home = sandbox / "home"
    tmp = sandbox / "tmp"
    xdg_config = home / ".config"
    xdg_cache = home / ".cache"
    xdg_data = home / ".local" / "share"
    app_data = home / "AppData" / "Roaming"
    local_app_data = home / "AppData" / "Local"
    program_data = sandbox / "ProgramData"
    git_config = sandbox / "empty-gitconfig"
    for directory in (
        home,
        tmp,
        xdg_config,
        xdg_cache,
        xdg_data,
        app_data,
        local_app_data,
        program_data,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    git_config.write_text("", encoding="utf-8")
    guard_bin = sandbox / "guard-bin"
    _write_guard_shims(guard_bin)
    original_path = env.get("PATH", os.defpath)
    env.update(
        {
            "PATH": f"{guard_bin}{os.pathsep}{original_path}",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(app_data),
            "LOCALAPPDATA": str(local_app_data),
            "PROGRAMDATA": str(program_data),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_CACHE_HOME": str(xdg_cache),
            "XDG_DATA_HOME": str(xdg_data),
            "TMPDIR": str(tmp),
            "TMP": str(tmp),
            "TEMP": str(tmp),
            "GIT_CONFIG_GLOBAL": str(git_config),
            "GIT_CONFIG_SYSTEM": str(git_config),
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(repo_root),
            SANDBOX_MARKER: "1",
        }
    )
    return env


def capability_reason(
    capability: Capability,
    *,
    env: dict[str, str],
    repo_root: Path,
    lookup_path: str,
) -> tuple[str | None, str | None]:
    replacements = {"python": sys.executable, "repo": str(repo_root)}
    commands = tuple(command.format(**replacements) for command in capability.commands)
    executable = next(
        (
            resolved
            for command in commands
            if (resolved := shutil.which(command, path=lookup_path)) is not None
        ),
        None,
    )
    if executable is None:
        return f"missing capability {capability.name} ({' or '.join(commands)})", None
    executable = str(Path(executable).resolve())
    if not capability.probe:
        return None, executable
    probe = [value.format(**replacements) for value in capability.probe]
    probe[0] = executable
    try:
        result = subprocess.run(
            probe,
            cwd=repo_root,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return f"capability {capability.name} probe timed out", None
    except OSError:
        return f"capability probe failed for {capability.name} (cannot execute)", None
    if result.returncode != 0:
        return f"capability probe failed for {capability.name} (exit {result.returncode})", None
    return None, executable


def _expanded_argv(step: Step, repo_root: Path) -> list[str]:
    replacements = {"python": sys.executable, "repo": str(repo_root)}
    return [item.format(**replacements) for item in step.argv]


class _BoundedOutput:
    """Keep the last STEP_OUTPUT_LIMIT_BYTES of a stream and count what was dropped."""

    def __init__(self) -> None:
        self.chunks: collections.deque[bytes] = collections.deque()
        self.size = 0
        self.dropped = 0

    def add(self, chunk: bytes) -> None:
        self.chunks.append(chunk)
        self.size += len(chunk)
        while self.size - len(self.chunks[0]) >= STEP_OUTPUT_LIMIT_BYTES:
            removed = self.chunks.popleft()
            self.size -= len(removed)
            self.dropped += len(removed)

    def text(self) -> str:
        data = b"".join(self.chunks)
        excess = max(0, len(data) - STEP_OUTPUT_LIMIT_BYTES)
        text = data[excess:].decode("utf-8", errors="replace").replace("\r\n", "\n")
        omitted = self.dropped + excess
        if omitted:
            return f"[runner: {omitted} bytes of step output omitted]\n" + text
        return text


class _PosixPipes:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.outputs = (_BoundedOutput(), _BoundedOutput())
        self.selector = selectors.DefaultSelector()
        for stream, output in zip((process.stdout, process.stderr), self.outputs):
            self.selector.register(stream, selectors.EVENT_READ, output)

    def drain(self, until: float) -> bool:
        while self.selector.get_map():
            remaining = until - time.monotonic()
            if remaining <= 0:
                return False
            for key, _ in self.selector.select(timeout=remaining):
                chunk = os.read(key.fd, 65536)
                if chunk:
                    key.data.add(chunk)
                else:
                    self.selector.unregister(key.fileobj)
                    key.fileobj.close()
        return True

    def abandon(self) -> bool:
        streams = [key.fileobj for key in list(self.selector.get_map().values())]
        for stream in streams:
            self.selector.unregister(stream)
            stream.close()
        return bool(streams)

    def text(self) -> tuple[str, str]:
        return self.outputs[0].text(), self.outputs[1].text()


class _ThreadedPipes:
    """Windows pipes cannot be selected, so a daemon thread reads each one."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.outputs = (_BoundedOutput(), _BoundedOutput())
        self.threads = [
            threading.Thread(target=self._pump, args=(stream, output), daemon=True)
            for stream, output in zip((process.stdout, process.stderr), self.outputs)
        ]
        for thread in self.threads:
            thread.start()

    @staticmethod
    def _pump(stream, output: _BoundedOutput) -> None:
        try:
            while chunk := stream.read1(65536):
                output.add(chunk)
        except (OSError, ValueError):
            return
        finally:
            stream.close()

    def drain(self, until: float) -> bool:
        while any(thread.is_alive() for thread in self.threads):
            remaining = until - time.monotonic()
            if remaining <= 0:
                return False
            # Short joins let Ctrl+C reach the runner; one long lock wait ignores it on Windows.
            for thread in self.threads:
                thread.join(min(remaining, 0.25))
        return True

    def text(self) -> tuple[str, str]:
        return self.outputs[0].text(), self.outputs[1].text()


def _stop_step_group(process: subprocess.Popen[bytes], pipes: _PosixPipes) -> str | None:
    errors: list[str] = []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(f"cannot signal step group: {error}")
        try:
            pipes.drain(time.monotonic() + STEP_STOP_GRACE_SECONDS)
        except OSError as error:
            errors.append(f"cannot drain step output: {error}")
        # macOS refuses killpg with EPERM while the group leader is an unreaped zombie.
        process.poll()
    if process.poll() is None:
        try:
            process.wait(timeout=STEP_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            errors.append("step parent did not exit")
    if pipes.abandon():
        errors.append("step output did not drain")
    return "; ".join(errors) or None


def _run_posix_step(
    command: list[str], *, repo_root: Path, env: dict[str, str], timeout_seconds: int
) -> tuple[str, str, int | None, str | None, str, str | None]:
    previous = {}
    process = None
    pipes = None
    pending_signal = None

    def cancel_step(signum, frame):
        nonlocal pending_signal
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        if process is None:
            pending_signal = signum
        else:
            raise SystemExit(128 + signum)

    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            previous[sig] = signal.signal(sig, cancel_step)
    try:
        try:
            process = subprocess.Popen(
                command, cwd=repo_root, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
        except OSError as error:
            return "", "", None, f"cannot execute step: {error}", "launch_error", None
        pipes = _PosixPipes(process)
        if pending_signal is not None:
            raise SystemExit(128 + pending_signal)
        deadline = time.monotonic() + timeout_seconds
        if not pipes.drain(deadline):
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        process.wait(timeout=max(0, deadline - time.monotonic()))
        return *pipes.text(), process.returncode, None, "exited", None
    except subprocess.TimeoutExpired:
        cleanup_error = _stop_step_group(process, pipes)
        reason = f"timeout after {timeout_seconds} seconds"
        if cleanup_error:
            reason += f"; cleanup failed: {cleanup_error}"
        return *pipes.text(), None, reason, "timeout", cleanup_error
    except BaseException:
        if process is not None and pipes is not None:
            cleanup_error = _stop_step_group(process, pipes)
            if cleanup_error:
                raise RuntimeError(f"step cleanup failed: {cleanup_error}")
        raise
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _stop_windows_tree(process: subprocess.Popen[bytes], pipes: _ThreadedPipes) -> str | None:
    errors: list[str] = []
    system_root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
    taskkill = Path(system_root) / "System32" / "taskkill.exe"
    try:
        stopped = subprocess.run(
            [str(taskkill), "/T", "/F", "/PID", str(process.pid)],
            cwd=system_root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False, timeout=WINDOWS_TASKKILL_TIMEOUT_SECONDS,
        )
    except OSError as error:
        errors.append(f"cannot run taskkill ({error}); stopped only the step process")
    except subprocess.TimeoutExpired:
        errors.append("taskkill did not finish; stopped only the step process")
    else:
        if stopped.returncode not in (0, WINDOWS_TASKKILL_NOT_FOUND):
            errors.append(f"taskkill exited {stopped.returncode}; stopped only the step process")
    if errors:
        try:
            process.kill()
        except OSError as error:
            errors.append(f"cannot stop step process: {error}")
    if not pipes.drain(time.monotonic() + STEP_STOP_GRACE_SECONDS):
        # Closing a pipe blocks while its reader thread waits on a descendant that still holds it.
        errors.append("step output did not drain")
    return "; ".join(errors) or None


def _run_windows_step(
    command: list[str], *, repo_root: Path, env: dict[str, str], timeout_seconds: int
) -> tuple[str, str, int | None, str | None, str, str | None]:
    try:
        process = subprocess.Popen(
            command, cwd=repo_root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=WINDOWS_NEW_PROCESS_GROUP,
        )
    except OSError as error:
        return "", "", None, f"cannot execute step: {error}", "launch_error", None
    pipes = _ThreadedPipes(process)
    try:
        deadline = time.monotonic() + timeout_seconds
        if not pipes.drain(deadline):
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        process.wait(timeout=max(0, deadline - time.monotonic()))
        return *pipes.text(), process.returncode, None, "exited", None
    except subprocess.TimeoutExpired:
        cleanup_error = _stop_windows_tree(process, pipes)
        reason = f"timeout after {timeout_seconds} seconds"
        if cleanup_error:
            reason += f"; cleanup failed: {cleanup_error}"
        return *pipes.text(), None, reason, "timeout", cleanup_error
    except BaseException:
        _stop_windows_tree(process, pipes)
        raise


def case_report_command(command: list[str], sidecar: Path) -> list[str] | None:
    """Swap `python -m unittest` for the case-report adapter, or return None for other argv."""
    if command[1:3] != ["-m", "unittest"]:
        return None
    selectors = command[3:]
    if not selectors or any(item.startswith("-") and item != "-v" for item in selectors):
        return None
    return [command[0], str(CASE_REPORT_ADAPTER), "--case-report", str(sidecar), *selectors]


def read_case_report(sidecar: Path) -> dict[str, object]:
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        cases = payload["cases"]
        complete = payload["complete"]
        if not isinstance(cases, list) or not isinstance(complete, bool):
            raise TypeError("cases must be a list and complete a boolean")
        outcomes = [str(case["outcome"]) for case in cases]
    except FileNotFoundError:
        return {"status": "missing"}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "invalid", "error": str(error)}
    tests = [outcome for outcome in outcomes if outcome != "fixture_error"]
    return {
        "status": "complete" if complete else "incomplete",
        "zero_tests": not tests,
        "all_skipped": bool(tests) and all(outcome == "skipped" for outcome in tests),
        "totals": {outcome: outcomes.count(outcome) for outcome in sorted(set(outcomes))},
        "cases": cases,
    }


def run_steps(
    registry: Registry,
    steps: tuple[Step, ...],
    *,
    repo_root: Path,
    require_capabilities: bool,
    required_capabilities: frozenset[str],
    case_report: bool = False,
    progress: Callable[[RunResult], None] | None = None,
) -> RunResult:
    platform = current_platform()
    step_results: list[StepResult] = []
    current = -1

    def record(result: StepResult) -> None:
        step_results.append(result)
        if progress is not None:
            progress(RunResult(platform, tuple(step_results), state="running"))

    try:
        with tempfile.TemporaryDirectory(prefix="dotfiles-tests-") as temp_dir:
            env = isolated_environment(repo_root, Path(temp_dir))
            lookup_path = os.environ.get("PATH", os.defpath)
            capability_cache: dict[str, tuple[str | None, str | None]] = {}
            for index, step in enumerate(steps):
                current = index
                if platform not in step.platforms:
                    reason = f"requires platform {', '.join(step.platforms)}"
                    print(f"SKIP {step.step_id}: {reason}")
                    record(StepResult(step, "skip", reason, None, "skipped_platform"))
                    continue
                missing: list[tuple[str, str]] = []
                for name in step.requires:
                    if name not in capability_cache:
                        capability_cache[name] = capability_reason(
                            registry.capabilities[name],
                            env=env,
                            repo_root=repo_root,
                            lookup_path=lookup_path,
                        )
                    reason, _ = capability_cache[name]
                    if reason:
                        missing.append((name, reason))
                if missing:
                    should_fail = require_capabilities or any(
                        name in required_capabilities for name, _ in missing
                    )
                    status = "FAIL" if should_fail else "SKIP"
                    reason = "; ".join(message for _, message in missing)
                    print(f"{status} {step.step_id}: {reason}")
                    if should_fail:
                        record(StepResult(step, "fail", reason, None, "missing_capability"))
                    else:
                        record(StepResult(step, "skip", reason, None, "skipped_capability"))
                    continue

                command = _expanded_argv(step, repo_root)
                step_env = env
                bd_path = capability_cache["bd"][1] if "bd" in step.requires else None
                if step.step_id == "beads-isolated-worktree" and bd_path:
                    step_env = {**env, "DOTFILES_TEST_BD": bd_path}
                sidecar = Path(temp_dir) / "case-reports" / f"{step.step_id}.json" if case_report else None
                adapted = case_report_command(command, sidecar) if sidecar else None
                if adapted is not None:
                    command = adapted
                print(f"RUN  {step.step_id}")
                started = time.perf_counter()
                launch = _run_windows_step if IS_WINDOWS else _run_posix_step
                stdout, stderr, returncode, failure_reason, kind, cleanup_error = launch(
                    command, repo_root=repo_root, env=step_env,
                    timeout_seconds=step.timeout_seconds,
                )
                duration = round(time.perf_counter() - started, 3)
                signal_number = -returncode if returncode is not None and returncode < 0 else None
                termination = {
                    "kind": "signal" if signal_number else kind,
                    "exit_code": returncode,
                    "signal": signal_number,
                    "cleanup_error": cleanup_error,
                }
                cases = None
                if sidecar:
                    cases = read_case_report(sidecar) if adapted else {"status": "unsupported-argv"}
                if stdout:
                    print(stdout, end="" if stdout.endswith("\n") else "\n")
                if stderr:
                    print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)
                if returncode == 0 and failure_reason is None:
                    print(f"PASS {step.step_id}")
                    record(StepResult(step, "pass", None, 0, "passed", duration, termination, cases))
                else:
                    reason = failure_reason or f"exit {returncode}"
                    outcome = {"launch_error": "launch_error", "timeout": "timeout"}.get(kind, "failed")
                    windows_crash = (IS_WINDOWS and returncode is not None
                                     and returncode >= WINDOWS_NTSTATUS_ERROR)
                    if signal_number or windows_crash:
                        outcome = "crash"
                    print(f"FAIL {step.step_id}: {reason}")
                    record(StepResult(step, "fail", reason, returncode, outcome, duration, termination, cases))
    except BaseException as error:
        if progress is not None:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
            if isinstance(error, SystemExit):
                exit_code = error.code if isinstance(error.code, int) else int(error.code is not None)
            else:
                exit_code = 130 if isinstance(error, KeyboardInterrupt) else 1
            partial = list(step_results)
            remaining = list(steps[len(step_results):])
            if current == len(step_results):
                reason = "interrupted" if interrupted else f"runner crashed: {type(error).__name__}"
                partial.append(StepResult(remaining.pop(0), "fail", reason, None, "interrupted"))
            partial.extend(
                StepResult(step, "skip", "not run: the run stopped early", None, "not_run")
                for step in remaining
            )
            progress(RunResult(
                platform, tuple(partial),
                state="interrupted" if interrupted else "crashed",
                forced_exit_code=exit_code,
            ))
        raise
    totals = {status: sum(result.status == status for result in step_results)
              for status in ("pass", "skip", "fail")}
    print(f"SUMMARY pass={totals['pass']} skip={totals['skip']} fail={totals['fail']}")
    return RunResult(platform, tuple(step_results))


def run_context(repo_root: Path, suite: str, steps: tuple[Step, ...]) -> dict[str, object]:
    def git(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo_root), *args], check=False, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return completed.stdout if completed.returncode == 0 else None

    revision = git("rev-parse", "HEAD")
    status = git("--no-optional-locks", "status", "--porcelain", "--untracked-files=no")
    return {
        "revision": revision.strip() if revision else None,
        "dirty": None if status is None else bool(status.strip()),
        "platform": current_platform(),
        "python": {
            "implementation": python_platform.python_implementation(),
            "version": python_platform.python_version(),
        },
        "suite": suite,
        "selected_steps": [step.step_id for step in steps],
    }


def write_report(
    destination: Path,
    suite: str,
    result: RunResult,
    *,
    context: dict[str, object] | None = None,
) -> None:
    payload = {
        "schema_version": 1,
        "platform": result.platform,
        "selected_suites": [suite],
        "steps": [
            {
                "id": step_result.step.step_id,
                "suites": list(step_result.step.suites),
                "covers": list(step_result.step.covers),
                "status": step_result.status,
                "reason": step_result.reason,
                "exit_code": step_result.exit_code,
            }
            for step_result in result.steps
        ],
        "totals": {
            status: sum(step.status == status for step in result.steps)
            for status in ("pass", "skip", "fail")
        },
        "exit_code": result.exit_code,
    }
    if context is not None:
        payload["schema_version"] = 2
        payload["state"] = result.state
        payload["context"] = context
        if result.state == "running":
            payload["exit_code"] = None
        for entry, step_result in zip(payload["steps"], result.steps):
            entry.update({
                "outcome": step_result.outcome,
                "duration_seconds": step_result.duration_seconds,
                "termination": step_result.termination,
                "case_report": step_result.case_report,
            })
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for delay in (0.05, 0.2, 0.5, None):
            try:
                os.replace(temporary, destination)
                break
            except PermissionError:
                if delay is None or not IS_WINDOWS:
                    raise
                time.sleep(delay)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", nargs="?", default="fast", choices=(*SUITES, "all"))
    parser.add_argument("--list", action="store_true", help="validate and list selected steps")
    parser.add_argument(
        "--require-capabilities",
        action="store_true",
        help="fail when a selected current-platform step lacks a declared capability",
    )
    parser.add_argument(
        "--require-capability",
        action="append",
        default=[],
        metavar="NAME",
        help="fail when a selected step lacks this capability; may be repeated",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        help="write a deterministic JSON suite-registration report",
    )
    parser.add_argument(
        "--case-report",
        action="store_true",
        help="pilot: run unittest steps through the case-report adapter and write report v2",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--registry", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    registry_path = args.registry or repo_root / "configs" / "test-suites.json"
    if not registry_path.is_absolute():
        registry_path = repo_root / registry_path
    try:
        registry = load_registry(repo_root, registry_path)
        unknown_required = set(args.require_capability) - set(registry.capabilities)
        if unknown_required:
            raise ConfigurationError(
                "--require-capability references unknown capability(s): "
                f"{', '.join(sorted(unknown_required))}"
            )
        steps = select_steps(registry, args.suite)
        if not steps:
            raise ConfigurationError(f"suite {args.suite!r} has no registered steps")
        if args.case_report and not args.report_file:
            raise ConfigurationError("--case-report requires --report-file")
    except ConfigurationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.list:
        for step in steps:
            print(f"{step.step_id}\t{','.join(step.suites)}\t{' '.join(_expanded_argv(step, repo_root))}")
        print(f"Selected steps: {len(steps)}")
        return 0
    report_path = args.report_file
    if report_path and not report_path.is_absolute():
        report_path = repo_root / report_path
    context = run_context(repo_root, args.suite, steps) if args.case_report else None
    progress = None
    if context is not None:
        def progress(partial: RunResult) -> None:
            try:
                write_report(report_path, args.suite, partial, context=context)
            except OSError as error:
                print(f"WARNING: cannot update report {report_path}: {error}", file=sys.stderr)

    result = run_steps(
        registry,
        steps,
        repo_root=repo_root,
        require_capabilities=args.require_capabilities,
        required_capabilities=frozenset(args.require_capability),
        case_report=args.case_report,
        progress=progress,
    )
    if report_path:
        try:
            write_report(report_path, args.suite, result, context=context)
        except OSError as error:
            print(f"ERROR: cannot write report {report_path}: {error}", file=sys.stderr)
            return 2
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
