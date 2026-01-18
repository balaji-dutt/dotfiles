#!/usr/bin/env bash
set -euo pipefail

# Prefer CWD provided by oh-my-opencode hook runner.
PROJECT_DIR="${CWD:-${OPENCODE_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-}}}"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

# If PROJECT_DIR is a Windows path, convert for Git Bash/MSYS
if [[ "$PROJECT_DIR" =~ ^[A-Za-z]:\\ ]] && command -v cygpath >/dev/null 2>&1; then
  PROJECT_DIR="$(cygpath -u "$PROJECT_DIR")"
fi

cd "$PROJECT_DIR"

SENTINEL=".opencode/.needs_dotfiles_review"
mkdir -p "$(dirname "$SENTINEL")"

# epoch seconds; cross-platform (macOS/WSL/Git Bash)
date -u +%s > "$SENTINEL"
