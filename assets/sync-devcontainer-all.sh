#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

bash "$repo_root/assets/sync-devcontainer-assets.sh"
bash "$repo_root/assets/sync-opencode-copilot-profiles.sh"
bash "$repo_root/assets/render-container-configs.sh"

echo "Devcontainer sync/render workflow complete."
