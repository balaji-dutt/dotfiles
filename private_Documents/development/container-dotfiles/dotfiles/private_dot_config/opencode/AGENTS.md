# Global Agent Rules

These rules apply to all OpenCode sessions regardless of repository.

## Commit Workflow

- **Never use `git commit` directly.** Always use `oc-commit` instead. This
  wrapper is a direct `git commit` replacement and ensures commits are
  attributed to OpenCode rather than the human user's git identity.
- Do not probe `oc-commit --help`; pass normal `git commit` arguments directly:
  - `oc-commit -m "subject"`
  - `oc-commit -m "subject" -m "body"`
  - `oc-commit -F <message-file>`
- When work is complete and verified, propose a commit message for approval before running `oc-commit`.
- Follow the commit message format specified in the repo's AGENTS.md or project documentation. If no repo-specific format exists, use a concise subject line in imperative mood.
