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
- The user says "continue work on <prefix>-<id>" and a matching state file
  or Beads tracking note exists.
- The user asks to close a `<prefix>-<id>` ticket they have been working on
  in this session.

## Do not use this skill when

- The user mentions Beads only abstractly (e.g. "what is Beads?") without
  naming an issue ID.
- The work item is tracked elsewhere (GitHub issue, Jira) and not in `bd`.
- The user only wants to inspect issues without claiming or modifying them
  (just run `command bd show` or `command bd list` directly).
- The work originated from a Claude Code plan-mode session with no
  pre-existing issue ID. Use the repo's "Beads plan handoff" protocol
  (in `.claude/CLAUDE.md`) and the `beads-issue-author` subagent instead
  — that flow creates or attaches the issue, then this skill can pick up
  the resulting ID for closure.

## Harness identity

This skill runs under two harnesses. Pick the right literal name everywhere
the workflow shows `Claude` or `OpenCode`:

- Claude Code: `Claude` (matches `bin/executable_cc-commit`).
- OpenCode: `OpenCode` (matches `bin/executable_oc-commit`).

Detect the harness from the running agent's identity. If unsure, ask the user
once at the start of the session and reuse the answer.

## Preflight

Confirm the native `bd` executable is available:

```bash
command -v bd >/dev/null || { echo "ERROR: bd not installed in this environment"; exit 1; }
```

This bails out cleanly inside environments (some devcontainers) where `bd`
isn't installed. Do not source shell rc files or `beads-helpers.*`; they are
interactive wrappers and are not an agent dependency. Invoke every Beads
command below as `command bd ...` on POSIX or `bd.exe ...` on native Windows.
When synchronizing a repository that documents a guarded sync helper, invoke
that helper explicitly instead of native `bd dolt pull` or `bd dolt push`.

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

Before Step 3 changes an issue, inspect its existing tracking notes and any
in-progress files for this harness and the other harness; ask on mismatch.
If no ID was supplied, inspect existing `.beads/in-progress-*.json` files
inside this worktree (if `.beads/` exists) and use `command bd list` (or
`bd.exe list` on Windows) to find in-progress issues with tracking notes.
Offer matching candidates; ask when none or more than one matches. State
files and notes are collision signals, not authority. Read `id`, `agent`,
`branch`, `worktree_path`, `started_sha`, and `started_at`; match the issue ID,
actual Git branch, and worktree root against `bd show`. Multiple anchors for
the same issue/branch/worktree, stale anchors, or conflicting files require
clarification. Never silently continue or overwrite another session's state.

### Step 2: Fetch the issue

```bash
command bd show <id>
```

Read the title, description, acceptance criteria, design notes, status, and
dependencies. Refuse to proceed and surface to the user when:

- Status is `closed` — ask whether to `command bd reopen` or abort.
- Status is `in_progress` and the assignee is not the current agent
  identity — surface the conflict and ask before claiming.
- The issue has unmet open dependencies — list them and ask whether to
  switch to a dependency first.

### Step 3: Claim the issue

Run the literal command matching the harness:

Claude Code:

```bash
command bd update <id> --claim --actor "Claude" --assignee "Claude"
```

OpenCode:

```bash
command bd update <id> --claim --actor "OpenCode" --assignee "OpenCode"
```

The `--actor` flag overrides the audit-trail default (which would otherwise
fall through to `git user.name`, i.e. the human). The explicit `--assignee`
is belt-and-suspenders against any change in `--claim` semantics. The
assignee field is a plain string; no email is required.

### Step 4: Record the in-progress anchor

Capture the current HEAD as the "started at" anchor so close-time SHA
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

In a disposable nested Git worktree, Beads 1.2.2 resolved its parent database
both without a local `.beads/` and with an empty `.beads/` stub. This is not
permission to create a stub: discovery behavior can vary by version and an
empty directory is not a durable handoff anchor.

Recheck the harness-specific state file and notes immediately before
recording. Only write a state file when its `.beads/` parent already exists
**inside the current worktree**, is
writable by this session, and there is no conflicting anchor. Never create a
stub `.beads/`, follow an out-of-worktree symlink, or write another checkout's
file. If any existing state/notes disagree with issue, agent, branch, worktree,
or started SHA, stop and ask; do not replace them. Use `claude` or `opencode`
as the `<harness>` token for the eligible state file:

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

