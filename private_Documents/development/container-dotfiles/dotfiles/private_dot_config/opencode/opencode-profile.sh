#!/usr/bin/env bash

_opencode_normalize_profile_name() {
  local name
  name="$1"
  case "$name" in
    ""|chatgpt)
      printf 'defaults\n'
      ;;
    "."|".."|*[!A-Za-z0-9._-]*)
      printf 'Invalid OpenCode profile: %s\n' "$name" >&2
      return 1
      ;;
    *)
      printf '%s\n' "$name"
      ;;
  esac
}

_opencode_profiles_joined() {
  local raw token normalized joined restore_noglob invalid
  raw="${OPENCODE_PROFILES:-${OPENCODE_PROFILE:-defaults}}"
  joined=""
  invalid=0

  case $- in
    *f*) restore_noglob=0 ;;
    *)
      restore_noglob=1
      set -f
      ;;
  esac

  for token in $raw; do
    if ! normalized="$(_opencode_normalize_profile_name "$token")"; then
      invalid=1
      break
    fi
    [ -n "$normalized" ] || continue
    case " $joined " in
      *" $normalized "*) ;;
      *) joined="${joined:+$joined }$normalized" ;;
    esac
  done

  if [ "$restore_noglob" -eq 1 ]; then
    set +f
  fi

  if [ "$invalid" -eq 1 ]; then
    return 1
  fi

  printf '%s\n' "${joined:-defaults}"
}

_opencode_apply_anthropic_api_export() {
  local joined
  if ! joined="$(_opencode_profiles_joined)"; then
    return
  fi

  case " $joined " in
    *" anthropic-api "*)
      if [ -n "${_OPENCODE_ANTHROPIC_API_KEY:-}" ]; then
        export ANTHROPIC_API_KEY="${_OPENCODE_ANTHROPIC_API_KEY}"
        export _OPENCODE_ANTHROPIC_API_MANAGED=1
      elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
        export ANTHROPIC_API_KEY
        unset _OPENCODE_ANTHROPIC_API_MANAGED
      else
        unset ANTHROPIC_API_KEY
        unset _OPENCODE_ANTHROPIC_API_MANAGED
      fi
      ;;
    *)
      if [ "${_OPENCODE_ANTHROPIC_API_MANAGED:-0}" = "1" ]; then
        unset ANTHROPIC_API_KEY
      fi
      unset _OPENCODE_ANTHROPIC_API_MANAGED
      ;;
  esac
}

_opencode_apply_profile_dir() {
  local config_home workspace_root workspace_config
  local sync_helper sync_result signature target_dir
  local joined primary

  config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
  if ! joined="$(_opencode_profiles_joined)"; then
    unset OPENCODE_CONFIG_DIR
    unset _OPENCODE_PROFILE_CONTEXT_SIGNATURE
    return
  fi
  primary="${joined%% *}"

  if [ -z "$primary" ]; then
    primary="defaults"
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

  signature="$joined|$workspace_root|$workspace_config"
  if [ "${_OPENCODE_PROFILE_CONTEXT_SIGNATURE:-}" = "$signature" ] && [ -n "${OPENCODE_CONFIG_DIR:-}" ]; then
    _opencode_apply_anthropic_api_export
    return
  fi

  target_dir="$config_home/opencode/profiles/$primary"
  sync_helper="$config_home/opencode/opencode-sync-workspace-overrides.sh"
  if [ -r "$sync_helper" ]; then
    if sync_result="$(bash "$sync_helper" "$joined" "$workspace_root")"; then
      if [ -n "$sync_result" ] && [ -d "$sync_result" ]; then
        target_dir="$sync_result"
      fi
    else
      echo "WARN: OpenCode workspace profile sync failed; using static profile." >&2
    fi
  fi

  if [ ! -d "$target_dir" ]; then
    unset OPENCODE_CONFIG_DIR
    unset _OPENCODE_PROFILE_CONTEXT_SIGNATURE
    _opencode_apply_anthropic_api_export
    return
  fi

  export OPENCODE_PROFILES="$joined"
  export OPENCODE_PROFILE="$primary"
  export OPENCODE_CONFIG_DIR="$target_dir"
  export _OPENCODE_PROFILE_CONTEXT_SIGNATURE="$signature"

  _opencode_apply_anthropic_api_export
}

