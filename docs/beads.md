# Beads (bd) operations

[Beads](https://github.com/gastownhall/beads) (`bd`) is the Dolt-backed issue
tracker for this repo. The database is named `dots`. This document covers the
architecture, cross-machine sync, schema migrations on `bd` upgrades, and
recovery procedures.

For first-time setup on a machine, see **Beads setup on a new machine** in the
top-level `README.md`. The DevContainer (`homelab-IaC`) uses a *separate* beads
database (`hliac`) with its own remote — see `docs/devcontainers.md`.

## Architecture

- **Backend:** Dolt. `bd` auto-starts and manages a project-local `dolt
  sql-server` (`dolt.shared-server: false`, `dolt.mode: server`) on
  `127.0.0.1`. No `dolt.port` is tracked in git. On non-WSL2 POSIX hosts, `bd`
  chooses a per-project runtime port. In WSL2 the port has to stay put, because
  the native-Windows client dials it: `.envrc` derives a stable per-checkout
  value, exports it as `BEADS_DOLT_SERVER_PORT`, **and** pins it as `dolt.port`
  in gitignored `.beads/config.local.yaml`. `bd` records the active port in
  `.beads/dolt-server.port` in both cases.

  The pin is what actually fixes the port; exporting the variable alone does
  not, because `bd` does not reliably bind the per-project port its own
  `bd dolt start --help` describes. Do not move the pin into `.beads/config.yaml`
  or `.beads/metadata.json` — both are tracked, and every host needs a different
  port. `.envrc` writes the pin only when the key is absent, so a fresh clone
  self-heals on the first `cd`; an existing pin wins and is exported as-is, so
  the file and the variable cannot drift apart.
- **Data dir:** `<repo>/.beads/dolt/` (the `dots` database lives at
  `.beads/dolt/dots/`). The whole `dolt/` tree and the `dolt-server.*` runtime
  files are git-ignored (`.beads/.gitignore`). `.beads/config.yaml` and
  `.beads/metadata.json` are tracked.
- **Remote:** a private `git+ssh://` GitLab repo, configured locally rather than
  in tracked `.beads/config.yaml`. Each machine holds its own Dolt clone and
  syncs through this remote. Auth is the standard GitLab SSH key; no HTTPS
  credentials. The remote URL lives in the 1Password Secure Note `dotfiles Dolt
  Remote`; write it to gitignored `.beads/config.local.yaml` or export it as
  `BD_SYNC_REMOTE` before bootstrapping.
- **Native Windows is a client, not a peer.** It holds no Dolt clone and runs no
  server of its own; `bd` there connects to the server WSL2 already hosts on
  `127.0.0.1`. One database, so nothing syncs between the two halves of that
  box. See **Windows client mode** below.
- **JSONL:** auto-export is disabled (`export.auto: false`) and
  `.beads/issues.jsonl` is git-ignored by the repo root `.gitignore` rather than
  by `.beads/.gitignore`. Dolt is the source of truth; validated
  point-in-time JSONL recovery snapshots live on the homelab share instead of
  in Git. Quarantine or remove a stale `issues.jsonl` before syncing.

## Install per platform

`bd` and the external `dolt` server binary are installed separately. On WSL2,
mise downloads `bd` directly from its upstream GitHub release archive. This
avoids relying on npm lifecycle scripts, and the archive still does not include
the Dolt server.

All three host platforms read `beads_version` from `.chezmoidata.yaml`. macOS
and WSL2 also read `dolt_version` there; native Windows has no Dolt binary in
client mode.

| Platform | `bd` | `dolt` |
| --- | --- | --- |
| macOS | GitHub release via mise (`private_dot_config/mise/conf.d/95-beads-dolt.toml`) | GitHub release via mise, same fragment |
| WSL2 | GitHub release via mise; ansible writes `~/.config/mise/conf.d/95-beads.toml` from `beads_version` | GitHub release tarball via ansible, from `dolt_version` |
| Windows | Winget `GasTownHall.Beads` package (manual install); exact Gating pin managed by `.chezmoiscripts/run_after_windows-beads-pin.ps1.tmpl` from `beads_version` | none — client mode; see below |

Installation ownership and command resolution are separate. Interactive WSL2
Zsh uses full mise activation and direct install paths. Setting
`not_found_auto_install = false` in the WSL2 fragment makes activation remove
mise shims instead of exposing both a direct `bd` binary and a shim. The paired
`not_found_system_fallback = false` setting also makes explicit shim callers
fail instead of selecting an unrelated system or Windows binary. Ansible runs
`mise install -y`; repair a missing configured tool through provisioning rather
than installing it as a shell side effect.

WSL2 Bash sessions use mise shims because they do not run full activation, and
the ansible-installed Dolt binary remains the real `~/.local/bin/dolt` file.
On macOS, Better Beads Kanban receives the absolute mise shim path through the
managed workspace setting, so Finder and Dock launches do not depend on shell
activation or launch `PATH`. `assets/beads-sync.sh` likewise resolves the tools
from `PATH` or their managed locations. The former `~/.local/bin/bd` and `dolt`
links were retired because interactive mise activation could expose a direct
install alongside the links and trigger a duplicate-binary warning. Native
Windows resolves the Winget-managed `bd.exe`; delegated sync explicitly adds
the WSL2 shim and local-bin directories before invoking the POSIX helper.

An arbitrary clean process is not expected to gain bare commands by sourcing
interactive rc files. Repo-managed automation must resolve known executable
paths or call `assets/beads-sync.sh`, which resolves `bd` and `dolt` from PATH
or their managed locations without changing PATH globally.

Neither tool is Homebrew-managed on macOS. Both used to be, and both drifted:
on 2026-08-15 an unattended `brew upgrade` moved macOS to dolt 2.3.0 and bd
1.2.1 while WSL2 stayed on 2.2.1 / 1.1.2. `brewfile.txt` is regenerated by
`brew bundle dump` on every brew operation, so a pin cannot be recorded there.
Do not re-add `brew "dolt"` or `brew "beads"` — an unpinned formula shadows the
mise copy depending on PATH order.

The WSL2 bd fragment is written by ansible rather than chezmoi because the
provisioning hook is a `run_onchange_before` script: a chezmoi-rendered file
would land after ansible's `mise install -y` and only take effect on the next
apply.

On Windows, `configs/winget-packages.json` is an exported inventory rather than
an automatically imported chezmoi manifest. It records the supported `bd`
package, but a clean host still needs a manual Winget installation. The managed
`run_after_windows-beads-pin.ps1.tmpl` hook reapplies an exact Gating pin from
`beads_version` and warns when the installed `bd.exe` reports a different
version. It does not install, upgrade, or downgrade the package, so every
`beads_version` bump also requires installing that version on Windows. A
Gating pin is a package-manager guardrail, not a security boundary: Winget
`--force`, package self-updates, and other installers can bypass it.

Chezmoi owns the `GasTownHall.Beads` pin and normalizes any prior pin type on
apply. If this management is intentionally retired, remove the hook and then
remove the local pin from a profile-free PowerShell session:

```powershell
winget.exe pin remove --id GasTownHall.Beads --exact --source winget
```

`dolt` is deliberately **not** installed on Windows. That box is a client of the
WSL2 server (**Windows client mode** below), and removing the binary is what
makes a stray second database impossible rather than merely loud. Nothing in
this repo reinstalls it: the `dolt_version` pin in `.chezmoidata.yaml` reaches
`ansible/wsl-playbook.yml` as an extra var and never touches Windows, and
`configs/winget-packages.json` has no dolt entry. If you ever do need it back,
do not use the `winget` PowerShell function — it re-exports and commits the
winget manifest. Use `pwsh -NoProfile` with `winget.exe`, or Settings →
Installed apps.

Keep `bd` at the **same minor version** across machines. Different builds of the
same version string are fine, but a machine on an older minor that targets a
lower Dolt schema cannot read a migrated remote.

### Anonymous usage metrics

Anonymous `bd` command metrics are disabled by default. Regular hosts use the
user-global `metrics.disabled: true` setting managed from
`private_dot_config/bd/config.yaml` and applied to `~/.config/bd/config.yaml`
(`%USERPROFILE%\.config\bd\config.yaml` on native Windows). Beads intentionally
ignores project-level telemetry consent settings.

The `homelab-IaC` devcontainer also sets `BD_DISABLE_METRICS=1` in
`containerEnv`, so the opt-out exists before lifecycle scripts run and survives
container rebuilds. After applying the dotfiles (and rebuilding that container),
verify the effective setting with `bd metrics status`; it must report `OFF`.

### Renovate gating

`bd` schema bumps are deliberately kept off unattended automerge:

- Under `renovate.json5`, all custom-regex-managed deps (including both Beads
  package sources) auto-merge only patch/digest updates; minor/major bumps carry
  `automerge: false`, so a schema-moving `bd` bump lands as a human-reviewed PR.
  A Beads-specific rule covers both `gastownhall/beads` and `@beads/bd` and
  hard-blocks `1.0.5` (`allowedVersions: "!/^1\\.0\\.5$/"`). The npm name is
  retained because the DevContainer still installs that package.
- The annotated `beads_version` and `dolt_version` pins in `.chezmoidata.yaml`
  use the `github-releases` datasource, so Renovate proposes upstream releases
  without routing installation through npm. `configs/mise_wsl2.toml` is a
  comment stub now and Renovate no longer scans it.

## Cross-machine sync

Sync Beads state through the Dolt remote, not JSONL. The interactive Bash, Zsh,
and PowerShell `bd` wrappers redirect exact `bd dolt pull` and `bd dolt push`
commands to the platform helper with a visible notice. Extra arguments are
refused rather than dropped. Other `bd` commands are unaffected.

Agents and non-interactive automation deliberately bypass those wrappers. Use
`command bd ...` on POSIX or `bd.exe ...` on native Windows for ordinary Beads
commands, and call the sync helper explicitly for pull or push. Do not source a
shell rc file or `beads-helpers.*` in an agent shell. The explicit POSIX form
remains `./assets/beads-sync.sh <command>`.

In an interactive POSIX shell, direnv adds this checkout's `assets/` directory
to `PATH`, so the repo-local launcher is available by bare name:

```bash
beads-sync status    # dirty tables, is a sync safe?
beads-sync clean     # restore dirty dolt_ignore'd tables from HEAD
beads-sync pull      # guarded replacement for `bd dolt pull`
beads-sync push      # guarded replacement for `bd dolt push`
beads-sync snapshot  # force a JSONL recovery snapshot
beads-sync init      # rebuild a wedged peer from the remote (Recovery)
```

Windows (PowerShell 7) — on a machine that still hosts its own database:

```powershell
pwsh -NoProfile -File ./assets/beads-sync.ps1 status
pwsh -NoProfile -File ./assets/beads-sync.ps1 clean
pwsh -NoProfile -File ./assets/beads-sync.ps1 pull
pwsh -NoProfile -File ./assets/beads-sync.ps1 push
pwsh -NoProfile -File ./assets/beads-sync.ps1 snapshot
pwsh -NoProfile -File ./assets/beads-sync.ps1 init
```

The refusal in `assets/beads-sync.ps1` keys on a missing `.beads/dolt/<db>`
directory, not on the platform: `status`, `clean`, `pull` and `push` are refused
whenever this checkout holds no database, and `init` is refused on top of that
only when `BEADS_CLIENT_WSL_DISTRO` is set — a genuinely empty peer still needs
`init` as its bootstrap path. `snapshot` always runs. On the client-mode Windows
box that means everything but `snapshot` is refused; run the `.sh` helper in
WSL2 instead, and see **Windows client mode** below.

Both accept `-DryRun` / `--dry-run`. On `pull` and `push`, `-Backup` /
`--backup` makes a failed forced snapshot abort the sync. `snapshot` is forced
by default; `-IfDue` / `--if-due` applies the automatic throttle. `init`
rejects backup flags because there is no database to export at that point.

`status`, `clean`, `pull`, and `push` first verify that the configured Dolt
database is reachable. If it is not, the helper exits before status output,
snapshots, or server restarts and reports the effective endpoint, for example:

```text
ERROR: Dolt server is unavailable at 127.0.0.1:3307; run 'bd dolt start' first
```

`pull` and `push` still restart an already reachable server so it inherits the
current SSH agent environment; they do not use that restart to recover a
stopped server. `init` establishes its own server, and `snapshot` remains
available without this preflight.

| What you're doing | Command |
| --- | --- |
| `bd create` / `update` / `close` / `list` / `ready` / `show` / `dep` … | Interactive `bd`; agents use `command bd` / `bd.exe` |
| `bd dolt commit`, `bd dolt status` / `start` / `stop` | Interactive `bd`; agents use `command bd` / `bd.exe` |
| **push** | **`beads-sync push` — wrapper redirect or explicit agent call** |
| **pull** | **`beads-sync pull` — wrapper redirect or explicit agent call** |
| **force recovery snapshot** | **`beads-sync snapshot`** |

`bd dolt pull` fails with `cannot merge with uncommitted changes` every time; see
the recovery section below for why. Push uses the same helper because it checks
that a Dolt remote exists before any push work and restarts the server with a
live `SSH_AUTH_SOCK`.

### Windows client mode

Native Windows cannot use the Dolt remote at all: dolt's git transport dies
there with `fork/exec <git.exe>: Not enough memory resources are available`.
That bug is upstream's and still unfixed (the diagnosis is in
[`archive/docs/beads.md`](../archive/docs/beads.md)).

