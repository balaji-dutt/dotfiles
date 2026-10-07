<!-- markdownlint-disable MD013 MD040 MD041 -->

# Peer environments

`assets/peer-env` lets an agent working on one machine verify a change on the
other platform without leaving the checkout. The MacBook (macOS) and the WSL2
Debian distro each hold a clone of this repo at
`~/Documents/development/dotfiles`; when the other machine is awake and on the
home network, the helper ships a snapshot of the working tree to it, runs
`./assets/cz-audit.sh check` or `./assets/run-tests.sh` there in a disposable
worktree, and streams the result back. When the other machine is asleep the
helper says so within a few seconds and the agent reports the platform as not
verified.

Native Windows is not a peer yet. See [Not yet: native Windows](#not-yet-native-windows).

## The boundary

Letting an agent on machine A run anything on machine B widens the T0 blast
radius described in `docs/decisions/0002-agent-isolation-tiers.md`. The peer,
not the client, decides which requests are accepted, and what the restricted
key grants is stated plainly here:

- The key grants code execution as the peer user through the snapshot's
  content. `serve` runs the snapshot's own `./assets/cz-audit.sh` and
  `./assets/run-tests.sh`, and the test suite executes the snapshot's test
  files, so whoever can push a snapshot can run what they put in it. That is
  the same exposure as running the suite locally on each machine, which
  agents already do; the controls below bound the transport, the environment,
  and what `serve` itself writes, not what the suite can do.
- The peer side is `peer-env serve`, pinned as the forced command of a
  dedicated SSH key (`restrict,command=...` in the peer's `authorized_keys`).
  A client holding that key gets no shell, no pty, and no agent or port
  forwarding; it can only send the requests listed under [Protocol](#protocol).
- `serve` accepts two entrypoints only: `./assets/cz-audit.sh check <path>`
  and `./assets/run-tests.sh <suite> [--require-capabilities]`, with the
  snapshot worktree as the working directory. The protocol has no verb for an
  arbitrary command.
- `serve` never sources a login shell. The child environment is built from an
  allowlist: `PATH` (sshd's PATH plus `/opt/homebrew/bin`, `/usr/local/bin`,
  `~/.local/bin`, and the mise shims directory when they exist), `HOME`,
  `USER`, `LOGNAME`, `SHELL`, `TMPDIR`, `LANG=C.UTF-8`, `TERM=dumb`,
  `PEER_ENV=1`, and on WSL2 `WSL_DISTRO_NAME` and `WSL_INTEROP`. The peer's
  ambient provider keys never reach the entrypoint, and nothing the client
  sends becomes an environment variable.
- `serve`'s own writes are confined to `worktrees/peer-env-<slug>/`,
  `refs/peer-env/*` plus worktree metadata under `.git/`, and
  `~/.local/state/dotfiles/peer-env/` (log and locks). A `pre-receive` hook
  installed per push through `core.hooksPath` rejects every ref outside
  `refs/peer-env/`. The peer's branches, HEAD, index, and stash are never
  touched by `serve`. The test runner writes to its own temp sandbox.
- The only reads outside the worktree are the ones `cz-audit` performs
  locally too: `chezmoi diff` and `chezmoi apply --dry-run` against the peer's
  `$HOME`. Diff content stays in the peer's `.cz-audit/*.log` because the
  client cannot set `CZ_AUDIT_SHOW`.
- Every request is appended to `~/.local/state/dotfiles/peer-env/serve.log` on
  the peer as one JSON line (time, client address, verb, arguments, exit code,
  duration).
- When `serve` is reached through an ordinary key instead of the forced
  command, the same code runs but nothing enforces it. The probe reports this
  as `reachable (unrestricted)`, and `audit`/`test` refuse such a peer with
  exit 3 unless its config entry sets `"allow_unrestricted": true`. Only the
  owner of both machines sets that flag; for an agent the peer counts as
  unreachable.

This is the verification lane. The ordinary keys and `Host` aliases stay as
they are for operations a task explicitly asks for (an `ansible-playbook` run,
a `chezmoi apply`, a tool upgrade); those go through the harness's `ssh`
permission prompt where one exists and are named in the final response.
OpenCode `--auto` has no such prompt, so there the `AGENTS.md` policy is the
only guard.

## Configuration

The helper reads `${XDG_CONFIG_HOME:-~/.config}/dotfiles/peer-envs.json`
(`%USERPROFILE%\.config\dotfiles\peer-envs.json` on Windows). The file is
machine-specific and is applied by the private dotfiles repo, never committed
here. The same file works on every machine because the entry whose `platform`
matches the running machine is dropped as "self".

```json
{
  "schema_version": 1,
  "peers": {
    "wsl2": {
      "ssh_host": "WSL2Debian",
      "platform": "wsl2",
      "repo": "~/Documents/development/dotfiles",
      "identity_file": "~/.ssh/keys/peer-env-macbook"
    },
    "macos": {
      "ssh_host": "M4MacBook",
      "platform": "macos",
      "repo": "~/Documents/development/dotfiles",
      "identity_file": "~/.ssh/peer-env-bigrig.pub"
    }
  }
}
```

| Field | Meaning |
| :--- | :--- |
| key under `peers` | Free label shown by `probe` and accepted by `audit`/`test`/`clean` instead of `all`. Must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and must not be `all`. |
| `ssh_host` | What is passed to `ssh`: a `Host` alias from `~/.ssh/config` or `user@host`. Defaults to the label. |
| `platform` | `linux`, `macos`, or `wsl2`. Compared against the detected local platform and against what the peer reports. `windows` is rejected for now. |
| `repo` | The peer's checkout. Must start with `~/` or `/`; no spaces or colons. |
| `identity_file` | Optional. The dedicated key, passed with `IdentitiesOnly=yes`. Use the `.pub` path when the private key lives only in an agent. |
| `connect_timeout` | Optional, 1 to 60 seconds; default 3. |
| `allow_unrestricted` | Optional, default `false`. Lets `audit`/`test` use a peer reached through an ordinary key. |
| `via` | Reserved for the Windows hop; any non-null value is rejected with `windows via wsl2 is not supported yet`. |

Unknown keys are rejected. A missing file exits 2 and names the path.

Platform detection follows `assets/run-tests.py`: Darwin is `macos`; Linux
with `WSL_DISTRO_NAME` or `WSL_INTEROP` set is `wsl2`; other Linux is `linux`;
`os.name == "nt"` is `windows`.

## SSH setup

Each origin machine gets its own ed25519 key used for nothing else:

1. Create the key and make the local agent load it at startup.
   - macOS: the `com.ssh-add-keychain` LaunchAgent runs
     `ssh-add --apple-use-keychain ~/.ssh/keys/<name>` at login for every
     name in `SSH_ADD_KEYFILES`, which `chezmoi init` builds from the
     `SSH_ADD_KEYFILES_RAW` prompt. Store the private key with the other
     keys (the private repo's `run_once_after_ssh_config.sh.tmpl` links them
     into `~/.ssh/keys/`; add the new name to its `KEY_FILES` list), append
     the name to `SSH_ADD_KEYFILES_RAW` and `SSH_ADD_KEYFILES` in
     `~/.config/chezmoi/chezmoi.toml` (or rerun `chezmoi init`), run
     `chezmoi apply`, then load it once by hand so the passphrase lands in
     the keychain:

     ```sh
     ssh-add --apple-use-keychain ~/.ssh/keys/peer-env-macbook
     ```

   - Windows and WSL2: `Start-WslSshPageant.ps1` loads every key named in
     the allowlist at `windows.pageant_keys_allowlist`, resolving relative
     names against `windows.putty_keys_dir` (both from `chezmoi init`, stored
     in `[data.windows]`). Convert the key to `.ppk`, put it in that keys
     directory, and add its filename as a line in the allowlist file; the
     script refuses to start while a listed file is missing. WSL2 then sees
     the key through the forwarded agent socket; export only the public half
     and manage that file through the private repo (see the `.pub` note
     below).

2. On the peer, add one line to `~/.ssh/authorized_keys` (managed by the
   private repo):

   ```text
   restrict,command="/Users/<user>/Documents/development/dotfiles/assets/peer-env serve" ssh-ed25519 AAAA... peer-env-bigrig
   ```

   Use the peer's absolute checkout path; `restrict` turns off pty, agent
   forwarding, port forwarding, X11, and `~/.ssh/rc`.
3. Point the peer's config entry at the key with `identity_file`.
4. Accept the peer's host key once with an interactive `ssh <alias> true`.
   The helper runs with `StrictHostKeyChecking=yes` and `BatchMode=yes`, so
   an unknown host key reads as `unreachable`.

When the private key lives only in an agent (Pageant forwarded through
`wsl2-ssh-agent`, for example), `identity_file` must name the public key
file. With `IdentitiesOnly=yes` and no such file, ssh never offers the agent
key and the probe reports `auth-failed`. Create the file once from
`ssh-add -L`, writing to a scratch name first so a key that is not loaded
cannot truncate a good file:

```sh
ssh-add -L | grep ' peer-env-bigrig$' >| ~/.ssh/peer-env-bigrig.pub.new \
  && command mv -f -- ~/.ssh/peer-env-bigrig.pub.new ~/.ssh/peer-env-bigrig.pub
```

The macOS sshd drop-in from `docs/automation/macos-home-ssh.md` already
limits logins to the chezmoi user with key-only authentication.

## Commands

Run everything from inside the checkout.

| Command | What it does | Exit |
| :--- | :--- | :--- |
| `./assets/peer-env probe [--json] [--fresh] [--ttl S] [--connect-timeout S]` | Probe every other-platform peer concurrently and print one line per peer plus a summary. | 0 when a peer is usable (reachable, and restricted unless `allow_unrestricted`), 3 when none is, 2 on a config error |
| `./assets/peer-env audit all <path>... [--timeout S] [--rm] [--tracked-only] [--fresh] [--ttl S]` | Snapshot, publish, prepare the peer worktree, run `./assets/cz-audit.sh check <path>` for each path there. | the first non-zero check exit, or 1 when a check exits 0 but prints an `ERROR:` line |
| `./assets/peer-env test all [suite] [--require-capabilities] [--timeout S] [--rm] [--tracked-only] [--fresh] [--ttl S]` | Same pipeline with `./assets/run-tests.sh <suite>`; `suite` defaults to `fast`. | the runner's exit code |
| `./assets/peer-env clean all [--timeout S] [--fresh] [--ttl S]` | Remove `worktrees/peer-env-*` and `refs/peer-env/*` on each reachable peer; delete the local probe cache. | 0, or 1 when a peer's clean failed |
| `./assets/peer-env config [--json]` | Show the resolved configuration and which entry is "self". | 0, or 2 with the validation error |

`all` targets every reachable other environment, which is what agents should
use; a peer label targets one. For `all`, peers that are not `reachable`
(including `reachable (unrestricted)`) are skipped with a `report "..."` line
and the command exits 0 when at least one peer ran and passed, 3 when nothing
ran. Client messages are prefixed `peer-env:`; peer messages
`peer-env serve:`. Invoke the helper as `./assets/peer-env` from the checkout
root: the permission allow rules match that form, and absolute paths or
`cd ... &&` prefixes prompt in Claude Code.

```text
$ ./assets/peer-env probe
self     macos
wsl2     reachable                WSL2Debian       Linux 6.18.33.2-microsoft-standard-WSL2; git chezmoi python3 pwsh
1 reachable, 0 not
```

Exit codes: `0` ok; `1` or the remote entrypoint's own code; `2` configuration,
usage, refused request, or secret-pattern refusal; `3` no reachable peer; `75`
the peer worktree is busy with another request; `124` the remote run hit the
timeout (900 seconds by default on both sides). A remote entrypoint's own exit
code is passed through and announced as `peer-env: <peer>: exit N`; a
client-side refusal or unreachable result prints its reason instead.

### What a run does

1. The working tree is captured into a commit object without touching HEAD,
   the index, or the stash: a temporary index is filled with `git read-tree
   HEAD` and `git add -A`, written with `git write-tree`, and committed with
   `git commit-tree` under a fixed `peer-env <peer-env@localhost>` identity and
   date, so the same tree always yields the same SHA. An unchanged tree reuses
   HEAD. Untracked files are included unless `--tracked-only` is given; the
   sender's ignore rules apply.
2. Untracked files whose names look like secrets (`.env`, `*.env`, `*.pem`,
   `*.key`, `*.p12`, `*.pfx`, `id_*`, `*_rsa`, `*_ed25519`, `credentials*`,
   `*.tfstate`) stop the run with exit 2 and a list; add them to
   `.gitignore`, pass `--tracked-only`, or leave their removal to the owner.
3. The commit is pushed to `refs/peer-env/<slug>` on the peer, where `<slug>`
   is the local branch name with unsafe characters replaced by `-`, or
   `detached-<short sha>`. The push uses `--receive-pack` so the peer's
   `serve` handles it and installs the namespace hook.
4. The peer checks the SHA out in `worktrees/peer-env-<slug>` (created on the
   first run, reused afterwards with `git checkout --detach --force` and
   `git clean -fd`, which keeps ignored `.cz-audit/` logs).
5. The entrypoint runs with the scrubbed environment; output streams back;
   the exit code is propagated. `--rm` removes the worktree and ref afterwards.

### Probe cache

Probe results live in `${XDG_STATE_HOME:-~/.local/state}/dotfiles/peer-env/probe.json`
for 300 seconds, keyed on the local platform and the config file's size and
mtime. `--fresh` bypasses the cache, `--ttl` changes the lifetime. `audit`,
`test`, and `clean` consult the same cache, so a task pays for one live probe
at most every five minutes. SSH connections reuse a control socket under the
same directory for two minutes when the path is short enough for the OS.

### Statuses

An agent reports `not verified on <platform>: ...` for every status other than
`reachable` and stops; it never edits the config, sets `allow_unrestricted`,
retries, or opens its own ssh session. The last column is for the person who
owns both machines.

| Status | Meaning | Agent statement | Owner action |
| :--- | :--- | :--- | :--- |
| `reachable` | Identity exchanged through the forced command; tools present. | none; run the commands | none |
| `reachable (unrestricted)` | Identity exchanged, but through an ordinary key. | `not verified on <platform>: peer unreachable (unrestricted key)` | Install the restricted key, or set `allow_unrestricted` knowingly. |
| `unreachable` | Connect timeout, refused, no route, or host-key failure. | `not verified on <platform>: peer unreachable` | Wake the machine or accept its host key. |
| `auth-failed` | ssh exited 255 with `Permission denied`. | `not verified on <platform>: peer unreachable` | Fix `identity_file` or the `authorized_keys` line; see [SSH setup](#ssh-setup). |
| `serve-missing` | The connection worked but no identity line came back. | `not verified on <platform>: peer unreachable` | The peer checkout lacks `assets/peer-env` or is behind `main`; pull it there. |
| `protocol-mismatch` | The peer speaks a different `peer-env/N`. | `not verified on <platform>: peer unreachable` | Land the protocol change and update the peer's `main`. |
| `platform-mismatch` | The peer reports a different platform than configured. | `not verified on <platform>: peer unreachable` | Fix the config entry. |
| `tools-missing` | git or a Python interpreter is missing on the peer's scrubbed PATH. | `not verified on <platform>: peer unreachable` | Install them, or extend the PATH list in `serve`. |
| no config file | `~/.config/dotfiles/peer-envs.json` is absent; the audit prints no line and `probe` exits 2. | `not verified on other platforms: no peer configured` | Apply the file from the private repo. |
| probe exit 2 with a config file | The config is invalid or the helper failed; the audit prints the `probe failed` line. | `not verified on other platforms: peer-env probe failed` | Fix the file from the private repo. |
| exit 75 | Another request holds the peer worktree lock. | `not verified on <platform>: peer busy`, after one retry once your own earlier command has finished | none |
| exit 124 | The remote run hit the 900-second bound. | `not verified on <platform>: peer timed out` | Investigate the slow step; agents do not raise `--timeout`. |

## Protocol

One request per SSH session. The client sends
`<repo>/assets/peer-env serve peer-env/1 <verb> [args]`; in forced-command
mode sshd ignores that text and `serve` re-reads it from
`SSH_ORIGINAL_COMMAND`. Anything that is not a `peer-env serve` request (or
the `git-receive-pack` form git emits) is refused.

| Request | Peer action |
| :--- | :--- |
| `identity` | Print `PEER-ENV-IDENTITY {json}` with `uname -s`, `uname -r`, platform, `restricted`, tool availability, and the protocol version. |
| `receive-pack [path]` | `git receive-pack` on the peer's own checkout with a per-push `pre-receive` hook that rejects any ref outside `refs/peer-env/<slug>`. The path argument is ignored. |
| `prepare <slug> <sha>` | Under a per-slug lock, create or re-check-out `worktrees/peer-env-<slug>` at `<sha>`, which must equal `refs/peer-env/<slug>`. |
| `exec <slug> audit <path>...` | Validate each path (relative, normalised, present in the snapshot) and run `./assets/cz-audit.sh check` for each. |
| `exec <slug> test <suite> [--require-capabilities]` | Run `./assets/run-tests.sh` for a known suite. |
| `remove <slug>` | Remove the worktree and its ref. |
| `clean` | Remove every `worktrees/peer-env-*` and `refs/peer-env/*`. |

Slugs match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`; SHAs are 40 hex digits; a
second request on a busy slug exits 75 instead of waiting; an entrypoint that
runs longer than 900 seconds is killed and exits 124. Refusals exit 2 with
`peer-env serve: refused: <reason>`.

The peer runs the copy of `serve` in its `main` checkout, so a protocol change
must land on `main` and be pulled on the peer before a feature branch can rely
on it. The probe surfaces skew as `serve-missing` or `protocol-mismatch`.

## Agent policy

`AGENTS.md` ("Cross-environment verification") is the binding text. In short:
`cz-audit` prints `INFO: peer-env:` lines after each check of a
platform-sensitive path; follow them. Platform-sensitive paths are
`.chezmoiscripts/**`, `.chezmoi*`, templated `dot_*`/`private_*` files with OS
conditionals, `assets/**` shell or Python helpers, `bin/**`, `tests/**`,
`configs/test-suites.json`, and `configs/automation-test-inventory.json`.
Docs-only changes, `.ps1`-only changes, `private_Library/**`, and `AppData/**`
are exempt. For every status other than `reachable`, including
`reachable (unrestricted)`, the final response carries the quoted statement
from the INFO line verbatim; nobody retries, waits, edits the config, or opens
an ad hoc ssh session. A platform a test declares but no peer provides (native
Windows today) is reported as `not verified on <platform>: no peer configured`.

`cz-audit` itself stays quiet when the config file does not exist, when
`assets/peer-env` or `python3` is missing, when the path is a Markdown file,
a `.ps1`, under `docs/`, `private_Library/`, or `AppData/`, or when
`CZ_AUDIT_PEER_ENV=0`. `CZ_AUDIT_PEER_ENV_CMD` points the audit at another
helper path for tests. Probe failures never change the audit's exit code.

## Cleanup

Peer worktrees are kept between runs so a second run is cheap. Remove them
with `./assets/peer-env clean all`, or add `--rm` to a single run. The
directory `worktrees/` is gitignored on every checkout; the refs live only
under `refs/peer-env/`.

## Not yet: native Windows

Reaching native Windows means a second hop: ssh into WSL2, then run
`pwsh.exe` through WSL interop against a separate Windows clone of this repo.
Two constraints apply:

- sshd sessions on WSL2 carry neither `WSL_DISTRO_NAME` nor `WSL_INTEROP`.
  `serve` already restores both (it exports the first live socket under
  `/run/WSL/*_interop`), which is what makes `pwsh.exe` callable from a
  forced command. Whether that is dependable on the home machine is confirmed
  live before the hop is built.
- The config schema reserves `via` for this: a Windows entry would say
  `"platform": "windows", "via": "wsl2"` and name the Windows clone path.
  Until the hop exists, both are rejected with `windows via wsl2 is not
  supported yet`, and `windows` is absent from the helper's platform list in
  `configs/test-suites.json` and `configs/automation-test-inventory.json`.

If interop proves unreliable inside sshd sessions, the alternative is an
OpenSSH server on Windows itself, reached directly with the same forced
command shape.

## Testing

`tests/test_peer_env.py` drives the real client and `serve` code without a
network. A fake `ssh` on `PATH` logs its arguments and, per configured host,
either sets `SSH_ORIGINAL_COMMAND` and runs the peer clone's own
`assets/peer-env serve` (the forced-command shape, with a planted
`FAKE_PROVIDER_KEY` that must not leak), runs the command through `sh -c`
(the unrestricted shape), fails with scripted stderr, or hangs. Because
`git push` invokes `ssh host '<receive-pack> path'`, the real push lands in
the local clone through `serve`. Stub `cz-audit.sh` and `run-tests.sh` scripts
record their arguments and environment inside the peer worktree. The suite is
registered as `peer-env` in `configs/test-suites.json` for `fast` and
`integration` on linux, macos, and wsl2; Windows cannot shadow `ssh.exe` with
a PATH shim.
