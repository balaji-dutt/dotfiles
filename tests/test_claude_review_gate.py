"""Behavioral tests for the Claude review-gate state machine."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import threading
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
        self.state = self.isolated.root / "state"
        env = {**self.isolated.env, review_gate.STATE_DIR_ENV: str(self.state)}
        self.environment = mock.patch.dict(os.environ, env, clear=True)
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

    def bash_payload(self, tool_use_id: str, session_id: str = "session/one", background: bool = False) -> dict[str, object]:
        tool_input: dict[str, object] = {"command": "true"}
        if background:
            tool_input["run_in_background"] = True
        return {"cwd": str(self.repo), "session_id": session_id, "tool_use_id": tool_use_id, "tool_input": tool_input}

    def snapshot(self, tool_use_id: str = "t1", **options: object) -> None:
        self.assertEqual(review_gate.cmd_snapshot(self.bash_payload(tool_use_id, **options), str(self.repo)), 0)

    def mark_bash(self, tool_use_id: str = "t1", **options: object) -> None:
        self.assertEqual(review_gate.cmd_mark_bash(self.bash_payload(tool_use_id, **options), str(self.repo)), 0)

    def bash(self, action, tool_use_id: str = "t1", **options: object) -> list[str]:
        self.snapshot(tool_use_id, **options)
        action()
        self.mark_bash(tool_use_id, **options)
        return self.gated()

    def gated(self, session_id: str = "session/one") -> list[str]:
        gate = self.gate(session_id)
        return review_gate.read_gate(gate)["files"] if gate.exists() else []

    def write(self, relpath: str, text: str) -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self, *relpaths: str) -> None:
        for relpath in relpaths:
            self.write(relpath, relpath + "\n")
        run_git(self.repo, "add", "--", *relpaths, env=self.isolated.env)
        run_git(self.repo, "commit", "-q", "-m", "fixture", env=self.isolated.env)

    def git(self, *args: str):
        return lambda: run_git(self.repo, *args, env=self.isolated.env)

    def snapshots(self) -> list[Path]:
        return sorted(self.state.glob("*.json"))

    def test_bash_marks_edits_new_files_deletions_and_moves(self) -> None:
        self.commit("gone.txt", "old.txt")
        self.assertEqual(self.bash(lambda: self.write("baseline.txt", "changed\n")), ["baseline.txt"])
        self.bash(lambda: self.write("new file.txt", "x\n"), "t2")
        self.bash(lambda: (self.repo / "gone.txt").unlink(), "t3")
        self.bash(self.git("mv", "old.txt", "moved.txt"), "t4")
        self.assertEqual(self.gated(), ["baseline.txt", "gone.txt", "moved.txt", "new file.txt", "old.txt"])
        self.assertEqual(self.snapshots(), [])
        self.assertEqual(json.loads(self.enforce())["decision"], "block")

    @unittest.skipIf(os.name == "nt", "symlinks need extra privileges on Windows")
    def test_bash_tracks_symlink_targets(self) -> None:
        link = self.repo / "link"
        link.symlink_to("a")

        def relink(target: str):
            def action() -> None:
                link.unlink()
                link.symlink_to(target)
            return action

        self.assertEqual(self.bash(relink("a")), [])
        self.assertEqual(self.bash(relink("b"), "t2"), ["link"])

    def test_bash_ignores_read_only_touch_staging_and_untouched_dirt(self) -> None:
        self.commit("dirty.txt")
        dirty = self.write("dirty.txt", "pre-existing\n")
        self.assertEqual(self.bash(self.git("status")), [])
        self.assertEqual(self.snapshots(), [])
        later = time.time_ns() + 5_000_000_000
        self.assertEqual(self.bash(lambda: os.utime(dirty, ns=(later, later)), "t2"), [])
        self.assertEqual(self.bash(self.git("add", "dirty.txt"), "t3"), [])
        self.assertFalse(self.gate().exists())
        self.assertEqual(self.bash(lambda: self.write("dirty.txt", "edited again\n"), "t4"), ["dirty.txt"])

    def test_bash_marks_same_size_rewrite_within_one_clock_tick(self) -> None:
        dirty = self.write("tick.txt", "aaa\n")
        stamp = dirty.stat()

        def rewrite() -> None:
            dirty.write_text("bbb\n", encoding="utf-8")
            os.utime(dirty, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

        self.assertEqual(self.bash(rewrite), ["tick.txt"])

    def test_bash_honors_path_policy_and_runtime_artifacts(self) -> None:
        def action() -> None:
            self.write("docs/guide.md", "x\n")
            self.write("docs/agents/rules.md", "x\n")
            self.write(".claude/.needs_dotfiles_review.other", "x\n")
            self.write(".claude/settings.local.json", "{}\n")

        self.assertEqual(self.bash(action), ["docs/agents/rules.md"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFOs")
    def test_fingerprints_survive_directories_specials_and_unreadable_files(self) -> None:
        (self.repo / "sub").mkdir()
        os.mkfifo(self.repo / "pipe")
        self.write("ok.txt", "ok\n")
        locked = self.write("locked.txt", "secret\n")
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o644)
        paths = ["sub", "pipe", "ok.txt", "locked.txt", "missing.txt"]
        with mock.patch.object(review_gate, "dirty_paths", return_value=paths):
            result = review_gate.dirty_fingerprints(str(self.repo))
            with mock.patch.object(review_gate, "HASH_BUDGET_BYTES", 1):
                over_budget = review_gate.dirty_fingerprints(str(self.repo))
        self.assertNotIn("sub", result)
        self.assertEqual(result["pipe"], ["special"])
        self.assertEqual(result["ok.txt"][0], "file")
        self.assertIsNotNone(result["ok.txt"][3])
        self.assertIsNone(over_budget["ok.txt"][3])
        self.assertEqual(result["missing.txt"], ["missing"])
        if os.geteuid() != 0:
            self.assertEqual(result["locked.txt"][0], "unreadable")

    @unittest.skipIf(os.name == "nt", "POSIX file names")
    def test_bash_marks_newline_and_non_utf8_names(self) -> None:
        names = ["new\nline.txt"]
        if sys.platform != "darwin":
            names.append(os.fsdecode(b"bad\xff.txt"))

        def action() -> None:
            for name in names:
                (self.repo / name).write_text("x\n", encoding="utf-8")

        self.assertEqual(self.bash(action), sorted(names))
        self.assertEqual(json.loads(self.enforce())["decision"], "block")

    def test_bash_mark_requires_its_own_snapshot_and_consumes_it(self) -> None:
        self.write("a.txt", "x\n")
        self.mark_bash("never-snapshotted")
        self.assertFalse(self.gate().exists())
        payload = self.bash_payload("t1")
        del payload["tool_use_id"]
        review_gate.cmd_snapshot(payload, str(self.repo))
        self.assertEqual(self.snapshots(), [])

        self.assertEqual(self.bash(lambda: self.write("b.txt", "x\n"), "t2"), ["b.txt"])
        marked_at = review_gate.read_gate(self.gate())["markedAt"]
        self.write("c.txt", "x\n")
        self.mark_bash("t2")
        self.assertEqual(self.gated(), ["b.txt"])
        self.assertEqual(review_gate.read_gate(self.gate())["markedAt"], marked_at)

    def test_bash_interleaved_calls_keep_their_own_baselines(self) -> None:
        self.snapshot("t1")
        self.write("a.txt", "x\n")
        self.snapshot("t2")
        self.write("b.txt", "x\n")
        self.mark_bash("t2")
        self.assertEqual(self.gated(), ["b.txt"])
        self.mark_bash("t1")
        self.assertEqual(self.gated(), ["a.txt", "b.txt"])

    def test_snapshot_prunes_only_expired_snapshot_files(self) -> None:
        self.state.mkdir()
        now = time.time()
        old = now - review_gate.SNAPSHOT_TTL_SECONDS - 1
        name = "0" * 16 + "-" + "%016d"
        expired = self.state / ((name % 1) + ".json")
        expired.write_text(json.dumps({"root": str(self.repo), "session_id": "s", "created": old, "fingerprints": {}}), encoding="utf-8")
        skewed = self.state / ((name % 2) + ".json")
        skewed.write_text(json.dumps({"root": str(self.repo), "session_id": "s", "created": now + 30, "fingerprints": {}}), encoding="utf-8")
        far_future = self.state / ((name % 4) + ".json")
        far_future.write_text(json.dumps({"root": str(self.repo), "session_id": "s", "created": now + 2 * review_gate.SNAPSHOT_TTL_SECONDS, "fingerprints": {}}), encoding="utf-8")
        leftover = self.state / ((name % 3) + ".json.tmp-abc")
        unrelated = self.state / "unrelated.txt"
        for path in (leftover, unrelated):
            path.write_text("", encoding="utf-8")
            os.utime(path, (old, old))
        self.snapshot("t1")
        self.assertFalse(expired.exists())
        self.assertFalse(far_future.exists())
        self.assertFalse(leftover.exists())
        self.assertTrue(skewed.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(len(self.snapshots()), 2)

    def test_background_snapshot_is_rebased_and_reconciled_at_stop(self) -> None:
        self.snapshot("bg", background=True)
        self.mark_bash("bg", background=True)
        self.assertFalse(self.gate().exists())
        self.assertEqual(len(self.snapshots()), 1)
        self.write("late.txt", "x\n")
        self.assertEqual(json.loads(self.enforce())["decision"], "block")
        self.assertEqual(self.gated(), ["late.txt"])
        self.gate().unlink()
        self.assertEqual(review_gate.reconcile_snapshots(str(self.repo), "session/one"), [])
        self.assertFalse(self.gate().exists())

    def test_marked_edits_do_not_resurface_from_a_lingering_snapshot(self) -> None:
        self.commit("gone.txt")
        self.snapshot("bg", background=True)
        self.mark_bash("bg", background=True)
        self.assertEqual(self.enforce(), "")

        gate = self.mark("edited.txt")
        self.bash(lambda: (self.repo / "gone.txt").unlink(), "fg")
        self.assertEqual(self.gated(), ["edited.txt", "gone.txt"])
        marked_at = self.rewind(gate)
        self.put_inflight("a1", marked_at + 0.5)
        self.assertIn("systemMessage", json.loads(self.enforce()))
        self.assertEqual(review_gate.read_gate(gate)["markedAt"], marked_at)

        self.clear(self.transcript("pass.jsonl", "RESULT=PASS"))
        self.assertFalse(gate.exists())
        self.assertEqual(self.enforce(), "")
        self.assertFalse(gate.exists())

        self.write("late.txt", "x\n")
        self.assertEqual(json.loads(self.enforce())["decision"], "block")
        self.assertEqual(self.gated(), ["late.txt"])

    @unittest.skipIf(os.name == "nt", "gate writes are unlocked on Windows")
    def test_parallel_gate_writers_keep_every_path(self) -> None:
        self.snapshot("bg", background=True)
        self.mark_bash("bg", background=True)
        rels = ["p%02d.txt" % i for i in range(16)]
        for rel in rels:
            self.write(rel, rel)
        barrier = threading.Barrier(len(rels))

        def write_after_barrier(rel: str) -> None:
            barrier.wait()
            review_gate.write_gate(str(self.repo), "session/one", [rel])

        threads = [threading.Thread(target=write_after_barrier, args=(rel,)) for rel in rels]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.gated(), rels)
        [snapshot] = self.snapshots()
        self.assertTrue(set(rels) <= set(review_gate.read_snapshot(str(snapshot))["fingerprints"]))
        self.assertEqual(review_gate.reconcile_snapshots(str(self.repo), "session/one"), [])

    @unittest.skipIf(os.name == "nt", "POSIX mode bits")
    def test_bash_marks_an_executable_bit_change_on_a_dirty_file(self) -> None:
        script = self.write("tool.sh", "echo hi\n")
        script.chmod(0o644)
        self.assertEqual(self.bash(lambda: script.chmod(0o755)), ["tool.sh"])

    def test_orphaned_foreground_snapshots_expire_before_background_ones(self) -> None:
        def age_snapshots() -> None:
            for path in self.snapshots():
                data = review_gate.read_snapshot(str(path))
                data["created"] -= review_gate.FOREGROUND_SNAPSHOT_TTL_SECONDS + 1
                path.write_text(json.dumps(data), encoding="utf-8")

        self.snapshot("interrupted")
        age_snapshots()
        self.write("partial.txt", "x\n")
        self.assertEqual(json.loads(self.enforce())["decision"], "block")
        self.assertEqual(self.gated(), ["partial.txt"])
        self.assertEqual(self.snapshots(), [])
        self.gate().unlink()

        self.snapshot("bg", background=True)
        age_snapshots()
        self.write("later.txt", "x\n")
        self.assertEqual(review_gate.reconcile_snapshots(str(self.repo), "session/one"), ["later.txt"])
        self.assertEqual(len(self.snapshots()), 1)

    def test_other_sessions_skip_the_foreground_ttl_for_a_prompt_wait(self) -> None:
        self.snapshot("waiting", session_id="session/other")
        [waiting] = self.snapshots()
        data = review_gate.read_snapshot(str(waiting))
        data["created"] -= review_gate.FOREGROUND_SNAPSHOT_TTL_SECONDS + 60
        waiting.write_text(json.dumps(data), encoding="utf-8")
        self.snapshot("t1", background=True)
        self.mark_bash("t1", background=True)
        self.enforce()
        self.assertTrue(waiting.exists())
        self.write("approved.txt", "x\n")
        self.mark_bash("waiting", session_id="session/other")
        self.assertEqual(self.gated("session/other"), ["approved.txt"])

    def test_background_flag_from_post_payload_is_kept(self) -> None:
        self.snapshot("late-bg")
        self.mark_bash("late-bg", background=True)
        [snapshot] = self.snapshots()
        self.assertTrue(review_gate.read_snapshot(str(snapshot))["background"])

    def test_clear_keeps_a_gate_marked_while_the_verdict_was_read(self) -> None:
        gate = self.mark("tracked.txt")
        self.rewind(gate)
        original = review_gate.transcript_has_pass

        def pass_after_concurrent_mark(*args: object) -> bool:
            review_gate.write_gate(str(self.repo), "session/one", ["racing.txt"])
            return original(*args)

        with mock.patch.object(review_gate, "transcript_has_pass", pass_after_concurrent_mark):
            self.clear(self.transcript("pass.jsonl", "RESULT=PASS"))
        self.assertTrue(gate.exists())
        self.assertIn("racing.txt", self.gated())

    def test_rebase_keeps_a_fingerprint_synced_concurrently(self) -> None:
        self.snapshot("bg", background=True)
        self.mark_bash("bg", background=True)
        original = review_gate.dirty_fingerprints

        def racing(*args: object):
            after = original(*args)
            self.write("raced.txt", "x\n")
            review_gate.write_gate(str(self.repo), "session/one", ["raced.txt"])
            return after

        with mock.patch.object(review_gate, "dirty_fingerprints", racing):
            review_gate.reconcile_snapshots(str(self.repo), "session/one")
        self.gate().unlink()
        self.assertEqual(review_gate.reconcile_snapshots(str(self.repo), "session/one"), [])

    def test_rebase_never_recreates_a_consumed_snapshot(self) -> None:
        self.snapshot("t1")
        [path] = self.snapshots()
        read = review_gate.read_snapshot(str(path))["fingerprints"]
        path.unlink()
        review_gate.rebase_snapshot(str(path), read, {}, time.time())
        self.assertFalse(path.exists())

    @unittest.skipIf(os.name == "nt", "POSIX ownership")
    def test_state_dir_refuses_a_default_directory_owned_by_someone_else(self) -> None:
        tmp = self.isolated.root / "tmp"
        with mock.patch.dict(os.environ):
            os.environ.pop(review_gate.STATE_DIR_ENV)
            with mock.patch.object(review_gate.tempfile, "gettempdir", return_value=str(tmp)), mock.patch.object(
                review_gate.os, "getuid", lambda: os.geteuid() + 1
            ):
                (tmp / ("claude-review-gate-%d" % (os.geteuid() + 1))).mkdir()
                stderr = io.StringIO()
                with mock.patch.object(review_gate.sys, "stderr", stderr):
                    self.assertIsNone(review_gate.state_dir())
        self.assertIn("owned by another user", stderr.getvalue())

    def test_enforce_keeps_a_gate_marked_while_it_stands_down(self) -> None:
        gate = self.mark("tracked.txt")
        self.rewind(gate)

        def pending_after_concurrent_mark(*args: object) -> list[str]:
            review_gate.write_gate(str(self.repo), "session/one", ["racing.txt"])
            return []

        with mock.patch.object(review_gate, "git_pending", pending_after_concurrent_mark):
            self.enforce()
        self.assertTrue(gate.exists())
        self.assertIn("racing.txt", self.gated())

    def test_enforce_still_blocks_when_reconcile_fails(self) -> None:
        self.mark("tracked.txt")
        stderr = io.StringIO()
        with mock.patch.object(review_gate, "reconcile_snapshots", side_effect=OSError("disk full")), mock.patch.object(
            review_gate.sys, "stderr", stderr
        ):
            self.assertEqual(json.loads(self.enforce())["decision"], "block")
        self.assertIn("snapshot reconcile failed", stderr.getvalue())

    def test_enforce_reconciles_only_this_sessions_orphaned_snapshots(self) -> None:
        self.snapshot("other", session_id="session/other")
        self.write("theirs.txt", "x\n")
        self.snapshot("interrupted")
        self.write("partial.txt", "x\n")
        self.assertEqual(json.loads(self.enforce())["decision"], "block")
        self.assertEqual(self.gated(), ["partial.txt"])
        self.assertFalse(self.gate("session/other").exists())
        self.assertEqual(len(self.snapshots()), 2)
        self.mark_bash("other", session_id="session/other")
        self.assertEqual(self.gated("session/other"), ["partial.txt", "theirs.txt"])

    def test_bash_mark_after_reviewer_start_makes_the_reviewer_stale(self) -> None:
        gate = self.mark("tracked.txt")
        record = self.put_inflight("a1", self.rewind(gate) + 0.5)
        self.assertIn("systemMessage", json.loads(self.enforce()))
        self.bash(lambda: self.write("later.txt", "x\n"))
        self.assertIn("does not count as in flight", json.loads(self.enforce())["reason"])
        self.clear(self.transcript("pass.jsonl", "RESULT=PASS"))
        self.assertFalse(record.exists())
        self.assertTrue(gate.exists())

    def test_bash_stash_apply_marks_restored_work(self) -> None:
        self.write("baseline.txt", "stashed edit\n")
        self.assertEqual(self.bash(self.git("stash", "push", "-q")), [])
        self.assertEqual(self.bash(self.git("stash", "apply", "-q"), "t2"), ["baseline.txt"])

    def test_state_dir_is_stable_without_getuid_and_rejects_non_directories(self) -> None:
        tmp = self.isolated.root / "tmp"
        with mock.patch.dict(os.environ):
            os.environ.pop(review_gate.STATE_DIR_ENV)
            with mock.patch.object(review_gate.tempfile, "gettempdir", return_value=str(tmp)), mock.patch.object(
                review_gate.os, "getuid", None, create=True
            ):
                first = review_gate.state_dir()
                self.assertEqual(first, review_gate.state_dir())
                self.assertEqual(first, str(tmp / "claude-review-gate"))
        blocker = self.isolated.root / "not-a-directory"
        blocker.write_text("", encoding="utf-8")
        unsafe = [blocker]
        if os.name != "nt":
            target = self.isolated.root / "elsewhere"
            target.mkdir()
            link = self.isolated.root / "linked-state"
            link.symlink_to(target)
            unsafe.append(link)
        for path in unsafe:
            with self.subTest(path=path.name), mock.patch.dict(os.environ, {review_gate.STATE_DIR_ENV: str(path)}):
                stderr = io.StringIO()
                with mock.patch.object(review_gate.sys, "stderr", stderr):
                    self.assertIsNone(review_gate.state_dir())
                self.assertIn("not a private directory", stderr.getvalue())

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_state_dir_leaves_an_override_directory_mode_alone(self) -> None:
        self.state.mkdir()
        self.state.chmod(0o755)
        self.assertEqual(review_gate.state_dir(), str(self.state))
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o755)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs POSIX permissions enforced")
    def test_helper_exits_nonzero_when_a_subcommand_raises(self) -> None:
        self.state.mkdir()
        self.state.chmod(0o500)
        self.addCleanup(self.state.chmod, 0o700)
        result = subprocess.run(
            [sys.executable, str(HELPER), "snapshot"],
            input=json.dumps(self.bash_payload("t1")),
            text=True,
            capture_output=True,
            cwd=self.repo,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("review_gate.py:", result.stderr)


if __name__ == "__main__":
    unittest.main()
