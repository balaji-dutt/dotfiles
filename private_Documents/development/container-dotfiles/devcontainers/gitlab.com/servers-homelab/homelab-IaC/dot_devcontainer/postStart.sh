#!/usr/bin/env bash
set -Eeuo pipefail

LOG_FILE="${LOG_FILE:-/tmp/postStart.log}"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee "$LOG_FILE") 2>&1

timestamp() { date +"%Y-%m-%d %H:%M:%S%z"; }

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
common_file="$script_dir/devcontainer-common.sh"
if [[ ! -r "$common_file" ]]; then
  echo "ERROR: Shared devcontainer helper not found: $common_file" >&2
  exit 1
fi
# shellcheck source=private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/devcontainer-common.sh
source "$common_file"

log_run_header() {
  local workspace
  workspace="$1"

  echo "==== [$(timestamp)] postStart run ===="
  echo "script=${BASH_SOURCE[0]}"
  echo "workspace=$workspace"
  echo "hostname=$(hostname 2>/dev/null || true)"
  echo "note=postStart runs when the container starts or reopens; check /tmp/postCreate.log for create/recreate runs."
}

workspace_root="${1:-}"
if [[ -z "$workspace_root" ]] && command -v git >/dev/null 2>&1; then
  workspace_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
workspace_root="${workspace_root:-$PWD}"
log_run_header "$workspace_root"

read_opencode_profiles_from_env_file() {
  local env_file
  env_file="$1"

  [[ -r "$env_file" ]] || return 0

  awk '
    /^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILES=/ {
      sub(/^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILES=/, "", $0)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0)
      if ($0 ~ /^".*"$/ || $0 ~ /^\047.*\047$/) {
        $0 = substr($0, 2, length($0) - 2)
      }
      print $0
      exit
    }
    /^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILE=/ {
      sub(/^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILE=/, "", $0)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0)
      if ($0 ~ /^".*"$/ || $0 ~ /^\047.*\047$/) {
        $0 = substr($0, 2, length($0) - 2)
      }
      print $0
      exit
    }
  ' "$env_file"
}

normalize_opencode_profiles() {
  local raw token normalized joined restore_noglob invalid
  raw="$1"
  joined=""
  invalid=0

  if [[ -z "$raw" ]]; then
    printf 'defaults\n'
    return 0
  fi

  case $- in
    *f*) restore_noglob=0 ;;
    *)
      restore_noglob=1
      set -f
      ;;
  esac

  for token in $raw; do
    case "$token" in
      ""|chatgpt)
        normalized="defaults"
        ;;
      "."|".."|*[!A-Za-z0-9._-]*)
        echo "WARN: Invalid OpenCode profile token in persisted settings: $token" >&2
        invalid=1
        break
        ;;
      *)
        normalized="$token"
        ;;
    esac

    case " $joined " in
      *" $normalized "*) ;;
      *) joined="${joined:+$joined }$normalized" ;;
    esac
  done

  if [[ "$restore_noglob" -eq 1 ]]; then
    set +f
  fi

  if [[ "$invalid" -eq 1 ]]; then
    return 1
  fi

  printf '%s\n' "${joined:-defaults}"
}

write_opencode_profiles_to_env_file() {
  local env_file profiles profile tmp_file
  env_file="$1"
  profiles="$2"

  if ! profiles="$(normalize_opencode_profiles "$profiles")"; then
    return 1
  fi
  profile="${profiles%% *}"

  [[ -n "$profiles" ]] || return 0

  tmp_file="$(mktemp "${env_file}.XXXXXX")"

  if [[ -r "$env_file" ]]; then
    if ! awk -v profiles="$profiles" -v profile="$profile" '
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
    ' "$env_file" > "$tmp_file"; then
      rm -f "$tmp_file"
      return 1
    fi
  else
    {
      printf 'OPENCODE_PROFILES="%s"\n' "$profiles"
      printf 'OPENCODE_PROFILE=%s\n' "$profile"
    } > "$tmp_file"
  fi

  if ! mv -f "$tmp_file" "$env_file"; then
    rm -f "$tmp_file"
    return 1
  fi

  chmod 600 "$env_file" 2>/dev/null || true
}

