"""Clone-local workflow receipts with atomic writes and short mutation locks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from pipeline_policy import (
    GitHubPolicy, HOST_PATTERN, PipelineError, REPOSITORY_PATTERN, SHA_PATTERN,
    WORKFLOW_PATTERN, git, positive_int, validate_ref, validate_remote, load_github_policy, policy_kind,
)


HEX = re.compile(r"^[0-9a-f]{64}$")
MAX_RECORD = 65536


def record_key(record: dict[str, Any]) -> str:
    identity = [record[name] for name in ("policy_fingerprint", "source_ref", "sha")]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def validate_record(record: Any) -> dict[str, Any]:
    fields = {"schema_version", "host", "repository", "repository_id", "policy_fingerprint",
              "guarded_remote", "guarded_ref", "source_ref", "sha", "workflows", "landing_sha", "before_sha"}
    if not isinstance(record, dict) or set(record) != fields or type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise PipelineError("evidence record schema is invalid")
    for name, pattern in (("host", HOST_PATTERN), ("repository", REPOSITORY_PATTERN), ("policy_fingerprint", HEX), ("sha", SHA_PATTERN), ("guarded_remote", re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$"))):
        if not isinstance(record[name], str) or not pattern.fullmatch(record[name]):
            raise PipelineError("evidence record identity is invalid")
    for name in ("guarded_ref", "source_ref"):
        validate_ref(record[name])
    for name in ("before_sha", "landing_sha"):
        if record[name] is not None and (not isinstance(record[name], str) or not SHA_PATTERN.fullmatch(record[name])):
            raise PipelineError("evidence landing identity is invalid")
    if (record["landing_sha"] is None) != (record["before_sha"] is None):
        raise PipelineError("evidence landing identity is incomplete")
    positive_int(record["repository_id"], 2**63 - 1, "record repository ID")
    workflows = record["workflows"]
    if not isinstance(workflows, list) or not 1 <= len(workflows) <= 8:
        raise PipelineError("evidence workflow identities are invalid")
    seen = set()
    for item in workflows:
        if not isinstance(item, dict) or set(item) != {"workflow_id", "path", "run_id", "run_attempt"}:
            raise PipelineError("evidence workflow identity is invalid")
        for name in ("workflow_id", "run_id", "run_attempt"):
            positive_int(item[name], 2**63 - 1, f"record {name}")
        if not isinstance(item["path"], str) or not item["path"].startswith(".github/workflows/") or not WORKFLOW_PATTERN.fullmatch(item["path"].removeprefix(".github/workflows/")):
            raise PipelineError("evidence workflow path is invalid")
        if item["workflow_id"] in seen:
            raise PipelineError("evidence workflow identities are duplicated")
        seen.add(item["workflow_id"])
    return record


class EvidenceStore:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        common = git(repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        if not common or not Path(common).is_absolute():
            raise PipelineError("cannot resolve the clone's common Git directory")
        self.common = Path(common).resolve(strict=True)
        self.path = self.common / "agent-wt-merge" / "evidence" / "v1"

    @staticmethod
    def _owned(path: Path, *, directory: bool) -> os.stat_result:
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
            raise PipelineError("evidence path is not an owned regular file or directory")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PipelineError("evidence path belongs to a different user")
        if os.name != "nt" and info.st_mode & 0o077:
            raise PipelineError("evidence path permissions must be private to its owner")
        if not directory and info.st_nlink != 1:
            raise PipelineError("evidence files must not have multiple hard links")
        return info

    def ensure(self, *, create: bool) -> bool:
        current = self.common
        for part in ("agent-wt-merge", "evidence", "v1"):
            current /= part
            try:
                if create:
                    try:
                        current.mkdir(mode=0o700)
                    except FileExistsError:
                        pass
                self._owned(current, directory=True)
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise PipelineError("cannot inspect or create the private evidence store") from exc
        return True

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.ensure(create=True)
        lock = self.path / "metadata.lock"
        token = uuid.uuid4().hex.encode()
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise PipelineError("evidence metadata is locked; do not remove a concurrent or unresolved lock") from exc
        except OSError as exc:
            raise PipelineError("cannot lock the evidence store") from exc
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(token)
                stream.flush()
                os.fsync(stream.fileno())
            yield
        finally:
            try:
                self._owned(lock, directory=False)
                if lock.read_bytes() != token:
                    raise PipelineError("evidence lock ownership changed; lock retained")
                lock.unlink()
            except OSError as exc:
                raise PipelineError("evidence metadata lock could not be released") from exc

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            info = self._owned(path, directory=False)
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise PipelineError("evidence record changed during inspection")
                raw = stream.read(MAX_RECORD + 1)
            if len(raw) > MAX_RECORD:
                raise PipelineError("evidence record exceeds the size limit")
            record = validate_record(json.loads(raw))
        except (OSError, ValueError) as exc:
            raise PipelineError("evidence record is unreadable or corrupt; retain it for review") from exc
        if path.name != record_key(record) + ".json":
            raise PipelineError("evidence filename does not match its identity")
        return record

    def records(self) -> list[dict[str, Any]]:
        if not self.ensure(create=False):
            return []
        return [self._read(path) for path in sorted(self.path.glob("*.json"))]

    def _atomic(self, name: str, payload: dict[str, Any]) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".write-", dir=self.path, delete=False) as stream:
                temporary = Path(stream.name)
                os.chmod(temporary, 0o600)
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path / name)
            if os.name != "nt":
                fd = os.open(self.path, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError as exc:
            raise PipelineError("evidence could not be persisted atomically") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def start_operation(self) -> str:
        token = uuid.uuid4().hex
        with self.lock():
            self.records()
            self._atomic(f".active-{token}", {"pid": os.getpid(), "token": token})
        return token

    def finish_operation(self, token: str) -> None:
        with self.lock():
            self.resume_operation(token)
            try:
                (self.path / f".active-{token}").unlink()
            except OSError as exc:
                raise PipelineError("active evidence operation could not be released") from exc

    def resume_operation(self, token: str) -> str:
        if re.fullmatch(r"[0-9a-f]{32}", token) is None:
            raise PipelineError("invalid evidence operation token")
        self.ensure(create=False)
        path = self.path / f".active-{token}"
        try:
            self._owned(path, directory=False)
            if json.loads(path.read_text()) != {"pid": os.getpid(), "token": token}:
                raise PipelineError("active evidence operation ownership changed")
        except (OSError, ValueError) as exc:
            raise PipelineError("active evidence operation could not be resumed") from exc
        return token

    def save(self, policy: GitHubPolicy, result: dict[str, Any], *, before_sha: str | None = None, landing_sha: str | None = None) -> str:
        if result.get("outcome") != "success" or result.get("provider") != "github" or result.get("policy_fingerprint") != policy.fingerprint:
            raise PipelineError("only successful current-policy workflow references may be recorded")
        record = {name: result[name] for name in ("host", "repository", "repository_id", "policy_fingerprint", "sha", "source_ref")}
        record.update({"schema_version": 1, "guarded_remote": policy.remote_name,
                       "guarded_ref": policy.guarded_ref, "before_sha": before_sha, "landing_sha": landing_sha,
                       "workflows": [{name: item[name] for name in ("workflow_id", "path", "run_id", "run_attempt")} for item in result["workflows"]]})
        validate_record(record)
        if landing_sha is not None:
            for ancestor in (record["sha"], before_sha):
                if git(self.repo_root, "merge-base", "--is-ancestor", ancestor, landing_sha, check=False).returncode:
                    raise PipelineError("recorded landing does not contain the checked history")
        key = record_key(record)
        with self.lock():
            previous = next((item for item in self.records() if record_key(item) == key), None)
            if previous is not None and previous["landing_sha"] is not None:
                if landing_sha is not None and (previous["landing_sha"], previous["before_sha"]) != (landing_sha, before_sha):
                    raise PipelineError("workflow identity already belongs to a different landing; retain both histories for review")
                if landing_sha is None:
                    record["landing_sha"], record["before_sha"] = previous["landing_sha"], previous["before_sha"]
            self._atomic(key + ".json", record)
        return str(self.path / (key + ".json"))

    def prune(self, policy: GitHubPolicy, *, apply: bool = False) -> dict[str, Any]:
        from pipeline_guard import remote_main

        records = self.records()
        snapshot = {record_key(item): item for item in records}
        report = {"evidence_store": str(self.path), "apply": apply, "remote_verified": False,
                  "records": [], "error": None}
        if not records:
            return report
        reasons = {}
        try:
            if policy_kind(policy.policy_path.parent.parent) != "github" or load_github_policy(policy.policy_path.parent.parent).fingerprint != policy.fingerprint:
                raise PipelineError("current policy does not match the pruning contract")
            validate_remote(self.repo_root, policy)
            git(self.repo_root, "fetch", "--no-tags", "--", policy.remote_name, policy.guarded_ref)
            fetched = git(self.repo_root, "rev-parse", "--verify", "FETCH_HEAD^{commit}").stdout.strip()
            advertised = remote_main(self.repo_root, policy)
            if advertised != fetched:
                raise PipelineError("remote main changed during pruning inspection")
            local_main = git(self.repo_root, "rev-parse", "--verify", policy.guarded_ref).stdout.strip()
            active = bool(list(self.path.glob(".active-*")))
            for key, item in snapshot.items():
                reason = None
                if active:
                    reason = "active or unresolved helper operation"
                elif item["policy_fingerprint"] != policy.fingerprint or item["host"] != policy.host or item["repository"] != policy.repository or item["guarded_remote"] != policy.remote_name or item["guarded_ref"] != policy.guarded_ref:
                    reason = "record belongs to a different policy or repository"
                elif item["landing_sha"] is None:
                    reason = "no associated landing; retained for explicit review"
                elif any(git(self.repo_root, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode for ancestor, descendant in (
                    (item["before_sha"], item["landing_sha"]), (item["sha"], item["landing_sha"]),
                    (item["landing_sha"], fetched),
                )):
                    reason = "landing is unpushed, unavailable, or its history cannot be verified"
                reasons[key] = reason
            report["remote_verified"] = True
            if apply and any(reason is None for reason in reasons.values()):
                if remote_main(self.repo_root, policy) != fetched:
                    raise PipelineError("remote main changed before pruning")
                with self.lock():
                    if self.records() != records or list(self.path.glob(".active-*")):
                        raise PipelineError("evidence or active operations changed; no records pruned")
                    if git(self.repo_root, "rev-parse", "--verify", policy.guarded_ref).stdout.strip() != local_main:
                        raise PipelineError("local main changed; no records pruned")
                    if load_github_policy(policy.policy_path.parent.parent).fingerprint != policy.fingerprint or policy_kind(policy.policy_path.parent.parent) != "github":
                        raise PipelineError("policy changed; no records pruned")
                    for key, reason in reasons.items():
                        if reason is None:
                            self._read(self.path / (key + ".json"))
                            (self.path / (key + ".json")).unlink()
                            report["records"].append({"key": key, "action": "pruned", "reason": "landing is contained in freshly verified remote main"})
        except (PipelineError, OSError) as exc:
            report["error"] = str(exc)
            reasons = {key: "verification failed; retained for review" for key in snapshot}
        pruned = {item["key"] for item in report["records"]}
        report["records"].extend({"key": key, "action": "eligible" if reason is None else "retained",
                                  "reason": reason or "landing is contained in freshly verified remote main"}
                                 for key, reason in reasons.items() if key not in pruned)
        return report
