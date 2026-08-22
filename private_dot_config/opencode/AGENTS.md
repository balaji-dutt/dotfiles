# Global Agent Rules

These rules apply to all OpenCode sessions regardless of repository.

## Code comments

- Never write a comment describing a change, a fix, a defect, its cause, or what
  the code used to do. No "was/now/previously/instead of", no "this fixes", no
  "needed because otherwise", no "note that we no longer". That context expires
  when the change merges and already belongs in the commit body.
- Apply the survival test before writing any comment: would this still be true
  and useful to someone reading this file a year from now, who never saw the
  diff? If it only makes sense beside the diff, it is a changelog entry — put it
  in the commit body instead.
- Default to zero comments. Declarative config (Terraform, DNS records, k8s
  manifests, CI YAML, Helm values) is self-describing. A resource named
  `dmarc-example-com` does not need a comment saying it is the DMARC record.
- Comment only where a future editor would break something without it: a
  non-obvious external constraint, a required out-of-band manual step, or an
  invariant the surrounding code cannot show. One line. If it needs a paragraph,
  it belongs in a plan document, not inline.
- This applies to comments you edit as well as ones you add. When a change
  invalidates an existing comment, delete it rather than rewriting it into a
  narrative about the change.

## GitHub issue AI-assistance disclosure

- When you create a GitHub issue at my request, in any repository and through
  any creation method, make this disclosure the first content in the issue body
  and include exactly one copy:

  ```md
  > On AI assistance: <assistant attribution> helped me narrow down the behavior and draft the wording below, including the repro steps. The investigation, the actual issue, and the workflow are mine. — @balaji-dutt
  ```

- Replace only `<assistant attribution>`, including the angle brackets. Keep the
  rest of the disclosure unchanged, then leave a blank line before the issue
  body's remaining content. Do not place a heading or preamble above it.
- Attribute the request-owning root agent: the first agent that accepted my
  request to create that issue. Use the verified model from the root agent's
  acceptance turn. Do not replace it with the model of a downstream
  orchestrator, editor, reviewer, or tool caller.
- Use a human-readable attribution. Include the model when verified, for
  example `OpenCode (Qwen-3.7 Max)`. Add the reasoning level only when runtime
  metadata explicitly provides it, for example
  `OpenCode (GPT-5.6 Sol / High)`. Never infer metadata from configuration,
  defaults, or aliases. If the model cannot be verified, use `OpenCode` without
  parentheses.
- Use `agent_attribution` when current-session history is useful for verifying
  the root acceptance turn. It reports only the current session's flat message
  history; do not use it to infer a parent/child call graph. If the tool cannot
  verify the root model, use the client-only fallback.
- Delegated agents may investigate or draft, but should return the issue body
  to the root agent for final review and submission. Preserve any disclosure
  supplied by the root agent. If a delegate must submit without verified root
  model metadata, use the client-only `OpenCode` fallback rather than the
  delegate's model.
- If a draft already contains this disclosure, move or update it instead of
  adding another copy.
- This requirement applies only to GitHub issues. Do not apply it automatically
  to pull requests, comments, discussions, or other GitHub artifacts.

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
- `oc-commit` is available as a Bash wrapper on POSIX and a managed `.ps1`
  wrapper on native Windows. Invoke the Windows wrapper from PowerShell; the
  same-name `.cmd` stub refuses because batch cannot preserve multiline
  arguments. Pass the same arguments from either supported shell.
- When work is complete and verified, propose a commit message for approval before running `oc-commit`.
- Before committing, re-read the comment lines the change adds. On POSIX,
  `git diff --cached | grep -E '^\+.*(#|//|/\*)'`; in PowerShell 7,
  `git diff --cached | Select-String '^\+.*(#|//|/\*)'`. Each hit must pass the
  survival test in **Code comments** on its own. Deleting is always an acceptable
  outcome; "I already wrote it", "it is only one line", and "this one is
  genuinely useful" are not exemptions.
- Follow the commit message format specified in the repo's AGENTS.md or project documentation. If no repo-specific format exists, use a concise subject line in imperative mood.
