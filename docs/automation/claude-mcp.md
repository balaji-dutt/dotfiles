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
chezmoi script `.chezmoiscripts/run_onchange_after_claude_mcp_servers.sh.tmpl`.

The repo does not manage `~/.claude.json` directly because that file is
stateful and may contain Claude session, project, or authentication state.
Instead, the script uses the `claude mcp` CLI to add configured servers.

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

Claude's `mcp get` command is name-only, so this script treats MCP names as
globally unique across scopes when checking whether a server already exists,
even though it only configures user-scope entries. Avoid reusing the same name
in different scopes.

Do not store secrets in this config. If an MCP server needs tokens or headers,
design a runtime secret flow before adding it here.

## Script behavior

The script runs on non-Windows chezmoi applies when its rendered contents change.
It includes a hash of `configs/claude-mcp.json`, so config edits retrigger it.

On each run it:

1. skips if `claude` is unavailable;
2. skips if `python3` is unavailable;
3. validates the JSON config;
4. validates each enabled entry's fields;
5. skips disabled entries;
6. skips existing entries unless `replace` is `true`;
7. calls `claude mcp add` for enabled entries.

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
