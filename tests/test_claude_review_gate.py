"""Behavioral tests for the Claude review-gate state machine."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import time
import unittest
from unittest import mock

from tests.support.fixtures import init_git_repository, isolated_environment, run_git


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".claude/hooks/lib/review_gate.py"
SPEC = importlib.util.spec_from_file_location("review_gate", HELPER)
assert SPEC and SPEC.loader
review_gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_gate)


class ReviewGatePureTests(unittest.TestCase):
    def test_jsonc_config_and_exemption_precedence(self) -> None:
        text = '{"url": "https://example.test//ok", /* x */ "paths": ["a",],}'
        self.assertEqual(
            json.loads(review_gate.strip_jsonc(text)),
            {"url": "https://example.test//ok", "paths": ["a"]},
        )
        patterns = ["docs/**", "!docs/agents/**", "assets/README.md"]
        self.assertTrue(review_gate.is_exempt("docs/guide.md", patterns))
        self.assertFalse(review_gate.is_exempt("docs/agents/rules.md", patterns))
        self.assertTrue(review_gate.is_exempt("assets/README.md", patterns))
        self.assertFalse(review_gate.is_exempt("src/docs/guide.md", patterns))

    def test_globs_runtime_paths_and_session_ids(self) -> None:
        self.assertIsNotNone(review_gate.glob_to_regex("docs/**/index?.md").match("docs/index1.md"))
        self.assertIsNotNone(review_gate.glob_to_regex("docs/**/index?.md").match("docs/a/index2.md"))
        self.assertIsNone(review_gate.glob_to_regex("docs/*.md").match("docs/a/x.md"))
        for relpath in (
            ".claude/.needs_dotfiles_review.x",
            ".opencode/.dotfiles_review_enforcer_state.x",
            ".opencode/node_modules/pkg/index.js",
            "tmp-review-gate.log",
            "docs/.DS_Store",
        ):
            with self.subTest(relpath=relpath):
                self.assertTrue(review_gate.is_runtime_artifact(relpath))
        self.assertFalse(review_gate.is_runtime_artifact("src/review-gate.md"))
        self.assertEqual(review_gate.sanitize_session_id("a/b c:@"), "a_b_c__")

    def test_gate_parsing_and_timestamp_fallback(self) -> None:
        with isolated_environment(prefix="review-gate-pure-") as isolated:
            gate = isolated.root / "gate"
            gate.write_text("123\n", encoding="utf-8")
            self.assertEqual(review_gate.read_gate(gate), {"timestamp": 123, "files": []})
            gate.write_text("not-json\n", encoding="utf-8")
            self.assertEqual(review_gate.read_gate(gate), {"files": []})
            self.assertGreater(review_gate.gate_epoch(gate), 0)
            gate.write_text('[]\n', encoding="utf-8")
            self.assertEqual(review_gate.read_gate(gate), {"files": []})

    def test_marker_requires_one_terminal_exact_token(self) -> None:
        passed = "DOTFILES_REVIEWER_RESULT=PASS"
        failed = "DOTFILES_REVIEWER_RESULT=FAIL"
        cases = {
            "review complete\nDOTFILES_REVIEWER_RESULT=PASS\n": "PASS",
            "DOTFILES_REVIEWER_RESULT=FAIL": "FAIL",
            "DOTFILES_REVIEWER_RESULT=PASS\nmore": "INVALID",
            "DOTFILES_REVIEWER_RESULT=PASS\nDOTFILES_REVIEWER_RESULT=PASS": "INVALID",
            "DOTFILES_REVIEWER_RESULT=FAIL\nDOTFILES_REVIEWER_RESULT=PASS": "INVALID",
            "quoted `DOTFILES_REVIEWER_RESULT=PASS`": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(review_gate.marker_from_text(text, passed, failed), expected)

    def test_transcript_filters_roles_time_and_malformed_records(self) -> None:
        with isolated_environment(prefix="review-transcript-") as isolated:
            transcript = isolated.root / "transcript.jsonl"
            records = [
                "not json",
                json.dumps({"type": "user", "timestamp": "2099-01-01T00:00:00Z", "message": {"content": "DOTFILES_REVIEWER_RESULT=PASS"}}),
                json.dumps({"isMeta": True, "timestamp": "2099-01-01T00:00:00Z", "message": {"content": "DOTFILES_REVIEWER_RESULT=PASS"}}),
                json.dumps({"timestamp": "2020-01-01T00:00:00Z", "message": {"content": [{"type": "text", "text": "DOTFILES_REVIEWER_RESULT=PASS"}]}}),
                json.dumps({"timestamp": "2099-01-01T00:00:00Z", "message": {"content": [{"type": "tool", "text": "DOTFILES_REVIEWER_RESULT=PASS"}]}}),
            ]
            transcript.write_text("\n".join(records) + "\n", encoding="utf-8")
            self.assertFalse(review_gate.transcript_has_pass(transcript, time.time(), "DOTFILES_REVIEWER_RESULT"))
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"timestamp": "2099-01-01T00:00:00Z", "message": {"content": [{"type": "text", "text": "done\nDOTFILES_REVIEWER_RESULT=PASS"}]}}) + "\n")
            self.assertTrue(review_gate.transcript_has_pass(transcript, time.time(), "DOTFILES_REVIEWER_RESULT"))


class ReviewGateRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = isolated_environment(prefix="review-gate-repo-")
        self.isolated = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.environment = mock.patch.dict(os.environ, self.isolated.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.repo = init_git_repository(self.isolated.root / "repo", env=self.isolated.env)
        (self.repo / ".opencode").mkdir()
        (self.repo / ".claude").mkdir()
        (self.repo / ".opencode/opencode-tooling.config.jsonc").write_text(
            '{"exemptPaths": ["docs/**", "!docs/agents/**"], "reviewerAgent": "review bot", "resultMarkerPrefix": "RESULT"}\n',
            encoding="utf-8",
        )
        (self.repo / "baseline.txt").write_text("base\n", encoding="utf-8")
        run_git(self.repo, "add", ".", env=self.isolated.env)
        run_git(self.repo, "commit", "-m", "baseline", env=self.isolated.env)

    def payload(self, relpath: str, session_id: str = "session/one") -> dict[str, object]:
        return {
            "cwd": str(self.repo),
            "session_id": session_id,
            "tool_input": {"file_path": str(self.repo / relpath)},
        }

    def gate(self, session_id: str = "session/one") -> Path:
        return Path(review_gate.gate_path(str(self.repo), session_id))

    def mark(self, relpath: str, session_id: str = "session/one") -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relpath + "\n", encoding="utf-8")
        self.assertEqual(review_gate.cmd_mark(self.payload(relpath, session_id), str(self.repo)), 0)
        return self.gate(session_id)

    def enforce(self, session_id: str = "session/one") -> str:
        output = io.StringIO()
        with mock.patch.object(review_gate.sys, "stdout", output):
            self.assertEqual(review_gate.cmd_enforce({"session_id": session_id}, str(self.repo)), 0)
        return output.getvalue()

    def test_mark_merges_files_and_absorbs_legacy_gate(self) -> None:
        legacy = self.gate("")
        legacy.write_text(json.dumps({"timestamp": 10, "firstTimestamp": 10, "files": ["legacy.txt"]}), encoding="utf-8")
        gate = self.mark("z file.txt")
        self.mark("a.txt")
        data = review_gate.read_gate(gate)
        self.assertEqual(data["files"], ["a.txt", "legacy.txt", "z file.txt"])
        self.assertEqual(data["firstTimestamp"], 10)
        self.assertFalse(legacy.exists())

    def test_mark_honors_exemptions_runtime_and_repository_boundaries(self) -> None:
        self.mark("docs/guide.md")
        self.assertFalse(self.gate().exists())
        self.mark("docs/agents/rules.md")
        self.assertTrue(self.gate().exists())
        self.gate().unlink()
        self.mark(".claude/.needs_dotfiles_review.extra")
        self.assertFalse(self.gate().exists())

        sibling = init_git_repository(self.isolated.root / "repo-sibling", env=self.isolated.env)
        outside = sibling / "outside.txt"
        outside.write_text("x\n", encoding="utf-8")
        payload = self.payload("baseline.txt")
        payload["tool_input"] = {"file_path": str(outside)}
        review_gate.cmd_mark(payload, str(self.repo))
        self.assertFalse(self.gate().exists())

        worktree = self.isolated.root / "linked-worktree"
        run_git(self.repo, "worktree", "add", "-q", "-b", "linked-test", str(worktree), env=self.isolated.env)
        linked_file = worktree / "linked.txt"
        linked_file.write_text("x\n", encoding="utf-8")
        payload["tool_input"] = {"file_path": str(linked_file)}
        review_gate.cmd_mark(payload, str(self.repo))
        self.assertFalse(self.gate().exists())

        nested = init_git_repository(self.repo / "nested", env=self.isolated.env)
        nested_file = nested / "nested.txt"
        nested_file.write_text("x\n", encoding="utf-8")
        payload["tool_input"] = {"file_path": str(nested_file)}
        review_gate.cmd_mark(payload, str(self.repo))
        self.assertFalse(self.gate().exists())

    def test_enforce_blocks_pending_scope_and_clears_reverted_edit(self) -> None:
        gate = self.mark("name with quote's.txt")
        payload = json.loads(self.enforce())
        self.assertEqual(payload["decision"], "block")
        self.assertIn("review bot", payload["reason"])
        self.assertIn("'name with quote'\"'\"'s.txt'", payload["reason"])
        (self.repo / "name with quote's.txt").unlink()
        self.assertEqual(self.enforce(), "")
        self.assertFalse(gate.exists())

    def test_enforce_tracks_staged_and_committed_work(self) -> None:
        gate = self.mark("tracked.txt")
        run_git(self.repo, "add", "tracked.txt", env=self.isolated.env)
        self.assertEqual(json.loads(self.enforce())["decision"], "block")
        run_git(self.repo, "commit", "-m", "change", env=self.isolated.env)
        self.assertEqual(json.loads(self.enforce())["decision"], "block")
        self.assertTrue(gate.exists())

    def test_legacy_gate_ignores_exempt_and_runtime_only_changes(self) -> None:
        gate = self.gate("")
        gate.write_text(str(int(time.time())), encoding="utf-8")
        (self.repo / "docs").mkdir()
        (self.repo / "docs/guide.md").write_text("x\n", encoding="utf-8")
        (self.repo / ".claude/.needs_dotfiles_review.extra").write_text("x\n", encoding="utf-8")
        self.assertEqual(self.enforce(), "")
        self.assertFalse(gate.exists())

    def test_clear_requires_current_pass_and_removes_all_gate_formats(self) -> None:
        now = int(time.time())
        for gate in (self.gate(), self.gate(""), self.repo / ".opencode/.needs_dotfiles_review"):
            gate.parent.mkdir(parents=True, exist_ok=True)
            gate.write_text(json.dumps({"timestamp": now, "files": ["x"]}), encoding="utf-8")
        stale = self.isolated.root / "stale.jsonl"
        stale.write_text(json.dumps({"timestamp": "2000-01-01T00:00:00Z", "message": {"content": "RESULT=PASS"}}) + "\n", encoding="utf-8")
        current = self.isolated.root / "current.jsonl"
        current.write_text(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "message": {"content": "done\nRESULT=PASS"}}) + "\n", encoding="utf-8")
        payload = {"session_id": "session/one", "agent_transcript_path": str(stale), "transcript_path": str(current)}
        with mock.patch.object(review_gate.time, "sleep"):
            review_gate.cmd_clear(payload, str(self.repo))
        for gate in (self.gate(), self.gate(""), self.repo / ".opencode/.needs_dotfiles_review"):
            self.assertFalse(gate.exists(), gate)

    def test_clear_retains_gate_for_invalid_or_stale_verdicts(self) -> None:
        cases = (
            ("2099-01-01T00:00:00Z", "RESULT=FAIL"),
            ("2099-01-01T00:00:00Z", "RESULT=PASS\nmore"),
            ("2099-01-01T00:00:00Z", "RESULT=PASS\nRESULT=PASS"),
            ("2000-01-01T00:00:00Z", "RESULT=PASS"),
        )
        for timestamp, content in cases:
            with self.subTest(content=content, timestamp=timestamp):
                gate = self.gate()
                gate.write_text(json.dumps({"timestamp": int(time.time()), "files": ["x"]}), encoding="utf-8")
                transcript = self.isolated.root / "invalid.jsonl"
                transcript.write_text(json.dumps({"timestamp": timestamp, "message": {"content": content}}) + "\n", encoding="utf-8")
                with mock.patch.object(review_gate.time, "sleep"):
                    review_gate.cmd_clear({"session_id": "session/one", "agent_transcript_path": str(transcript)}, str(self.repo))
                self.assertTrue(gate.exists())


if __name__ == "__main__":
    unittest.main()
