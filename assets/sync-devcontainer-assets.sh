#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
manifest_path="$repo_root/configs/devcontainer-sync.jsonc"

is_devcontainer_host() {
  local uname_s osrelease os_id
  uname_s="$(uname -s 2>/dev/null || true)"

  if [[ "$uname_s" == "Darwin" ]]; then
    return 0
  fi

  if [[ "$uname_s" == "Linux" ]]; then
    osrelease="$(tr '[:upper:]' '[:lower:]' </proc/sys/kernel/osrelease 2>/dev/null || true)"
    os_id=""
    if [[ -r /etc/os-release ]]; then
      os_id="$(. /etc/os-release && printf '%s' "${ID:-}")"
    fi

    [[ "$osrelease" == *microsoft* && "$osrelease" == *wsl2* && "$os_id" == "debian" ]] && return 0
  fi

  return 1
}

if ! is_devcontainer_host; then
  echo "Devcontainer asset sync skipped: supported only on macOS or Debian WSL2."
  exit 0
fi

python3 - "$repo_root" "$manifest_path" <<'PY'
import fnmatch
import json
import os
import shutil
import sys
from pathlib import Path, PurePosixPath


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


def is_match(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    as_posix = PurePosixPath(path).as_posix()
    return any(fnmatch.fnmatch(as_posix, pattern) for pattern in patterns)


def should_include(path: str, include: list[str], exclude: list[str]) -> bool:
    if not is_match(path, include):
        return False
    if is_match(path, exclude):
        return False
    return True


def iter_candidates(root: Path) -> list[Path]:
    out: list[Path] = []
    if not root.exists():
        return out
    for candidate in root.rglob("*"):
        if candidate.is_file() or candidate.is_symlink():
            out.append(candidate)
    return out


def copy_entry(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        link_target = os.readlink(src)
        if dst.exists() or dst.is_symlink():
            if dst.is_symlink() and os.readlink(dst) == link_target:
                return False
            dst.unlink()
        os.symlink(link_target, dst)
        return True

    if dst.is_symlink():
        dst.unlink()

    before = dst.read_bytes() if dst.exists() else None
    payload = src.read_bytes()
    if before == payload:
        return False
    shutil.copy2(src, dst)
    return True


def remove_stale(target_root: Path, managed_paths: set[str], include: list[str], exclude: list[str]) -> int:
    removed = 0
    if not target_root.exists():
        return removed
    for candidate in iter_candidates(target_root):
        rel = candidate.relative_to(target_root).as_posix()
        if not should_include(rel, include, exclude):
            continue
        if rel in managed_paths:
            continue
        if candidate.is_dir():
            continue
        candidate.unlink()
        removed += 1
    return removed


def sync_mirror(repo_root: Path, spec: dict) -> tuple[int, int]:
    include = spec.get("include", [])
    exclude = spec.get("exclude", [])
    source_root = repo_root / spec["source_root"]
    target_root = repo_root / spec["target_root"]
    cleanup_managed = bool(spec.get("cleanup_managed", False))

    if not source_root.is_dir():
        raise SystemExit(f"Missing source root: {source_root}")

    changed = 0
    managed_paths: set[str] = set()

    for src in iter_candidates(source_root):
        rel = src.relative_to(source_root).as_posix()
        if not should_include(rel, include, exclude):
            continue
        managed_paths.add(rel)
        dst = target_root / rel
        if copy_entry(src, dst):
            changed += 1

    removed = 0
    if cleanup_managed:
        removed = remove_stale(target_root, managed_paths, include, exclude)

    return changed, removed


def main() -> int:
    repo_root = Path(sys.argv[1])
    manifest_path = Path(sys.argv[2])
    manifest = parse_jsonc(manifest_path)

    mirrors = manifest.get("shared", {}).get("mirrors", [])
    if not mirrors:
        print("No mirror specs found; nothing to do.")
        return 0

    total_changed = 0
    total_removed = 0

    for spec in mirrors:
        name = spec.get("name", "unnamed")
        changed, removed = sync_mirror(repo_root, spec)
        total_changed += changed
        total_removed += removed
        print(f"{name}: synced {changed} file(s), removed {removed} stale file(s)")

    if total_changed == 0 and total_removed == 0:
        print("Devcontainer asset sync complete: no changes.")
    else:
        print(
            "Devcontainer asset sync complete: "
            f"{total_changed} file(s) updated, {total_removed} stale file(s) removed."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
