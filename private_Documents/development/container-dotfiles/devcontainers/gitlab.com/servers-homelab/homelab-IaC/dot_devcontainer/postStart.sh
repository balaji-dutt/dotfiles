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
  key_line="$(ssh-add -L 2>/dev/null | grep -m1 "$ssh_key_comment" || true)"
  if [[ -n "$key_line" ]]; then
    printf '%s\n' "$key_line" >"$ssh_pub_key_file"
    chmod 600 "$ssh_pub_key_file"
  fi
fi

CCR_BIN="$(command -v ccr || true)"
echo "postStart $(date -Is) HOME=$HOME CCR_BIN=$CCR_BIN PATH=$PATH" >>/home/vscode/persistent-data/poststart-debug.log
PORT="${CCR_PORT:-}"
if [[ -n "$CCR_BIN" && -n "$PORT" ]] && ! ss -ltn | grep -q ":${PORT} "; then
  setsid -f "$CCR_BIN" start </dev/null >>/home/vscode/persistent-data/ccr.log 2>&1
fi
