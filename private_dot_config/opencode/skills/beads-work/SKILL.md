---
name: beads-work
description: Work on a Beads issue end-to-end (bd issue <prefix>-*) — fetch
  with bd show, claim, plan, persist plan to bd, implement, commit, close
  with SHAs. Triggered by phrases like "work on the beads issue dots-go2",
  "I'd like to work on slab-cdr", "plan abc-arl", "implement foo-bar", or
  "continue work on dots-bar".
license: MIT
compatibility: opencode
metadata:
  audience: agent
  workflow: beads-work
---

# beads-work

Drive the full Beads issue loop for any `<prefix>-<id>` ticket: fetch, claim,
plan, persist the approved plan back onto the issue, implement, commit, close
with commit SHAs. Repo-specific verification and commit format rules live in
the repo's own `CLAUDE.md`/`AGENTS.md` — this skill defers to them rather
than hard-coding details for any one project.

## Use this skill when

- The user names a Beads issue by ID, e.g. "work on dots-go2", "let's tackle
  the beads issue slab-cdr", "I'd like to plan abc-arl", or "implement
  foo-bar".
- The user says "continue work on <prefix>-<id>" and
  `.beads/in-progress-*.json` exists.
- The user asks to close a `<prefix>-<id>` ticket they have been working on
  in this session.

## Do not use this skill when

- The user mentions Beads only abstractly (e.g. "what is Beads?") without
  naming an issue ID.
- The work item is tracked elsewhere (GitHub issue, Jira) and not in `bd`.
- The user only wants to inspect issues without claiming or modifying them
  (just run `bd show` or `bd list` directly).

## Harness identity

This skill runs under two harnesses. Pick the right literal name everywhere
the workflow shows `Claude` or `OpenCode`:

- Claude Code: `Claude` (matches `bin/executable_cc-commit`).
- OpenCode: `OpenCode` (matches `bin/executable_oc-commit`).

Detect the harness from the running agent's identity. If unsure, ask the user
once at the start of the session and reuse the answer.

## Preflight

Confirm `bd` is available and load the noise-filter wrapper:

```bash
command -v bd >/dev/null || { echo "ERROR: bd not installed in this environment"; exit 1; }
[ -r "$HOME/.local/share/beads-helpers.bash" ] && . "$HOME/.local/share/beads-helpers.bash"
```

The first line bails out cleanly inside environments (some devcontainers)
where `bd` isn't installed. The second line sources the bash wrapper that
filters dolt `auto-importing`/`auto-imported` lines from `bd` output so they
don't distract you mid-workflow. The wrapper is a no-op if it isn't present.

Every `bd` invocation in the rest of this skill assumes the wrapper has been
sourced. If you spawn a fresh non-interactive bash (e.g. via the Bash tool
for a one-off command), re-source the helper at the top of that command.

## Workflow

### Step 1: Parse and normalize the issue ID

Accept any of these forms and normalize to lowercase `<prefix>-<id>`:

- `<PREFIX>-<ID>` — lowercase the whole token.
- `<prefix>-<id>` — use as-is, lowercased.
- bare `<id>` (no prefix) — determine the project prefix:
  1. Check `.beads/metadata.json` for `dolt_database` (Dolt-backed Beads
     deployments use this as the prefix). If present, prepend it.
  2. Otherwise, ask the user for the prefix once and reuse it for the rest
     of the session.

If the user invokes the skill with no ID, check for a resume state file:

```bash
ls .beads/in-progress-*.json 2>/dev/null
```

Treat state files as resume candidates, not authority. If exactly one exists,
read its `id`, `branch`, `worktree_path`, and `started_sha` fields when
present, then offer to resume that issue. If the branch/worktree metadata does
not match the current session, call that out as a possible collision and ask.
If more than one exists, list them and ask which to resume. Never silently
continue or overwrite another session's state.

### Step 2: Fetch the issue

```bash
bd show <id>
```

Read the title, description, acceptance criteria, design notes, status, and
dependencies. Refuse to proceed and surface to the user when:

- Status is `closed` — ask whether to `bd reopen` or abort.
- Status is `in_progress` and the assignee is not the current agent
  identity — surface the conflict and ask before claiming.
- The issue has unmet open dependencies — list them and ask whether to
  switch to a dependency first.

### Step 3: Claim the issue

