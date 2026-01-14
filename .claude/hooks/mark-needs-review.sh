#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-}"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

cd "$PROJECT_DIR"
SENTINEL=".claude/.needs_dotfiles_review"
mkdir -p "$(dirname "$SENTINEL")"

# epoch seconds; cross-platform (works in macOS/WSL/Git Bash)
date -u +%s > "$SENTINEL"