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

## OpenCode Bash gate flow

`.opencode/plugins/review-loop-marker.js` records Git dirty/untracked paths
before and after each `bash` tool call. The snapshots are paired by the
hook's `sessionID` and `callID`. A session-scoped gate is written only for
reviewable paths still dirty after the command whose kind, content, symlink
target, or executable bit changed since the snapshot. Existing `file.edited`,
deleted, renamed, and moved events keep their direct-event behavior. The
sessionless `file.watcher.updated` event is not used for Bash attribution.

- Read-only commands, an unchanged pre-dirty file, a timestamp-only touch,
  and an edit reverted to HEAD do not raise a Bash gate. Ordinary shell
  commands that exit nonzero still return a tool result and run the after
  hook in OpenCode 1.18.31. Setup/permission errors that throw before a
  tool result do not run the after hook.
- The before and after scans each hash at most 64 MiB of regular files; a
  file outside the remaining budget is not treated as a proven change. The
  budget is shared across dirty paths in sorted order. Snapshot
  failures emit a warning and do not fabricate a gate from an unknown
  baseline. Gitignored files are absent from the dirty set.
- Only changes visible when the Bash tool returns are captured. A detached
  process writing later, or a command that edits and then commits, can leave
  no dirty change at the after hook. Concurrent writers can be attributed
  to overlapping calls. OpenCode does not provide a session-scoped completion
  hook for a detached process.
- In-flight snapshots are held in memory with a 128-call limit. If a tool
  fails before the after hook or the limit is reached, an orphaned snapshot
  cannot produce a gate.
- Regression tests invoke the plugin hooks around real filesystem mutations
  in a disposable Git repository; they do not launch a live OpenCode session.

## Claude Code gate flow

The Claude Code hooks in `.claude/hooks/` mirror the OpenCode plugins; the
shared logic lives in `.claude/hooks/lib/review_gate.py`.

- `mark-needs-review.sh` (PostToolUse, `Write|Edit`) reads the hook payload
  and raises a gate only when the edited file is inside the session's
  checkout (git-toplevel match, so nested worktrees under `worktrees/` gate
  independently), is not a review-loop runtime artifact, and is not exempt
  per `exemptPaths`. Edits outside the repo (`/tmp`, plan files) and
  backlog-only sessions never raise a gate.
- Native worktrees (`EnterWorktree`, `claude --worktree`, subagents with
  `isolation: worktree`): Claude Code can move the hook payload `cwd` into a
  worktree while `CLAUDE_PROJECT_DIR` stays on the checkout the session
  started in. The gate, its lock and the in-flight records stay at that
  project checkout. An edit is also accepted when it lands in the checkout
  that contains the payload `cwd`, if that checkout is another worktree of
  the same repository (same git common dir); it is recorded in the gate's
  `roots` map under the worktree path. A Bash command run from a worktree is
  diffed in that worktree and in the project checkout. At Stop each checkout
  is judged separately. Whenever the gate has worktree entries or Stop runs
  from a worktree, the reviewer is scoped with `git -C` for every checkout.
  A worktree that has since been removed, or whose gitdir was pruned, hands
  its files to the project checkout, so a branch merged into the project
  checkout's current branch still needs review; a worktree that git refuses
  to open (for example `safe.directory`) keeps its files and the block.
  Finding the worktree costs no git call while `cwd` is inside the project
  checkout, and the git calls that find worktrees and diff snapshots share
  an 8-second budget per hook run, under the 10-second hook timeout.
  - Known gaps: a command run from the project checkout that edits a
    worktree by path, or one that edits a third checkout, is not diffed
    there. While the session is in a worktree, project-checkout files can be
    reviewed only after `ExitWorktree` (keeping the worktree), because
    Claude Code blocks `git -C` into the project checkout from a worktree
    session and its subagents. A `cd` into any other worktree of this repo,
    the main checkout included, also gates edits made there. The worktree's
    own settings may load as well and fire the hooks twice; every hook step
    is idempotent. The no-Python legacy fallback judges the project checkout
    only.
