#!/usr/bin/env bash

_opencode_apply_profile_dir() {
  local profile config_home profile_dir workspace_root workspace_config
  local sync_helper sync_result target_dir signature
  config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
  profile="${OPENCODE_PROFILE:-chatgpt}"
  profile_dir="$config_home/opencode/profiles/$profile"

  if [ ! -d "$profile_dir" ]; then
    unset OPENCODE_CONFIG_DIR
    unset _OPENCODE_PROFILE_CONTEXT_SIGNATURE
    return
  fi

  workspace_root=""
  if command -v git >/dev/null 2>&1; then
    workspace_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
  fi

  workspace_config=""
  if [ -n "$workspace_root" ]; then
    if [ -f "$workspace_root/.opencode/opencode.json" ]; then
      workspace_config="$workspace_root/.opencode/opencode.json"
    elif [ -f "$workspace_root/.opencode/opencode.jsonc" ]; then
      workspace_config="$workspace_root/.opencode/opencode.jsonc"
    fi
  fi

  signature="$profile|$workspace_root|$workspace_config"
  if [ "${_OPENCODE_PROFILE_CONTEXT_SIGNATURE:-}" = "$signature" ] && [ -n "${OPENCODE_CONFIG_DIR:-}" ]; then
    return
  fi

  target_dir="$profile_dir"
  sync_helper="$config_home/opencode/opencode-sync-workspace-overrides.sh"
  if [ -n "$workspace_config" ] && [ -r "$sync_helper" ]; then
    if sync_result="$(bash "$sync_helper" "$profile" "$workspace_root")"; then
      if [ -n "$sync_result" ] && [ -d "$sync_result" ]; then
        target_dir="$sync_result"
      fi
    else
      echo "WARN: OpenCode workspace profile sync failed; using static profile." >&2
    fi
  fi

  export OPENCODE_CONFIG_DIR="$target_dir"

  export _OPENCODE_PROFILE_CONTEXT_SIGNATURE="$signature"
}

_opencode_on_directory_change() {
  _opencode_apply_profile_dir
}

_opencode_install_shell_hooks() {
  if [ -n "${ZSH_VERSION:-}" ]; then
    if [ -z "${_OPENCODE_ZSH_CHPWD_HOOK_SET:-}" ]; then
      autoload -Uz add-zsh-hook >/dev/null 2>&1 || true
      if command -v add-zsh-hook >/dev/null 2>&1; then
        add-zsh-hook chpwd _opencode_on_directory_change 2>/dev/null || true
      fi
      export _OPENCODE_ZSH_CHPWD_HOOK_SET=1
    fi
  elif [ -n "${BASH_VERSION:-}" ]; then
    case ";${PROMPT_COMMAND:-};" in
      *";_opencode_on_directory_change;"*) ;;
      *)
        if [ -n "${PROMPT_COMMAND:-}" ]; then
          PROMPT_COMMAND="_opencode_on_directory_change;${PROMPT_COMMAND}"
        else
          PROMPT_COMMAND="_opencode_on_directory_change"
        fi
        ;;
    esac
  fi
}

_opencode_update_profile_in_env_file() {
  local next_profile env_file env_dir tmp_file
  next_profile="$1"
  env_file="${OPENCODE_ENV_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.env}"
  env_dir="${env_file%/*}"

  mkdir -p "$env_dir"
  tmp_file="$(mktemp "$env_dir/opencode.env.XXXXXX")"

  if [ -r "$env_file" ]; then
    if ! awk -v profile="$next_profile" '
      BEGIN { updated = 0 }
      /^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILE=/ {
        if (!updated) {
          printf "OPENCODE_PROFILE=%s\n", profile
          updated = 1
        }
        next
      }
      { print }
      END {
        if (!updated) {
          printf "OPENCODE_PROFILE=%s\n", profile
        }
      }
    ' "$env_file" >| "$tmp_file"; then
      rm -f "$tmp_file"
      return 1
    fi
  else
    printf 'OPENCODE_PROFILE=%s\n' "$next_profile" >| "$tmp_file"
  fi

  if ! mv -f "$tmp_file" "$env_file"; then
    rm -f "$tmp_file"
    return 1
  fi

  chmod 600 "$env_file" 2>/dev/null || true
}

_opencode_run_post_switch_hook() {
  if command -v _opencode_profile_post_switch >/dev/null 2>&1; then
    _opencode_profile_post_switch
  fi
}

opencode_profile() {
  local action
  action="${1:-show}"

  case "$action" in
    show)
      _opencode_apply_profile_dir
      printf 'OPENCODE_PROFILE=%s\n' "${OPENCODE_PROFILE:-chatgpt}"
      printf 'OPENCODE_CONFIG_DIR=%s\n' "${OPENCODE_CONFIG_DIR:-<unset>}"
      ;;
    chatgpt|copilot)
      if ! _opencode_update_profile_in_env_file "$action"; then
        printf 'Failed to update %s\n' "$OPENCODE_ENV_FILE" >&2
        return 1
      fi
      export OPENCODE_PROFILE="$action"
      _opencode_apply_profile_dir
      _opencode_run_post_switch_hook
      printf 'Switched OpenCode profile to %s\n' "$action"
      printf 'OPENCODE_CONFIG_DIR=%s\n' "${OPENCODE_CONFIG_DIR:-<unset>}"
      ;;
    *)
      printf 'Usage: opencode-profile {show|chatgpt|copilot}\n' >&2
      return 1
      ;;
  esac
}

alias opencode-profile='opencode_profile'
_opencode_install_shell_hooks
_opencode_apply_profile_dir
_opencode_run_post_switch_hook
