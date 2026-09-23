<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Plannotator Port Ranges

Plannotator uses a browser origin for review UI settings. Firefox Temporary
Containers make random localhost ports inconvenient because Multi-Account
Containers can only pin by host/port. A single fixed port keeps settings stable
but blocks concurrent agent sessions.

This repo uses bounded native ranges. The canonical definitions are in
[`.chezmoidata.yaml`](../.chezmoidata.yaml)
under `plannotator_ports.host` and `plannotator_ports.devcontainer`. Templates
consume those values for the corresponding `PLANNOTATOR_PORTS_*` variables.

| Environment | Workflow | Ports | Plannotator setting |
| :--- | :--- | :--- | :--- |
| Host/WSL | OpenCode build handoff | `8993-8998` | switch to Build |
| Host/WSL | OpenCode stay-current custom | `9004-9009` | stay on current agent |
| Host/WSL | Claude Code plan review | `9014-9019` | Claude Code |
| Devcontainer | OpenCode build handoff | `9993-9998` | switch to Build |
| Devcontainer | OpenCode stay-current custom | `10004-10009` | stay on current agent |
| Devcontainer | Claude Code plan review | `10014-10019` | Claude Code |

Plain `opencode` and plain `claude` still use `PLANNOTATOR_PORT=8999` as the
direct host fallback. Devcontainers have the same direct fallback behavior on
`9999`. Those direct fallback ports are intentionally outside the native
ranges. Plain sessions still share one fixed port per environment, so two plain
sessions cannot host review UIs concurrently on that fallback.

The host fallback comes from the separate top-level `plannotator_port` in
`.chezmoidata.yaml`. The container fallback is set directly as
`PLANNOTATOR_PORT` in the homelab `devcontainer.json.tmpl`; it does not use the
host fallback value.

## Wrapper commands

On macOS, Linux, WSL2, and devcontainers, use the wrappers when concurrent
Plannotator sessions are expected:

```sh
opencode-plannotator         # build-handoff range
opencode-plannotator-custom  # stay-current custom range
claude-plannotator           # Claude Code range
```

The wrappers select a workflow range and export it as `PLANNOTATOR_PORT` only
for the child agent process. Plannotator 0.24.2 or newer binds the first
available port when a review UI starts. Idle agent processes do not reserve
ports; each six-port range limits simultaneous review UIs instead.

The OpenCode wrappers also default `ANTHROPIC_SYSTEM_PROMPT_PATH` to `/dev/null`
for the child OpenCode process, unless a non-empty value is already set, so
`opencode-claude-bridge` does not reuse a stale validator-captured Claude Code
system prompt.

The homelab devcontainer intentionally does not use fixed VS Code
`forwardPorts` or Docker-published `appPort` mappings for Plannotator. Use VS
Code attach, manual forwarding, or another explicit forwarding path to reach the
review UI from terminal-only `devcontainer-launch` sessions.
The devcontainer-installed wrappers default to verbose mode so terminal sessions
print the configured profile and range before the agent starts. The selected
port is reported later by Plannotator when review begins. Host wrappers remain
quiet unless `OPENCODE_PLANNOTATOR_VERBOSE=1` or
`CLAUDE_PLANNOTATOR_VERBOSE=1` is set.

In devcontainers, both OpenCode wrappers also guard tracked project dependency
metadata before launch. When a repository tracks `.opencode/package.json` and
`.opencode/package-lock.json`, their exact `@opencode-ai/plugin` version must
match the selected OpenCode CLI. The guard hydrates missing ignored
`node_modules` with `npm ci`, verifies the tracked files are unchanged, and
blocks mismatches with an intentional-update command. It never rewrites tracked
metadata. Plain `opencode` bypasses this container wrapper guard; use a matching
CLI or update the owning repository's metadata before starting that way.

OpenCode Plannotator uses the CLI runtime in both host and devcontainer config.
That keeps WSL/devcontainer ready messages inside OpenCode's logging path instead
of letting the embedded runtime write directly to the terminal TUI. Keep
`PLANNOTATOR_REMOTE=1` for remote browser forwarding behavior, and restart OpenCode
after changing plugin config.

For diagnostics:

```sh
OPENCODE_PLANNOTATOR_DRY_RUN=1 opencode-plannotator
OPENCODE_PLANNOTATOR_DRY_RUN=1 opencode-plannotator-custom
CLAUDE_PLANNOTATOR_DRY_RUN=1 claude-plannotator
```

Dry-run output reports the selected profile and native range; it does not probe
or reserve an individual port. Start a fresh shell after applying this change so
the old comma-separated `PLANNOTATOR_PORTS_*` values are not inherited.