Since 2026-08-12 that box does not try. It holds no Dolt clone and starts no
server; `bd.exe` connects to the server WSL2 hosts on `127.0.0.1`, so both
halves read and write **one** database and there is nothing to sync between
them. The broken transport never runs.

How it is wired:

- `.chezmoidata.yaml` carries `beads_client.wsl_distro` and
  `beads_client.wsl_repo_rel`. The WSL2 `$HOME` is resolved at runtime, so no
  Linux path is hardcoded.
- `.chezmoiscripts/run_after_windows-beads-client.ps1.tmpl` reads the port from
  the WSL2 checkout's `.beads/dolt-server.port` and exports
  `BEADS_DOLT_SERVER_PORT` at **User** scope, so bare `bd.exe` callers — agents,
  OpenCode, the VSCode Better Beads Kanban extension — inherit it without a
  shell profile. It also exports `BEADS_CLIENT_WSL_DISTRO` and
  `BEADS_CLIENT_WSL_REPO_REL` for `assets/beads-sync.ps1`, which is never
  templated.
- `~/.config/powershell/beads-env.ps1` supplies the helpers the PowerShell `bd`
  wrapper uses, and fills in the port when the User-scope value is missing.
- `.beads\dolt` on the Windows checkout has been moved aside, and `dolt.exe`
  uninstalled. With no local store and no server binary, a second database is
  structurally impossible rather than merely detectable.

