#!/usr/bin/env bash

_opencode_apply_profile_dir() {
  local profile config_home profile_dir
  config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
  profile="${OPENCODE_PROFILE:-chatgpt}"
  profile_dir="$config_home/opencode/profiles/$profile"

  if [ -d "$profile_dir" ]; then
    export OPENCODE_CONFIG_DIR="$profile_dir"
  else
    unset OPENCODE_CONFIG_DIR
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
    ' "$env_file" > "$tmp_file"; then
      rm -f "$tmp_file"
      return 1
    fi
  else
    printf 'OPENCODE_PROFILE=%s\n' "$next_profile" > "$tmp_file"
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
      _opencode_update_profile_in_env_file "$action"
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
_opencode_apply_profile_dir
_opencode_run_post_switch_hook
