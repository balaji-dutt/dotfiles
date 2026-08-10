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

# AoE 1.13.2 reads first-run state from this sibling TOML file. Seed only a
# missing file; AoE owns it after creation and existing state must survive.
initialize_agent_of_empires_state() {
  local aoe_persist_dir state_file
  aoe_persist_dir="$1"
  state_file="$aoe_persist_dir/state.toml"

  if [[ -e "$state_file" || -L "$state_file" ]]; then
    return 0
  fi

  if ! (
    set -o noclobber
    umask 077
    printf '%s\n' 'has_seen_welcome = true' >"$state_file"
  ) 2>/dev/null; then
    if [[ -e "$state_file" || -L "$state_file" ]]; then
      return 0
    fi
    echo "ERROR: Failed initializing Agent of Empires state: $state_file" >&2
    return 1
  fi

  return 0
}

ensure_agent_of_empires_persistence_link() {
  local aoe_persist_dir aoe_config_dir
  aoe_persist_dir="/home/vscode/persistent-data/agent-of-empires"
  aoe_config_dir="$HOME/.config/agent-of-empires"

  mkdir -p "$aoe_persist_dir" "$HOME/.config"

  if [[ -L "$aoe_config_dir" ]]; then
    ln -sfn "$aoe_persist_dir" "$aoe_config_dir"
  else
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
  fi

  initialize_agent_of_empires_state "$aoe_persist_dir"
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

opencode_managed_source_dir() {
  local candidate

  for candidate in \
    /home/vscode/.host-dotfiles/private_dot_config/opencode \
    /tmp/host-dotfiles/private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode \
    /tmp/host-dotfiles/private_dot_config/opencode \
    /home/vscode/.host-dotfiles/.config/opencode \
    /tmp/host-dotfiles/.config/opencode; do
    if [[ -d "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

backup_opencode_unmanaged_path() {
  local dst backup_root backup_run_dir backup_path backup_parent
  dst="$1"

  if [[ ! -e "$dst" && ! -L "$dst" ]]; then
    return 0
  fi

  backup_root="${OPENCODE_MANAGED_BACKUP_ROOT:-/home/vscode/persistent-data/opencode/unmanaged-managed-path-backups}"
  backup_run_dir="$backup_root/$(date -u +%Y%m%dT%H%M%SZ)-$$"
  backup_path="$backup_run_dir/${dst#/}"
  backup_parent="${backup_path%/*}"

  mkdir -p "$backup_parent"
  mv "$dst" "$backup_path"
  echo "WARN: Moved conflicting OpenCode managed path aside: $dst" >&2
  echo "WARN: Backup location: $backup_path" >&2
}

opencode_managed_asset_names() {
  printf '%s\n' \
    AGENTS.md \
    agents \
    commands \
    opencode-notifier.json \
    opencode-profile.sh \
    opencode-quota \
    opencode.jsonc \
    plugins \
    profiles \
    prompts \
    skills \
    tui.json
}

opencode_managed_path_is_allowed() {
  local relpath
  relpath="$1"

  case "$relpath" in
    AGENTS.md | \
      agents | agents/* | \
      commands | commands/* | \
      opencode-notifier.json | \
      opencode-profile.sh | \
      opencode-quota | opencode-quota/* | \
      opencode.jsonc | \
      plugins | plugins/* | \
      profiles | profiles/* | \
      prompts | prompts/* | \
      skills | skills/* | \
      tui.json)
      return 0
      ;;
  esac

  return 1
}

opencode_managed_path_is_runtime_generated() {
  local relpath profile_relpath profile_child
  relpath="$1"

  case "/$relpath/" in
    */node_modules/*)
      return 0
      ;;
  esac

  case "$relpath" in
    .gitignore | package.json | package-lock.json | bun.lock)
      return 0
      ;;
    profiles/*)
      profile_relpath="${relpath#profiles/}"
      profile_child="${profile_relpath#*/}"
      if [[ "$profile_child" != "$profile_relpath" ]]; then
        case "$profile_child" in
          .gitignore | package.json | package-lock.json | bun.lock)
            return 0
            ;;
        esac
      fi
      ;;
  esac

  return 1
}

validate_opencode_managed_manifest() {
  local manifest error_prefix entry_type relpath extra
  manifest="$1"
  error_prefix="${2:-ERROR}"

  [[ -f "$manifest" ]] || return 0

  while IFS=$'\t' read -r entry_type relpath extra || \
    [[ -n "${entry_type:-}${relpath:-}${extra:-}" ]]; do
    if [[ "$entry_type" != "d" && "$entry_type" != "f" ]]; then
      echo "$error_prefix: Invalid OpenCode managed manifest entry type: $entry_type" >&2
      return 1
    fi
    if [[ -z "$relpath" || -n "${extra:-}" ]]; then
      echo "$error_prefix: Invalid OpenCode managed manifest path entry." >&2
      return 1
    fi
    case "$relpath" in
      /* | . | .. | ../* | */../* | */..)
        echo "$error_prefix: Unsafe OpenCode managed manifest path: $relpath" >&2
        return 1
        ;;
    esac
    if ! opencode_managed_path_is_allowed "$relpath"; then
      echo "$error_prefix: OpenCode managed manifest path is outside the allowlist: $relpath" >&2
      return 1
    fi
  done <"$manifest"
}

build_opencode_managed_manifest() {
  local source_dir manifest source_entries managed_name source_path entry relpath
  source_dir="$1"
  manifest="$2"
  source_entries="$3"

  : >"$manifest"

  while IFS= read -r managed_name; do
    source_path="$source_dir/$managed_name"

    if [[ -L "$source_path" ]]; then
      echo "ERROR: OpenCode managed source symlinks are not supported: $source_path" >&2
      return 1
    fi
    if [[ -f "$source_path" ]]; then
      printf 'f\t%s\n' "$managed_name" >>"$manifest"
      continue
    fi
    if [[ ! -d "$source_path" ]]; then
      if [[ -e "$source_path" ]]; then
        echo "ERROR: Unsupported OpenCode managed source entry: $source_path" >&2
        return 1
      fi
      continue
    fi

    if ! find "$source_path" -mindepth 1 \
      \( -name node_modules -prune \) -o -print | \
      LC_ALL=C sort >"$source_entries"; then
      echo "ERROR: Could not inventory OpenCode managed source: $source_path" >&2
      return 1
    fi

    printf 'd\t%s\n' "$managed_name" >>"$manifest"
    while IFS= read -r entry; do
      [[ -n "$entry" ]] || continue
      relpath="${entry#"$source_dir"/}"
      if opencode_managed_path_is_runtime_generated "$relpath"; then
        continue
      elif [[ -L "$entry" ]]; then
        echo "ERROR: OpenCode managed source symlinks are not supported: $entry" >&2
        return 1
      elif [[ -d "$entry" ]]; then
        printf 'd\t%s\n' "$relpath" >>"$manifest"
      elif [[ -f "$entry" ]]; then
        printf 'f\t%s\n' "$relpath" >>"$manifest"
      else
        echo "ERROR: Unsupported OpenCode managed source entry: $entry" >&2
        return 1
      fi
    done <"$source_entries"
  done < <(opencode_managed_asset_names)
}

remove_opencode_managed_asset_links() {
  local opencode_config_dir managed_name dst
  opencode_config_dir="$1"

  while IFS= read -r managed_name; do
    dst="$opencode_config_dir/$managed_name"
    if [[ -L "$dst" ]]; then
      rm -f "$dst"
    fi
  done < <(opencode_managed_asset_names)
}

remove_stale_opencode_managed_assets() {
  local old_manifest new_manifest opencode_config_dir stale_dirs
  local entry_type relpath dst
  old_manifest="$1"
  new_manifest="$2"
  opencode_config_dir="$3"
  stale_dirs="$4"

  : >"$stale_dirs"
  while IFS=$'\t' read -r entry_type relpath || \
    [[ -n "${entry_type:-}${relpath:-}" ]]; do
    dst="$opencode_config_dir/$relpath"
    if opencode_managed_path_is_runtime_generated "$relpath"; then
      continue
    elif [[ "$entry_type" == "d" ]]; then
      printf '%s\n' "$relpath" >>"$stale_dirs"
    elif [[ -L "$dst" || -f "$dst" ]]; then
      rm -f "$dst"
    elif [[ -e "$dst" ]]; then
      echo "WARN: Preserving stale OpenCode managed path with a changed type: $dst" >&2
    fi
  done < <(LC_ALL=C comm -23 "$old_manifest" "$new_manifest")

  while IFS= read -r relpath; do
    [[ -n "$relpath" ]] || continue
    dst="$opencode_config_dir/$relpath"
    if [[ -d "$dst" && ! -L "$dst" ]]; then
      rmdir "$dst" 2>/dev/null || true
    fi
  done < <(LC_ALL=C sort -ru "$stale_dirs")
}

copy_opencode_managed_assets() {
  local source_dir opencode_config_dir manifest
  local entry_type relpath src dst dst_parent tmp_file
  source_dir="$1"
  opencode_config_dir="$2"
  manifest="$3"

  while IFS=$'\t' read -r entry_type relpath; do
    [[ "$entry_type" == "d" ]] || continue
    src="$source_dir/$relpath"
    dst="$opencode_config_dir/$relpath"

    if [[ ! -d "$src" || -L "$src" ]]; then
      echo "ERROR: OpenCode managed source directory changed during materialization: $src" >&2
      return 1
    fi
    if [[ -L "$dst" || ( -e "$dst" && ! -d "$dst" ) ]]; then
      backup_opencode_unmanaged_path "$dst"
    fi
    mkdir -p "$dst"
    chmod u+rwx "$dst"
  done <"$manifest"

  while IFS=$'\t' read -r entry_type relpath; do
    [[ "$entry_type" == "f" ]] || continue
    src="$source_dir/$relpath"
    dst="$opencode_config_dir/$relpath"
    dst_parent="${dst%/*}"

    if [[ ! -f "$src" || -L "$src" ]]; then
      echo "ERROR: OpenCode managed source file changed during materialization: $src" >&2
      return 1
    fi
    if [[ -f "$dst" && ! -L "$dst" ]] && cmp -s "$src" "$dst"; then
      chmod u+rw "$dst"
      if [[ -x "$src" ]]; then
        chmod u+x "$dst"
      else
        chmod u-x "$dst"
      fi
      continue
    fi
    if [[ -L "$dst" || ( -e "$dst" && ! -f "$dst" ) ]]; then
      backup_opencode_unmanaged_path "$dst"
    fi

    mkdir -p "$dst_parent"
    tmp_file="$(mktemp "$dst_parent/.opencode-managed.XXXXXX")"
    if ! cp "$src" "$tmp_file"; then
      rm -f "$tmp_file"
      return 1
    fi
    if ! chmod u+rw "$tmp_file"; then
      rm -f "$tmp_file"
      return 1
    fi
    if [[ -x "$src" ]]; then
      if ! chmod u+x "$tmp_file"; then
        rm -f "$tmp_file"
        return 1
      fi
    else
      if ! chmod u-x "$tmp_file"; then
        rm -f "$tmp_file"
        return 1
      fi
    fi
    if ! mv -f "$tmp_file" "$dst"; then
      rm -f "$tmp_file"
      return 1
    fi
  done <"$manifest"
}

materialize_opencode_managed_assets_from() (
  set -Eeuo pipefail

  local source_dir opencode_config_dir state_dir manifest_path
  local raw_manifest new_manifest previous_manifest stale_dirs source_entries
  source_dir="$1"
  opencode_config_dir="$2"
  state_dir="$3"

  if [[ ! -d "$source_dir" ]]; then
    echo "WARN: OpenCode managed source not found; keeping existing OpenCode config." >&2
    return 0
  fi

  mkdir -p "$opencode_config_dir" "$state_dir"
  manifest_path="$state_dir/managed-assets.tsv"
  raw_manifest="$(mktemp "$state_dir/managed-assets.raw.XXXXXX")"
  new_manifest="$(mktemp "$state_dir/managed-assets.new.XXXXXX")"
  previous_manifest="$(mktemp "$state_dir/managed-assets.previous.XXXXXX")"
  stale_dirs="$(mktemp "$state_dir/managed-assets.stale-dirs.XXXXXX")"
  source_entries="$(mktemp "$state_dir/managed-assets.source-entries.XXXXXX")"
  trap 'rm -f "$raw_manifest" "$new_manifest" "$previous_manifest" "$stale_dirs" "$source_entries"' EXIT

  build_opencode_managed_manifest "$source_dir" "$raw_manifest" "$source_entries"
  LC_ALL=C sort -u "$raw_manifest" >"$new_manifest"
  validate_opencode_managed_manifest "$new_manifest"
  if validate_opencode_managed_manifest "$manifest_path" WARN; then
    if [[ -f "$manifest_path" ]]; then
      LC_ALL=C sort -u "$manifest_path" >"$previous_manifest"
    fi
  else
    echo "WARN: Ignoring invalid OpenCode managed manifest; stale cleanup is disabled for this run." >&2
  fi

  remove_opencode_managed_asset_links "$opencode_config_dir"
  remove_stale_opencode_managed_assets \
    "$previous_manifest" "$new_manifest" "$opencode_config_dir" "$stale_dirs"
  copy_opencode_managed_assets "$source_dir" "$opencode_config_dir" "$new_manifest"

  chmod 0600 "$new_manifest"
  mv -f "$new_manifest" "$manifest_path"
)

remove_legacy_opencode_config() {
  local legacy_config
  legacy_config="$1"

  if [[ ! -e "$legacy_config" && ! -L "$legacy_config" ]]; then
    return 0
  fi

  if [[ -L "$legacy_config" ]]; then
    rm -f "$legacy_config"
  else
    backup_opencode_unmanaged_path "$legacy_config"
  fi

  echo "WARN: Removed legacy $legacy_config; using opencode.jsonc." >&2
}

materialize_opencode_managed_assets() {
  local source_dir opencode_config_dir state_dir

  if ! source_dir="$(opencode_managed_source_dir)"; then
    echo "WARN: OpenCode managed source not found; keeping existing OpenCode config." >&2
    return 0
  fi

  opencode_config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
  state_dir="${OPENCODE_MANAGED_STATE_DIR:-/home/vscode/persistent-data/opencode/lifecycle}"
  materialize_opencode_managed_assets_from "$source_dir" "$opencode_config_dir" "$state_dir"

  if [[ -e "$opencode_config_dir/opencode.json" && -e "$opencode_config_dir/opencode.jsonc" ]]; then
    remove_legacy_opencode_config "$opencode_config_dir/opencode.json"
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

# Install a chezmoi executable_ source file as a real 0755 target. The source is
# a read-only 0644 bind mount, so it must be COPIED (not symlinked) to end up
# executable; a symlinked target would fail when invoked as a plain path via
# `sh -c` with "Permission denied". Any prior symlink at the target is removed
# first so cp does not write through it back onto the read-only source. A
# missing source drops a stale target symlink.
install_claude_managed_executable() {
  local src dst
  src="$1"
  dst="$2"

  if [[ ! -e "$src" ]]; then
    [[ -L "$dst" ]] && rm -f "$dst"
    return 0
  fi

  [[ -L "$dst" ]] && rm -f "$dst"
  cp -f "$src" "$dst"
  chmod 0755 "$dst"
}

# Decode a chezmoi source entry name into its applied target name. The container
# consumes the RAW chezmoi source tree over a read-only bind mount, so entries
# still carry chezmoi attribute prefixes that `chezmoi apply` would otherwise
# strip on the host. Echoes "<exec>\t<rendered>", where <exec> is 1 when the
# entry carries the executable_ attribute (needs +x), else 0. Handles stacked
# attributes (e.g. private_executable_) and converts a leading dot_ to ".".
chezmoi_decode_name() {
  local name is_exec
  name="$1"
  is_exec=0

  while true; do
    case "$name" in
      encrypted_*) name="${name#encrypted_}" ;;
      private_*) name="${name#private_}" ;;
      readonly_*) name="${name#readonly_}" ;;
      empty_*) name="${name#empty_}" ;;
      symlink_*) name="${name#symlink_}" ;;
      executable_*)
        name="${name#executable_}"
        is_exec=1
        ;;
      *) break ;;
    esac
  done

  case "$name" in
    dot_*) name=".${name#dot_}" ;;
  esac

  printf '%s\t%s\n' "$is_exec" "$name"
}

