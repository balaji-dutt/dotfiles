<!-- markdownlint-disable MD007 MD013 MD023 MD024 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Standards

- This git repository linked with this project is publicly available. In order to prevent information leakage, any sensitive information is stored in 1Password or provided at runtime and is to NEVER be included as part of a git commit.

## About This Project

This project contains configuration files (commonly referred to as dotfiles) for a number of programs. The configuration files are managed through the following tools:

- chezmoi
- Bash scripts
- PowerShell scripts (on Windows)

The configuration files are intended to be used in a cross-platform manner across macOS, WSL2/Linux, and (where applicable) native Windows (PowerShell). Some validation commands are OS-specific; follow the audit instructions below.

## Available Tools

These tools are installed globally on the system and can be used via CLI commands.

- chezmoi: for applying changes to configuration files.
- python3 (preferred) / python: for ad-hoc script execution; if unavailable, ask before installing.

The following tools may be available, so check for their availability before executing a command. If required, stop and ask if the tool can be installed. Prefer containerized fallbacks (Docker/Podman) when available.

- jq: for processing JSON data.

## Shell command hygiene

- The agent shell may be `zsh`, where lowercase names such as `path` and
  `status` are special parameters. Do not use them as loop/local variables in
  inline validation commands; use names like `file_path`, `relpath`, or `rc`
  instead. Accidentally assigning to `path` mutates `PATH` and can make commands
  such as `python3`, `dirname`, or `chezmoi` disappear mid-run; assigning to
  `status` fails because it is read-only.
- Prezto's utility module aliases `cp`, `ln`, `mv`, and `rm` to their
  `nocorrect ... -i` forms in interactive zsh. In an agent command that prompt
  can read EOF, leave the target untouched, and still return success; it can
  also sit waiting for input that never arrives, stranding a background shell.
- Adding `-f` is not a dependable escape. It usually cancels the `-i`, but not
  always: measured on macOS (Darwin 25), `/bin/cp -i -f` still prompts and
  refuses, in either flag order and with no alias involved, despite the BSD man
  page saying `-f` overrides any previous `-i`. Prefix with `command` instead,
  which bypasses the alias on every platform: `command cp -f`,
  `command mv -f`, `command ln -f` (add `-s` only if you want a symlink),
  `command rm -f -- <path>`. Verify the result when later steps depend on it.
- Never chain a cleanup step on the assumption that a bare `cp`/`mv` succeeded.
  A declined `mv` exits 0, so `mv src dst; rm -f src` deletes the source that
  was never moved. Exit status is not a dependable guard across platforms, so
  join steps with `&&` and verify the content too. To restore a file from a
  backup and then drop the backup:
  `command cp -f -- "$b" "$f" && cmp -s -- "$b" "$f" && command rm -f -- "$b"`.
- That shell also sets `noclobber`, so `> file` onto an existing path fails with
  `file exists` and the command never runs. Use `command rm -f -- file` first,
  or `>| file`; note `>> file` fails when `file` does not exist, so pair the
  alias-safe removal form with `>` rather than `>>`. This matters twice in the
  audit loop: capturing output with
  `./assets/cz-audit.sh check <path> > log 2>&1; echo "rc=$?"` reports the blocked
  redirect rather than the audit result, and a blocked
  `chezmoi execute-template ... > rendered` leaves the previous render in
  place, so the next `bash -n rendered` reports "syntax OK" for pre-edit
  content.

## Documentation References

- When adding any scripts to be used when managing dotfiles, please refer to: docs/agents/ADDING_SCRIPTS.md
- For review-gate path policy and post-review docs updates, refer to: docs/agents/review-loop.md
- Before editing any file under `dot_claude/agents/`, `dot_claude/skills/`, or `private_dot_config/opencode/`, refer to: docs/agents/generated-agents.md
<!-- - docs/DISCOVERIES.md contains useful lessons learned and discoveries made during development. -->

## Post-review docs refresh workflow

- After a successful dotfiles review (`DOTFILES_REVIEWER_RESULT=PASS`), assess whether docs are stale.
- If docs are stale, use the `refresh-docs` skill.
- Refresh docs only when impact exists; do not rewrite docs broadly.
- If docs refresh changes reviewed docs (`README.md`, `AGENTS.md`, `dot_claude/AGENTS.md`, `docs/agents/**`), run the reviewer again.

