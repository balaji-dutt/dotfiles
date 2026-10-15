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
- `serve`'s own writes are confined to `peer-env-<slug>/` under the
  canonical worktree parent (see [Worktree layouts](#worktree-layouts)),
  `refs/peer-env/*` plus worktree metadata under `.git/`, and
  `~/.local/state/dotfiles/peer-env/` (diagnostics, log, and locks). A
  `pre-receive` hook
  installed per push through `core.hooksPath` rejects every ref outside
  `refs/peer-env/`. The peer's branches, HEAD, index, and stash are never
  touched by `serve`. The test runner writes to its own temp sandbox.
- The only reads outside the worktree are the ones `cz-audit` performs
  locally too: `chezmoi diff` and `chezmoi apply --dry-run` against the peer's
  `$HOME`. Diff content stays in the peer's private diagnostic archive of
  `.cz-audit/*.log` because the client cannot set `CZ_AUDIT_SHOW`.
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
      "identity_file": "~/.ssh/keys/peer-env"
    },
    "macos": {
      "ssh_host": "M4MacBook",
      "platform": "macos",
      "repo": "~/Documents/development/dotfiles",
      "identity_file": "~/.ssh/peer-env.pub"
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

One ed25519 key, `peer-env`, used for nothing else, is loaded by the agent
on each machine and accepted by each peer's `authorized_keys`. A per-machine
key would only add independent revocation; the forced command bounds what the
key can do either way.

1. Create the key once and make each machine's agent load it at startup.
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
     ssh-add --apple-use-keychain ~/.ssh/keys/peer-env
     ```

   - Windows and WSL2: `Start-WslSshPageant.ps1` loads every key named in
     the allowlist at `windows.pageant_keys_allowlist`, resolving relative
     names against `windows.putty_keys_dir` (both from `chezmoi init`, stored
     in `[data.windows]`). Convert the same key to `.ppk`, put it in that
     keys directory, and add its filename as a line in the allowlist file; the
     script refuses to start while a listed file is missing. WSL2 then sees
     the key through the forwarded agent socket; export only the public half
     and manage that file through the private repo (see the `.pub` note
     below).

2. On each peer, add one line to `~/.ssh/authorized_keys` (managed by the
   private repo):

   ```text
   restrict,command="/Users/<user>/Documents/development/dotfiles/assets/peer-env serve" ssh-ed25519 AAAA... peer-env
   ```

   Use that peer's absolute checkout path (`/home/<user>/...` on WSL2);
   `restrict` turns off pty, agent forwarding, port forwarding, X11, and
   `~/.ssh/rc`.
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
ssh-add -L | grep ' peer-env$' >| ~/.ssh/peer-env.pub.new \
  && command mv -f -- ~/.ssh/peer-env.pub.new ~/.ssh/peer-env.pub
```

The macOS sshd drop-in from `docs/automation/macos-home-ssh.md` already
limits logins to the chezmoi user with key-only authentication.

### Local WSL2 relay recovery

`~/bin/wsl2-pageant.py` is a managed helper for WSL2 hosts with configured
`homelab.windows_user` and `homelab.npiperelay_path`, outside containers. It
provides `--check`, `--ensure`, and `--reset`, with optional `--json` health
metadata. Bash startup, zsh startup and its 30-second health hook delegate to
it; `sset` selects the configured socket and calls `--reset`, while `sunset`
unsets `SSH_AUTH_SOCK`. Missing helper/dependencies leave shell startup usable.

Before the client's `probe`, `audit`, or `test`, a selected unhealthy Pageant
socket gets at most one `--ensure` restart within a 15-second operation budget.
An unset `SSH_AUTH_SOCK` selects the configured socket only in peer-env's
child environment. A different selected agent, another OS, a container, or a
healthy agent with no loaded keys is not reset. Health requires `ssh-add -l`
to exit 0, or exit 1 with exactly `The agent has no identities.` on stdout
and no stderr, checked in the C locale. Communication failures are unhealthy
even when they exit 1; identity output is not logged. `--no-agent-repair`
disables the restart. Neither client nor server sources shell rc files;
incoming `serve` and `config` do not repair.

Repair is serialized with a private lock and rechecks health after acquiring
it. An owned legacy socket directory with group/world write permission (such
as 0775 or 0777) is secured to 0700 under that lock; foreign-owned directories
and symlinks remain refused. New relays create the socket with `mode=600`.

Linux socket diagnostics match the selected socket's filesystem device and
inode, not just its pathname. Identically named sockets in other WSL distros
and unlinked listeners do not select another relay. Only a unique visible
same-user socat listener with the exact socket/relay arguments is stopped,
using a verified PID/start time and Linux pidfd. Legacy plain, single-quoted,
or double-quoted relay paths and the listen form with or without `mode=600`
are accepted; other arguments are refused.

Prior-boot PID records are ignored; same-boot PID reuse, foreign ownership,
symlinks, or ambiguous listeners are refused without broad `pkill`. Failed or
incomplete diagnostics also refuse recovery. An unmatched live socket reports
`socket has an unidentified live listener; refusing recovery`; inspect its
ownership, configured relay path, and Linux diagnostic visibility rather than
deleting the socket or killing another distro's relay to bypass the refusal.
The helper does not start Windows Pageant, load keys, change trust/configuration,
or retry remote validation.

## Commands

Run everything from inside the checkout.

| Command | What it does | Exit |
| :--- | :--- | :--- |
| `./assets/peer-env probe [--json] [--fresh] [--ttl S] [--connect-timeout S] [--no-agent-repair]` | Perform local relay preflight, then probe every other-platform peer concurrently. | 0 when a peer is usable (reachable, and restricted unless `allow_unrestricted`), 3 when none is, 2 on a config error |
| `./assets/peer-env audit all <path>... [--timeout S] [--keep-worktree\|--rm] [--tracked-only] [--fresh] [--ttl S] [--no-agent-repair]` | Snapshot, publish, run all path audits within one budget, archive diagnostics, then remove the owned worktree/ref. | the first non-zero check exit, 1 for an `ERROR:` line or cleanup failure after success, or 124 on deadline exhaustion |
| `./assets/peer-env test all [suite] [--require-capabilities] [--timeout S] [--keep-worktree\|--rm] [--tracked-only] [--fresh] [--ttl S] [--no-agent-repair]` | Same lifecycle with `./assets/run-tests.sh <suite>`; `suite` defaults to `fast`. | the runner's exit code, 1 for cleanup failure after success, or 124 on deadline exhaustion |
| `./assets/peer-env clean all [--timeout S] [--fresh] [--ttl S]` | Remove proven owned, quiescent helper worktrees and namespace refs under per-slug locks; delete the local probe cache. | 0, or 1 when a peer's clean failed or was busy (peer `serve clean` returns 75 for busy slugs, 1 for a mixture of refused and busy slugs) |
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
the per-run slug is busy with another request; `124` a bounded helper phase or
the whole execution hit its deadline (900 seconds by default for execution).
A remote entrypoint's own exit code is passed through and announced as
`peer-env: <peer>: exit N`; a
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
   combines a shortened safe branch name (or detached SHA) with a random
   per-invocation suffix. Same-branch sessions have independent worktrees and
   locks. The push uses `--receive-pack` so the peer's
   `serve` handles it and installs the namespace hook.
4. One peer-owned `run` request holds the slug lock across SHA validation,
   detached checkout in `<worktree parent>/peer-env-<slug>`, execution, archival, and
   cleanup. The installed server, not the snapshot, owns this lifecycle.
5. The entrypoint runs with the scrubbed environment and streams output back.
   Diagnostics are saved separately, then the worktree/ref are removed on
   success or failure. `--keep-worktree` retains them for debugging; `--rm`
   is a compatibility alias for default cleanup, mutually exclusive with keep.
   Cleanup failure preserves a primary failure code, or turns success into 1.

### Deadlines and terminal budget

`--timeout S` is a finite number in `(0, 86400]`, default 900 seconds, shared
by the entire audit batch or test suite, not restarted for each audit path.
Publication has 120 seconds, preparation 60, shutdown a 10-second TERM grace
followed by KILL, and archival/cleanup 60. The client streaming deadline adds
preparation, shutdown, cleanup, and a 30-second transport margin. Silent output
does not suspend the peer's monotonic deadline. A silent SSH disconnect remains
bounded by that independent peer deadline; cooperative interrupts and broken
streams enter shutdown and cleanup. Validation is never automatically retried.

The terminal tool must outlive these helper phases. Allow at least 1500000 ms
(25 minutes) for one peer with the default execution budget, or
`(180 + n * (T + 420)) * 1000` ms for `n` peers and execution budget `T` seconds.
Use the harness's supported background mechanism if it cannot supply that
budget, or report the limitation before starting. A terminal's 120-second cap
is neither the helper's 900-second deadline nor SSH's 120-second idle
`ControlPersist`; extending the terminal budget does not authorize raising
`--timeout` or rerunning timed-out validation.

### Probe cache

Probe results live in `${XDG_STATE_HOME:-~/.local/state}/dotfiles/peer-env/probe.json`
for 300 seconds, keyed on the local platform and the config file's size and
mtime. `--fresh` bypasses the cache, `--ttl` changes the lifetime. `audit`,
`test`, and `clean` consult the same versioned cache. A verified local relay
repair or healthy socket generation change refreshes cached `auth-failed`
peers once before publication, without reprobes of unrelated unreachable
peers or retries of validation. Older cache formats are ignored. SSH
connections reuse a control socket under
`/tmp/peer-env-<uid>/` for two minutes; that directory is created mode 0700,
and multiplexing is skipped when the directory is not private to the user or
the socket path would exceed the OS limit (OpenSSH needs 17 spare characters
for its temporary name while binding).

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
| `protocol-mismatch` | The peer speaks a different `peer-env/N` or lacks `run-lifecycle-v1`. | `not verified on <platform>: peer unreachable` | Land the change and update the peer's installed `main` server first. |
| `platform-mismatch` | The peer reports a different platform than configured. | `not verified on <platform>: peer unreachable` | Fix the config entry. |
| `tools-missing` | git or a Python interpreter is missing on the peer's scrubbed PATH. | `not verified on <platform>: peer unreachable` | Install them, or extend the PATH list in `serve`. |
| no config file | `~/.config/dotfiles/peer-envs.json` is absent; the audit prints no line and `probe` exits 2. | `not verified on other platforms: no peer configured` | Apply the file from the private repo. |
| probe exit 2 with a config file | The config is invalid or the helper failed; the audit prints the `probe failed` line. | `not verified on other platforms: peer-env probe failed` | Fix the file from the private repo. |
| exit 75 | Another request holds the peer worktree lock. | `not verified on <platform>: peer busy`, after one retry once your own earlier command has finished | none |
| exit 124 | A helper phase or the whole execution exhausted its budget. | `not verified on <platform>: peer timed out` | Investigate the slow step; agents do not raise `--timeout`. |

## Protocol

One request per SSH session. The client sends
`<repo>/assets/peer-env serve peer-env/1 <verb> [args]`; in forced-command
mode sshd ignores that text and `serve` re-reads it from
`SSH_ORIGINAL_COMMAND`. Anything that is not a `peer-env serve` request (or
the `git-receive-pack` form git emits) is refused.

| Request | Peer action |
| :--- | :--- |
| `identity` | Print `PEER-ENV-IDENTITY {json}` with `uname -s`, `uname -r`, platform, `restricted`, tool availability, protocol version, and capabilities. |
| `receive-pack [path]` | `git receive-pack` on the peer's own checkout with a per-push `pre-receive` hook that rejects any ref outside `refs/peer-env/<slug>`. The path argument is ignored. |
| `run <slug> <sha> <timeout> cleanup\|keep audit\|test ...` | Hold the slug lock across preparation, one bounded execution, diagnostics, and cleanup or explicit retention. |
| `prepare <slug> <sha>` | Under a per-slug lock, create or re-check-out `<worktree parent>/peer-env-<slug>` at `<sha>`, which must equal `refs/peer-env/<slug>`. |
| `exec <slug> audit <path>...` | Validate each path (relative, normalised, present in the snapshot) and run `./assets/cz-audit.sh check` for each. |
| `exec <slug> test <suite> [--require-capabilities]` | Run `./assets/run-tests.sh` for a known suite. |
| `remove <slug> [expected-sha]` | Remove a proven owned worktree/ref after verifying shutdown and expected SHA; the single-argument legacy form remains available. |
| `clean` | Lock and verify each canonical helper worktree/ref; skip busy slugs and refuse unverified child shutdown. No global prune or ordinary branch deletion. |

Slugs match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`; SHAs are 40 hex digits; a
second request on a busy slug exits 75 instead of waiting; execution past its
aggregate budget is stopped and exits 124. Refusals exit 2 with
`peer-env serve: refused: <reason>`.

The peer runs the copy of `serve` in its `main` checkout, so a protocol change
must land on `main` and be pulled on the peer before a feature branch can rely
on it. Protocol `peer-env/1` remains, with `run-lifecycle-v1` advertised by
new servers. New clients require that capability before publishing and never
fall back to the non-atomic lifecycle. New servers retain legacy
`prepare`/`exec`/`remove` verbs for older clients. The probe surfaces skew as
`serve-missing` or `protocol-mismatch`; uploading a snapshot cannot upgrade the
installed server. Deployment is a separate owner-authorized operation.

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

## Cleanup and diagnostics

Default runs remove only their registered detached worktree and matching
`refs/peer-env/<slug>` ref. `--keep-worktree` preserves a debugging run, and
`./assets/peer-env clean all` removes proven owned leftovers under per-slug
locks. Ordinary branches and worktrees, symlink escapes, ambiguous legacy
worktrees outside the canonical helper directory, and active locked slugs
are not deleted; `clean` reports a detached `peer-env-<slug>` registration
outside that directory and exits 1, even when its ref is gone. There are no
helper-created branch refs. `worktrees/` is gitignored; namespace refs remain
local to each peer.

When the worktree layout is refused, cleanup deletes `refs/peer-env/<slug>`
only if it still names the expected SHA, it is not a symbolic ref, no
registered worktree is named `peer-env-<slug>`, and nothing exists at that
path. The manifest then records `"prepared": false` and
`"cleanup": "ref removed; no worktree was created"`, and the run keeps exit 2;
no audit or test ran. If any of those conditions fails, including a refused
leaf path, the ref is retained.

### Worktree layouts

`serve` derives the worktree parent from `<repo>/worktrees` on every request:

| Layout | Behaviour |
| :--- | :--- |
| Missing or a real directory | Used as is; created on first `prepare`. |
| Symlink to a private directory, such as the Agent of Empires layout `worktrees -> ~/.local/share/agent-of-empires/worktrees/<repo>` | The resolved target is the parent. Helper worktrees sit beside the AoE worktrees; neither the symlink nor the AoE worktrees are touched. |

The symlink target is refused when it is dangling or not a directory, is not
owned by the serving user, has a target or ancestor that is not owned by that
user or root, or has one that is group- or world-writable without the sticky
bit. It is also refused when it is the checkout, an ancestor of it, or inside
it, inside the git common directory or helper state, or inside a registered
worktree. A symlinked `peer-env-<slug>` leaf, or a leaf whose real path leaves
the parent, is refused.

Git records linked worktree paths with symlinks resolved, so the registration
is the binding between requests. If any `peer-env-<slug>` registration lies
outside the current parent, because the symlink was re-pointed after a
`--keep-worktree` run or during a run, `remove` and `clean` refuse with
`worktree parent changed` and retain the worktree and ref. Restore the link,
then run `./assets/peer-env clean all`. The checks run as the serving user and
do not defend against that same user racing them.

The root `.gitignore` carries `/worktrees` alongside `worktrees/`, because the
directory pattern does not match a symlink; without it, `git clean -fd` in the
peer checkout would delete the link.

Deployment follows the server-first rollout: land the change on `main`, have
the owner pull it into the peer's `main` checkout, then run
`./assets/peer-env clean all` to clear refs leaked by refused runs. Rollback is
a revert on `main` and the same pull; an old server refuses symlinked parents
again but leaves helper worktrees it did not create alone.

Private `${XDG_STATE_HOME:-~/.local/state}/dotfiles/peer-env/runs/<slug>/`
contains `output.log`, `result.json` (SHA, exit, timing, cleanup, truncation),
and archived regular top-level `.cz-audit` files. Directories are 0700 and
files 0600; no symlinks or snapshot contents are copied. Each run has a shared
20 MiB log cap and archival scans at most 1024 entries, with truncation recorded.
The 20 most recent completed runs with verified child shutdown are retained;
active, incomplete, and unverified-child records are not pruned.

The configured state root (`XDG_STATE_HOME` or `~/.local/state`) is canonicalized
before appending `dotfiles/peer-env`. Symlinked ancestors of that root are
accepted; symlinks within the helper-owned subtree remain refused during cleanup.

SIGKILL, host failure, or a forced-killed runner with independent step groups
cannot guarantee immediate cleanup. When shutdown is unverified, the peer
retains the worktree/ref and records `children_unverified`; both `remove` and
`clean` refuse deletion. The client reports incomplete cleanup without hiding
the primary failure. There is no unattended override: the owner must inspect
the exact result, worktree registration, expected SHA, and owned processes,
establish that all descendants are stopped, then separately authorize manual
state/worktree reconciliation. Incomplete diagnostics need the same inspection;
their retention is not a claim that the crashed run is still active. Agents
must not clear markers or infer ownership from a leftover's basename.

## Not yet: native Windows

Reaching native Windows means a second hop: ssh into WSL2, then run
`pwsh.exe` through WSL interop against a separate Windows clone of this repo.
Two constraints apply:

- sshd sessions on WSL2 carry neither `WSL_DISTRO_NAME` nor `WSL_INTEROP`.
  `serve` restores both (it exports the first live socket under
  `/run/WSL/*_interop`), but finding a socket does not prove that the sshd
  process tree can launch Windows executables. The test PowerShell resolver
  execution-probes candidates, preferring usable local `pwsh` before
  `pwsh.exe` and the standard Windows installation path. If none can execute,
  the wrapper test skips with its probe reason; a wrapper failure after a
  successful probe still fails. A Windows hop needs live execution evidence.
- The config schema reserves `via` for this: a Windows entry would say
  `"platform": "windows", "via": "wsl2"` and name the Windows clone path.
  Until the hop exists, both are rejected with `windows via wsl2 is not
  supported yet`, and `windows` is absent from the helper's platform list in
  `configs/test-suites.json` and `configs/automation-test-inventory.json`.

If interop proves unreliable inside sshd sessions, the alternative is an
OpenSSH server on Windows itself, reached directly with the same forced
command shape.

## Testing

`serve` supplies `TERM=dumb` for its noninteractive entrypoint. Tests that
allocate a PTY supply the terminal capabilities they emulate: the live tmuxp
test uses `TERM=xterm-256color` and a nonzero window size, verifies an actual
attach, and retains detached-load and repeat-attach coverage even when the
incoming terminal type is dumb or absent.

The `repo-ops` and `test-runner` steps use verbose unittest output, including
individual-test skip reasons. `--report-file` remains a registered-step
report: a passing step may contain skipped tests, and their reasons appear in
the output rather than JSON. Per-test JSON is available through the opt-in
`--case-report` pilot described in `docs/tooling/test-runner.md`.

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

Lifecycle tests also cover aggregate deadlines, cooperative signals, cleanup
failure, legacy compatibility, capability refusal, retained unverified
shutdown, and diagnostic limits. `SymlinkedParentTests` repeats success,
failure, keep, remove, clean, busy, and unverified-shutdown runs with
`worktrees` symlinked beside locked and ordinary AoE-style worktrees, and
covers target changes, escapes, unsafe ownership, symlinked leaves, and
ref-only reconciliation. That is offline coverage of the server code; it does
not show that a deployed peer accepts its own layout, which needs the owner's
deployment and a separate peer rerun. `tests/test_wsl2_pageant.py` renders the
managed helper and uses disposable sockets and mocked process records;
`tests/test_shell_startup.py` checks shared shell dispatch and
`tests/test_test_runner.py` verifies independent step-group signal shutdown.
Live WSL2/Pageant named-pipe IPC is unverified, tracked as planned coverage
under `dots-acum`; synthetic fixtures do not substitute for that runtime test.
