# Hold OpenCode on reviewed v1 releases

This policy keeps OpenCode v1 available while `dots-lsx` investigates the v2
plugin migration. It does not convert configuration or update project-owned
plugin lockfiles. Implementation is tracked by `dots-6f8z`.

## Ownership and protection

| Environment | Owner | Version policy |
| --- | --- | --- |
| macOS | Homebrew `anomalyco/tap/opencode` | Hold the installed stable v1 with `brew pin` |
| Native Windows | Chocolatey `opencode` | Hold the installed stable v1 with `choco.exe pin` |
| WSL2 | mise, provisioned by Ansible | Exact version in `configs/mise_wsl2.toml` |
| Devcontainer | npm, installed by `postCreate.sh` | Exact `opencode-ai` version in the homelab-IaC `configs/npm_packages.txt` |

The WSL/container baseline is `1.18.31`. Host pins freeze **all** updates,
including v1 patches, at each host's installed version; they do not align the
fleet to the manifests. The Brewfile and Chocolatey export remain inventories,
not exact-version or pin-state authorities. Chocolatey exports discard version
and pin metadata, so editing that inventory is not a durable hold.

Both managed OpenCode configurations set `autoupdate: false`. Preserve that
setting and check local/project overrides. Package pins do not block explicit
unpin/force operations, another installer, or an external updater. Do not run
the v2 installer or install `opencode-v2` / `@opencode/cli`: v1 and v2 use the
same `opencode` executable name and shared config locations.

## Rollout and verification

The OS-gated `run_after_macos-opencode-pin.sh.tmpl` and
`run_after_windows-opencode-pin.ps1.tmpl` hooks reconcile holds on each apply.
They resolve native package-manager executables, require exactly one installed
stable v1, reject conflicting executable paths, and verify the installed
version and pin after mutation. They never install, upgrade, downgrade, unpin,
or elevate. Unrelated pins remain untouched.

A missing manager or package produces an informational **hold not established**
message, not proof of protection. Non-v1, ambiguous versions, PATH conflicts,
query failures, and failed pin verification stop the hook. Resolve those
conditions before retrying. Native Windows needs Chocolatey 2.x list semantics.

Chocolatey writes pins under `C:\ProgramData\chocolatey\lib\<package>`, where
`BUILTIN\Users` holds only read and execute rights, so the pin write needs an
elevated session. A non-elevated apply therefore skips the write, reports
**hold not established**, and prints a `Start-Process pwsh -Verb RunAs`
command carrying the pin and its verification as `-EncodedCommand`. Copy that
line into a terminal to establish the hold without re-running chezmoi apply.
This mirrors `run_onchange_after_browser-policies.ps1.tmpl`. Every Chocolatey
mutation passes `--yes`; without it Chocolatey stops on its non-elevated
confirmation prompt, and because the hook captures command output that prompt
is invisible and blocks the apply indefinitely.

Before rollout, inspect the installed binary and pin state:

```sh
type -a opencode
opencode --version
command brew list --versions opencode
command brew list --pinned
```

On Windows, use standalone `pwsh -NoProfile` to bypass inventory-export wrappers:

```powershell
Get-Command opencode -All -CommandType Application
opencode.exe --version
choco.exe list --exact opencode --limit-output
choco.exe pin list --limit-output
```

For an installed, verified stable v1, establish the host hold manually with
`command brew pin opencode` or `choco.exe pin add --name=opencode --yes`, then
repeat the read-only checks. Run only that pin command in elevated profile-free
PowerShell, then verify again. Do not run an entire chezmoi apply elevated just
to establish a hold.

On a fresh Mac, inspect `brew info anomalyco/tap/opencode` before installing;
the ordinary formula installs its current published version, not an arbitrary
historical version. Install only an approved stable v1 and pin immediately. If
the formula no longer offers v1, stop and choose a reviewed packaging solution;
the hook cannot prevent the Brewfile from installing an unsuitable release.
On fresh Windows, install manually using an approved published exact version
(`choco.exe install opencode --version=<approved-v1>`), then pin and verify.
The export-only inventory does not provision this package. A missing package
has no hold until these steps succeed.

Existing WSL installations and containers are not changed by editing a manifest.
Run the normal WSL provisioning or container rebuild only when ready, then
verify the selected `opencode --version`. Package publication can lag between
GitHub, npm, Homebrew, and Chocolatey; do not substitute an unreviewed version
because a particular channel is late.

## Reviewed updates without a seven-day wait

