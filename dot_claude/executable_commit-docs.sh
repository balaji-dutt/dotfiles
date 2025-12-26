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

# 2. Git Awareness
GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
IS_GIT=true
if [ -z "$GIT_ROOT" ]; then
    GIT_ROOT="."
    IS_GIT=false
fi

ACTION=$1
INPUT_TEXT=$2
AUTHOR="Claude <claude@anthropic.com>"
NOW=$(date '+%Y-%m-%d %H:%M')
TODO_FILE="$GIT_ROOT/TODO.md"
TARGET=""
FULL_MSG=""

# 3. Escape INPUT_TEXT for use in Sed Regex
# This escapes characters that sed treats as special: \ . [ ] ^ $ * /
ESCAPED_TEXT=$(echo "$INPUT_TEXT" | sed 's/[^^]/[&]/g; s/\^/\\^/g; s/\//\\\//g')

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
        safe_sed "s/- \[ \] $ESCAPED_TEXT/- [ ] [PAUSED] $INPUT_TEXT/" "$TODO_FILE"
        safe_sed "/- \[ \] \[PAUSED\] $ESCAPED_TEXT/,/\`\`\`/ s/\`\`\//  Paused: $NOW\n  \`\`\// " "$TODO_FILE"
        FULL_MSG="docs(todo): task paused"
        TARGET="TODO.md"
        ;;
    "resume")
        safe_sed "s/- \[ \] \[PAUSED\] $ESCAPED_TEXT/- [ ] $INPUT_TEXT/" "$TODO_FILE"
        safe_sed "/- \[ \] $ESCAPED_TEXT/,/\`\`\`/ s/\`\`\//  Resumed: $NOW\n  \`\`\// " "$TODO_FILE"
        FULL_MSG="docs(todo): task resumed"
        TARGET="TODO.md"
        ;;
    "complete")
        safe_sed "s/- \[ \] .*$ESCAPED_TEXT/- [x] $INPUT_TEXT/" "$TODO_FILE"
        safe_sed "/- \[x\] $ESCAPED_TEXT/,/\`\`\`/ s/\`\`\//  Completed: $NOW\n  \`\`\// " "$TODO_FILE"
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
