---
name: beads-work
description: Work on a Beads issue end-to-end (bd issue dots-*) — fetch with
  bd show, claim, plan, persist plan to bd, implement, commit, close with
  SHAs. Triggered by phrases like "work on the beads issue dots-go2",
  "I'd like to work on dots-cdr", "plan dots-arl", "implement dots-foo",
  or "continue work on dots-bar".
license: MIT
compatibility: opencode
metadata:
  audience: dotfiles-maintainer
  workflow: beads-work
---

# beads-work

Drive the full Beads issue loop for a `dots-*` ticket: fetch, claim, plan,
persist the approved plan back onto the issue, implement, commit, close with
commit SHAs, and refresh the JSONL export.

## Use this skill when

- The user names a Beads issue by ID, e.g. "work on dots-go2", "let's tackle
  the beads issue dots-cdr", "I'd like to plan dots-arl", or "implement
  dots-foo".
- The user says "continue work on dots-X" and `.beads/in-progress-*.json`
  exists.
- The user asks to close a `dots-*` ticket they have been working on in
  this session.

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

Run before step 1:

```bash
command -v bd >/dev/null || { echo "ERROR: bd not installed in this environment"; exit 1; }
```

This matters inside devcontainers where `bd` may not be present. Bail with a
clear message rather than failing opaquely on the first `bd show`.

## Workflow

### Step 1: Parse the issue ID

Accept any of these forms and normalize to lowercase `dots-<id>`:

- `dots-<id>` — use as-is, lowercased.
- `DOTS-<ID>` — lowercase the whole token.
- bare `<id>` (no prefix) — prepend `dots-`.

If the user invokes the skill with no ID, check for a resume state file:

```bash
ls .beads/in-progress-*.json 2>/dev/null
```

If exactly one exists, read its `id` field and offer to resume that issue.
If more than one exists, list them and ask which to resume.

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
```

Then write the harness-specific state file. Use `claude` or `opencode` as
the `<harness>` token:

```bash
cat > .beads/in-progress-claude.json <<JSON
{
  "id": "dots-<id>",
  "agent": "Claude",
  "started_sha": "${STARTED_SHA}",
  "started_at": "${STARTED_AT}"
}
JSON
```

OpenCode variant:

```bash
cat > .beads/in-progress-opencode.json <<JSON
{
  "id": "dots-<id>",
  "agent": "OpenCode",
  "started_sha": "${STARTED_SHA}",
  "started_at": "${STARTED_AT}"
}
JSON
```

This file is the contract between Plan-phase and Build-phase agents in
OpenCode and a resume anchor across sessions. It is gitignored.

### Step 5: Investigate scope

Read every file mentioned in the issue's description and design notes.
Honor the project rule from `CLAUDE.md`: check for related documentation
under `./docs/` and look at existing patterns in adjacent files before
writing new code.

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
bd update dots-<id> \
  --design-file - \
  --acceptance "<one-line acceptance summary>" \
  --append-notes "Plan approved $(date -u +%Y-%m-%d); design notes updated." \
  --actor "Claude" <<'EOF'
<paste the full approved plan markdown here>
EOF
```

OpenCode:

```bash
bd update dots-<id> \
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

### Step 8: Implement per the approved plan

Edit files as the plan specifies. Honor the post-edit verification rules
from this repo's `CLAUDE.md` for every touched file:

```bash
./assets/cz-audit.sh check <repo-relative-path>
```

After all edits, run:

```bash
chezmoi doctor
```

Fix any audit failures before continuing. A vault error from
`chezmoi doctor` is the documented exception and may be ignored.

### Step 9: Commit using the harness wrapper

Never call `git commit` directly. Use the wrapper for the running harness —
`cc-commit` for Claude Code, `oc-commit` for OpenCode.

The subject and body **format** is set by the **repo**, not the harness.
Read the repo's `CLAUDE.md` or `AGENTS.md` for its documented commit
workflow before drafting the message. Defaults when nothing is documented:

- Subject ≤50 chars, imperative mood, no trailing period.
- Blank second line.
- Body (optional): bulleted `- ` lines, each <80 chars.

Some repos (e.g. `homelab-IaC`) keep the same 50/72 body structure but use
Conventional Commits for the subject (`type(scope): subject`). If the
repo's docs say so, follow that.

Include a `Refs: dots-<id>` trailer in the commit body so the link to the
Beads issue survives even if state files are lost.

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
size). Both short and full-40 forms are valid per the existing `dots-arl`
precedent in this repo.

### Step 11: Close the issue with the SHAs

Build a comma-separated SHA list and close the issue. Run the literal
command for the harness:

Claude Code:

```bash
bd close dots-<id> \
  --reason "Fixed with commit(s) <sha1>[, <sha2>...]" \
  --actor "Claude"
```

OpenCode:

```bash
bd close dots-<id> \
  --reason "Fixed with commit(s) <sha1>[, <sha2>...]" \
  --actor "OpenCode"
```

### Step 12: Refresh the JSONL export and delete the state file

Repo history shows a recurring `chore(beads): Update issues.jsonl` commit
after every closed issue. Reproduce it:

```bash
bd export > .beads/issues.jsonl
```

Confirm exact flags against `bd export --help` if `bd` reports an
unexpected schema. Then commit via the harness wrapper:

- Claude Code: `cc-commit -m "chore(beads): Update issues.jsonl"`.
- OpenCode: `oc-commit -m "chore(beads): Update issues.jsonl"`.

Finally, remove the state file:

```bash
rm -f .beads/in-progress-claude.json   # Claude Code
rm -f .beads/in-progress-opencode.json # OpenCode
```

### Step 13: Final report

Summarize:

- The issue ID and one-line title.
- Every commit SHA produced (8-char form is fine).
- Verification that `bd show dots-<id>` reports `status=closed` with the
  expected close reason.
- Confirmation that the state file was removed and `.beads/issues.jsonl`
  was refreshed and committed.

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
bd update dots-<id> --status open --assignee "" --actor "Claude"
rm -f .beads/in-progress-claude.json
```

OpenCode:

```bash
bd update dots-<id> --status open --assignee "" --actor "OpenCode"
rm -f .beads/in-progress-opencode.json
```

### Conflicting state file from a different agent

If `.beads/in-progress-claude.json` exists while running under OpenCode (or
vice versa), do not overwrite silently. Surface the conflict, show the
contents of the existing file, and ask whether to abandon the other
agent's claim before proceeding.

### Audit failure mid-implementation

If `./assets/cz-audit.sh check ...` exits non-zero or prints an `ERROR:`
line, fix or revert the offending change. Do not close the issue on a
broken state. The state file stays in place so the work can resume.

### Issue not found

`bd show dots-<id>` returns no such issue → stop and ask the user whether
the ID is correct or whether to create a new issue first.

### OpenCode plan-to-build handoff

When OpenCode switches from the Plan agent to the Build agent mid-issue,
the Build agent re-discovers state from `bd show dots-<id>` plus
`.beads/in-progress-opencode.json`. If the Build agent loses skill
context, the user can re-invoke with "continue work on dots-<id>" and
the resume path in step 1 picks it up.

## Output / final report

```markdown
## Beads issue dots-<id>: <title>

- Status: closed
- Started SHA: <8-char>
- Commits: <sha1>, <sha2>, ...
- Close reason: Fixed with commit(s) <sha1>[, <sha2>...]
- JSONL export refreshed: yes (commit <sha>)
- State file removed: yes
```
