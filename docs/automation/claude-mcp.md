<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Claude MCP Management

Global Claude MCP intent is managed through `configs/claude-mcp.json`. Three
callers apply it:

- `.chezmoiscripts/run_onchange_after_claude_mcp_servers.sh.tmpl` on macOS,
  Linux and WSL2 applies. It is a thin wrapper that delegates to
  `assets/claude-mcp-apply.py`.
- The homelab-IaC devcontainer, which runs the same
  `assets/claude-mcp-apply.py` against the same config over its read-only
  `/tmp/host-dotfiles` mount. See "Dev Container registration" below.
- `.chezmoiscripts/run_onchange_after_claude_mcp_servers.ps1.tmpl` on native
  Windows applies, a separate PowerShell implementation of the same schema. See
  "Native Windows" below.

The repo does not manage `~/.claude.json` directly because that file is
stateful and may contain Claude session, project, or authentication state.
Instead, the scripts use the `claude mcp` CLI to add configured servers.

This config covers globally registered user-scope Claude MCP servers: DeepWiki
(`http`) and codebase-memory-mcp (`stdio`). Agent-scoped MCP servers can instead
be declared in Claude subagent frontmatter when a canonical agent specification
requires them.

## Config format

Each entry under `servers` is keyed by the MCP server name:

