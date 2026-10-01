# Dedicated tmuxp workspaces

`repo-ops <layout>` opens a tmuxp workspace on the `repo-ops` tmux server.
Run `repo-ops --help` (or `repo-ops -h`) for a quick reference to workspace
controls; help works even inside tmux and does not start or attach to a server.
The launcher reads only `~/.config/tmuxp/<layout>.yaml` and uses
`~/.config/tmux/repo-ops.conf` via explicit `-L` and `-f` arguments. It never
loads the default tmux configuration or changes the Agent of Empires server.
The public dotfiles supply the launcher, server theme, and pinned tmuxp tool;
actual workspace YAML belongs in the separate private chezmoi source. macOS
and Debian WSL2 are supported hosts; native Windows is not provisioned.

Install managed packages through the normal dotfiles provisioning workflow and
apply the public and private chezmoi sources separately when ready. Neither
`repo-ops` nor this documentation invokes apply, sync, an agent, or an
infrastructure command. Check `tmux --version` and `tmuxp --version` before
launching; the dedicated theme requires tmux 3.3 or newer. If uv cannot use a
prebuilt wheel or its managed Python runtime is unavailable, provisioning
fails rather than compiling on the host.

The private layouts must use distinct lowercase filename tokens, for example
`<layout>.yaml`, and use matching `session_name: repo-ops-<layout>`. Declare
each intended working directory explicitly, including separate macOS and WSL2
values where necessary. Use tmuxp `before_script` to fail if any required
directory is unavailable, rather than opening a shell in a fallback directory.
No specific repository list or machine path is stored in this public source.

Launch from outside tmux: `repo-ops <layout>`. A second launch of that layout
attaches to the existing session without rebuilding windows or re-running pane
commands. Different layout tokens create separate sessions on the same
dedicated server. Launch from inside any tmux session is rejected; detach
first to avoid nested clients. The launcher refuses a missing/symlinked YAML,
an invalid token, or an existing session without a matching ownership marker.
`tmux ls` checks the default server, not this workspace: use `tmux -L repo-ops
list-sessions -F '#{session_name} #{session_id}'` to inspect the dedicated
server. An unmarked session may be left by an interrupted launch. You can
attach to its displayed session ID with `tmux -L repo-ops attach-session -t
<session-id>` to inspect its panes, but `repo-ops` will not adopt or replace
it. Resolve an interrupted launch manually after checking the session and the
reported lock; do not remove a lock while a launch is active.

Each host shell pane in a private layout can run `repo-ops tag shell` as its
tmuxp `shell_command`. A future backlog TUI pane can run `repo-ops tag
backlog` before executing its command. For a command that must replace the
pane's shell, use tmuxp's `shell` field with `repo-ops pane host backlog --
<command> [args...]`. Container panes should instead use `shell: repo-ops
pane container <key> backlog --` (or `shell` role): that command executes
`devcontainer-launch <key> exec --existing -- zsh -i`. It does not create or
start a container and never falls back to the host shell on error. The
dedicated tmux config keeps exited panes visible (`remain-on-exit`); inspect
the error and resolve a missing or ambiguous container before restarting the
pane. No TUI is required to start the supplied layouts.

Inside a tagged pane, **prefix + G** switches to the unique live opposite
role in the current window. The binding follows pane IDs and role tags, not
positions: unrelated extra panes and zoomed panes do not change its target.
A missing, dead, or duplicate counterpart shows a tmux message and selects
nothing. Put paired roles in the same window; add a counterpart explicitly
when the TUI workflow is ready.

| Keys (prefix is Ctrl+b) | Effect |
| --- | --- |
| Ctrl+b, then Shift+K | Confirm termination of the current layout session and all its windows/panes. Other layouts survive. |
| Ctrl+b, then Shift+R | Confirm killing and restarting the active pane's original command. Sibling panes survive. |
| Ctrl+b, then G | Select the unique live pane with the opposite role in this window. |
| Ctrl+b, then d | Detach; all panes keep running. |

The confirmation identifies the session or pane selected when you pressed the
shortcut. Answer `n` to leave it alone. For native termination without the
custom confirmation, use **Ctrl+b, then :, `kill-session`, Enter**. From outside
tmux, inspect the session ID and use `tmux -L repo-ops kill-session -t
'<session-id>'` (quote literal IDs such as `'$0'`). Termination discards that
session; launch `repo-ops <layout>` again to rebuild it from the private YAML.

At an idle shell prompt, `exec zsh` replaces only that shell and reloads its
configuration while retaining the current directory; use `exec zsh -l` if login
initialization is needed. Pane restart is different: tmux's `respawn-pane`
restarts an exited pane, and `respawn-pane -k` also kills a live command. It
reuses the pane's original launch command and working directory by default,
not tmuxp's `shell_command` keystrokes or a fresh YAML layout. A container
pane still requires an existing running container; restart does not create or
start it.

The dark, Gruvbox-inspired status bar displays `Layout: core` for session
`repo-ops-core` on the left, the numbered window list centered across the bar,
and the clock/date on the right. The window list grows outward as windows are
added; a narrow terminal can still clip it. Mouse clicks select windows.
The default **Ctrl+b, then 0–9** selects the corresponding numbered window
without a custom binding. The colors and segments are built into the dedicated
tmux config; no theme plugin, powerline font, or `tmuxpack` is required.
`tmuxp` is still needed to load the private workspace layouts.

The status list and terminal tab both show a command suffix during foreground
work: `0:dotfiles | git` and `repo-ops: dotfiles | git`. The suffix disappears
at the next Zsh prompt; arguments are never displayed. Interactive Zsh in the
dedicated server records the first command name in a pane-local tmux option,
so shell-backed `git` and `bd` commands show their typed names even when tmux
only detects the wrapper process. The hook runs only on the `repo-ops` socket;
other servers are unaffected. For Bash, container shells without this Zsh
startup hook, or commands that bypass the prompt, tmux falls back to its
detected foreground executable. An interactive shell or dead pane has no
suffix; short commands can finish before the next one-second refresh. Only a
simple command name is accepted by the hook: compound commands, aliases, and
prefixes such as `env` may display a different name or the native fallback.
Window names remain fixed; use tmux's window-rename command (**Ctrl+b, then ,**)
to change one. For iTerm2, enable application title setting as described in
[terminal tab titles](iterm-titles.md); in Windows Terminal, leave **Suppress
application title** disabled in the profile. This server's tmux-managed title
does not change the default or Agent of Empires tmux server.

Tmux keeps sessions after client detach or terminal exit, until they are
killed or the server exits. Attaching in another terminal uses that terminal's
dimensions; tmux resizes panes with the attached clients, so no fixed size is
stored in YAML. Avoid `tmux -L repo-ops kill-server` unless all dedicated
layouts may be discarded.
The default tmux server and its sessions are independent.
After applying the managed tmux theme and Zsh helper through the normal
dotfiles workflow, an already running dedicated server still has its old tmux
options. To update its theme, bindings, status, and titles without discarding
sessions, explicitly reload only that server with `tmux -L repo-ops -f
~/.config/tmux/repo-ops.conf source-file ~/.config/tmux/repo-ops.conf`. No
shell restart is needed for the tmux changes. Existing Zsh panes need
`exec zsh` to load the new command-name hook. Do not reload the default server.
