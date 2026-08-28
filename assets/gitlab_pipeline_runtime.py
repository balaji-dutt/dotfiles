#!/usr/bin/env python3
"""Shared policy and exact-SHA GitLab pipeline operations."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PIPELINE_POLICY_RELATIVE_PATH = Path("configs/gitlab-pipeline-guard.json")
PIPELINE_CHECKER_RELATIVE_PATH = Path("assets/check-gitlab-pipeline.py")
PIPELINE_POLICY_SCHEMA = "./schemas/gitlab-pipeline-guard.v1.schema.json"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
REMOTE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class PipelineRuntimeError(Exception):
    """A user-facing policy or pipeline operation failure."""


@dataclass(frozen=True)
class PipelineRuntime:
    policy_path: Path
    checker_path: Path
    remote_name: str
    guarded_ref: str
    required_job: str


def _run_git(
    repo_root: Path,
    args: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def load_pipeline_runtime(source_root: Path, main_branch: str) -> PipelineRuntime:
    """Load and validate the repository's pipeline-guard policy."""
    policy_path = source_root / PIPELINE_POLICY_RELATIVE_PATH
    checker_path = source_root / PIPELINE_CHECKER_RELATIVE_PATH
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineRuntimeError(
            f"cannot read pipeline guard policy {policy_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise PipelineRuntimeError("pipeline guard policy root must be an object")
    if payload.get("$schema") != PIPELINE_POLICY_SCHEMA:
        raise PipelineRuntimeError(
            f"pipeline guard policy $schema must be {PIPELINE_POLICY_SCHEMA!r}"
        )
    if payload.get("schema_version") != 1:
        raise PipelineRuntimeError("pipeline guard policy schema_version must be 1")

    def required_string(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise PipelineRuntimeError(
                f"pipeline guard policy {name} must be a non-empty string"
            )
        return value.strip()

    api_url = required_string("api_url").rstrip("/")
    parsed_url = urllib.parse.urlparse(api_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise PipelineRuntimeError(
            "pipeline guard policy api_url must be an absolute HTTP(S) URL"
        )
    project_id = payload.get("project_id")
    if not isinstance(project_id, int) or isinstance(project_id, bool) or project_id <= 0:
        raise PipelineRuntimeError(
            "pipeline guard policy project_id must be a positive integer"
        )
    timeout_seconds = payload.get("timeout_seconds", 5)
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 30
    ):
        raise PipelineRuntimeError(
            "pipeline guard policy timeout_seconds must be between 1 and 30"
        )
    remote_name = required_string("guarded_remote")
    if not REMOTE_NAME_PATTERN.fullmatch(remote_name):
        raise PipelineRuntimeError(
            "pipeline guard policy guarded_remote must be a local remote name"
        )
    guarded_ref = required_string("guarded_ref")
    expected_ref = f"refs/heads/{main_branch}"
    if guarded_ref != expected_ref:
        raise PipelineRuntimeError(
            f"pipeline guard policy guarded_ref must be {expected_ref!r}"
        )
    if not checker_path.is_file():
        raise PipelineRuntimeError(f"pipeline checker is missing: {checker_path}")
    return PipelineRuntime(
        policy_path=policy_path,
        checker_path=checker_path,
        remote_name=remote_name,
        guarded_ref=guarded_ref,
        required_job=required_string("required_job"),
    )


def remote_ref_sha(repo_root: Path, remote_name: str, remote_ref: str) -> str | None:
    """Return one strictly validated remote advertisement."""
    result = _run_git(
        repo_root,
        ["ls-remote", "--heads", "--", remote_name, remote_ref],
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git ls-remote failed").strip()
        raise PipelineRuntimeError(
            f"could not read {remote_name} {remote_ref}: {detail}"
        )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) != 1:
        raise PipelineRuntimeError(
            f"remote advertised multiple values for {remote_ref}"
        )
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != remote_ref or not SHA_PATTERN.fullmatch(fields[0]):
        raise PipelineRuntimeError(
            f"remote advertised a malformed value for {remote_ref}: {lines[0]!r}"
        )
    return fields[0]


def check_pipeline(
    repo_root: Path,
    runtime: PipelineRuntime,
    sha: str,
) -> dict[str, Any]:
    """Invoke the read-only checker and validate its structured contract."""
    result = subprocess.run(
        [
            sys.executable,
            str(runtime.checker_path),
            "--repo-root",
            str(repo_root),
            "--policy",
            str(runtime.policy_path),
            "--check-sha",
            sha,
            "--json",
        ],
        cwd=str(repo_root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        detail = (result.stderr or result.stdout or "pipeline checker returned no JSON").strip()
        raise PipelineRuntimeError(
            f"pipeline checker failed for {sha}: {detail}"
        ) from exc
    if not isinstance(payload, dict):
        raise PipelineRuntimeError("pipeline checker JSON root is not an object")
    outcome = payload.get("outcome")
    if payload.get("schema_version") != 1 or outcome not in {
        "success",
        "retryable",
        "terminal",
        "error",
        "bypass",
    }:
        raise PipelineRuntimeError("pipeline checker returned an invalid outcome")
    if payload.get("sha") != sha:
        raise PipelineRuntimeError("pipeline checker returned a mismatched SHA")
    if outcome not in {"bypass", "error"} and payload.get("required_job") != runtime.required_job:
        raise PipelineRuntimeError(
            "pipeline checker returned a mismatched required job"
        )
    if outcome in {"success", "bypass"} and result.returncode != 0:
        raise PipelineRuntimeError(
            "pipeline checker returned a passing outcome with a failing exit status"
        )
    if outcome not in {"success", "bypass"} and result.returncode == 0:
        raise PipelineRuntimeError(
            "pipeline checker returned a blocking outcome with a passing exit status"
        )
    return payload


def main_ci_remote_ref(main_branch: str, main_sha: str) -> str:
    """Construct the sole remote namespace used for rewritten-main CI."""
    if BRANCH_PATTERN.fullmatch(main_branch) is None:
        raise PipelineRuntimeError(
            f"main branch name is unsafe for CI publication: {main_branch!r}"
        )
    if SHA_PATTERN.fullmatch(main_sha) is None:
        raise PipelineRuntimeError(
            f"main SHA is invalid for CI publication: {main_sha!r}"
        )
    return f"refs/heads/ci/{main_branch}/{main_sha}"


def publish_main_ci_ref(
    repo_root: Path,
    runtime: PipelineRuntime,
    main_branch: str,
    main_sha: str,
) -> tuple[str, bool]:
    """Publish only the reserved exact-SHA CI ref and verify it."""
    remote_ref = main_ci_remote_ref(main_branch, main_sha)
    advertised_sha = remote_ref_sha(repo_root, runtime.remote_name, remote_ref)
    if advertised_sha is not None:
        if advertised_sha != main_sha:
            raise PipelineRuntimeError(
                f"reserved CI ref {remote_ref} advertises {advertised_sha}, expected "
                f"{main_sha}; refusing to update it"
            )
        return remote_ref, False

    remote_check = _run_git(
        repo_root, ["remote", "get-url", runtime.remote_name], check=False
    )
    if remote_check.returncode != 0:
        detail = (remote_check.stderr or remote_check.stdout or "remote not found").strip()
        raise PipelineRuntimeError(
            f"cannot publish through remote {runtime.remote_name!r}: {detail}"
        )
    result = _run_git(
        repo_root,
        [
            "push",
            "--porcelain",
            "--",
            runtime.remote_name,
            f"{main_sha}:{remote_ref}",
        ],
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "git push failed").strip()
        raise PipelineRuntimeError(
            f"could not publish rewritten main SHA {main_sha} to "
            f"{runtime.remote_name}/{remote_ref.removeprefix('refs/heads/')}: {detail}"
        )
    advertised_sha = remote_ref_sha(repo_root, runtime.remote_name, remote_ref)
    if advertised_sha != main_sha:
        raise PipelineRuntimeError(
            f"published main CI verification failed: {remote_ref} advertises "
            f"{advertised_sha or 'nothing'}, expected {main_sha}"
        )
    return remote_ref, True


def cleanup_main_ci_ref(
    repo_root: Path,
    runtime: PipelineRuntime,
    remote_ref: str,
    main_sha: str,
) -> dict[str, Any]:
    """Lease-delete an unchanged CI ref, retaining surprising evidence."""
    result: dict[str, Any] = {
        "requested": True,
        "cleaned": False,
        "retained": True,
        "remote_ref": remote_ref,
        "expected_sha": main_sha,
    }
    try:
        advertised_sha = remote_ref_sha(repo_root, runtime.remote_name, remote_ref)
    except (PipelineRuntimeError, OSError) as exc:
        result["reason"] = str(exc)
        return result
    if advertised_sha is None:
        result.update({"cleaned": True, "retained": False, "already_absent": True})
        return result
    if advertised_sha != main_sha:
        result["reason"] = (
            f"temporary CI ref moved to {advertised_sha}; exact-lease deletion was not attempted"
        )
        return result
    try:
        deletion = _run_git(
            repo_root,
            [
                "push",
                "--porcelain",
                f"--force-with-lease={remote_ref}:{main_sha}",
                "--",
                runtime.remote_name,
                f":{remote_ref}",
            ],
            check=False,
        )
    except OSError as exc:
        result["reason"] = f"lease-protected CI ref deletion failed: {exc}"
        return result
    if deletion.returncode != 0:
        result["reason"] = (
            deletion.stderr or deletion.stdout or "lease-protected CI ref deletion failed"
        ).strip()
        return result
    try:
        remaining_sha = remote_ref_sha(repo_root, runtime.remote_name, remote_ref)
    except (PipelineRuntimeError, OSError) as exc:
        result["reason"] = f"deletion ran but verification failed: {exc}"
        return result
    if remaining_sha is not None:
        result["reason"] = (
            f"deletion verification found {remote_ref} at {remaining_sha}; "
            "no second deletion attempted"
        )
        return result
    result.update({"cleaned": True, "retained": False})
    return result
