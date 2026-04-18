#!/usr/bin/env bash
set -Eeuo pipefail

profile="${1:-${OPENCODE_PROFILE:-chatgpt}}"
workspace_root="${2:-}"

if [[ -z "$workspace_root" ]]; then
  if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    workspace_root="$git_root"
  else
    workspace_root="$(pwd)"
  fi
fi

config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
profile_dir="$config_home/opencode/profiles/$profile"

if [[ ! -d "$profile_dir" ]]; then
  exit 0
fi

if [[ "$profile" != "copilot" ]]; then
  exit 0
fi

active_profile_config="$profile_dir/opencode.jsonc"
base_profile_config="$profile_dir/opencode.base.jsonc"

if [[ ! -f "$base_profile_config" && -f "$active_profile_config" ]]; then
  cp -f "$active_profile_config" "$base_profile_config"
fi

if [[ ! -f "$base_profile_config" ]]; then
  exit 0
fi

workspace_opencode_json=""
if [[ -f "$workspace_root/.opencode/opencode.json" ]]; then
  workspace_opencode_json="$workspace_root/.opencode/opencode.json"
elif [[ -f "$workspace_root/.opencode/opencode.jsonc" ]]; then
  workspace_opencode_json="$workspace_root/.opencode/opencode.jsonc"
fi

if [[ -z "$workspace_opencode_json" ]]; then
  cp -f "$base_profile_config" "$active_profile_config"
  exit 0
fi

export OPENCODE_BASE_PROFILE_CONFIG="$base_profile_config"
export OPENCODE_ACTIVE_PROFILE_CONFIG="$active_profile_config"
export OPENCODE_WORKSPACE_CONFIG="$workspace_opencode_json"
export OPENCODE_MODEL_MAP="${OPENCODE_MODEL_MAP:-gpt-5.4=gpt-5.4,gpt-5.3-codex=gpt-5.3-codex,gpt-5.2=gpt-5.2,gpt-5.2-high=gpt-5.2-high,gpt-5.2-xhigh=gpt-5.2-xhigh}"

python3 <<'PY'
import json
import os
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


def parse_jsonc(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    return json.loads(strip_json_comments(raw))


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

base_cfg = parse_jsonc(base_path)
workspace_cfg = parse_jsonc(workspace_path)

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

merged_cfg = base_cfg
if generated_agent_overrides:
    merged_cfg.setdefault("agent", {}).update(generated_agent_overrides)

active_path.write_text(json.dumps(merged_cfg, indent=2) + "\n", encoding="utf-8")
PY