When the parent is absent or inaccessible, or only another checkout is
writable, **skip the file**. Append a structured, single-line record to the
*issue's* notes with the selected actor, then read it back using `bd show`.
Use the literal command for the current platform (`command bd` on POSIX;
`bd.exe` on native Windows), substituting actual values:

```bash
command bd update <id> --append-notes 'beads-work anchor: {"id":"<id>","agent":"Claude","branch":"<actual branch>","worktree_path":"<absolute worktree root>","started_sha":"<full SHA>","started_at":"<UTC ISO 8601>"}' --actor "Claude"
```

Use `OpenCode` as both `agent` and `--actor` in OpenCode. Do not claim a
successful handoff until readback confirms all six fields exactly. If the
append succeeds but readback fails, report an unknown outcome and reconcile
before retrying; do not append a second anchor speculatively. Notes preserve
existing description, design, acceptance, status, and assignee.

The file, when present, is the resume anchor between agents; the verified
note is its equivalent in isolated sessions. Never infer an anchor from the
latest Git HEAD on resume. This repo's `AGENTS.md` requires closure only
after landing on main, regardless of the generic close step below.
For a worktree merge, use the `worktree-merge` skill: when the authoritative
helper advertises `inspect --beads-issue`, inspect the explicit issue ID and
pass `--close-beads` only for a matching Claude claim. The helper handles both
file and note anchors, verifies closure after landing, and retains note history.
Do not separately close a note-backed issue before the merge lands.
On native Windows use the Write tool for an eligible local JSON file; invoke
`bd.exe update`/`bd.exe show` in PowerShell for note mode. Do not run the POSIX
shell examples in PowerShell.

When a workflow creates or attaches an issue from an approved Plannotator plan,
the state file may also include `plan_source` and `plan_sha256`. Use those
fields as extra collision checks. If the plan source/fingerprint, branch, or
worktree does not match the current work, ask before proceeding. In note mode,
read the approved design back from `bd show` and check issue/branch/worktree
and any plan source recorded in notes before implementation.

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
command bd update <id> \
  --design-file - \
  --append-notes "Plan approved $(date -u +%Y-%m-%d); design notes updated." \
  --actor "Claude" <<'EOF'
<paste the full approved plan markdown here>
EOF
```

OpenCode:

```bash
command bd update <id> \
  --design-file - \
  --append-notes "Plan approved $(date -u +%Y-%m-%d); design notes updated." \
  --actor "OpenCode" <<'EOF'
<paste the full approved plan markdown here>
EOF
```

Use `--design-file` (not `--description-file` or `--body-file`) so the
original problem statement is preserved while the agreed implementation
plan lands in the design field.

Preserve existing acceptance criteria unless the user explicitly approved
replacement. Put new verification detail in design; add `--acceptance` only
with explicit approval.

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

Do not rely on conversation memory. Read and verify the matching `started_sha`
from this issue's state file **or** readback-confirmed `beads-work anchor` note.
Match issue, agent, branch, and worktree first. Enumerate commits from that
anchor to `HEAD`; inspect each commit's `Refs: <id>` trailer and exclude
commits belonging to other issues. If the range is ambiguous, stop and
reconcile rather than attaching another issue's SHA. Capture the exact SHA
list now, before Step 11 removes the state file:

```bash
STARTED_SHA="$(jq -r .started_sha .beads/in-progress-claude.json)"
git log "${STARTED_SHA}..HEAD" --format='%H %s%n%b'
SHAS='<verified comma-separated SHAs for this issue>'
```

For note mode, substitute the verified note's `started_sha` for the `jq`
line; on OpenCode use its state path. Set `SHAS` to only the verified commits
for this issue. Prefer full SHA values in a dedicated
issue note including `id`, `branch`, `worktree_path`, `started_sha`, and
`commits`, then read it back before switching to the next issue. Never derive
the next issue's anchor from a previous issue's notes.

### Step 11: Delete the state file

If this issue has a harness-specific state file, verify its issue, branch,
worktree, and started SHA match the confirmed anchor. Remove it **before**
closing the issue or freeing the slot for another issue:

```bash
command rm -f -- .beads/in-progress-claude.json   # Claude Code
command rm -f -- .beads/in-progress-opencode.json # OpenCode
```

Use `command rm`, not bare `rm`. Prezto aliases `rm` to `nocorrect rm -i` in
interactive zsh; in an agent shell that prompt can read EOF, leave the file
in place, and still exit 0. This delete is a safety property, not a tidy-up,
so verify it rather than assuming it, for whichever file you removed:

```bash
STATE_FILE=.beads/in-progress-claude.json   # or -opencode.json
if [ -e "$STATE_FILE" ]; then
  echo "ABORT: $STATE_FILE still present; do not close the issue" >&2
  exit 1
