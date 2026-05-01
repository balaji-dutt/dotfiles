<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Package Wrappers and Auto-Commit Flows

Shell wrappers in dotfiles can update package manifests and create git commits automatically.

## Package Manager Wrappers

| Tool | Wrapper Function | Manifest / Artifact | Behavior |
| :--- | :--- | :--- | :--- |
| Homebrew | `brew` | `brewfile.txt` | Updates Brewfile and commits on install/remove/tap changes. |
| Mise | `mise` | `configs/mise.toml` | Commits global or local mise changes depending on context. |
| uv | `uv` | `configs/uv_tools.txt` | Updates uv tool list and commits tool operations. |
| npm | `npm` | `configs/npm_globals*.txt` | Updates npm global package lists and commits changes. |
| npx | `npx` | none | Pass-through wrapper that enforces native Linux resolution on WSL2. |
| bun | `bun` | `configs/bun_globals.txt` | Tracks bun global package operations and commits updates. |
| bunx | `bunx` | `$XDG_STATE_HOME/bunx/commands.log` (or `~/.local/state/bunx/commands.log`) | Audits successful command usage only (no auto-commit behavior). |

On WSL2, wrappers prefer native Linux executables and reject `/mnt/<drive>/...`
Windows-mounted shims for tool-managed commands.

## Docs Commit Helper

- `~/.claude/commit-docs.sh` is installed from `dot_claude/executable_commit-docs.sh`.
- It supports `README.md` updates and `TODO.md` task actions (`add`, `pause`, `resume`, `complete`).
- For README updates, subject/body formatting follows a constrained docs commit style.

Repo-local equivalent helper:

- `assets/commit-docs.sh`

## Safety Notes

- Review auto-generated commits before pushing.
- Do not use these wrappers in repositories where automated commits are undesirable.
- Never commit secrets from environment-derived files.
- `chezmoi apply` package hydration uses the same native-tool preference on WSL2.