ensure_beads_persistence_mounts() {
  local workspace beads_dir

  workspace="$1"
  beads_dir="$workspace/.beads"

  if [[ ! -d "$workspace" ]]; then
    echo "ERROR: Workspace path does not exist: $workspace" >&2
    return 1
  fi

  mkdir -p "$beads_dir"

  if [[ ! -w "$beads_dir" ]]; then
    sudo chown "$USER:$USER" "$beads_dir"
  fi

  if [[ ! -f "$beads_dir/config.yaml" && ! -f "$beads_dir/issues.jsonl" ]]; then
    echo "WARN: Beads project metadata not found in $beads_dir; run bd init in the workspace if needed." >&2
  fi
}

install_sset_helper() {
  local helper_path
  mkdir -p "$HOME/.local/bin"
  helper_path="$HOME/.local/bin/sset"

  cat >"$helper_path" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

sock="${SSH_AUTH_SOCK:-/tmp/wsl-ssh-pageant/ssh-agent.sock}"
sock_dir="$(dirname "$sock")"
npiperelay_path="${WSL_NPIPERELAY_PATH:-}"
orbstack_host_sock="${ORBSTACK_HOST_SSH_AUTH_SOCK:-}"
orbstack_relay_script="/tmp/host-homelab-devcontainer/orbstack_ssh_agent_relay.py"
orbstack_relay_log="/tmp/orbstack-ssh-agent-relay.log"
orbstack_relay_pid="${sock_dir}/relay.pid"
ssh_pub_key_file="$HOME/.ssh/root_terraform_ansible.pub"
ssh_key_comments=("root_terraform_ansible" "terraform-ansible")

ensure_orbstack_relay() {
  local python_bin user_name group_name

  [[ -n "$orbstack_host_sock" ]] || return 0

  if [[ ! -S "$orbstack_host_sock" ]]; then
    echo "WARN: OrbStack SSH agent socket not found: $orbstack_host_sock" >&2
    return 1
  fi

  if [[ "$sock" == "$orbstack_host_sock" ]]; then
    echo "WARN: relay socket path must differ from the OrbStack host socket." >&2
    return 1
  fi

  if ! python_bin="$(command -v python3)"; then
    echo "WARN: python3 is required for the OrbStack SSH agent relay." >&2
    return 1
  fi

  if [[ ! -f "$orbstack_relay_script" ]]; then
    echo "WARN: OrbStack SSH agent relay helper not found: $orbstack_relay_script" >&2
    return 1
  fi

  if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true >/dev/null 2>&1; then
    echo "WARN: passwordless sudo is required for the OrbStack SSH agent relay." >&2
    return 1
  fi

  user_name="$(id -un)"
  group_name="$(id -gn)"

  sudo -n sh -c '
set -eu
relay_sock="$1"
relay_dir="$2"
relay_pid="$3"
relay_log="$4"
python_bin="$5"
relay_script="$6"
upstream_sock="$7"
user_name="$8"
group_name="$9"
mkdir -p "$relay_dir"
chmod 700 "$relay_dir"
chown "$user_name:$group_name" "$relay_dir"
if [ -f "$relay_pid" ]; then
  pid="$(cat "$relay_pid" 2>/dev/null || true)"
  if [ -n "$pid" ]; then
    cmdline="$(ps -o command= -p "$pid" 2>/dev/null || true)"
    case "$cmdline" in
      *orbstack_ssh_agent_relay.py*--listen*"$relay_sock"*)
        kill "$pid" >/dev/null 2>&1 || true
        ;;
    esac
  fi
  rm -f "$relay_pid"
fi
rm -f "$relay_sock" "$relay_log"
nohup "$python_bin" "$relay_script" \
  --listen "$relay_sock" \
  --upstream "$upstream_sock" \
  --user "$user_name" \
  --group "$group_name" \
  --mode 0600 \
  >"$relay_log" 2>&1 </dev/null &
echo $! >"$relay_pid"
' sh "$sock" "$sock_dir" "$orbstack_relay_pid" "$orbstack_relay_log" "$python_bin" "$orbstack_relay_script" "$orbstack_host_sock" "$user_name" "$group_name"
}

