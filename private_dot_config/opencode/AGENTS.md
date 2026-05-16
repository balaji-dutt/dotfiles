# Global Agent Rules

These rules apply to all OpenCode sessions regardless of repository.

## Commit Workflow

- **Never use `git commit` directly.** Always use `oc-commit` instead. This wrapper ensures commits are attributed to OpenCode rather than the human user's git identity.
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