Run the literal command matching the harness:

Claude Code:

```bash
bd update <id> --claim --actor "Claude" --assignee "Claude"
```

OpenCode:

```bash
bd update <id> --claim --actor "OpenCode" --assignee "OpenCode"
```

The `--actor` flag overrides the audit-trail default (which would otherwise
fall through to `git user.name`, i.e. the human). The explicit `--assignee`
is belt-and-suspenders against any change in `--claim` semantics. The
assignee field is a plain string; no email is required.

### Step 4: Write the in-progress state file

Capture the merge-target HEAD as the "started at" anchor so close-time SHA
collection works regardless of how many agent transitions occur:

```bash
STARTED_SHA="$(git rev-parse HEAD)"
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
WORKTREE_PATH="$(git rev-parse --show-toplevel)"
```

`BRANCH` is the actual Git branch. It is never the worktree directory name,
Agent of Empires session name, or `ai-wt` path suffix. `WORKTREE_PATH` is the
Git worktree root, even when the agent starts from a subdirectory.

Then write the harness-specific state file. Use `claude` or `opencode` as
the `<harness>` token:

```bash
cat >| .beads/in-progress-claude.json <<JSON
{
  "id": "<id>",
  "agent": "Claude",
  "started_sha": "${STARTED_SHA}",
  "started_at": "${STARTED_AT}",
  "branch": "${BRANCH}",
  "worktree_path": "${WORKTREE_PATH}"
}
JSON
```

OpenCode variant:

```bash
cat >| .beads/in-progress-opencode.json <<JSON
{
  "id": "<id>",
  "agent": "OpenCode",
  "started_sha": "${STARTED_SHA}",
  "started_at": "${STARTED_AT}",
  "branch": "${BRANCH}",
  "worktree_path": "${WORKTREE_PATH}"
}
JSON
```

This file is the contract between Plan-phase and Build-phase agents in
OpenCode and a resume anchor across sessions. It is gitignored.

When a workflow creates or attaches an issue from an approved Plannotator plan,
the state file may also include `plan_source` and `plan_sha256`. Use those
fields as extra collision checks. If the plan source/fingerprint, branch, or
worktree does not match the current work, ask before proceeding.

### Step 5: Investigate scope

Read every file mentioned in the issue's description and design notes.
Honor the project rules in the repo's `CLAUDE.md`/`AGENTS.md` for
documentation lookups and existing-pattern checks before writing new code.

### Step 6: Draft a plan and request approval

- Claude Code: enter plan mode and surface the plan through `ExitPlanMode`.
- OpenCode: follow the plan-agent flow at
  `private_dot_config/opencode/prompts/plan-agent.md`.

Do not start writing files until the user approves.

### Step 7: Persist the approved plan onto the issue

Once the user approves, and before implementation, write the plan text
into the Beads database so it survives across machines and agents. Use
`--design-file -` to read from stdin via heredoc — do not reference a
file path on disk:

Claude Code:

```bash
bd update <id> \
  --design-file - \
  --acceptance "<one-line acceptance summary>" \
  --append-notes "Plan approved $(date -u +%Y-%m-%d); design notes updated." \
  --actor "Claude" <<'EOF'
<paste the full approved plan markdown here>
EOF
```

OpenCode:

```bash
bd update <id> \
  --design-file - \
  --acceptance "<one-line acceptance summary>" \
  --append-notes "Plan approved $(date -u +%Y-%m-%d); design notes updated." \
  --actor "OpenCode" <<'EOF'
<paste the full approved plan markdown here>
EOF
```

Use `--design-file` (not `--description-file` or `--body-file`) so the
original problem statement is preserved while the agreed implementation
plan lands in the design field.

If the approved plan is being attached to an existing issue that already has
design content, preserve the previous content below the newly approved plan
instead of discarding it:

```markdown
<approved plan content>

--------------------------

<previous content preserved for reference>
```

Do not replace existing acceptance criteria unless the user explicitly approved
that replacement. Put new verification detail in design notes when acceptance
should be preserved.

### Step 8: Implement per the approved plan

Edit files as the plan specifies. Run the project's post-edit verification
per the repo's `CLAUDE.md`/`AGENTS.md` — the repo will document its own
command (e.g. an audit script, a lint pass, a test command, a build).
Fix any failures before continuing. If the repo defines no such step, run
whatever lint/test commands are conventional for that project.

