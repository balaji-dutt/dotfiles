#!/bin/bash

# Runs as root from /Library/LaunchDaemons/com.user.home-ssh.plist under the
# system /bin/bash 3.2, so it avoids bash 4+ syntax.

set -uo pipefail

readonly CONFIG_FILE="/usr/local/etc/dotfiles-home-ssh.conf"
readonly DROPIN_FILE="/etc/ssh/sshd_config.d/050-dotfiles-home-ssh.conf"
readonly SSHD_LABEL="com.openssh.sshd"
readonly SSHD_PLIST="/System/Library/LaunchDaemons/ssh.plist"
readonly LOG_TAG="dotfiles-home-ssh"
readonly SETTLE_SECONDS=5
readonly SAMPLE_GAP_SECONDS=3
readonly DOMAIN_RE='^[a-z0-9][a-z0-9.-]*$'
readonly MAC_RE='^([0-9a-f]{1,2}:){5}[0-9a-f]{1,2}$'
readonly IPV4_RE='^[0-9]{1,3}([.][0-9]{1,3}){3}$'
readonly ETHERNET_PORT_RE='(Ethernet|LAN)'
readonly ETHERNET_DEVICE_RE='^en[0-9]+$'

HOME_DHCP_DOMAIN=""
HOME_ROUTER_MAC=""
reason=""
decision_applied=0

log() {
  /usr/bin/logger -t "$LOG_TAG" -p daemon.notice -- "$*"
}

to_lower() {
  printf '%s' "$1" | /usr/bin/tr '[:upper:]' '[:lower:]'
}

