"""GitHub runtime using the shared exact-ref Git publication operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gitlab_pipeline_runtime as shared_git
from github_pipeline import check_workflows
from pipeline_evidence import EvidenceStore
from pipeline_policy import GitHubPolicy, PipelineError, load_github_policy, validate_remote


PipelineRuntimeError = PipelineError


@dataclass(frozen=True)
class PipelineRuntime:
    policy: GitHubPolicy
    provider: str = "github"

    @property
    def remote_name(self) -> str:
        return self.policy.remote_name

    @property
    def guarded_ref(self) -> str:
        return self.policy.guarded_ref

    @property
    def required_job(self) -> str:
        return "GitHub Actions workflows " + ", ".join(map(str, self.policy.workflows))


def load_pipeline_runtime(source_root: Path, main_branch: str) -> PipelineRuntime:
    return PipelineRuntime(load_github_policy(source_root, main_branch))


def _shared(function, *args):
    try:
        return function(*args)
    except shared_git.PipelineRuntimeError as exc:
        raise PipelineError(str(exc)) from exc


def remote_ref_sha(repo_root: Path, remote_name: str, remote_ref: str) -> str | None:
    return _shared(shared_git.remote_ref_sha, repo_root, remote_name, remote_ref)


def main_ci_remote_ref(main_branch: str, main_sha: str) -> str:
    return _shared(shared_git.main_ci_remote_ref, main_branch, main_sha)


def publish_main_ci_ref(repo_root: Path, runtime: PipelineRuntime, main_branch: str, main_sha: str) -> tuple[str, bool]:
    validate_remote(repo_root, runtime.policy)
    return _shared(shared_git.publish_main_ci_ref, repo_root, runtime, main_branch, main_sha)


def cleanup_main_ci_ref(repo_root: Path, runtime: PipelineRuntime, remote_ref: str, main_sha: str) -> dict[str, Any]:
    validate_remote(repo_root, runtime.policy)
    return _shared(shared_git.cleanup_main_ci_ref, repo_root, runtime, remote_ref, main_sha)


def check_pipeline(repo_root: Path, runtime: PipelineRuntime, sha: str, source_ref: str) -> dict[str, Any]:
    return check_workflows(repo_root, runtime.policy, sha, source_ref)


def save_evidence(repo_root: Path, runtime: PipelineRuntime, result: dict[str, Any], **landing) -> str:
    return EvidenceStore(repo_root).save(runtime.policy, result, **landing)
