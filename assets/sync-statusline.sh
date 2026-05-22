#!/usr/bin/env bash
# sync-statusline.sh — keep the vendored claude-pace.sh aligned with the
# pinned upstream tag declared in CLAUDE_PACE_VERSION.
#
# Modes:
#   (no args) | --write    Rewrite both vendored copies to match upstream
#                          for the pinned version.
#   --check                Exit non-zero if either vendored copy drifts from
#                          upstream for the pinned version (no writes).
#
# Reads CLAUDE_PACE_VERSION from the host dot_claude/ copy (canonical source)
# and from the container-dotfiles copy. Both must declare the same version.
#
# Designed to be re-run safely: on a clean tree the default mode is a no-op.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
HOST_FILE="${ROOT}/dot_claude/executable_statusline.sh"
CONTAINER_FILE="${ROOT}/private_Documents/development/container-dotfiles/dotfiles/dot_claude/executable_statusline.sh"

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
command -v diff >/dev/null || { echo "ERROR: diff not found" >&2; exit 1; }

extract_version() {
  local file="$1" v
  v=$(grep -E '^CLAUDE_PACE_VERSION="v[^"]+"' "$file" | head -n1 \
      | sed -E 's/^CLAUDE_PACE_VERSION="(v[^"]+)".*/\1/')
  [ -n "$v" ] || { echo "ERROR: no CLAUDE_PACE_VERSION sentinel in $file" >&2; return 1; }
  printf '%s\n' "$v"
}

for f in "$HOST_FILE" "$CONTAINER_FILE"; do
  [ -f "$f" ] || { echo "ERROR: missing vendored file: $f" >&2; exit 1; }
done

HOST_VER="$(extract_version "$HOST_FILE")"
CONTAINER_VER="$(extract_version "$CONTAINER_FILE")"
if [ "$HOST_VER" != "$CONTAINER_VER" ]; then
  echo "ERROR: version mismatch between vendored copies." >&2
  echo "  host:      $HOST_VER  ($HOST_FILE)" >&2
  echo "  container: $CONTAINER_VER  ($CONTAINER_FILE)" >&2
  echo "Set both to the same version, then re-run." >&2
  exit 1
fi
VER="$HOST_VER"

URL="https://raw.githubusercontent.com/Astro-Han/claude-pace/${VER}/claude-pace.sh"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
upstream="${tmpdir}/upstream.sh"
expected="${tmpdir}/expected.sh"

curl -fsSL "$URL" -o "$upstream"
bash -n "$upstream" || { echo "ERROR: downloaded body fails syntax check: $URL" >&2; exit 1; }

# Construct the expected vendored body:
#   line 1     upstream shebang (verbatim)
#   lines 2-3  renovate sentinel block
#   lines 4..  upstream body from line 2 onwards
{
  head -n 1 "$upstream"
  printf '# renovate: datasource=github-releases depName=Astro-Han/claude-pace\n'
  printf 'CLAUDE_PACE_VERSION="%s"\n' "$VER"
  tail -n +2 "$upstream"
} > "$expected"

drift=0
for vendored in "$HOST_FILE" "$CONTAINER_FILE"; do
  if ! diff -q "$expected" "$vendored" >/dev/null 2>&1; then
    drift=1
    if [ "$MODE" = "check" ]; then
      echo "ERROR: vendored file drifted from upstream ${VER}: ${vendored}" >&2
      diff -u "$vendored" "$expected" | head -n 40 >&2 || true
    else
      cp "$expected" "$vendored"
      echo "wrote ${vendored} (${VER})"
    fi
  fi
done

if [ "$MODE" = "check" ] && [ "$drift" -ne 0 ]; then
  exit 1
fi

if [ "$drift" -eq 0 ]; then
  echo "no changes (both vendored copies already match upstream ${VER})"
fi
