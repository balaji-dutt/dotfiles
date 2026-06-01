#!/usr/bin/env bash
set -e

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
  echo "Container config render skipped: supported only on macOS or Debian WSL2."
  exit 0
fi

echo "Rendering container configuration files..."

echo "NOTE: This script is ONLY for generating container runtime inputs that must be"
echo "materialized from templates (e.g., files that read 1Password)."
echo ""
echo "It runs on the host, so any chezmoi template logic that depends on host values"
echo "(e.g. .chezmoi.os, lookPath, etc.) may NOT match the container environment."
echo ""
echo "If a template needs container-specific behavior, gate it behind FOR_CONTAINER=true"
echo "and ensure the template branches on that variable rather than host characteristics."
echo ""

# Change to dotfiles root directory
cd "$(dirname "$0")/.."

# Check if chezmoi is available
if ! command -v chezmoi >/dev/null 2>&1; then
  echo "❌ Error: chezmoi not found. Please install chezmoi first."
  exit 1
fi

# Check if 1Password CLI is available
if ! command -v op >/dev/null 2>&1; then
  echo "❌ Error: 1Password CLI not found. Please install and sign in to 1Password first."
  exit 1
fi

echo "✓ Checking prerequisites..."

# Create directory if it doesn't exist
mkdir -p private_Documents/development/container-dotfiles/dotfiles/configs

# Render container environment file (reads 1Password)
echo "Rendering container environment file..."
{
  echo "OP_SERVICE_ACCOUNT_TOKEN=$(chezmoi execute-template '{{ onepasswordRead "op://App Automation Credentials/aifst6wxmvnaf2uraszcf73nlu/credential" }}')"
} > private_Documents/development/container-dotfiles/dotfiles/configs/container_env

# Render OpenCode environment file (reads 1Password)
echo "Rendering OpenCode environment file..."
FOR_CONTAINER=true NON_INTERACTIVE_MODE=true \
  chezmoi execute-template < private_dot_config/opencode/private_opencode.env.tmpl \
  > private_Documents/development/container-dotfiles/dotfiles/configs/opencode.env

echo "✓ Done!"
echo ""
echo "Generated files:"
echo "  - private_Documents/development/container-dotfiles/dotfiles/configs/container_env"
echo "  - private_Documents/development/container-dotfiles/dotfiles/configs/opencode.env"
echo ""
echo "Next steps:"
echo "  1. Review generated files (DO NOT commit them to git)"
echo "  2. Rebuild your devcontainer in VS Code"
echo "  3. Verify installation"
echo ""
echo "Remember to regenerate these files whenever:"
echo "  - API keys are rotated in 1Password"
