#!/usr/bin/env bash

# Shared helpers for the Homelab IaC devcontainer lifecycle scripts.
# Keep this file side-effect free: define functions only.

load_opencode_env_file() {
  local env_file restore_allexport
  env_file="${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.env"

  case $- in
    *a*)
      set +a
      restore_allexport=1
      ;;
    *)
      restore_allexport=0
      ;;
  esac

  if [[ -r "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$env_file"
  fi

  if [[ "$restore_allexport" -eq 0 ]]; then
    set +a
  else
    set -a
  fi
}

ensure_agent_of_empires_persistence_link() {
  local aoe_persist_dir aoe_config_dir
  aoe_persist_dir="/home/vscode/persistent-data/agent-of-empires"
  aoe_config_dir="$HOME/.config/agent-of-empires"

  mkdir -p "$aoe_persist_dir" "$HOME/.config"

  if [[ -L "$aoe_config_dir" ]]; then
    ln -sfn "$aoe_persist_dir" "$aoe_config_dir"
    return 0
  fi

  if [[ -d "$aoe_config_dir" ]]; then
    if ! cp -a "$aoe_config_dir"/. "$aoe_persist_dir"/; then
      echo "ERROR: Failed migrating existing AoE config directory to persistent storage." >&2
      return 1
    fi
    rm -rf "$aoe_config_dir"
  elif [[ -e "$aoe_config_dir" ]]; then
    rm -f "$aoe_config_dir"
  fi

  ln -sfn "$aoe_persist_dir" "$aoe_config_dir"
}

migrate_directory_to_persistent_target() {
  local source_dir target_dir skip_name target_has_content item item_name
  source_dir="$1"
  target_dir="$2"
  skip_name="$3"
  target_has_content=0

  if [[ -n "$(find "$target_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    target_has_content=1
  fi

  while IFS= read -r -d '' item; do
    item_name="$(basename "$item")"
    if [[ "$item_name" == "$skip_name" && -L "$item" && "$(readlink "$item")" == "$target_dir" ]]; then
      echo "Skipping stale nested OpenCode symlink artifact: $item"
      continue
    fi

    if [[ "$target_has_content" -eq 1 ]]; then
      cp -an "$item" "$target_dir"/
    else
      cp -a "$item" "$target_dir"/
    fi
  done < <(find "$source_dir" -mindepth 1 -maxdepth 1 -print0)
}

ensure_opencode_persistence_link() {
  local target_dir link_path skip_name
  target_dir="$1"
  link_path="$2"
  skip_name="$(basename "$target_dir")"

  mkdir -p "$target_dir" "$(dirname "$link_path")"

  if [[ -L "$link_path" ]]; then
    ln -sfn "$target_dir" "$link_path"
    return 0
  fi

  if [[ -d "$link_path" ]]; then
    migrate_directory_to_persistent_target "$link_path" "$target_dir" "$skip_name"
    rm -rf "$link_path"
  elif [[ -e "$link_path" ]]; then
    rm -rf "$link_path"
  fi

  ln -sfn "$target_dir" "$link_path"
}

ensure_opencode_persistence_links() {
  ensure_opencode_persistence_link /home/vscode/persistent-data/opencode/config "$HOME/.config/opencode"
  ensure_opencode_persistence_link /home/vscode/persistent-data/opencode/cache "$HOME/.cache/opencode"
  ensure_opencode_persistence_link /home/vscode/persistent-data/opencode/share "$HOME/.local/share/opencode"
  ensure_opencode_persistence_link /home/vscode/persistent-data/opencode/state "$HOME/.local/state/opencode"
}

ensure_claude_persistence_links() {
  local claude_persist_dir claude_config_persist_dir
  local claude_config_dir claude_home_config claude_home_config_persist

  claude_persist_dir="/home/vscode/persistent-data/claude"
  claude_config_persist_dir="$claude_persist_dir/config"
  claude_config_dir="$HOME/.claude"
  claude_home_config="$HOME/.claude.json"
  claude_home_config_persist="$claude_persist_dir/.claude.json"

  mkdir -p "$claude_config_persist_dir"

  if [[ -L "$claude_config_dir" ]]; then
    ln -sfn "$claude_config_persist_dir" "$claude_config_dir"
  else
    if [[ -d "$claude_config_dir" ]]; then
      if [[ -n "$(find "$claude_config_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
        if [[ -n "$(find "$claude_config_persist_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
          cp -an "$claude_config_dir"/. "$claude_config_persist_dir"/
        else
          cp -a "$claude_config_dir"/. "$claude_config_persist_dir"/
        fi
      fi
      rm -rf "$claude_config_dir"
    elif [[ -e "$claude_config_dir" ]]; then
      rm -f "$claude_config_dir"
    fi

    ln -sfn "$claude_config_persist_dir" "$claude_config_dir"
  fi

  if [[ -L "$claude_home_config" ]]; then
    ln -sfn "$claude_home_config_persist" "$claude_home_config"
  else
    if [[ -f "$claude_home_config" ]]; then
      if [[ ! -e "$claude_home_config_persist" ]] || \
        ! cmp -s "$claude_home_config" "$claude_home_config_persist"; then
        cp -a "$claude_home_config" "$claude_home_config_persist"
      fi
      rm -f "$claude_home_config"
    elif [[ -e "$claude_home_config" ]]; then
      rm -rf "$claude_home_config"
    fi

    ln -sfn "$claude_home_config_persist" "$claude_home_config"
  fi
}

claude_managed_source_dir() {
  if [[ -d /tmp/host-claude ]]; then
    printf '%s\n' /tmp/host-claude
    return 0
  fi

  if [[ -d /home/vscode/.host-dotfiles/dot_claude ]]; then
    printf '%s\n' /home/vscode/.host-dotfiles/dot_claude
    return 0
  fi

  return 1
}

link_claude_managed_path() {
  local src dst backup_root backup_run_dir backup_path backup_parent
  src="$1"
  dst="$2"

  if [[ ! -e "$src" && ! -L "$src" ]]; then
    if [[ -L "$dst" ]]; then
      rm -f "$dst"
    fi
    return 0
  fi

  if [[ -L "$dst" ]]; then
    ln -sfn "$src" "$dst"
    return 0
  fi

  if [[ -e "$dst" ]]; then
    backup_root="${CLAUDE_MANAGED_BACKUP_ROOT:-/home/vscode/persistent-data/claude/unmanaged-managed-path-backups}"
    backup_run_dir="$backup_root/$(date -u +%Y%m%dT%H%M%SZ)-$$"
    backup_path="$backup_run_dir/${dst#/}"
    backup_parent="${backup_path%/*}"

    mkdir -p "$backup_parent"
    mv "$dst" "$backup_path"
    echo "WARN: Moved existing non-symlink Claude managed path aside: $dst" >&2
    echo "WARN: Backup location: $backup_path" >&2
  fi

  ln -s "$src" "$dst"
}

install_claude_managed_asset_links() {
  local source_dir claude_config_dir managed_name source_path target_path

  if ! source_dir="$(claude_managed_source_dir)"; then
    echo "WARN: Claude managed source not found; keeping existing Claude config." >&2
    return 0
  fi

  claude_config_dir="$HOME/.claude"
  mkdir -p "$claude_config_dir"

  link_claude_managed_path "$source_dir/private_settings.json" "$claude_config_dir/settings.json"
  link_claude_managed_path "$source_dir/AGENTS.md" "$claude_config_dir/AGENTS.md"
  link_claude_managed_path "$source_dir/AGENTS.md" "$claude_config_dir/CLAUDE.md"
  link_claude_managed_path "$source_dir/executable_statusline.sh" "$claude_config_dir/statusline.sh"

  for managed_name in agents hooks commands; do
    source_path="$source_dir/$managed_name"
    target_path="$claude_config_dir/$managed_name"
    if [[ -d "$source_path" ]] && \
      [[ -n "$(find "$source_path" -mindepth 1 -print -quit)" ]]; then
      link_claude_managed_path "$source_path" "$target_path"
    elif [[ -L "$target_path" ]]; then
      rm -f "$target_path"
    fi
  done

  if [[ -d "$claude_config_dir/commands" && ! -L "$claude_config_dir/commands" ]]; then
    rm -f "$claude_config_dir/commands/todo.md"
  fi
  rm -f "$claude_config_dir/commit-docs.sh"
}

find_vscode_cli() {
  local candidate root nullglob_state

  nullglob_state="$(shopt -p nullglob || true)"
  shopt -s nullglob

  for root in \
    "${VSCODE_AGENT_FOLDER:-}" \
    "$HOME/.vscode-server" \
    "$HOME/.vscode-server-insiders"; do
    [[ -n "$root" ]] || continue

    for candidate in \
      "$root"/bin/*/bin/code-server \
      "$root"/bin/*/bin/code-insiders-server; do
      if [[ -x "$candidate" ]]; then
        eval "$nullglob_state"
        printf '%s\n' "$candidate"
        return 0
      fi
    done
  done
  eval "$nullglob_state"

  if command -v code >/dev/null 2>&1; then
    command -v code
    return 0
  fi

  if command -v code-insiders >/dev/null 2>&1; then
    command -v code-insiders
    return 0
  fi

  return 1
}

install_beads_kanban_bd_fixes_vscode_extension() {
  local repo tag asset expected_sha fork_extension_id fork_version upstream_extension_id
  local code_cmd cache_base cache_root vsix_path marker_path download_url
  local current_sha tmp_file list_output install_output

  repo="balaji-dutt/Beads-Kanban"
  tag="bd-fixes-v2.1.4-bd.1-13ed786"
  asset="beads-kanban-bd-fixes-2.1.4-bd.1-integration-bd-fixes-13ed786.vsix"
  expected_sha="7731e1b0711437fabb93f56a544d53e1cacd8ffcf8b412a568d6421b84af6e79"
  fork_extension_id="balaji-dutt.beads-kanban-bd-fixes"
  fork_version="2.1.4-bd.1"
  upstream_extension_id="davidcforbes.beads-kanban"

  if ! code_cmd="$(find_vscode_cli)"; then
    echo "INFO: VS Code CLI not found; skipping Beads Kanban VSIX install."
    return 0
  fi
  echo "Using VS Code CLI for Beads Kanban install: $code_cmd"

  if ! command -v curl >/dev/null 2>&1; then
    echo "INFO: curl command not found; skipping Beads Kanban VSIX install."
    return 0
  fi

  if ! command -v sha256sum >/dev/null 2>&1; then
    echo "WARN: sha256sum command not found; skipping Beads Kanban VSIX install." >&2
    return 0
  fi

  if [[ -d /home/vscode/persistent-data ]]; then
    cache_base="/home/vscode/persistent-data"
  else
    cache_base="${XDG_CACHE_HOME:-$HOME/.cache}"
  fi

  cache_root="$cache_base/dotfiles/beads-kanban-vsix"
  vsix_path="$cache_root/$asset"
  marker_path="$cache_root/$tag.installed"
  download_url="https://github.com/${repo}/releases/download/${tag}/${asset}"
  mkdir -p "$cache_root"

  "$code_cmd" --uninstall-extension "$upstream_extension_id" >/dev/null 2>&1 || true

  if [[ -f "$marker_path" ]] && [[ "$(<"$marker_path")" == "$expected_sha" ]]; then
    if list_output="$("$code_cmd" --list-extensions --show-versions 2>&1)" && \
      grep -Fxq "${fork_extension_id}@${fork_version}" <<<"$list_output"; then
      echo "Beads Kanban BD Fixes VSIX already installed: ${fork_extension_id}@${fork_version}"
      return 0
    fi
  fi

  if [[ -f "$vsix_path" ]]; then
    current_sha="$(sha256sum "$vsix_path" | awk '{print $1}')"
  else
    current_sha=""
  fi

  if [[ "$current_sha" != "$expected_sha" ]]; then
    tmp_file="$(mktemp "$cache_root/${asset}.XXXXXX")"
    if ! curl -fL --retry 3 --retry-delay 2 -o "$tmp_file" "$download_url"; then
      rm -f "$tmp_file"
      echo "WARN: Failed downloading Beads Kanban VSIX: $download_url" >&2
      return 1
    fi

    current_sha="$(sha256sum "$tmp_file" | awk '{print $1}')"
    if [[ "$current_sha" != "$expected_sha" ]]; then
      rm -f "$tmp_file"
      echo "ERROR: Beads Kanban VSIX checksum mismatch." >&2
      echo "ERROR: expected $expected_sha" >&2
      echo "ERROR: actual   $current_sha" >&2
      return 1
    fi

    mv -f "$tmp_file" "$vsix_path"
  fi

  echo "Installing Beads Kanban BD Fixes VSIX: ${fork_extension_id}@${fork_version}"
  if ! install_output="$("$code_cmd" --install-extension "$vsix_path" --force 2>&1)"; then
    printf '%s\n' "$install_output" >&2
    echo "ERROR: Failed installing Beads Kanban BD Fixes with: $code_cmd" >&2
    return 1
  fi

  if [[ -n "$install_output" ]]; then
    printf '%s\n' "$install_output"
  fi

  if ! list_output="$("$code_cmd" --list-extensions --show-versions 2>&1)"; then
    printf '%s\n' "$list_output" >&2
    echo "ERROR: Failed listing VS Code extensions with: $code_cmd" >&2
    return 1
  fi

  if ! grep -Fxq "${fork_extension_id}@${fork_version}" <<<"$list_output"; then
    echo "ERROR: Beads Kanban BD Fixes extension was not listed after install." >&2
    echo "ERROR: VS Code CLI used: $code_cmd" >&2
    if grep -i 'beads-kanban' <<<"$list_output" >&2; then
      :
    else
      echo "INFO: No Beads Kanban extensions were listed by VS Code CLI." >&2
    fi
    return 1
  fi

  printf '%s\n' "$expected_sha" > "$marker_path"
  echo "Installed Beads Kanban BD Fixes VSIX from $tag"
}