What the `bd` wrapper does there:

| `bd` invocation | Behavior on the client |
| --- | --- |
| ordinary commands (`list`, `create`, `close`, `show`, …) | preflight the port, then run `bd.exe` against the WSL2 server |
| `bd dolt pull` / `push` | delegated into WSL2, which runs `./assets/beads-sync.sh` with a live `SSH_AUTH_SOCK` |
| `bd dolt start` / `stop` | refused — this machine is a client, not a host |
| server unreachable | refused with the `wsl` command to start it; `bd.exe` is never invoked, so nothing can start a local server |

The delegation is deliberately independent of WSL2 rc files. `bash -lc` leaves
`SSH_AUTH_SOCK` empty and resolves `bd` to the `beads-helpers` shell function
rather than the binary, and `wsl --cd <linux-path>` fails with
`Wsl/ERROR_PATH_NOT_FOUND`. The working form exports the pageant socket and the
mise shims explicitly, then runs the command under `direnv exec`.

`direnv exec` is not cosmetic. `beads-sync.sh push` restarts the Dolt server, and
without `.envrc` loaded the restart binds a fresh port instead of the
deterministic per-checkout one — leaving the client chasing drift after every
push. The `bd` preflight recovers from that by re-reading
`.beads/dolt-server.port`, but the delegation should not cause it in the first
place. If `direnv` is missing or the `.envrc` is not allowed, the command still
runs, just without the pinned port.

**When WSL2 is down there is no Windows fallback, by design.** Wait, or work in
WSL2. Do not stand up a local database and reconcile it later: two environments
that can both mutate Beads state means remembering which one wrote last and
which export is current, which is a worse reimplementation of what the Dolt
remote already does. JSONL snapshots remain what they always were —
point-in-time recovery for one database, not a second writer.

Costs, accepted knowingly:

- WSL2 is a hard dependency for Beads on Windows.
- `BEADS_DOLT_SERVER_PORT` is set for the whole user, so a *second* Beads repo
  on Windows would need a per-shell override.
- No raw `dolt ... sql -q` diagnostics from Windows. There is nothing local to
  inspect; query from WSL2 against the same database.
- `.beads/last-touched` and `in-progress-*.json` stay per-checkout, so a bare
  `bd update` with no ID targets that host's last issue.

`bd doctor` on Windows does **not** complain about the absent `dolt` binary —
verified 2026-08-12 after removing it. It needs the binary only to start a
server, which a client never does.

### JSONL recovery snapshots

Interactive Bash, Zsh, and PowerShell wrappers request one throttled snapshot
after a successful command form known to mutate `dots`. Read-only commands,
failed mutations, help, `BD_GIT_HOOK` calls, and direct native invocations do
not trigger it. Agents deliberately use direct `command bd` / `bd.exe`, so they
get snapshots when they call `beads-sync pull` or `push`: pull takes a forced
best-effort pre-sync snapshot, while push takes a throttled pre-sync snapshot
after confirming that a Dolt remote exists. Use `--backup` / `-Backup` when a
failed pre-sync snapshot must abort pull or push.

The platform-selected roots and machine directories are:

| Platform | Root | Machine key |
| --- | --- | --- |
| WSL2 | `/mnt/devdrive` | `<hostname>-<WSL_DISTRO_NAME>` |
| macOS | `/Volumes/devdrive` | `<hostname>-macos` |
| native Windows | `V:\` | `<computername>-windows` |

Components are lowercased, runs outside `[a-z0-9._-]` become `-`, and leading
or trailing separators are removed. Thus `Ubuntu-24.04` remains distinct from
another WSL distro on the same host. Snapshots land under
`<root>/beads-snapshots/dots/<machine>/`. Generic Linux and devcontainers have
no default root. The mirrored devcontainer wrappers also skip their separate
`hliac` database because the automatic hook is scoped to `dots`.

Automatic attempts are limited to once every 10 minutes and retain the newest
10 completed snapshots for the current machine. A local attempt marker avoids
repeatedly probing a missing share; a destination lock handles concurrent
shells. Every potentially blocking shared-filesystem operation runs in a worker
with a two-second deadline. The worker validates the root, copies a validated
local `bd export --all` to a hidden temporary file, atomically renames it, and
then prunes only this machine's older completed files. A missing, unwritable, or
slow share warns and skips automatic work without changing the original `bd`
exit code.

Force a snapshot without waiting for the throttle:

```bash
./assets/beads-sync.sh snapshot
```

```powershell
pwsh -NoProfile -File ./assets/beads-sync.ps1 snapshot
```

Set `BD_AUTO_SNAPSHOT=0` to opt out of throttled wrapper and push snapshots. It
does not suppress forced manual, pull, or `--backup` snapshots. Test or unusual
hosts can override `BD_SNAPSHOT_ROOT`, `BD_SNAPSHOT_MACHINE`,
`BD_SNAPSHOT_INTERVAL_SECONDS`, `BD_SNAPSHOT_RETENTION`, and
`BD_SNAPSHOT_DEADLINE_SECONDS`.

These JSONL files are a portable recovery floor, not a full database backup.
`--all` includes issues plus labels, dependencies, comments, infra items,
templates, gates, and memories, so keep the share private. JSONL does not retain
Dolt branches, commit history, the working set, or non-issue tables. Native
`bd backup` preserves that Dolt state and is complementary; the JSONL floor is
useful when bad remote history or an accidental hard reset has already
propagated.

The tracked config intentionally omits the private remote URL. On each machine,
create this gitignored local override after reading the URL from the 1Password
Secure Note `dotfiles Dolt Remote`:

```yaml
sync:
  remote: "git+ssh://git@example.com/owner/private-beads.git"
