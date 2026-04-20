#!/usr/bin/env bash
# install-promptfoo.sh — Cross-platform promptfoo installer (macOS / Linux / WSL2)
#
# Checks for promptfoo presence, installs via brew (macOS) or npm fallback.
# Exit codes: 0 = installed/already present, 1 = install failed.

set -euo pipefail

PROMPTFOO_CMD="promptfoo"

# -------------------------------------------------------------------
# Check if promptfoo is already installed
# -------------------------------------------------------------------
if command -v "$PROMPTFOO_CMD" &>/dev/null; then
    echo "promptfoo is already installed: $(command -v "$PROMPTFOO_CMD")"
    "$PROMPTFOO_CMD" --version 2>/dev/null || true
    exit 0
fi

# Also check npx availability (promptfoo can run via npx without global install)
if command -v npx &>/dev/null && npx promptfoo@latest --version &>/dev/null 2>&1; then
    echo "promptfoo is available via npx"
    exit 0
fi

echo "promptfoo not found. Attempting installation..."

# -------------------------------------------------------------------
# Detect platform
# -------------------------------------------------------------------
OS="$(uname -s)"
INSTALLED=false

# -------------------------------------------------------------------
# macOS: try brew first, fallback to npm
# -------------------------------------------------------------------
if [[ "$OS" == "Darwin" ]]; then
    if command -v brew &>/dev/null; then
        echo "Installing promptfoo via Homebrew..."
        if brew install promptfoo 2>/dev/null; then
            INSTALLED=true
        else
            echo "Homebrew install failed, falling back to npm..."
        fi
    fi
fi

# -------------------------------------------------------------------
# npm fallback (all platforms)
# -------------------------------------------------------------------
if [[ "$INSTALLED" == "false" ]]; then
    if command -v npm &>/dev/null; then
        echo "Installing promptfoo via npm (global)..."
        npm install -g promptfoo
        INSTALLED=true
    else
        echo "ERROR: Neither brew nor npm found. Install Node.js/npm first."
        echo "  macOS:     brew install node"
        echo "  Ubuntu:    sudo apt install nodejs npm"
        echo "  Or visit:  https://nodejs.org/"
        exit 1
    fi
fi

# -------------------------------------------------------------------
# Verify installation
# -------------------------------------------------------------------
if command -v "$PROMPTFOO_CMD" &>/dev/null; then
    echo "promptfoo installed successfully: $(promptfoo --version)"
    exit 0
else
    echo "ERROR: Installation completed but promptfoo not found in PATH."
    exit 1
fi
