#!/usr/bin/env python3
"""Run the repository's registered test suites in an isolated environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from string import Formatter


SUITES = ("fast", "integration", "render", "provenance", "platform")
PLATFORMS = ("linux", "macos", "windows", "wsl2")
STEP_PLACEHOLDERS = {"python", "repo"}
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?$")
PASSTHROUGH_ENV = {
    "COMSPEC",
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
    command: str
    probe: tuple[str, ...]


@dataclass(frozen=True)
class Step:
    step_id: str
    suites: tuple[str, ...]
    argv: tuple[str, ...]
    covers: tuple[str, ...]
    platforms: tuple[str, ...]
    requires: tuple[str, ...]


@dataclass(frozen=True)
class Registry:
    capabilities: dict[str, Capability]
    steps: tuple[Step, ...]


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
        if not isinstance(command, str) or not command:
            raise ConfigurationError(f"capabilities.{name}.command must be a non-empty string")
        probe = _string_list(
            raw.get("probe", []),
            f"capabilities.{name}.probe",
            allow_empty=True,
            allow_duplicates=True,
        )
        capabilities[name] = Capability(name, command, probe)

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
        steps.append(Step(step_id, suites, argv, covers, platforms, requires))

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
        }
    )
    return env


def capability_reason(
    capability: Capability,
    *,
    env: dict[str, str],
    repo_root: Path,
) -> str | None:
    executable = shutil.which(capability.command, path=env.get("PATH"))
    if executable is None:
        return f"missing capability {capability.name} ({capability.command})"
    if not capability.probe:
        return None
    probe = [executable if index == 0 else value for index, value in enumerate(capability.probe)]
    result = subprocess.run(
        probe,
        cwd=repo_root,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return f"capability probe failed for {capability.name} (exit {result.returncode})"
    return None


def _expanded_argv(step: Step, repo_root: Path) -> list[str]:
    replacements = {"python": sys.executable, "repo": str(repo_root)}
    return [item.format(**replacements) for item in step.argv]


def run_steps(
    registry: Registry,
    steps: tuple[Step, ...],
    *,
    repo_root: Path,
    require_capabilities: bool,
) -> int:
    passed = skipped = failed = 0
    platform = current_platform()
    with tempfile.TemporaryDirectory(prefix="dotfiles-tests-") as temp_dir:
        env = isolated_environment(repo_root, Path(temp_dir))
        capability_cache: dict[str, str | None] = {}
        for step in steps:
            if platform not in step.platforms:
                print(f"SKIP {step.step_id}: requires platform {', '.join(step.platforms)}")
                skipped += 1
                continue
            missing: list[str] = []
            for name in step.requires:
                if name not in capability_cache:
                    capability_cache[name] = capability_reason(
                        registry.capabilities[name], env=env, repo_root=repo_root
                    )
                if capability_cache[name]:
                    missing.append(capability_cache[name])
            if missing:
                status = "FAIL" if require_capabilities else "SKIP"
                print(f"{status} {step.step_id}: {'; '.join(missing)}")
                if require_capabilities:
                    failed += 1
                else:
                    skipped += 1
                continue

            command = _expanded_argv(step, repo_root)
            print(f"RUN  {step.step_id}")
            result = subprocess.run(
                command,
                cwd=repo_root,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.stdout:
                print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
            if result.stderr:
                print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
            if result.returncode == 0:
                print(f"PASS {step.step_id}")
                passed += 1
            else:
                print(f"FAIL {step.step_id}: exit {result.returncode}")
                failed += 1
    print(f"SUMMARY pass={passed} skip={skipped} fail={failed}")
    return 1 if failed else 0


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
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--registry", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    registry_path = args.registry or repo_root / "configs" / "test-suites.json"
    if not registry_path.is_absolute():
        registry_path = repo_root / registry_path
    try:
        registry = load_registry(repo_root, registry_path)
        steps = select_steps(registry, args.suite)
        if not steps:
            raise ConfigurationError(f"suite {args.suite!r} has no registered steps")
    except ConfigurationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.list:
        for step in steps:
            print(f"{step.step_id}\t{','.join(step.suites)}\t{' '.join(_expanded_argv(step, repo_root))}")
        print(f"Selected steps: {len(steps)}")
        return 0
    return run_steps(
        registry,
        steps,
        repo_root=repo_root,
        require_capabilities=args.require_capabilities,
    )


if __name__ == "__main__":
    raise SystemExit(main())
