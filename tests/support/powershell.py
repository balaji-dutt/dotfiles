"""PowerShell runtime, parser, and Pester helpers for repository tests."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


POWERSHELL_AUDIT_IMAGE = "local/powershell-audit:lts"
POWERSHELL_TEST_IMAGE = "local/powershell-tests:lts"
PESTER_TEST_VERSION = "5.7.1"


@dataclass
class FixturePowerShell:
    executable: str | None = None
    engine: str | None = None
    image: str = POWERSHELL_AUDIT_IMAGE
    client_env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    pester: tuple[int, ...] | None = None

    @property
    def label(self) -> str:
        return f"container:{self.image}" if self.engine else f"native:{self.executable}"

    def path(self, root: Path, value: str | Path) -> str:
        root = root.resolve()
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        relative = candidate.resolve().relative_to(root)
        return str(Path('/fixture') / relative) if self.engine else str(candidate)

    def quote(self, root: Path, value: str | Path) -> str:
        return powershell_quote(self.path(root, value))

    def run(
        self, root: Path, *, cwd: Path | None = None, script: Path | None = None,
        command: str | None = None, args: Sequence[str] = (),
        environ: dict[str, str] | None = None, env_keys: Sequence[str] = (),
        path_keys: Sequence[str] = (), timeout: float = 180,
    ) -> subprocess.CompletedProcess[str]:
        if (script is None) == (command is None):
            raise ValueError("provide exactly one of script or command")
        root = root.resolve()
        if self.engine and ',' in str(root):
            raise ValueError('fixture root must not contain a comma')
        workdir = self.path(root, cwd or root)
        invocation = ['-NoLogo', '-NoProfile', '-NonInteractive']
        invocation += ['-File', self.path(root, script), *args] if script else ['-Command', command, *args]
        env = dict(os.environ if environ is None else environ)
        if not self.engine:
            return subprocess.run(
                [self.executable, *invocation], cwd=workdir, env=env,
                capture_output=True, text=True, check=False, timeout=timeout,
            )
        home, temporary = root / 'home', root / 'tmp'
        home.mkdir(exist_ok=True)
        temporary.mkdir(exist_ok=True)
        forwarded = {key: env[key] for key in env_keys if key in env}
        for key in path_keys:
            if forwarded.get(key):
                forwarded[key] = self.path(root, forwarded[key])
        forwarded.setdefault('HOME', self.path(root, home))
        forwarded.setdefault('USERPROFILE', forwarded['HOME'])
        for key in ('TMPDIR', 'TEMP', 'TMP'):
            forwarded.setdefault(key, self.path(root, temporary))
        forwarded['PATH'] = '/fixture/bin:/opt/microsoft/powershell/7:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
        options = ['--mount', f'type=bind,source={root},target=/fixture', '--workdir', workdir]
        for key, value in sorted(forwarded.items()):
            options += ['--env', f'{key}={value}']
        return self._container_run(options, invocation, timeout, root=root)

    def _container_run(self, options, invocation, timeout, *, root=None):
        name = 'dotfiles-pwsh-' + uuid.uuid4().hex
        argv = [self.engine, 'run', '--rm', '--pull=never', '--network=none', '--name', name]
        if hasattr(os, 'getuid'):
            if Path(self.engine).name == 'podman' and os.getuid() != 0:
                argv += ['--userns=keep-id']
            argv += ['--user', f'{os.getuid()}:{os.getgid()}']
        argv += [*options, '--entrypoint', 'pwsh', self.image, *invocation]
        try:
            return subprocess.run(argv, env=self.client_env, capture_output=True,
                                  text=True, check=False, timeout=timeout)
        except BaseException:
            try:
                cleanup = subprocess.run([self.engine, 'rm', '--force', name],
                                         env=self.client_env, capture_output=True,
                                         check=False, timeout=2)
                cleaned = cleanup.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                cleaned = False
            if not cleaned:
                retained = None
                if root is not None:
                    retained = root.with_name(root.name + '-retained-' + name)
                    root.rename(retained)
                raise RuntimeError(f'container cleanup unverified: {name}; fixture retained: {retained}') from None
            raise


def resolve_fixture_powershell(
    *, image: str = POWERSHELL_TEST_IMAGE, container_only: bool = False,
    require_pester: bool = False, environ=os.environ, diagnostics: list[str] | None = None,
) -> FixturePowerShell | None:
    deadline = time.monotonic() + 25
    notes = diagnostics if diagnostics is not None else []
    if not container_only:
        executable = resolve_powershell_runtime(environ=environ, interop='forbid', diagnostics=notes)
        if executable:
            version = None
            if require_pester:
                try:
                    result = subprocess.run(
                        [executable, '-NoProfile', '-Command',
                         '(Get-Module -ListAvailable Pester | Sort-Object Version -Descending | Select-Object -First 1).Version.ToString()'],
                        env=dict(environ), capture_output=True, text=True, check=False, timeout=3,
                    )
                    version = tuple(int(part) for part in result.stdout.strip().split('.')) if result.returncode == 0 else None
                except (ValueError, OSError, subprocess.TimeoutExpired):
                    version = None
            if not require_pester or (version and version >= (3, 4)):
                return FixturePowerShell(executable=executable, client_env=dict(environ), pester=version)
    if os.name != 'nt':
        for engine_name in ('docker', 'podman'):
            engine = shutil.which(engine_name, path=environ.get('PATH'))
            if not engine or time.monotonic() >= deadline:
                continue
            backend = FixturePowerShell(engine=engine, image=image, client_env=dict(environ))
            try:
                inspected = subprocess.run([engine, 'image', 'inspect', image], env=dict(environ),
                                           capture_output=True, check=False, timeout=min(2, max(.1, deadline - time.monotonic())))
                if inspected.returncode:
                    notes.append(f'{engine_name}: retained {image} unavailable (image or daemon); see docs/tooling/test-runner.md for the explicit build command')
                    continue
                probe = 'exit 0'
                if image == POWERSHELL_TEST_IMAGE:
                    probe = "$ErrorActionPreference = 'Stop'; Get-Command git, python3, bash -ErrorAction Stop | Out-Null; Import-Module Pester -RequiredVersion " + PESTER_TEST_VERSION + "; exit 0"
                result = backend._container_run([], ['-NoProfile', '-NonInteractive', '-Command', probe],
                                                min(10, max(.1, deadline - time.monotonic())))
                if result.returncode == 0:
                    backend.pester = (5, 7, 1) if image == POWERSHELL_TEST_IMAGE else None
                    return backend
                notes.append(f'{engine_name}: {image} runtime probe exited {result.returncode}')
            except (OSError, subprocess.TimeoutExpired):
                notes.append(f'{engine_name}: {image} runtime probe unavailable or timed out')
    notes.append(f'fixture PowerShell unavailable; explicitly build {image} as documented in docs/tooling/test-runner.md')
    return None


def require_fixture_powershell(**kwargs) -> FixturePowerShell:
    import unittest
    diagnostics: list[str] = []
    backend = resolve_fixture_powershell(diagnostics=diagnostics, **kwargs)
    if backend is None:
        raise unittest.SkipTest('; '.join(diagnostics))
    print(f'PowerShell backend: {backend.label}', file=sys.stderr)
    return backend


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
    backend: FixturePowerShell | None = None,
) -> subprocess.CompletedProcess[str]:
    if backend is not None:
        if not backend.pester or backend.pester < (3, 4):
            raise RuntimeError('Pester 3.4 or newer is unavailable')
        paths = ', '.join(backend.quote(repo_root, path) for path in test_paths)
        parameter = 'Path' if backend.pester[0] >= 5 else 'Script'
        module = ('Import-Module Pester -RequiredVersion ' + PESTER_TEST_VERSION
                  if backend.engine else 'Import-Module Pester')
        script = (
            f"$ErrorActionPreference = 'Stop'; {module}; "
            f'$result = Invoke-Pester -{parameter} @({paths}) -PassThru; '
            "if (-not $result -or $result.TotalCount -le 0 -or $result.PassedCount -le 0 "
            "-or $result.FailedCount -gt 0 -or $result.FailedContainersCount -gt 0 "
            "-or $result.FailedBlocksCount -gt 0) { exit 1 }; exit 0"
        )
        return backend.run(repo_root, command=script, environ=dict(environ))
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
        "action", choices=("probe-parser", "probe-pester", "probe-runtime", "run-pester",
                           "probe-container", "probe-fixture", "probe-pester-fixture")
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
    if args.action in ('probe-container', 'probe-fixture', 'probe-pester-fixture'):
        diagnostics: list[str] = []
        backend = resolve_fixture_powershell(
            image=POWERSHELL_AUDIT_IMAGE if args.action == 'probe-container' else POWERSHELL_TEST_IMAGE,
            container_only=args.action == 'probe-container',
            require_pester=args.action == 'probe-pester-fixture', diagnostics=diagnostics,
        )
        if backend:
            print(backend.label)
            return 0
        sys.stderr.write('\n'.join(diagnostics) + '\n')
        return 1
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