_opencode_update_profiles_in_env_file() {
  local next_profiles env_file env_dir tmp_file primary
  next_profiles="$1"
  env_file="${OPENCODE_ENV_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.env}"
  env_dir="${env_file%/*}"
  if ! primary="$(_opencode_normalize_profile_name "${next_profiles%% *}")"; then
    return 1
  fi

  mkdir -p "$env_dir"
  tmp_file="$(mktemp "$env_dir/opencode.env.XXXXXX")"

  if [ -r "$env_file" ]; then
    if ! awk -v profiles="$next_profiles" -v profile="$primary" '
      BEGIN { updated_profiles = 0; updated_profile = 0 }
      /^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILES=/ {
        if (!updated_profiles) {
          printf "OPENCODE_PROFILES=\"%s\"\n", profiles
          updated_profiles = 1
        }
        next
      }
      /^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILE=/ {
        if (!updated_profile) {
          printf "OPENCODE_PROFILE=%s\n", profile
          updated_profile = 1
        }
        next
      }
      { print }
      END {
        if (!updated_profiles) {
          printf "OPENCODE_PROFILES=\"%s\"\n", profiles
        }
        if (!updated_profile) {
          printf "OPENCODE_PROFILE=%s\n", profile
        }
      }
    ' "$env_file" >| "$tmp_file"; then
      rm -f "$tmp_file"
      return 1
    fi
  else
    {
      printf 'OPENCODE_PROFILES="%s"\n' "$next_profiles"
      printf 'OPENCODE_PROFILE=%s\n' "$primary"
    } >| "$tmp_file"
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

_opencode_normalize_profile_args() {
  local raw normalized
  local -a stack=()

  for raw in "$@"; do
    if ! normalized="$(_opencode_normalize_profile_name "$raw")"; then
      return 1
    fi
    [ -n "$normalized" ] || continue
    case " ${stack[*]} " in
      *" $normalized "*) ;;
      *) stack+=("$normalized") ;;
    esac
  done

  if [ "${#stack[@]}" -eq 0 ]; then
    stack=("defaults")
  fi

  printf '%s\n' "${stack[*]}"
}

opencode_profile() {
  local action next_profiles
  action="${1:-show}"

  case "$action" in
    show)
      _opencode_apply_profile_dir
      printf 'OPENCODE_PROFILES=%s\n' "${OPENCODE_PROFILES:-defaults}"
      printf 'OPENCODE_PROFILE=%s\n' "${OPENCODE_PROFILE:-defaults}"
      printf 'OPENCODE_CONFIG_DIR=%s\n' "${OPENCODE_CONFIG_DIR:-<unset>}"
      ;;
    set)
      shift || true
      if ! next_profiles="$(_opencode_normalize_profile_args "$@")"; then
        printf 'Invalid OpenCode profile in set arguments\n' >&2
        return 1
      fi
      if ! _opencode_update_profiles_in_env_file "$next_profiles"; then
        printf 'Failed to update %s\n' "${OPENCODE_ENV_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.env}" >&2
        return 1
      fi
      export OPENCODE_PROFILES="$next_profiles"
      export OPENCODE_PROFILE="${next_profiles%% *}"
      _opencode_apply_profile_dir
      _opencode_run_post_switch_hook
      printf 'Switched OpenCode profiles to %s\n' "$next_profiles"
      printf 'OPENCODE_CONFIG_DIR=%s\n' "${OPENCODE_CONFIG_DIR:-<unset>}"
      ;;
    chatgpt|defaults|copilot|anthropic-api|api-fallback)
      if ! next_profiles="$(_opencode_normalize_profile_args "$action")"; then
        printf 'Invalid OpenCode profile: %s\n' "$action" >&2
        return 1
      fi
      if ! _opencode_update_profiles_in_env_file "$next_profiles"; then
        printf 'Failed to update %s\n' "${OPENCODE_ENV_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.env}" >&2
        return 1
      fi
      export OPENCODE_PROFILES="$next_profiles"
      export OPENCODE_PROFILE="${next_profiles%% *}"
      _opencode_apply_profile_dir
      _opencode_run_post_switch_hook
      printf 'Switched OpenCode profiles to %s\n' "$next_profiles"
      printf 'OPENCODE_CONFIG_DIR=%s\n' "${OPENCODE_CONFIG_DIR:-<unset>}"
      ;;
    *)
      printf 'Usage: opencode-profile {show|set <profiles...>|defaults|chatgpt|copilot|anthropic-api|api-fallback}\n' >&2
      return 1
      ;;
  esac
}

alias opencode-profile='opencode_profile'
_opencode_apply_profile_dir
_opencode_run_post_switch_hook
