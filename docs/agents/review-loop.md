<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Dotfiles Review Loop

This document explains when the dotfiles review gate should trigger, when it
should not, and how post-review docs refresh should behave.

## Review gate path policy

- Exempt from review-gate marking:
  - `docs/**` except `docs/agents/**`
  - `assets/README.md`
  - `.beads/**` (backlog/issue state, not dotfiles content)
- Still reviewed (normal review required):
  - `docs/agents/**`
  - `README.md`
  - `AGENTS.md`
  - `dot_claude/AGENTS.md`
- Any non-doc change remains reviewed as usual.

The policy is data-driven: `exemptPaths` in
`.opencode/opencode-tooling.config.jsonc` is the single source of truth,
read by both the OpenCode plugins and the Claude Code hooks.

## Claude Code gate flow

The Claude Code hooks in `.claude/hooks/` mirror the OpenCode plugins; the
shared logic lives in `.claude/hooks/lib/review_gate.py`.

- `mark-needs-review.sh` (PostToolUse, `Write|Edit`) reads the hook payload
  and raises a gate only when the edited file is inside the session's
  checkout (git-toplevel match, so nested worktrees under `worktrees/` gate
  independently), is not a review-loop runtime artifact, and is not exempt
  per `exemptPaths`. Edits outside the repo (`/tmp`, plan files) and
  backlog-only sessions never raise a gate.
- Gate file: `.claude/.needs_dotfiles_review.<session_id>` (gitignored),
  JSON with `timestamp`, `firstTimestamp`, `sessionID`, and the accumulated
  repo-relative `files` list.
- `enforce-review-on-stop.sh` (Stop) blocks stopping while the session's
  gate exists, with a reviewer prompt scoped to the gated files. If the
  gated edits no longer exist in git (reverted) and nothing touching them
  was committed since the first mark, the gate is cleared instead.
  Escape hatch: `CLAUDE_ENFORCE_REVIEW=0|false|off`.
- `clear-needs-review-on-pass.sh` (SubagentStop) clears the gate when a
  reviewer transcript ends with `DOTFILES_REVIEWER_RESULT=PASS` as its
  final meaningful line (exactly one marker; a quoted or mid-message
  marker does not count).

## Expected review loop behavior

1. Edit reviewed files or normal files.
2. Run `@dotfiles-reviewer` until the final line is exactly:
   `DOTFILES_REVIEWER_RESULT=PASS`
3. Only after PASS, assess documentation impact.
4. If docs are stale, invoke `refresh-docs` and make minimal updates.
5. If `refresh-docs` changes reviewed docs (`README.md`, `docs/agents/**`,
   `AGENTS.md`, `dot_claude/AGENTS.md`), run one more review pass.

## Docs refresh conventions

- Docs refresh is conditional and idempotent, not automatic on every PASS.
- Prefer small targeted edits over broad rewrites.
- Keep `README.md` concise; detailed operational content belongs under `docs/`.
- Keep `.opencode/journal.md` as working memory; promote only durable guidance
  into `docs/agents/`.
- Build completion requires a docs-impact assessment after review PASS before
  the task is considered complete.

## Debug logging

- Review gate PASS detection: set `DOTFILES_REVIEW_GATE_DEBUG=1`; logs to
  `.opencode/.dotfiles-review-gate.log`.
- Review marker decisions: set `DOTFILES_REVIEW_MARKER_DEBUG=1`; logs to
  `$XDG_STATE_HOME/opencode-tooling/` or `~/.local/state/opencode-tooling/`.
- Review enforcer decisions: set `DOTFILES_REVIEW_ENFORCER_DEBUG=1`; logs to
  `$XDG_STATE_HOME/opencode-tooling/` or `~/.local/state/opencode-tooling/`.
- If the state directory would be inside the repo, logs fall back to temp state.
- Restart OpenCode after changing debug environment variables.

## Manual command wrapper

- Use `/refresh-docs` as a manual wrapper around the `refresh-docs` skill.
- Supported modes: `auto`, `human-only`, `agent-only`, `deep`.
- Optional target docs can be passed after the mode.
- If mode is omitted or unrecognized, the command defaults to `auto`.
- If reviewed docs are changed, run one more `@dotfiles-reviewer` pass.

## Related files

- OpenCode Build prompt:
  `.opencode/prompts/build.md`
- OpenCode marker plugin:
  `.opencode/plugins/review-loop-marker.js`
- OpenCode enforcer plugin:
  `.opencode/plugins/review-loop-enforcer.js`
- OpenCode gate plugin:
  `.opencode/plugins/review-loop-gate.js`
- OpenCode config wiring:
  `.opencode/opencode.jsonc`
- Claude gate helper (mark/enforce/clear logic):
  `.claude/hooks/lib/review_gate.py`
- Claude marker hook:
  `.claude/hooks/mark-needs-review.sh`
- Claude stop hook:
  `.claude/hooks/enforce-review-on-stop.sh`
- Claude clear hook:
  `.claude/hooks/clear-needs-review-on-pass.sh`
- Skill:
  `.opencode/skills/refresh-docs/SKILL.md`
- Command:
  `.opencode/commands/refresh-docs.md`
