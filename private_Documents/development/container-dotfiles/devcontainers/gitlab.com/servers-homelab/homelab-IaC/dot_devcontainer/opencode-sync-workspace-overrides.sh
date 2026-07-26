#!/usr/bin/env bash
set -Eeuo pipefail

normalize_profile() {
  local name
  name="$1"
  case "$name" in
    ""|chatgpt)
      printf 'defaults\n'
      ;;
    "."|".."|*[!A-Za-z0-9._-]*)
      printf 'ERROR: Invalid OpenCode profile: %s\n' "$name" >&2
      return 1
      ;;
    *)
      printf '%s\n' "$name"
      ;;
  esac
}

resolve_profile_stack() {
  local raw token normalized restore_noglob invalid
  local -a stack=()
  invalid=0

  raw="${1:-${OPENCODE_PROFILES:-${OPENCODE_PROFILE:-defaults}}}"

  case $- in
    *f*) restore_noglob=0 ;;
    *)
      restore_noglob=1
      set -f
      ;;
  esac

  for token in $raw; do
    if ! normalized="$(normalize_profile "$token")"; then
      invalid=1
      break
    fi
    [[ -n "$normalized" ]] || continue
    case " ${stack[*]} " in
      *" $normalized "*) ;;
      *) stack+=("$normalized") ;;
    esac
  done

  if [[ "$restore_noglob" -eq 1 ]]; then
    set +f
  fi

  if [[ "$invalid" -eq 1 ]]; then
    return 1
  fi

  if [[ "${#stack[@]}" -eq 0 ]]; then
    stack=("defaults")
  fi

  printf '%s\n' "${stack[*]}"
}

profile_arg="${1:-${OPENCODE_PROFILES:-${OPENCODE_PROFILE:-defaults}}}"
workspace_root="${2:-}"
config_home="${XDG_CONFIG_HOME:-$HOME/.config}"

if ! profiles_joined="$(resolve_profile_stack "$profile_arg")"; then
  exit 1
fi
read -r -a profiles <<< "$profiles_joined"

if [[ -z "$workspace_root" ]] && command -v git >/dev/null 2>&1; then
  workspace_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi

workspace_opencode_json=""
if [[ -n "$workspace_root" ]]; then
  if [[ -f "$workspace_root/.opencode/opencode.jsonc" ]]; then
    workspace_opencode_json="$workspace_root/.opencode/opencode.jsonc"
  elif [[ -f "$workspace_root/.opencode/opencode.json" ]]; then
    workspace_opencode_json="$workspace_root/.opencode/opencode.json"
  fi
fi

global_opencode_json=""
if [[ -f "$config_home/opencode/opencode.jsonc" ]]; then
  global_opencode_json="$config_home/opencode/opencode.jsonc"
fi

if [[ -f "$config_home/opencode/opencode.json" ]]; then
  if [[ -f "$config_home/opencode/opencode.jsonc" ]]; then
    rm -f "$config_home/opencode/opencode.json"
    echo "WARN: Removed legacy $config_home/opencode/opencode.json; using opencode.jsonc." >&2
  else
    global_opencode_json="$config_home/opencode/opencode.json"
    echo "WARN: Keeping legacy $config_home/opencode/opencode.json because opencode.jsonc is missing." >&2
  fi
fi

defaults_profile_config="$config_home/opencode/profiles/defaults/opencode.jsonc"
if [[ ! -f "$defaults_profile_config" ]]; then
  defaults_profile_config="$config_home/opencode/profiles/${profiles[0]}/opencode.jsonc"
fi

if [[ ! -f "$defaults_profile_config" ]]; then
  printf '%s\n' "$config_home/opencode/profiles/${profiles[0]}"
  exit 0
fi

profiles_key="${profiles_joined// /--}"
profiles_key="${profiles_key//\//_}"
profiles_key="${profiles_key//:/_}"

workspace_scope="${workspace_root:-$PWD}"
workspace_hash="$(
  WORKSPACE_ROOT="$workspace_scope" python3 - <<'PY'
import hashlib
import os

workspace = os.environ.get("WORKSPACE_ROOT", "")
print(hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:16])
PY
)"

runtime_dir="$config_home/opencode/runtime/$profiles_key/$workspace_hash"
mkdir -p "$runtime_dir"

if [[ -f "$runtime_dir/opencode.json" ]]; then
  rm -f "$runtime_dir/opencode.json"
  echo "WARN: Removed legacy $runtime_dir/opencode.json; using opencode.jsonc." >&2
fi

active_profile_config="$runtime_dir/opencode.jsonc"

export OPENCODE_BASE_PROFILE_CONFIG="$defaults_profile_config"
export OPENCODE_ACTIVE_PROFILE_CONFIG="$active_profile_config"
export OPENCODE_WORKSPACE_CONFIG="$workspace_opencode_json"
export OPENCODE_GLOBAL_CONFIG="$global_opencode_json"
export OPENCODE_PROFILES_STACK="$profiles_joined"
export OPENCODE_CONFIG_HOME="$config_home"

python3 <<'PY'
import json
import os
import re
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


