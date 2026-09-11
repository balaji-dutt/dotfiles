"""Authoritative provider routing and remotely revalidated main-push evidence."""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import sys
from pathlib import Path
from typing import Any

from github_pipeline import check_workflows
from gitlab_pipeline_runtime import PipelineRuntimeError, remote_ref_sha
from pipeline_evidence import EvidenceStore
from pipeline_policy import (
    LEGACY_POLICY_PATH, PipelineError, git, load_github_policy, policy_kind,
    validate_remote,
)


def legacy_checker():
    name = "_pipeline_guard_legacy"
    if name not in sys.modules:
        path = Path(__file__).with_name("check-gitlab-pipeline.py")
        loader = importlib.machinery.SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_loader(name, loader)
        if spec is None:
            raise PipelineError("cannot load the compatible GitLab history checker")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            loader.exec_module(module)
        except (OSError, ImportError, SyntaxError) as exc:
            sys.modules.pop(name, None)
            raise PipelineError("restore the documented pipeline checker files") from exc
    return sys.modules[name]


def authoritative_root(repo_root: Path) -> Path:
    branch = next((name for name in ("main", "master") if git(
        repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{name}", check=False,
    ).returncode == 0), None)
    if branch is None:
        raise PipelineError("cannot resolve an authoritative main/master branch")
    paths = []
    for record in git(repo_root, "worktree", "list", "--porcelain", "-z").stdout.split("\0\0"):
        fields = record.split("\0")
        if f"branch refs/heads/{branch}" in fields:
            paths.extend(Path(item.removeprefix("worktree ")) for item in fields if item.startswith("worktree "))
    if len(paths) != 1:
        raise PipelineError("guarded pushes require one checked-out authoritative main worktree")
    return paths[0].resolve(strict=True)


def remote_main(repo_root: Path, policy) -> str | None:
    try:
        return remote_ref_sha(repo_root, policy.remote_name, policy.guarded_ref)
    except PipelineRuntimeError as exc:
        raise PipelineError("cannot verify advertised remote main") from exc


def require_history(repo_root: Path, ancestor: str, descendant: str) -> None:
    if git(repo_root, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode:
        raise PipelineError("workflow receipt does not match the pushed landing history")


def receipt_for_push(repo_root: Path, policy, push, checked_sha: str, records: list[dict[str, Any]]):
    parents = git(repo_root, "rev-list", "--parents", "-n", "1", push.local_sha).stdout.split()[1:]
    batch = len(parents) == 2 and parents[0] != push.remote_sha
    reserved_ref = f"refs/heads/ci/{policy.main_branch}/{push.local_sha}"
    associated = [item for item in records if item["landing_sha"] == push.local_sha]
    current = [item for item in associated if item["policy_fingerprint"] == policy.fingerprint]
    exact_main = [item for item in current if item["source_ref"] == reserved_ref and item["sha"] == push.local_sha]
    candidates = exact_main or ([] if batch else [item for item in current if item["sha"] == checked_sha])
    if len(candidates) > 1:
        raise PipelineError("multiple workflow receipts claim this landing; prepare exact main evidence")
    if not candidates:
        if associated and not current:
            raise PipelineError("workflow receipts belong to an older policy; prepare exact main evidence")
        return push.local_sha, reserved_ref, None
    record = candidates[0]
    if any(record[name] != value for name, value in (
        ("host", policy.host), ("repository", policy.repository),
        ("guarded_remote", policy.remote_name), ("guarded_ref", policy.guarded_ref),
    )):
        raise PipelineError("workflow receipt repository or guard identity does not match policy")
    require_history(repo_root, record["before_sha"], push.local_sha)
    require_history(repo_root, record["sha"], push.local_sha)
    if len(parents) == 2 and record["source_ref"] != reserved_ref and record["before_sha"] != parents[0]:
        raise PipelineError("workflow receipt first-parent history does not match the landing")
    return record["sha"], record["source_ref"], record


def check_push(repo_root: Path, remote_name: str, remote_url: str, text: str) -> int:
    root = authoritative_root(repo_root)
    provider = policy_kind(root)
    if provider is None:
        return 0
    legacy = legacy_checker()
    if provider == "gitlab":
        import io
        previous = sys.stdin
        try:
            sys.stdin = io.StringIO(text)
            return legacy.main(["--repo-root", str(root), "--policy", str(root / LEGACY_POLICY_PATH), remote_name, remote_url])
        finally:
            sys.stdin = previous
    policy = load_github_policy(root)
    if remote_name != policy.remote_name:
        return 0
    try:
        pushes = [item for item in legacy.parse_push_records(text) if item.remote_ref == policy.guarded_ref]
        checks = [(item, legacy.validation_sha(root, item)) for item in pushes]
    except legacy.GuardError as exc:
        raise PipelineError(str(exc)) from exc
    checks = [(item, sha) for item, sha in checks if sha is not None]
    if not checks:
        return 0
    if len(checks) != 1:
        raise PipelineError("duplicate guarded-ref push records are ambiguous")
    validate_remote(root, policy, push_url=remote_url)
    push, checked_sha = checks[0]
    expected_remote = None if set(push.remote_sha) == {"0"} else push.remote_sha
    if remote_main(root, policy) != expected_remote:
        raise PipelineError("advertised remote main changed; retry with fresh push facts")
    records = EvidenceStore(root).records()
    remedy = (
        f"Exact main SHA {push.local_sha}: from the authoritative main worktree, run "
        "./assets/agent-wt-merge prepare-main-ci with approval, then retry the original push separately. "
        "Later commits require evidence for the new SHA."
    )
    try:
        sha, source_ref, receipt = receipt_for_push(root, policy, push, checked_sha, records)
        result = check_workflows(root, policy, sha, source_ref, expected_runs=receipt["workflows"] if receipt else None)
        if result["outcome"] != "success":
            raise PipelineError(f"matching GitHub workflow evidence is {result['outcome']}")
        if receipt is not None and (result["repository_id"] != receipt["repository_id"] or
                [(item["workflow_id"], item["path"]) for item in result["workflows"]] !=
                [(item["workflow_id"], item["path"]) for item in receipt["workflows"]]):
            raise PipelineError("remote workflow identity differs from the stored receipt")
    except PipelineError as exc:
        raise PipelineError(f"{exc}. {remedy}") from exc
    if load_github_policy(root).fingerprint != policy.fingerprint or policy_kind(root) != "github":
        raise PipelineError("pipeline policy changed during push validation")
    validate_remote(root, policy, push_url=remote_url)
    if remote_main(root, policy) != expected_remote:
        raise PipelineError("remote main changed during workflow validation")
    if git(root, "rev-parse", "--verify", push.local_ref).stdout.strip() != push.local_sha:
        raise PipelineError("pushed local ref changed during workflow validation")
    print(f"PASS pipeline-guard: GitHub workflows succeeded for {sha} on {source_ref}; no refs published")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("remote_name")
    parser.add_argument("remote_url")
    args = parser.parse_args(argv)
    try:
        text = sys.stdin.read(1024 * 1024 + 1)
        if len(text) > 1024 * 1024:
            raise PipelineError("push records exceed the size limit")
        return check_push(args.repo_root, args.remote_name, args.remote_url, text)
    except (PipelineError, OSError) as exc:
        print(f"ERROR: pipeline guard blocked push: {exc}", file=sys.stderr)
        return 1
