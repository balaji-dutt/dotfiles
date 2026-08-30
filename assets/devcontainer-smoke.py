#!/usr/bin/env python3
"""Run the opt-in disposable devcontainer lifecycle smoke test."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


IMAGE = "homelab-iac:base"
OPT_IN_ENV = "DEVCONTAINER_SMOKE"
RUNTIME_RELATIVE = Path(
    "private_Documents/development/container-dotfiles/devcontainers/"
    "gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer"
)


class SmokeFailure(RuntimeError):
    pass


class CommandRunner:
    def __init__(self, artifacts: Path, timeout: int) -> None:
        artifacts.mkdir(parents=True, exist_ok=True)
        self.artifacts = artifacts
        self.timeout = timeout
        self.sequence = 0

    def run(
        self,
        argv: list[str],
        *,
        check: bool = True,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.sequence += 1
        label = f"{self.sequence:02d}-{Path(argv[0]).name}"
        try:
            result = subprocess.run(
                argv,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout or ""
            stderr = error.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            self._write_log(label, argv, stdout, stderr, "timeout")
            raise SmokeFailure(f"command timed out: {' '.join(argv)}") from error

        self._write_log(label, argv, result.stdout, result.stderr, str(result.returncode))
        if check and result.returncode != 0:
            raise SmokeFailure(
                f"command failed with exit {result.returncode}: {' '.join(argv)}"
            )
        return result

    def _write_log(
        self,
        label: str,
        argv: list[str],
        stdout: str,
        stderr: str,
        outcome: str,
    ) -> None:
        (self.artifacts / f"{label}.json").write_text(
            json.dumps({"argv": argv, "outcome": outcome}, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.artifacts / f"{label}.stdout.log").write_text(stdout, encoding="utf-8")
        (self.artifacts / f"{label}.stderr.log").write_text(stderr, encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--run", action="store_true")
    return parser.parse_args(argv)


def prerequisite_error(environ: dict[str, str] | os._Environ[str]) -> str | None:
    if environ.get(OPT_IN_ENV) != "1":
        return f"set {OPT_IN_ENV}=1 to enable the disposable devcontainer smoke"
    if os.name == "nt":
        return "the disposable devcontainer smoke requires a POSIX host"
    for command in ("docker", "devcontainer"):
        if shutil.which(command, path=environ.get("PATH")) is None:
            return f"required command not found: {command}"
    try:
        daemon = subprocess.run(
            ["docker", "info"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        image = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        cli = subprocess.run(
            ["devcontainer", "--version"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"prerequisite probe failed: {error}"
    if daemon.returncode != 0:
        return "Docker daemon is unavailable"
    if image.returncode != 0:
        return f"preloaded image is unavailable: {IMAGE}"
    if cli.returncode != 0:
        return "devcontainer CLI is unavailable"
    return None


def _write(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def write_fixture(
    root: Path,
    repo_root: Path,
    token: str,
    volume: str,
) -> Path:
    workspace = root / "workspace"
    fixture = root / "fixture"
    runtime = repo_root / RUNTIME_RELATIVE
    workspace.mkdir(parents=True)

    _write(workspace / "README.md", "devcontainer smoke fixture\n")
    _write(
        workspace / ".opencode/opencode.jsonc",
        '{"agent":{"build":{"temperature":0.25}},"ignored":"workspace"}\n',
    )
    _write(
        fixture / "host-dotfiles/private_dot_config/opencode/opencode.jsonc",
        '{"model":"anthropic/claude-sonnet-4-6","agent":{"build":{"mode":"primary"}}}\n',
    )
    _write(
        fixture
        / "host-dotfiles/private_dot_config/opencode/profiles/defaults/opencode.jsonc",
        '{"agent":{"build":{"model":"anthropic/claude-sonnet-4-6"}}}\n',
    )
    _write(
        fixture
        / "host-dotfiles/private_dot_config/opencode/profiles/anthropic-api/opencode.jsonc",
        '{"agent":{"build":{"model":"anthropic/claude-haiku-4-5"}}}\n',
    )
    _write(
        fixture / "host-dotfiles/.config/agent-of-empires/config.toml",
        'theme = "smoke"\n',
    )
    _write(fixture / "host-dotfiles/dot_markdownlint-cli2.jsonc", "{}\n")
    _write(
        fixture / "host-dotfiles/dot_local/share/git-helpers.zsh",
        "typeset -g DEVCONTAINER_SMOKE_GIT_HELPERS=1\n",
    )
    _write(
        fixture / "host-claude/settings-base.json",
        '{"env":{"DEVCONTAINER_SMOKE":"1"}}\n',
    )
    _write(fixture / "host-claude/AGENTS.md", "# Smoke agent instructions\n")
    _write(fixture / "host-claude/no-ai-isms.md", "Smoke fixture.\n")
    _write(
        fixture / "container-configs/opencode.env",
        "OPENCODE_PROFILES=defaults\nDEVCONTAINER_SMOKE_INPUT=fixture\n",
    )
    _write(
        fixture / "container-configs/container_env",
        "export DEVCONTAINER_SMOKE_CONTAINER_ENV=1\n",
    )

    lifecycle = fixture / "smoke/lifecycle.sh"
    _write(
        lifecycle,
        """#!/usr/bin/env bash
