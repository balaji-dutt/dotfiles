<!-- markdownlint-disable MD013 MD041 -->

# Terminal tab titles for AI agent sessions

Names an iTerm2 tab (or Windows Terminal tab) after the agent running in it —
`claude · fix/titles` — instead of whichever MCP server the agent spawned.

## The problem

iTerm2's default `Title Components` value is `iTermTitleComponentsJob`, which
resolves `iTermVariableKeySessionJob`: *"name of the deepest foreground job
attached to the tty"*, computed by `iTermProcessInfo.deepestForegroundJob`.

A stdio MCP server is a child of the agent, on the same tty and in the same
process group, so it qualifies as a foreground job and it is deeper than the
agent:

```text
PID   PPID  PGID  TPGID  TTY      STAT COMM
11721 37422 11721 11721  ttys004  Ss+  /opt/homebrew/bin/claude
11774 11721 11721 11721  ttys004  S+   codebase-memory-mcp   <- wins
```

So the tab reads `codebase-memory-mcp`.

tmux's own `automatic-rename` picks names the same way and inherits the same
flaw, which is why the fix sets titles explicitly rather than relying on either
heuristic.

## Manual iTerm2 setup (not managed by chezmoi)

iTerm2 rewrites its plist from memory when it quits, so `defaults write`
against a running iTerm2 is silently reverted. Make these changes in the GUI.

Apply to the **Default** and **Dev Container Terminal** profiles. Leave the SSH
profiles alone — no MCP servers run there.

1. **Settings → Profiles → General → Title:** choose **Session Name**.
2. **Settings → Profiles → General:** tick **Applications in terminal may
   change the title**, directly beneath the Title dropdown.

The checkbox is greyed out until step 1 is done. iTerm2's own help text for it:
*"Can the session title be changed by control sequence? Note that this is only
enabled if Session or Profile & Session are selected in the title menu above."*
It is not on the Terminal tab, which is where it is easy to go looking for it.

### Leave title reporting off

**Settings → Profiles → Terminal → Emulation features → "Terminal may report
window title"** is a different setting and should stay off. Title *reporting*
lets a remote host echo a crafted title back into the shell's input buffer.
Title *setting* only changes what the tab says.

### Resulting plist keys

Under `New Bookmarks` in `~/Library/Preferences/com.googlecode.iterm2.plist`,
per profile:

| Key | Value | Meaning |
| --- | --- | --- |
| `Title Components` | `1` | `iTermTitleComponentsSessionName` |
| `Allow Title Setting` | `true` | OSC 0/1/2 may set the title |
| `Allow Title Reporting` | `false` | title is never reported back |

The bit values come from `iTermTitleComponents` in
`sources/Settings/Profiles/ITAddressBookMgr.h` (gnachman/iTerm2):
`SessionName = 1<<0`, `Job = 1<<1`, `Custom = 1<<4`, `ProfileName = 1<<5`. The
SSH profiles sit at `34` (`Job + ProfileName`).

To read the current values back:

```sh
defaults export com.googlecode.iterm2 - | python3 -c '
import plistlib, sys
for p in plistlib.load(sys.stdin.buffer)["New Bookmarks"]:
    print(p["Name"], p.get("Title Components"), p.get("Allow Title Setting"))
'
```

## What the repo provides

| File | Role |
| --- | --- |
| `dot_local/share/zsh/55-terminal-title.zsh.tmpl` | `preexec` hook that titles the tab on agent launch |
| `bin/executable_ai-wt.tmpl` | `terminal_title()` / `emit_terminal_title()`, called from `launch_child()` |

### zsh hook

Fires only for `claude`, `claude-plannotator`, `opencode`,
`opencode-plannotator`, and `opencode-plannotator-custom`, so the two `git`
calls it makes do not run on every command. `ai-wt` is deliberately excluded —
it sets its own title from the real worktree branch.

The title context is the branch when it is a feature branch, otherwise the repo
directory name, falling back to the cwd basename outside a repo:

| Location | Title |
| --- | --- |
| `dotfiles` on `main` | `claude · dotfiles` |
| `dotfiles` on `fix/titles` | `claude · fix/titles` |
| detached HEAD | `claude · dotfiles` |
| `/tmp` | `claude · tmp` |

It emits OSC 0 rather than calling oh-my-zsh's `title` helper, because inside
tmux that helper emits screen's `\ek...\e\\`, which sets the tmux window name
and never reaches the outer terminal.

Ordering matters: `dot_zshrc.tmpl` sources this file after `antidote load`, so
the hooks register after oh-my-zsh's `omz_termsupport_preexec` and
`omz_termsupport_precmd`. The `preexec` hook therefore wins when an agent
starts. The companion `precmd` hook renders oh-my-zsh's idle tab title and
writes it through OSC 0 when the prompt returns. This uses the same title
channel as the explicit agent title rather than relying on oh-my-zsh's separate
OSC 1 and OSC 2 updates, which can leave iTerm's Session Name showing the last
application after it exits. Because OSC 0 also changes the window title, the
hook follows it with OSC 2 to restore `ZSH_THEME_TERM_TITLE_IDLE`; the normal
short-tab/long-window split remains intact.