normalize_mac() {
  local value octet result=""
  local -a octets
  value="$(to_lower "$1")"
  [[ "$value" =~ $MAC_RE ]] || return 1
  IFS=: read -r -a octets <<< "$value"
  for octet in "${octets[@]}"; do
    [[ ${#octet} -eq 1 ]] && octet="0$octet"
    result="${result:+$result:}$octet"
  done
  printf '%s' "$result"
}

config_value() {
  /usr/bin/awk -v key="$1" '
    index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }
  ' "$CONFIG_FILE"
}

load_config() {
  local owner_mode owner group mode domain mac

  if [[ -L "$DROPIN_FILE" ]] || [[ ! -f "$DROPIN_FILE" ]]; then
    reason="sshd hardening drop-in missing"
    return 1
  fi
  if [[ -L "$CONFIG_FILE" ]] || [[ ! -f "$CONFIG_FILE" ]]; then
    reason="config missing"
    return 1
  fi
  owner_mode="$(/usr/bin/stat -f '%u %g %Lp' "$CONFIG_FILE")" || {
    reason="config unreadable"
    return 1
  }
  read -r owner group mode <<< "$owner_mode"
  if [[ "$owner" != 0 ]] || [[ "$group" != 0 ]] || \
    [[ ! "$mode" =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & 8#022) != 0 )); then
    reason="config not root-owned or writable by others"
    return 1
  fi

  domain="$(config_value HOME_DHCP_DOMAIN)" || return 1
  domain="$(to_lower "$domain")"
  domain="${domain%.}"
  if [[ ! "$domain" =~ $DOMAIN_RE ]]; then
    reason="config domain invalid"
    return 1
  fi

  mac="$(config_value HOME_ROUTER_MAC)" || return 1
  if [[ -n "$mac" ]]; then
    mac="$(normalize_mac "$mac")" || {
      reason="config router MAC invalid"
      return 1
    }
  fi

  HOME_DHCP_DOMAIN="$domain"
  HOME_ROUTER_MAC="$mac"
}

list_ports() {
  /usr/sbin/networksetup -listallhardwareports | /usr/bin/awk '
    /^Hardware Port: / { port = substr($0, 16) }
    /^Device: / { print port "|" substr($0, 9) }
  '
}

has_ipv4() {
  local details
  details="$(/sbin/ifconfig "$1" 2>/dev/null)" || return 1
  [[ "$details" == *"status: active"* ]] || return 1
  printf '%s\n' "$details" | /usr/bin/awk '
    $1 == "inet" && $2 !~ /^169[.]254[.]/ { found = 1 }
    END { exit !found }
  '
}

router_mac() {
  local device="$1" router
  router="$(/usr/sbin/ipconfig getoption "$device" router 2>/dev/null)" || return 1
  [[ "$router" =~ $IPV4_RE ]] || return 1
  /usr/sbin/arp -n "$router" 2>/dev/null | /usr/bin/awk -v dev="$device" '
    $5 == "on" && $6 == dev { print $4; exit }
  '
}

# The router MAC is only compared on Ethernet: while Ethernet carries the
# traffic, the router's ARP entry on Wi-Fi can expire and would read as foreign.
fingerprint_is_home() {
  local device="$1" check_mac="$2" domain mac
  domain="$(/usr/sbin/ipconfig getoption "$device" domain_name 2>/dev/null)" || return 1
  domain="$(to_lower "$domain")"
  domain="${domain%.}"
  [[ -n "$domain" ]] && [[ "$domain" == "$HOME_DHCP_DOMAIN" ]] || return 1
  if [[ "$check_mac" == 1 ]] && [[ -n "$HOME_ROUTER_MAC" ]]; then
    mac="$(router_mac "$device")" || return 1
    mac="$(normalize_mac "$mac")" || return 1
    [[ "$mac" == "$HOME_ROUTER_MAC" ]] || return 1
  fi
  return 0
}

evaluate() {
  local ports port device home_ethernet=""

  ports="$(list_ports)" || {
    reason="hardware port discovery failed"
    return 1
  }
  if [[ -z "$ports" ]]; then
    reason="no hardware ports"
    return 1
  fi

  while IFS='|' read -r port device; do
    if [[ "$port" == "Wi-Fi" ]]; then
      has_ipv4 "$device" || continue
      if ! fingerprint_is_home "$device" 0; then
        reason="Wi-Fi $device is not on the home network"
        return 1
      fi
    elif [[ "$port" =~ $ETHERNET_PORT_RE ]] && [[ "$device" =~ $ETHERNET_DEVICE_RE ]]; then
      has_ipv4 "$device" || continue
      if ! fingerprint_is_home "$device" 1; then
        reason="Ethernet $device is not on the home network"
        return 1
      fi
      home_ethernet="$device"
    fi
  done <<< "$ports"

  if [[ -z "$home_ethernet" ]]; then
    reason="no home Ethernet link"
    return 1
  fi
  reason="home Ethernet on $home_ethernet"
}

sshd_override() {
  local value
  value="$(/bin/launchctl print-disabled system 2>/dev/null | /usr/bin/awk -v label="\"$SSHD_LABEL\"" '
    $1 == label && $2 == "=>" { print $3; exit }
  ')"
  case "$value" in
    enabled|false) printf 'enabled' ;;
    disabled|true) printf 'disabled' ;;
    *) printf 'unset' ;;
  esac
}

sshd_loaded() {
  /bin/launchctl print "system/$SSHD_LABEL" >/dev/null 2>&1
}

kill_sessions() {
  local name found=""
  for name in sshd sshd-session sshd-auth; do
    if /usr/bin/pgrep -x "$name" >/dev/null 2>&1; then
      found=1
      /usr/bin/pkill -TERM -x "$name"
    fi
  done
  [[ -n "$found" ]] || return 0
  log "terminated SSH sessions while Remote Login is off"
  /bin/sleep 1
  for name in sshd sshd-session sshd-auth; do
    if /usr/bin/pgrep -x "$name" >/dev/null 2>&1; then
      /usr/bin/pkill -KILL -x "$name"
    fi
  done
  return 0
}

ensure_on() {
  local changed=""
  if [[ "$(sshd_override)" != enabled ]]; then
    /bin/launchctl enable "system/$SSHD_LABEL" || return 1
    changed=1
  fi
  if ! sshd_loaded; then
    /bin/launchctl bootstrap system "$SSHD_PLIST" || return 1
    changed=1
  fi
  sshd_loaded || return 1
  if [[ -n "$changed" ]]; then
    log "Remote Login on: $reason"
  fi
}

ensure_off() {
  local rc=0 changed=""
  if sshd_loaded; then
    /bin/launchctl bootout "system/$SSHD_LABEL" || rc=1
    changed=1
  fi
  if [[ "$(sshd_override)" != disabled ]]; then
    /bin/launchctl disable "system/$SSHD_LABEL" || rc=1
    changed=1
  fi
  if [[ -n "$changed" ]]; then
    log "Remote Login off: ${reason:-undetermined}"
  fi
  kill_sessions || rc=1
  return "$rc"
}

on_exit() {
  if [[ "$decision_applied" != 1 ]]; then
    decision_applied=1
    reason="helper exited before deciding"
    ensure_off
  fi
}

main() {
  local first="off" second="off"

  if [[ "$(/usr/bin/id -u)" != 0 ]]; then
    printf 'ERROR: %s must run as root\n' "$LOG_TAG" >&2
    return 1
  fi
  trap on_exit EXIT
  trap 'decision_applied=1; exit 143' TERM

  /bin/sleep "$SETTLE_SECONDS"
  if load_config && evaluate; then
    first="on"
  fi
  /bin/sleep "$SAMPLE_GAP_SECONDS"
  if [[ "$first" == on ]] && evaluate; then
    second="on"
  elif [[ "$first" == on ]]; then
    reason="network changed between samples: $reason"
  fi

  if [[ "$second" == on ]]; then
    if ensure_on; then
      decision_applied=1
      return 0
    fi
    reason="enabling Remote Login failed"
  fi
  decision_applied=1
  ensure_off
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
