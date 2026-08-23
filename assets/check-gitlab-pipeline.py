#!/usr/bin/env python3
"""Require a successful GitLab job before pushing a guarded Git ref."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn


OVERRIDE_NAME = "pipeline-guard.override"
SCHEMA_REF = "./schemas/gitlab-pipeline-guard.v1.schema.json"
ZERO_SHAS = frozenset({"0" * 40, "0" * 64})
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class GuardError(RuntimeError):
    """A failure that should block the guarded push."""


@dataclass(frozen=True)
class Policy:
    api_url: str
    project_id: int
    guarded_remote: str
    guarded_ref: str
    required_job: str
    timeout_seconds: int


@dataclass(frozen=True)
class PushRecord:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def fail(message: str) -> NoReturn:
    raise GuardError(message)


def run_git(repo_root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith("GIT_")
    }
    result = subprocess.run(
        ["git", "-C", str(repo_root), *arguments],
        check=False,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        fail(f"git {' '.join(arguments)} failed: {detail}")
    return result


def common_git_dir(repo_root: Path) -> Path:
    value = run_git(
        repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir"
    ).stdout.strip()
    if not value:
        fail("Git returned an empty common directory")
    return Path(value)


def override_path(repo_root: Path) -> Path:
    return common_git_dir(repo_root) / OVERRIDE_NAME


def load_policy(path: Path) -> Policy:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read pipeline guard policy {path}: {error}")
    if not isinstance(payload, dict):
        fail("pipeline guard policy root must be an object")
    if payload.get("$schema") != SCHEMA_REF:
        fail(f"pipeline guard policy $schema must be {SCHEMA_REF!r}")
    if payload.get("schema_version") != 1:
        fail("pipeline guard policy schema_version must be 1")

    def required_string(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            fail(f"pipeline guard policy {name} must be a non-empty string")
        return value.strip()

    api_url = required_string("api_url").rstrip("/")
    parsed_url = urllib.parse.urlparse(api_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        fail("pipeline guard policy api_url must be an absolute HTTP(S) URL")
    project_id = payload.get("project_id")
    if not isinstance(project_id, int) or isinstance(project_id, bool) or project_id <= 0:
        fail("pipeline guard policy project_id must be a positive integer")
    timeout = payload.get("timeout_seconds", 5)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 30:
        fail("pipeline guard policy timeout_seconds must be between 1 and 30")
    return Policy(
        api_url=api_url,
        project_id=project_id,
        guarded_remote=required_string("guarded_remote"),
        guarded_ref=required_string("guarded_ref"),
        required_job=required_string("required_job"),
        timeout_seconds=timeout,
    )


def parse_push_records(text: str) -> tuple[PushRecord, ...]:
    records: list[PushRecord] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            fail(f"invalid pre-push record on line {line_number}: expected four fields")
        local_ref, local_sha, remote_ref, remote_sha = parts
        for label, value in (("local SHA", local_sha), ("remote SHA", remote_sha)):
            if not SHA_PATTERN.fullmatch(value):
                fail(f"invalid {label} on pre-push line {line_number}: {value!r}")
        records.append(PushRecord(local_ref, local_sha, remote_ref, remote_sha))
    return tuple(records)


def _require_commit(repo_root: Path, sha: str) -> None:
    run_git(repo_root, "cat-file", "-e", f"{sha}^{{commit}}")


def validation_sha(repo_root: Path, record: PushRecord) -> str | None:
    if record.local_sha in ZERO_SHAS:
        return None
    _require_commit(repo_root, record.local_sha)
    if record.remote_sha in ZERO_SHAS:
        return record.local_sha
    _require_commit(repo_root, record.remote_sha)
    ancestor = run_git(
        repo_root,
        "merge-base",
        "--is-ancestor",
        record.remote_sha,
        record.local_sha,
        check=False,
    )
    if ancestor.returncode != 0:
        if ancestor.returncode == 1:
            fail("guarded push is not a fast-forward from the advertised remote main SHA")
        detail = ancestor.stderr.strip() or f"exit {ancestor.returncode}"
        fail(f"cannot compare guarded push history: {detail}")

    parent_line = run_git(
        repo_root, "rev-list", "--parents", "-n", "1", record.local_sha
    ).stdout.strip()
    fields = parent_line.split()
    if not fields or fields[0] != record.local_sha:
        fail("cannot determine parents for the guarded push tip")
    parents = fields[1:]
    if len(parents) == 1:
        return record.local_sha
    if len(parents) == 2 and parents[0] == record.remote_sha:
        return parents[1]
    if len(parents) > 2:
        fail("octopus merge at the guarded push tip is ambiguous")
    fail(
        "merge history at the guarded push tip is ambiguous; expected a linear tip "
        "or one no-ff merge whose first parent is advertised remote main"
    )


def _request_json(url: str, timeout: int) -> object:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "dotfiles-pipeline-guard/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.HTTPError, urllib.error.URLError) as error:
        fail(f"GitLab API request failed for {url}: {error}")
    if len(raw) > MAX_RESPONSE_BYTES:
        fail(f"GitLab API response exceeded {MAX_RESPONSE_BYTES} bytes")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"GitLab API returned malformed JSON for {url}: {error}")


def require_successful_job(policy: Policy, sha: str) -> str:
    query = urllib.parse.urlencode(
        {"sha": sha, "per_page": 100, "order_by": "id", "sort": "desc"}
    )
    pipelines_url = (
        f"{policy.api_url}/projects/{policy.project_id}/pipelines?{query}"
    )
    pipelines = _request_json(pipelines_url, policy.timeout_seconds)
    if not isinstance(pipelines, list):
        fail("GitLab pipelines response must be an array")
    observations: list[str] = []
    pipeline_urls: list[str] = []
    for pipeline in pipelines:
        if not isinstance(pipeline, dict):
            fail("GitLab pipelines response contains a non-object entry")
        pipeline_id = pipeline.get("id")
        pipeline_sha = pipeline.get("sha")
        if not isinstance(pipeline_id, int) or pipeline_sha != sha:
            fail("GitLab pipelines response contains invalid id or mismatched SHA")
        web_url = pipeline.get("web_url")
        if isinstance(web_url, str) and web_url:
            pipeline_urls.append(web_url)
        jobs_url = (
            f"{policy.api_url}/projects/{policy.project_id}/pipelines/"
            f"{pipeline_id}/jobs?per_page=100"
        )
        jobs = _request_json(jobs_url, policy.timeout_seconds)
        if not isinstance(jobs, list):
            fail("GitLab jobs response must be an array")
        for job in jobs:
            if not isinstance(job, dict):
                fail("GitLab jobs response contains a non-object entry")
            if job.get("name") != policy.required_job:
                continue
            status = job.get("status")
            if not isinstance(status, str) or not status:
                fail(f"GitLab job {policy.required_job!r} has an invalid status")
            observations.append(f"pipeline {pipeline_id}: {status}")
            if status == "success":
                return web_url if isinstance(web_url, str) else ""
    detail = "; ".join(observations) if observations else "job not found"
    url_hint = f" Latest pipeline: {pipeline_urls[0]}" if pipeline_urls else ""
    fail(
        f"required GitLab job {policy.required_job!r} has not succeeded for {sha}: "
        f"{detail}.{url_hint} Retry after the pipeline passes or use the documented override."
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("remote_name")
    parser.add_argument("remote_url")
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(), help=argparse.SUPPRESS
    )
    parser.add_argument("--policy", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo_root = args.repo_root.resolve()
    try:
        bypass = override_path(repo_root)
        if bypass.is_file():
            print(
                f"WARNING: bypassing GitLab pipeline guard because {bypass} exists",
                file=sys.stderr,
            )
            return 0
        policy_path = args.policy or repo_root / "configs" / "gitlab-pipeline-guard.json"
        if not policy_path.is_absolute():
            policy_path = repo_root / policy_path
        policy = load_policy(policy_path)
        if args.remote_name != policy.guarded_remote:
            return 0
        records = parse_push_records(sys.stdin.read())
        validation_shas = {
            sha
            for record in records
            if record.remote_ref == policy.guarded_ref
            for sha in (validation_sha(repo_root, record),)
            if sha is not None
        }
        for sha in sorted(validation_shas):
            pipeline_url = require_successful_job(policy, sha)
            suffix = f" ({pipeline_url})" if pipeline_url else ""
            print(
                f"PASS pipeline-guard: {policy.required_job} succeeded for {sha}{suffix}"
            )
        return 0
    except GuardError as error:
        print(f"ERROR: pipeline guard blocked push: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