# Materialize a chezmoi-managed asset directory into ~/.claude using rendered
# target names. executable_ entries must become real 0755 files: the source
# mount is read-only and 0644, so a bare symlink would resolve non-executable
# and a hook invoked as a plain path via `sh -c` would fail with "Permission
# denied". Non-executable regular files are symlinked by rendered name (kept
# live-editable from the host). Subdirectories are symlinked wholesale by
# rendered name to preserve namespaced nesting (e.g. commands/<ns>/...); a
# future executable_ file nested inside such a subdir would NOT gain +x.
materialize_claude_managed_dir() {
  local source_dir target_dir entry base decoded is_exec rendered
  source_dir="$1"
  target_dir="$2"

  if [[ ! -d "$source_dir" ]] || \
    [[ -z "$(find "$source_dir" -mindepth 1 -print -quit)" ]]; then
    # Nothing to materialize; drop a stale wholesale symlink if one remains.
    [[ -L "$target_dir" ]] && rm -f "$target_dir"
    return 0
  fi

  # Replace a pre-fix wholesale directory symlink with a real directory.
  [[ -L "$target_dir" ]] && rm -f "$target_dir"
  mkdir -p "$target_dir"

  for entry in "$source_dir"/*; do
    [[ -e "$entry" ]] || continue
    base="${entry##*/}"
    decoded="$(chezmoi_decode_name "$base")"
    is_exec="${decoded%%$'\t'*}"
    rendered="${decoded#*$'\t'}"

    # executable_ regular files must be real 0755 copies (read-only 0644
    # source mount); everything else is symlinked by rendered name.
    if [[ -f "$entry" ]] && [[ "$is_exec" == 1 ]]; then
      install_claude_managed_executable "$entry" "$target_dir/$rendered"
    else
      link_claude_managed_path "$entry" "$target_dir/$rendered"
    fi
  done
}

