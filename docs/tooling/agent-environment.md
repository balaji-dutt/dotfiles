# Host agent tool environment

The host `opencode-plannotator`, `opencode-plannotator-custom`, and
`claude-plannotator` launchers use the same non-interactive tool-resolution
policy on macOS and WSL2/Linux. AoE can keep launching them by name. The
`agent-env` command reproduces that policy from a terminal without starting
an agent:

```sh
agent-env python3 --version
agent-env /bin/zsh -c 'command -v python3; python3 --version'
```

## Prerequisites and PATH policy

The launchers require Bash 3.2 or newer and the managed helper at
`~/.local/share/dotfiles/agent-env.sh`. They refuse host launches when that
file is missing or unreadable, or when the mise shim directory is inaccessible
or has no executable `python3` shim. Provision mise and the selected tool
versions explicitly; startup does not create shims or install tools.

The shim directory is the first nonempty choice from:

1. `MISE_SHIMS_DIR`
2. `$MISE_DATA_DIR/shims`
3. `${XDG_DATA_HOME:-$HOME/.local/share}/mise/shims`

It must be absolute. Existing directories are promoted in this order:

1. `$VIRTUAL_ENV/bin`, when a virtual environment is explicitly active
2. `$HOME/bin` (intentional user command overrides)
3. The mise shim directory
4. `$HOME/.local/bin`
5. `$HOME/.bun/bin`
6. On macOS: `/opt/homebrew/bin`, `/opt/homebrew/sbin`, `/usr/local/bin`,
   `/usr/local/sbin`
7. The remaining inherited PATH, in its original order

Promoted entries are moved even when already present later in PATH, and
appear only once. Empty entries are dropped; an unset or empty PATH gets
`/usr/bin:/bin:/usr/sbin:/sbin` as its inherited tail. Repeating normalization
does not change the result. Paths containing spaces or glob characters remain
literal.

Normalization happens before agent executable lookup, including when
`OPENCODE_BIN` or `CLAUDE_BIN` is explicitly set. Arguments, working directory,
other inherited exports (except the OpenCode Go key described below), and the
downstream exit status are preserved.
Plannotator dry-runs exit before loading the helper. The host launchers skip
normalization when `DEVCONTAINER=1`, `REMOTE_CONTAINERS` is nonempty, or
`CODESPACES` is nonempty; container-owned launchers remain separate. Native
Windows is outside this policy.

## Mise and shell boundaries

Environment normalization uses only Bash builtins and filesystem checks. It
does not invoke mise, Python, `brew shellenv`, shell profiles, or network
commands. The command and launchers source a fixed home-relative helper, not
a helper found through PATH or the current project.

Each normalized process exports:

```sh
MISE_AUTO_INSTALL=false
MISE_NOT_FOUND_AUTO_INSTALL=false
MISE_NOT_FOUND_SYSTEM_FALLBACK=false
```

