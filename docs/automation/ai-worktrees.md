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

On macOS, Linux, and WSL2, chezmoi installs the canonical Python script as
`~/bin/ai-wt`. On native Windows, chezmoi renders the same source to
`~/.local/ai-wt.py` and installs `~/.local/ai-wt.cmd` as the command entry point.
The Windows launcher requires an existing Python 3.10 or newer runtime; it does
not install Python.

On native Windows, `ai-wt` gives its Git commands and launched agent process
tree a process-scoped `core.longpaths=true` setting. This allows Git to check
out long worktree paths without changing repository, global, or system Git
configuration or Windows registry policy. The setting applies to Git, not to
unrelated legacy Windows applications.

## Basic Usage

```sh
ai-wt opencode feat/example-change
ai-wt opencode --opencode-profile custom feat/custom-agent-change
ai-wt opencode --auto feat/auto-approved-change
ai-wt claude fix/example-bug
ai-wt run opencode docs/update-notes -- --agent build
ai-wt run opencode --auto docs/auto-update
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

For `ai-wt opencode` and `ai-wt run opencode`, interactive branch creation asks
`Enable OpenCode --auto?` after the type and description prompts. The choice
defaults to No. Gum provides the confirmation UI when available; the plain
fallback accepts `y`/`yes` and `n`/`no`. Supplying `--auto` before the branch,
or passing it after `--`, skips the redundant question.

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
- OpenCode Plannotator profile: `build`

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

On native Windows, the running agent can retain handles to its current
directory. Worktree merge tooling therefore defers cleanup for a matching
`ai-wt` session instead of trying to remove it from inside that session. After
the agent exits, the existing foreground `ai-wt` wrapper owns normal cleanup.
This avoids partially removing Git worktree state while Windows still blocks
directory deletion.

Retained sessions can be resumed in the same worktree and branch context:

```sh
ai-wt resume list
ai-wt resume <session-id>
ai-wt resume --auto <session-id>
ai-wt resume <session-id> -- --agent build
```

By default, `resume` reuses the command recorded in the session metadata. Tool
arguments passed after `--` replace those recorded tool arguments for that
launch. After the resumed tool exits, normal automatic cleanup runs again: clean
worktrees are removed, while dirty worktrees stay retained for another resume or
manual cleanup. OpenCode sessions also retain their recorded build or custom
Plannotator profile across resumes.

On an OpenCode session, `resume --auto` appends the flag for that launch without
discarding the recorded command or rewriting it in metadata. A later resume
without the flag returns to the recorded command unless the session was created
with `--auto`. First-class and interactive `--auto` handling is OpenCode-only;
Claude launches are unchanged. The existing `-- --auto` tool-argument form
remains available for compatibility.

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
The `build` profile prefers `opencode-plannotator`, and `custom` prefers
`opencode-plannotator-custom`; either profile falls back to `opencode` when its
POSIX wrapper is unavailable. Native Windows therefore launches `opencode.exe`
directly while still applying the selected pool. Custom OpenCode commands may
still use an explicit `{worktree}` placeholder when they need the absolute
worktree path as an argument.
OpenCode's `--auto` flag auto-approves permissions that are not explicitly
denied; it does not replace configured denials.

For every OpenCode child, `ai-wt` exports the selected range as
`PLANNOTATOR_PORT`, records the pool in `OPENCODE_PLANNOTATOR_POOL`, and disables
Claude Code prompt and skill imports. It defaults `ANTHROPIC_SYSTEM_PROMPT_PATH`
to the platform null device (`/dev/null` on POSIX or `NUL` on Windows) so
`opencode-claude-bridge` cannot reuse a stale Claude Code system prompt. A
non-empty custom prompt path is preserved. Claude children do not receive these
OpenCode-specific overrides.

Claude launches with its current working directory set to the worktree path. The
wrapper prefers `claude-plannotator` when it is available and falls back to
`claude`. Do not pass Claude's `--worktree` or `-w` flags through `ai-wt`; the
wrapper has already created the worktree.

The wrapper runs the tool as a foreground child process instead of using
`exec`, so it can run cleanup after the tool exits. Cleanup is best-effort;
crashes, reboots, `SIGKILL`, or detached child processes can leave a managed
worktree behind. Use `ai-wt list`, `ai-wt cleanup`, and `ai-wt prune` for
recovery.

On native Windows, configured command strings use Windows command-line parsing
and child tools still launch directly without a command shell. Native `.exe`
tools are supported. `.cmd` and `.bat` agent commands are rejected before
worktree creation; configure the corresponding native executable instead.

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
ai-wt.opencodeProfile
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
AI_WT_OPENCODE_PROFILE
AI_WT_CLAUDE_COMMAND
AI_WT_SUBMODULE_INIT
AI_WT_PROMPT_BACKEND
```

`AI_WT_PROMPT_BACKEND` accepts `auto`, `gum`, or `plain`. The default `auto`
uses Gum for branch creation prompts when `gum` is available and otherwise uses
the plain fallback. Set it to `plain` to disable Gum prompts, or `gum` to fail
fast when Gum is missing.

`AI_WT_OPENCODE_PROFILE` and `ai-wt.opencodeProfile` accept `build` or
`custom`. The CLI form is `--opencode-profile`; new OpenCode sessions default
to `build`. `PLANNOTATOR_PORTS_BUILD` and `PLANNOTATOR_PORTS_CUSTOM` override
the corresponding managed host or devcontainer ranges. An explicit OpenCode
command changes the executable and arguments, not the selected profile or child
environment.

If `AI_WT_OPENCODE_COMMAND` contains `{worktree}`, that placeholder is replaced
with the worktree path. Otherwise the command runs from the worktree without an
implicit project argument.

## Devcontainer Availability

The canonical source is `bin/executable_ai-wt.tmpl`. The Windows Python payload
includes that template rather than maintaining a copy. The homelab devcontainer
receives the same canonical file through the selective mirror in
`configs/devcontainer-sync.jsonc`:

```sh
./assets/sync-devcontainer-assets.sh
```

Do not hand-edit the generated container copy under
`private_Documents/development/container-dotfiles/dotfiles/dot_local/bin/`.