## Documentation Workflow (README.md)

- **Modifications:** When updating `README.md`, you are authorized to update/commit the changes, but follow this protocol:
  1. Perform the edits.
  2. Propose a descriptive summary of the change following the **50/72 rule**:
     - **Subject:** Write the `docs: ` prefix explicitly. Keep the complete subject, including the prefix, at 50 characters or fewer.
     - **Body:** Detailed explanation, with each line wrapped at 72 characters.
  3. **Ask for my approval** of the draft message before proceeding.
  4. Do not auto-commit the change. Include the approved docs commit message in
     your final response unless I explicitly ask you to commit.

## Beads conventions (for the `beads-work` skill)

The global `beads-work` skill defers to this section when working on issues in
this repo.

- **Prefix**: Beads issues in this repo use the `dots-` prefix (e.g.
  `dots-go2`). The dolt database is also named `dots`, so a bare ID can be
  resolved by reading `dolt_database` from `.beads/metadata.json`.
- **JSONL export**: Do not refresh or commit `.beads/issues.jsonl` at close
  time. This repository uses the Dolt-backed Beads model, disables JSONL
  auto-export (`export.auto: false`), and ignores the file via
  `.gitignore`/`.chezmoiignore` to avoid churn and leaking git identity
  metadata.
- **CLI invocation**: Agent and non-interactive shell commands must call the
  executable directly with `command bd ...` on POSIX or `bd.exe ...` on native
  Windows. Do not source shell rc files or `beads-helpers.*`; those wrappers are
  for interactive use. To synchronize this repo, invoke
  `./assets/beads-sync.sh pull|push` or
  `pwsh -NoProfile -File ./assets/beads-sync.ps1 pull|push` explicitly.
- **Windows is a client, not a peer**: on native Windows `bd.exe` talks to the
  Dolt server WSL2 hosts, so both machines share one database and there is
  nothing to sync between them. `beads-sync.ps1 status|clean|pull|push|init` is
  refused there; delegate into WSL2 instead:
  `wsl -d Debian -- bash -c 'cd "$HOME/<repo>" && ./assets/beads-sync.sh push'`.
  Never run `bd dolt start|stop` on Windows. If `bd.exe` reports the server is
  unreachable, start it in WSL2 rather than working around it — there is no
  Windows fallback by design. See `docs/beads.md` → **Windows client mode**.
- **Commits**: Use `cc-commit` (Claude Code) or `oc-commit` (OpenCode), never
  `git commit` directly — see **Commit message workflow (required)** below.
  Format follows the same 50/72 rule documented below; include a
  `Refs: dots-<id>` trailer. Invoke the wrapper as the entire shell command
  from the repo working directory, with literal single-quoted `-m` arguments
  or bare `-F <real-message-file>`. Run staging, `git status`, and `git log`
  separately; avoid heredocs, `-F -`, redirects, substitutions, and command
  chains around the wrapper or provenance can degrade to tool-only trailers.
  See `docs/git-agent-attestation.md` for accepted forms and limitations.
- **Post-edit verification**: After implementation, run the repo audit
  documented under **Post-edit verification (required)** below
  (`./assets/cz-audit.sh check <repo-relative-path>` followed by
  `chezmoi doctor`). The skill itself does not name these commands — it
  expects to find them here.

## Post-edit verification (required)

**CRUCIALLY IMPORTANT**: Whenever you finish a task you must perform the following steps:

After editing any file in this repository, run the audit tool using the **repo-relative path**
(i.e. the path relative to the repo root / `chezmoi source-path`).

### macOS / WSL2

```sh
./assets/cz-audit.sh check <repo-relative-path>
```

### Native Windows (PowerShell 7)

```powershell
pwsh ./assets/cz-audit.ps1 check <repo-relative-path>
```

### Examples

``` sh
./assets/cz-audit.sh check dot_bashrc
./assets/cz-audit.sh check .chezmoiscripts/run_once_after_99-cleanup-wrong-apply.sh.tmpl
./assets/cz-audit.sh check ansible/site.yml
```

```powershell
pwsh ./assets/cz-audit.ps1 check bootstrap-wsl.sh
pwsh ./assets/cz-audit.ps1 check ansible/site.yml
```

### What the audit tool does

