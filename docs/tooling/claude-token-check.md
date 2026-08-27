<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# claude-token-check

`~/bin/claude-token-check` reports whether Anthropic still accepts the local
Claude Code credential. Run it before starting a session so an expired login
surfaces up front instead of as a `Run /login` prompt partway through real work.

```sh
claude-token-check
claude-token-check --debug
```

## Why not just `claude auth status`

`claude auth status --json` is cheap and useful, but it only reads local state.
Pointing `HTTPS_PROXY`, `HTTP_PROXY`, and `ALL_PROXY` at a closed port still
returns success in the same 0.2s, which shows it never contacts Anthropic. It
confirms a credential exists and whose it is; it cannot confirm the server will
honour it.

`claude-token-check` uses that as a cheap first gate, then probes Anthropic's
OAuth usage endpoint for the authoritative answer.

## Exit codes

The codes describe the **Claude Code** credential, since that is the one a
`claude` session uses. OpenCode lines in the output are context and never move
the exit code.

| Code | Meaning |
| :--- | :--- |
| 0 | Valid. The usage endpoint accepted the Claude Code credential. |
| 1 | Invalid. Run `claude auth login`. |
| 2 | Indeterminate. No Claude Code credential found, or it could not be checked. |

Exit 2 is deliberately distinct from exit 1. A rate-limited, unreachable, or
withdrawn endpoint must never be reported as a dead token, so scripts consuming
this should treat only exit 1 as a reason to re-authenticate.

## Credential discovery

OpenCode and Claude Code keep separate OAuth credentials. Claude Code never
reads OpenCode's `auth.json`, so answering with OpenCode's token can report a
healthy login while a `claude` session prompts `Run /login`. Both are probed and
printed, one line per source:

```
VALID: claude-code (keychain)  accepted by Anthropic.
VALID: opencode                accepted by Anthropic.
```

When they disagree, the disagreement is visible and the Claude Code line drives
the exit code:

```
INVALID: claude-code (keychain)  rejected (HTTP 401). Run `claude auth login`.
VALID:   opencode                accepted by Anthropic.
```

**claude-code** is one credential, resolved from the macOS Keychain service
`Claude Code-credentials`, falling back to `~/.claude/.credentials.json`.
Keychain lookup is macOS-only, so the credentials file is the source everywhere
else, including the mirrored copy inside a devcontainer. This row is always
printed; when no credential exists it reads `INDETERMINATE` rather than being
replaced by OpenCode's answer.

**opencode** is read from `auth.json` under the OpenCode data directory. An
entry whose `expires` timestamp has passed is skipped rather than probed, and
the row is omitted entirely when there is nothing to report. Its remediation is
`opencode auth login`, not `claude auth login`.

When both stores hold the same token the rows merge, so the endpoint is asked
once and the line names both sources.

The token reaches curl through a config on stdin, so it never appears in the
process list. `--debug` prints each discovery source and a truncated SHA-256
fingerprint, never token material.

## API-key billing

With `ANTHROPIC_API_KEY` or an `apiKeyHelper` set, Claude Code bills through the
API and shows `Not logged in` without ever prompting `/login`. `claude auth
status --json` grows an `apiKeySource` field in that configuration.

This check reports on the OAuth credential only and does not validate an API
key. On a machine that has no OAuth login at all, it will report a problem —
exit 1 when `claude auth status` reports no active login, otherwise exit 2 for a
missing credential — even though Claude Code will start and run on the API key.
Treat a non-zero result as "no usable OAuth credential", not as "Claude Code is
broken".

## Endpoint stability

The usage endpoint and its beta header are undocumented, discovered from
`@slkiser/opencode-quota`. If Anthropic changes or removes it, the script
degrades to exit 2. Do not build anything load-bearing on a 0 from this check.
