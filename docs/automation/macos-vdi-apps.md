<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# macOS VDI Apps

Citrix Workspace and the Zoom VDI plugin are managed outside Homebrew.
Citrix is intentionally absent from `brewfile.txt` so `brew bundle`, cask
upgrade flows, and wrapper-driven manifest updates do not move it to a newer
build without an explicit dotfiles metadata change.

## Public vs Local Data

Committed data in `.chezmoidata.yaml` is limited to non-secret policy:

- VDI management defaults to disabled and report-only.
- Citrix uses the desired version family `26.03.11`.
- Zoom VDI uses the full `pkgutil` package version `6.4.17.26900`.
- Citrix falls back to `/Users/balaji/Downloads/Utilities/CitrixWorkspaceApp.dmg`
  when no local URL source is configured.

Do not commit employer portal hosts, private installer URLs, or private
1Password item names. Store direct installer URLs in 1Password fields and enter
only the local 1Password references in the generated chezmoi config. Generic
examples are safe, such as `op://Vault/Installer Item/pkg_url`.

Useful 1Password fields:

- Zoom VDI direct `.pkg` URL for automation.
- Citrix Workspace direct `.dmg` URL for automation.
- Optional source/download page notes for human provenance.
- Optional full package versions copied from `pkgutil`.
- Optional SHA-256 checksums for the downloaded package or DMG.

## Modes

The hook `.chezmoiscripts/run_onchange_after_macos-vdi-apps.sh.tmpl` always
reports installed Citrix and Zoom VDI versions on macOS. It exits without
installing when either VDI management is disabled or install mode is disabled.

Enable install mode only on machines where automated VDI installer handling is
wanted. Missing or unreadable runtime URL/path configuration is a warning, not a
failed `chezmoi apply`.

`macos_vdi.enabled` controls drift handling beyond passive reporting.
`macos_vdi.install` controls whether the hook may install/update apps. Set both
in local chezmoi config before expecting automatic fixes, and configure the
installer URL refs or local paths first.

## Trigger Behavior

This is a `run_onchange` hook, not a `run_after` hook. Chezmoi runs it when the
rendered script changes, including when `macos_vdi` data changes. It should not
be expected to appear on every `chezmoi apply`.

## Citrix Source Order

When Citrix needs installation and install mode is enabled, sources are tried in
this order:

1. Local 1Password ref for a direct Citrix `.dmg` URL.
2. Local Citrix DMG path override in chezmoi data.
3. The committed default DMG path.

The DMG is mounted read-only, the contained package is installed with
`installer`, and the DMG is detached during cleanup.

## Safe Updates

To test a newer approved build:

1. Install it manually or update the private 1Password URL field.
2. Confirm the installed versions with:
   `pkgutil --pkg-info com.citrix.ICAClient` or
   `pkgutil --pkg-info us.zoom.ZoomVDI`.
3. Bump only the public desired version metadata in `.chezmoidata.yaml`:
   - Citrix: `macos_vdi.citrix.desired_family` and
     `macos_vdi.citrix.display_version`.
   - Zoom VDI: `macos_vdi.zoom.desired_pkg_version`.
4. Add or update checksums when available.
5. Verify no private URL, host, or item name appears in the git diff.

Automatic downgrades are refused unless `macos_vdi.allow_downgrade` is set to
true in data.
