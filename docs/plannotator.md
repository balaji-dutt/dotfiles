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
but blocks concurrent agent sessions.

This repo uses small fixed pools instead:

| Environment | Workflow | Ports | Plannotator setting |
| :--- | :--- | :--- | :--- |
| Host/WSL | OpenCode build handoff | `8996,8997,8998` | switch to Build |
| Host/WSL | OpenCode stay-current custom | `9007,9008,9009` | stay on current agent |
| Host/WSL | Claude Code plan review | `9017,9018,9019` | Claude Code |
| Devcontainer | OpenCode build handoff | `9996,9997,9998` | switch to Build |
| Devcontainer | OpenCode stay-current custom | `10007,10008,10009` | stay on current agent |
| Devcontainer | Claude Code plan review | `10017,10018,10019` | Claude Code |

Plain `opencode` and plain `claude` still use `PLANNOTATOR_PORT=8999` as the
direct host fallback. Devcontainers have the same direct fallback behavior on
`9999`. Those direct fallback ports are intentionally outside wrapper-managed
pools. Avoid mixing plain sessions with wrapper-managed sessions when
concurrency matters; plain sessions do not take the wrapper lock before
Plannotator starts.

## Wrapper commands

Use the wrappers when concurrent Plannotator sessions are expected:

```sh
opencode-plannotator         # build-handoff pool
opencode-plannotator-custom  # stay-current custom pool
claude-plannotator           # Claude Code pool
```

The wrappers choose an available port from the configured pool, export
`PLANNOTATOR_PORT` only for the child agent process, and hold an advisory file
lock until that process exits.

The OpenCode wrappers also default `ANTHROPIC_SYSTEM_PROMPT_PATH` to `/dev/null`
for the child OpenCode process, unless a non-empty value is already set, so
`opencode-claude-bridge` does not reuse a stale validator-captured Claude Code
system prompt.

The homelab devcontainer intentionally does not use fixed VS Code
`forwardPorts` or Docker-published `appPort` mappings for Plannotator. Use VS
Code attach, manual forwarding, or another explicit forwarding path to reach the
review UI from terminal-only `devcontainer-launch` sessions.
The devcontainer-installed wrappers default to verbose mode so terminal sessions
print the selected port before the agent starts. Host wrappers remain quiet
unless `OPENCODE_PLANNOTATOR_VERBOSE=1` or `CLAUDE_PLANNOTATOR_VERBOSE=1` is set.
Devcontainer wrappers also pause for one second before launching the agent; set
`OPENCODE_PLANNOTATOR_LAUNCH_DELAY_SECONDS=0` or
`CLAUDE_PLANNOTATOR_LAUNCH_DELAY_SECONDS=0` to skip the pause.

OpenCode Plannotator uses the CLI runtime in both host and devcontainer config.
That keeps WSL/devcontainer ready messages inside OpenCode's logging path instead
of letting the embedded runtime write directly to the terminal TUI. Keep
`PLANNOTATOR_REMOTE=1` for fixed-port forwarding behavior, and restart OpenCode
after changing plugin config.

For diagnostics:

```sh
OPENCODE_PLANNOTATOR_DRY_RUN=1 opencode-plannotator
OPENCODE_PLANNOTATOR_DRY_RUN=1 opencode-plannotator-custom
CLAUDE_PLANNOTATOR_DRY_RUN=1 claude-plannotator
```

## Claude Code

Claude Code enables the Plannotator plugin through `~/.claude/settings.json`.
This repo also installs an explicit fallback hook:

```text
PermissionRequest > ExitPlanMode > plannotator
```

Keep the plugin enabled. It still provides the `EnterPlanMode` context hook and
Plannotator slash commands, but `/hooks` may show the EnterPlan hook without the
ExitPlan hook. The explicit settings hook makes the review UI launch path
deterministic.

On WSL, shell startup exports `PLANNOTATOR_REMOTE=1` with the configured
`PLANNOTATOR_PORT`. macOS keeps local browser behavior and does not set remote
mode.

Use `claude-plannotator` for concurrent Claude Code sessions. The wrapper picks
from the Claude Code pool and launches Claude with only that child process's
`PLANNOTATOR_PORT` changed.

If Claude Code does not open the Plannotator UI on ExitPlan:

1. Restart or reload Claude Code after plugin or settings changes.
2. Confirm `plannotator` is on `PATH` in the Claude Code environment.
3. Run `/hooks` and confirm `PermissionRequest > ExitPlanMode > plannotator`.
4. Check the Claude Code plugin errors view for Plannotator load errors.

## Agents of Empire

On Linux and macOS, the global AoE default uses Plannotator wrappers:

```toml
[session]
default_tool = "opencode"

[session.agent_command_override]
claude = "claude-plannotator"
opencode = "opencode-plannotator"
```

The managed host AoE config emits these overrides on Linux and macOS targets.
Native Windows does not receive the Bash wrapper override.

The host wrappers depend on Bash plus either `flock` or `lockf`. macOS hosts use
the bundled `lockf` fallback when `flock` is unavailable; native Windows should
not use these Bash wrappers.

Do not set `default_tool` to a wrapper command. `default_tool` selects the tool;
`agent_command_override` changes the command used to launch that tool.

For a custom/stay-current session, create the AoE session with a command override:

```sh
aoe add --cmd opencode --cmd-override opencode-plannotator-custom --title <TITLE> --launch <repo-path>
```

Use the normal global default for build-handoff sessions:

```sh
aoe add --cmd opencode --launch <repo-path>
```

Claude Code sessions use the Claude wrapper by default:

```sh
aoe add --cmd claude --launch <repo-path>
```

## Firefox Multi-Account Containers

Plannotator stores settings in cookies, so Agent Switching and identity follow
the Firefox container cookie jar for `localhost` rather than the individual port.
Use separate containers for the three workflows:

