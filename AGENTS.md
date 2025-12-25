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

- Make atomic changes. After each change, Run `cz apply --dry-run --verbose 2>&1` and review the output to check for any errors introduced by the change. If errors are encountered, address the issue before proceeding further.
  - If you are creating new files as part of a plan, note that `cz apply --dry-run --verbose 2>&1` will not show any files being created. If you need the files to be created in order to validate correctness, pause and ask me if it is okay to run `cz apply` for each new file that you would like to create.
- If execution of `cz apply --dry-run --verbose 2>&1` succeeds, run `cz doctor` to check for any errors. If you find any that are related to your changes, fix them before moving on to the next task.
