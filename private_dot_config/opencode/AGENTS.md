# Global Agent Rules

These rules apply to all OpenCode sessions regardless of repository.

## Beads CLI

- In agent and non-interactive shells, invoke the Beads executable with
  `command bd ...` on POSIX or `bd.exe ...` on native Windows.
- Do not source shell rc files or `beads-helpers.*`; interactive wrappers can
  change command behavior and are not an agent dependency.
- When a repository documents a guarded sync helper, call that helper
  explicitly instead of using native `bd dolt pull` or `bd dolt push`.
- When a repository documents Beads client mode on native Windows, `bd.exe`
  there is a client of a Dolt server hosted elsewhere. Do not run
  `bd dolt start|stop` and do not run the PowerShell sync helper; delegate sync
  to the host as that repo describes. A server-unreachable error means start the
  host, not stand up a local database.

## Commit Workflow

- **Never use `git commit` directly.** Always use `oc-commit` instead. This
  wrapper is a direct `git commit` replacement and ensures commits are
  attributed to OpenCode rather than the human user's git identity.
- Do not probe `oc-commit --help`; pass normal `git commit` arguments directly:
  - `oc-commit -m "subject"`
  - `oc-commit -m "subject" -m "body"`
  - `oc-commit -F <message-file>`
- `oc-commit` is available as a Bash wrapper on POSIX and a managed `.cmd`
  wrapper on native Windows. Pass the same arguments in either environment.
- When work is complete and verified, propose a commit message for approval before running `oc-commit`.
- Follow the commit message format specified in the repo's AGENTS.md or project documentation. If no repo-specific format exists, use a concise subject line in imperative mood.
