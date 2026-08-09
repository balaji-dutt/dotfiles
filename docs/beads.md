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

`bd` and the external `dolt` server binary are installed separately. On WSL2,
mise downloads `bd` directly from its upstream GitHub release archive. This
avoids relying on npm lifecycle scripts, and the archive still does not include
the Dolt server.

| Platform | `bd` | `dolt` |
| --- | --- | --- |
| macOS | Homebrew `beads` formula | Homebrew `dolt` |
| WSL2 | `gastownhall/beads` GitHub release via mise (`configs/mise_wsl2.toml`) | GitHub release tarball via ansible; pinned in `configs/packages.yaml` |
| Windows | `@beads/bd` (npm) | `dolt` release |

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
- The annotated WSL2 pin in `configs/mise_wsl2.toml` uses the
  `github-releases` datasource, so Renovate proposes upstream Beads releases
  without routing installation through npm.

## Cross-machine sync

Sync Beads state through the Dolt remote, not JSONL. The interactive Bash, Zsh,
and PowerShell `bd` wrappers redirect exact `bd dolt pull` and `bd dolt push`
commands to the platform helper with a visible notice. Extra arguments are
refused rather than dropped. Other `bd` commands are unaffected.

Agents and non-interactive automation deliberately bypass those wrappers. Use
`command bd ...` on POSIX or `bd.exe ...` on native Windows for ordinary Beads
commands, and call the sync helper explicitly for pull or push. Do not source a
shell rc file or `beads-helpers.*` in an agent shell.

```bash
./assets/beads-sync.sh status    # dirty tables, is a sync safe?
./assets/beads-sync.sh pull      # guarded replacement for `bd dolt pull`
./assets/beads-sync.sh push      # guarded replacement for `bd dolt push`
./assets/beads-sync.sh init      # rebuild a wedged peer from the remote (Recovery)
```

Windows (PowerShell 7):

```powershell
pwsh -NoProfile -File ./assets/beads-sync.ps1 status
pwsh -NoProfile -File ./assets/beads-sync.ps1 pull
pwsh -NoProfile -File ./assets/beads-sync.ps1 push
pwsh -NoProfile -File ./assets/beads-sync.ps1 init
```

Both accept `-DryRun` / `--dry-run` and `-Backup` / `--backup` (`init` rejects
the backup flag — there is no database to export at that point).

| What you're doing | Command |
| --- | --- |
| `bd create` / `update` / `close` / `list` / `ready` / `show` / `dep` … | Interactive `bd`; agents use `command bd` / `bd.exe` |
| `bd dolt commit`, `bd dolt status` / `start` / `stop` | Interactive `bd`; agents use `command bd` / `bd.exe` |
| **push** | **`beads-sync push` — wrapper redirect or explicit agent call** |
| **pull** | **`beads-sync pull` — wrapper redirect or explicit agent call** |

`bd dolt pull` fails with `cannot merge with uncommitted changes` every time; see
the recovery section below for why. Push uses the same helper because it checks
that a Dolt remote exists before any push work and restarts the server with a
live `SSH_AUTH_SOCK`.

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

As of 2026-08-01 **all four machines (WSL2 Ubuntu, WSL2 Debian, macOS,
native Windows) are Dolt sync peers**. The native Windows checkout ran as a
JSONL-only satellite from 2026-07-26 until it was converted back with
`beads-sync init` (see Recovery). The satellite mode documentation is kept
for the next time a clone path breaks somewhere.

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

   Windows (PowerShell 7):

   ```powershell
   if (-not (Test-Path .beads\config.local.yaml)) { Write-Error 'no local remote override' }
   bd dolt start
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

**Honesty note (2026-07-26):** the steps below fixed the WSL2 clones and failed
on the Windows clone — same `bd` 1.1.0, same remote, repeated on two freshly
cloned databases and on dolt 2.2.1 and 2.2.2. No documented or undocumented
in-place repair worked there, including deleting the `.bd-dolt-ok` fast-path
marker. The mechanism that decides whether recreation runs is not understood.
Try the ladder in order; if step 2 fails, go straight to `beads-sync init`
(next section) rather than repeating variations — that path is proven on
WSL2 and on native Windows (2026-08-01).

**Step 1 — restart, then any bd command** (fixed Debian):

```bash
bd dolt stop && bd dolt start
bd ready
bd doctor          # the Dolt Schema warning should be gone
```

Windows (PowerShell 7): `bd dolt stop; bd dolt start`, then `bd ready`,
`bd doctor`.

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

Windows (PowerShell 7):

```powershell
$PORT = $env:BEADS_DOLT_SERVER_PORT
if (-not $PORT) { $PORT = (Get-Content -Raw .beads\dolt-server.port).Trim() }
dolt --host 127.0.0.1 --port $PORT --user root --password "" --no-tls `
  --use-db dots sql -q "delete from ignored_schema_migrations;"
bd dolt stop; bd dolt start
bd ready
```

**Dead ends — do not bother:**

