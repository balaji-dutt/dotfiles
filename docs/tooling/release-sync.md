<!-- markdownlint-disable MD013 MD040 MD041 -->

# Release sync (native Windows)

`.chezmoiscripts/run_after_windows-release-sync.ps1.tmpl` keeps local copies of
GitHub release assets current. For each entry in a local config file it finds
the newest release on the chosen channel, verifies the asset against the
SHA-256 digest GitHub publishes, extracts it into a versioned folder, and points
a `current` junction at that folder. It never runs anything it downloads.

The hook ships in this public repo, but the list of repositories and
destinations does not. The config is machine-specific and is applied by the
private dotfiles repo, the same way as `peer-envs.json`
([peer environments](peer-environments.md#configuration)). Without the file the
hook does nothing and prints nothing.

## Configuration

The hook reads `%USERPROFILE%\.config\dotfiles\release-sync.json`.
`DOTFILES_RELEASE_SYNC_CONFIG` overrides the path; the tests use it.

```json
{
  "schema_version": 1,
  "releases": [
    {
      "name": "example-tool",
      "repo": "owner/repo",
      "channel": "prerelease",
      "asset_pattern": "^example-tool.*\\.zip$",
      "dest": "D:\\Tools\\example-tool",
      "keep": 3,
      "post_install": "C:\\Users\\me\\.config\\dotfiles\\release-sync\\example-tool.post.ps1"
    }
  ]
}
```

| Field | Required | Meaning |
| :--- | :--- | :--- |
| `name` | yes | Slug matching `^[a-z0-9][a-z0-9-]*$`. Names the state file, so it must be unique. |
| `repo` | yes | `owner/repo` on GitHub. |
| `channel` | yes | `prerelease` takes the newest published release including pre-releases; `stable` skips pre-releases. Drafts are always skipped. "Newest" means latest `published_at`, not highest version number. |
| `asset_pattern` | yes | .NET regex, case-sensitive. It must match exactly one asset name in the chosen release, or the entry is skipped with a warning. The asset name must end in `.zip`; anything else is refused before download. |
| `dest` | yes | Absolute path, not a drive root, unique across entries. Its parent must exist; when it doesn't (an unmounted drive, say), the entry is skipped. `dest` itself is created on first install. |
| `keep` | no | Number of installed releases to keep, 1 to 50. Default 3. |
| `post_install` | no | Absolute path to a `.ps1` run after each new release. See [Post-install contract](#post-install-contract). |

Unknown keys are rejected so a misspelled `post-install` fails loudly instead
of being ignored. An invalid entry is skipped with a warning; the other entries
still run.

## Layout

```
<dest>\
  current\            junction to releases\<tag>
  releases\
    <tag>\            extracted asset, one folder per installed release
  ...                 anything else is yours and is never touched
```

The hook writes only `<dest>\releases\`, `<dest>\current` (plus a transient
`<dest>\current.new` while swapping), its state file, and a temporary download
folder. Programs that write output next to the directory they are launched from
can be run from `<dest>` as `.\current\<program>`, so their output stays at
`<dest>` across upgrades.

To pin an older release, repoint the junction. Checks that find nothing new
leave a repointed junction alone, and pruning never deletes the folder it
targets. The next new release moves `current` to that release. A missing
`current` is recreated on the next check.

```powershell
cmd /c rmdir D:\Tools\example-tool\current
cmd /c mklink /J D:\Tools\example-tool\current D:\Tools\example-tool\releases\v1.2.3
```

`rmdir` on a junction removes the link, not the target.

## When it runs

The hook is a `run_after_` script, so it runs on every `chezmoi apply`. The
network check is throttled to once per 24 hours per entry. The timestamp of
the last completed check is kept in the state file.

| Environment variable | Effect |
| :--- | :--- |
| `DOTFILES_RELEASE_SYNC_FORCE=1` | Check now, ignoring the throttle. |
| `DOTFILES_RELEASE_SYNC_DRY_RUN=1` | Query GitHub and print what would be installed or run; download and write nothing. |
| `GITHUB_API_TOKEN` | Optional. Sent as a bearer token when set; unauthenticated requests get 60 per hour, which is plenty at one per entry per day. |

A check counts as completed when the hook installed a release, found it
already current, or found the release unusable for a reason a retry won't fix
(no matching asset, a non-zip asset, no digest, an unexpected download URL, a
tag that isn't a safe folder name). API errors, download errors, digest mismatches, and
extraction failures leave the check due, so the next apply retries.

## Verification and safety

- The asset's `browser_download_url`, after URI normalization, must be an
  `https://github.com/<repo>/releases/download/...` URL on the default port.
- The release must publish a `sha256:` digest for the asset; without one the
  hook refuses to install. The downloaded file must match it.
- Every zip entry must resolve inside the staging folder; an archive with an
  entry such as `..\x` is rejected before anything is extracted.
- Extraction goes to `releases\.staging-<guid>` and is renamed into place, so a
  failed run never leaves a half-extracted `releases\<tag>`.
- If `current` or `current.new` exists and is not a junction, or
  `releases\<tag>` exists but the state file has no record of installing it,
  the hook refuses and changes nothing for that entry.
- Pruning deletes only folders the state file records as installed by the hook,
  never the newest install, and never the folder `current` points at.
- Leftover `releases\.staging-<guid>` and `releases\.retired-<guid>` folders
  from an interrupted run are deleted on the next run. Other dot-folders are
  left alone.
- When upstream replaces an asset on an existing tag, the new digest no longer
  matches the record, so the hook reinstalls that tag in place. The old folder
  is set aside as `.retired-<guid>` and moved back if the new one can't be
  renamed into place. If that move back also fails, the next run's sweep
  deletes the retired copy and a later check downloads the tag again.
- Tags become folder names, so a tag must start with a letter or digit, use
  only letters, digits, `.`, `_`, and `-`, not end in `.`, and not be a
  reserved Windows device name such as `CON` or `LPT1`.

A matching digest proves the download is the file GitHub serves for that
release. It says nothing about whether the file is safe to run.

## State

`%LOCALAPPDATA%\dotfiles\release-sync\<name>.json`:

```json
{
  "schema_version": 1,
  "last_check_utc": "2026-11-09T04:00:00.0000000Z",
  "current_tag": "v1.2.4",
  "post_install_done_tag": "v1.2.4",
  "installed": [
    { "tag": "v1.2.3", "asset": "example-tool.zip", "sha256": "…", "installed_utc": "…" },
    { "tag": "v1.2.4", "asset": "example-tool.zip", "sha256": "…", "installed_utc": "…" }
  ]
}
```

Deleting the state file makes the hook check on the next apply. After that,
though, every existing `releases\<tag>` folder counts as unrecorded and blocks
reinstalling that tag. Delete `<dest>\releases` and `<dest>\current` with it,
or leave both alone.

## Post-install contract

When `post_install` is set and `post_install_done_tag` differs from
`current_tag`, the hook runs:

```
pwsh -NoLogo -NoProfile -NonInteractive -File <post_install> -ReleaseDir <dest>\releases\<tag> -Tag <tag> -Dest <dest>
```

| Exit code | Meaning |
| :--- | :--- |
| `0` | Done. The hook records `post_install_done_tag` and doesn't run it again for this tag. |
| `75` | Deferred, for example because an application that must be closed is running. Retried on the next apply. |
| anything else | Failed. The hook prints a warning and retries on the next apply. |

Retries need no network access and ignore the 24-hour throttle. The script
must be idempotent, because a deferred or failed run repeats it from the start.

## Failure policy

Every runtime failure prints a line starting `WARNING: release-sync:` and the
hook exits 0, so a GitHub outage or a locked folder never aborts the rest of
`chezmoi apply`.

## Tests

`tests/test_windows_release_sync.py` runs the rendered hook under native
Windows `pwsh` with `Invoke-RestMethod` and `Invoke-WebRequest` replaced by
fixtures. It covers channel selection, throttling, digest and URL refusal, zip
traversal, junction activation, pruning, manual pins, same-tag reinstall,
post-install deferral, dry run, and invalid config. `tests/test_windows_lifecycle_boundaries.py`
checks that the hook makes no network call and writes no state without a
config. Both run in the `lifecycle-windows` step of `configs/test-suites.json`.
The relative-symlink pin test skips on hosts where the test user can't create
directory symlinks (no Developer Mode or `SeCreateSymbolicLinkPrivilege`).
