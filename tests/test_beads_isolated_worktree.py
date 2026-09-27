import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


BD = shutil.which("bd")


def run(argv, cwd, check=True):
    env = os.environ.copy()
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "BEADS_DIR", "BD_DB", "BD_DATABASE", "BD_ACTOR",
    ):
        env.pop(key, None)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return subprocess.run(
        argv, cwd=cwd, env=env, text=True, capture_output=True, check=check
    )


def anchors(notes):
    return [
        json.loads(line.split("beads-work anchor: ", 1)[1])
        for line in notes.splitlines() if line.startswith("beads-work anchor: ")
    ]


def matching_anchor(notes, issue, agent, branch, worktree):
    rows = anchors(notes)
    if len(rows) != 1 or (
        rows[0]["id"], rows[0]["agent"], rows[0]["branch"], rows[0]["worktree_path"]
    ) != (issue, agent, branch, str(worktree)):
        raise ValueError("ambiguous or stale tracking anchor")
    return rows[0]


@unittest.skipUnless(BD, "bd is unavailable")
class IsolatedBeadsWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="beads-isolated-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "project"
        self.repo.mkdir()
        run(["git", "init", "-q", "-b", "main"], self.repo)
        tree = run(["git", "mktree"], self.repo).stdout.strip()
        commit = run([
            "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit-tree", tree, "-m", "initial",
        ], self.repo).stdout.strip()
        run(["git", "update-ref", "refs/heads/main", commit], self.repo)
        run([BD, "init", "--non-interactive", "--skip-agents", "--skip-hooks", "--prefix", "demo"], self.repo)
        self.assertTrue((self.repo / ".beads").is_dir())
        self.issue = run([BD, "create", "fixture issue", "--actor", "Fixture", "--json"], self.repo)
        self.issue_id = json.loads(self.issue.stdout)["id"]
        self.assertTrue(self.issue_id.startswith("demo-"))
        self.worktree = self.repo / ".claude" / "worktrees" / "isolated"
        self.worktree.parent.mkdir(parents=True)
        run(["git", "worktree", "add", "-q", "--detach", str(self.worktree), "HEAD"], self.repo)
        if (self.worktree / ".beads").exists():
            shutil.rmtree(self.worktree / ".beads")

    def show(self):
        return json.loads(run([BD, "show", self.issue_id, "--json"], self.worktree).stdout)[0]

    def test_no_local_beads_note_readback_and_stub_discovery(self):
        self.assertFalse((self.worktree / ".beads").exists())
        before = run([BD, "show", self.issue_id, "--json"], self.worktree, check=False)
        self.assertEqual(before.returncode, 0, before.stderr)
        anchor = {
            "id": self.issue_id, "agent": "OpenCode", "branch": "HEAD",
            "worktree_path": str(self.worktree), "started_sha": run([
                "git", "rev-parse", "HEAD"], self.worktree).stdout.strip(),
            "started_at": "2026-09-27T00:00:00Z",
        }
        run([BD, "update", self.issue_id, "--append-notes",
             "beads-work anchor: " + json.dumps(anchor, separators=(",", ":")),
             "--actor", "OpenCode"], self.worktree)
        self.assertEqual(matching_anchor(
            self.show()["notes"], self.issue_id, "OpenCode", "HEAD", self.worktree
        ), anchor)
        self.assertFalse((self.worktree / ".beads").exists())
        (self.worktree / ".beads").mkdir()
        with_stub = run([BD, "show", self.issue_id, "--json"], self.worktree, check=False)
        self.assertEqual(with_stub.returncode, 0, with_stub.stderr)
        self.assertEqual(json.loads(with_stub.stdout)[0]["id"], self.issue_id)
        self.assertEqual(self.issue_id, self.show_from_parent()["id"])

    def show_from_parent(self):
        return json.loads(run([BD, "show", self.issue_id, "--json"], self.repo).stdout)[0]

    def test_existing_file_and_collision_boundaries(self):
        self.assertFalse((self.worktree / ".beads").exists())
        file_path = self.repo / ".beads" / "in-progress-opencode.json"
        self.assertTrue(file_path.parent.is_dir())
        self.assertFalse(file_path.parent.is_relative_to(self.worktree))
        anchor = {
            "id": self.issue_id, "agent": "OpenCode", "branch": "main",
            "worktree_path": str(self.repo), "started_sha": run([
                "git", "rev-parse", "HEAD"], self.repo).stdout.strip(),
            "started_at": "2026-09-27T00:00:00Z",
        }
        file_path.write_text(json.dumps(anchor))
        self.assertEqual(json.loads(file_path.read_text()), anchor)
        note = "beads-work anchor: " + json.dumps(anchor)
        self.assertEqual(matching_anchor(note, self.issue_id, "OpenCode", "main", self.repo), anchor)
        for bad in (
            (self.issue_id, "OpenCode", "main", self.worktree),
            ("demo-other", "OpenCode", "main", self.repo),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                matching_anchor(note, *bad)
        with self.assertRaises(ValueError):
            matching_anchor(note + "\n" + note, self.issue_id, "OpenCode", "main", self.repo)

        tree = run(["git", "mktree"], self.repo).stdout.strip()
        first = run([
            "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit-tree", tree, "-p", anchor["started_sha"], "-m", "first",
            "-m", "Refs: demo-first",
        ], self.repo).stdout.strip()
        second = run([
            "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit-tree", tree, "-p", first, "-m", "second",
            "-m", "Refs: demo-second",
        ], self.repo).stdout.strip()
        first_range = run([
            "git", "log", f"{anchor['started_sha']}..{second}", "--format=%H %B"
        ], self.repo).stdout
        second_range = run(["git", "log", f"{first}..{second}", "--format=%H %B"], self.repo).stdout
        self.assertIn(first, first_range)
        self.assertIn(second, first_range)
        self.assertIn("Refs: demo-first", first_range)
        self.assertIn(second, second_range)
        self.assertNotIn(first, second_range)
        self.assertNotIn("Refs: demo-first", second_range)


if __name__ == "__main__":
    unittest.main()