set -Eeuo pipefail

mode="${1:?lifecycle mode is required}"
workspace="${2:?workspace path is required}"
sudo install -d -o vscode -g vscode /home/vscode/persistent-data
install -d "$HOME/.local/bin"

case "$mode" in
  post-create)
    source /tmp/host-homelab-devcontainer/postCreate.sh
    post_create_persistence_phase
    post_create_materialization_phase
    post_create_workspace_override_phase "$workspace"
    ;;
  post-start)
    source /tmp/host-homelab-devcontainer/postStart.sh
    post_start_persistence_phase "$workspace"
    post_start_materialization_phase "$workspace"
    ;;
  *)
    echo "ERROR: unsupported lifecycle mode: $mode" >&2
    exit 2
    ;;
esac

counter="/home/vscode/persistent-data/smoke-${mode}.count"
value=0
[[ ! -f "$counter" ]] || value="$(cat "$counter")"
printf '%s\n' "$((value + 1))" >"$counter"
""",
        0o755,
    )

    config = {
        "name": "Dotfiles devcontainer smoke",
        "image": IMAGE,
        "runArgs": [
            "--pull=never",
            "--network=none",
            f"--label=dotfiles.devcontainer-smoke={token}",
        ],
        "containerEnv": {
            "DEVCONTAINER": "1",
            "OPENCODE_PROFILES": "defaults",
        },
        "mounts": [
            f"source={runtime},target=/tmp/host-homelab-devcontainer,type=bind,readonly",
            f"source={fixture / 'host-dotfiles'},target=/home/vscode/.host-dotfiles,type=bind,readonly",
            f"source={fixture / 'host-dotfiles'},target=/tmp/host-dotfiles,type=bind,readonly",
            f"source={fixture / 'host-claude'},target=/tmp/host-claude,type=bind,readonly",
            f"source={fixture / 'container-configs'},target=/tmp/host-container-configs,type=bind,readonly",
            f"source={fixture / 'smoke'},target=/tmp/devcontainer-smoke,type=bind,readonly",
            f"source={volume},target=/home/vscode/persistent-data,type=volume",
        ],
        "postCreateCommand": [
            "bash",
            "/tmp/devcontainer-smoke/lifecycle.sh",
            "post-create",
            "${containerWorkspaceFolder}",
        ],
        "postStartCommand": [
            "bash",
            "/tmp/devcontainer-smoke/lifecycle.sh",
            "post-start",
            "${containerWorkspaceFolder}",
        ],
        "remoteUser": "vscode",
    }
    _write(
        workspace / ".devcontainer/devcontainer.json",
        json.dumps(config, indent=2) + "\n",
    )
    return workspace


def _container_id(output: str) -> str:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("containerId"), str):
            return value["containerId"]
    raise SmokeFailure("devcontainer up did not report a container ID")


def _exec(
    runner: CommandRunner,
    workspace: Path,
    command: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return runner.run(
        [
            "devcontainer",
            "exec",
            "--workspace-folder",
            str(workspace),
            "bash",
            "-lc",
            command,
        ],
        check=check,
    )


def _assert_state(
    runner: CommandRunner,
    workspace: Path,
    create_count: int,
    start_count: int,
    profiles: str,
) -> None:
    checks = [
        (
            "post-create invocation count",
            f'test "$(cat /home/vscode/persistent-data/smoke-post-create.count)" = {create_count}',
        ),
        (
            "post-start invocation count",
            f'test "$(cat /home/vscode/persistent-data/smoke-post-start.count)" = {start_count}',
        ),
        (
            "OpenCode persistence link",
            'test "$(readlink "$HOME/.config/opencode")" = /home/vscode/persistent-data/opencode/config',
        ),
        (
            "Claude persistence link",
            'test "$(readlink "$HOME/.claude")" = /home/vscode/persistent-data/claude/config',
        ),
        (
            "Agent of Empires persistence link",
            'test "$(readlink "$HOME/.config/agent-of-empires")" = /home/vscode/persistent-data/agent-of-empires',
        ),
        ("Claude settings link", 'test -L "$HOME/.claude/settings.json"'),
        ("managed OpenCode config", 'test -f "$HOME/.config/opencode/opencode.jsonc"'),
        (
            "managed OpenCode manifest",
            "test -f /home/vscode/persistent-data/opencode/lifecycle/managed-assets.tsv",
        ),
        (
            "generated OpenCode environment",
            "grep -qx 'DEVCONTAINER_SMOKE_INPUT=fixture' \"$HOME/.config/opencode/opencode.env\"",
        ),
        (
            "preserved OpenCode profiles",
            "source \"$HOME/.config/opencode/opencode.env\" && "
            f"test \"$OPENCODE_PROFILES\" = {shlex.quote(profiles)}",
        ),
        (
            "private OpenCode environment mode",
            'test "$(stat -c %a "$HOME/.config/opencode/opencode.env")" = 600',
        ),
        (
            "workspace runtime config",
            'test -n "$(find "$HOME/.config/opencode/runtime" -type f -name opencode.jsonc -print -quit)"',
        ),
        (
            "managed asset temporary cleanup",
            "test -z \"$(find /home/vscode/persistent-data/opencode/lifecycle -type f "
            "\\( -name 'managed-assets.raw.*' -o -name 'managed-assets.new.*' "
            "-o -name 'managed-assets.previous.*' -o -name 'managed-assets.stale-dirs.*' "
            "-o -name 'managed-assets.source-entries.*' \\) -print -quit)\"",
        ),
        (
            "OpenCode conflict backup cleanup",
            "test ! -e /home/vscode/persistent-data/opencode/unmanaged-managed-path-backups",
        ),
        (
            "Claude conflict backup cleanup",
            "test ! -e /home/vscode/persistent-data/claude/unmanaged-managed-path-backups",
        ),
    ]
    for label, command in checks:
        result = _exec(runner, workspace, command, check=False)
        if result.returncode != 0:
            raise SmokeFailure(f"state assertion failed: {label}")


def _state_fingerprint(runner: CommandRunner, workspace: Path) -> str:
    command = """set -Eeuo pipefail
