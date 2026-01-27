#!/bin/bash

GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
IS_GIT=true
if [ -z "$GIT_ROOT" ]; then
    GIT_ROOT="."
    IS_GIT=false
fi

ACTION=$1
INPUT_TEXT=$2
AUTHOR="OpenCode <noreply@opencode.ai>"
TARGET=""
FULL_MSG=""

case $ACTION in
    "readme")
        TARGET="README.md"
        FIRST_LINE=$(echo "$INPUT_TEXT" | head -n 1)
        CLEAN_CONTENT=$(echo "$FIRST_LINE" | sed 's/^docs: //' | cut -c 1-44)
        SUBJ="docs: $CLEAN_CONTENT"
        BODY=$(echo "$INPUT_TEXT" | tail -n +2 | sed '/./,$!d' | fmt -w 72)
        if [ -n "$BODY" ]; then
            FULL_MSG=$(printf "%s\n\n%s" "$SUBJ" "$BODY")
        else
            FULL_MSG="$SUBJ"
        fi
        ;;
    *)
        echo "Usage: $0 readme \"message\"" >&2
        exit 1
        ;;
esac

if [ "$IS_GIT" = true ] && [ -f "$GIT_ROOT/$TARGET" ]; then
    git add "$GIT_ROOT/$TARGET"
    git commit -m "$FULL_MSG" --author="$AUTHOR"
fi
