<!-- markdownlint-disable MD013 MD040 MD041 -->

# AoE / OpenCode notifications

How notification backends are wired across platforms for
[`~/bin/aoe-notify`](../../bin/executable_aoe-notify.tmpl) (invoked by the
AoE `[status_hooks]` `on_waiting` / `on_error`) and the OpenCode plugin
[`@mohak34/opencode-notifier`](../../private_dot_config/opencode/opencode-notifier.json.tmpl).

## Backend order

`aoe-notify` tries backends in this order and stops on the first success:

1. **HTTP bridge** — when `AOE_NOTIFY_BRIDGE_URL` / `DEV_NOTIFY_BRIDGE` is
   set, or when running inside a devcontainer (then defaults to
   `http://host.docker.internal:6789/notify`).
2. **`terminal-notifier`** (macOS) — preferred over `osascript`; see below.
3. **`osascript`** (macOS) — fallback only.
4. **`powershell.exe`** popup (Windows / WSL2).
5. **`notify-send`** (Linux).

The OpenCode notifier plugin uses its own config — `notification: true`
(macOS osascript) or `command.enabled: true` with an explicit binary. We
route macOS through `command` → `terminal-notifier` for the same reason
described next.

## Why `terminal-notifier` instead of `osascript` on macOS

`osascript -e 'display notification ...'` attributes the banner to **Script
Editor**. On modern macOS, Script Editor does not appear in
**System Settings > Notifications** until it has been the foreground app
of a `display notification` call at least once — which never happens when
`osascript` is invoked from a child process such as `aoe-notify`. The
result: `osascript` returns `0` but no banner ever shows, and the user
has no UI affordance to grant permission.

`terminal-notifier` ships with its own bundle ID, registers in
**System Settings > Notifications** the first time it runs, and is
permissioned like any other app.

It is installed via `brewfile.txt` (`brew "terminal-notifier"`).

## Diagnostic log

`aoe-notify` appends one line per invocation to
`${XDG_CACHE_HOME:-$HOME/.cache}/aoe-notify.log`. Use it to confirm
whether AoE is actually firing the status hook:

- **No new lines while a session sits idle** → AoE's status poller is not
  classifying the session as `waiting` (an upstream AoE issue, especially
  relevant for OpenCode where AoE has no agent-side hook integration and
  relies on tmux pane content polling).
- **Lines with `backend=none rc=1`** → the hook fired but every backend
  failed.
- **Lines with `backend=terminal-notifier rc=0`** → notification was
  dispatched. If no banner appeared, check the notification permission
  for `terminal-notifier` in System Settings.

Log fields:

```
<iso-timestamp> status=<waiting|error|ignored> backend=<name|none>
  rc=<0|1> pid=<n> ppid=<n>
  instance=<AOE_INSTANCE_ID> session=<AOE_SESSION_TITLE>
  project=<AOE_PROJECT_PATH> new_status=<AOE_NEW_STATUS>
```

Set `AOE_NOTIFY_DEBUG=1` for extra stderr output on bridge failures.

## OpenCode notifier on macOS

`opencode-notifier.json.tmpl` switches macOS off of
`notificationSystem: "osascript"` and uses the same `command` mechanism as
Windows/WSL2 — routing through `opencode-notifier-bridge`, which calls
`terminal-notifier` outside AoE. `suppressWhenFocused` is also forced `false`
on macOS because the upstream focus-detection path is marked "untested" in the
plugin README and almost certainly contributes to the "no banner appears"
symptom even outside AoE.

## OpenCode inside AoE on WSL2

AoE's WSL2 OpenCode status detection was fixed upstream after
[agent-of-empires/agent-of-empires#2022](https://github.com/agent-of-empires/agent-of-empires/issues/2022)
and
[`ce6d11c`](https://github.com/agent-of-empires/agent-of-empires/commit/ce6d11cdc2c91381c71350cd1c43ce768a1438cd).
That means both AoE status hooks and `@mohak34/opencode-notifier` can see the
same waiting/error moment. To avoid duplicate banners, this dotfiles setup uses
one notification owner per runtime:

- **Inside AoE:** AoE owns waiting/error banners through `[status_hooks]` and
  `~/bin/aoe-notify`.
- **Native OpenCode:** `@mohak34/opencode-notifier` owns OpenCode banners.

On WSL2, `opencode-notifier.json.tmpl` routes the plugin command through
`~/bin/opencode-notifier-bridge` instead of calling `powershell.exe` directly.
The bridge exits successfully without sending a popup when `AOE_INSTANCE_ID` is
set or the current tmux session name starts with `aoe_`; otherwise it uses the
same PowerShell popup fallback path as `aoe-notify`.

Accepted edge case: a standalone `opencode` launched inside an attached
leftover `aoe_*` tmux session is treated as AoE-owned and its plugin popup is
suppressed.

For Claude sessions on WSL2, the `[status_hooks]` chain should work end-to-end.
Claude state comes from `.claude/settings.json` hooks writing to
`/tmp/aoe-hooks/$ID/status`, which AoE classifies cleanly into `running` /
`waiting` / `idle`. The `powershell.exe` PATH fallback in `aoe-notify` covers
the case where AoE's status-hook child shell does not inherit Windows-interop
PATH additions.