```

Alternatively, export the remote for the current shell:

```bash
export BD_SYNC_REMOTE="git+ssh://git@example.com/owner/private-beads.git"
```

Do not commit the real URL, vault names, generated local config, or other Secure
Note contents.

`bd bootstrap` writes the remote back into **tracked** config. On success it
appends a `sync.remote` block to `.beads/config.yaml`, which is committed to a
public repo. The managed pre-commit hook blocks staged `sync.remote` leaks and,
when `HEAD:.beads/config.yaml` is safe, restores the file from `HEAD` before
aborting the commit. `--no-verify` bypasses local hooks, so still check and
revert after every bootstrap:

```bash
git diff --stat .beads/config.yaml                       # expect no output
git show HEAD:.beads/config.yaml >| .beads/config.yaml   # if it was rewritten
```

Reverting costs nothing: `bd` reads the gitignored `.beads/config.local.yaml` on
the next run, so sync keeps working.

Shared-server mode is intentionally not the default for this repo. All clones use
the same database name (`dots`), so one shared Dolt server would also share that
database and change clone isolation semantics. Use it only as an explicit local
experiment.

There are **three Dolt sync peers: WSL2 Ubuntu, WSL2 Debian and macOS**. Native
Windows was briefly a fourth (2026-08-01 to 2026-08-12) and is now a client of
the WSL2 server, so it holds nothing to sync — see **Windows client mode**
above. The JSONL-only satellite mode it ran before that was retired on
2026-08-12 and lives in [`archive/docs/beads.md`](../archive/docs/beads.md).

## Schema migrations (bd version bumps)

Upgrading `bd` can advance the Dolt **schema version** (for example
`1.0.4 -> 1.1.0` moved `dots` from schema v32 to v53). Because `dots` is
remote-backed, `bd` will **not** auto-migrate it — migrating clones
independently forks the schema so `bd dolt pull` can no longer merge (upstream
issue #4259).

Upgrade flow across all machines:

1. Pick **one** designated migrator (any single clone).
2. Back up first: a Dolt safety branch and a full export.

   ```bash
   dolt --host 127.0.0.1 --port "$(cat .beads/dolt-server.port)" --user root \
     --password '' --no-tls --use-db dots \
     sql -q "call dolt_branch('backup_pre_migrate');"
   bd export --all -o ~/dots-backup.jsonl
   ```

3. Migrate and publish:

   ```bash
   BD_ALLOW_REMOTE_MIGRATE=1 bd migrate --yes
   bd dolt commit        # capture the migration working set
   bd dolt push
   ```

4. Every **other** clone re-bootstraps to adopt the migrated schema (an old
   clone cannot upgrade in place) — see the recovery section below, since a
   pre-existing clone will hit `database exists`. The native Windows client is
   not a clone: it reads the WSL2 peer's database, so it has nothing of its own
   to migrate. Keep its `bd.exe` on the same minor version as the host anyway.

## Recovery

**Everything in this section is a POSIX-peer activity.** Native Windows holds no
`dots` database and no `dolt` binary, so run these from WSL2 or macOS. The
PowerShell `bd` wrapper refuses `bd dolt start`/`stop` outright and refuses to
invoke `bd.exe` at all when the WSL2 server is unreachable, precisely so a
recovery attempt there cannot stand up a second database — see **Windows client
mode** above.

**Run `bd doctor` first.** It names most of the failures below directly and often
prints the fix. Reaching for the manual snippets before running it wastes time and
risks damage — every destructive step here is avoidable if `bd doctor` has already
told you what is wrong.

```bash
bd doctor
```

**Never hardcode the Dolt port.** Derive it every time:

```bash
PORT=${BEADS_DOLT_SERVER_PORT:-$(cat .beads/dolt-server.port)}
```

This is not pedantry. WSL2 runs every distro in one VM with a **shared network
namespace**, so `127.0.0.1` is shared between them — a stale or copy-pasted port
silently connects to a *different clone's* database, with no error. Every
destructive snippet in this section is dangerous under those conditions:
`drop database dots` aimed at the wrong port destroys the wrong machine. To see
another distro's server, look for a listener with no owning process:

```bash
ss -ltnp | grep -E '127\.0\.0\.1:[0-9]+'   # yours shows a process; other distros do not
```

Distros share the kernel socket table but have separate PID namespaces, which is
why another distro's listener appears with no owning process. If your *own*
listener also shows no process, re-run with `sudo`.

Windows loopback is *not* shared with WSL2, so that hazard is WSL2-to-WSL2 only.

Every path below can destroy local state. Two preconditions, in order:

1. **Export first.** This is the only reliable undo:

   ```bash
   bd export --all -o ~/dots-$(date +%Y%m%d-%H%M).jsonl
   ```

2. **Confirm the remote is configured.** Without it, a later `bd bootstrap`
   silently builds an *empty* database instead of cloning:

   ```bash
   test -f .beads/config.local.yaml || printf '%s\n' "no local remote override" >&2
   bd dolt start              # --dry-run needs a server, else "connection refused"
   bd bootstrap --dry-run     # must print "clone from remote"
   ```

   If the plan reads `create fresh database`, stop and restore the remote URL
   from the 1Password Secure Note `dotfiles Dolt Remote` first (see
   **Cross-machine sync** above). The URL is also held inside the Dolt repo, so
   dropping the database loses that copy — gitignored `.beads/config.local.yaml`
   is the only one that survives a drop.

### `bd close` fails: `table not found: wisp_dependencies`

```
Error closing dots-abc: affected by close for dots-abc: load wisp dependers:
query: Error 1146 (HY000): table not found: wisp_dependencies
```

Any status change fails — `bd close` and `bd update --status` both. Reads,
creates and non-status updates keep working, so the database looks healthy until
you try to close something. `bd doctor` names it outright:

```
⚠ Dolt Schema: Missing dolt_ignore'd tables: [repo_mtimes wisps wisp_labels
  wisp_dependencies wisp_events wisp_comments]
  dolt_ignore'd tables live in the working set and must be recreated each server session