These settings are scoped to the command or agent session, not the global mise
configuration or interactive shell. Missing ordinary selected tool versions
should fail rather than silently choosing system Python. They are not a
universal no-mutation guarantee: running a shim invokes mise, honors project
tool selection, and can encounter explicitly configured lazy installs,
auto-updates, or hooks. Mise may also maintain runtime caches. Avoid such
configuration when tool execution must be read-only. See mise's
[shim documentation](https://mise.jdx.dev/dev-tools/shims.html).

This provides consistent tool selection, not a byte-identical terminal
environment. Interactive Zsh may resolve direct installation paths while
agents resolve shims. Shim dispatch can load project environment for the tool
and its children; it does not load arbitrary mise `[env]` values into the
parent agent. Apart from the OpenCode Go key refresh below, the launchers do
not reconstruct direnv state, interactive aliases, functions, secrets, or
exports absent from the launching process.

Plain `zsh -c` still reads `.zshenv`. A later login shell can reorder PATH via
macOS `path_helper`; an OpenCode `shell.env` plugin can also change the tool
environment. If a child resolves a different tool, inspect those later layers
rather than sourcing the entire interactive `.zshrc`.

## Host OpenCode Go credential refresh

On each host launch, both OpenCode wrappers call `dotfiles_opencode_env` from
the shared helper. It reads the trusted user shell file at
`${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.env` and imports only
`OPENCODE_API_KEY`. This avoids reusing an expired key retained by a persistent
tmux server after the managed file has been refreshed. The config-home path
must be absolute; project `.env` files, `OPENCODE_CONFIG_DIR`, and an inherited
`OPENCODE_ENV_FILE` do not select the credential source.

- A readable regular file (including a symlink to one) is authoritative:
  its nonempty key overrides any inherited key, even a deliberate per-process
  override. An empty or absent key in that file unsets the inherited key;
  any remaining authentication depends on OpenCode's configured sources.
- A genuinely absent file preserves the inherited key or its absence.
- An unreadable, nonregular, dangling, invalid, or unsuccessfully evaluated
  file refuses launch with a generic error. Early exit without completing
  evaluation also refuses launch.

The file is evaluated in a subshell to isolate unrelated exports, profile
selection, PATH, working directory, and shell options. Source output and
tracing are suppressed during credential loading. This is trusted shell
configuration, not a sandbox: commands in the file still execute as the user.
The loader itself uses Bash builtins and does not fetch credentials or run
chezmoi, 1Password, shell profiles, or network commands.

Claude and generic `agent-env` launches do not load this file. Dry-runs and
all three container markers listed above skip the refresh. Container-owned
wrappers and their credential lifecycle are separate and unchanged.

## Compare with a terminal

Run these commands from the same repository directory, with the same intended
virtual environment and project configuration:

```sh
python3 -c 'import sys; print(sys.executable); print(sys.version)'
agent-env python3 -c 'import sys; print(sys.executable); print(sys.version)'
node --version
agent-env node --version
npm --version
agent-env npm --version
agent-env /bin/bash -c 'command -v python3; python3 --version'
agent-env /bin/zsh -c 'command -v python3; python3 --version'
```

Compare versions and `sys.executable`, not just `command -v`: a shim path and
an installation path can select the same interpreter. Outside a virtual
environment, the project-selected installed Python should win over macOS
Command Line Tools Python. With an active virtual environment, its Python
takes precedence. Check other tools used by the failing workflow too.

## Targeted rollout and live verification

Apply the shared dependency and all host entrypoints together, from the
intended chezmoi source checkout, only after approving the live apply:

```sh
chezmoi apply ~/.local/share/dotfiles/agent-env.sh ~/bin/agent-env \
  ~/bin/opencode-plannotator ~/bin/opencode-plannotator-custom \
  ~/bin/claude-plannotator
```

Restart a disposable AoE session so its fresh pane script launches the updated
wrapper. Ask that agent to run the same nonmutating version checks in the same
repository. Do not use a commit or merge as an environment test, or restart
unrelated running sessions.

For an OpenCode Go credential-refresh rollout alone, apply the shared helper
and both OpenCode wrappers together, with separate live-apply approval and
the intended source checkout selected:

```sh
chezmoi apply ~/.local/share/dotfiles/agent-env.sh \
  ~/bin/opencode-plannotator ~/bin/opencode-plannotator-custom
```

Quit and relaunch only the affected OpenCode agent through AoE, then check
`/quota`: Go should authenticate and Claude/OpenAI should retain their usual
behavior. Existing OpenCode processes retain their original environment;
reopening the AoE UI alone does not prove a new agent process was launched.
Do not kill the tmux server, restart unrelated sessions, or print credentials
to diagnose a mismatch.

The isolated wrapper tests cover environment plumbing with fake tools; they
do not prove live provider authentication, mise dispatch, or AoE behavior.
Live tool-resolution acceptance also needs an
installed project-specific Python selection and a missing-version refusal
check using an isolated mise fixture, without changing the real tool store.
If the wrapper, terminal, and agent disagree, capture executable paths and
versions before changing shell or plugin configuration.