## Native Windows CLI provisioning

Native Windows admits a binary-only onchange hook that installs the
Renovate-pinned Plannotator release as `~/.local/plannotator.exe`.
`windows-bootstrap.ps1` already owns `~/.local` in the user and apply-process
`PATH`. The hook selects the upstream `win32-x64` or `win32-arm64` asset, requires
the matching `.sha256` sidecar entry, verifies the downloaded candidate, and
checks its reported version before publication.

This path intentionally differs from Plannotator's upstream Windows installer.
The hook downloads only the CLI binary; it does not run upstream `install.ps1`
or modify Claude Code/OpenCode plugins, agents, skills, or MCP configuration.
Those remain declarative dotfiles state.

Publication uses a staged executable and retires the current binary only long
enough to validate the replacement. A failed publication rolls the prior binary
back. Windows does not permit replacing an executable that is still running; if
that lock is detected, close Plannotator and retry `chezmoi apply`. The existing
binary is left intact for that retry.

Set `PLANNOTATOR_INSTALL_DRY_RUN=1` before the hook runs to report the pinned
version, native architecture, release URL, and destination without creating
directories, downloading, or publishing. After a real first installation,
restart terminals, OpenCode, and Claude Code that inherited the old `PATH`, then
verify from a fresh PowerShell process:

```powershell
Get-Command plannotator
plannotator --version
```

Native Windows intentionally does not install PowerShell or Python equivalents
of the POSIX agent wrappers. Use the supported `ai-wt` launcher to select an
OpenCode pool:

```powershell
ai-wt opencode feat/example-change
ai-wt opencode --opencode-profile custom feat/custom-agent-change
```

The first command defaults to the build range; the second selects the custom
range. `ai-wt` launches the native `opencode.exe` directly and supplies the
selected `PLANNOTATOR_PORT` and `OPENCODE_PLANNOTATOR_POOL` to that child.

The managed PowerShell OpenCode environment also defaults
`ANTHROPIC_SYSTEM_PROMPT_PATH` to the native `NUL` device and disables Claude
Code prompt and skill imports. This prevents `opencode-claude-bridge` from
reusing a stale Claude Code system prompt in direct OpenCode sessions. A
non-empty custom prompt path or disable-variable value still wins. Start a fresh
PowerShell and restart OpenCode after applying the change.

## Claude Code

Claude Code enables the Plannotator plugin through `~/.claude/settings.json`.

Keep the plugin enabled. It ships hooks and nothing else — a `PreToolUse` hook on
`EnterPlanMode` that runs `plannotator improve-context`, and a
`PermissionRequest` hook on `ExitPlanMode` that runs `plannotator`. Both come
from the plugin's own `hooks/hooks.json`, so this repo adds no `PermissionRequest`
entry of its own; a duplicate in `settings.json` would launch `plannotator` twice
per plan approval, with both instances contending for the same hook stdin.