```

**These tables are per-session working-set state, and their recreation is
unreliable.** `wisps`, `wisp_*`, `local_metadata` and `repo_mtimes` are all
matched by `dolt_ignore`, so they never travel with a clone — every clone is
born without them. `bd` is supposed to recreate them on a fresh server session,
but a schema fast-path (gated by `schema_migrations` being current, which a
clone always is) can skip that recreation entirely.

The mechanism that decides whether recreation runs is not understood, so treat
the two steps below as the whole in-place repertoire. If step 2 does not clear
it, go to `beads-sync init` (next section) rather than trying variations —
repair attempts beyond this were exhausted on 2026-07-26 without result, down to
deleting the `.bd-dolt-ok` fast-path marker.

**Step 1 — restart, then any bd command** (fixed Debian on 2026-07-26):

```bash
bd dolt stop && bd dolt start
bd ready
bd doctor          # the Dolt Schema warning should be gone
```

**Step 2 — clear the migration tracker, then step 1 again.** A clone inherits
`ignored_schema_migrations` rows claiming the table-creating migrations were
already applied, which gates them off. The tracker is local bookkeeping and
repopulates itself:

```bash
PORT=${BEADS_DOLT_SERVER_PORT:-$(cat .beads/dolt-server.port)}
dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls \
  --use-db dots sql -q "delete from ignored_schema_migrations;"
bd dolt stop && bd dolt start
bd ready
```

**Dead ends — do not bother:**

- `bd migrate schema` reports `✓ Schema already at v53` (unrelated track) and
  `BD_ALLOW_REMOTE_MIGRATE=1 bd migrate --yes` dies on the missing
  `local_metadata` it would need to record progress.
- Re-bootstrapping (`bd bootstrap --yes`) clones again and reproduces the
  problem — clones are how the tables go missing in the first place. Use
  `beads-sync init` (next section) instead; it rebuilds without cloning.
- `bd doctor --fix` printed `Fixing Dolt Schema... ✓ Fixed` while its own
  verification pass, in the same invocation, reported the tables still
  missing. See also the caution below on what else `--fix` touches.

If both steps fail, rebuild the peer in place with **`beads-sync init`** — next
section, and the end of the line. Related:
[gastownhall/beads#5033](https://github.com/gastownhall/beads/issues/5033).

### Rebuild a sync peer without cloning (`beads-sync init`)

Proven empirically on Ubuntu WSL2 (2026-08-01). Every known table-loss failure
lives in the **clone** path: a clone inherits the tracked
`ignored_schema_migrations` cursor claiming the table-creating migrations ran,
but none of the dolt_ignore'd tables the cursor is about — and no in-place
repair reliably recreates them. `beads-sync init` sidesteps cloning entirely:

1. `bd init` builds a **fresh local** database, where the ignored migrations
   genuinely run — every wisp table, `local_metadata` and `repo_mtimes` exists
   and the cursor is honest.
2. `dolt_fetch` + `dolt_reset('--hard','origin/main')` then adopts the
   remote's tracked tables and history. Dolt treats dolt_ignore'd tables like
   git treats untracked files — **a hard reset preserves them** (verified:
   all 6 wisp tables and `local_metadata` survived; 119 issues adopted).
3. From then on the local `main` shares history with the remote, so
   `beads-sync pull`/`push` work normally. A follow-up `dolt_pull` reported
   `Everything up-to-date`.

Usage — after forcing a JSONL floor and moving `.beads/dolt` aside yourself (the
command refuses to delete anything):

```bash
./assets/beads-sync.sh snapshot                            # if bd still runs
mv .beads/dolt ~/dots-broken-dolt-$(date +%Y%m%d)
./assets/beads-sync.sh init
```

After init, import the newest snapshot only when it contains local work that
was never pushed. Do not pass `--allow-stale`:

```bash
bd import /mnt/devdrive/beads-snapshots/dots/<machine>/<snapshot>.jsonl
```

Normal import keeps strictly newer local scalar fields. Review the import
summary, run `bd doctor`, and push the recovered work through `beads-sync`.

**Sequencing rule: push from a current peer first** (`beads-sync push` on a
healthy machine). The reset adopts the *remote's* migration cursor; if it
trails the local bd version, the first bd command re-runs the missing ignored
migrations. That is idempotent here, but it is also the machinery that has
failed before. A fresh push makes the adopted cursor match a fresh init
exactly, so nothing re-runs.

The command automates the four traps the manual run hit:

- **Wrong-server attach.** `.envrc` exports a per-checkout
  `BEADS_DOLT_SERVER_PORT`, WSL2 distros share `127.0.0.1`, and `bd init`
  adopts whatever answers on that port (2026-08-01: it stamped a live
  database with a scratch project identity). init stops this checkout's
  server, aborts if the port (when `BEADS_DOLT_SERVER_PORT` is set) still
  has a listener, forces
  `BEADS_DOLT_CLI_DIR` to this checkout, and verifies the new server is
  serving the database it just created before touching anything.
- **Remote-derived init.** If `bd init` can see a git `origin` or a
  configured sync remote, it prints `initialized from git remote!` and takes
  the clone-ish path — inheriting the poisoned cursor with **no wisp tables**
  (caught by the script's own assertion in a test clone, 2026-08-01). init
  temporarily renames `origin` and hides `.beads/config.local.yaml` /
  `BD_SYNC_REMOTE` while `bd init` runs, restoring them immediately after
  (even on failure). The same visibility also feeds the
  [#5068](https://github.com/gastownhall/beads/issues/5068) push hazard: bd
  derives Dolt remotes from the git origin — in this repo, the PUBLIC
  dotfiles repo. After init, the script removes every Dolt remote that does
  not match `sync.remote` and re-adds the private one as `origin`. It also
  restores `.beads/config.yaml` if bd leaked `sync.remote` into it, and warns
  (without auto-resetting) if bd created unsolicited git commits.
- **Identity mismatch.** The tracked `metadata` table rides in with the
  reset, so the DB then carries the shared project identity while
  `.beads/metadata.json` still holds the throwaway one from init — bd refuses
  to connect. init re-points `metadata.json` at the adopted `_project_id`.
- **Cursor drift.** See the sequencing rule above; init runs one `bd list` to
  reconcile and tells you to check `bd doctor`.

If `bd doctor` afterwards reports a **Repo Fingerprint** error, do **not**
run `bd migrate --update-repo-id`: `repo_id` lives in the tracked `metadata`
table and is shared by every peer — rewriting it propagates on the next
push. The mechanism (confirmed 2026-08-01): bd fingerprints the checkout by
the git remote **named `origin`**, nothing else — machines with different
paths but the same `origin` URL fingerprint identically, and a checkout
whose remote was renamed computes a different value even with the same URL.
The fix is to make `origin` exist with the checkout's normal **git** URL —
the public dotfiles repo, never the private Dolt remote. On the one machine
that hit this, `git remote rename gitlab origin` cleared it outright.
A missing-`origin` guard is only needed on a machine with an
**empty** `dolt_remotes` (the #5068 derivation bug needs both); after init
the private remote is configured, so a normal `origin` is the same safe
posture as every other peer.

### Last resort: rebuild as a local-only satellite (no Dolt sync)

Retired 2026-08-12. A satellite makes a second environment that can mutate Beads
state, which then has to be reconciled by hand — the problem client mode exists
to remove. A peer that cannot sync should stop being a peer and connect to a
server instead. Kept for reference in
[`archive/docs/beads.md`](../archive/docs/beads.md).

### Caution: `bd doctor --fix` and bd-installed git hooks

Never run `bd doctor --fix` wholesale in this repo. Two of its "fixes" touch
things this repo manages itself: it edits the tracked `.gitignore`, and it
installs five git hooks into `.git/hooks` — including `prepare-commit-msg`,
which this repo provides via the git template
(`private_dot_config/git/template/hooks/executable_prepare-commit-msg`). It
will also happily act while secrets sit in the working tree (see the
`sync.remote` leak above). Fix items by hand instead, or decline anything that
touches git.

To remove bd's hooks: `bd hooks uninstall` — it is marker-managed and strips
only the `BEGIN/END BEADS INTEGRATION` blocks, preserving pre-existing hook
content it appended to. Ignore `bd hooks list` afterwards claiming
`prepare-commit-msg: installed (version )` — an empty version string means it
is misdetecting a non-bd hook as its own.

### `bd bootstrap` fails: `database exists`

```
Error: Bootstrap failed: clone from remote via server: dolt clone ...:
Error 1007 (HY000): can't create database dots; database exists
```

`bd bootstrap` runs a `dolt clone` that issues `CREATE DATABASE dots`, which
fails when the machine already has an old (or partially cloned) `dots`
database. `bd` has no `--force`/replace flag and no single-database drop
command, so drop the stale database, then re-bootstrap. **Only safe once all
local issues are pushed** — otherwise recover local-only issues first (see
below).

The drop is final: this setup keeps no `dropped_databases/` directory, so
`dolt_undrop()` cannot bring it back. It also takes the Dolt history and the
repo's `dolt remote` config with it.

```bash
PORT=${BEADS_DOLT_SERVER_PORT:-$(cat .beads/dolt-server.port 2>/dev/null)}
if [ -z "$PORT" ]; then
  printf '%s\n' "Start bd first so .beads/dolt-server.port exists" >&2
  exit 1
