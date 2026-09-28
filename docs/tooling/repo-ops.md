# Dedicated tmuxp workspaces

`repo-ops <layout>` opens a tmuxp workspace on the `repo-ops` tmux server.
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
launching. If uv cannot use a prebuilt wheel or its managed Python runtime is
unavailable, provisioning fails rather than compiling on the host.

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
Resolve an interrupted launch manually by inspecting `tmux -L repo-ops
list-sessions` and the reported lock before removing any stale lock. It does
not replace or kill unmarked sessions.

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

Tmux keeps sessions after client detach or terminal exit, until they are
killed or the server exits. Attaching in another terminal uses that terminal's
dimensions; tmux resizes panes with the attached clients, so no fixed size is
stored in YAML. Run `tmux -L repo-ops list-sessions` to inspect them and
`tmux -L repo-ops kill-session -t =repo-ops-<layout>` to discard one when
intentionally reloading its YAML. Then run `repo-ops <layout>` again. Avoid
`tmux -L repo-ops kill-server` unless all dedicated layouts may be discarded.
The default tmux server and its sessions are independent.
