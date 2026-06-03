<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)",
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
as DeepWiki. Agent-scoped MCP servers can be declared in Claude subagent
frontmatter instead; for example, `dot_claude/agents/special-builder.md` and
`dot_claude/agents/agent-engineer.md` declare TempoGraph inline so its tools are
available only to those agents.

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

## Chrome DevTools MCP

`chrome-devtools` is registered as a user-scope stdio server for macOS and
Windows only. It targets Chrome Dev via `--channel=dev`, opts out of package
usage statistics, and requires a local Chrome Dev executable. Dev Containers are
skipped because Chrome is not available there.

WSL2 is intentionally not enabled for this entry. A safer future WSL2 setup
would start Windows Chrome Dev with an explicit remote-debugging port and then
configure the MCP server with `--browser-url` from WSL2.

For `--autoConnect`, Chrome Dev must already be running, remote debugging must be
enabled from `chrome://inspect/#remote-debugging`, and Chrome will show a local
permission prompt before the MCP server can attach.

Set `CLAUDE_MCP_DRY_RUN=1` to validate what would be configured without calling
`claude mcp add`.

Verify the configured servers with:

```sh
claude mcp list
```

Inspect one server with:

```sh
claude mcp get deepwiki
```