def rebase_file_refs(node, source_dir: Path, runtime_dir: Path):
    if isinstance(node, dict):
        return {key: rebase_file_refs(value, source_dir, runtime_dir) for key, value in node.items()}
    if isinstance(node, list):
        return [rebase_file_refs(item, source_dir, runtime_dir) for item in node]
    if isinstance(node, str):
        match = re.fullmatch(r"\{file:([^}]+)\}", node.strip())
        if not match:
            return node
        ref_path = match.group(1).strip()
        if not (ref_path.startswith("./") or ref_path.startswith("../")):
            return node
        rebased_abs = (source_dir / ref_path).resolve(strict=False)
        rebased_rel = os.path.relpath(rebased_abs, runtime_dir)
        return f"{{file:{Path(rebased_rel).as_posix()}}}"
    return node


def parse_jsonc_rebased(path: Path, runtime_dir: Path) -> dict:
    parsed = parse_jsonc(path)
    return rebase_file_refs(parsed, path.parent.resolve(strict=False), runtime_dir)


def deep_merge(dst: dict, src: dict) -> dict:
    for key, value in src.items():
        if key in dst and isinstance(dst[key], dict) and isinstance(value, dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value
    return dst


def remap_anthropic_to_api(agent_cfg: dict) -> None:
    for _name, cfg in agent_cfg.items():
        if not isinstance(cfg, dict):
            continue
        model = cfg.get("model")
        if isinstance(model, str) and model.startswith("anthropic/"):
            cfg["model"] = f"anthropic-api/{model.split('/', 1)[1]}"


def apply_api_fallback(agent_cfg: dict) -> None:
    fallback_map = {
        "moonshot/kimi-k3": "openrouter/moonshotai/kimi-k3",
        "moonshot/kimi-k2.7-code": "openrouter/moonshotai/kimi-k3",
        "moonshot/kimi-k2.6": "openrouter/moonshotai/kimi-k2.6",
        "google/gemini-3.6-flash": "openrouter/z-ai/glm-5.2:exacto",
    }
    for _name, cfg in agent_cfg.items():
        if not isinstance(cfg, dict):
            continue
        model = cfg.get("model")
        if isinstance(model, str) and model in fallback_map:
            cfg["model"] = fallback_map[model]


base_path = Path(os.environ["OPENCODE_BASE_PROFILE_CONFIG"])
active_path = Path(os.environ["OPENCODE_ACTIVE_PROFILE_CONFIG"])
workspace_raw = os.environ.get("OPENCODE_WORKSPACE_CONFIG", "").strip()
global_raw = os.environ.get("OPENCODE_GLOBAL_CONFIG", "").strip()
profiles = [p for p in os.environ.get("OPENCODE_PROFILES_STACK", "defaults").split() if p]
config_home = Path(os.environ["OPENCODE_CONFIG_HOME"])
runtime_dir = active_path.parent.resolve(strict=False)

global_path = Path(global_raw) if global_raw else None
base_source = global_path if global_path and global_path.is_file() else base_path

try:
    merged = parse_jsonc_rebased(base_source, runtime_dir)
except Exception as exc:  # noqa: BLE001
    print(f"ERROR: Failed to parse base OpenCode JSONC: {exc}", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(merged, dict):
    merged = {}

if base_source != base_path and base_path.is_file():
    try:
        base_overlay = parse_jsonc_rebased(base_path, runtime_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Failed to parse defaults profile JSONC: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if isinstance(base_overlay, dict):
        deep_merge(merged, base_overlay)

for profile in profiles:
    profile = "defaults" if profile == "chatgpt" else profile
    if profile == "defaults":
        continue
    profile_cfg = config_home / "opencode" / "profiles" / profile / "opencode.jsonc"
    if profile_cfg.is_file():
        try:
            profile_data = parse_jsonc_rebased(profile_cfg, runtime_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: Failed to parse profile JSONC ({profile}): {exc}", file=sys.stderr)
            raise SystemExit(1)
        if isinstance(profile_data, dict):
            deep_merge(merged, profile_data)

for cfg_path_raw in [workspace_raw]:
    if not cfg_path_raw:
        continue
    cfg_path = Path(cfg_path_raw)
    if not cfg_path.is_file():
        continue
    try:
        cfg = parse_jsonc_rebased(cfg_path, runtime_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: Failed to parse OpenCode JSONC ({cfg_path}): {exc}", file=sys.stderr)
        raise SystemExit(1)
    if not isinstance(cfg, dict):
        continue
    agents = cfg.get("agent", {})
    if not isinstance(agents, dict):
        continue
    merged_agents = merged.setdefault("agent", {})
    if not isinstance(merged_agents, dict):
        merged["agent"] = {}
        merged_agents = merged["agent"]
    for name, agent_cfg in agents.items():
        if not isinstance(agent_cfg, dict):
            continue
        if isinstance(merged_agents.get(name), dict):
            deep_merge(merged_agents[name], agent_cfg)
        else:
            merged_agents[name] = dict(agent_cfg)

agents_cfg = merged.setdefault("agent", {})
if not isinstance(agents_cfg, dict):
    merged["agent"] = {}
    agents_cfg = merged["agent"]

if "anthropic-api" in profiles:
    remap_anthropic_to_api(agents_cfg)

if "api-fallback" in profiles:
    apply_api_fallback(agents_cfg)

active_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
PY

printf '%s\n' "$runtime_dir"
