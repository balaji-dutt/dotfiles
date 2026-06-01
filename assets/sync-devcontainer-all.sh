#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

is_devcontainer_host() {
  local uname_s osrelease os_id
  uname_s="$(uname -s 2>/dev/null || true)"

  if [[ "$uname_s" == "Darwin" ]]; then
    return 0
  fi

  if [[ "$uname_s" == "Linux" ]]; then
    osrelease="$(tr '[:upper:]' '[:lower:]' </proc/sys/kernel/osrelease 2>/dev/null || true)"
    os_id=""
    if [[ -r /etc/os-release ]]; then
      os_id="$(. /etc/os-release && printf '%s' "${ID:-}")"
    fi

    [[ "$osrelease" == *microsoft* && "$osrelease" == *wsl2* && "$os_id" == "debian" ]] && return 0
  fi

  return 1
}

if ! is_devcontainer_host; then
  echo "Devcontainer sync/render workflow skipped: supported only on macOS or Debian WSL2."
  exit 0
fi

bash "$repo_root/assets/sync-devcontainer-assets.sh"
bash "$repo_root/assets/sync-opencode-copilot-profiles.sh"
bash "$repo_root/assets/render-container-configs.sh"

echo "Devcontainer sync/render workflow complete."