```json
{
  "servers": {
    "cbm": {
      "enabled": true,
      "scope": "user",
      "transport": "stdio",
      "command": "codebase-memory-mcp",
      "platforms": ["darwin", "linux", "wsl2", "windows"],
      "replace": false
    },
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
  values are `darwin`, `linux`, `wsl2`, and `windows`. WSL1 reports `wsl`, which
  no entry lists, so WSL1 hosts skip every gated server; that is intentional
  since WSL1 is not a supported host here.
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
rendered contents change. The shell template embeds hashes of both
`configs/claude-mcp.json` and `assets/claude-mcp-apply.py`, so edits to either
retrigger it.

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

The applier lives in `assets/claude-mcp-apply.py` rather than inline in the
template so the devcontainer can reuse it verbatim. Preview or reapply it by
hand from the repo root:

```sh
CLAUDE_MCP_DRY_RUN=1 python3 assets/claude-mcp-apply.py configs/claude-mcp.json
```

## Codebase Memory (cbm)

`cbm` is registered at **user scope**, so `codebase-memory-mcp` tools are
available in every Claude session and appear in `/mcp`, not just inside the
`agent-engineer` and `special-builder` subagents that declare the server in
frontmatter.

`command` is the bare `codebase-memory-mcp`, resolved from `PATH`. Provisioning
is platform-specific and deliberately separate from this registration. The
version has one Renovate-managed source in `.chezmoidata.yaml`:

| Platform | Binary source |
| :--- | :--- |
| macOS | shared pin rendered into `private_dot_config/mise/conf.d/96-codebase-memory-mcp.toml.tmpl` and installed by mise |
| WSL2 | shared pin passed to Ansible, written as a mise fragment before `mise install` |
| Dev Container | shared pin rendered as `CBM_VERSION`; portable release archive installed to `/usr/local/bin` by `postCreate.sh` |
| Native Windows | shared pin installed to `~/.local/codebase-memory-mcp.exe` by `run_onchange_after_install_codebase-memory-mcp.ps1.tmpl` |

Each environment has its own CBM cache and configuration. Chezmoi's POSIX and
Windows `run_after_zz-configure-codebase-memory-mcp.*.tmpl` hooks reconcile
`auto_index=true` after binary provisioning. The homelab devcontainer performs
the same get/set/get verification in `postCreate.sh`, using its persistent
`CBM_CACHE_DIR`. These paths configure the standard binary directly; they never
run the upstream installer or export `.codebase-memory` into a repository.

Auto-index runs when a new MCP server process can identify a repository root,
finish its bounded discovery preflight, and confirm that the repository is
within the default 50,000-file limit. A preflight timeout can skip auto-index
even below that limit. It also does not guarantee a fresh index when a
pre-existing database is stale, so structural agents with explicit
`index_repository` access may request approval and recover cache-locally with
`persistence: false`; known limit or safety refusals still require direct-source
fallback. Restart Claude Code after a configuration apply so its MCP process
reads the reconciled cache setting.

`dot_claude/settings-base.json` allows the nine read-only cbm tools outright and
denies `mcp__cbm__delete_project`. `index_repository`, `query_graph`,
`manage_adr`, and `ingest_traces` still prompt under `defaultMode: plan`.
Operating rules for the tools (notably manual `index_repository` recovery with
`persistence: false` and the prohibition on CBM lifecycle commands) live in
`dot_claude/AGENTS.md`, with the long-form OpenCode version in
`.opencode/instructions/mcp-usage.md`. The non-MCP entries in that same allow
list, and the rule syntax they follow, are covered in
`docs/automation/claude-permissions.md`.

## Dev Container registration

The `homelab-IaC` devcontainer never runs chezmoi, so it replays the same
repo-only inputs directly. `register_claude_mcp_servers` in
`dot_devcontainer/devcontainer-common.sh` runs
`/tmp/host-dotfiles/assets/claude-mcp-apply.py` against
`/tmp/host-dotfiles/configs/claude-mcp.json`. It is called from both
`postCreate.sh` and `postStart.sh`, immediately after
`install_claude_managed_asset_links`, so a host config edit lands on the next
container start without a rebuild.

Ordering matters: `ensure_claude_persistence_links` runs first at both call
sites, so `~/.claude.json` is already the symlink into
`/home/vscode/persistent-data/claude/` when `claude mcp add` writes to it, and
the registration survives a container rebuild.

The step never fails the lifecycle hook — a missing `claude`, `python3`, mount,
or a non-zero applier exit logs a `WARN:` and continues. `DEVCONTAINER=1` is set
in `containerEnv`, so `skipDevcontainer` entries drop out automatically.

`claude mcp add` does not validate that a stdio `command` resolves, and
`replace: false` makes the entry sticky in the persistent-volume
`~/.claude.json`. `postCreate.sh` installs the cbm binary to `/usr/local/bin`
well before the Claude step, so the ordering is safe in the normal case — but if
that install is skipped (unset `CBM_VERSION` or an unsupported architecture, both
of which only `WARN`), the container registers a server whose command is missing
and no later `postStart.sh` repairs it. Recover by hand inside the container:

```sh
claude mcp remove --scope user cbm
```

Watch the `platforms` key when adding a server that must work in the container.
Containers share the host kernel, so the applier's platform detection reads the
*host* kernel release from inside the container: a WSL2-hosted container reports
`wsl2`, while a macOS-hosted (OrbStack) one reports `linux`. An entry meant for
the devcontainer therefore needs **both** `linux` and `wsl2` listed, which is why
`cbm`'s `platforms` carries both. Omitting either would silently skip the
container on one of the two supported hosts.

## Native Windows

`.chezmoiignore` whitelists `claude_mcp_servers.ps1`, so native Windows applies
register both DeepWiki and cbm. The `cbm` entry lists `windows` in `platforms`,
and `codebase-memory-mcp` resolves from `~/.local` on `PATH`, so the bare
`command` starts.

The PowerShell hook parses the config with `ConvertFrom-Json`. Read list-valued
keys through `Get-JsonProperty`, which wraps arrays with a unary comma: a bare
`return @()` would enumerate the array away and hand the caller `$null`, and a
one-element list would collapse to a scalar string that the `string list`
validation then rejects.

If the hook is skipped (no `claude` on `PATH` during the apply), register cbm by
hand:

```powershell
claude mcp add --transport stdio --scope user cbm -- codebase-memory-mcp
```

The `agent-engineer` and `special-builder` subagents work on Windows regardless,
because they declare the server in their own frontmatter.

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
