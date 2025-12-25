#!/usr/bin/env bash
set -e

echo "Rendering container configuration files..."

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

# Render Claude Code Router config (reads 1Password, generates config with API keys)
echo "Rendering Claude Code Router config..."
FOR_CONTAINER=true NON_INTERACTIVE_MODE=true chezmoi execute-template < dot_claude-code-router/config.json.tmpl > private_Documents/development/container-dotfiles/dotfiles/configs/config.json

# Render TAVILY_API_KEY (reads 1Password)
echo "Rendering container environment file..."
echo "TAVILY_API_KEY=$(chezmoi execute-template '{{ onepasswordRead "op://Private/67syrnfba3dcbdx2zsydp6ef5y/credential" }}')" > private_Documents/development/container-dotfiles/dotfiles/configs/container_env

echo "✓ Done!"
echo ""
echo "Generated files:"
echo "  - private_Documents/development/container-dotfiles/dotfiles/configs/config.json"
echo "  - private_Documents/development/container-dotfiles/dotfiles/configs/container_env"
echo ""
echo "Next steps:"
echo "  1. Review generated files (DO NOT commit them to git)"
echo "  2. Rebuild your devcontainer in VS Code"
echo "  3. Verify installation"
echo ""
echo "Remember to regenerate these files whenever:"
echo "  - API keys are rotated in 1Password"
echo "  - Claude Code Router config template changes"
