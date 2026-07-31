<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Browser policies

This repo vendors Just the Browser policy artifacts for Chrome and Firefox:

- upstream: <https://github.com/corbindavenport/just-the-browser>
- vendored manifest: `configs/browser-policies/justthebrowser/manifest.json`
- platforms: native Windows and macOS only

Linux/WSL browser policy application is intentionally out of scope.

## Why artifacts are vendored

The apply scripts never download policy files during `chezmoi apply`. Policy
artifacts are committed under `configs/browser-policies/justthebrowser/**` so
upstream changes are visible in normal review diffs before any privileged or
user-approved platform action uses them.

Renovate tracks the upstream Just the Browser GitHub releases by updating the
manifest version, release URL, and raw source base URL together. CI then runs
`assets/sync-browser-policies.py` so Renovate branches cannot merge with the
manifest and vendored artifacts out of sync.

## Windows behavior

Windows uses the upstream HKLM registry policy files:

- `configs/browser-policies/justthebrowser/chrome/install.reg`
- `configs/browser-policies/justthebrowser/firefox/install.reg`

The chezmoi script only imports these files when running in an elevated
PowerShell session. Non-elevated applies print an elevated direct-import
command and skip the registry import so normal applies do not fail.

Rollback from an elevated PowerShell session:

```powershell
reg.exe import configs\browser-policies\justthebrowser\chrome\uninstall.reg
reg.exe import configs\browser-policies\justthebrowser\firefox\uninstall.reg
```

## macOS behavior

macOS uses the upstream `.mobileconfig` profiles. The chezmoi script stages the
profiles under:

```text
~/.local/share/dotfiles/browser-policies/justthebrowser/
```

When staged files change, the script opens them with `open` and starts System
Settings. Install the Chrome and Firefox profiles from General > Device
Management, or from Profiles on older macOS versions.

Rollback on macOS: remove the installed Chrome and Firefox settings profiles
from System Settings / Device Management.

Most repo changes currently happen on Windows. Validate profile XML/plist
syntax there with Python `plistlib` when possible; run `plutil -lint` later on a
macOS host when available.

## Verification

After applying policies and restarting browsers:

- Chrome: open `chrome://policy/`
- Firefox: open `about:policies`

If Firefox reports unexpected policies, check for a legacy application-bundle
JSON file at:

```text
/Applications/Firefox.app/Contents/Resources/distribution/policies.json
C:\Program Files\Mozilla Firefox\distribution\policies.json
```

Remove that file manually only if it is the old Just the Browser JSON policy.

## Updating artifacts

1. Let Renovate or a human update `upstream.version` in the manifest.
2. Run the sync helper from the repo root:

   ```sh
   python3 assets/sync-browser-policies.py --write
   ```

   On Windows, `python assets/sync-browser-policies.py --write` is equivalent
   when `python` resolves to Python 3. Running the helper with no mode flag is
   also write mode.
3. Review the actual policy diffs before applying on Windows or macOS.
4. Validate the pinned version, URLs, vendored bytes, and manifest hashes:

   ```sh
   python3 assets/sync-browser-policies.py --check
   ```
