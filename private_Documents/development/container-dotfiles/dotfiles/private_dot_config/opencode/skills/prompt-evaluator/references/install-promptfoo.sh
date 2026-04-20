#!/usr/bin/env bash
# install-promptfoo.sh — Cross-platform prerequisite installer
#
# Ensures promptfoo and @opencode-ai/sdk are available for prompt evaluation.
# Install priority: mise (if available) > brew (macOS, promptfoo only) > npm.
# Exit codes: 0 = installed/already present, 1 = install failed.

set -euo pipefail

PROMPTFOO_CMD="promptfoo"
PROMPTFOO_READY=false
SDK_READY=false

check_sdk() {
    # Check mise-managed install
    if command -v mise &>/dev/null; then
        mise where npm:@opencode-ai/sdk &>/dev/null && return 0
    fi
    # Check npm global install
    if command -v npm &>/dev/null; then
        local npm_root
        npm_root="$(npm root -g 2>/dev/null)"
        [[ -d "${npm_root}/@opencode-ai/sdk" ]] && return 0
    fi
    # Check node require path (project-local installs)
    if command -v node &>/dev/null; then
        node -e "require.resolve('@opencode-ai/sdk')" &>/dev/null 2>&1 && return 0
    fi
    return 1
}

# -------------------------------------------------------------------
# Check if promptfoo is already installed
# -------------------------------------------------------------------
if command -v "$PROMPTFOO_CMD" &>/dev/null; then
    echo "promptfoo is already installed: $(command -v "$PROMPTFOO_CMD")"
    "$PROMPTFOO_CMD" --version 2>/dev/null || true
    PROMPTFOO_READY=true
fi

# Also check npx availability (promptfoo can run via npx without global install)
if [[ "$PROMPTFOO_READY" == "false" ]] && command -v npx &>/dev/null && npx promptfoo@latest --version &>/dev/null 2>&1; then
    echo "promptfoo is available via npx"
    PROMPTFOO_READY=true
fi

if check_sdk; then
    echo "@opencode-ai/sdk is already available in current project"
    SDK_READY=true
fi

if [[ "$PROMPTFOO_READY" == "false" ]]; then
    echo "promptfoo not found. Attempting installation..."
fi

# -------------------------------------------------------------------
# Detect platform
# -------------------------------------------------------------------
OS="$(uname -s)"
INSTALLED=false

# -------------------------------------------------------------------
# macOS: try brew first, fallback to npm
# -------------------------------------------------------------------
if [[ "$PROMPTFOO_READY" == "false" ]] && [[ "$OS" == "Darwin" ]]; then
    if command -v brew &>/dev/null; then
        echo "Installing promptfoo via Homebrew..."
        if brew install promptfoo 2>/dev/null; then
            INSTALLED=true
            PROMPTFOO_READY=true
        else
            echo "Homebrew install failed, falling back to npm..."
        fi
    fi
fi

# -------------------------------------------------------------------
# npm fallback (all platforms) — prefer mise over raw npm
# -------------------------------------------------------------------
if [[ "$PROMPTFOO_READY" == "false" ]] && [[ "$INSTALLED" == "false" ]]; then
    if command -v mise &>/dev/null; then
        echo "Installing promptfoo via mise..."
        if mise use -g npm:promptfoo@latest 2>/dev/null; then
            INSTALLED=true
            PROMPTFOO_READY=true
        else
            echo "mise install failed, falling back to npm..."
        fi
    fi
    if [[ "$INSTALLED" == "false" ]]; then
        if command -v npm &>/dev/null; then
            echo "Installing promptfoo via npm (global)..."
            npm install -g promptfoo
            INSTALLED=true
            PROMPTFOO_READY=true
        else
            echo "ERROR: Neither mise, brew, nor npm found. Install Node.js/npm first."
            echo "  macOS:     brew install node"
            echo "  Ubuntu:    sudo apt install nodejs npm"
            echo "  Or visit:  https://nodejs.org/"
            exit 1
        fi
    fi
fi

# -------------------------------------------------------------------
# Ensure OpenCode SDK is installed in current project
# -------------------------------------------------------------------
if [[ "$SDK_READY" == "false" ]]; then
    SDK_INSTALLED=false
    if command -v mise &>/dev/null; then
        echo "Installing @opencode-ai/sdk via mise..."
        if mise use -g npm:@opencode-ai/sdk@latest 2>/dev/null; then
            SDK_INSTALLED=true
        else
            echo "mise install failed, falling back to npm..."
        fi
    fi
    if [[ "$SDK_INSTALLED" == "false" ]]; then
        if command -v npm &>/dev/null; then
            echo "Installing @opencode-ai/sdk via npm (global)..."
            npm install -g @opencode-ai/sdk
        else
            echo "ERROR: Neither mise nor npm found. Cannot install @opencode-ai/sdk."
            exit 1
        fi
    fi
    if check_sdk; then
        SDK_READY=true
    fi
fi

# -------------------------------------------------------------------
# Verify installation
# -------------------------------------------------------------------
if [[ "$PROMPTFOO_READY" == "false" ]] && command -v "$PROMPTFOO_CMD" &>/dev/null; then
    PROMPTFOO_READY=true
fi

if [[ "$PROMPTFOO_READY" == "true" ]] && [[ "$SDK_READY" == "true" ]]; then
    if command -v "$PROMPTFOO_CMD" &>/dev/null; then
        echo "promptfoo ready: $(promptfoo --version)"
    else
        echo "promptfoo ready via npx"
    fi
    echo "@opencode-ai/sdk is installed"
    exit 0
else
    if [[ "$PROMPTFOO_READY" == "false" ]]; then
        echo "ERROR: promptfoo is not available after installation attempt."
    fi
    if [[ "$SDK_READY" == "false" ]]; then
        echo "ERROR: @opencode-ai/sdk is not available after installation attempt."
    fi
    exit 1
fi
