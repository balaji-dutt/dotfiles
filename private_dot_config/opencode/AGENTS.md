# Global Agent Rules

These rules apply to all OpenCode sessions regardless of repository.

## Beads CLI

- In agent and non-interactive shells, invoke the Beads executable with
  `command bd ...` on POSIX or `bd.exe ...` on native Windows.
- Do not source shell rc files or `beads-helpers.*`; interactive wrappers can
  change command behavior and are not an agent dependency.
- When a repository documents a guarded sync helper, call that helper
  explicitly instead of using native `bd dolt pull` or `bd dolt push`.

## Commit Workflow

- **Never use `git commit` directly.** Always use `oc-commit` instead. This
  wrapper is a direct `git commit` replacement and ensures commits are
  attributed to OpenCode rather than the human user's git identity.
- Do not probe `oc-commit --help`; pass normal `git commit` arguments directly:
  - `oc-commit -m "subject"`
  - `oc-commit -m "subject" -m "body"`
  - `oc-commit -F <message-file>`
- `oc-commit` is a Bash script. On native Windows (PowerShell), use Git's
  environment variable overrides directly:
  ```powershell
  $env:GIT_AUTHOR_NAME = "OpenCode"
  $env:GIT_AUTHOR_EMAIL = "noreply@opencode.ai"
  $env:GIT_COMMITTER_NAME = "OpenCode"
  $env:GIT_COMMITTER_EMAIL = "noreply@opencode.ai"
  git commit <args>
  ```
- When work is complete and verified, propose a commit message for approval before running `oc-commit`.
- Follow the commit message format specified in the repo's AGENTS.md or project documentation. If no repo-specific format exists, use a concise subject line in imperative mood.
