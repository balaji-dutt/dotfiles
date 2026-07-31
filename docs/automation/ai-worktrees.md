<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# AI Worktree Wrapper

`ai-wt` starts Claude Code or OpenCode in a wrapper-managed Git worktree.
It is meant for direct tool usage outside Agent of Empires while keeping the
same basic session shape: one branch, one worktree, and safe cleanup.

## Basic Usage

```sh
ai-wt opencode feat/example-change
ai-wt claude fix/example-bug
ai-wt run opencode docs/update-notes -- --agent build
```

If no branch is passed and stdin is interactive, `ai-wt` prompts for a branch
type and description, then builds a branch name such as `feat/add-widget`.
When `gum` is installed, branch creation uses `gum choose` for the branch type
and `gum input` for the description, providing the preferred arrow-key and
line-editing UI. Without `gum`, `ai-wt` falls back to a plain numbered branch
type prompt and the terminal's normal `input()` line editing for the
description.
The Espanso `:aoens` expansion can still be used to paste a branch name, but
it is not required.

Each command has its own help output:

```sh
ai-wt opencode --help
ai-wt resume --help
ai-wt cleanup --help
```

Name generation can also be used directly:

```sh
ai-wt name --type feat --description "add widget"
```

## Defaults

- Worktree path: `<repo>/worktrees/<safe-branch>-<session-id>`
- State directory: `<repo>/.ai-wt`
- Base ref: `HEAD`
- Submodule initialization: disabled
- Branch deletion on cleanup: disabled
- Dirty worktree cleanup: keep the worktree and warn

The session ID is wrapper-owned and has this form:

```text
YYYYMMDD-HHMMSS-<6-hex-random>
```

Active or retained session metadata is stored under:

```text
.ai-wt/sessions/<session-id>.json
```

## Cleanup Behavior

On normal tool exit, `ai-wt` removes a clean managed worktree, deletes its
session metadata, and keeps the branch. Clean completed sessions do not appear
in `ai-wt list`. If the worktree has uncommitted changes, it is left on disk
and the metadata remains available for later cleanup.

Retained sessions can be resumed in the same worktree and branch context:

```sh
ai-wt resume list
ai-wt resume <session-id>
ai-wt resume <session-id> -- --agent build
```

By default, `resume` reuses the command recorded in the session metadata. Tool
arguments passed after `--` replace those recorded tool arguments for that
launch. After the resumed tool exits, normal automatic cleanup runs again: clean
worktrees are removed, while dirty worktrees stay retained for another resume or
manual cleanup.

Manual cleanup accepts a session ID, exact branch name, or exact worktree path:

```sh
ai-wt list
ai-wt cleanup <session-id>
ai-wt cleanup <session-id> --dry-run
ai-wt cleanup <session-id> --force
ai-wt cleanup <session-id> --delete
ai-wt cleanup <session-id> --delete --force
```

- `--force` is required to remove a dirty worktree.
- `--delete` is required to delete the branch.
- `--delete --force` is required for forced branch deletion.
- Pre-existing branches are not deleted unless `--force` is also passed.

`ai-wt prune` removes stale metadata for missing worktrees and can clean up
remaining managed worktrees. It still refuses dirty worktrees unless `--force`
is passed.

`ai-wt list`, `ai-wt resume list`, `ai-wt cleanup`, and `ai-wt prune` remain
plain CLI/table commands. They do not use Gum, which keeps their output
script-friendly and preserves explicit handling for destructive cleanup actions.

## Ignore Rules

The wrapper does not edit committed `.gitignore` files in arbitrary repos. On
first use it idempotently ensures these local-only ignore entries in
`<git-common-dir>/info/exclude` unless `--no-update-exclude` is passed:

- `/.ai-wt/`
- the in-repo worktree parent, for example `/worktrees/`

If a path is already ignored by `.gitignore`, global excludes, or local
excludes, nothing is appended. Existing exact local exclude entries are not
duplicated.

This dotfiles repo also commits `.ai-wt/` to `.gitignore` because it uses the
wrapper itself.

## Tool Launching

OpenCode launches with its current working directory set to the worktree path.
The wrapper prefers `opencode-plannotator` when it is available and falls back
to `opencode`. Custom OpenCode commands may still use an explicit `{worktree}`
placeholder when they need the absolute worktree path as an argument.

Claude launches with its current working directory set to the worktree path. The
wrapper prefers `claude-plannotator` when it is available and falls back to
`claude`. Do not pass Claude's `--worktree` or `-w` flags through `ai-wt`; the
wrapper has already created the worktree.

The wrapper runs the tool as a foreground child process instead of using
`exec`, so it can run cleanup after the tool exits. Cleanup is best-effort;
crashes, reboots, `SIGKILL`, or detached child processes can leave a managed
worktree behind. Use `ai-wt list`, `ai-wt cleanup`, and `ai-wt prune` for
recovery.

Agent-driven merges are handled by the repo-local `assets/agent-wt-merge`
helper instead of extra `ai-wt` subcommands. The helper supports `ai-wt` and
non-`ai-wt` worktrees and only uses `.ai-wt` metadata for cleanup suggestions.
See `docs/agents/worktree-merge-helper.md`.

## Configuration

Configuration precedence is:

1. CLI flags
2. Environment variables
3. Git config
4. Defaults

Supported Git config keys:

```text
ai-wt.worktreeParent
ai-wt.pathTemplate
ai-wt.stateDir
ai-wt.baseRef
ai-wt.deleteBranchOnCleanup
ai-wt.cleanupDirty
ai-wt.updateExclude
ai-wt.opencodeCommand
ai-wt.claudeCommand
ai-wt.submoduleInit
```

Common environment variables:

```text
AI_WT_WORKTREE_PARENT
AI_WT_PATH_TEMPLATE
AI_WT_STATE_DIR
AI_WT_BASE_REF
AI_WT_DELETE_BRANCH_ON_CLEANUP
AI_WT_CLEANUP_DIRTY
AI_WT_UPDATE_EXCLUDE
AI_WT_OPENCODE_COMMAND
AI_WT_CLAUDE_COMMAND
AI_WT_SUBMODULE_INIT
AI_WT_PROMPT_BACKEND
```

`AI_WT_PROMPT_BACKEND` accepts `auto`, `gum`, or `plain`. The default `auto`
uses Gum for branch creation prompts when `gum` is available and otherwise uses
the plain fallback. Set it to `plain` to disable Gum prompts, or `gum` to fail
fast when Gum is missing.

If `AI_WT_OPENCODE_COMMAND` contains `{worktree}`, that placeholder is replaced
with the worktree path. Otherwise the command runs from the worktree without an
implicit project argument.

## Devcontainer Availability

The canonical source is `bin/executable_ai-wt.tmpl`. The homelab devcontainer
receives the same file through the selective mirror in
`configs/devcontainer-sync.jsonc`:

```sh
./assets/sync-devcontainer-assets.sh
```

Do not hand-edit the generated container copy under
`private_Documents/development/container-dotfiles/dotfiles/dot_local/bin/`.