Renovate extracts the mise version from GitHub releases (`anomalyco/opencode`,
with the `v` tag prefix stripped) and the container version from npm
(`opencode-ai`). The dedicated `opencode v1 cli` group permits stable
`>=1.0.0 <2.0.0`, has no minimum release age, and disables automerge even for
patches. Unrelated dependency policies keep their existing ageing rules.

The group requires two dependencies, and the `opencode-v1-policy` CI job checks
that both manifests contain the same exact stable v1 on pushes and merge
requests, including Renovate branches. If publication lag produces different
candidates, leave the change unmerged until both sources offer the reviewed
version. Grouping alone does not guarantee version equality.

Review release notes and plugin compatibility before merging. Then provision
WSL/rebuild containers deliberately. Host upgrades are separate manual actions:

1. Confirm the package manager offers the approved stable v1. Homebrew's normal
   upgrade command selects the formula's current version; it cannot request an
   arbitrary version. If it is not the approved release, leave the hold intact.
2. Close OpenCode sessions and perform the maintenance window without a
   concurrent chezmoi apply (the always-run hook reasserts holds).
3. macOS: `command brew unpin opencode`, then `command brew upgrade opencode`.
   Windows: in elevated profile-free PowerShell,
   `choco.exe pin remove --name=opencode --yes`, then
   `choco.exe upgrade opencode --version=<approved-v1> --yes`. Both mutate and
   need `--yes`, or Chocolatey waits on a confirmation prompt.
4. Verify package and executable versions, re-pin, and query pin state again.
   Re-pin the remaining installed stable v1 even if the upgrade fails. A failed
   upgrade is not evidence that the hold survived. If an unexpected version or
   PATH conflict appears, stop and repair it rather than claiming protection.

Rollback needs a still-available approved artifact and a deliberate downgrade
procedure for its owner. Neither a Homebrew pin nor these hooks retains old
artifacts or performs automatic rollback. Do not mix package owners to bypass
a missing release.

The container project-dependency guard remains separate: tracked
`.opencode/package.json` and `.opencode/package-lock.json` must match the CLI.
Update them in their owning repository only after review; see
[the devcontainer guard workflow](../devcontainers.md). SDK pins in Promptfoo
are not CLI pins and are not part of this Renovate exception.

## Maintenance window and retirement

As checked on **2026-09-22**, upstream published v1 `1.18.32` on September 21,
including provider fixes. That is evidence of active maintenance, not a future
support commitment. No published v1 end-of-life date or guaranteed maintenance
duration was found in the migration/install documentation reviewed:

- [v1 release history](https://github.com/anomalyco/opencode/releases/tag/v1.18.32)
- [v2 migration guide](https://opencode.ai/v2/docs/migrate-v1/)
- [v2 installation](https://opencode.ai/v2/docs/)
- [Homebrew pin semantics](https://docs.brew.sh/FAQ#how-do-i-stop-certain-formulae-from-being-updated)
- [Chocolatey pins](https://docs.chocolatey.org/en-us/choco/commands/pin/)

Reassess this policy with `dots-lsx`; staying on v1 indefinitely may mean missing
security or provider fixes. To retire it, first remove/disable both hold hooks
and the Windows `.chezmoiignore` admission, then remove only OpenCode's host
pins during an approved migration. Adjust both exact manifests, Renovate/CI
policy, tests, inventories, and configuration compatibility together. Removing
a pin alone is temporary: the next apply recreates it.

## Validation boundaries

`tests/test_opencode_v1.py` covers extraction, rule precedence, manifest parity,
platform rendering, and pin-only behavior using fake managers and executables.
PowerShell fixtures run locally or in the retained Linux audit image; they mock
Windows command discovery and elevation. Native Chocolatey shim resolution,
permissions, prompting behavior, and end-to-end host rollout still need a
Windows smoke check under `dots-6f8z`. No test installs/upgrades OpenCode or
mutates real package-manager pins.

The fixtures cannot reach real Chocolatey, so they prove which arguments the
hook passes and which branch it takes, not that Chocolatey accepts them. The
fake Chocolatey matches argument strings exactly and exits 99 otherwise, which
is what pins `--yes` in place. Run `WindowsHoldTests` where a POSIX PowerShell
exists: under WSL with Windows interop on `PATH`,
`resolve_powershell_runtime` selects `pwsh.exe`, which can neither read `/tmp`
nor execute the `/bin/sh` fakes. Clear `WSL_DISTRO_NAME` and `WSL_INTEROP` to
force the retained Linux audit image.