fi
```

In note mode, do not create or remove a state file. For an issue switch in a
repo that defers closure until main, keep the first issue open: verify its
per-issue note and commits, remove only its matching state file, and ask before
overwriting or adopting a conflicting file. The next issue receives a new
anchor at its own claim SHA.

Do not proceed to Step 12 while the file is still there — closing on top of a
surviving state file is the exact pairing this ordering exists to prevent.

The order matters. An interruption between the delete and the close leaves
(open issue, no state file), which is recoverable — the plan-approval gate
prompts and the issue is still visibly open. The reverse order leaves
(closed issue, stale state file), which used to disable the Claude Code
plan gate silently and indefinitely.

Confirm the per-issue SHA list from Step 10 is still available before
continuing; if the shell was lost, recover it from the readback-confirmed
issue note and `git log` rather than recreating a state file.

### Step 12: Close the issue with the SHAs

Follow the repo's landing policy before this step: in this dotfiles repo,
**do not close** on a feature branch; close only after the issue's commits
are verified on main. Close the issue with its own comma-separated SHA list
from Step 10. If `SHAS` is
empty — no commits since the claim — close with a reason that says so rather
than an empty list. Run the literal command for the harness:

Claude Code:

```bash
command bd close <id> \
  --reason "Fixed with commit(s) <sha1>[, <sha2>...]" \
  --actor "Claude"
```

OpenCode:

```bash
command bd close <id> \
  --reason "Fixed with commit(s) <sha1>[, <sha2>...]" \
  --actor "OpenCode"
```

If the repo's Beads conventions (documented in `CLAUDE.md`/`AGENTS.md`)
require refreshing or committing `.beads/issues.jsonl` after close, do so.
Some repos disable JSONL auto-export entirely (e.g. Dolt-backed setups
where Dolt is the source of truth) and ignore the file — defer to the
repo's docs.

### Step 13: Final report

Summarize:

- The issue ID and one-line title.
- Every commit SHA produced (8-char form is fine).
- Verification that `command bd show <id>` reports `status=closed` with the
  expected close reason.
- Confirmation that its state file was removed, or note mode used no file.

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
command bd update <id> --status open --assignee "" --actor "Claude"
```

OpenCode:

```bash
command bd update <id> --status open --assignee "" --actor "OpenCode"
```

Read back the release and remove **only** this issue's matching state file;
verify removal. In note mode, append a dated cancellation note with the same
actor and read it back; never delete issue history or remove another session's
file. An ambiguous or failed release is a partial outcome requiring read-only
reconciliation, not a reason to claim another issue.

### Conflicting state file from a different agent

If `.beads/in-progress-claude.json` exists while running under OpenCode (or
vice versa), do not overwrite silently. Surface the conflict, show the
contents of the existing file, and ask whether to abandon the other
agent's claim before proceeding.

### Audit failure mid-implementation

If the repo's post-edit verification command exits non-zero (or surfaces an
`ERROR:` line, depending on its convention), fix or revert the offending
change. Do not close the issue on a broken state. The state file or verified
note remains available for resume.

### Issue not found

`command bd show <id>` returns no such issue → stop and ask the user whether
the ID is correct or whether to create a new issue first.

### OpenCode plan-to-build handoff

When OpenCode switches from the Plan agent to the Build agent mid-issue,
the Build agent re-discovers state from `command bd show <id>` plus a matching
`.beads/in-progress-opencode.json` or verified issue note. If the Build agent
loses skill context, the user can re-invoke with "continue work on <id>";
Step 1 checks the available anchor and collisions.

## Output / final report

```markdown
## Beads issue <id>: <title>

- Status: closed
- Started SHA: <8-char>
- Commits: <sha1>, <sha2>, ...
- Close reason: Fixed with commit(s) <sha1>[, <sha2>...]
- Tracking: state file removed | note readback confirmed (no file)
```