### Step 9: Commit using the harness wrapper

Never call `git commit` directly. Use the wrapper for the running harness —
`cc-commit` for Claude Code, `oc-commit` for OpenCode — so the commit is
attributed to the agent rather than the human's git identity.

The subject and body **format** is set by the **repo**, not the harness.
Read the repo's `CLAUDE.md`/`AGENTS.md` for its documented commit workflow
(50/72 rule, Conventional Commits, etc.) before drafting the message.

Include a `Refs: <id>` trailer in the commit body so the link to the Beads
issue survives even if state files are lost.

Repeat for each logical commit the plan requires.

### Step 10: Collect commit SHAs since claim

Do not rely on conversation memory. Read `started_sha` from the state file
and enumerate every commit between then and `HEAD`:

```bash
STARTED_SHA="$(jq -r .started_sha .beads/in-progress-claude.json)"
git log "${STARTED_SHA}..HEAD" --format=%h
```

Use the OpenCode state file path when running under OpenCode. Use whatever
short SHA `--format=%h` produces (typically 7–12 chars depending on repo
size). Both short and full-40 forms are valid.

### Step 11: Close the issue with the SHAs

Build a comma-separated SHA list and close the issue. Run the literal
command for the harness:

Claude Code:

```bash
bd close <id> \
  --reason "Fixed with commit(s) <sha1>[, <sha2>...]" \
  --actor "Claude"
```

OpenCode:

```bash
bd close <id> \
  --reason "Fixed with commit(s) <sha1>[, <sha2>...]" \
  --actor "OpenCode"
```

### Step 12: Delete the state file

If the repo's Beads conventions (documented in `CLAUDE.md`/`AGENTS.md`)
require refreshing or committing `.beads/issues.jsonl` after close, do so.
Some repos disable JSONL auto-export entirely (e.g. Dolt-backed setups
where Dolt is the source of truth) and ignore the file — defer to the
repo's docs.

Remove the harness-specific state file:

```bash
rm -f .beads/in-progress-claude.json   # Claude Code
rm -f .beads/in-progress-opencode.json # OpenCode
```

### Step 13: Final report

Summarize:

- The issue ID and one-line title.
- Every commit SHA produced (8-char form is fine).
- Verification that `bd show <id>` reports `status=closed` with the
  expected close reason.
- Confirmation that the state file was removed.

## Edge cases

### Plan rejection

OpenCode does not have a Claude-style `ExitPlanMode` rejection UI, so the
release-claim path cannot rely on a platform signal. Recognize these user
phrases as release signals in either harness:

- `no`
- `abort`
- `cancel`
- `stop`
- `don't do this`
- `skip this`

On Claude Code also treat a declined `ExitPlanMode` as a release signal.
Either signal triggers the release sequence:

Claude Code:

```bash
bd update <id> --status open --assignee "" --actor "Claude"
rm -f .beads/in-progress-claude.json
```

OpenCode:

```bash
bd update <id> --status open --assignee "" --actor "OpenCode"
rm -f .beads/in-progress-opencode.json
```

### Conflicting state file from a different agent

If `.beads/in-progress-claude.json` exists while running under OpenCode (or
vice versa), do not overwrite silently. Surface the conflict, show the
contents of the existing file, and ask whether to abandon the other
agent's claim before proceeding.

### Audit failure mid-implementation

If the repo's post-edit verification command exits non-zero (or surfaces an
`ERROR:` line, depending on its convention), fix or revert the offending
change. Do not close the issue on a broken state. The state file stays in
place so the work can resume.

### Issue not found

`bd show <id>` returns no such issue → stop and ask the user whether
the ID is correct or whether to create a new issue first.

### OpenCode plan-to-build handoff

When OpenCode switches from the Plan agent to the Build agent mid-issue,
the Build agent re-discovers state from `bd show <id>` plus
`.beads/in-progress-opencode.json`. If the Build agent loses skill
context, the user can re-invoke with "continue work on <id>" and
the resume path in step 1 picks it up.

## Output / final report

```markdown
## Beads issue <id>: <title>

- Status: closed
- Started SHA: <8-char>
- Commits: <sha1>, <sha2>, ...
- Close reason: Fixed with commit(s) <sha1>[, <sha2>...]
- State file removed: yes
```
