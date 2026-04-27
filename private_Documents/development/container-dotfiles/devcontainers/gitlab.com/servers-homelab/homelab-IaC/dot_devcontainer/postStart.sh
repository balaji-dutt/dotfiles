#!/usr/bin/env bash
set -Eeuo pipefail

workspace_root="${1:-}"
if [[ -z "$workspace_root" ]] && command -v git >/dev/null 2>&1; then
  workspace_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
workspace_root="${workspace_root:-$PWD}"

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

read_opencode_profile_from_env_file() {
  local env_file
  env_file="$1"

  [[ -r "$env_file" ]] || return 0

  awk '
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

write_opencode_profile_to_env_file() {
  local env_file profile tmp_file
  env_file="$1"
  profile="$2"

  [[ -n "$profile" ]] || return 0

  tmp_file="$(mktemp "${env_file}.XXXXXX")"

  if [[ -r "$env_file" ]]; then
    if ! awk -v profile="$profile" '
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
    printf 'OPENCODE_PROFILE=%s\n' "$profile" > "$tmp_file"
  fi

  if ! mv -f "$tmp_file" "$env_file"; then
    rm -f "$tmp_file"
    return 1
  fi

  chmod 600 "$env_file" 2>/dev/null || true
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
ssh_pub_key_file="$HOME/.ssh/root_terraform_ansible.pub"
ssh_key_comments=("root_terraform_ansible" "terraform-ansible")

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
  echo "WARN: Relay recovery requires host-side initializeCommand; rebuild/reopen the container." >&2
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
    echo "WARN: Relay recovery requires host-side initializeCommand; rebuild/reopen the container." >&2
  else
    ssh_add_error="$(tr '\n' ' ' <"$ssh_add_stderr" | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')"
    if [[ -n "$ssh_add_error" ]]; then
      echo "WARN: SSH agent socket is not usable (ssh-add -L exit $ssh_add_exit): $ssh_add_error" >&2
    else
      echo "WARN: SSH agent socket is not usable (ssh-add -L exit $ssh_add_exit)." >&2
    fi
    echo "WARN: Relay recovery requires host-side initializeCommand; rebuild/reopen the container." >&2
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

if [[ -f /home/vscode/.host-dotfiles/.config/agent-of-empires/config.toml ]]; then
  install -m 0644 /home/vscode/.host-dotfiles/.config/agent-of-empires/config.toml \
    /home/vscode/persistent-data/agent-of-empires/config.toml
else
  echo "WARN: Agent of Empires config not found; keeping existing config." >&2
fi

ln -sfn /home/vscode/persistent-data/opencode/config "$HOME/.config/opencode"
ln -sfn /home/vscode/persistent-data/opencode/cache "$HOME/.cache/opencode"
ln -sfn /home/vscode/persistent-data/opencode/share "$HOME/.local/share/opencode"
ln -sfn /home/vscode/persistent-data/opencode/state "$HOME/.local/state/opencode"
if [[ -f /home/vscode/.host-dotfiles/.config/opencode/opencode.jsonc ]]; then
  install -m 0644 /home/vscode/.host-dotfiles/.config/opencode/opencode.jsonc \
    /home/vscode/persistent-data/opencode/config/opencode.jsonc
else
  echo "WARN: OpenCode config not found; keeping existing config." >&2
fi

if [[ -f /home/vscode/.host-dotfiles/.config/opencode/opencode-notifier.json ]]; then
  install -m 0644 /home/vscode/.host-dotfiles/.config/opencode/opencode-notifier.json \
    /home/vscode/persistent-data/opencode/config/opencode-notifier.json
else
  echo "WARN: OpenCode notifier config not found; keeping existing config." >&2
fi

if [[ -d "$HOME/.host-dotfiles/.config/opencode/profiles" ]]; then
  mkdir -p "$HOME/persistent-data/opencode/config/profiles"
  rm -rf "$HOME/persistent-data/opencode/config/profiles/"*
  cp -R "$HOME/.host-dotfiles/.config/opencode/profiles/." \
    "$HOME/persistent-data/opencode/config/profiles/"
else
  echo "WARN: OpenCode profile config directory not found; keeping existing profile configs." >&2
fi

if [[ -f /tmp/host-container-configs/opencode.env ]]; then
  persisted_opencode_profile="$(read_opencode_profile_from_env_file /home/vscode/persistent-data/opencode/config/opencode.env || true)"
  install -m 0600 /tmp/host-container-configs/opencode.env \
    /home/vscode/persistent-data/opencode/config/opencode.env

  if [[ -n "${persisted_opencode_profile:-}" ]]; then
    if ! write_opencode_profile_to_env_file /home/vscode/persistent-data/opencode/config/opencode.env "$persisted_opencode_profile"; then
      echo "WARN: Failed preserving OPENCODE_PROFILE in refreshed opencode.env." >&2
    fi
  fi
else
  echo "WARN: /tmp/host-container-configs/opencode.env not found; keeping existing env file." >&2
fi

load_opencode_env_file

if [[ -f /tmp/host-homelab-devcontainer/opencode-sync-workspace-overrides.sh ]]; then
  install -m 0755 /tmp/host-homelab-devcontainer/opencode-sync-workspace-overrides.sh \
    "$HOME/.local/bin/opencode-sync-workspace-overrides"
  if ! "$HOME/.local/bin/opencode-sync-workspace-overrides" "${OPENCODE_PROFILE:-chatgpt}" "$workspace_root"; then
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
  echo "WARN: If this persists, rebuild/reopen the container to rerun initializeCommand." >&2
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
