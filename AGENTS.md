<!-- markdownlint-disable MD007 MD013 MD023 MD024 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
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

## Documentation References

- When adding any scripts to be used when managing dotfiles, please refer to: docs/agents/ADDING_SCRIPTS.md
<!-- - docs/DISCOVERIES.md contains useful lessons learned and discoveries made during development. -->

## Documentation Workflow (README.md)

- **Modifications:** When updating `README.md`, you are authorized to update/commit the changes, but follow this protocol:
  1. Perform the edits.
  2. Propose a descriptive summary of the change following the **50/72 rule**:
     - **Subject:** A single line under 44 characters (the script adds the "docs: " prefix automatically).
     - **Body:** Detailed explanation, with each line wrapped at 72 characters.
  3. **Ask for my approval** of the draft message before proceeding.
  4. Once approved, run `./assets/commit-docs.sh "readme" "<insert approved multi-line message here>"`.
  5. **Note:** The script automatically handles the "docs: " prefix and the blank line separator, so do not include them in your draft.

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
    - `chezmoi diff --verbose <target>`
    - `chezmoi apply --dry-run --verbose <target>`
    - If not managed (repo-only inputs like `.chezmoiscripts/**`, `ansible/**`, `assets/**`, `configs/**`, `docs/**`), it will not run `chezmoi apply --dry-run --verbose <target>`.
    - For repo-only inputs it runs best-effort checks:
      - `.chezmoiscripts/** and bootstrap-wsl.sh:`
        - Uses `shellcheck` if available locally; otherwise runs ShellCheck in a Docker/Podman container (if available).
      - `ansible/**`:
        - Uses `ansible-playbook --syntax-check` if available locally; otherwise runs it in a Docker/Podman container (if available).
      - `configs/**`:
        - Attempts to validate YAML/TOML if Python tooling is available; otherwise skips.
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

- Exception: A message that says "config file template has changed, run `chezmoi init` to regenerate config file" may be ignored.

### Post audit tool execution steps

- If the audit tool run has been completed successfully as defined in the previous section, run `chezmoi doctor`
  - If any findings appear related to your changes, fix them before moving on.
  - Errors relating to a `vault` command failure can be ignored.

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

### Commit message workflow (required)

- After changes are complete and verification has passed (audit tool + `chezmoi doctor` as applicable), the agent must propose a commit message before running any commit-related commands.
- Exception: If the change includes `README.md`, follow Documentation Workflow (README.md) for committing.

#### Commit message format

- Subject line: max 50 characters, summary in imperative mood (e.g., “Add …”, “Fix …”, “Update …”), no trailing period.
- Second line: blank
- Body (optional):
  - From the third line onward, use bulleted lines starting with ` - `.
  - Each bullet line must be < 80 characters.
  - Bullets should explain *what changed and why*, not a changelog of every tiny edit.

#### Choosing short vs full message (small vs big)

Use `git diff --stat` (and/or `git diff --numstat`) to classify the change. Prefer the full format if unsure.

Small change → subject-only (no body):
- No new files, and
- Touches 1 file, and
- Total changed lines (additions + deletions) is <= 15, and
- Change is low-risk (typos, comments, formatting, trivial docs, narrow tweak).

Big change → full format (subject + blank line + bullets):
- Any new file added, or
- Touches 2+ files, or
- Total changed lines (additions + deletions) is >= 16, or
- Change is higher-risk / behavior-affecting (scripts, bootstrap, chezmoi templates,
  audit tooling, cross-platform logic), even if the diff is small.

Body guidance for big changes (recommended 1–5 bullets):
- Mention key behavior changes, cross-platform considerations, and any safety/rollback notes.
- If the work naturally splits into unrelated changes, propose splitting into multiple commits.

#### Examples

**Small (subject-only):**
- `Fix typo in zsh alias comment`
- `Tweak git prompt spacing`

**Big (subject + bullets):**

Add WSL bootstrap step for package sync

- Ensure apt packages are updated before applying dotfiles
- Skip when running under native Windows PowerShell
- Document expected env vars and failure modes
