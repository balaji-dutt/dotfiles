import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTHOR = ROOT / "private_dot_config/opencode/agents/beads-issue-author.md"
CLAUDE_AUTHOR = ROOT / "dot_claude/agents/beads-issue-author.md"
OPEN_SKILL = ROOT / "private_dot_config/opencode/skills/beads-work/SKILL.md"
CLAUDE_SKILL = ROOT / "dot_claude/skills/beads-work/SKILL.md"
HANDOFF = ROOT / ".opencode/instructions/beads-plan-handoff.md"
BACKLOG = ROOT / ".opencode/agents/beads-backlog-manager.md"
MIRROR = ROOT / (
    "private_Documents/development/container-dotfiles/dotfiles/"
    "private_dot_config/opencode/agents/beads-issue-author.md"
)


def normalized(text: str) -> str:
    return " ".join(text.split())


class BeadsAgentSourceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = {path: path.read_text(encoding="utf-8") for path in (AUTHOR, BACKLOG)}

    def assert_outcome_contract(self, text: str) -> None:
        source = normalized(text)
        for term in (
            "Distinguish command success from readback-confirmed fields.",
            "On a command failure, permission denial, or missing result, stop further writes.",
            "Read-only reconciliation is allowed; do not bypass the denial.",
            "A later failure does not undo an earlier mutation. Do not imply rollback.",
            "Use “not changed” only when no mutation was attempted or readback confirms "
            "that no change occurred.",
            "A failed readback leaves the outcome unknown.",
            "Never repeat a confirmed successful create.",
            "stop for caller-assisted reconciliation; do not create a replacement speculatively.",
            "Use `Done` only when all required steps are confirmed.",
            "For a confirmed no-change stop, return:",
            "For a partial or unknown outcome, return:",
        ):
            self.assertIn(term, source)
        for field in ("Confirmed completed", "Failed", "Unknown", "Not attempted", "Needed from caller"):
            self.assertIn(f"- {field}:", text)

    def test_partial_and_unknown_outcomes_are_explicit(self) -> None:
        for path, text in self.sources.items():
            with self.subTest(path=path):
                self.assert_outcome_contract(text)

    def test_missing_safety_clauses_fail_source_contract(self) -> None:
        for path, text in self.sources.items():
            for clause in (
                "Never repeat a confirmed successful create.",
                "A failed readback leaves the outcome unknown.",
                "Do not imply rollback.",
                "Use `Done` only when all required steps are confirmed.",
                "- Unknown:",
            ):
                with self.subTest(path=path, clause=clause):
                    mutated = text.replace(clause, "REMOVED", 1)
                    self.assertNotEqual(mutated, text)
                    with self.assertRaises(AssertionError):
                        self.assert_outcome_contract(mutated)

    def test_retries_reconcile_before_missing_authorized_steps(self) -> None:
        for path, text in self.sources.items():
            with self.subTest(path=path):
                retry = normalized(text).split("On retry,", 1)[1].split("Never repeat", 1)[0]
                self.assertIn("reconcile the known issue", retry)
                self.assertIn("with `<bd> show <id>` before any additional mutation.", retry)
                self.assertIn("Resume only missing, still-authorized steps", retry)
                self.assertIn("do not reapply confirmed updates", retry)

    def test_author_checks_collisions_before_mutations_and_state_write(self) -> None:
        text = self.sources[AUTHOR]
        preflight = normalized(text.split("1. Preflight:", 1)[1].split("2. Resolve", 1)[0])
        state = normalized(text.split("6. Record tracking state", 1)[1].split("7. Return", 1)[0])
        self.assertIn("Before any Beads mutation, check `.beads/in-progress-opencode.json`", preflight)
        self.assertIn("`<bd> show <id>` for tracking notes", preflight)
        self.assertIn("require explicit caller selection and matching issue, branch, and worktree", preflight)
        self.assertIn("Recheck for a collision immediately before writing tracking.", state)
        self.assertIn("report any already-completed issue mutations as partial", state)
        self.assertIn("Do not create a stub directory, write to another checkout", state)
        self.assertIn("--append-notes '<record>' --actor \"OpenCode\"", state)
        self.assertIn("confirm all fields before reporting handoff ready", state)

    def test_author_claim_and_state_need_distinct_evidence(self) -> None:
        text = normalized(self.sources[AUTHOR])
        self.assertIn("Claim and tracking outcomes require their own evidence.", text)
        self.assertIn("Verify current status and assignee with `<bd> show <id>`", text)
        self.assertIn("A failed readback is an unknown outcome, not a failed claim.", text)
        self.assertIn("do not append a duplicate note", text)

    def test_both_author_and_skill_modes(self) -> None:
        claude = normalized(CLAUDE_AUTHOR.read_text(encoding="utf-8"))
        self.assertIn("Check both before mutation", claude)
        self.assertIn("--append-notes", claude)
        self.assertIn(' --actor "Claude"', claude)
        self.assertIn("Read back using `command bd show <id>` and confirm all six fields", claude)
        self.assertIn("Do not create a local stub `.beads/`", claude)
        self.assertIn("report an unknown/partial outcome", claude)
        self.assertIn("Preserve the existing title, type, labels, priority", claude)
        handoff = normalized(HANDOFF.read_text(encoding="utf-8"))
        self.assertIn("Match a `beads-work anchor:` note by issue ID, agent, branch, and worktree", handoff)
        for skill, actor in ((OPEN_SKILL, "OpenCode"), (CLAUDE_SKILL, "Claude")):
            with self.subTest(skill=skill):
                source = skill.read_text(encoding="utf-8")
                text = normalized(source)
                for clause in (
                    "parent already exists **inside the current worktree**",
                    "Never create a stub `.beads/`",
                    "--append-notes",
                    "Do not claim a successful handoff until readback confirms all six fields exactly",
                    "Match issue, agent, branch, and worktree first",
                    "Set `SHAS` to only the verified commits for this issue",
                    "**do not close** on a feature branch",
                    "append a dated cancellation note",
                ):
                    self.assertIn(clause, text)
                self.assertTrue(any(
                    "beads-work anchor:" in line and f'"agent":"{actor}"' in line
                    and f'--actor "{actor}"' in line
                    for line in source.splitlines()
                ))
        self.assertIn("unknown, <reason>", AUTHOR.read_text(encoding="utf-8"))
        self.assertIn("SHAS='<verified comma-separated SHAs for this issue>'", OPEN_SKILL.read_text(encoding="utf-8"))
        self.assertIn("SHAS='<verified comma-separated SHAs for this issue>'", CLAUDE_SKILL.read_text(encoding="utf-8"))

    def test_backlog_link_failure_cannot_authorize_handoff_or_duplicate_create(self) -> None:
        text = normalized(self.sources[BACKLOG])
        self.assertIn("If creation succeeds but linking fails, report the created ID separately", text)
        self.assertIn("Retries never authorize claiming issues, writing handoff state", text)
        self.assertIn("Do not claim issues.", text)
        self.assertIn("Do not write `.beads/in-progress-opencode.json`.", text)
        self.assertIn('Pass `--actor "OpenCode"` on every Beads write.', text)

    def test_platform_and_body_transport_boundaries_remain(self) -> None:
        for path, text in self.sources.items():
            with self.subTest(path=path):
                source = normalized(text)
                self.assertIn("On POSIX, `<bd>` means `command bd`", source)
                self.assertIn("In native Windows PowerShell, `<bd>` means `bd.exe`", source)
                self.assertIn("Needs body transport decision", source)
                self.assertIn("Do not create `/tmp` description files with heredocs", source)
                self.assertIn("caller explicitly approved a one-off", source)

    def test_author_container_mirror_is_identical(self) -> None:
        self.assertEqual(AUTHOR.read_bytes(), MIRROR.read_bytes())

    def test_other_container_mirrors_are_identical(self) -> None:
        mirror_root = ROOT / "private_Documents/development/container-dotfiles/dotfiles"
        for source, mirror in (
            (CLAUDE_AUTHOR, mirror_root / "dot_claude/agents/beads-issue-author.md"),
            (OPEN_SKILL, mirror_root / "private_dot_config/opencode/skills/beads-work/SKILL.md"),
            (CLAUDE_SKILL, mirror_root / "dot_claude/skills/beads-work/SKILL.md"),
        ):
            with self.subTest(source=source):
                self.assertEqual(source.read_bytes(), mirror.read_bytes())


if __name__ == "__main__":
    unittest.main()
