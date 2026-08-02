<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Claude MCP Management

Global Claude MCP intent is managed through `configs/claude-mcp.json` and the
chezmoi scripts `.chezmoiscripts/run_onchange_after_claude_mcp_servers.sh.tmpl`
and `.chezmoiscripts/run_onchange_after_claude_mcp_servers.ps1.tmpl`.

The repo does not manage `~/.claude.json` directly because that file is
stateful and may contain Claude session, project, or authentication state.
Instead, the scripts use the `claude mcp` CLI to add configured servers.

This config is only for globally registered user-scope Claude MCP servers such
as DeepWiki. Agent-scoped MCP servers can instead be declared in Claude
subagent frontmatter when a canonical agent specification requires them.

## Config format

Each entry under `servers` is keyed by the MCP server name:

```json
{
  "servers": {
    "deepwiki": {
      "enabled": true,
      "scope": "user",
      "transport": "http",
      "url": "https://mcp.deepwiki.com/mcp",
      "replace": false
    }
  }
}
```

- `enabled`: set to `false` to leave an entry in the file without applying it.
- `scope`: Claude MCP scope. This global chezmoi script only supports `user`
  because local and project scopes depend on the current working directory.
- `transport`: `http`, `sse`, or `stdio`.
- `url`: required for `http` and `sse` servers.
- `command` and `args`: required for `stdio` servers.
- `replace`: when `true`, remove and re-add an existing user-scope server with
  the same name. Leave this `false` unless you intentionally want to rewrite an
  existing Claude MCP entry.
- `platforms`: optional list of platforms where the entry should apply. Current
  values are `darwin`, `linux`, `wsl2`, and `windows`.
- `skipDevcontainer`: optional boolean. When `true`, skip the entry inside a
  Dev Container or container runtime.
- `executablePaths`: optional per-platform candidate paths for `stdio` servers.
  The first existing path for the current platform is appended as
  `--executablePath=<path>`. If no candidate exists, the server is skipped
  instead of registering a broken command.

Claude's `mcp get` command is name-only, so this script treats MCP names as
globally unique across scopes when checking whether a server already exists,
even though it only configures user-scope entries. Avoid reusing the same name
in different scopes.

Do not store secrets in this config. If an MCP server needs tokens or headers,
design a runtime secret flow before adding it here.

## Script behavior

The scripts run on macOS, Linux/WSL2, and Windows chezmoi applies when their
rendered contents change. They include a hash of `configs/claude-mcp.json`, so
config edits retrigger them.

On each run, the scripts:

1. skip if `claude` is unavailable;
2. skip if `python3` is unavailable on non-Windows platforms;
3. validate the JSON config;
4. validate each enabled entry's fields;
5. skip disabled entries;
6. skip entries excluded by `platforms` or `skipDevcontainer`;
7. resolve configured executable candidates;
8. skip existing entries unless `replace` is `true`;
9. call `claude mcp add` for enabled entries.

The Windows script stores `npx` stdio servers as `cmd /c npx ...` so Claude can
start the package runner reliably on Windows.

## claude.ai account connectors

When Claude Code authenticates with a Claude.ai subscription, it also loads the
MCP connectors authorized in the claude.ai web app (for example Adobe, Canva,
Zapier, and Notion). These are not registered through this repo's
`configs/claude-mcp.json`; they come from the account.

This repo disables them by default by setting the
`ENABLE_CLAUDEAI_MCP_SERVERS` environment variable to `false` in the `env`
block of `dot_claude/modify_private_settings.json` (which becomes
`~/.claude/settings.json`). This is an all-or-nothing switch: there is no
per-connector toggle and no equivalent settings.json field. To use a connector
ad hoc, start Claude Code with `ENABLE_CLAUDEAI_MCP_SERVERS=true claude`.

The same setting propagates to the Dev Container because the devcontainer sync
mirrors `dot_claude/settings-base.json` verbatim, and the container symlinks
that file straight to `~/.claude/settings.json`.

## Chrome DevTools MCP

`chrome-devtools` is intentionally **not** a globally managed user-scope server.
It is useful only in specific repos, so it is registered manually as a
**local-scope** server in the single repo that needs it, instead of loading
everywhere. Local scope is stored in `~/.claude.json` keyed by that repo's
absolute path, stays private to the machine, and is never committed.

To register it locally (macOS, Chrome Dev installed):

```sh
cd /Users/balaji/Documents/development/Beads-Kanban
claude mcp add chrome-devtools --scope local -- \
  npx -y chrome-devtools-mcp@latest --no-usage-statistics \
  "--executablePath=/Applications/Google Chrome Dev.app/Contents/MacOS/Google Chrome Dev"
```

Adjust `--executablePath` for the local Chrome Dev install (on Windows, the
`%LOCALAPPDATA%`/`%PROGRAMFILES%` `Chrome Dev\Application\chrome.exe` paths). The
server launches Chrome Dev on demand with its managed profile; it is not
available in Dev Containers because Chrome is not installed there.

If `chrome-devtools` was previously registered at user scope by this repo's
scripts, remove that global entry once so it no longer loads everywhere (and to
avoid a name collision with the local-scope registration above):

```sh
claude mcp remove --scope user chrome-devtools
```

If you opt into `--autoConnect`, Chrome Dev must already be running on Chrome
144 or newer, remote debugging must be enabled from
`chrome://inspect/#remote-debugging`, and Chrome will show a local permission
prompt before the MCP server can attach. Do not combine `--autoConnect` or
`--channel=dev` with the `--executablePath` argument; upstream treats
`--channel` and `--executablePath` as mutually exclusive.

Set `CLAUDE_MCP_DRY_RUN=1` when running the chezmoi MCP scripts to validate which
user-scope servers they would configure without calling `claude mcp add`. This
gates the scripts only; it has no effect on manual `claude mcp` CLI invocations
like the local-scope registration above.

Verify the configured servers with:

```sh
claude mcp list
```

Inspect one server with:

```sh
claude mcp get deepwiki
```