install_claude_managed_asset_links() {
  local source_dir claude_config_dir managed_name

  if ! source_dir="$(claude_managed_source_dir)"; then
    echo "WARN: Claude managed source not found; keeping existing Claude config." >&2
    return 0
  fi

  claude_config_dir="$HOME/.claude"
  mkdir -p "$claude_config_dir"

  # settings-base.json, not modify_private_settings.json: the latter is a
  # chezmoi modify-template (Go template source, not JSON) and this container
  # symlinks host files directly without running chezmoi. The base carries
  # env/permissions/statusLine and the gate-bd-destructive.sh hook; the aoe
  # status hooks it omits are host-only anyway.
  link_claude_managed_path "$source_dir/settings-base.json" "$claude_config_dir/settings.json"
  link_claude_managed_path "$source_dir/AGENTS.md" "$claude_config_dir/AGENTS.md"
  link_claude_managed_path "$source_dir/AGENTS.md" "$claude_config_dir/CLAUDE.md"
  # AGENTS.md imports @~/.claude/no-ai-isms.md; the link must exist.
  link_claude_managed_path "$source_dir/no-ai-isms.md" "$claude_config_dir/no-ai-isms.md"
  install_claude_managed_executable "$source_dir/executable_statusline.sh" "$claude_config_dir/statusline.sh"

  for managed_name in agents hooks commands; do
    materialize_claude_managed_dir \
      "$source_dir/$managed_name" "$claude_config_dir/$managed_name"
  done

  if [[ -d "$claude_config_dir/commands" && ! -L "$claude_config_dir/commands" ]]; then
    rm -f "$claude_config_dir/commands/todo.md"
  fi
  rm -f "$claude_config_dir/commit-docs.sh"
}

