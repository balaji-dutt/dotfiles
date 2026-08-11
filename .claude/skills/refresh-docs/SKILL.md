---
name: refresh-docs
description: Assess and refresh stale docs after a dotfiles review PASS with
  minimal, deterministic edits. Use after DOTFILES_REVIEWER_RESULT=PASS to
  decide docs impact (none/human/agent/both) and update only the stale files.
  Triggered by "refresh docs", "are the docs stale", "/refresh-docs", or the
  post-review docs-impact assessment.
license: Proprietary
compatibility: claude-code
metadata:
  audience: dotfiles-maintainers
  workflow: post-review-doc-refresh
---

# refresh-docs

Deterministically refreshes stale documentation after implementation work has
already passed the required dotfiles review flow.

## Use this skill when

- `DOTFILES_REVIEWER_RESULT=PASS` was produced for the non-exempt changes.
- The latest diff likely changed operator behavior, workflows, or docs surface.
- You need an explicit docs-impact decision (`none`, `human`, `agent`, `both`).
- A human explicitly asks to refresh docs.

## Do not use this skill when

- The task itself is already docs-only wording/typo cleanup.
- You are trying to rewrite large docs sections without evidence from code diffs.
- You need API reference generation or inline code comments.

## Inputs

- Changed paths and/or a focused diff.
- Optional mode: `auto` (default), `human-only`, `agent-only`, `deep`.
- Optional explicit target docs list.

## Path policy for this repository

- Exempt human docs from review marking:
  - `docs/**` except `docs/agents/**`
  - `assets/README.md`
- Reviewed docs (normal review required):
  - `docs/agents/**`
  - `README.md`
  - `AGENTS.md`
  - `dot_claude/AGENTS.md`

`exemptPaths` in `.opencode/opencode-tooling.config.jsonc` is the single source
of truth; the Claude Code hooks (`.claude/hooks/lib/review_gate.py`) and the
OpenCode plugins both read it. The lists above are its docs subset — it also
exempts non-docs paths such as `.beads/**`.

## Workflow

1. Read changed files and identify behavior/workflow/documentation impact.
2. Decide docs impact class:
   - `none` = no documentation drift found.
   - `human` = only human-facing docs drift found.
   - `agent` = only agent-facing docs drift found.
   - `both` = both classes drift found.
3. Select the smallest stale-doc set and update only those files.
4. Keep edits minimal and idempotent:
   - no broad rewrites
   - no formatting-only churn
   - preserve existing doc structure unless clearly wrong
5. Re-read edited docs for correctness and link/path consistency.
6. If the refresh edited any reviewed doc, run `@dotfiles-reviewer` once more
   (Agent tool, `subagent_type: dotfiles-reviewer`). The `SubagentStop` hook
   `.claude/hooks/clear-needs-review-on-pass.sh` clears the gate on
   `DOTFILES_REVIEWER_RESULT=PASS`.
7. Return one terminal result marker as the final line.

## Output markers (final line)

- `DOCS_RESULT=NO_CHANGES`
- `DOCS_RESULT=UPDATED_HUMAN`
- `DOCS_RESULT=UPDATED_AGENT`
- `DOCS_RESULT=UPDATED_BOTH`
- `DOCS_RESULT=BLOCKED`

## Guardrails

- Never auto-edit docs from hooks; this skill is explicitly invoked.
- Do not refresh docs after every PASS; only when impact is present.
- Treat `README.md` as a concise front door, not a deep runbook.
- Do not dump the repo journal (`.opencode/journal.md`, or `.claude/journal.md`
  where present) into `docs/agents/`; promote only durable
  decisions/conventions/recurring gotchas.
