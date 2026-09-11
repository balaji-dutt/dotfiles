"""Provider selection and the GitHub workflow policy contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


POLICY_PATH = Path("configs/pipeline-guard.json")
LEGACY_POLICY_PATH = Path("configs/gitlab-pipeline-guard.json")
POLICY_SCHEMA = "./schemas/pipeline-guard.v1.schema.json"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")
WORKFLOW_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.ya?ml$")


class PipelineError(Exception):
    """A fail-closed policy, identity, or evidence error."""


def git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith("GIT_") and key not in {
            "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
        }:
            env.pop(key)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PipelineError("Git policy inspection could not complete") from exc
    if check and result.returncode:
        raise PipelineError("Git policy inspection failed; check the repository and remote")
    return result


def policy_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        if path.parent.is_symlink() and not path.parent.exists():
            raise PipelineError("pipeline policy directory is a broken symlink")
        return False
    except OSError as exc:
        raise PipelineError("pipeline policy path cannot be inspected") from exc
    return True


def policy_kind(source_root: Path) -> str | None:
    generic = policy_present(source_root / POLICY_PATH)
    legacy = policy_present(source_root / LEGACY_POLICY_PATH)
    if generic and legacy:
        raise PipelineError("conflicting pipeline policies; configure exactly one policy")
    return "github" if generic else "gitlab" if legacy else None


@dataclass(frozen=True)
class GitHubPolicy:
    policy_path: Path
    host: str
    repository: str
    remote_name: str
    guarded_ref: str
    workflows: tuple[int | str, ...]
    timeout_seconds: int
    max_pages: int
    fingerprint: str

    @property
    def main_branch(self) -> str:
        return self.guarded_ref.removeprefix("refs/heads/")


def positive_int(value: Any, maximum: int, field: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise PipelineError(f"{field} must be an integer between 1 and {maximum}")
    return value


def validate_ref(ref: str) -> str:
    if (
        not isinstance(ref, str) or not ref.startswith("refs/heads/")
        or not ref.removeprefix("refs/heads/") or len(ref) > 1024
        or any(ord(char) < 33 or ord(char) == 127 for char in ref)
        or any(char in ref for char in "~^:?*[\\")
        or any(part in ref for part in ("..", "@{", "//"))
        or any(part.startswith(".") or part.endswith(".lock") for part in ref.split("/"))
        or ref.endswith(("/", "."))
    ):
        raise PipelineError("expected a safe full refs/heads/ branch ref")
    return ref


def load_github_policy(source_root: Path, main_branch: str | None = None) -> GitHubPolicy:
    if policy_kind(source_root) != "github":
        raise PipelineError("GitHub workflow policy is not configured")
    policy_path = source_root / POLICY_PATH
    try:
        with policy_path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise PipelineError("pipeline policy exceeds 64 KiB")
        payload = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise PipelineError("cannot read the configured pipeline policy") from exc
    fields = {
        "$schema", "schema_version", "provider", "host", "repository",
        "guarded_remote", "guarded_ref", "workflows", "timeout_seconds", "max_pages",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise PipelineError("pipeline policy has missing or unsupported fields")
    if (
        payload["$schema"] != POLICY_SCHEMA or type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1 or payload["provider"] != "github"
    ):
        raise PipelineError("unsupported pipeline policy schema or provider")
    for name, pattern in (
        ("host", HOST_PATTERN), ("repository", REPOSITORY_PATTERN),
        ("guarded_remote", re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")),
    ):
        if not isinstance(payload[name], str) or not pattern.fullmatch(payload[name]):
            raise PipelineError(f"pipeline policy {name} is invalid")
    if any(not part or part.startswith("-") or part.endswith("-") for part in payload["host"].split(".")):
        raise PipelineError("pipeline policy host is invalid")
    ref = validate_ref(payload["guarded_ref"])
    if main_branch is not None and ref != f"refs/heads/{main_branch}":
        raise PipelineError("pipeline policy guarded_ref does not match local main")
    workflows = payload["workflows"]
    if not isinstance(workflows, list) or not 1 <= len(workflows) <= 8:
        raise PipelineError("workflows must select between one and eight workflow IDs or filenames")
    for workflow in workflows:
        if type(workflow) is int:
            positive_int(workflow, 2**63 - 1, "workflow ID")
        elif not isinstance(workflow, str) or not WORKFLOW_PATTERN.fullmatch(workflow):
            raise PipelineError("workflow selectors must be numeric IDs or YAML filenames")
    if len(set(workflows)) != len(workflows):
        raise PipelineError("workflow selectors must be unique")
    return GitHubPolicy(
        policy_path, payload["host"].lower(), payload["repository"],
        payload["guarded_remote"], ref, tuple(workflows),
        positive_int(payload["timeout_seconds"], 30, "timeout_seconds"),
        positive_int(payload["max_pages"], 20, "max_pages"),
        hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    )


def remote_identity(url: str) -> tuple[str, str]:
    if "://" not in url:
        match = re.fullmatch(r"git@([^/:]+):([^?#\s]+)", url)
        if match is None:
            raise PipelineError("remote must use an unambiguous GitHub SSH or HTTPS URL")
        host, repository = match.groups()
    else:
        try:
            parsed = urllib.parse.urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise PipelineError("remote URL is malformed") from exc
        if (
            parsed.scheme not in {"https", "ssh"} or not parsed.hostname
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.username not in ({None} if parsed.scheme == "https" else {"git"})
            or port not in ({None, 443} if parsed.scheme == "https" else {None, 22})
        ):
            raise PipelineError("remote must use an unambiguous GitHub SSH or HTTPS URL")
        host, repository = parsed.hostname, parsed.path.removeprefix("/")
    repository = repository.removesuffix(".git")
    if not HOST_PATTERN.fullmatch(host) or not REPOSITORY_PATTERN.fullmatch(repository):
        raise PipelineError("remote host or repository is invalid")
    return host.lower(), repository.lower()


def validate_remote(repo_root: Path, policy: GitHubPolicy, push_url: str | None = None) -> None:
    expected = policy.host, policy.repository.lower()
    for mode in ([], ["--push"]):
        urls = git(repo_root, "remote", "get-url", *mode, "--all", policy.remote_name).stdout.splitlines()
        if len(urls) != 1 or remote_identity(urls[0]) != expected:
            raise PipelineError("Git remote and configured GitHub repository do not agree")
    if push_url is not None and remote_identity(push_url) != expected:
        raise PipelineError("push destination and configured GitHub repository do not agree")
