<!-- markdownlint-disable MD013 -->

# Archived Beads recovery procedures

Sections retired from [`docs/beads.md`](../../docs/beads.md). They are kept
because the diagnosis is worth reading, not because they are still operational
guidance. Do not follow them.

Retired 2026-08-12, when native Windows stopped hosting its own `dots` database
and became a client of the Dolt server WSL2 already runs (`dots-to5`).

## Retired: rebuild as a local-only satellite (no Dolt sync)

**Superseded 2026-08-12.** A satellite is a false solution. Two environments
that can both mutate Beads state means a human has to remember which one wrote
last, which export is current, and where to import it — a worse reimplementation
of what the git-backed Dolt remote already does. The answer to a peer that
cannot sync is to stop it being a peer: point it at a known-good server.


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

## Retired: Windows peer — `fork/exec ... Not enough memory resources`

**Superseded 2026-08-12.** The transport described below no longer runs on
Windows. `bd` there is a client of the WSL2 Dolt server, so no `dolt` process on
Windows ever spawns `git`. The bug is real and still unfixed upstream; this
arrangement routes around it rather than resolving it. The JSONL round-trip at
the end is retired too, for the same reason as the satellite above.


`bd dolt push` / `pull` fail on the native Windows peer with a useless surface
message:

```
Pushing to Dolt remote...
Error: dolt push failed: - Uploading...: exit status 1
```

**The real error is only in `.beads\dolt-server.log`.** Nothing is swallowed by
`beads-sync.ps1` — it captures `2>&1` — the detail is below bd, in the `git`
child dolt spawns:

```powershell
Select-String -Path .beads\dolt-server.log -Pattern 'error running query' |
  Select-Object -Last 3 | ForEach-Object { $_.Line }
```

```
error="failed to get remote db; git command failed
command: git cat-file -s <sha>
error: fork/exec C:\Program Files\Git\cmd\git.exe:
       Not enough memory resources are available to process this command."
```

That is Windows `ERROR_NO_SYSTEM_RESOURCES` (1450) raised on **process
creation**. Dolt's git-backed remote shells out to `git` once per object, and on
Windows that eventually fails. Diagnosed 2026-08-10; **unresolved, and believed
to be an upstream dolt bug** rather than anything configurable here.

**Ruled out** (each cost real time — don't re-test):

| Suspect | Measured |
| --- | --- |
| SSH / auth | `git cat-file -s` is a local object read; no network involved |
| Memory / commit | 45.7 GB free RAM, 47.2 GB free commit, pagefile usage 0 |
| Desktop heap | `SharedSection=1024,20480,768`; fails on the interactive desktop too |
| Concurrency fan-out | fails with a single git child |
| Defender ASR | no rules configured |
| Defender AV | exclusions verified for `.beads\dolt` paths and `dolt.exe`/`git.exe` |
| Kernel resources | 4.29e9 free system PTEs, paged pool 853 MB, nonpaged 1.17 GB |
| Mirror bloat | git-remote-cache held only 52 objects / 7.4 MB |
| Dolt version skew | both peers on dolt 2.2.1; the WSL2 half of the same box syncs fine |

The decisive control: a shell loop of 300 `git --version` spawns completed in
16.9 s with zero failures on the same box. `CreateProcess` fails **only when
dolt is the parent**.

**Mitigation worth keeping.** Put `C:\Program Files\Git\mingw64\bin` ahead of
`C:\Program Files\Git\cmd` on PATH. The `cmd\git.exe` shim creates the real
binary suspended and dies before resuming it, leaving an orphan
(`ThreadState=Wait`, `WaitReason=Suspended`) that dolt waits on forever. This
turned a silent 9m33s hang into an immediate error. It does not fix sync — a
fast honest failure just beats a hang.

**`beads-sync init` is NOT a recovery path for this.** Its final step is
`call dolt_fetch(...); call dolt_reset('--hard','origin/main')` — the failing
call — and it runs *after* the local database has been replaced. Verified
2026-08-11 non-destructively: `dolt init` + `dolt remote add` + `dolt fetch` in
a scratch `%TEMP%` directory, empty store, fresh mirror, `mingw64\bin\git.exe`
on PATH, fails with the same `fork/exec` error on its first objects. The failure
has nothing to do with local store state, mirror size, or the Git shim.

**Working around it.** The Windows peer is offline for Dolt sync in *both*
directions, but local `bd` writes still work. Round-trip through JSONL instead:

```powershell
bd export --all -o V:\beads-snapshots\dots\big-rig-windows\handoff.jsonl   # on Windows
```

```bash
command bd import /mnt/devdrive/beads-snapshots/dots/big-rig-windows/handoff.jsonl
```

`bd import` upserts and only rewrites a local row when the incoming `updated_at`
is **strictly newer**, so a stale peer's export cannot clobber current state.
Never pass `--allow-stale`.
