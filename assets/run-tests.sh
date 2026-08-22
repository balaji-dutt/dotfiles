#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

if command -v python3 >/dev/null 2>&1; then
  python_command=(python3)
elif command -v python >/dev/null 2>&1 \
  && python -c 'import sys; raise SystemExit(sys.version_info[0] != 3)' >/dev/null 2>&1; then
  python_command=(python)
else
  printf '%s\n' 'ERROR: Python 3 was not found' >&2
  exit 2
fi

exec "${python_command[@]}" "$repo_root/assets/run-tests.py" "$@"