The plugin does **not** supply the `/plannotator-*` slash commands. Those are
vendored into this repo — see [Vendored slash commands](#vendored-slash-commands).

On WSL, shell startup exports `PLANNOTATOR_REMOTE=1` with the configured
`PLANNOTATOR_PORT`. macOS keeps local browser behavior and does not set remote
mode.

Use `claude-plannotator` for concurrent Claude Code sessions. The wrapper gives
the child process the Claude range, and Plannotator selects a port when review
starts.

If Claude Code does not open the Plannotator UI on ExitPlan:

1. Restart or reload Claude Code after plugin or settings changes.
2. Confirm `plannotator` is on `PATH` in the Claude Code environment.
3. Run `/hooks` and confirm `PermissionRequest > ExitPlanMode > plannotator`.
4. Check the Claude Code plugin errors view for Plannotator load errors.

## Vendored slash commands

`/plannotator-annotate`, `/plannotator-last` and `/plannotator-review` reach both
harnesses through files this repo vendors, not through the plugin or the npm
package.

Upstream delivers them from `scripts/install.sh`, which copies
`apps/skills/claude/plannotator-*` into `~/.claude/skills` and, for OpenCode,
relies on the `@plannotator/opencode` package's `postinstall` to drop
`commands/*.md` into `~/.config/opencode/commands`. Provisioning here installs
only the checksum-verified CLI binary and never runs that installer, so the copy
step never happens. Six tracked files stand in for it:

| Target | Source in `backnotprop/plannotator` |
| :--- | :--- |
| `dot_claude/skills/plannotator-*/SKILL.md` | `apps/skills/claude/plannotator-*/SKILL.md` |
| `private_dot_config/opencode/commands/plannotator-*.md` | `apps/opencode-plugin/commands/plannotator-*.md` |

Claude Code needs the full skill bodies because nothing intercepts the command —
each skill runs `plannotator` itself and acts on the output. The OpenCode files
are frontmatter-only stubs; there, the plugin intercepts the command at runtime.
Do not give the OpenCode stubs a body: it becomes a preamble message, and
`/plannotator-last` would then annotate that preamble instead of the real reply.

Do not add `dot_claude/commands/plannotator-*.md` alongside the skills. Upstream
`install.sh` deletes those whenever the matching skill directory exists.

`configs/plannotator-assets.json` records each file's upstream path and `sha256`.
It does not duplicate the tag or release URLs: the sync helper reads
`plannotator_version` from `.chezmoidata.yaml` and derives the source URL from
that single pin. Check and refresh with:

```sh
python3 assets/sync-plannotator-assets.py --check
python3 assets/sync-plannotator-assets.py --write
bash ./assets/sync-devcontainer-assets.sh
```

The tag is deliberately coupled to the CLI pin. These files call `plannotator`
subcommands by name, and upstream already spells the same operation two ways
(`plannotator last` under `apps/skills/core/`, `plannotator annotate-last` under
`apps/skills/claude/`). Without the coupling, a Renovate CLI bump that renamed a
subcommand would break the vendored copies silently. With it, the bump fails
`--check` until someone re-syncs, and `--write` follows the shared pin directly.

Two limits of that coupling, both intentional:

- It binds the vendored files to the **CLI** pin in `.chezmoidata.yaml` only. The
  `plannotator@plannotator` entry in `configs/host-ai-plugin-refresh.jsonc` is a
  separate Renovate sentinel for the Claude Code plugin, and that file's own
  header notes its versions are notification triggers rather than enforced pins.
  Nothing asserts the two agree, and they can land in separate PRs.
- It does not cover the container-dotfiles mirrors under
  `private_Documents/development/container-dotfiles/`. Those are generated for
  every mirrored asset by `assets/sync-devcontainer-assets.sh` and guarded by
  `configs/devcontainer-sync.jsonc`, the same as the other mirrored skills; run
  that script after `--write` rather than expecting `--check` to notice.

## Agents of Empire

On Linux and macOS, the global AoE default uses Plannotator wrappers:

```toml
[session]
default_tool = "opencode"

[session.agent_command_override]
claude = "claude-plannotator"
opencode = "opencode-plannotator"

[session.agent_detect_as]
opencode-custom = "opencode"

[session.custom_agents]
opencode-custom = "opencode-plannotator-custom"
```

The managed host AoE config emits these overrides on Linux and macOS targets.
Native Windows does not receive the Bash wrapper override.

In the homelab devcontainer, these existing AoE overrides also put normal and
custom OpenCode sessions behind the project dependency guard. No separate AoE
launcher is required.

The host wrappers depend on Bash. Port probing and `flock`/`lockf` are no longer
required because Plannotator owns range allocation. Native Windows should not
use these Bash wrappers.

Do not set `default_tool` to a wrapper command. `default_tool` selects the tool;
`agent_command_override` changes the command used to launch that tool.

The built-in `opencode` agent continues to use the build-handoff wrapper. Select
the separate custom agent for a stay-current session:

```sh
aoe add --tool opencode-custom --title <TITLE> --launch <repo-path>
```

Use the normal global default, or name the built-in agent explicitly, for a
build-handoff session:

```sh
aoe add --launch <repo-path>
aoe add --tool opencode --launch <repo-path>
```

Claude Code sessions use the Claude wrapper by default:

```sh
aoe add --cmd claude --launch <repo-path>
```

The managed `auto` profile passes OpenCode's `--auto` CLI flag to both OpenCode
agents:

```sh
aoe --profile auto add --tool opencode --launch <repo-path>
aoe --profile auto add --tool opencode-custom --launch <repo-path>
```

The default profile does not add the flag. The `auto` profile affects newly
launched processes; it does not change an already-running OpenCode session.
This intentionally avoids AoE YOLO mode, which injects an
`OPENCODE_PERMISSION` environment override that can be shadowed by stricter
permission configuration. OpenCode `--auto` auto-approves permissions that are
not explicitly denied.

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

Create or refresh all range assignments from the three seeds:

```js
{
  const seedKeys = {
    build: "siteContainerMap@@_localhost8997",
    custom: "siteContainerMap@@_localhost9007",
    claude: "siteContainerMap@@_localhost9017",
  }
  const spans = {
    build: [[8993, 8998], [9993, 9998]],
    custom: [[9004, 9009], [10004, 10009]],
    claude: [[9014, 9019], [10014, 10019]],
  }
  const seedData = await browser.storage.local.get(Object.values(seedKeys))

  for (const [profile, key] of Object.entries(seedKeys)) {
    if (!seedData[key]) throw new Error(`Missing ${profile} seed: ${key}`)
  }

  const entries = []
  for (const [profile, profileSpans] of Object.entries(spans)) {
    for (const [start, end] of profileSpans) {
      for (let port = start; port <= end; port += 1) {
        entries.push([
          `siteContainerMap@@_localhost${port}`,
          { ...seedData[seedKeys[profile]] },
        ])
      }
    }
  }

  await browser.storage.local.set(Object.fromEntries(entries))
  console.log(`Assigned ${entries.length} Plannotator hosts`)
}
```

`undefined` from `browser.storage.local.set(...)` is expected.

Verify the stored assignments explicitly:

```js
{
  const spans = {
    build: [[8993, 8998], [9993, 9998]],
    custom: [[9004, 9009], [10004, 10009]],
    claude: [[9014, 9019], [10014, 10019]],
  }
  const rows = []

  for (const [profile, profileSpans] of Object.entries(spans)) {
    for (const [start, end] of profileSpans) {
      for (let port = start; port <= end; port += 1) {
        rows.push({
          profile,
          port,
          key: `siteContainerMap@@_localhost${port}`,
        })
      }
    }
  }

  const data = await browser.storage.local.get(rows.map(row => row.key))
  const report = rows.map(({ profile, port, key }) => ({
    profile,
    port,
    userContextId: data[key]?.userContextId,
    identityMacAddonUUID: data[key]?.identityMacAddonUUID,
    neverAsk: data[key]?.neverAsk,
  }))
  console.table(report)

  const missing = report.filter(row => row.userContextId == null)
  if (missing.length) throw new Error(`Missing ${missing.length} assignments`)

  const idsByProfile = Object.fromEntries(
    Object.keys(spans).map(profile => [
      profile,
      new Set(
        report
          .filter(row => row.profile === profile)
          .map(row => row.userContextId)
      ),
    ])
  )
  if (Object.values(idsByProfile).some(ids => ids.size !== 1)) {
    throw new Error("A profile spans multiple Firefox containers")
  }

  const profileIds = Object.values(idsByProfile).map(ids => [...ids][0])
  if (new Set(profileIds).size !== Object.keys(spans).length) {
    throw new Error("Profiles do not use distinct Firefox containers")
  }

  console.log("Verified 36 assignments across 3 distinct containers")
}
```

Then open Multi-Account Containers' **Manage Site List** UI and confirm the
localhost entries are split between the three Plannotator containers. Do not edit
Firefox profile storage files directly while Firefox is running.

## Plannotator UI settings

In the `Plannotator Build` Firefox container, open
`http://localhost:8997` and configure Agent Switching once as Build. In the
`Plannotator Custom` container, open `http://localhost:9007` and configure it as
Disabled / stay-current. Configure Claude Code once in the `Plannotator Claude`
container at `http://localhost:9017`.

All mapped ports for a profile share that Firefox container's cookie jar, so the
settings apply across both host and devcontainer ranges.

## Manual smoke test

For Claude Code, start `claude-plannotator`, ask for a tiny plan, and stop
before editing:

```text
Enter plan mode and draft a plan for a tiny no-op change: add a temporary comment to README.md, then stop and ask for approval before editing anything.
```

When Claude Code requests approval to exit plan mode, the Plannotator UI should
open on one of `9014..9019` on host/WSL or `10014..10019` in the devcontainer.
Do not approve the README edit unless a real edit is desired.

Plannotator slash commands are typed into the OpenCode or Claude Code input box.
They are not shell commands.

OpenCode:

1. Start `opencode-plannotator`.
2. Ask any trivial question so there is a last assistant message.
3. Type `/plannotator-last` in the OpenCode input box.
4. Confirm Firefox opens on one of `8993..8998` on host/WSL or `9993..9998`
   in the devcontainer.
5. Repeat with `opencode-plannotator-custom` and confirm `9004..9009` on
   host/WSL or `10004..10009` in the devcontainer.

Claude Code:

1. Start `claude-plannotator`.
2. Ask any trivial question so there is a last assistant message.
3. Type `/plannotator-last` in the Claude Code input box.
4. Confirm Firefox opens on one of `9014..9019` on host/WSL or `10014..10019`
   in the devcontainer.
5. Repeat `/plannotator-review` in a worktree with uncommitted changes.

If the commands do not appear in Claude Code, the vendored skills are missing or
unapplied — see [Vendored slash commands](#vendored-slash-commands).

For a full approval-path test, start a planning agent and ask it to submit a
one-line test plan through Plannotator.