find "$HOME/.config/opencode" -type f ! -path '*/runtime/*' -print0 | sort -z | xargs -0 -r sha256sum
find "$HOME/.config/opencode/runtime" -type f -name opencode.jsonc -print0 | sort -z | xargs -0 -r sha256sum
printf '%s\n' "$(readlink "$HOME/.config/opencode")" "$(readlink "$HOME/.claude")" "$(readlink "$HOME/.config/agent-of-empires")"
"""
    return _exec(runner, workspace, command).stdout


def _collect_container_logs(
    runner: CommandRunner,
    container_id: str | None,
    label: str,
) -> None:
    if container_id:
        runner.run(["docker", "inspect", container_id], check=False, timeout=30)
        runner.run(["docker", "logs", container_id], check=False, timeout=30)
    runner.run(
        ["docker", "ps", "-a", "--filter", f"label={label}", "--no-trunc"],
        check=False,
        timeout=30,
    )


def _cleanup(
    runner: CommandRunner,
    container_id: str | None,
    volume: str,
    label: str,
) -> None:
    ids = runner.run(
        ["docker", "ps", "-aq", "--filter", f"label={label}"],
        check=False,
        timeout=30,
    ).stdout.split()
    if container_id:
        ids.append(container_id)
    unique_ids = sorted(set(ids))
    if unique_ids:
        runner.run(["docker", "rm", "-f", *unique_ids], check=False, timeout=30)
    runner.run(["docker", "volume", "rm", "-f", volume], check=False, timeout=30)


def run_smoke(repo_root: Path, environ: dict[str, str] | os._Environ[str]) -> Path:
    timeout_text = environ.get("DEVCONTAINER_SMOKE_TIMEOUT_SECONDS", "180")
    try:
        timeout = int(timeout_text)
    except ValueError as error:
        raise SmokeFailure("DEVCONTAINER_SMOKE_TIMEOUT_SECONDS must be an integer") from error
    if timeout < 30 or timeout > 900:
        raise SmokeFailure("DEVCONTAINER_SMOKE_TIMEOUT_SECONDS must be between 30 and 900")

    token = secrets.token_hex(6)
    volume = f"dotfiles-devcontainer-smoke-{token}"
    label = f"dotfiles.devcontainer-smoke={token}"
    artifacts_root = Path(
        environ.get(
            "DEVCONTAINER_SMOKE_ARTIFACTS",
            str(repo_root / "ci-artifacts/devcontainer-smoke"),
        )
    ).resolve()
    artifacts = artifacts_root / token
    runner = CommandRunner(artifacts, timeout)
    container_id: str | None = None

    with tempfile.TemporaryDirectory(prefix="dotfiles-devcontainer-smoke-") as temp_name:
        temp_root = Path(temp_name)
        workspace = write_fixture(temp_root, repo_root, token, volume)
        try:
            runner.run(
                [
                    "docker",
                    "volume",
                    "create",
                    "--label",
                    label,
                    volume,
                ],
                timeout=30,
            )
            first = runner.run(
                ["devcontainer", "up", "--workspace-folder", str(workspace)]
            )
            container_id = _container_id(first.stdout)
            _assert_state(runner, workspace, 1, 1, "defaults")

            _exec(
                runner,
                workspace,
                "source /tmp/host-homelab-devcontainer/postStart.sh && "
                "write_opencode_profiles_to_env_file "
                "/home/vscode/persistent-data/opencode/config/opencode.env "
                "'defaults anthropic-api'",
            )
            _exec(
                runner,
                workspace,
                "bash /tmp/devcontainer-smoke/lifecycle.sh post-create \"$PWD\"",
            )
            _assert_state(runner, workspace, 2, 1, "defaults anthropic-api")

            runner.run(["docker", "stop", container_id], timeout=30)
            restarted = runner.run(
                ["devcontainer", "up", "--workspace-folder", str(workspace)]
            )
            restarted_id = _container_id(restarted.stdout)
            if restarted_id != container_id:
                raise SmokeFailure("devcontainer restart replaced the disposable container")
            _assert_state(runner, workspace, 2, 2, "defaults anthropic-api")

            before = _state_fingerprint(runner, workspace)
            second_up = runner.run(
                ["devcontainer", "up", "--workspace-folder", str(workspace)]
            )
            second_id = _container_id(second_up.stdout)
            if second_id != container_id:
                raise SmokeFailure("second devcontainer up replaced the disposable container")
            after = _state_fingerprint(runner, workspace)
            if after != before:
                raise SmokeFailure("second devcontainer up changed persisted runtime state")

            _collect_container_logs(runner, container_id, label)
            (artifacts / "result.json").write_text(
                json.dumps(
                    {
                        "container_id": container_id,
                        "image": IMAGE,
                        "outcome": "pass",
                        "volume": volume,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return artifacts
        except Exception:
            shutil.copy2(
                workspace / ".devcontainer/devcontainer.json",
                artifacts / "devcontainer.json",
            )
            shutil.copy2(
                temp_root / "fixture/smoke/lifecycle.sh",
                artifacts / "lifecycle.sh",
            )
            _collect_container_logs(runner, container_id, label)
            raise
        finally:
            _cleanup(runner, container_id, volume, label)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    error = prerequisite_error(os.environ)
    if error:
        print(f"SKIP: {error}", file=sys.stderr)
        return 3
    if args.probe:
        print(f"READY: {IMAGE}")
        return 0

    repo_root = Path(__file__).resolve().parents[1]
    try:
        artifacts = run_smoke(repo_root, os.environ)
    except (OSError, SmokeFailure, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"PASS: disposable devcontainer smoke; artifacts: {artifacts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