- Files changed by a shell command are gated by a snapshot pair on
  `Bash|PowerShell`. `snapshot-before-bash.sh` (PreToolUse) fingerprints the
  checkout's dirty set
  (`git --no-optional-locks status -z --untracked-files=all --no-renames`,
  so a move lists both paths), and
  `mark-needs-review-bash.sh` (PostToolUse and PostToolUseFailure, so a
  command that writes and then exits non-zero is covered) marks every
  reviewable path that is still dirty afterwards and is new or changed. A
  command that restores a file to HEAD marks nothing. The same path policy
  applies as for `Write|Edit`.
  - Read-only commands, dirty files the command did not touch, `touch` on an
    existing file, and `git add` raise nothing, because regular files
    compare by content hash and executable bit. Hashes are reused only when
    a file's size and mtime are unchanged and it was last modified at least
    2 s before the baseline was taken. Each scan hashes at most 64 MiB; a
    file that does not fit in what is left compares by size and mtime, and
    one hashed by only one of the two scans counts as changed.
  - Snapshots live outside the checkout, in `$CLAUDE_REVIEW_GATE_STATE_DIR`
    or `<tempdir>/claude-review-gate-<uid>` (`claude-review-gate` on native
    Windows). There is one file per tool call, keyed by session and
    `tool_use_id`. Snapshots expire after an hour. After its final diff, a
    session's own Stop also drops its foreground snapshots older than 15
    minutes, which are orphans from an interrupt or a denial. Only
    snapshot-named files in the directory are ever pruned.
  - Post fires when a `run_in_background` command launches, so that
    command's snapshot is kept and rebased rather than consumed. The Stop
    hook diffs every leftover snapshot for the session (background commands,
    Esc interrupts, hook timeouts, denied commands), marks what changed, and
    rebases it. Every mark from any hook also writes the marked paths'
    current fingerprints into the session's live snapshots, under the same
    lock. So a snapshot does not report an edit that is already gated, and a
    PASS is not undone at the next Stop.
  - Without a working Python the Bash hooks skip, and the Post hook prints a
    stderr warning; an unconditional mark would gate every read-only
    command. The Post hook skips only while the resolver matches HEAD. If a
    modified resolver finds no Python, if the helper or resolver file is
    missing or fails to load, or if the helper fails, the Post hook writes
    the legacy unconditional mark instead. Both files are tracked, so the
    command under review may be what broke them. The Pre hook never blocks a
    command. Its errors are swallowed, and the settings entry ends in
    `|| true`, because a PreToolUse exit 2 would deny every Bash call.
  - Notices from these hooks (no Python, skipped snapshot, unusable state
    directory, lock failure) go to stderr, which Claude Code does not show
    in its normal view for a hook that succeeds.
  - Gate and snapshot updates hold an `flock` on `.claude/` on POSIX, so
    parallel hooks do not drop each other's paths. Native Windows has no
    `fcntl`, and filesystems that refuse `flock` print a notice. In both
    cases the writes run unlocked, and two concurrent writers can lose one
    update.
  - Cost on WSL2 in this repo: about 115 ms per Bash call for both hooks
    together.
  - Known gaps: a single command that edits and then commits leaves nothing
    dirty to see. Background writes after the session's last Stop, or after
    the one-hour TTL, are missed. So are writes from a foreground command
    after it is moved to the background mid-run, because its snapshot is
    consumed when its Post hook fires, and writes from a process a
    foreground command detaches (`cmd &`, `nohup`) after that command
    returns. A skipped snapshot (git timeout or error) leaves that one
    command unchecked, with a stderr notice. If the state directory is a
    symlink, cannot be created, or (at the default location) belongs to
    another user, every Bash call goes unchecked, each with a notice. Edits
    other processes make in the worktree during a command are attributed to
    the session. While a leftover snapshot lives (an hour for a background
    command, until a Stop 15 minutes on for an interrupted or denied one),
    that includes edits made from your editor. A command left at a
    permission prompt for over an hour loses its snapshot and goes
    unchecked. So does a background subagent's call that has been pending
    for more than 15 minutes when the main session stops. `git stash apply`
    and `git reset --soft` or `--mixed` mark the work they restore.
    Gitignored files are never marked, which matches the pending-work check
    at Stop.
- Gate file: `.claude/.needs_dotfiles_review.<session_id>` (gitignored),
  JSON with `timestamp`, `firstTimestamp`, `markedAt` (the last mark as a
  float, which orders an edit and a reviewer launch in the same second;
  gates without it round `timestamp` up a second), `sessionID`, the
  accumulated repo-relative `files` list, and an optional `roots` map from
  another worktree's path to files relative to it. Without Python or the
  helper script, the `Write|Edit` marker hook falls back to an unconditional
  mark in the legacy unsuffixed `.claude/.needs_dotfiles_review`.
  That legacy mark has no file list and means "review the whole repo". The
  next helper mark absorbs it into the session gate by adding every dirty,
  reviewable path in the project checkout, then deletes it. The legacy mark
  stays instead in three cases: git cannot list those paths within the
  hook's time budget, there are more than 200, or a fallback rewrote the
  mark while they were being listed. Stop then asks for a review of the
  session gate's files together with every reviewable pending path, clears
  both gates when neither has pending work, and past 200 paths asks for a
  whole-repo review instead of a file list.
