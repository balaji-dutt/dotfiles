#!/usr/bin/env bash
set -Eeuo pipefail

PATH="$HOME/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

readonly STABLE_DIR="/tmp/macos-ssh-agent"
readonly STABLE_SOCK="$STABLE_DIR/ssh-agent.sock"

die() {
  printf 'macos_ssh_agent_relay: %s\n' "$*" >&2
  exit 1
}

agent_socket_ok() {
  local sock rc
  sock="$1"

  [[ -S "$sock" ]] || return 1

  rc=0
  SSH_AUTH_SOCK="$sock" /usr/bin/ssh-add -l >/dev/null 2>&1 || rc=$?
  case "$rc" in
    0|1) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_source_sock() {
  local candidate
  candidate="${SSH_AUTH_SOCK:-}"

  if [[ -z "$candidate" || "$candidate" == "$STABLE_SOCK" ]]; then
    candidate="$(/bin/launchctl getenv SSH_AUTH_SOCK 2>/dev/null || true)"
  fi

  [[ -n "$candidate" ]] || die "SSH_AUTH_SOCK is not set in the user launchd environment"
  [[ "$candidate" != "$STABLE_SOCK" ]] || die "source SSH_AUTH_SOCK already points to the stable relay socket"
  [[ -S "$candidate" ]] || die "source SSH_AUTH_SOCK is not a socket: $candidate"

  agent_socket_ok "$candidate" || die "source SSH agent is not reachable via: $candidate"

  printf '%s\n' "$candidate"
}

main() {
  local source_sock

  command -v socat >/dev/null 2>&1 || die "socat is required"

  source_sock="$(resolve_source_sock)"

  umask 077
  mkdir -p "$STABLE_DIR"
  chmod 700 "$STABLE_DIR" >/dev/null 2>&1 || true
  rm -f "$STABLE_SOCK" >/dev/null 2>&1 || true

  exec socat \
    UNIX-LISTEN:"$STABLE_SOCK",unlink-close,fork,mode=600 \
    UNIX-CONNECT:"$source_sock"
}

main "$@"
