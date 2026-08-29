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


def resolve_powershell_runtime(
    *,
    environ: dict[str, str] | os._Environ[str] = os.environ,
) -> str | None:
    search_path = environ.get("PATH")
    local = shutil.which("pwsh", path=search_path)
    if local:
        return local
    if is_wsl(environ) or os.name == "nt":
        native = shutil.which("pwsh.exe", path=search_path)
        if native:
            return native
    if is_wsl(environ):
        standard = Path("/mnt/c/Program Files/PowerShell/7/pwsh.exe")
        if standard.is_file():
            return str(standard)
    return None


def powershell_path(value: str | Path, executable: str | None) -> str:
    text = str(value)
    if os.name != "nt" and executable and executable.lower().endswith(".exe"):
        result = subprocess.run(
            ["wslpath", "-w", "-a", text],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return text


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
    parser.add_argument("action", choices=("probe-parser", "probe-pester", "run-pester"))
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.action == "probe-parser":
        resolved = powershell_parser_command(
            args.repo_root,
            "probe.ps1",
            provision_container=False,
        )
        return 0 if resolved else 1
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