fi

dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls \
  sql -q "drop database dots;"
bd bootstrap --yes

# verify
dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls \
  --use-db dots sql -q "select max(version) as schema_version from schema_migrations;"
bd ready
```

Any `bd bootstrap --yes` in this section clones, and a fresh clone can arrive
without the local working-set tables — if `bd close` fails afterwards with
`table not found: wisp_dependencies`, go to the escalation ladder above.

If an existing clone keeps trying to use old port `3318`, stop its Beads/Dolt
server, remove stale local runtime files only after the server is stopped, then
reload direnv/the shell:

```bash
bd dolt stop
rm -f .beads/dolt-server.port .beads/dolt-server.pid .beads/dolt-server.lock
direnv reload
```

If the drop misbehaves, the blunt fallback (removes **all** local Dolt
databases, including the local `beads_global` copy, which `bd` re-creates):

```bash
pkill -9 -f 'dolt sql-server' 2>/dev/null; sleep 1
rm -rf .beads/dolt .beads/embeddeddolt .beads/dolt-server.*
bd bootstrap --yes
```

### `bd bootstrap` succeeds but `bd ready` says `database "dots" not found`

Bootstrap prints `Created fresh database with prefix "dots"` and `bd ready`
fails immediately with `database "dots" not found on Dolt server`. Re-running
bootstrap loops forever.

This is an embedded/server split-brain. With no remote configured, bootstrap
takes the `create fresh database` path and writes the new database in
**embedded** mode under `.beads/embeddeddolt/dots/`, while `dolt.mode: server`
sends `bd ready` to the sql-server. Both commands report honestly; they are
looking at different databases.

```bash
ls -a .beads/dolt/         # dots/ is the real database
ls -a .beads/embeddeddolt  # a dots/ here means you have the split
PORT=${BEADS_DOLT_SERVER_PORT:-$(cat .beads/dolt-server.port 2>/dev/null)}
dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls \
  sql -q "show databases;"
```

`bd dolt start` creates `.dolt/` and `.doltcfg/` inside the data dir, so the
server also lists a database named `dolt`. That entry is noise, not a symptom —
subdirectory scanning still works, and a healthy data dir lists both `dolt` and
`dots`.

Fix by restoring the remote first, then re-bootstrapping. Move the stale
directories aside rather than deleting them:

```bash
bd dolt stop
BROKEN=".beads/broken-$(date +%Y%m%d-%H%M)"
mkdir -p "$BROKEN"
mv .beads/dolt .beads/embeddeddolt "$BROKEN"/ 2>/dev/null
rm -f .beads/dolt-server.port .beads/dolt-server.pid .beads/dolt-server.lock
direnv reload

