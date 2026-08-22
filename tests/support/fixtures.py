"""Small standard-library fixtures for isolated automation tests."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class IsolatedEnvironment:
    root: Path
    home: Path
    fake_bin: Path
    env: dict[str, str]


def _write_guard_commands(directory: Path) -> None:
    for command in ("bd", "dolt"):
        write_executable(
            directory / command,
            "#!/bin/sh\n"
            f"printf '%s\\n' 'ERROR: blocked real {command} invocation in test fixture' >&2\n"
            "exit 97\n",
        )
        (directory / f"{command}.cmd").write_text(
            f"@echo ERROR: blocked real {command} invocation in test fixture 1>&2\r\n"
            "@exit /b 97\r\n",
            encoding="utf-8",
        )


@contextmanager
def isolated_environment(*, prefix: str = "dotfiles-test-") -> Iterator[IsolatedEnvironment]:
    """Yield a temporary home, fake-bin directory, and isolated child env."""
    with tempfile.TemporaryDirectory(prefix=prefix) as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        fake_bin = root / "bin"
        tmp = root / "tmp"
        for directory in (home, fake_bin, tmp):
            directory.mkdir(parents=True)
        blocked_prefixes = ("BD_", "BEADS_", "DOLT_", "GIT_")
        env = {
            name: value
            for name, value in os.environ.items()
            if not name.upper().startswith(blocked_prefixes)
        }
        env.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_CACHE_HOME": str(home / ".cache"),
                "XDG_DATA_HOME": str(home / ".local" / "share"),
                "TMPDIR": str(tmp),
                "TMP": str(tmp),
                "TEMP": str(tmp),
                "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', os.defpath)}",
                "GIT_CONFIG_GLOBAL": str(root / "empty-gitconfig"),
                "GIT_CONFIG_SYSTEM": str(root / "empty-gitconfig"),
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        (root / "empty-gitconfig").write_text("", encoding="utf-8")
        _write_guard_commands(fake_bin)
        yield IsolatedEnvironment(root, home, fake_bin, env)


def write_executable(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def append_json_line(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def read_json_lines(path: Path) -> list[object]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_fake_command(
    directory: Path,
    name: str,
    *,
    log_path: Path,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> Path:
    """Create a PATH command that logs argv/cwd as one JSON object per call."""
    script = directory / name
    body = (
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"path = pathlib.Path({str(log_path)!r})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "with path.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'argv': sys.argv[1:], 'cwd': str(pathlib.Path.cwd())}, sort_keys=True) + '\\n')\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"raise SystemExit({exit_code})\n"
    )
    write_executable(script, body)
    if os.name == "nt":
        (directory / f"{name}.cmd").write_text(
            f'@"{sys.executable}" "{script}" %*\r\n@exit /b %ERRORLEVEL%\r\n',
            encoding="utf-8",
        )
        return directory / f"{name}.cmd"
    return script


def run_git(
    cwd: Path,
    *args: str,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"git command failed in {cwd}: {args!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def init_git_repository(
    path: Path,
    *,
    bare: bool = False,
    env: dict[str, str] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    arguments = ["init", "--quiet"]
    if bare:
        arguments.append("--bare")
    arguments.append(str(path))
    run_git(path.parent, *arguments, env=env)
    if not bare:
        run_git(path, "config", "user.name", "Test User", env=env)
        run_git(path, "config", "user.email", "test@example.com", env=env)
    return path


@contextmanager
def loopback_listener() -> Iterator[socket.socket]:
    """Yield a TCP listener bound only to loopback on an ephemeral port."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        yield listener
    finally:
        listener.close()
