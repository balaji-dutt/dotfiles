"""PowerShell runtime, parser, and Pester helpers for repository tests."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


POWERSHELL_AUDIT_IMAGE = "local/powershell-audit:lts"


def is_wsl(environ: dict[str, str] | os._Environ[str] = os.environ) -> bool:
    return bool(environ.get("WSL_DISTRO_NAME") or environ.get("WSL_INTEROP"))


INTEROP_MODES = ("allow", "forbid", "require")
_PROBED_RUNTIMES: set[str] = set()


def is_interop_executable(executable: str) -> bool:
    return os.name != "nt" and executable.lower().endswith(".exe")


def resolve_powershell_runtime(
    *,
    environ: dict[str, str] | os._Environ[str] = os.environ,
    diagnostics: list[str] | None = None,
    interop: str = "allow",
) -> str | None:
    if interop not in INTEROP_MODES:
        raise ValueError(f"interop must be one of {', '.join(INTEROP_MODES)}: {interop!r}")
    search_path = environ.get("PATH")
    wsl = is_wsl(environ)

    def candidates():
        yield shutil.which("pwsh", path=search_path)
        if wsl or os.name == "nt":
            yield shutil.which("pwsh.exe", path=search_path)
        if wsl:
            standard = Path("/mnt/c/Program Files/PowerShell/7/pwsh.exe")
            if standard.is_file():
                yield str(standard)

    def note(message: str) -> None:
        if diagnostics is not None:
            diagnostics.append(message)

    seen: set[str] = set()
    for candidate in candidates():
        if not candidate:
            continue
        key = os.path.normcase(candidate)
        if key in seen:
            continue
        seen.add(key)
        crosses = is_interop_executable(candidate)
        label = ("Windows PowerShell/WSL interop candidate"
                 if crosses else "PowerShell candidate")
        if interop == "forbid" and crosses:
            note(f"{label} {candidate!r}: skipped, caller passes POSIX paths")
            continue
        if interop == "require" and not crosses:
            note(f"{label} {candidate!r}: skipped, caller needs WSL interop")
            continue
        if key in _PROBED_RUNTIMES:
            return candidate
        try:
            result = subprocess.run(
                [candidate, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "exit 0"],
                env=dict(environ),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except OSError as error:
            reason = f"cannot execute (errno {error.errno}: {error.strerror})"
        except subprocess.TimeoutExpired:
            reason = "probe timed out after 10 seconds"
        else:
            if result.returncode == 0:
                _PROBED_RUNTIMES.add(key)
                return candidate
            reason = f"probe exited {result.returncode}"
        note(f"{label} {candidate!r}: {reason}")
    if not seen:
        note("PowerShell not found: no pwsh or eligible pwsh.exe candidate")
    return None


def powershell_path(value: str | Path, executable: str | None) -> str:
    text = str(value)
    if not executable or not is_interop_executable(executable):
        return text
    try:
        result = subprocess.run(
            ["wslpath", "-w", "-a", text],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"wslpath cannot translate {text!r} for {executable!r}: {error}") from error
    converted = result.stdout.strip()
    if result.returncode != 0 or not converted:
        raise RuntimeError(
            f"wslpath cannot translate {text!r} for {executable!r}: "
            f"exit {result.returncode}: {result.stderr.strip()}"
        )
    return converted


def powershell_quote(value: str | Path, executable: str | None = None) -> str:
    return "'" + powershell_path(value, executable).replace("'", "''") + "'"


def _parser_script(source_label: str) -> str:
    label = powershell_quote(source_label)
    return (
        "$source = [Console]::In.ReadToEnd(); "
        "$tokens = $null; $errors = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseInput($source, {label}, "
        "[ref]$tokens, [ref]$errors); "
        "foreach ($errorItem in $errors) { [Console]::Error.WriteLine("
        "'{0}:{1}:{2}: {3}', $errorItem.Extent.File, "
        "$errorItem.Extent.StartLineNumber, $errorItem.Extent.StartColumnNumber, "
        "$errorItem.Message) }; if ($errors.Count -gt 0) { exit 1 }"
    )


def _container_runtime(environ: dict[str, str] | os._Environ[str]) -> str | None:
    search_path = environ.get("PATH")
    return shutil.which("docker", path=search_path) or shutil.which("podman", path=search_path)


def _ensure_audit_image(runtime: str, repo_root: Path, env: dict[str, str]) -> bool:
    try:
        inspect = subprocess.run(
            [runtime, "image", "inspect", POWERSHELL_AUDIT_IMAGE],
            cwd=repo_root,
            env=env,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False
    if inspect.returncode == 0:
        return True
    try:
        build = subprocess.run(
            [
                runtime,
                "build",
                "--file",
                str(repo_root / "assets" / "Dockerfile.powershell-audit"),
                "--tag",
                POWERSHELL_AUDIT_IMAGE,
                str(repo_root),
            ],
            cwd=repo_root,
            env=env,
            check=False,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False
    return build.returncode == 0


def powershell_parser_command(
    repo_root: Path,
    source_label: str,
    *,
    environ: dict[str, str] | os._Environ[str] = os.environ,
    provision_container: bool = True,
) -> tuple[list[str], dict[str, str]] | None:
    env = dict(environ)
    executable = resolve_powershell_runtime(environ=environ)
    parser = _parser_script(source_label)
    if executable:
        return (
            [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", parser],
            env,
        )
    runtime = _container_runtime(environ)
    if not runtime:
        return None
    if provision_container and not _ensure_audit_image(runtime, repo_root, env):
        return None
    return (
        [
            runtime,
            "run",
            "--rm",
            "--interactive",
            "--network=none",
            "--entrypoint",
            "pwsh",
            POWERSHELL_AUDIT_IMAGE,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            parser,
        ],
        env,
    )


def parse_powershell(
    content: str,
    source_label: str,
    *,
    repo_root: Path,
    environ: dict[str, str] | os._Environ[str] = os.environ,
) -> subprocess.CompletedProcess[str]:
    resolved = powershell_parser_command(repo_root, source_label, environ=environ)
    if resolved is None:
        raise RuntimeError("PowerShell parser is unavailable")
    command, env = resolved
    return subprocess.run(
        command,
        cwd=repo_root,
        env=env,
        input=content,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def pester_version(
    executable: str | None = None,
    *,
    environ: dict[str, str] | os._Environ[str] = os.environ,
) -> tuple[int, ...] | None:
    runtime = executable or resolve_powershell_runtime(environ=environ)
    if not runtime:
        return None
    script = (
        "$module = Get-Module -ListAvailable Pester | Sort-Object Version -Descending | "
        "Select-Object -First 1; if (-not $module) { exit 1 }; "
        "$module.Version.ToString()"
    )
    result = subprocess.run(
        [runtime, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        env=dict(environ),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return tuple(int(part) for part in result.stdout.strip().split(".") if part.isdigit())


def run_pester(
    test_paths: Sequence[Path],
    *,
    repo_root: Path,
    environ: dict[str, str] | os._Environ[str] = os.environ,
) -> subprocess.CompletedProcess[str]:
    executable = resolve_powershell_runtime(environ=environ)
    version = pester_version(executable, environ=environ)
    if not executable or not version or version < (3, 4):
        raise RuntimeError("Pester 3.4 or newer is unavailable")
    paths = ", ".join(
        powershell_quote(path if path.is_absolute() else repo_root / path, executable)
        for path in test_paths
    )
    parameter = "Path" if version[0] >= 5 else "Script"
    script = (
        "$ErrorActionPreference = 'Stop'; Import-Module Pester; "
        f"$result = Invoke-Pester -{parameter} @({paths}) -PassThru; "
        "if ($result.FailedCount -gt 0) { exit 1 }"
    )
    return subprocess.run(
        [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        cwd=repo_root,
        env=dict(environ),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("probe-parser", "probe-pester", "probe-runtime", "run-pester")
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--native", dest="interop", action="store_const", const="forbid")
    mode.add_argument("--interop", dest="interop", action="store_const", const="require")
    parser.set_defaults(interop="allow")
    args = parser.parse_args(argv)
    if args.interop != "allow" and args.action != "probe-runtime":
        parser.error("--native and --interop apply only to probe-runtime")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.action == "probe-parser":
        resolved = powershell_parser_command(
            args.repo_root,
            "probe.ps1",
            provision_container=False,
        )
        return 0 if resolved else 1
    if args.action == "probe-runtime":
        diagnostics: list[str] = []
        executable = resolve_powershell_runtime(diagnostics=diagnostics, interop=args.interop)
        if executable is None:
            sys.stderr.write("".join(f"{line}\n" for line in diagnostics))
            return 1
        print(executable)
        return 0
    if args.action == "run-pester":
        if not args.paths:
            raise SystemExit("run-pester requires at least one test path")
        result = run_pester(args.paths, repo_root=args.repo_root)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    version = pester_version()
    if version and version >= (3, 4):
        print(json.dumps({"version": ".".join(str(part) for part in version)}))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
