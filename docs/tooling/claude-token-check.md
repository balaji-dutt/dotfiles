<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# claude-token-check

`~/bin/claude-token-check` reports whether Anthropic still accepts the local
Claude credential. Run it before starting a session so an expired login surfaces
up front instead of as a `Run /login` prompt partway through real work.

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

| Code | Meaning |
| :--- | :--- |
| 0 | Valid. The usage endpoint accepted the credential. |
| 1 | Invalid. Run `claude auth login`. |
| 2 | Indeterminate. No credential found, or the endpoint could not be reached. |

Exit 2 is deliberately distinct from exit 1. A rate-limited, unreachable, or
withdrawn endpoint must never be reported as a dead token, so scripts consuming
this should treat only exit 1 as a reason to re-authenticate.

## Credential discovery

Discovery order matches the `@slkiser/opencode-quota` plugin so the result lines
up with the quota figures OpenCode reports:

1. OpenCode's own `auth.json` under the OpenCode data directory.
2. The macOS Keychain service `Claude Code-credentials`.
3. `~/.claude/.credentials.json`.

An OpenCode entry whose `expires` timestamp has passed is skipped rather than
probed. Keychain discovery is macOS-only; elsewhere discovery starts at step 1
and falls through to step 3, which is also how the mirrored copy behaves inside
a devcontainer.

The token reaches curl through a config on stdin, so it never appears in the
process list. `--debug` prints the discovery source and a truncated SHA-256
fingerprint, never token material.

## Endpoint stability

The usage endpoint and its beta header are undocumented, discovered from
`@slkiser/opencode-quota`. If Anthropic changes or removes it, the script
degrades to exit 2. Do not build anything load-bearing on a 0 from this check.
