#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any


WRAPPER_NAMES = {"cc-commit", "cc-commit.exe", "cc-commit.ps1"}
CONTROL_TOKENS = {";", "&&", "||", "|", "&", "(", ")"}
PREFIX_TOKENS = {"command", "env", "builtin", "nohup"}
POLICY_SCHEMA = "./schemas/gitlab-pipeline-guard.v1.schema.json"
REMOTE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REF_PATTERN = re.compile(r"^refs/heads/[A-Za-z0-9._/-]+$")
JOB_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f]")


def command_uses_wrapper(command: str) -> bool:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    expecting_command = True
    for token in tokens:
        if token in CONTROL_TOKENS:
            expecting_command = True
            continue
        if not expecting_command:
            continue
        if token in PREFIX_TOKENS or ("=" in token and not token.startswith("/")):
            continue
        if Path(token).name.casefold() in WRAPPER_NAMES:
            return True
        expecting_command = False
    return False


def git_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("GIT_")
    }


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=git_environment(),
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=2,
    )
    return result.stdout.strip()


def usable_helper(root: Path) -> Path | None:
    for relative in (Path("assets/agent-wt-merge"), Path(".opencode/bin/agent-wt-merge")):
        candidate = root / relative
        if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return candidate.resolve()
    return None


def required_job(root: Path) -> str | None:
    policy = json.loads(
        (root / "configs/gitlab-pipeline-guard.json").read_text(encoding="utf-8")
    )
    if not isinstance(policy, dict):
        return None
    api_url = policy.get("api_url")
    parsed_api = urllib.parse.urlparse(api_url) if isinstance(api_url, str) else None
    project_id = policy.get("project_id")
    guarded_remote = policy.get("guarded_remote")
    guarded_ref = policy.get("guarded_ref")
    job = policy.get("required_job")
    timeout_seconds = policy.get("timeout_seconds")
    if (
        policy.get("$schema") != POLICY_SCHEMA
        or policy.get("schema_version") != 1
        or parsed_api is None
        or parsed_api.scheme not in {"http", "https"}
        or not parsed_api.hostname
        or not isinstance(project_id, int)
        or isinstance(project_id, bool)
        or project_id <= 0
        or not isinstance(guarded_remote, str)
        or REMOTE_PATTERN.fullmatch(guarded_remote) is None
        or not isinstance(guarded_ref, str)
        or REF_PATTERN.fullmatch(guarded_ref) is None
        or not isinstance(job, str)
        or JOB_PATTERN.fullmatch(job) is None
        or not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 30
    ):
        return None
    return job


def inspect_eligible_repo(cwd: Path) -> dict[str, str] | None:
    root = Path(git(cwd, "rev-parse", "--show-toplevel")).resolve()
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if (
        not branch
        or branch in {"main", "master"}
        or BRANCH_PATTERN.fullmatch(branch) is None
        or CONTROL_CHARACTER_PATTERN.search(str(root)) is not None
    ):
        return None
    helper = usable_helper(root)
    if helper is None:
        return None
    job = required_job(root)
    if job is None:
        return None
    sha = git(root, "rev-parse", "HEAD")
    if (
        SHA_PATTERN.fullmatch(sha) is None
        or CONTROL_CHARACTER_PATTERN.search(str(helper)) is not None
    ):
        return None
    return {
        "root": str(root),
        "branch": branch,
        "sha": sha,
        "helper": str(helper),
        "required_job": job,
    }


def state_path(payload: dict[str, Any]) -> Path | None:
    session_id = payload.get("session_id")
    tool_use_id = payload.get("tool_use_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(tool_use_id, str) or not tool_use_id:
        return None
    uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
    directory = Path(tempfile.gettempdir()) / f"claude-feature-ci-reminder-{uid}"
    try:
        if directory.is_symlink():
            return None
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            directory.chmod(0o700)
    except OSError:
        return None
    key = hashlib.sha256(f"{session_id}\0{tool_use_id}".encode()).hexdigest()
    return directory / f"{key}.json"


def write_state(path: Path, state: dict[str, str]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(state, stream, sort_keys=True)
        stream.write("\n")
    if os.name != "nt":
        path.chmod(0o600)


def read_and_remove_state(path: Path) -> dict[str, str] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    value = json.loads(raw)
    if not isinstance(value, dict) or not all(isinstance(item, str) for item in value.values()):
        return None
    return value


def instruction(state: dict[str, str], sha: str) -> str:
    return (
        f"You are the active coding agent. Post-commit CI gate for feature {state['branch']} at {sha}: "
        "commit approval did not authorize network publication. Your next action is to request permission "
        f"to run {shlex.quote(state['helper'])} prepare-ci from {shlex.quote(state['root'])}. Use only that "
        "helper; do not run raw git push, and never push main or tags. If permission is denied or prepare-ci "
        "reports failure or timeout, stop and report the outcome; do not merge. Continue to agent-wt-merge "
        f"inspect and the selected ff/no-ff only after prepare-ci reports exact-SHA {state['required_job']} "
        "success or an "
        "explicit override bypass. Treat bypass as not CI success; the merge helper must retain the remote "
        "feature branch."
    )


def handle_pre(payload: dict[str, Any], path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        command = payload.get("tool_input", {}).get("command", "")
        if not isinstance(command, str) or not command_uses_wrapper(command):
            return
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            return
        state = inspect_eligible_repo(Path(cwd))
        if state is not None:
            write_state(path, state)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return


def handle_post(path: Path) -> None:
    try:
        state = read_and_remove_state(path)
        if state is None:
            return
        current = inspect_eligible_repo(Path(state["root"]))
        if (
            current is None
            or current["root"] != state["root"]
            or current["branch"] != state["branch"]
            or current["helper"] != state["helper"]
            or current["required_job"] != state["required_job"]
            or current["sha"] == state["sha"]
        ):
            return
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": instruction(state, current["sha"]),
            }
        }
        print(json.dumps(output, separators=(",", ":")))
    except (KeyError, OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in {"pre", "post", "failure"}:
        return 0
    try:
        payload = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    path = state_path(payload)
    if path is None:
        return 0
    if argv[0] == "pre":
        handle_pre(payload, path)
    elif argv[0] == "post":
        handle_post(path)
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