- `bd migrate schema` reports `✓ Schema already at v53` (unrelated track) and
  `BD_ALLOW_REMOTE_MIGRATE=1 bd migrate --yes` dies on the missing
  `local_metadata` it would need to record progress.
- Re-bootstrapping (`bd bootstrap --yes`) clones again and reproduces the
  problem — clones are how the tables go missing in the first place. On the
  Windows machine a re-clone made things strictly worse. Use
  `beads-sync init` (next section) instead — it rebuilds without cloning.
- `bd doctor --fix` printed `Fixing Dolt Schema... ✓ Fixed` while its own
  verification pass, in the same invocation, reported the tables still
  missing. See also the caution below on what else `--fix` touches.

If the ladder fails, rebuild the peer in place with **`beads-sync init`** —
next section. Only if that also fails, fall back to the **local-only
satellite** below. Related:
[gastownhall/beads#5033](https://github.com/gastownhall/beads/issues/5033).

### Rebuild a sync peer without cloning (`beads-sync init`)

Proven empirically on Ubuntu WSL2 and on native Windows (2026-08-01). Every
known table-loss failure lives in the **clone** path: a clone inherits the
tracked `ignored_schema_migrations` cursor claiming the table-creating
migrations ran,
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

Usage — after exporting a JSONL floor and moving `.beads/dolt` aside yourself
(the command refuses to delete anything):

```bash
bd export --all -o ~/dots-pre-init-$(date +%Y%m%d).jsonl   # if bd still runs
mv .beads/dolt ~/dots-broken-dolt-$(date +%Y%m%d)
./assets/beads-sync.sh init
```

Windows (PowerShell 7): same shape with `Move-Item`, then
`pwsh ./assets/beads-sync.ps1 init`. Proven on the native Windows machine
2026-08-01: 120 issues adopted, `bd doctor` 0 errors after the repo
fingerprint fix below — this is what converted the Windows satellite back
into a sync peer.

**Sequencing rule: push from a current peer first** (`beads-sync push` on a
healthy machine). The reset adopts the *remote's* migration cursor; if it
trails the local bd version, the first bd command re-runs the missing ignored
migrations — idempotent on Linux, but on Windows that is the historically
broken machinery. A fresh push makes the adopted cursor match a fresh init
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
the public dotfiles repo, never the private Dolt remote
(`git remote rename gitlab origin` cleared the error on the Windows
conversion). A missing-`origin` guard is only needed on a machine with an
**empty** `dolt_remotes` (the #5068 derivation bug needs both); after init
the private remote is configured, so a normal `origin` is the same safe
posture as every other peer.

### Last resort: rebuild as a local-only satellite (no Dolt sync)

Proven on the Windows clone (2026-07-26) after every in-place repair failed.
The insight: all known failures live in the **clone** path. `bd init` with no
remote reachable builds the schema locally from zero, where the table-creating
migrations genuinely run. The cost: the new database shares no history with the
remote, so this machine must never `bd dolt push`/`pull` again — it syncs by
JSONL export/import instead.

> **Historical since 2026-08-01 — prefer `beads-sync init` (previous
> section).** It reaches the same fresh-init state and then grafts the
> remote's history on top, producing a full sync peer instead of a
> satellite. The Windows satellite was converted back to a peer exactly this
> way: restore `.beads/config.local.yaml` from the 1Password Secure Note,
> `bd export --all` as a floor, move `.beads/dolt` aside, run
> `pwsh ./assets/beads-sync.ps1 init`, then `git remote rename gitlab
> origin` for the repo fingerprint. Push from a current peer first
> (sequencing rule above). This section is kept for the next time a clone
> path breaks with no working remote to graft from.

```powershell
# 1. Save the issues (works even on a broken database)
bd export --all -o $HOME\dots-pre-satellite.jsonl

# 2. Make sure NO remote is discoverable, or init will clone and re-break:
#    - move the local override aside (do NOT restore it afterwards)
#    - check the TRACKED config too: a prior bootstrap may have leaked
#      sync.remote into it (see Cross-machine sync above)
Move-Item .beads\config.local.yaml .beads\config.local.yaml.bak
git diff .beads\config.yaml          # if a sync: block appears:
git checkout -- .beads\config.yaml

# 3. Fresh local database
bd dolt stop
Remove-Item -Recurse -Force .beads\dolt
Remove-Item .beads\dolt-server.port, .beads\dolt-server.pid, .beads\dolt-server.lock -ErrorAction SilentlyContinue
bd init --server --non-interactive --skip-agents --skip-hooks --prefix dots
```

Then three cleanups `bd init` makes necessary:

1. **It auto-derives a Dolt remote from the git repo's `origin`** — for this
   repo that is the *public* dotfiles repository, so an accidental
   `bd dolt push` would publish issue data there. Remove it and verify:

   ```powershell
   bd dolt remote remove origin
   bd dolt remote list      # must print "No remotes configured."
   ```

2. **It commits to git on your behalf** (`✓ Committed beads files to git`).
   Inspect `git log origin/main..HEAD` and reset anything you did not author.

3. Verify no git hooks appeared despite `--skip-hooks`
   (`ls .git/hooks` / `Get-ChildItem .git\hooks`) — on the Windows rebuild the
   hooks actually came from an earlier `bd doctor --fix`, not from init, but
   check rather than assume. See the caution below for removal.

Finally, load the issues and verify:

```powershell
bd import $HOME\dots-pre-satellite.jsonl
bd doctor            # expect 0 errors, no missing-tables warning
bd ready
```

**Satellite workflow from here on.** `bd import` has upsert semantics — new
issues are created, existing ones updated, newest `updated_at` wins, nothing is
deleted — so refreshes are repeatable and safe in both directions:

- refresh the satellite: `bd export --all` on a sync peer, `bd import` here
- publish satellite work: `bd export --all` here, `bd import` on a sync peer,
  which then pushes to the Dolt remote as usual
- avoid editing the *same issue* on both sides between refreshes; newer-wins
  resolves conflicts silently

**A satellite is NOT safe against a habitual `bd dolt push` by default.**
Verified 2026-07-26, twice: with `dolt_remotes` empty, `bd dolt push` prints
`Configured Dolt remote origin from git origin.` and **begins uploading the
issue database to whatever the git remote named `origin` points at** — for this
repo, the *public* dotfiles repository. It does this unprompted, and a
configured `sync.remote` in `config.local.yaml` does **not** prevent it. Both
observed attempts were stopped only by Ctrl+C mid-upload.

The working guard is to have **no git remote named `origin`** on a satellite
(satellites only — undo it once the machine is a peer again, or the repo
fingerprint check fails; see the Repo Fingerprint note in the
`beads-sync init` section):

```powershell
git remote rename origin gitlab
git branch -u gitlab/main main
```

Ordinary `git push`/`pull` are unaffected (the remote is just named `gitlab`).
With no `origin` to derive from, `bd dolt push` fails safely and prints:

```
Pushing to Dolt remote...
No remote is configured — skipping.

For solo use, pushing is optional — your issues are stored locally
in .beads/ and versioned by Dolt automatically.
```

That message is the guard working. After any accidental push attempt, check
`bd dolt remote list` and remove anything bd wired, and check
`git log <remote>/main..HEAD` — bd has been observed committing to git under
the *user's* identity (message `bd: clear sync.remote`) during push/remote
operations; reset any commit you did not author.

Also delete `config.local.yaml.bak` on a satellite (the real URL lives in
1Password) — a stray backup invites an accidental restore, and as above it
would not even function as a guard.

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

On Windows (PowerShell) the same steps apply with `--password ""` and this
port lookup:

```powershell
$PORT = $env:BEADS_DOLT_SERVER_PORT
if (-not $PORT) {
  $PORT = (Get-Content -Raw .beads\dolt-server.port).Trim()
}
```

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

Windows (PowerShell 7):

```powershell
bd dolt stop
$BROKEN = ".beads\broken-$(Get-Date -Format 'yyyyMMdd-HHmm')"
New-Item -ItemType Directory -Force -Path $BROKEN | Out-Null
Move-Item .beads\dolt, .beads\embeddeddolt $BROKEN -ErrorAction SilentlyContinue
Remove-Item .beads\dolt-server.port, .beads\dolt-server.pid, .beads\dolt-server.lock `
  -ErrorAction SilentlyContinue

bd dolt start
bd bootstrap --dry-run   # must print "clone from remote"
bd bootstrap --yes
Get-ChildItem -Force .beads\dolt\   # dots\ must now be present
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

Windows (PowerShell 7):

```powershell
$PORT = (Get-Content -Raw .beads\dolt-server.port).Trim()
dolt --host 127.0.0.1 --port $PORT --user root --password "" --no-tls --use-db dots `
  sql -q "call dolt_checkout('HEAD', '--', 'ignored_schema_migrations'); call dolt_pull('origin');"
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

Windows (PowerShell 7): same shape with `Move-Item`, then
`pwsh ./assets/beads-sync.ps1 pull` / `push`.

If push is still rejected on `main -> main` with a fresh mirror, suspect a dolt
version skew between peers — the remote carries both `refs/heads/main` and
`refs/heads/__dolt_remote_info__`, and builds differ in which one they write.
Compare `dolt version` across machines against the pin in
`configs/packages.yaml`. That theory is **unconfirmed**: on 2026-08-08 the
mirror reset alone fixed both directions on dolt 2.2.1, so it was never tested.

### Upstream status

Both halves of this are reported upstream and **both issues are closed with no
documented fix**, while the behaviour still reproduces on `bd` 1.1.0 / dolt 2.2.1:

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

If a clone has issues created locally that were never pushed, export them before
dropping or re-bootstrapping, then re-import after:

```bash
bd export --all -o ~/dots-local-only.jsonl   # before
# ... drop + bootstrap ...
bd import ~/dots-local-only.jsonl            # after, if needed
```
