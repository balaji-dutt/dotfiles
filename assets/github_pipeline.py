"""Bounded, account-scoped GitHub Actions workflow evidence checks."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline_policy import (
    GitHubPolicy, PipelineError, SHA_PATTERN, WORKFLOW_PATTERN, git,
    positive_int, validate_ref, validate_remote,
)


MAX_RESPONSE = 2 * 1024 * 1024
ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}
CONCLUSIONS = {"success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required", "stale", "startup_failure"}
TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")


def bounded_gh(args: list[str], env: dict[str, str], timeout: int, *, token: bool = False) -> str:
    label = "stored GitHub account lookup" if token else "GitHub API request"
    try:
        process = subprocess.Popen(
            ["gh", *args], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise PipelineError(f"{label} could not start; install gh and check account access") from exc
    chunks: queue.Queue[tuple[int, bytes | None]] = queue.Queue(maxsize=16)
    stopped = threading.Event()

    def read_stream(index: int, stream: Any) -> None:
        try:
            while not stopped.is_set():
                data = stream.read(4096)
                while not stopped.is_set():
                    try:
                        chunks.put((index, data or None), timeout=0.05)
                        break
                    except queue.Full:
                        continue
                if not data:
                    break
        finally:
            stream.close()

    threads = [threading.Thread(target=read_stream, args=(i, stream), daemon=True)
               for i, stream in enumerate((process.stdout, process.stderr))]
    for thread in threads:
        thread.start()
    output = [bytearray(), bytearray()]
    deadline = time.monotonic() + timeout
    ended = 0
    try:
        while ended < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PipelineError(f"{label} timed out")
            try:
                index, data = chunks.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if data is None:
                ended += 1
            else:
                output[index].extend(data)
                if len(output[index]) > (16384 if token else MAX_RESPONSE):
                    raise PipelineError(f"{label} exceeded the response size limit")
        try:
            rc = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise PipelineError(f"{label} timed out") from exc
        if rc:
            raise PipelineError(f"{label} failed (exit {rc}); check configured host, repository, and account access")
        try:
            return output[0].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PipelineError(f"{label} returned invalid text") from exc
    finally:
        stopped.set()
        if process.poll() is None:
            process.kill()
        process.wait()
        for thread in threads:
            thread.join(timeout=1)


class GitHubClient:
    def __init__(self, repo_root: Path, policy: GitHubPolicy):
        self.policy = policy
        self.env = os.environ.copy()
        self.env.pop("GH_DEBUG", None)
        self.env["GH_PROMPT_DISABLED"] = "1"
        self.env["GH_PAGER"] = "cat"
        account_result = git(repo_root, "config", "--local", "--get-all", "pipeline-guard.githubAccount", check=False)
        if account_result.returncode not in {0, 1}:
            raise PipelineError("cannot read clone-local GitHub account selection")
        accounts = account_result.stdout.splitlines()
        if accounts:
            if len(accounts) != 1 or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", accounts[0]) is None:
                raise PipelineError("clone-local GitHub account pin must name exactly one account")
            for key in TOKEN_ENV:
                self.env.pop(key, None)
            value = bounded_gh(
                ["auth", "token", "--hostname", policy.host, "--user", accounts[0]],
                self.env, policy.timeout_seconds, token=True,
            ).strip()
            if not value or any(char.isspace() or ord(char) < 32 for char in value):
                raise PipelineError("selected GitHub account has no usable stored token")
            key = "GH_TOKEN" if policy.host == "github.com" or policy.host.endswith(".ghe.com") else "GH_ENTERPRISE_TOKEN"
            self.env[key] = value

    def api(self, endpoint: str) -> dict[str, Any]:
        raw = bounded_gh(
            ["api", "--hostname", self.policy.host, "--method", "GET", endpoint,
             "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28"],
            self.env, self.policy.timeout_seconds,
        )
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise PipelineError("GitHub API returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise PipelineError("GitHub API returned a non-object response")
        return payload

    def pages(self, endpoint: str, field: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        total: int | None = None
        for page in range(1, self.policy.max_pages + 1):
            payload = self.api(f"{endpoint}{'&' if '?' in endpoint else '?'}per_page=100&page={page}")
            count = payload.get("total_count")
            items = payload.get(field)
            if type(count) is not int or count < 0 or not isinstance(items, list) or len(items) > 100:
                raise PipelineError("GitHub pagination response is malformed")
            if total is not None and total != count:
                raise PipelineError("GitHub evidence changed during pagination; check again")
            total = count
            if total > 100 * self.policy.max_pages:
                raise PipelineError("GitHub evidence exceeds the configured pagination limit")
            if any(not isinstance(row, dict) for row in items):
                raise PipelineError("GitHub pagination contains malformed entries")
            rows.extend(items)
            if len(rows) > total or (not items and len(rows) != total):
                raise PipelineError("GitHub pagination is incomplete or inconsistent")
            if len(rows) == total:
                ids = [positive_int(row.get("id"), 2**63 - 1, "GitHub object ID") for row in rows]
                if len(ids) != len(set(ids)):
                    raise PipelineError("GitHub pagination contains duplicate entries")
                return rows
        raise PipelineError("GitHub evidence exceeds the configured pagination limit")


def repository_id(payload: Any, expected: str) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get("full_name"), str) or payload["full_name"].lower() != expected.lower():
        raise PipelineError("GitHub returned evidence for a different repository")
    return positive_int(payload.get("id"), 2**63 - 1, "repository ID")


def workflow_identity(client: GitHubClient, selector: int | str) -> tuple[int, str]:
    workflow = client.api(f"repos/{client.policy.repository}/actions/workflows/{urllib.parse.quote(str(selector), safe='')}")
    identity = positive_int(workflow.get("id"), 2**63 - 1, "workflow ID")
    path = workflow.get("path")
    if not isinstance(path, str) or not path.startswith(".github/workflows/") or not WORKFLOW_PATTERN.fullmatch(path.removeprefix(".github/workflows/")):
        raise PipelineError("GitHub workflow has an unsupported path")
    if workflow.get("state") != "active" or (type(selector) is int and identity != selector) or (isinstance(selector, str) and path != f".github/workflows/{selector}"):
        raise PipelineError("configured GitHub workflow is disabled or mismatched")
    return identity, path


def state(payload: dict[str, Any]) -> str:
    status, conclusion = payload.get("status"), payload.get("conclusion")
    if not isinstance(status, str) or (conclusion is not None and not isinstance(conclusion, str)):
        raise PipelineError("GitHub returned an invalid workflow or job state")
    if status in ACTIVE and conclusion is None:
        return "retryable"
    if status == "completed" and conclusion in CONCLUSIONS:
        return "success" if conclusion == "success" else "terminal"
    raise PipelineError("GitHub returned an invalid workflow or job state")


def run_identity(run: dict[str, Any], policy: GitHubPolicy, repo_id: int, workflow_id: int, path: str, sha: str, ref: str) -> tuple[int, int]:
    if (
        repository_id(run.get("repository"), policy.repository) != repo_id
        or repository_id(run.get("head_repository"), policy.repository) != repo_id
        or run.get("workflow_id") != workflow_id or type(run.get("workflow_id")) is not int
        or run.get("head_sha") != sha or run.get("head_branch") != ref.removeprefix("refs/heads/")
        or run.get("event") != "push" or run.get("path") != path
    ):
        raise PipelineError("GitHub workflow evidence does not match the published identity")
    state(run)
    return (positive_int(run.get("id"), 2**63 - 1, "run ID"),
            positive_int(run.get("run_attempt"), 2**31 - 1, "run attempt"))


def newest_run(client: GitHubClient, repo_id: int, workflow_id: int, path: str, sha: str, ref: str) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({"event": "push", "branch": ref.removeprefix("refs/heads/"), "head_sha": sha})
    rows = client.pages(f"repos/{client.policy.repository}/actions/workflows/{workflow_id}/runs?{query}", "workflow_runs")
    ranked = []
    for run in rows:
        run_id, _ = run_identity(run, client.policy, repo_id, workflow_id, path, sha, ref)
        try:
            created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            if created.tzinfo is None:
                raise ValueError("missing timezone")
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise PipelineError("GitHub run creation time is invalid") from exc
        ranked.append((created, run_id, run))
    return max(ranked, key=lambda item: item[:2])[2] if ranked else None


def check_workflows(repo_root: Path, policy: GitHubPolicy, sha: str, source_ref: str, *, expected_runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not SHA_PATTERN.fullmatch(sha):
        raise PipelineError("CI evidence requires an exact commit SHA")
    validate_ref(source_ref)
    validate_remote(repo_root, policy)
    client = GitHubClient(repo_root, policy)
    repo_id = repository_id(client.api(f"repos/{policy.repository}"), policy.repository)
    identities = [workflow_identity(client, selector) for selector in policy.workflows]
    if len({identity for identity, _ in identities}) != len(identities):
        raise PipelineError("workflow selectors resolve to duplicate identities")
    if expected_runs is not None:
        expected = {item["workflow_id"]: (item["run_id"], item["run_attempt"]) for item in expected_runs}
        if len(expected) != len(expected_runs) or set(expected) != {identity for identity, _ in identities}:
            raise PipelineError("stored workflow identities do not match the current policy")
    else:
        expected = None
    checks = []
    for workflow_id, path in identities:
        run = newest_run(client, repo_id, workflow_id, path, sha, source_ref)
        if run is None:
            if expected is not None:
                raise PipelineError("recorded GitHub run is no longer available")
            checks.append({"workflow_id": workflow_id, "path": path, "outcome": "retryable", "detail": "no matching push run"})
            continue
        run_id, attempt = run_identity(run, policy, repo_id, workflow_id, path, sha, source_ref)
        if expected is not None and expected[workflow_id] != (run_id, attempt):
            raise PipelineError("recorded GitHub evidence was superseded; prepare fresh evidence")
        prefix = f"repos/{policy.repository}/actions/runs/{run_id}"
        pinned = client.api(f"{prefix}/attempts/{attempt}")
        if run_identity(pinned, policy, repo_id, workflow_id, path, sha, source_ref) != (run_id, attempt):
            raise PipelineError("GitHub run attempt changed during inspection")
        jobs = client.pages(f"{prefix}/attempts/{attempt}/jobs", "jobs")
        diagnostics = []
        for job in jobs:
            if (
                type(job.get("run_id")) is not int or job["run_id"] != run_id
                or type(job.get("run_attempt")) is not int or job["run_attempt"] != attempt
                or job.get("head_sha") != sha or job.get("head_branch") != source_ref.removeprefix("refs/heads/")
                or not isinstance(job.get("name"), str)
            ):
                raise PipelineError("GitHub job belongs to a different run identity")
            state(job)
            diagnostics.append({"id": job["id"], "name": job["name"][:256], "status": job["status"], "conclusion": job["conclusion"]})
        current = client.api(prefix)
        latest = newest_run(client, repo_id, workflow_id, path, sha, source_ref)
        if (
            run_identity(current, policy, repo_id, workflow_id, path, sha, source_ref) != (run_id, attempt)
            or latest is None
            or run_identity(latest, policy, repo_id, workflow_id, path, sha, source_ref) != (run_id, attempt)
            or (current["status"], current["conclusion"]) != (pinned["status"], pinned["conclusion"])
            or (latest["status"], latest["conclusion"]) != (pinned["status"], pinned["conclusion"])
        ):
            raise PipelineError("GitHub evidence changed during inspection; check again")
        checks.append({"workflow_id": workflow_id, "path": path, "run_id": run_id,
                       "run_attempt": attempt, "outcome": state(pinned),
                       "status": pinned["status"], "conclusion": pinned["conclusion"], "jobs": diagnostics})
    if len(checks) > 1:
        for item in checks:
            latest = newest_run(client, repo_id, item["workflow_id"], item["path"], sha, source_ref)
            if "run_id" not in item:
                if latest is not None:
                    raise PipelineError("GitHub evidence changed across required workflows; check again")
            elif (
                latest is None
                or run_identity(latest, policy, repo_id, item["workflow_id"], item["path"], sha, source_ref) != (item["run_id"], item["run_attempt"])
                or (latest["status"], latest["conclusion"]) != (item["status"], item["conclusion"])
            ):
                raise PipelineError("GitHub evidence changed across required workflows; check again")
    outcomes = {item["outcome"] for item in checks}
    outcome = "terminal" if "terminal" in outcomes else "retryable" if "retryable" in outcomes else "success"
    return {"schema_version": 2, "provider": "github", "host": policy.host,
            "repository": policy.repository, "repository_id": repo_id,
            "policy_fingerprint": policy.fingerprint, "sha": sha, "source_ref": source_ref,
            "outcome": outcome, "workflows": checks,
            "detail": f"{outcome}: {len(checks)} required GitHub workflow(s) for {sha} on {source_ref}"}
