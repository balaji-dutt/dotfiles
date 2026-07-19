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
  `127.0.0.1`. The port is `dolt.port` in `.beads/config.yaml` (`3318`),
  materialized at runtime into `.beads/dolt-server.port`.
- **Data dir:** `<repo>/.beads/dolt/` (the `dots` database lives at
  `.beads/dolt/dots/`). The whole `dolt/` tree and the `dolt-server.*` runtime
  files are git-ignored (`.beads/.gitignore`). `.beads/config.yaml` and
  `.beads/metadata.json` are tracked.
- **Remote:** a private `git+ssh://` GitLab repo, configured as `sync.remote`
  in `.beads/config.yaml`. Each machine holds its own Dolt clone and syncs
  through this remote. Auth is the standard GitLab SSH key; no HTTPS
  credentials.
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

```bash
PORT=$(cat .beads/dolt-server.port 2>/dev/null || echo 3318)
dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls \
  sql -q "drop database dots;"
bd bootstrap --yes

# verify
dolt --host 127.0.0.1 --port "$PORT" --user root --password '' --no-tls \
  --use-db dots sql -q "select max(version) as schema_version from schema_migrations;"
bd ready
```

On Windows (PowerShell) the same steps apply with `$PORT =
(Get-Content .beads\dolt-server.port).Trim()` and `--password ""`.

If the drop misbehaves, the blunt fallback (removes **all** local Dolt
databases, including the local `beads_global` copy, which `bd` re-creates):

```bash
pkill -9 -f dolt 2>/dev/null; sleep 1
rm -rf .beads/dolt .beads/dolt-server.*
bd bootstrap --yes
```

### `bd dolt pull`/`push` fails: `No user exists for uid <n>`

```
failed to read latest version of remote db: No user exists for uid 501
fatal: Could not read from remote repository.
```

This is **not** an SSH-key problem. It is OpenSSH's `getpwuid()` failing inside
the `git`/`ssh` child spawned by a **stale, detached `dolt sql-server`** (for
example one that has been running for days and lost its login-session context).
Normal interactive `git`/`ssh -T git@gitlab.com` still works, which is the tell.
Fix by restarting the server from a live session:

```bash
bd dolt stop && bd dolt start    # or: pkill -9 -f dolt
```

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