The hook also returns early when stdout is not a terminal, so escape bytes
never land in redirected output such as `zsh -ic claude | tee log`.

Set `DISABLE_AUTO_TITLE=true` to opt out; both the hook and `ai-wt` honour it,
matching oh-my-zsh.

### ai-wt

`emit_terminal_title()` writes OSC 0 to stderr and no-ops when stderr is not a
terminal, when `DISABLE_AUTO_TITLE=true`, or on native Windows outside Windows
Terminal. That last guard exists because legacy conhost only interprets OSC
once `ENABLE_VIRTUAL_TERMINAL_PROCESSING` is set on the handle, which Python
does not do for stderr, so the escape would print as literal text. Windows
Terminal sets `WT_SESSION`, which is how the guard detects it; under WSL2
`ai-wt` runs as ordinary POSIX Python and is unaffected.

### Why there is no tmux config

There is deliberately no `~/.config/tmux/tmux.conf`. Commit `a9fb6251` added one
containing `set -g set-titles on` and `set -g set-titles-string '#{pane_title}'`,
to stop AoE tabs reading `tmux`. It was reverted, for two reasons.

AoE starts tmux without `-f` and exports `XDG_CONFIG_HOME`, so any file at that
path becomes the config its server reads — and AoE's `tmux.status_bar`,
`tmux.clipboard`, and `tmux.mouse` settings all default to `"auto"`, which means
"step aside once the user has a tmux config". The file cost us AoE's themed
status bar; tmux fell back to its own `bg=green,fg=black` default, which is
unreadable. `private_dot_config/agent-of-empires/modify_config.toml` now pins
`status_bar` and `clipboard` to `"enabled"` so that cannot happen silently
again.

`set-titles` is also server-wide, and there is no per-client form. The tmux
server pushes a title to every terminal hosting a client, including the tab
running the `aoe` TUI dashboard, and it has no idea which session that dashboard
is previewing — so the tab ended up named after whichever session last triggered
a write. AoE itself emits no OSC 0 or OSC 2, has no focus or preview hook
(`on_create` / `on_launch` / `on_destroy` only), and its plugin workers are
separate processes that do not own the TUI's stdout, so nothing outside AoE can
name that tab correctly.

What names it instead: oh-my-zsh's `termsupport` `preexec`, which emits
`\e]1;aoe\a`. The hook in `55-terminal-title.zsh` returns early for commands
outside its allowlist, so that title survives, and no `precmd` fires while `aoe`
is in the foreground. That is a real dependency — drop `ohmyzsh/ohmyzsh path:lib`
from `dot_zsh_plugins.txt.tmpl`, or set `DISABLE_AUTO_TITLE=true`, and the
dashboard tab loses its name.

When AoE exits, `_agent_title_precmd` sends the idle cwd title through OSC 0,
so the dashboard name does not remain stuck on the direct iTerm tab. Inside a
tmux pane the same sequence only updates `pane_title`; with `set-titles` off it
is not forwarded to the outer AoE dashboard tab.

If any of this is revisited: a running tmux server does not pick up config
changes, and AoE applies its per-session options at session creation. Restart
the server (or kill all AoE sessions) before judging the result.

## Verification

```sh
# 1. iTerm2 honours OSC at all. Tab should read "probe".
printf '\033]0;probe\007'

# 2. Direct launch. Tab should read "claude · <branch>".
exec zsh
claude

# 3. AoE reset. Start aoe, then quit it. The tab should return to the cwd title.
aoe

# 4. The MCP server is still running, it just no longer wins.
ps -eo pid,ppid,pgid,tpgid,tty,comm | grep -E 'claude|codebase-memory'

# 5. ai-wt helpers.
python3 tests/test_ai_wt.py

# 6. No tmux config, and AoE owns the status bar. Run on a server started
#    after the config change; a running server does not reload.
test ! -e ~/.config/tmux/tmux.conf
tmux show-options -g set-titles   # must report: set-titles off

# AoE sets status-style, status-left and status-right per session, not
# globally, so -g reports tmux's untouched defaults even when the bar is
# styled correctly. Run this one from inside an AoE session; show-options
# without -g resolves to the current session. Empty output is the failure.
# Match on #{@aoe_title}, not "aoe:" - a #[...] style escape splits that
# prefix in the raw format string.
tmux show-options status-right    # must contain: #{@aoe_title}
```

Quitting an agent or another foreground application returns the tab to the cwd
title when the next prompt appears.
