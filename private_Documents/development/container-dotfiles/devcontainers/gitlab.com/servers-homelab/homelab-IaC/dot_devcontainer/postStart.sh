#!/usr/bin/env bash
set -Eeuo pipefail

mkdir -p \
  /home/vscode/persistent-data \
  "$HOME/.claude-code-router/logs" \
  /home/vscode/persistent-data/opencode/{config,cache,share,state} \
  "$HOME/.config" \
  "$HOME/.cache" \
  "$HOME/.local/share" \
  "$HOME/.local/state" \
  "$HOME/.ssh"

ln -sfn /home/vscode/persistent-data/opencode/config "$HOME/.config/opencode"
ln -sfn /home/vscode/persistent-data/opencode/cache "$HOME/.cache/opencode"
ln -sfn /home/vscode/persistent-data/opencode/share "$HOME/.local/share/opencode"
ln -sfn /home/vscode/persistent-data/opencode/state "$HOME/.local/state/opencode"
ln -sf /tmp/opencode.jsonc "$HOME/.config/opencode/opencode.jsonc"
ln -sf /tmp/opencode.env "$HOME/.config/opencode/opencode.env"

ssh_key_comment="root_terraform_ansible"
ssh_pub_key_file="$HOME/.ssh/root_terraform_ansible.pub"
if [[ -S "${SSH_AUTH_SOCK:-}" ]]; then
  key_lines="$(ssh-add -L 2>/dev/null || true)"
  key_line="$(printf '%s\n' "$key_lines" | grep -m1 "$ssh_key_comment" || true)"

  if [[ -z "$key_line" ]]; then
    key_line="$(printf '%s\n' "$key_lines" | grep -m1 '^ssh-' || true)"
  fi

  if [[ -n "$key_line" ]]; then
    printf '%s\n' "$key_line" >"$ssh_pub_key_file"
    chmod 600 "$ssh_pub_key_file"
    if [[ "$key_line" != *"$ssh_key_comment"* ]]; then
      echo "WARN: '$ssh_key_comment' not found; using first SSH agent key instead." >&2
    fi
  else
    echo "WARN: SSH agent available but has no keys to export for Ansible." >&2
  fi
else
  echo "WARN: SSH_AUTH_SOCK is missing or not a socket: ${SSH_AUTH_SOCK:-<unset>}" >&2
fi

CCR_BIN="$(command -v ccr || true)"
echo "postStart $(date -Is) HOME=$HOME CCR_BIN=$CCR_BIN PATH=$PATH" >>/home/vscode/persistent-data/poststart-debug.log
PORT="${CCR_PORT:-}"
if [[ -n "$CCR_BIN" && -n "$PORT" ]] && ! ss -ltn | grep -q ":${PORT} "; then
  setsid -f "$CCR_BIN" start </dev/null >>/home/vscode/persistent-data/ccr.log 2>&1
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
