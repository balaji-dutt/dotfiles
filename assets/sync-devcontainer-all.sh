#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

is_wsl2() {
  local osrelease
  osrelease="$(tr '[:upper:]' '[:lower:]' </proc/sys/kernel/osrelease 2>/dev/null || true)"

  [[ "$osrelease" == *microsoft* && "$osrelease" == *wsl2* ]]
}

os_release_id() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck source=/dev/null
    . /etc/os-release
    printf '%s' "${ID:-}"
  fi
}

is_debian_wsl2() {
  [[ "$(uname -s 2>/dev/null || true)" == "Linux" ]] || return 1
  is_wsl2 && [[ "$(os_release_id)" == "debian" ]]
}

is_devcontainer_host() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || true)"

  if [[ "$uname_s" == "Darwin" ]]; then
    return 0
  fi

  is_debian_wsl2
}

if ! is_devcontainer_host; then
  echo "Devcontainer sync/render workflow skipped: supported only on macOS or Debian WSL2."
  exit 0
fi

bash "$repo_root/assets/sync-devcontainer-assets.sh"
bash "$repo_root/assets/render-container-configs.sh"

echo "Devcontainer sync/render workflow complete."
