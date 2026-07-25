# Beads (bd) operations

[Beads](https://github.com/steveyegge/beads) (`bd`) is the Dolt-backed issue
tracker for this repo. The database is named `dots`. This document covers the
architecture, cross-machine sync, schema migrations on `bd` upgrades, and
recovery procedures.

For first-time setup on a machine, see **Beads setup on a new machine** in the
top-level `README.md`. The DevContainer (`homelab-IaC`) uses a *separate* beads
database (`hliac`) with its own remote — see `docs/devcontainers.md`.

## Architecture

- **Backend:** Dolt. `bd` auto-starts and manages a project-local `dolt
  sql-server` (`dolt.shared-server: false`, `dolt.mode: server`) on
  `127.0.0.1`. The repo does not track a single `dolt.port`; `.envrc` exports a
  stable per-checkout `BEADS_DOLT_SERVER_PORT` so Windows and WSL2 clones do not
  fight over `3318`. `bd` still records the active runtime port in
  `.beads/dolt-server.port`.
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
- **JSONL:** auto-export is disabled (`export.auto: false`) and
  `.beads/issues.jsonl` is git-ignored. Dolt is the single source of truth;
  quarantine or remove a stale `issues.jsonl` before syncing.

## Install per platform

`bd` and the external `dolt` server binary are installed separately — the
`@beads/bd` npm package ships `bd` but not the Dolt server.

| Platform | `bd` | `dolt` |
| --- | --- | --- |
| macOS | Homebrew `beads` formula | Homebrew `dolt` |
| WSL2 | `npm:@beads/bd` via mise (`configs/mise_wsl2.toml`) | GitHub release tarball via ansible; pinned in `configs/packages.yaml` |
| Windows | `@beads/bd` (npm) | `dolt` release |

Keep `bd` at the **same minor version** across machines. Different builds of the
same version string are fine, but a machine on an older minor that targets a
lower Dolt schema cannot read a migrated remote.

### Renovate gating

`bd` schema bumps are deliberately kept off unattended automerge:

- Under `renovate.json5`, all custom-regex-managed deps (including `@beads/bd`)
  auto-merge only patch/digest updates; minor/major bumps carry
  `automerge: false`, so a schema-moving `bd` bump lands as a human-reviewed PR.
  A `@beads/bd`-specific rule additionally hard-blocks `1.0.5`
  (`allowedVersions: "!/^1\\.0\\.5$/"`).
- The WSL2 pin in `configs/mise_wsl2.toml` has no `# renovate:` annotation, so
  it is bumped manually.

## Cross-machine sync

Sync Beads state through the Dolt remote, not JSONL:

```bash
bd dolt pull      # fetch + merge remote into local
bd dolt push      # publish local commits to the remote
bd dolt status    # server state, port, data dir
```

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
public repo. Check and revert after every bootstrap:

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
   pre-existing clone will hit `database exists`.

## Recovery

Every path below can destroy local state. Two preconditions, in order:

1. **Export first.** This is the only reliable undo:

   ```bash
   bd export --all -o ~/dots-$(date +%Y%m%d-%H%M).jsonl
   ```

2. **Confirm the remote is configured.** Without it, a later `bd bootstrap`
   silently builds an *empty* database instead of cloning:

   ```bash
   ls .beads/config.local.yaml || printf '%s\n' "no local remote override" >&2
   bd dolt start              # --dry-run needs a server, else "connection refused"
   bd bootstrap --dry-run     # must print "clone from remote"
   ```

   If the plan reads `create fresh database`, stop and restore the remote URL
   from the 1Password Secure Note `dotfiles Dolt Remote` first (see
   **Cross-machine sync** above). The URL is also held inside the Dolt repo, so
   dropping the database loses that copy — gitignored `.beads/config.local.yaml`
   is the only one that survives a drop.

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

On Windows (PowerShell) the same steps apply with `--password ""` and this
port lookup:

```powershell
$PORT = $env:BEADS_DOLT_SERVER_PORT
if (-not $PORT) {
  $PORT = (Get-Content .beads\dolt-server.port).Trim()
}
```

If an existing clone keeps trying to use old port `3318`, stop its Beads/Dolt
server, remove stale local runtime files only after the server is stopped, then
reload direnv/the shell:

```bash
bd dolt stop
rm -f .beads/dolt-server.port .beads/dolt-server.pid
direnv reload
```

If the drop misbehaves, the blunt fallback (removes **all** local Dolt
databases, including the local `beads_global` copy, which `bd` re-creates):

```bash
pkill -9 -f dolt 2>/dev/null; sleep 1
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
bd dolt stop && bd dolt start    # or: pkill -9 -f dolt
bd dolt pull
```

Do not escalate to dropping the database for this failure. It is a process
environment problem; the drop path costs the Dolt history and the repo's remote
config, and leaves the JSONL export as the only way back.

### `ignored_schema_migrations` working-set churn

After a migration, `bd` may repeatedly toggle rows in
`ignored_schema_migrations`, leaving a perpetually dirty working set that makes
`bd dolt pull` fail with `cannot merge with uncommitted changes`. This is local
bookkeeping only (it never enters a commit or the remote). Clear it with:

```bash
dolt --host 127.0.0.1 --port "$(cat .beads/dolt-server.port)" --user root \
  --password '' --no-tls --use-db dots sql -q "call dolt_reset('--hard');"
```

## Recovering local-only issues before a destructive step

If a clone has issues created locally that were never pushed, export them before
dropping or re-bootstrapping, then re-import after:

```bash
bd export --all -o ~/dots-local-only.jsonl   # before
# ... drop + bootstrap ...
bd import ~/dots-local-only.jsonl            # after, if needed
```
