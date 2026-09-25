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

    def test_handback_verdicts_and_last_decisive_record_win(self) -> None:
        fresh = "2099-01-01T00:00:00Z"

        def text(body: str, timestamp: str | None = fresh) -> dict[str, object]:
            record: dict[str, object] = {"message": {"content": [{"type": "text", "text": body}]}}
            if timestamp:
                record["timestamp"] = timestamp
            return record

        def handback(body: object, name: str = "SubagentHandback") -> dict[str, object]:
            block = {"type": "tool_use", "name": name, "input": {"message": body}}
            return {"timestamp": fresh, "message": {"content": [block]}}

        cases = (
            ("handback PASS then prose", [handback("review\nRESULT=PASS"), text("Report delivered.")], True),
            ("handback FAIL", [handback("review\nRESULT=FAIL")], False),
            ("other tool", [handback("RESULT=PASS", name="Bash")], False),
            ("non-string handback", [handback(["RESULT=PASS"])], False),
            ("PASS then FAIL", [text("RESULT=PASS"), text("RESULT=FAIL")], False),
            ("FAIL then PASS", [text("RESULT=FAIL"), text("RESULT=PASS")], True),
            ("PASS then INVALID", [text("RESULT=PASS"), text("RESULT=PASS\nmore")], False),
            ("PASS then undated FAIL", [text("RESULT=PASS"), text("RESULT=FAIL", timestamp=None)], False),
            ("PASS then stale PASS", [text("RESULT=PASS"), text("RESULT=PASS", timestamp="2000-01-01T00:00:00Z")], False),
        )
        with isolated_environment(prefix="review-handback-") as isolated:
            transcript = isolated.root / "transcript.jsonl"
            for label, records, expected in cases:
                with self.subTest(label):
                    transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
                    self.assertIs(review_gate.transcript_has_pass(transcript, time.time(), "RESULT"), expected)


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

    def inflight(self, agent_id: str, session_id: str = "session/one") -> Path:
        return Path(review_gate.inflight_path(str(self.repo), session_id, agent_id))

    def put_inflight(self, agent_id: str, started: float | str) -> Path:
        path = self.inflight(agent_id)
        path.write_text(f"{started}\n", encoding="utf-8")
        return path

    def rewrite_gate(self, gate: Path, **fields: object) -> None:
        data = review_gate.read_gate(gate)
        data.update(fields)
        data = {key: value for key, value in data.items() if value is not None}
        gate.write_text(json.dumps(data), encoding="utf-8")

    def rewind(self, gate: Path, seconds: float = 60) -> float:
        marked_at = time.time() - seconds
        self.rewrite_gate(gate, timestamp=int(marked_at), markedAt=marked_at)
        return marked_at

    def transcript(self, name: str, *bodies: str, timestamp: str = "2099-01-01T00:00:00Z") -> Path:
        path = self.isolated.root / name
        path.write_text(
            "".join(json.dumps({"timestamp": timestamp, "message": {"content": [{"type": "text", "text": body}]}}) + "\n" for body in bodies),
            encoding="utf-8",
        )
        return path

    def clear(self, transcript: Path | None, agent_id: str = "a1", agent_type: str | None = "review bot", **extra: object) -> None:
        payload: dict[str, object] = {"session_id": "session/one", "agent_id": agent_id, **extra}
        if agent_type is not None:
            payload["agent_type"] = agent_type
        if transcript is not None:
            payload["agent_transcript_path"] = str(transcript)
        with mock.patch.object(review_gate.time, "sleep"):
            self.assertEqual(review_gate.cmd_clear(payload, str(self.repo)), 0)

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

    def test_clear_uses_main_transcript_only_without_agent_path(self) -> None:
        now = int(time.time())
        gates = (self.gate(), self.gate(""), self.repo / ".opencode/.needs_dotfiles_review")
        for gate in gates:
            gate.parent.mkdir(parents=True, exist_ok=True)
            gate.write_text(json.dumps({"timestamp": now, "files": ["x"]}), encoding="utf-8")
        stale = self.isolated.root / "stale.jsonl"
        stale.write_text(json.dumps({"timestamp": "2000-01-01T00:00:00Z", "message": {"content": "RESULT=PASS"}}) + "\n", encoding="utf-8")
        current = self.isolated.root / "current.jsonl"
        current.write_text(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "message": {"content": "done\nRESULT=PASS"}}) + "\n", encoding="utf-8")

        self.clear(stale, transcript_path=str(current))
        for gate in gates:
            self.assertTrue(gate.exists(), gate)

        self.clear(None, transcript_path=str(current))
        for gate in gates:
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


    def test_start_records_only_the_configured_reviewer(self) -> None:
        base = {"session_id": "session/one", "agent_id": "a1", "agent_type": "review bot"}
        self.assertEqual(review_gate.cmd_start(base, str(self.repo)), 0)
        started = review_gate.read_inflight(self.inflight("a1"))
        self.assertIsNotNone(started)
        self.assertLessEqual(abs(time.time() - started), 60)
        for payload in (
            {**base, "agent_id": "a2", "agent_type": "Explore"},
            {**base, "agent_id": ""},
            {**base, "session_id": ""},
            {**base, "agent_id": ["a3"]},
        ):
            with self.subTest(payload=payload):
                review_gate.cmd_start(payload, str(self.repo))
        self.assertEqual(sorted(p.name for p in (self.repo / ".claude").glob(".dotfiles_review_inflight*")), [self.inflight("a1").name])

    def test_enforce_allows_stop_while_a_fresh_reviewer_runs(self) -> None:
        gate = self.mark("tracked.txt")
        marked_at = self.rewind(gate)
        self.put_inflight("a1", marked_at + 0.5)
        output = json.loads(self.enforce())
        self.assertNotIn("decision", output)
        self.assertIn("review bot started less than a minute ago", output["systemMessage"])
        self.assertTrue(gate.exists())
        self.assertTrue(self.inflight("a1").exists())

        self.rewind(gate, 200)
        self.put_inflight("a1", time.time() - 150)
        self.assertIn("started 2 min ago", json.loads(self.enforce())["systemMessage"])

        self.rewind(self.mark("other.txt", "session/other"), 300)
        self.assertEqual(json.loads(self.enforce("session/other"))["decision"], "block")

    def test_enforce_blocks_for_expired_predating_or_malformed_reviewers(self) -> None:
        gate = self.mark("tracked.txt")
        now = time.time()
        base = int(now) - 30

        def blocked() -> str:
            output = json.loads(self.enforce())
            self.assertEqual(output["decision"], "block")
            return output["reason"]

        self.rewrite_gate(gate, timestamp=base - review_gate.INFLIGHT_TTL_SECONDS - 100, markedAt=base - review_gate.INFLIGHT_TTL_SECONDS - 99.5)
        expired = self.put_inflight("expired", now - review_gate.INFLIGHT_TTL_SECONDS - 1)
        self.assertIn("does not count as in flight", blocked())
        self.assertFalse(expired.exists())

        self.rewrite_gate(gate, timestamp=base, markedAt=base + 0.7)
        predating = self.put_inflight("predating", base + 0.2)
        self.assertIn("does not count as in flight", blocked())
        self.assertTrue(predating.exists())
        predating.unlink()

        self.rewrite_gate(gate, timestamp=base, markedAt=None)
        same_second = self.put_inflight("same-second", base + 0.5)
        self.assertIn("does not count as in flight", blocked())
        same_second.unlink()

        for label, started in (("future", now + 3600), ("infinite", "inf"), ("nan", "nan")):
            with self.subTest(label):
                record = self.put_inflight(label, started)
                self.assertIn("does not count as in flight", blocked())
                self.assertFalse(record.exists())

        self.put_inflight("malformed", "not-a-number")
        self.assertNotIn("does not count as in flight", blocked())

    def test_enforce_and_clear_agree_on_the_newest_gate(self) -> None:
        gate = self.mark("tracked.txt")
        marked_at = self.rewind(gate)
        self.gate("").write_text(str(int(marked_at) + 5), encoding="utf-8")
        record = self.put_inflight("a1", marked_at + 2)
        self.assertEqual(json.loads(self.enforce())["decision"], "block")
        self.clear(self.transcript("pass.jsonl", "done\nRESULT=PASS"))
        self.assertTrue(gate.exists())
        self.assertFalse(record.exists())

    def test_clear_fail_drops_record_and_keeps_blocking(self) -> None:
        gate = self.mark("tracked.txt")
        record = self.put_inflight("a1", self.rewind(gate) + 0.5)
        self.assertIn("systemMessage", json.loads(self.enforce()))
        self.clear(self.transcript("fail.jsonl", "issues\nRESULT=FAIL"))
        self.assertFalse(record.exists())
        self.assertTrue(gate.exists())
        self.assertEqual(json.loads(self.enforce())["decision"], "block")

    def test_clear_handback_pass_drops_record_and_gate(self) -> None:
        gate = self.mark("tracked.txt")
        record = self.put_inflight("a1", self.rewind(gate) + 0.5)
        transcript = self.isolated.root / "handback.jsonl"
        records = (
            {"timestamp": "2099-01-01T00:00:00Z", "message": {"content": [{"type": "tool_use", "name": "SubagentHandback", "input": {"message": "ok\n\nRESULT=PASS"}}]}},
            {"type": "user", "timestamp": "2099-01-01T00:00:01Z", "message": {"content": [{"type": "tool_result", "content": "delivered"}]}},
            {"timestamp": "2099-01-01T00:00:02Z", "message": {"content": [{"type": "text", "text": "Report delivered via SubagentHandback."}]}},
        )
        transcript.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        self.clear(transcript)
        self.assertFalse(record.exists())
        self.assertFalse(gate.exists())

    def test_clear_keeps_gate_for_reviewer_started_before_last_mark(self) -> None:
        gate = self.mark("tracked.txt")
        record = self.put_inflight("a1", review_gate.read_gate(gate)["markedAt"] - 1)
        self.clear(self.transcript("pass.jsonl", "RESULT=PASS"))
        self.assertFalse(record.exists())
        self.assertTrue(gate.exists())

    def test_clear_ignores_other_subagents(self) -> None:
        gate = self.mark("tracked.txt")
        self.clear(self.transcript("pass.jsonl", "RESULT=PASS"), agent_type="Explore")
        self.assertTrue(gate.exists())
        self.clear(self.transcript("pass.jsonl", "RESULT=PASS"), agent_type=None)
        self.assertFalse(gate.exists())

    def test_clear_without_gate_still_drops_record(self) -> None:
        record = self.put_inflight("a1", time.time())
        self.clear(None)
        self.assertFalse(record.exists())


if __name__ == "__main__":
    unittest.main()
