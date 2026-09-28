#!/usr/bin/env bash

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
common_file="$script_dir/devcontainer-common.sh"
if [[ ! -r "$common_file" ]]; then
  echo "ERROR: Shared devcontainer helper not found: $common_file" >&2
  return 1 2>/dev/null || exit 1
fi
source "$common_file" || {
  echo "ERROR: Failed to source shared devcontainer helper: $common_file" >&2
  return 1 2>/dev/null || exit 1
}

post_attach_main() (
  set -Eeuo pipefail
  LOG_FILE="${LOG_FILE:-/tmp/postAttach.log}"
  mkdir -p "$(dirname "$LOG_FILE")"
  exec > >(tee "$LOG_FILE") 2>&1

  if ! install_better_beads_kanban_vscode_extension; then
    echo "WARN: Better Beads Kanban VSIX install failed; continuing attachment." >&2
  fi
)

post_attach_dispatch() {
  post_attach_main "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  post_attach_dispatch "$@"
fi
