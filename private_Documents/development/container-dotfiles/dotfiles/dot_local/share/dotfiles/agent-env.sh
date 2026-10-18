dotfiles_agent_env() {
  local shims_dir candidate entry existing remaining new_path duplicate last
  local -a candidates promoted
  shims_dir="${MISE_SHIMS_DIR:-${MISE_DATA_DIR:-${XDG_DATA_HOME:-${HOME}/.local/share}/mise}/shims}"
  case "$shims_dir" in
    /*) ;;
    *) printf '%s\n' 'agent-env: mise shim directory must be absolute' >&2; return 1 ;;
  esac
  if [ ! -d "$shims_dir" ] || [ ! -r "$shims_dir" ] || [ ! -x "$shims_dir" ]; then
    printf '%s\n' 'agent-env: mise shim directory is missing or inaccessible' >&2
    return 1
  fi
  if [ ! -x "$shims_dir/python3" ] || [ -d "$shims_dir/python3" ]; then
    printf '%s\n' 'agent-env: mise python3 shim is missing or not executable' >&2
    return 1
  fi

  candidates=()
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -d "$VIRTUAL_ENV/bin" ]; then
    candidates+=("$VIRTUAL_ENV/bin")
  fi
  candidates+=("$HOME/bin" "$shims_dir" "$HOME/.local/bin" "$HOME/.bun/bin")
  case "${OSTYPE:-}" in
    darwin*) candidates+=(/opt/homebrew/bin /opt/homebrew/sbin /usr/local/bin /usr/local/sbin) ;;
  esac
  promoted=()
  new_path=''
  for candidate in "${candidates[@]}"; do
    [ -d "$candidate" ] || continue
    duplicate=0
    for existing in ${promoted[@]+"${promoted[@]}"}; do
      if [ "$candidate" = "$existing" ]; then duplicate=1; break; fi
    done
    [ "$duplicate" = 0 ] || continue
    promoted+=("$candidate")
    new_path="${new_path:+${new_path}:}${candidate}"
  done
  remaining="${PATH:-/usr/bin:/bin:/usr/sbin:/sbin}"
  while :; do
    last=0
    case "$remaining" in
      *:*) entry="${remaining%%:*}"; remaining="${remaining#*:}" ;;
      *) entry="$remaining"; last=1 ;;
    esac
    duplicate=0
    for existing in ${promoted[@]+"${promoted[@]}"}; do
      if [ "$entry" = "$existing" ]; then duplicate=1; break; fi
    done
    if [ -n "$entry" ] && [ "$duplicate" = 0 ]; then
      new_path="${new_path:+${new_path}:}${entry}"
    fi
    [ "$last" = 0 ] || break
  done
  export PATH="$new_path"
  export MISE_AUTO_INSTALL=false
  export MISE_NOT_FOUND_AUTO_INSTALL=false
  export MISE_NOT_FOUND_SYSTEM_FALLBACK=false
}

dotfiles_opencode_env() {
  local tracing=0 verbose=0 rc=0 config_home env_file completed_key
  case "$-" in *x*) tracing=1 ;; esac
  case "$-" in *v*) verbose=1 ;; esac
  { set +x +v; } 2>/dev/null

  config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
  env_file="$config_home/opencode/opencode.env"
  case "$config_home" in
    /*)
      if [ ! -e "$env_file" ] && [ ! -L "$env_file" ]; then
        :
      elif [ ! -f "$env_file" ] || [ ! -r "$env_file" ]; then
        rc=1
      elif completed_key="$(
        {
          unset OPENCODE_API_KEY BASH_XTRACEFD
          source "$env_file" >/dev/null 2>&1 || exit 1
          set +x +v
          builtin printf '%s.' "${OPENCODE_API_KEY-}"
        } 2>/dev/null
      )"; then
        case "$completed_key" in
          *.)
            completed_key="${completed_key%.}"
            if [ -n "$completed_key" ]; then
              export OPENCODE_API_KEY="$completed_key"
            else
              unset OPENCODE_API_KEY
            fi
            ;;
          *) rc=1 ;;
        esac
      else
        rc=1
      fi
      ;;
    *) rc=1 ;;
  esac

  unset completed_key
  if [ "$verbose" = 1 ]; then set -v; fi
  if [ "$tracing" = 1 ]; then set -x; fi
  return "$rc"
}
