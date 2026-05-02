<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Plannotator Port Pools

Plannotator uses a browser origin for review UI settings. Firefox Temporary
Containers make random localhost ports inconvenient because Multi-Account
Containers can only pin by host/port. A single fixed port keeps settings stable
but blocks concurrent OpenCode sessions.

This repo uses small fixed pools instead:

| Environment | Workflow | Ports | Plannotator setting |
| :--- | :--- | :--- | :--- |
| Host/WSL | Build handoff | `8997,8998,8999` | switch to Build |
| Host/WSL | Stay-current custom | `9007,9008,9009` | stay on current agent |
| Devcontainer | Build handoff | `9997,9998,9999` | switch to Build |
| Devcontainer | Stay-current custom | `10007,10008,10009` | stay on current agent |

Plain `opencode` still uses `PLANNOTATOR_PORT=8999` as the direct fallback.
That fallback is intentionally part of the build pool so the same Firefox origin
keeps its settings. Avoid mixing plain `opencode` with wrapper-managed sessions
when concurrency matters; plain sessions do not take the wrapper lock before
Plannotator starts. Devcontainers have the same direct fallback behavior on
`9999`.

## Wrapper commands

Use the wrappers when concurrent Plannotator sessions are expected:

```sh
opencode-plannotator         # build-handoff pool
opencode-plannotator-custom  # stay-current custom pool
```

The wrappers choose an available port from the configured pool, export
`PLANNOTATOR_PORT` only for the child OpenCode process, and hold a `flock` lock
until that process exits.

For diagnostics:

```sh
OPENCODE_PLANNOTATOR_DRY_RUN=1 opencode-plannotator
OPENCODE_PLANNOTATOR_DRY_RUN=1 opencode-plannotator-custom
```

## Agents of Empire

On Linux, the global AoE default uses the build-handoff wrapper:

```toml
[session]
default_tool = "opencode"

[session.agent_command_override]
opencode = "opencode-plannotator"
```

The managed host AoE config only emits this override on Linux targets. Native
Windows and macOS do not receive the Bash wrapper override.

The wrappers depend on Bash and `flock`; native macOS and native Windows should
not use them unless those dependencies are available and tested there.

Do not set `default_tool` to a wrapper command. `default_tool` selects the tool;
`agent_command_override.opencode` changes the command used to launch that tool.

For a custom/stay-current session, create the AoE session with a command override:

```sh
aoe add --cmd opencode --cmd-override opencode-plannotator-custom --launch <repo-path>
```

Use the normal global default for build-handoff sessions:

```sh
aoe add --cmd opencode --launch <repo-path>
```

## Firefox Multi-Account Containers

The pool origins should be assigned to the existing Firefox container named
`localhost`. Use the extension inspector instead of editing Firefox profile files
directly.

Open:

1. `about:debugging#/runtime/this-firefox`
2. Multi-Account Containers
3. Inspect

Confirm the existing seed assignment:

```js
(await browser.storage.local.get("siteContainerMap@@_localhost8999"))["siteContainerMap@@_localhost8999"]
```

The expected local seed currently has `userContextId: "40286"` and belongs to the
`localhost` container. Confirm container identity with:

```js
await browser.contextualIdentities.query({})
```

Create or refresh all pool assignments from the seed:

```js
{
  const seed = (await browser.storage.local.get("siteContainerMap@@_localhost8999"))["siteContainerMap@@_localhost8999"]
  const hosts = [
    "localhost8997",
    "localhost8998",
    "localhost8999",
    "localhost9007",
    "localhost9008",
    "localhost9009",
    "localhost9997",
    "localhost9998",
    "localhost9999",
    "localhost10007",
    "localhost10008",
    "localhost10009",
  ]
  await browser.storage.local.set(Object.fromEntries(
    hosts.map(h => [`siteContainerMap@@_${h}`, { ...seed }])
  ))
}
```

`undefined` from `browser.storage.local.set(...)` is expected.

Verify the stored assignments explicitly:

```js
{
  const all = await browser.storage.local.get(null)
  const entries = Object.fromEntries(Object.entries(all).filter(([k]) =>
    k.startsWith("siteContainerMap@@_localhost89") ||
    k.startsWith("siteContainerMap@@_localhost90") ||
    k.startsWith("siteContainerMap@@_localhost999") ||
    k.startsWith("siteContainerMap@@_localhost100")
  ))
  console.table(entries)
  entries
}
```

Then open Multi-Account Containers' **Manage Site List** UI and confirm the
localhost entries are visible. Do not edit Firefox profile storage files directly
while Firefox is running.

## Plannotator UI settings

Configure these origins once with agent switch set to Build:

- `http://localhost:8997`
- `http://localhost:8998`
- `http://localhost:8999`
- `http://localhost:9997`
- `http://localhost:9998`
- `http://localhost:9999`

Configure these origins once with agent switch set to Disabled / stay-current:

- `http://localhost:9007`
- `http://localhost:9008`
- `http://localhost:9009`
- `http://localhost:10007`
- `http://localhost:10008`
- `http://localhost:10009`

## Manual smoke test

Plannotator slash commands are OpenCode TUI commands, not shell commands.

1. Start `opencode-plannotator`.
2. Ask any trivial question so there is a last assistant message.
3. Type `/plannotator-last` in the OpenCode input box.
4. Confirm Firefox opens on one of `8997..8999`.
5. Repeat with `opencode-plannotator-custom` and confirm `9007..9009`.

For a full approval-path test, start a planning agent and ask it to submit a
one-line test plan through Plannotator.
