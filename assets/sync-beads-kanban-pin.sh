#!/usr/bin/env bash
# sync-beads-kanban-pin.sh — keep the Better Beads Kanban VSIX checksum aligned
# with the version pinned in the three install sites.
#
# Modes:
#   (no args) | --write    Rewrite EXPECTED_SHA in all three files to match the
#                          release named by the pinned version.
#   --check                Exit non-zero if any EXPECTED_SHA drifts from the
#                          release (no writes).
#
# Renovate bumps only the version sentinel (FORK_VERSION / $ForkVersion /
# fork_version); the tag and asset name are derived inside each script, and the
# checksum is what Renovate cannot compute. CI runs --write on
# renovate/beads-kanban-* branches and --check everywhere else.
#
# Designed to be re-run safely: on a clean tree the default mode is a no-op.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REPO="balajidutt/better-beads-kanban"

SH_FILE="${ROOT}/.chezmoiscripts/run_onchange_after_install_better_beads_kanban.sh.tmpl"
PS1_FILE="${ROOT}/.chezmoiscripts/run_onchange_after_install_better_beads_kanban.ps1.tmpl"
CONTAINER_FILE="${ROOT}/private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/devcontainer-common.sh"

FILES=("$SH_FILE" "$PS1_FILE" "$CONTAINER_FILE")

case "${1:-}" in
  ""|--write) MODE="write" ;;
  --check)    MODE="check" ;;
  -h|--help)
    sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "ERROR: unknown argument: $1" >&2
    echo "Usage: $0 [--write|--check]" >&2
    exit 2
    ;;
esac

command -v curl >/dev/null || { echo "ERROR: curl not found" >&2; exit 1; }

sha256_file() {
  local file_path=$1

  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file_path" | cut -d ' ' -f 1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file_path" | cut -d ' ' -f 1
  else
    echo "ERROR: neither sha256sum nor shasum is available." >&2
    return 1
  fi
}

# Matches FORK_VERSION="x", $ForkVersion = "x", and fork_version="x".
# SC2016: $ForkVersion is a literal PowerShell identifier inside the pattern,
# not a shell expansion — single quotes are required here.
# shellcheck disable=SC2016
VERSION_RE='^[[:space:]]*(\$ForkVersion[[:space:]]*=|FORK_VERSION=|fork_version=)[[:space:]]*"([^"]+)"'
# Same three spellings for the checksum assignment.
# shellcheck disable=SC2016
SHA_RE='^([[:space:]]*(\$ExpectedSha[[:space:]]*=|EXPECTED_SHA=|expected_sha=)[[:space:]]*")[0-9a-f]{64}(")'

extract_field() {
  local file="$1" regex="$2" label="$3" value
  # `|| true`: without it, set -e/pipefail aborts on a non-matching grep and the
  # message below never prints.
  value=$(grep -E -m1 "$regex" "$file" | sed -E "s/${regex}.*/\2/" || true)
  [ -n "$value" ] || { echo "ERROR: no $label sentinel in $file" >&2; return 1; }
  printf '%s\n' "$value"
}

for f in "${FILES[@]}"; do
  [ -f "$f" ] || { echo "ERROR: missing install site: $f" >&2; exit 1; }
done

VER=""
for f in "${FILES[@]}"; do
  file_ver="$(extract_field "$f" "$VERSION_RE" "fork version")"
  if [ -z "$VER" ]; then
    VER="$file_ver"
  elif [ "$file_ver" != "$VER" ]; then
    echo "ERROR: pinned version mismatch between install sites." >&2
    echo "  $VER  (first site)" >&2
    echo "  $file_ver  ($f)" >&2
    echo "Set all three to the same version, then re-run." >&2
    exit 1
  fi
done

TAG="v${VER}"
ASSET="better-beads-kanban-${VER}.vsix"
BASE_URL="https://github.com/${REPO}/releases/download/${TAG}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# Prefer the release's SHA256SUMS manifest (a few bytes) over downloading the
# ~1.4 MB VSIX. Fall back to hashing the asset if a release omits the manifest.
EXPECTED=""
if curl -fsSL "${BASE_URL}/SHA256SUMS" -o "${tmpdir}/SHA256SUMS" 2>/dev/null; then
  EXPECTED=$(awk -v want="$ASSET" '$2 == want || $2 == "*" want { print $1; exit }' \
    "${tmpdir}/SHA256SUMS")
fi

if [ -z "$EXPECTED" ]; then
  echo "INFO: SHA256SUMS unavailable or missing ${ASSET}; hashing the asset instead."
  curl -fsSL "${BASE_URL}/${ASSET}" -o "${tmpdir}/${ASSET}" \
    || { echo "ERROR: failed downloading ${BASE_URL}/${ASSET}" >&2; exit 1; }
  EXPECTED="$(sha256_file "${tmpdir}/${ASSET}")"
fi

if ! printf '%s' "$EXPECTED" | grep -Eq '^[0-9a-f]{64}$'; then
  echo "ERROR: could not resolve a sha256 for ${ASSET} at ${TAG}" >&2
  exit 1
fi

drift=0
for f in "${FILES[@]}"; do
  current=$(grep -E -m1 "$SHA_RE" "$f" | grep -Eo '[0-9a-f]{64}' | head -n1 || true)
  if [ -z "$current" ]; then
    echo "ERROR: no expected-sha sentinel in $f" >&2
    exit 1
  fi
  [ "$current" = "$EXPECTED" ] && continue

  drift=1
  if [ "$MODE" = "check" ]; then
    echo "ERROR: EXPECTED_SHA drifted from ${TAG}: ${f}" >&2
    echo "  expected: ${EXPECTED}" >&2
    echo "  actual:   ${current}" >&2
  else
    # Rewrite only the hash, preserving each file's indentation and alignment.
    # `#` as the delimiter: the pattern itself contains `|` alternations.
    # Write via a temp file rather than `sed -i.bak`: a stray *.tmpl.bak left in
    # .chezmoiscripts/ by an interrupted run would be read as a source entry.
    sed -E "s#${SHA_RE}#\\1${EXPECTED}\\3#" "$f" > "${tmpdir}/rewrite"
    cat "${tmpdir}/rewrite" > "$f"
    echo "wrote ${f} (${TAG})"
  fi
done

if [ "$MODE" = "check" ] && [ "$drift" -ne 0 ]; then
  exit 1
fi

if [ "$drift" -eq 0 ]; then
  echo "no changes (all three install sites already match ${TAG})"
fi