bd dolt start            # from a live shell, so the server inherits ssh-agent
bd bootstrap --dry-run   # must print "clone from remote"
bd bootstrap --yes
ls -a .beads/dolt/       # dots/ must now be present
bd ready
```

`.beads/.gitignore` matches `dolt/` and `embeddeddolt/` at any depth, so the
moved copies stay untracked. Remove the `broken-*` directory once `bd ready`
works.

As above: the re-bootstrap clones, so check `bd doctor` for missing
working-set tables afterwards and use the escalation ladder if they are absent.

### `bd dolt pull`/`push` fails with an auth or user-lookup error

```
failed to read latest version of remote db: No user exists for uid 501
fatal: Could not read from remote repository.
```

```
failed to get remote db; git@gitlab.com: Permission denied (publickey).
fatal: Could not read from remote repository.
```

Neither is an SSH-key problem. Both come from the `git`/`ssh` child spawned by a
**stale, detached `dolt sql-server`** — one that has been running for days and
lost its login-session context, so either `getpwuid()` fails or `SSH_AUTH_SOCK`
no longer points at a live agent (on WSL2, `/tmp/wsl2-ssh-agent/ssh-agent.sock`).
The tell is that the same operation works interactively:

```bash
ssh-add -l             # keys loaded in this shell?
ssh -T git@gitlab.com  # succeeds here but not from the server?
```

Fix by restarting the server from a live session, and make that the habit before
every sync — a freshly started server inherits the current shell's agent socket:

```bash
bd dolt stop && bd dolt start    # or: pkill -9 -f 'dolt sql-server'
bd dolt pull
```

Do not escalate to dropping the database for this failure. It is a process
environment problem; the drop path costs the Dolt history and the repo's remote
config, and leaves the JSONL export as the only way back.

`beads-sync.sh pull` and `push` check this before doing anything: if the remote
is an SSH URL and `ssh-add -l` cannot reach an agent while `SSH_AUTH_SOCK` is
set — a stale socket — they refuse up front and name the fix rather than letting
it surface as `Could not read from remote repository` from inside `dolt_pull`.
With `SSH_AUTH_SOCK` unset they only warn, since a passphraseless `IdentityFile`
is a valid setup. On WSL2 the fix is usually `sset`, the
Pageant relay helper defined in `dot_bashrc.tmpl` and
`dot_local/share/zsh/60-wsl-native-commands.zsh.tmpl`; it does not exist on
macOS, where the equivalent is starting a fresh `ssh-agent` and re-adding the
key. Note that `ssh-add` alone cannot fix an unreachable agent — it is the
answer to the separate "reachable but holds no identities" warning, not to a
dead socket.

### `bd dolt pull` always fails: `cannot merge with uncommitted changes`

`bd dolt pull` cannot succeed in this repo. Use `beads-sync pull` instead — see
**Cross-machine sync** above.

The mechanism, verified 2026-07-25 on both the WSL2 and Windows clones:

1. During its own pull path, `bd` writes rows into `ignored_schema_migrations`
   immediately before calling `dolt merge`.
2. That table matches a `dolt_ignore` pattern, so dolt refuses to stage it and
   `bd dolt commit` reports `nothing to commit`.
3. `dolt merge` refuses to run against **any** dirty working set, ignored or not.

`bd` therefore deadlocks itself. Cleaning the table first does **not** help: with
a provably clean working set, `bd dolt pull` still failed and left the table
dirty afterwards. Read-only commands (`bd ready`, `bd export`) do not trigger it;
`bd bootstrap` does.

The fix is to reset and merge in a **single** dolt session, so no `bd` process
runs in between. That is all `beads-sync pull` does. Manual equivalent:

```bash
PORT=${BEADS_DOLT_SERVER_PORT:-$(cat .beads/dolt-server.port)}
dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls \
  --use-db dots sql -q "
    call dolt_checkout('HEAD', '--', 'ignored_schema_migrations');
    call dolt_pull('origin');
  "
```

Check `select * from dolt_status;` first. Only reset tables that appear in
`dolt_ignore` (`ignored_schema_migrations`, `local_metadata`, `repo_mtimes`,
`wisps`, `wisp_%`). Anything else dirty is real data — run `bd dolt commit`
instead. `beads-sync` enforces this and refuses otherwise.

**Do not retry `bd dolt pull` in a loop.** Upstream reports that repeated failed
pull attempts can corrupt the Dolt journal, needing
`dolt fsck --revive-journal-with-data-loss` (which loses data) to recover. To
check integrity, stop the server and run `dolt fsck` from `.beads/dolt/dots/` —
without the `--revive` flag it is read-only and prints `No problems found` on a
healthy database.

### Merge conflicts on pull

```
error on line 1 for query call dolt_pull('origin','main'): Error 1105 (HY000):
Merge conflict detected, @autocommit transaction rolled back.
```

This one is not a bug in the sync path — two peers really did edit the same row.
The message is unhelpful because with `@@autocommit` on, dolt discards the whole
merge before you can look at it, so it can only tell you which flag to set.

`beads-sync.sh pull` now sets `@@dolt_allow_commit_conflicts = 1` for the merge, so
the conflicts land in the working set instead. It then prints the conflicted
tables and row IDs and runs `dolt_merge('--abort')`, leaving the working set
clean — a half-merged database would break the next `bd` command.

To resolve, inspect the collision first. The merge only exists inside a session,
so the pull and the inspection have to be one invocation:

```bash
PORT=${BEADS_DOLT_SERVER_PORT:-$(cat .beads/dolt-server.port)}
dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls \
  --use-db dots sql -q "
    set autocommit=0;
    call dolt_pull('origin','main');
    select base_id, our_id, their_id, our_status, their_status,
           our_updated_at, their_updated_at
      from dolt_conflicts_issues;
    rollback;
  "
```

`rollback` discards the probe, so this is safe to repeat. Compare every column
that differs before choosing a side — the two peers usually changed *different*
fields, and `--ours` silently drops the other peer's work.

Then apply the resolution, again in one session. Take one side wholesale, graft
the other side's columns back on, and commit the merge:

```bash
dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls \
  --use-db dots sql -q "
    set autocommit=0;
    call dolt_pull('origin','main');
    call dolt_conflicts_resolve('--ours','issues');
    update issues set notes = '...', priority = 2 where id = 'dots-0lg';
    call dolt_commit('-A','-m','Merge origin/main; resolve dots-0lg');
    commit;
  "
```

Take a snapshot first (`./assets/beads-sync.sh snapshot`), and verify afterwards
that `dolt_status` and `dolt_conflicts` are empty and
`dolt_log('main..remotes/origin/main')` counts zero. Worked example: `dots-0lg`
on 2026-08-15, where this peer had closed the issue while the other had raised
its priority and appended a design section — the resolution kept the close and
grafted the other peer's design notes back in.

Note the column names differ between the two system tables: `dolt_conflicts`
keys on `` `table` ``, `dolt_schema_conflicts` on `table_name`.

### Sync wedge: pull says "ahead", push is rejected non-fast-forward

Two commands disagree about the same remote:

```
$ ./assets/beads-sync.sh pull
| fast_forward | conflicts | message                                                  |
| 0            | 0         | cannot fast forward from a to b. a is ahead of b already |

$ ./assets/beads-sync.sh push
 ! [rejected]            main -> main (non-fast-forward)