if [[ -z "$npiperelay_path" ]]; then
  for candidate in /mnt/c/Users/*/Applications/npiperelay.exe; do
    if [[ -x "$candidate" ]]; then
      npiperelay_path="$candidate"
      break
    fi
  done
fi

mkdir -p "$HOME/.ssh" "$sock_dir"
chmod 700 "$sock_dir" >/dev/null 2>&1 || true

ensure_orbstack_relay || true

if [[ -n "$npiperelay_path" ]] && command -v socat >/dev/null 2>&1 && [[ -x "$npiperelay_path" ]]; then
  pkill -f 'socat.*npiperelay\.exe.*ssh-pageant' >/dev/null 2>&1 || true
  rm -f "$sock" >/dev/null 2>&1 || true

  if command -v setsid >/dev/null 2>&1; then
    setsid -f socat EXEC:"\"$npiperelay_path\" -ei -s //./pipe/ssh-pageant" UNIX-LISTEN:"$sock",unlink-close,fork,mode=600 </dev/null >/dev/null 2>&1
  else
    nohup socat EXEC:"\"$npiperelay_path\" -ei -s //./pipe/ssh-pageant" UNIX-LISTEN:"$sock",unlink-close,fork,mode=600 </dev/null >/dev/null 2>&1 &
  fi
fi

agent_ready=0
for ((i=0; i<50; i++)); do
  rc=0
  timeout 2 ssh-add -l >/dev/null 2>&1 || rc=$?
  if [[ "$rc" -eq 0 || "$rc" -eq 1 ]]; then
    agent_ready=1
    break
  fi
  sleep 0.1
done

if [[ "$agent_ready" -ne 1 ]]; then
  echo "WARN: SSH agent did not become ready; keeping existing key file." >&2
  echo "WARN: Reopen or rebuild the devcontainer to refresh SSH agent forwarding." >&2
  exit 1
fi

ssh_add_stdout="$(mktemp)"
ssh_add_stderr="$(mktemp)"
ssh_add_exit=0

timeout 5 ssh-add -L >"$ssh_add_stdout" 2>"$ssh_add_stderr" || ssh_add_exit=$?

if [[ "$ssh_add_exit" -eq 0 ]]; then
  key_lines="$(<"$ssh_add_stdout")"
  key_line=""
  matched_comment=""

  for ssh_key_comment in "${ssh_key_comments[@]}"; do
    key_line="$(printf '%s\n' "$key_lines" | grep -m1 "$ssh_key_comment" || true)"
    if [[ -n "$key_line" ]]; then
      matched_comment="$ssh_key_comment"
      break
    fi
  done

  if [[ -z "$key_line" ]]; then
    key_line="$(printf '%s\n' "$key_lines" | grep -m1 '^ssh-' || true)"
  fi

  if [[ -n "$key_line" ]]; then
    printf '%s\n' "$key_line" >"$ssh_pub_key_file"
    chmod 600 "$ssh_pub_key_file"
    if [[ -z "$matched_comment" ]]; then
      echo "WARN: preferred key comments not found; using first SSH agent key instead." >&2
    fi
    echo "INFO: refreshed $ssh_pub_key_file from SSH agent." >&2
  else
    echo "WARN: SSH agent returned no usable public keys for Ansible." >&2
    rm -f "$ssh_add_stdout" "$ssh_add_stderr"
    exit 1
  fi
else
  if [[ "$ssh_add_exit" -eq 1 ]]; then
    echo "WARN: SSH agent available but has no keys to export for Ansible." >&2
  elif [[ "$ssh_add_exit" -eq 124 ]]; then
    echo "WARN: ssh-add -L timed out; SSH agent appears unhealthy." >&2
    echo "WARN: Reopen or rebuild the devcontainer to refresh SSH agent forwarding." >&2
  else
    ssh_add_error="$(tr '\n' ' ' <"$ssh_add_stderr" | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')"
    if [[ -n "$ssh_add_error" ]]; then
      echo "WARN: SSH agent socket is not usable (ssh-add -L exit $ssh_add_exit): $ssh_add_error" >&2
    else
      echo "WARN: SSH agent socket is not usable (ssh-add -L exit $ssh_add_exit)." >&2
    fi
    echo "WARN: Reopen or rebuild the devcontainer to refresh SSH agent forwarding." >&2
  fi
  rm -f "$ssh_add_stdout" "$ssh_add_stderr"
  exit 1
fi

rm -f "$ssh_add_stdout" "$ssh_add_stderr"
EOF

  chmod 700 "$helper_path"
}

mkdir -p \
  /home/vscode/persistent-data \
  /home/vscode/persistent-data/opencode/{config,cache,share,state} \
  "$HOME/.cache" \
  "$HOME/.local/share" \
  "$HOME/.local/state" \
  "$HOME/.local/bin" \
  "$HOME/.ssh"

ensure_agent_of_empires_persistence_link
ensure_claude_persistence_links
ensure_beads_persistence_mounts "$workspace_root"
install_claude_managed_asset_links
register_claude_mcp_servers

if ! install_beads_kanban_bd_fixes_vscode_extension; then
  echo "WARN: Beads Kanban BD Fixes VSIX install failed; continuing postStart." >&2
fi

if [[ -f /home/vscode/.host-dotfiles/.config/agent-of-empires/config.toml ]]; then
  install -m 0644 /home/vscode/.host-dotfiles/.config/agent-of-empires/config.toml \
    /home/vscode/persistent-data/agent-of-empires/config.toml
else
  echo "WARN: Agent of Empires config not found; keeping existing config." >&2
fi

ensure_opencode_persistence_links
materialize_opencode_managed_assets

if [[ -f /tmp/host-container-configs/opencode.env ]]; then
  persisted_opencode_profiles="$(read_opencode_profiles_from_env_file /home/vscode/persistent-data/opencode/config/opencode.env || true)"
  install -m 0600 /tmp/host-container-configs/opencode.env \
    /home/vscode/persistent-data/opencode/config/opencode.env

  if [[ -n "${persisted_opencode_profiles:-}" ]]; then
    if normalized_persisted_profiles="$(normalize_opencode_profiles "$persisted_opencode_profiles")"; then
      if ! write_opencode_profiles_to_env_file /home/vscode/persistent-data/opencode/config/opencode.env "$normalized_persisted_profiles"; then
        echo "WARN: Failed preserving OpenCode profile settings in refreshed opencode.env." >&2
      fi
    else
      echo "WARN: Skipping invalid persisted OpenCode profile settings." >&2
    fi
  fi
else
  echo "WARN: /tmp/host-container-configs/opencode.env not found; run ./assets/sync-devcontainer-all.sh or ./assets/render-container-configs.sh on the host, then restart the container. Keeping existing env file if present." >&2
fi

load_opencode_env_file

if [[ -f /tmp/host-homelab-devcontainer/opencode-sync-workspace-overrides.sh ]]; then
  install -m 0755 /tmp/host-homelab-devcontainer/opencode-sync-workspace-overrides.sh \
    "$HOME/.local/bin/opencode-sync-workspace-overrides"
  if ! "$HOME/.local/bin/opencode-sync-workspace-overrides" "${OPENCODE_PROFILES:-${OPENCODE_PROFILE:-defaults}}" "$workspace_root"; then
    echo "WARN: OpenCode workspace override sync failed." >&2
  fi
else
  echo "WARN: OpenCode workspace override helper not found; skipping workspace override sync." >&2
fi

if [[ -f /tmp/host-dotfiles/dot_markdownlint-cli2.jsonc ]]; then
  ln -sfn /tmp/host-dotfiles/dot_markdownlint-cli2.jsonc \
    "$HOME/.markdownlint-cli2.jsonc"
else
  echo "WARN: /tmp/host-dotfiles/dot_markdownlint-cli2.jsonc not found; keeping existing markdownlint config." >&2
fi

if [[ -f /tmp/host-dotfiles/dot_local/share/git-helpers.zsh ]]; then
  install -m 0644 /tmp/host-dotfiles/dot_local/share/git-helpers.zsh \
    "$HOME/.local/share/git-helpers.zsh"
fi

install_sset_helper

if ! "$HOME/.local/bin/sset"; then
  echo "WARN: sset refresh failed; Ansible may not be able to use SSH keys." >&2
  echo "WARN: If this persists, rerun sset or reopen/rebuild the container." >&2
fi

SAFE_DIRS_FILE="/home/vscode/persistent-data/git/safe-dirs"
if command -v git >/dev/null 2>&1; then
  mkdir -p "$(dirname "$SAFE_DIRS_FILE")"
  SAFE_DIRS_TMP="$(mktemp "$(dirname "$SAFE_DIRS_FILE")/safe-dirs.XXXXXX")"
  trap 'rm -f "$SAFE_DIRS_TMP"' EXIT

  safe_dirs_count=0
  shopt -s nullglob
  for workspace in /workspaces/*; do
    [[ -d "$workspace" ]] || continue
    git config --file "$SAFE_DIRS_TMP" --add safe.directory "$workspace"
    safe_dirs_count=$((safe_dirs_count + 1))
    if [[ -d "$workspace/.git" ]]; then
      git config --file "$SAFE_DIRS_TMP" --add safe.directory "$workspace/.git"
      safe_dirs_count=$((safe_dirs_count + 1))
    fi
  done
  shopt -u nullglob

  if [[ "$safe_dirs_count" -gt 0 ]]; then
    mv -f "$SAFE_DIRS_TMP" "$SAFE_DIRS_FILE"
  else
    rm -f "$SAFE_DIRS_TMP"
  fi

  trap - EXIT
fi