- It checks whether the edited repo file maps to a managed chezmoi target on this machine.
  - If managed, it runs:
    - `chezmoi diff --use-builtin-diff --no-pager --verbose <target>`
    - `chezmoi apply --use-builtin-diff --no-pager --dry-run --verbose <target>`
    - If not managed (repo-only inputs like `.chezmoiscripts/**`, `ansible/**`, `assets/**`, `configs/**`, `docs/**`), it will not run `chezmoi apply --no-pager --dry-run --verbose <target>`.
    - For repo-only inputs it runs targeted checks:
      - Shell files under `.chezmoiscripts/**` and `bootstrap-wsl.sh`:
        - Renders templates and enforces Bash syntax. Raw shell files also use local ShellCheck or its Docker/Podman fallback as an advisory check.
      - PowerShell files under `.chezmoiscripts/**`:
        - Renders templates and enforces PowerShell AST parsing without executing the script. On POSIX hosts, the parser uses local `pwsh`, WSL host `pwsh.exe`, or a retained Docker/Podman image.
      - `ansible/**`:
        - Uses `ansible-playbook --syntax-check` if available locally; otherwise lazily builds and runs the retained Docker/Podman fallback image.
      - `configs/**`:
        - Attempts advisory YAML/TOML validation when Python tooling is available. Missing advisory validators fail in strict mode.
      - The full required/optional dependency and missing-tool contract is documented in `docs/tooling/cz-audit.md`.
    - **chezmoi configuration / special files**:
      - These are special files and should **not** be treated like normal managed dotfiles (i.e. do **not** run `chezmoi apply --dry-run` directly for them).
        - Examples:
          - `.chezmoiignore` / `.chezmoiignore.tmpl`
          - `.chezmoiremove` / `.chezmoiremove.tmpl`
          - `.chezmoi.toml` / `.chezmoi.toml.tmpl`
          - `.chezmoidata.*`
          - `.chezmoiroot`
      - Always use the audit tool:
        - macOS / WSL2:
          ```sh
          ./assets/cz-audit.sh check <repo-relative-path>
          ```
        - Windows (PowerShell 7):
          ```powershell
          pwsh ./assets/cz-audit.ps1 check <repo-relative-path>
          ```
      - What the audit tool does for `.chezmoi*` files:
        - If the file is templated (`*.tmpl`), it runs `chezmoi execute-template -f <file>` to ensure the template renders on the current machine (catches missing-key/template errors early).
        - It then runs `chezmoi doctor` to surface config warnings.
          - A warning message that "config file template has changed, run `chezmoi init` to regenerate config file" can be ignored.
        - It does not run `chezmoi diff` / `chezmoi apply` for these files.

- You must run the repo audit for changed files:
  - For macOS/WSL2: `./assets/cz-audit.sh check <repo-relative-path>`
  - For Windows: `pwsh ./assets/cz-audit.ps1 check <repo-relative-path>`

- A change is considered **failing** and must be fixed (or reverted) if:
  - the audit command exits non-zero, **or**
  - the audit output contains a line beginning with `ERROR:`.

- Output lines beginning with `INFO:` (and references to `.cz-audit/*.log`) are **informational** and do not block changes unless strict mode is enabled.

- Strict mode (optional): enforce advisory checks (e.g. ansible-lint/shellcheck):
  - `CZ_AUDIT_STRICT=1 ./assets/cz-audit.sh check <path>`
  - or per-check strict flags (see `assets/cz-audit.env`)

- Worktree mode: when editing in a git worktree whose path differs from the configured chezmoi source dir, run the normal audit command. `cz-audit` auto-detects this case and points chezmoi at the worktree so it sees the edited source files.
  - Manual override: set `CHEZMOI_SOURCE_DIR` only when you need to force a different source directory.

- Exception: A message that says "config file template has changed, run `chezmoi init` to regenerate config file" may be ignored.

### Post audit tool execution steps

- If the audit tool run has been completed successfully as defined in the previous section, run `chezmoi doctor`
  - If any findings appear related to your changes, fix them before moving on.
  - Errors relating to a `vault` command failure can be ignored.
  - On native Windows, the exact `upgrade-method` failure
    `json: unknown field "$schema"` can be ignored. This is a known upstream
    failure parsing WinGet settings. Every other `failed` result remains blocking.

### Final step (required)

