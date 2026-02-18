#!/bin/bash

# 1. OS-Aware Sed Function
# macOS requires 'sed -i ""', while Linux requires 'sed -i'
safe_sed() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i "" "$@"
    else
        sed -i "$@"
    fi
}

insert_stamp() {
    local file="$1"
    local header="$2"
    local label="$3"
    local now="$4"

    local tmp
    tmp="$(mktemp -t commit-docs.XXXXXX)" || return 1

    if ! HEADER="$header" LABEL="$label" NOW="$now" awk '
        BEGIN {
            header = ENVIRON["HEADER"]
            label = ENVIRON["LABEL"]
            now = ENVIRON["NOW"]
            in_task = 0
            fence_count = 0
            done = 0
        }
        {
            if (!done && $0 == header) {
                in_task = 1
                fence_count = 0
            }

            if (!done && in_task && $0 == "  ```") {
                fence_count++
                if (fence_count == 2) {
                    print "  " label ": " now
                    print $0
                    done = 1
                    in_task = 0
                    next
                }
            }

            print $0
        }
    ' "$file" > "$tmp"; then
        echo "ERROR: awk failed; temp preserved at: $tmp" >&2
        return 1
    fi

    if cat "$tmp" > "$file"; then
        rm -f "$tmp"
    else
        echo "ERROR: write-back failed; temp preserved at: $tmp" >&2
        return 1
    fi
}

# 2. Git Awareness
GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
IS_GIT=true
if [ -z "$GIT_ROOT" ]; then
    GIT_ROOT="."
    IS_GIT=false
fi

ACTION=$1
INPUT_TEXT=$2
AUTHOR="OpenCode <noreply@opencode.ai>"
NOW=$(date '+%Y-%m-%d %H:%M')
TODO_FILE="$GIT_ROOT/TODO.md"
TARGET=""
FULL_MSG=""

# 3. Escape INPUT_TEXT for use in Sed
# - ESCAPED_TEXT is used in the *pattern* (regex) side.
# - ESCAPED_REPLACEMENT is used in the *replacement* side.
#
# NOTE: If INPUT_TEXT contains '/', it must be escaped in replacements or the
# sed command breaks (e.g. "bad flag in substitute command").
ESCAPED_TEXT=$(printf '%s' "$INPUT_TEXT" | sed 's/[][\\.^$*\/]/\\&/g')
ESCAPED_REPLACEMENT=$(printf '%s' "$INPUT_TEXT" | sed 's/[\\/&]/\\&/g')

# 4. Initialize TODO.md if it doesn't exist
if [ ! -f "$TODO_FILE" ] && [[ "$ACTION" != "readme" ]]; then
    cat <<EOF > "$TODO_FILE"
<!-- markdownlint-disable MD007 MD022 MD023 MD029 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "options": {
    "frontMatter": "(^---\\\\s*\$[^]*?^---\\\\s*\$)(\\\\r\\\\n|\\\\r|\\\\n|\$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# TO-DO LIST

EOF
fi

# 5. Logic Engine
case $ACTION in
    "add")
        printf -- "- [ ] %s\n  \`\`\`\n  Added: %s\n  \`\`\`\n" "$INPUT_TEXT" "$NOW" >> "$TODO_FILE"
        FULL_MSG="docs(todo): task added"
        TARGET="TODO.md"
        ;;
    "pause")
        if grep -F -q -- "- [ ] $INPUT_TEXT" "$TODO_FILE"; then
            safe_sed "s/- \[ \] $ESCAPED_TEXT/- [ ] [PAUSED] $ESCAPED_REPLACEMENT/" "$TODO_FILE"
            insert_stamp "$TODO_FILE" "- [ ] [PAUSED] $INPUT_TEXT" "Paused" "$NOW" || exit 1
        fi
        FULL_MSG="docs(todo): task paused"
        TARGET="TODO.md"
        ;;
    "resume")
        if grep -F -q -- "- [ ] [PAUSED] $INPUT_TEXT" "$TODO_FILE"; then
            safe_sed "s/- \[ \] \[PAUSED\] $ESCAPED_TEXT/- [ ] $ESCAPED_REPLACEMENT/" "$TODO_FILE"
            insert_stamp "$TODO_FILE" "- [ ] $INPUT_TEXT" "Resumed" "$NOW" || exit 1
        fi
        FULL_MSG="docs(todo): task resumed"
        TARGET="TODO.md"
        ;;
    "complete")
        if grep -F -q -- "- [ ] $INPUT_TEXT" "$TODO_FILE" || grep -F -q -- "- [ ] [PAUSED] $INPUT_TEXT" "$TODO_FILE"; then
            safe_sed "s/- \[ \] .*$ESCAPED_TEXT/- [x] $ESCAPED_REPLACEMENT/" "$TODO_FILE"
            insert_stamp "$TODO_FILE" "- [x] $INPUT_TEXT" "Completed" "$NOW" || exit 1
        fi
        FULL_MSG="docs(todo): task completed"
        TARGET="TODO.md"
        ;;
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
esac

# 6. Execution (Git Commit)
if [ "$IS_GIT" = true ] && [ -f "$GIT_ROOT/$TARGET" ]; then
    git add "$GIT_ROOT/$TARGET"
    git commit -m "$FULL_MSG" --author="$AUTHOR"
fi
