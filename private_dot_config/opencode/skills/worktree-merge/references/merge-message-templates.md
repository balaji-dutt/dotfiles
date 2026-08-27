# Merge commit message templates

Two worked examples for the `no-ff` merge commit message in step 5 of
`worktree-merge`. Describe **what the branch did**, not "Merge branch X"
boilerplate.

The subject/body **format** below is the repo's call (read its `CLAUDE.md`
or `AGENTS.md`). The agent **identity** (Claude vs OpenCode) is the
harness's call. The two are independent: a Claude session merging into
`homelab-IaC` writes a Conventional Commits subject; an OpenCode session
merging into this dotfiles repo writes a plain imperative subject. Each
example below shows both harness invocations of the same message so the
independence is explicit.

## Example 1: This dotfiles repo (50/72, plain imperative subject)

Branch under merge: `feat/beads-skill` with 3 commits adding a new skill.

`git log main..feat/beads-skill --oneline`:

```
ab12cd3 Add beads-work SKILL.md
ef45678 Add resume path for in-progress state
9a0b1c2 Gitignore .beads/in-progress-*.json
```

`git diff --stat main...feat/beads-skill`:

```
 .beads/.gitignore                              |   1 +
 dot_claude/skills/beads-work/SKILL.md          | 280 ++++++++++++++++++
 2 files changed, 281 insertions(+)
```

Drafted message:

- Subject (≤50 chars):

  ```
  Add beads-work skill for dots-* issue loop
  ```

- Blank line.

- Body (each line <80 chars, bulleted):

  ```
  - Encode claim/plan/implement/commit/close loop using bd CLI
  - Add resume path via .beads/in-progress-<harness>.json state file
  - Gitignore in-progress-*.json so state files do not commit
  - 3 commits, 281 insertions across 2 files
  ```

Full invocation under Claude Code:

```bash
"<merge-helper>" no-ff --actor claude \
  -m "Add beads-work skill for dots-* issue loop" \
  -m "- Encode claim/plan/implement/commit/close loop using bd CLI
- Add resume path via .beads/in-progress-<harness>.json state file
- Gitignore in-progress-*.json so state files do not commit
- 3 commits, 281 insertions across 2 files"
```

Same message under OpenCode (only the identity env vars change):

```bash
"<merge-helper>" no-ff --actor opencode \
  -m "Add beads-work skill for dots-* issue loop" \
  -m "- Encode claim/plan/implement/commit/close loop using bd CLI
- Add resume path via .beads/in-progress-<harness>.json state file
- Gitignore in-progress-*.json so state files do not commit
- 3 commits, 281 insertions across 2 files"
```

## Example 2: homelab-IaC-style repo (50/72 body, Conventional Commits subject)

Branch under merge: `fix/audit-script-strict-mode` with 2 commits in a
repo whose `AGENTS.md` prescribes Conventional Commits subjects on top of
the same 50/72 body shape.

`git log main..fix/audit-script-strict-mode --oneline`:

```
3344556 Respect CZ_AUDIT_STRICT for ansible-lint
7788990 Add fixture for strict-mode failure
```

Drafted message:

- Subject (≤72 chars, Conventional Commits):

  ```
  fix(audit): honor CZ_AUDIT_STRICT for ansible-lint runs
  ```

- Blank line.

- Body:

  ```
  - Treat ansible-lint findings as errors when CZ_AUDIT_STRICT=1
  - Add fixture covering the strict-mode failure path
  - 2 commits, 14 insertions, 3 deletions across 3 files
  ```

Full invocation under Claude Code:

```bash
"<merge-helper>" no-ff --actor claude \
  -m "fix(audit): honor CZ_AUDIT_STRICT for ansible-lint runs" \
  -m "- Treat ansible-lint findings as errors when CZ_AUDIT_STRICT=1
- Add fixture covering the strict-mode failure path
- 2 commits, 14 insertions, 3 deletions across 3 files"
```

Same message under OpenCode (only the identity env vars change):

```bash
"<merge-helper>" no-ff --actor opencode \
  -m "fix(audit): honor CZ_AUDIT_STRICT for ansible-lint runs" \
  -m "- Treat ansible-lint findings as errors when CZ_AUDIT_STRICT=1
- Add fixture covering the strict-mode failure path
- 2 commits, 14 insertions, 3 deletions across 3 files"
```

## What to extract from the branch

For either convention, build the body from these commands run inside the
main worktree before step 5's helper invocation:

```bash
git log "${MAIN_BRANCH}..${FEATURE_BRANCH}" --oneline
git diff --stat "${MAIN_BRANCH}...${FEATURE_BRANCH}"
```

Summarize:

- Two to four bullets covering the major changes (group related commits).
- A final bullet with the commit count and the diff stat totals.

Avoid:

- "Merge branch 'X' into main" — that is git's default boilerplate.
- Restating every commit verbatim — group and summarize instead.
- Trailing periods on the subject line (both conventions).
