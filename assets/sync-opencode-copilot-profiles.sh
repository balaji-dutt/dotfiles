#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
manifest_path="$repo_root/configs/devcontainer-sync.jsonc"

python3 - "$repo_root" "$manifest_path" <<'PY'
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


def map_model(model: str, mapping: dict[str, str]) -> str | None:
    if not isinstance(model, str) or not model.startswith("openai/"):
        return None
    model_id = model.split("/", 1)[1]
    mapped = mapping.get(model_id)
    if not mapped:
        return None
    return f"github-copilot/{mapped}"


def parse_mapping() -> dict[str, str]:
    payload = os.environ.get(
        "OPENCODE_MODEL_MAP",
        "gpt-5.4=gpt-5.4,gpt-5.3-codex=gpt-5.3-codex,gpt-5.2=gpt-5.2,gpt-5.2-high=gpt-5.2-high,gpt-5.2-xhigh=gpt-5.2-xhigh",
    )
    mapping: dict[str, str] = {}
    for pair in payload.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        src, dst = pair.split("=", 1)
        src = src.strip()
        dst = dst.strip()
        if src and dst:
            mapping[src] = dst
    return mapping


def write_if_changed(path: Path, payload: str) -> bool:
    before = path.read_text(encoding="utf-8") if path.exists() else None
    if before == payload:
        return False
    path.write_text(payload, encoding="utf-8")
    return True


def sync_profile(source_path: Path, target_path: Path, keep_agents: list[str], mapping: dict[str, str]) -> bool:
    source_cfg = parse_jsonc(source_path)
    target_cfg = parse_jsonc(target_path)

    out_cfg = {k: v for k, v in target_cfg.items() if k != "agent"}

    mapped_default = map_model(source_cfg.get("model"), mapping)
    if mapped_default:
        out_cfg["model"] = mapped_default

    target_agents = target_cfg.get("agent", {})
    kept_agents = {}
    if isinstance(target_agents, dict):
        for name in keep_agents:
            if name in target_agents and isinstance(target_agents[name], dict):
                kept_agents[name] = target_agents[name]

    generated_agents = {}
    source_agents = source_cfg.get("agent", {})
    if isinstance(source_agents, dict):
        for name, cfg in source_agents.items():
            if not isinstance(cfg, dict):
                continue
            mapped_model = map_model(cfg.get("model"), mapping)
            if not mapped_model:
                continue
            generated_agents[name] = {"model": mapped_model}

    merged_agents = {}
    merged_agents.update(kept_agents)
    merged_agents.update(generated_agents)
    out_cfg["agent"] = merged_agents

    rendered = json.dumps(out_cfg, indent=2) + "\n"
    return write_if_changed(target_path, rendered)


repo_root = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
mapping = parse_mapping()

manifest = parse_jsonc(manifest_path)
specs = [
    spec
    for spec in manifest.get("shared", {}).get("derived", [])
    if spec.get("type") == "opencode-copilot-profile"
]

if not specs:
    raise SystemExit(
        "No opencode-copilot-profile derived entries found in configs/devcontainer-sync.jsonc"
    )

changed = False
for spec in specs:
    source = repo_root / spec["source"]
    target = repo_root / spec["target"]
    if not source.is_file() or not target.is_file():
        raise SystemExit(f"Missing required file for {spec['label']}: {source} or {target}")
    did_change = sync_profile(source, target, spec["keep_agents"], mapping)
    state = "updated" if did_change else "up-to-date"
    print(f"{spec['label']}: {state} -> {spec['target']}")
    changed = changed or did_change

if changed:
    print("OpenCode Copilot profile sync complete: changes written.")
else:
    print("OpenCode Copilot profile sync complete: no changes.")
PY
