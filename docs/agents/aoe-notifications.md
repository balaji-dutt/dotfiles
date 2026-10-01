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
4. **`powershell.exe`** Windows notification banner via
   `~/.local/windows-notify.ps1` (WSL2 host).
5. **`notify-send`** (Linux outside the WSL2 host).

On a WSL2 host, a failed banner does not fall back to `notify-send`.
The helper uses Windows PowerShell 5.1 and the registered Windows PowerShell
notification identity; it does not open an OK-button dialog. Devcontainers
retain their HTTP bridge/Linux behavior. Native Windows OpenCode calls the
same helper directly from its notifier configuration.

The OpenCode notifier plugin uses its own config with
`command.enabled: true` and an explicit binary. We route macOS through
`command` → `terminal-notifier` for the reason described next.

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
  failed (on a WSL2 host, check that the helper is installed and reachable
  through Windows interop).
- **Lines with `backend=suppressed-opencode rc=0`** → an OpenCode tool's
  AoE hook was deliberately skipped; the OpenCode plugin owns the banner.
- **Lines with `backend=powershell-banner rc=0`** → the Windows banner helper
  returned successfully.
- **Lines with `backend=terminal-notifier rc=0`** → notification was
  dispatched. If no banner appeared, check the notification permission
  for `terminal-notifier` in System Settings.

Log fields:

```
<iso-timestamp> status=<waiting|error|ignored> backend=<name|none>
  rc=<0|1> pid=<n> ppid=<n>
  instance=<AOE_INSTANCE_ID> session_id=<AOE_SESSION_ID> tool=<AOE_TOOL>
  session=<AOE_SESSION_TITLE>
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

## Permission notifications in `--auto` mode

On macOS and WSL2, `opencode-notifier-bridge` suppresses the plugin's
`permission` event when the nearest OpenCode ancestor was launched with the
exact `--auto` argument. OpenCode has already approved that permission by the
time the detached notification command runs, so the banner would be stale.

Only the `permission` event is suppressed. The plugin's separate `question`
event remains enabled because an agent question still requires input, and all
other enabled completion/error events keep their existing behavior. Permission
notifications also remain enabled when OpenCode is not running in `--auto`
mode.

The bridge checks a bounded process-ancestor chain and fails open when it cannot
identify the owning OpenCode process, preserving the notification rather than
silently hiding a possible prompt. Native Windows is unchanged because its
notifier configuration invokes PowerShell directly instead of using the
macOS/WSL2 bridge.

## OpenCode inside AoE on WSL2

On a WSL2 host, `@mohak34/opencode-notifier` owns OpenCode notifications both
inside and outside AoE. `opencode-notifier.json.tmpl` calls
`~/bin/opencode-notifier-bridge`, which forwards enabled events to the Windows
banner helper even when `AOE_INSTANCE_ID` is set or tmux reports an `aoe_*`
session. The bridge still suppresses stale `--auto` permission events.

For `AOE_TOOL=opencode` or `opencode-custom`, `~/bin/aoe-notify` suppresses
AoE's waiting/error status hooks on the WSL2 host and logs the decision. Other
AoE tools continue using the status hooks. On macOS and in devcontainers the
bridge still suppresses OpenCode plugin events inside AoE, leaving the existing
AoE ownership intact. AoE status detection is not customized here.

Install the helper and updated hook/bridge together when applying on WSL2;
applying only one side can leave duplicate or missing OpenCode banners. Restart
OpenCode after applying its notifier configuration. Native Windows OpenCode
invokes the helper via `powershell.exe -File` without a Bash bridge.

For Claude sessions on WSL2, the `[status_hooks]` chain should work end-to-end.
Claude state comes from `.claude/settings.json` hooks writing to
`/tmp/aoe-hooks-<uid>/$ID/status`, which AoE classifies cleanly into `running` /
`waiting` / `idle`. As of AoE 1.12.1 the base directory is uid-scoped and each
hook re-checks that it is `drwx------` and owned by the calling uid before
writing. The `powershell.exe` PATH fallback in `aoe-notify` covers the case
where AoE's status-hook child shell does not inherit Windows-interop PATH
additions.

## Who owns the AoE config files

AoE rewrites both `~/.claude/settings.json` and
`~/.config/agent-of-empires/config.toml` in place — on upgrade, and (for the
Claude hooks) behind a startup prompt that blocks until accepted. chezmoi used
to own both files outright, so the two fought on every release. Both are now
`chezmoi:modify-template` sources that merge over whatever is on disk:

- [`dot_claude/modify_private_settings.json`](../../dot_claude/modify_private_settings.json)
  — chezmoi owns `env` / `permissions` / `statusLine` and the hooks declared
  in `dot_claude/settings-base.json`. Every hook **group** whose
  command carries a trailing `# aoe-hooks` sentinel is adopted verbatim from
  disk.
- [`private_dot_config/agent-of-empires/modify_config.toml`](../../private_dot_config/agent-of-empires/modify_config.toml)
  — the live file is the merge base, so settings outside the small overlay and
  any newly added keys survive. Since AoE 1.13.2, application state is stored
  separately in the AoE-owned sibling `state.toml`.

The overlay deliberately pins `session.host_tab_title = true` (AoE 1.16.1),
so the local TUI names its host terminal tab after the selected session. This
matches the upstream default; if the live global value is `false`, chezmoi
overrides it on apply. Profile overrides remain available in AoE.

Consequence: on a fresh machine chezmoi writes base-only hooks, AoE prompts
once on first launch, and its hooks stick from then on.

### After an AoE upgrade

Two failure modes are silent, so check both when the pinned version in
`configs/packages.yaml` moves.

If AoE stops emitting the `# aoe-hooks` sentinel, its hooks stop being adopted
and `chezmoi apply` drops them. Confirm they survive a round trip:

```sh
chezmoi --use-builtin-diff --no-pager diff ~/.claude/settings.json
```

If AoE renames an overlay key, the overlay re-injects the dead name forever.
Check every key it declares still exists in the schema:

```sh
for k in acp.auto_stop_idle_secs acp.max_concurrent_workers \
         session.confirm_before_quit session.default_attach_mode \
         session.default_tool session.delete_to_trash session.host_tab_title \
         session.row_tag session.agent_command_override \
         status_hooks.enabled status_hooks.on_error status_hooks.on_waiting \
         telemetry.enabled tmux.clipboard tmux.status_bar \
         updates.update_check_mode \
         worktree.delete_branch_on_cleanup worktree.enabled \
         worktree.path_template; do
  aoe settings explain "$k" 2>&1 | grep -q "not a known setting" \
    && echo "DEAD KEY: $k"
done
```

The live-file merge can also preserve retired keys that are outside the
overlay. Generated targets may never be opened by AoE before another process
copies them over an already-migrated config, so AoE's one-time migrations
cannot clean them. After an upgrade, review upstream config migrations and any
ignored-key warnings, then mirror the required rename/prune semantics in the
modify-template.

Use `aoe settings explain <section>.<field>` to see whether a value is a
persisted user value or a schema default before adding it to the overlay. Note
that "equals the schema default" is not sufficient reason to drop a key: AoE
1.12.1 persisted `session.default_attach_mode = "live_send"` even though the
default is `tmux`. AoE 1.13.2 uses that one setting for both existing-session
and new-session attach behavior, which is why it remains pinned explicitly.
