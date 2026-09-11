from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.support.fixtures import isolated_environment, run_git, write_json
from tests.support.github_fixtures import REPO_ROOT, policy_data

sys.path.insert(0, str(REPO_ROOT / "assets"))
try:
    from pipeline_evidence import EvidenceStore, record_key
    from pipeline_policy import PipelineError, load_github_policy
finally:
    sys.path.pop(0)


class PipelineEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = self.enterContext(isolated_environment(prefix="pipeline evidence "))
        self.enterContext(mock.patch.dict(os.environ, self.fixture.env, clear=True))
        self.repo = self.fixture.root / "main"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.com")
        write_json(self.repo / "configs/pipeline-guard.json", policy_data())
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.before = self.git("rev-parse", "HEAD")
        (self.repo / "feature").write_text("feature\n")
        self.git("add", ".")
        self.git("commit", "-m", "feature")
        self.sha = self.git("rev-parse", "HEAD")
        self.policy = load_github_policy(self.repo)
        self.store = EvidenceStore(self.repo)
        self.result = {"outcome": "success", "provider": "github", "host": "github.com", "repository": "sample/project", "repository_id": 29, "policy_fingerprint": self.policy.fingerprint, "sha": self.sha, "source_ref": "refs/heads/topic", "workflows": [{"workflow_id": 17, "path": ".github/workflows/build.yml", "run_id": 41, "run_attempt": 1}]}

    def git(self, *args):
        return run_git(self.repo, *args, env=self.fixture.env).stdout.strip()

    def test_shared_store_survives_feature_removal_without_opencode(self):
        feature = self.fixture.root / "linked worktree"
        self.git("worktree", "add", "-b", "topic", str(feature))
        store = EvidenceStore(feature)
        self.assertEqual(store.path, self.store.path)
        self.assertFalse(store.path.exists())
        (feature / ".opencode").mkdir()
        (feature / ".opencode/private.txt").write_text("unrelated configuration")
        path = store.save(self.policy, self.result, before_sha=self.before, landing_sha=self.sha)
        self.git("worktree", "remove", "--force", str(feature))
        self.assertTrue(Path(path).exists())
        self.assertEqual(self.store.records()[0]["landing_sha"], self.sha)
        self.assertFalse((self.repo / ".opencode").exists())
        if os.name != "nt":
            self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o700)
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)

    def test_atomic_association_and_failed_replace_keep_previous_receipt(self):
        path = Path(self.store.save(self.policy, self.result))
        original = path.read_bytes()
        with mock.patch("pipeline_evidence.os.replace", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(PipelineError):
                self.store.save(self.policy, self.result, before_sha=self.before, landing_sha=self.sha)
        self.assertEqual(path.read_bytes(), original)
        self.store.save(self.policy, self.result, before_sha=self.before, landing_sha=self.sha)
        self.store.save(self.policy, self.result)
        self.assertEqual(self.store.records()[0]["before_sha"], self.before)
        self.assertEqual(list(self.store.path.glob(".write-*")), [])

    def test_short_lock_and_active_operations_are_explicit(self):
        first = self.store.start_operation()
        second = self.store.start_operation()
        self.assertEqual(len(list(self.store.path.glob(".active-*"))), 2)
        with self.store.lock():
            with self.assertRaisesRegex(PipelineError, "locked"):
                self.store.save(self.policy, self.result)
        self.store.finish_operation(first)
        self.store.finish_operation(second)
        self.assertEqual(list(self.store.path.iterdir()), [])

    def test_corrupt_mismatched_and_unsafe_records_fail_closed(self):
        path = Path(self.store.save(self.policy, self.result))
        valid = path.read_text()
        for text in ("{", "[]", valid.replace('"schema_version":1', '"schema_version":2'), valid.replace(self.sha, "b" * 40)):
            with self.subTest(text=text[:24]):
                path.write_text(text)
                with self.assertRaises(PipelineError):
                    self.store.records()
        path.write_text(valid)
        path.rename(path.with_name("unexpected.json"))
        with self.assertRaises(PipelineError):
            self.store.records()

    def test_record_symlinks_are_not_followed(self):
        path = Path(self.store.save(self.policy, self.result))
        target = self.fixture.root / "outside.json"
        path.rename(target)
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(PipelineError):
            self.store.records()
        self.assertTrue(target.exists())

    def test_invalid_landing_and_non_success_cannot_be_recorded(self):
        for outcome in ("bypass", "retryable", "terminal"):
            bad = dict(self.result, outcome=outcome)
            with self.assertRaises(PipelineError):
                self.store.save(self.policy, bad)
        with self.assertRaises(PipelineError):
            self.store.save(self.policy, self.result, before_sha=self.before, landing_sha=self.before)


if __name__ == "__main__":
    unittest.main()
