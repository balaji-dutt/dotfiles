#!/usr/bin/env bash
set -Eeuo pipefail

profile="${1:-${OPENCODE_PROFILE:-chatgpt}}"
workspace_root="${2:-}"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
profile_dir="$config_home/opencode/profiles/$profile"

if [[ ! -d "$profile_dir" ]]; then
  exit 0
fi

if [[ "$profile" != "copilot" ]]; then
  printf '%s\n' "$profile_dir"
  exit 0
fi

if [[ -z "$workspace_root" ]] && command -v git >/dev/null 2>&1; then
  workspace_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi

if [[ -z "$workspace_root" ]]; then
  printf '%s\n' "$profile_dir"
  exit 0
fi

workspace_opencode_json=""
if [[ -f "$workspace_root/.opencode/opencode.json" ]]; then
  workspace_opencode_json="$workspace_root/.opencode/opencode.json"
elif [[ -f "$workspace_root/.opencode/opencode.jsonc" ]]; then
  workspace_opencode_json="$workspace_root/.opencode/opencode.jsonc"
fi

if [[ -z "$workspace_opencode_json" ]]; then
  printf '%s\n' "$profile_dir"
  exit 0
fi

base_profile_config="$profile_dir/opencode.jsonc"
if [[ ! -f "$base_profile_config" ]]; then
  printf '%s\n' "$profile_dir"
  exit 0
fi

workspace_hash="$({
  printf '%s' "$workspace_root" | shasum -a 256 2>/dev/null | awk '{print substr($1,1,16)}'
} || true)"

if [[ -z "$workspace_hash" ]]; then
  export WORKSPACE_ROOT="$workspace_root"
  workspace_hash="$(python3 - <<'PY'
import hashlib
import os

workspace = os.environ.get("WORKSPACE_ROOT", "")
print(hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:16])
PY
  )"
fi

if [[ -z "$workspace_hash" ]]; then
  printf '%s\n' "$profile_dir"
  exit 0
fi

runtime_dir="$config_home/opencode/runtime/$profile/$workspace_hash"
mkdir -p "$runtime_dir"

active_profile_config="$runtime_dir/opencode.jsonc"

export OPENCODE_BASE_PROFILE_CONFIG="$base_profile_config"
export OPENCODE_ACTIVE_PROFILE_CONFIG="$active_profile_config"
export OPENCODE_WORKSPACE_CONFIG="$workspace_opencode_json"
export OPENCODE_MODEL_MAP="${OPENCODE_MODEL_MAP:-gpt-5.4=gpt-5.4,gpt-5.3-codex=gpt-5.3-codex,gpt-5.2=gpt-5.2,gpt-5.2-high=gpt-5.2-high,gpt-5.2-xhigh=gpt-5.2-xhigh}"

python_exit=0
python3 <<'PY' || python_exit=$?
import json
import os
import sys
from pathlib import Path


def strip_json_comments(payload: str) -> str:
    out = []
    in_string = False
    string_char = ""
    escape = False
    i = 0
    while i < len(payload):
        ch = payload[i]
        nxt = payload[i + 1] if i + 1 < len(payload) else ""
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == string_char:
                in_string = False
            i += 1
            continue
        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(payload) and payload[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(payload) and not (payload[i] == "*" and payload[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def strip_jsonc_trailing_commas(payload: str) -> str:
    out = []
    in_string = False
    string_char = ""
    escape = False
    i = 0
    while i < len(payload):
        ch = payload[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == string_char:
                in_string = False
            i += 1
            continue

        if ch in ('"', "'"):
            in_string = True
            string_char = ch
            out.append(ch)
            i += 1
            continue

        if ch == ",":
            j = i + 1
            while j < len(payload) and payload[j] in " \t\r\n":
                j += 1
            if j < len(payload) and payload[j] in "]}":
                i += 1
                continue

        out.append(ch)
        i += 1
    return "".join(out)


def parse_jsonc(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    stripped = strip_json_comments(raw)
    return json.loads(strip_jsonc_trailing_commas(stripped))


base_path = Path(os.environ["OPENCODE_BASE_PROFILE_CONFIG"])
active_path = Path(os.environ["OPENCODE_ACTIVE_PROFILE_CONFIG"])
workspace_path = Path(os.environ["OPENCODE_WORKSPACE_CONFIG"])

mapping = {}
for pair in os.environ.get("OPENCODE_MODEL_MAP", "").split(","):
    pair = pair.strip()
    if not pair or "=" not in pair:
        continue
    src, dst = pair.split("=", 1)
    src = src.strip()
    dst = dst.strip()
    if src and dst:
        mapping[src] = dst

try:
    base_cfg = parse_jsonc(base_path)
    workspace_cfg = parse_jsonc(workspace_path)
except Exception as exc:  # noqa: BLE001
    print(f"ERROR: Failed to parse OpenCode JSONC: {exc}", file=sys.stderr)
    raise SystemExit(1)

generated_agent_overrides = {}
for name, cfg in workspace_cfg.get("agent", {}).items():
    if not isinstance(cfg, dict):
        continue
    model = cfg.get("model")
    if not isinstance(model, str) or not model.startswith("openai/"):
        continue
    model_id = model.split("/", 1)[1]
    mapped = mapping.get(model_id)
    if not mapped:
        continue
    generated_agent_overrides[name] = {"model": f"github-copilot/{mapped}"}

if not generated_agent_overrides:
    raise SystemExit(42)

merged_cfg = base_cfg
merged_cfg.setdefault("agent", {}).update(generated_agent_overrides)
active_path.write_text(json.dumps(merged_cfg, indent=2) + "\n", encoding="utf-8")
PY

if [[ "$python_exit" -eq 0 ]]; then
  printf '%s\n' "$runtime_dir"
  exit 0
fi

if [[ "$python_exit" -eq 42 ]]; then
  printf '%s\n' "$profile_dir"
  exit 0
fi

echo "WARN: OpenCode workspace override generation failed; falling back to static profile." >&2
printf '%s\n' "$profile_dir"