After the audit and `chezmoi doctor` steps succeed, you must follow **Commit message workflow (required)** and include a proposed commit message in
your final response. If the commit message proposal is missing, the task is incomplete. Do not conclude with ‘done’/‘complete’ until the commit message proposal is included.

## Commit message workflow (required)

After changes are complete and verification has passed (audit tool + `chezmoi doctor` as applicable), the agent must propose a commit message as part of the final response.

- Do not wait to be asked.
- Use the agent commit wrapper specified by your harness's global configuration
  (e.g. `oc-commit` for OpenCode). The wrapper ensures commits are attributed to
  the agent rather than the human user's git identity.
- If no harness-specific wrapper is available, use `git commit` directly.
- Exception: If the change includes `README.md`, follow **Documentation Workflow (README.md)** for committing.

### Commit message format

- Subject line: <= 50 characters, imperative mood, no trailing period.
- Second line: blank.
<!-- markdownlint-disable MD038 -->
- Body (optional): bulleted lines starting with `- `, each < 80 characters.
<!-- markdownlint-enable MD038 -->

### Choosing short vs full message (small vs big)

Use `git diff --stat` (and/or `git diff --numstat`) to classify the change.
Prefer the full format if unsure.

Small change → subject-only (no body):
- No new files, and
- Touches 1 file, and
- Total changed lines (additions + deletions) <= 15, and
- Low-risk (typos/comments/formatting/trivial docs/narrow tweak).

Big change → full format (subject + blank line + bullets):
- Any new file added, or
- Touches 2+ files, or
- Total changed lines (additions + deletions) >= 16, or
- Higher-risk / behavior-affecting change (scripts/bootstrap/templates/tooling).

### Final response requirement

A task is not complete unless the final response includes a proposed commit message
that follows the rules above.

## Landing a branch on main

- Use the `worktree-merge` skill rather than running `git merge` by hand.
- `assets/agent-wt-merge` is this repo's canonical merge helper. The skill
  discovers it at `<main-worktree>/assets/agent-wt-merge`, so the full helper
  path runs here instead of the approval-gated manual fallback other repos get.
- Start from `./assets/agent-wt-merge inspect --json` and treat its output as
  the source of truth: `feature.fast_forward_possible` selects `ff` when true
  and `no-ff` when false. The actor-specific match
  (`beads.claude.matches` or `beads.opencode.matches`) is the only signal that
  authorizes `--close-beads <issue-id>`. After closing the issue, the helper
  removes the handoff state file; if removal fails, it warns and leaves the
  closed issue with the state file still present.
- Cleanup is classified per worktree, not per repo, and the helper checks in
  this order: an `aoe-managed` lock defers to Agent of Empires; native Windows
  always defers cleanup until the active agent exits; an `ai-wt` session
  suggests `ai-wt cleanup <session-id> --delete --yes`; an unmanaged worktree
  suggests `git worktree remove` plus `git branch -d`. Read `cleanup.action` and
  `cleanup.manager` rather than assuming: `defer` means do not offer cleanup at
  all, `suggest` still needs confirmation, and `git branch -D` is never part of
  this workflow.

## New files (not yet in chezmoi state)

If you add a brand new file to the repo/source state, it may not appear in `chezmoi managed` yet on this machine (because it hasn’t been applied/recorded in state). In this case, do not assume it is unmanaged; instead, preview the computed target output.

### Preview target contents without applying

- Print the computed target contents:
  ```sh
  chezmoi --use-builtin-diff --no-pager cat <target-path>
  ```
- Or review what would change:
  ```sh
  chezmoi --use-builtin-diff --no-pager diff <target-path> 2>&1
  ```

### Applying changes

If filesystem side-effects must be validated (permissions, directory creation, scripts, etc.) and cat/diff is
insufficient, pause and ask before running:

```sh
chezmoi apply <target-path>
```

### Post new file preview execution steps

- Run the `chezmoi doctor` command.
  - If any findings appear related to your changes, fix them before moving on.
  - Errors relating to a `vault` command failure can be ignored.
  - On native Windows, the exact `upgrade-method` failure
    `json: unknown field "$schema"` can be ignored. This is a known upstream
    failure parsing WinGet settings. Every other `failed` result remains blocking.

### Final step (required)

After completing the new-file preview/apply steps and running `chezmoi doctor`, you must follow **Commit message workflow (required)** and include a proposed
commit message in your final response. If the commit message proposal is missing, the task is incomplete.