- `Plannotator Build`: build-handoff ports.
- `Plannotator Custom`: stay-current custom ports.
- `Plannotator Claude`: Claude Code ports.

Use the extension inspector instead of editing Firefox profile files directly.

Open:

1. `about:debugging#/runtime/this-firefox`
2. Multi-Account Containers
3. Inspect

Confirm the three seed assignments after manually adding `localhost8997` to the
Build container, `localhost9007` to the Custom container, and `localhost9017` to
the Claude container:

```js
{
  const keys = [
    "siteContainerMap@@_localhost8997",
    "siteContainerMap@@_localhost9007",
    "siteContainerMap@@_localhost9017",
  ]
  const data = await browser.storage.local.get(keys)
  console.table(data)
  data
}
```

The three seeds should have different `userContextId` values. Confirm container
identity with:

```js
await browser.contextualIdentities.query({})
```

Create or refresh all pool assignments from the three seeds:

```js
{
  const buildSeedKey = "siteContainerMap@@_localhost8997"
  const customSeedKey = "siteContainerMap@@_localhost9007"
  const claudeSeedKey = "siteContainerMap@@_localhost9017"

  const data = await browser.storage.local.get([
    buildSeedKey,
    customSeedKey,
    claudeSeedKey,
  ])
  const buildSeed = data[buildSeedKey]
  const customSeed = data[customSeedKey]
  const claudeSeed = data[claudeSeedKey]

  if (!buildSeed) throw new Error(`Missing seed: ${buildSeedKey}`)
  if (!customSeed) throw new Error(`Missing seed: ${customSeedKey}`)
  if (!claudeSeed) throw new Error(`Missing seed: ${claudeSeedKey}`)

  const buildHosts = [
    "localhost8996",
    "localhost8997",
    "localhost8998",
    "localhost9996",
    "localhost9997",
    "localhost9998",
  ]

  const customHosts = [
    "localhost9007",
    "localhost9008",
    "localhost9009",
    "localhost10007",
    "localhost10008",
    "localhost10009",
  ]

  const claudeHosts = [
    "localhost9017",
    "localhost9018",
    "localhost9019",
    "localhost10017",
    "localhost10018",
    "localhost10019",
  ]

  await browser.storage.local.set({
    ...Object.fromEntries(
      buildHosts.map(h => [`siteContainerMap@@_${h}`, { ...buildSeed }])
    ),
    ...Object.fromEntries(
      customHosts.map(h => [`siteContainerMap@@_${h}`, { ...customSeed }])
    ),
    ...Object.fromEntries(
      claudeHosts.map(h => [`siteContainerMap@@_${h}`, { ...claudeSeed }])
    ),
  })
}
```

`undefined` from `browser.storage.local.set(...)` is expected.

Verify the stored assignments explicitly:

```js
{
  const hosts = [
    "localhost8996",
    "localhost8997",
    "localhost8998",
    "localhost9007",
    "localhost9008",
    "localhost9009",
    "localhost9017",
    "localhost9018",
    "localhost9019",
    "localhost9996",
    "localhost9997",
    "localhost9998",
    "localhost10007",
    "localhost10008",
    "localhost10009",
    "localhost10017",
    "localhost10018",
    "localhost10019",
  ]

  const keys = hosts.map(h => `siteContainerMap@@_${h}`)
  const data = await browser.storage.local.get(keys)

  console.table(Object.fromEntries(
    Object.entries(data).map(([key, value]) => [
      key,
      {
        userContextId: value?.userContextId,
        identityMacAddonUUID: value?.identityMacAddonUUID,
        neverAsk: value?.neverAsk,
      },
    ])
  ))

  data
}
```

Then open Multi-Account Containers' **Manage Site List** UI and confirm the
localhost entries are split between the three Plannotator containers. Do not edit
Firefox profile storage files directly while Firefox is running.

## Plannotator UI settings

In the `Plannotator Build` Firefox container, configure Agent Switching once as
Build:

- `http://localhost:8996`
- `http://localhost:8997`
- `http://localhost:8998`
- `http://localhost:9996`
- `http://localhost:9997`
- `http://localhost:9998`

In the `Plannotator Custom` Firefox container, configure Agent Switching once as
Disabled / stay-current:

- `http://localhost:9007`
- `http://localhost:9008`
- `http://localhost:9009`
- `http://localhost:10007`
- `http://localhost:10008`
- `http://localhost:10009`

In the `Plannotator Claude` Firefox container, configure Claude Code settings
once:

- `http://localhost:9017`
- `http://localhost:9018`
- `http://localhost:9019`
- `http://localhost:10017`
- `http://localhost:10018`
- `http://localhost:10019`

## Manual smoke test

For Claude Code, start `claude-plannotator`, ask for a tiny plan, and stop
before editing:

```text
Enter plan mode and draft a plan for a tiny no-op change: add a temporary comment to README.md, then stop and ask for approval before editing anything.
```

When Claude Code requests approval to exit plan mode, the Plannotator UI should
open on one of `9017..9019` on host/WSL or `10017..10019` in the devcontainer.
Do not approve the README edit unless a real edit is desired.

Plannotator slash commands are OpenCode TUI commands, not shell commands.

1. Start `opencode-plannotator`.
2. Ask any trivial question so there is a last assistant message.
3. Type `/plannotator-last` in the OpenCode input box.
4. Confirm Firefox opens on one of `8996..8998` on host/WSL or `9996..9998`
   in the devcontainer.
5. Repeat with `opencode-plannotator-custom` and confirm `9007..9009` on
   host/WSL or `10007..10009` in the devcontainer.

For a full approval-path test, start a planning agent and ask it to submit a
one-line test plan through Plannotator.
