#!/bin/bash
# Get the git root
GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)

if [ -n "$GIT_ROOT" ]; then
    STATUS=$1
    MESSAGE="docs(todo): update list" # Fallback

    # Map the action to a clean commit message
    case $STATUS in
        "add")      MESSAGE="docs(todo): task added" ;;
        "pause")    MESSAGE="docs(todo): task paused" ;;
        "resume")   MESSAGE="docs(todo): task resumed" ;;
        "complete") MESSAGE="docs(todo): task completed" ;;
    esac

    git add "$GIT_ROOT/TODO.md"
    # Force Claude as the primary author for this commit
    git commit -m "$MESSAGE" --author="Claude <claude@anthropic.com>"
fi