# Register the user-scope Claude MCP servers declared by the host dotfiles.
# The container never runs chezmoi, so it replays the SAME repo-only inputs the
# host apply hook uses, over the read-only /tmp/host-dotfiles bind mount. The
# applier honours the config's platforms/skipDevcontainer keys and DEVCONTAINER=1
# is already set in containerEnv, so container-excluded entries drop out here.
#
# Callers must run ensure_claude_persistence_links first: `claude mcp add` writes
# to ~/.claude.json, which has to be the persistent-volume symlink for the
# registration to survive a rebuild.
#
# Never fails the lifecycle hook. Claude Code is optional plumbing, and a stale
# or absent host mount must not block the rest of postCreate/postStart.
register_claude_mcp_servers() {
  local host_dotfiles applier config
  host_dotfiles="${CLAUDE_MCP_HOST_DOTFILES:-/tmp/host-dotfiles}"
  applier="$host_dotfiles/assets/claude-mcp-apply.py"
  config="$host_dotfiles/configs/claude-mcp.json"

  if ! command -v claude >/dev/null 2>&1; then
    echo "WARN: claude command not found; skipping Claude MCP registration." >&2
    return 0
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    echo "WARN: python3 not found; skipping Claude MCP registration." >&2
    return 0
  fi

  if [[ ! -f "$applier" ]]; then
    echo "WARN: Claude MCP applier not found at $applier; skipping registration." >&2
    return 0
  fi

  if [[ ! -f "$config" ]]; then
    echo "WARN: Claude MCP config not found at $config; skipping registration." >&2
    return 0
  fi

  if ! python3 "$applier" "$config"; then
    echo "WARN: Claude MCP registration failed; continuing." >&2
  fi

  return 0
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
  tag="bd-fixes-v2.1.4-bd.2-45f59e9"
  asset="beads-kanban-bd-fixes-2.1.4-bd.2-integration-bd-fixes-45f59e9.vsix"
  expected_sha="58344a593b89bfef1944c742fa5640b9e8d3ea34023d6dc45d2586f8202f30b6"
  fork_extension_id="balaji-dutt.beads-kanban-bd-fixes"
  fork_version="2.1.4-bd.2"
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