hint: Updates were rejected because the tip of your current branch is behind
```

They are not looking at the same thing. The remote is a git repo, and dolt keeps
a mirror of it at `.beads/dolt/dots/.dolt/git-remote-cache/<hash>/repo.git`.
`pull` resolves the remote's state through that mirror's blobstore refs; `push`
talks to the live remote. When the mirror goes stale, pull compares against an
old remote root and concludes it is ahead, while push is rejected against the
real one. Diagnosed 2026-08-08 on the WSL2 peer after the Windows peer pushed.

**First rule out the benign case.** On its own, `a is ahead of b already` is
also what dolt says when there is genuinely nothing to pull. Settle it with the
commit graph before touching anything:

```bash
PORT=${BEADS_DOLT_SERVER_PORT:-$(cat .beads/dolt-server.port)}
dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls \
  --use-db dots sql -q "select count(*) from dolt_log('main..remotes/origin/main');"
```

`0` means the remote's head is already contained in local `main` — nothing to
pull, and the only pending action is `beads-sync push`. A non-zero count means
the remote really does have commits this peer lacks, so read on. Verified
2026-08-09: a peer 106 commits ahead reported `a is ahead of b already` and was
perfectly healthy.

For a genuine wedge, confirm with two read-only checks (`CACHE` is the
`repo.git` path above):

```bash
CACHE=$(echo .beads/dolt/dots/.dolt/git-remote-cache/*/repo.git)
git --git-dir "$CACHE" ls-remote origin      # live remote refs
cat "$CACHE/packed-refs"                     # what the mirror believes
```

The wedge looks like: the mirror's `refs/dolt/blobstore/origin/dolt/data/<uuid>`
is days behind the remote's `refs/dolt/data`; several
`refs/dolt/remotes/origin/dolt/data/<uuid>` entries have accumulated, one per
historical peer rebuild; and the mirror has never fetched the remote's current
`refs/heads/main`, so the bookkeeping commit push cannot fast-forward.

**Do not** compare the table file names under the remote's blobstore commit
against `.beads/dolt/dots/.dolt/noms/` and read absences as missing data. Dolt
table files are content-addressed per *file*, and peers pack identical chunks
into differently named files, so the lists diverge routinely on databases that
are perfectly in sync. That check reported 20+ "missing" files on the healthy
peer above. Compare the manifest **root hashes** instead (field 4 of
`manifest`), and trust `dolt_log('main..remotes/origin/main')` over both.

The mirror is derived state — deleting it costs a slower next fetch and nothing
else. Move it aside rather than deleting, so the old copy is the rollback:

```bash
bd export --all -o ~/dots-local-only-$(date +%Y%m%d).jsonl   # floor, always
bd dolt stop
mv .beads/dolt/dots/.dolt/git-remote-cache ~/dots-git-remote-cache-$(date +%Y%m%d)
./assets/beads-sync.sh pull      # rebuilds the mirror; expect "merge successful"
./assets/beads-sync.sh push
```

If push is still rejected on `main -> main` with a fresh mirror, suspect a dolt
version skew between peers — the remote carries both `refs/heads/main` and
`refs/heads/__dolt_remote_info__`, and builds differ in which one they write.
Compare `dolt version` across machines against `dolt_version` in
`.chezmoidata.yaml` (currently `2.2.4`). That theory is **unconfirmed**: on
2026-08-08 the mirror reset alone fixed both directions on dolt 2.2.1, so it was
never tested. Since 2026-08-15 both tools are pinned from that one file and
neither is Homebrew-managed, so the drift the theory depends on should no longer
arise on its own.

### Windows peer: `fork/exec ... Not enough memory resources`

Retired 2026-08-12. Native Windows no longer hosts a `dots` database, so dolt's
git transport never runs there and this failure is unreachable — see **Windows
client mode** above. The diagnosis, the ruled-out list and the `beads-sync init`
verdict are kept in
[`archive/docs/beads.md`](../archive/docs/beads.md).

### Upstream status

Both halves of this are reported upstream and **both issues are closed with no
documented fix**. Last confirmed to reproduce on `bd` 1.1.0 / dolt 2.2.1; not
re-tested against the current pin (`bd` 1.1.2 / dolt 2.2.4 in
`.chezmoidata.yaml`), and `beads-sync pull` still assumes it is present:

- [dolt#7973](https://github.com/dolthub/dolt/issues/7973) — `dolt pull` fails in
  the presence of ignored tables. This is the root cause: `dolt_ignore` suppresses
  a table from `dolt status` but **not** from the merge precondition check.
- [beads#2474](https://github.com/gastownhall/beads/issues/2474) — `bd dolt pull`
  fails with this error and can corrupt journals in server mode.

`bd` compounds it: its own pre-pull check skips `dolt_ignore`d tables when looking
for uncommitted changes, concludes the working set is clean, and calls a merge
that dolt then rejects for exactly those tables. Hence `nothing to commit`
immediately followed by `cannot merge with uncommitted changes`.

A workaround circulating upstream is to set `dolt.auto-commit: "on"` in
`.beads/config.yaml`. **Untested here, and it probably does not help this case:**
auto-commit cannot commit `dolt_ignore`d tables, and those are exactly the ones
leaving the working set dirty. It appears to address a different variant of the
same error — a dirty `config` table, or another database on a shared server.

If either upstream bug is fixed, plain `bd dolt pull` can be used again and the
`pull` command in `beads-sync` can be dropped.

## Recovering local-only issues before a destructive step

If a clone has issues created locally that were never pushed, force a snapshot
before dropping or re-bootstrapping, then import that exact file after init:

```bash
./assets/beads-sync.sh snapshot               # before
# ... move .beads/dolt aside, then beads-sync init ...
bd import /mnt/devdrive/beads-snapshots/dots/<machine>/<snapshot>.jsonl
```

### Import behavior verified on bd 1.1.2

An isolated disposable-repo test on 2026-08-10 used `bd version 1.1.2`
(`20e493e56`) and exercised scalar fields, labels, comments, and dependencies:

- An older incoming row was reported in `stale_skipped_ids`. Newer local scalar
  fields and local collections survived; collections from that stale row did
  not merge.
- At an equal `updated_at`, the row appeared in `tie_kept_local_ids`. Local
  scalar fields won, while incoming labels, comments, and dependencies merged.
- A strictly newer incoming row updated scalar fields and merged, rather than
  replaced, labels, comments, and dependencies.
- Re-importing the same row deduplicated labels, comment IDs, and dependency
  keys.
- `--allow-stale` overwrote newer local scalar fields and timestamps. Relations
  still unioned, but the scalar overwrite makes this an explicitly destructive
  recovery option.

Therefore normal recovery uses `bd import <snapshot>` without `--allow-stale`.
Import is still not a database restore: it cannot recreate Dolt history,
branches, working-set state, or non-issue tables.

**Caution: `bd import --dry-run` is not trustworthy.** It reported
`created: 184, skipped: 0` for a file whose 184 issues all already existed
locally with none newer. It counts lines; it does not model the upsert or the
`updated_at` guard. Compare `updated_at` directly before acting on it.
