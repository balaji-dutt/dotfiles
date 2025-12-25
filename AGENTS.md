<!-- markdownlint-disable MD007 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
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
- When executing a `chezmoi` command, always the use alias `cz`.

## About This Project

This project contains configuration files (commonly referred to as dotfiles) for a number of programs. The configuration files are managed through the following tools:

- chezmoi
- Bash scripts

The configuration files are intended to be used in a cross-platform manner. At this time, cross-platform represents the following 1)macOS and 2) Linux VM's running under WSL2.

## Available Tools

These tools are installed globally on the system and can be used via CLI commands.

- chezmoi: for applying changes to configuration files.
- jq: for processing JSON data.

## Documentation References

- When adding any scripts to be used when managing dotfiles, please refer to: docs/agents/ADDING_SCRIPTS.md
- docs/DISCOVERIES.md contains useful lessons learned and discoveries made during development.

## Documentation Workflow (README.md)

- **Modifications:** When updating `README.md`, you are authorized to update/commit the changes, but follow this protocol:
  1. Perform the edits.
  2. Propose a descriptive summary of the change following the **50/72 rule**:
     - **Subject:** A single line under 44 characters (the script adds the "docs: " prefix automatically).
     - **Body:** Detailed explanation, with each line wrapped at 72 characters.
  3. **Ask for my approval** of the draft message before proceeding.
  4. Once approved, run `~/.claude/commit-docs.sh "readme" "<insert approved multi-line message here>"`.
  5. **Note:** The script automatically handles the "docs: " prefix and the blank line separator, so do not include them in your draft.

## Final Steps

**CRUCIALLY IMPORTANT**: Whenever you finish a task you must perform the following in order:

- Make atomic changes.

  This repo is edited primarily by changing **chezmoi source-state files** (files in the repo such as `dot_*`, `private_*`, `dot_config/...`, `*.tmpl`). After each change, validate only what you touched (avoid a full repo-wide apply):

  1) Run a *scoped* dry-run apply using the source path(s) you changed:

     `cz apply --dry-run --verbose --source-path <source-path...> 2>&1`

     Review the output and fix any errors before proceeding.

     Notes:
     - Prefer passing the specific source file(s) you edited (often just one).
     - If you changed a shared template, template directory, or other input that may affect many targets and you cannot confidently enumerate impacted paths, pause and ask me if it's OK to run a full:
       `cz apply --dry-run --verbose 2>&1`

  2) Review diffs in a terminal-friendly way (do not open GUI diff tools such as VS Code):

     `cz --use-builtin-diff --no-pager diff <target-path...> 2>&1`

     Target-path selection rules:
     - If the target path is obvious from the source-state naming (e.g. `dot_zshrc` -> `~/.zshrc`, `dot_config/git/config.tmpl` -> `~/.config/git/config`), use it.
     - If the correct target path is not obvious or could be ambiguous, **pause and ask me to confirm the intended target path(s)** before running `cz diff`.

  3) If you created a new source file and want to verify the generated target contents without applying:

     - Print the computed target contents:
       `cz cat <target-path...>`

     - Or review what would change via:
       `cz --use-builtin-diff --no-pager diff <target-path...> 2>&1`

     If filesystem side-effects must be validated (permissions, directory creation, scripts, etc.)
     and dry-run/cat/diff are insufficient, pause and ask me if it is OK to run:
     `cz apply <target-path...>`.

- If the scoped `cz apply --dry-run --verbose --source-path ...` succeeds, run `cz doctor`.
  If any findings appear related to your changes, fix them before moving on.
