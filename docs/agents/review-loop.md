<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
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
  JSON with `timestamp`, `firstTimestamp`, `markedAt` (the last mark as a
  float, which orders an edit and a reviewer launch in the same second;
  gates without it round `timestamp` up a second), `sessionID`, and the
  accumulated repo-relative `files` list. Without Python or the helper
  script, the marker hook falls back to an unconditional mark in the
  legacy unsuffixed `.claude/.needs_dotfiles_review`.
- All four hooks pick their interpreter through
  `.claude/hooks/lib/resolve-python.sh`, which tries `python3`, `python`, then
  `py -3` and executes each candidate before accepting it. A lookup alone is
  not enough on native Windows, where the Microsoft Store app-execution alias
  for `python3` is in `PATH` but exits 49 with "Python was not found".
  `CLAUDE_REVIEW_GATE_PYTHON` prepends a candidate for debugging; it is probed
  like any other. The hooks do not `exec`, so a helper that starts and then
  fails reaches the same fallback as a missing interpreter — on Stop that
  means blocking rather than erroring open.
- `record-reviewer-start.sh` (SubagentStart) writes
  `.claude/.dotfiles_review_inflight.<session_id>.<agent_id>` (gitignored)
  holding the start time, only when `agent_type` is the configured
  `reviewerAgent`.
- `enforce-review-on-stop.sh` (Stop) blocks stopping while the session's
  gate exists, with a reviewer prompt scoped to the gated files. If the
  gated edits no longer exist in git (reverted) and nothing touching them
  was committed since the first mark, the gate is cleared instead.
  While a reviewer that started after the latest gated edit is still in
  flight, Stop does not block; it shows a "Dotfiles review in flight"
  notice instead. The finished background reviewer re-invokes the agent,
  and the next Stop re-checks the gate. A record older than 45 minutes, or
  one that started before the latest gated edit, does not count, and the
  block reason says so. The 45-minute bound also caps how long a reviewer
  killed without a SubagentStop can hold the gate open; the gate file
  itself survives either way.
  Escape hatch: `CLAUDE_ENFORCE_REVIEW=0|false|off`.
- `clear-needs-review-on-pass.sh` (SubagentStop) first deletes the stopping
  agent's in-flight record, whatever the verdict. It then clears the gate
  only when all of these hold:
  - `agent_type` is absent or is the configured reviewer.
  - If its start was recorded, the reviewer did not start before the
    latest gated edit. Without a record (the start hook failed, or Stop
    expired it after 45 minutes), only the PASS timestamp is checked.
  - The last transcript record carrying a marker ends with
    `DOTFILES_REVIEWER_RESULT=PASS` as its final meaningful line (exactly
    one marker; a quoted or mid-message marker does not count). A later
    FAIL or malformed marker outranks an earlier PASS.

  Verdicts are read from assistant text blocks and from the `message` input
  of `SubagentHandback` tool calls, which is how background subagents
  report. The main session transcript is read only when the payload names
  no agent transcript.

## Reviewer tool grants

The reviewer inspects tracked work from staged and unstaged `git diff` output
on both platforms. It uses `git status --short` to identify in-scope untracked
files and reads only those exact files. The two harnesses enforce that scope
differently:

- Claude Code (`.claude/agents/dotfiles-reviewer.md`): `tools: Bash, Read`.
  Claude subagent frontmatter has no per-agent bash allowlist, so
  "git commands only" is prompt-level — the agent's "Hard limits" section —
  and not enforced. `permissions.allow` in `.claude/settings.json` only
  controls auto-approval and is project-wide, so it does not narrow the
  reviewer; it does mean the reviewer inherits pre-approved mutating entries
  such as `Bash(git add:*)` and `Bash(git checkout:*)`. The prompt restricts
  `Read` to exact paths that scoped status reports as untracked.
- OpenCode (`.opencode/agents/dotfiles-reviewer.md`): `permission.bash` is
  deny-by-default with `git status*`, `git diff*`, and `git log*` allowed, and
  `edit`, `glob`, `grep`, and `task` denied. Here the shell and broad-discovery
  rules are hard-enforced. `read` retains inherited sensitive-file protections,
  while the prompt restricts it to exact paths that scoped status reports as
  untracked.

`Grep`/`Glob` are deliberately withheld on the Claude side and `grep`/`glob`
are denied on the OpenCode side, so the reviewer cannot fall back to scanning
the working tree when it should be reading scoped diffs or untracked files.

## Automation coverage review

The reviewer applies the coverage policy only to the scoped diff; it does not
scan the repository for unrelated gaps. Added, renamed, or removed production
automation must update `configs/automation-test-inventory.json`. New owned
automation needs registered evidence or a truthful planned/partial declaration
with a durable work item and rationale. Exclusions require repository ownership
and a rationale. Critical behavior changes require success, failure, and safety
requirements with matching evidence or explicit planned gaps. Existing truthful
gaps do not block unrelated changes.

See `docs/tooling/automation-coverage-policy.md` for the machine-enforced rules
and `docs/agents/ADDING_SCRIPTS.md` for the author checklist.

Claude Code snapshots subagent definitions at session start. Editing
`.claude/agents/dotfiles-reviewer.md` does not affect reviewer runs later in
the same session, so verify changes to it from a fresh session.

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
- Both harnesses ship the skill and the command, project-scoped: OpenCode under
  `.opencode/`, Claude Code under `.claude/`.
- Supported modes: `auto`, `human-only`, `agent-only`, `deep`.
- Optional target docs can be passed after the mode.
- If mode is omitted or unrecognized, the command defaults to `auto`.
- If reviewed docs are changed, run one more `@dotfiles-reviewer` pass.

## Related files

- Claude reviewer agent:
  `.claude/agents/dotfiles-reviewer.md`
- OpenCode reviewer agent:
  `.opencode/agents/dotfiles-reviewer.md`
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
- Claude interpreter resolver (sourced by all four hooks):
  `.claude/hooks/lib/resolve-python.sh`
- Claude marker hook:
  `.claude/hooks/mark-needs-review.sh`
- Claude stop hook:
  `.claude/hooks/enforce-review-on-stop.sh`
- Claude reviewer-start hook:
  `.claude/hooks/record-reviewer-start.sh`
- Claude clear hook:
  `.claude/hooks/clear-needs-review-on-pass.sh`
- OpenCode docs-refresh skill:
  `.opencode/skills/refresh-docs/SKILL.md`
- OpenCode docs-refresh command:
  `.opencode/commands/refresh-docs.md`
- Claude docs-refresh skill:
  `.claude/skills/refresh-docs/SKILL.md`
- Claude docs-refresh command:
  `.claude/commands/refresh-docs.md`