- All six hooks, and the plan-approval Beads hook, pick their interpreter
  through `.claude/hooks/lib/resolve-python.sh`, which tries `python3`,
  `python`, then `py -3` and executes each candidate before accepting it. A
  lookup alone is not enough on native Windows, where the Microsoft Store
  app-execution alias for `python3` is in `PATH` but exits 49 with "Python
  was not found".
  Each hook runs the resolver in a child of its own bash (`$BASH`) and reads
  `PY_CMD` back from its output. A resolver that fails to parse, trips
  `set -u`, or calls `exit` is a load failure: the hook keeps running without
  the helper, both markers write the legacy mark (the Bash Post hook skips an
  unmodified resolver only when it loads and finds no Python), Stop blocks
  while a gate file exists, and with a state file present the plan-approval
  hook blocks as unverified and names the resolver.
  `CLAUDE_REVIEW_GATE_PYTHON` prepends a candidate for debugging; it is probed
  like any other. The helper exits non-zero on any unexpected error and the
  hooks do not `exec`, so a helper that starts and then fails, including one
  broken by the edit under review, takes each hook's conservative branch.
  The markers write the legacy mark, and Stop blocks rather than erroring
  open.
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
  `Read` to exact paths that scoped status reports as untracked, plus the
  file where the harness saved one of the reviewer's own truncated outputs.
  When a gate spans worktrees, the Stop reason writes the commands as
  `git -C <checkout> diff ...`; the prompt allows `-C` only for a checkout
  the invocation names and applies it to every command for those files. No
  `permissions.allow` entry matches a command that starts with `git -C`, so
  those calls ask for approval. The OpenCode plugin never emits `-C`, so its
  twin has no such rule and its `git diff*` allowance always matches.
- OpenCode (`.opencode/agents/dotfiles-reviewer.md`): `permission.bash` is
  deny-by-default with `git status*`, `git diff*`, and `git log*` allowed, and
  `edit`, `glob`, `grep`, and `task` denied. Here the shell and broad-discovery
  rules are hard-enforced. `read` retains inherited sensitive-file protections,
  while the prompt restricts it to the same paths as on the Claude side.

`Grep`/`Glob` are deliberately withheld on the Claude side and `grep`/`glob`
are denied on the OpenCode side, so the reviewer cannot fall back to scanning
the working tree when it should be reading scoped diffs or untracked files.
Both prompts forbid `--no-index` and `--output` on `git diff` and `git log`,
and a path outside the worktree on `git diff`: those read arbitrary files or
write one. All of them pass the `git diff*` and `git log*` allowances, so this
rule is prompt-level on both harnesses.

The call budget is also prompt-level: 6 tool calls for a normal review, plus
one `Read` per untracked in-scope file. On Claude Code, Bash output past
`BASH_MAX_OUTPUT_LENGTH` (20,000 characters) is saved to a file, and each
`Read` is capped at about 4,000 tokens by
`CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`; both are set in
`dot_claude/settings-base.json`. The OpenCode twin uses the same thresholds
under OpenCode's own output limits. The reviewer sizes the change with
`git diff HEAD --stat` first. Above about 200 changed lines, or when the one
`Read` of an untracked in-scope file comes back truncated or rejected, it
switches to large-diff mode with a cap of 20 calls: whole-file
`git diff HEAD -U0` batches of about 200 changed lines, a separate
`git diff --cached` for files staged and then edited again (`MM`), and
`Read` pages of about 150 lines (`offset`/`limit`) through any saved output
or long untracked file. If either budget runs out, or a call is denied
permission, it returns FAIL and lists the unreviewed or partly reviewed
files so the caller can review them in a separate run. On Claude Code each
checkout the Stop reason names gets its own budget.

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
- Claude gate helper (mark/snapshot/enforce/clear logic):
  `.claude/hooks/lib/review_gate.py`
- Claude interpreter resolver (run in a child shell by all six hooks and the
  plan-approval hook):
  `.claude/hooks/lib/resolve-python.sh`
- Claude marker hook:
  `.claude/hooks/mark-needs-review.sh`
- Claude shell-edit hooks:
  `.claude/hooks/snapshot-before-bash.sh`,
  `.claude/hooks/mark-needs-review-bash.sh`
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
