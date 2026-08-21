#!/usr/bin/env bash
# install-promptfoo.sh — managed Promptfoo runtime verifier
#
# The compatibility filename is retained for existing skill consumers. This
# script validates a lockfile-managed package root; it does not install packages.

set -euo pipefail

if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: node is required to validate the Promptfoo runtime." >&2
    exit 1
fi

candidates=()
if [[ $# -gt 0 && -n "$1" ]]; then
    candidates+=("$1")
fi
if [[ -n "${PROMPTFOO_RUNTIME_DIR:-}" ]]; then
    candidates+=("$PROMPTFOO_RUNTIME_DIR")
fi
candidates+=("$PWD" "$HOME/.local/share/promptfoo-runtime")

validate_runtime() {
    local runtime_dir="$1"
    local promptfoo_bin="$runtime_dir/node_modules/.bin/promptfoo"

    [[ -f "$runtime_dir/package.json" && -x "$promptfoo_bin" ]] || return 1

    (
        cd "$runtime_dir"
        node --input-type=module <<'NODE'
for (const packageName of [
  'promptfoo',
  '@opencode-ai/sdk',
  '@anthropic-ai/claude-agent-sdk',
  '@anthropic-ai/sdk',
]) {
  import.meta.resolve(packageName);
}
NODE
    ) || return 1

    "$promptfoo_bin" --version >/dev/null || return 1
    printf '%s\n' "$runtime_dir"
}

for candidate in "${candidates[@]}"; do
    [[ -n "$candidate" ]] || continue
    if runtime_dir="$(validate_runtime "$candidate" 2>/dev/null)"; then
        echo "Promptfoo runtime ready: $runtime_dir"
        "$runtime_dir/node_modules/.bin/promptfoo" --version
        echo "Provider SDKs resolve from the same package root."
        exit 0
    fi
done

cat >&2 <<'EOF'
ERROR: no complete managed Promptfoo runtime was found.
Expected promptfoo, @opencode-ai/sdk, @anthropic-ai/claude-agent-sdk, and
@anthropic-ai/sdk in one node_modules tree. Install the project's lockfile or
set PROMPTFOO_RUNTIME_DIR to its package root, then run this verifier again.
EOF
exit 1
