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

ssh_key_comments=("root_terraform_ansible" "terraform-ansible")
ssh_pub_key_file="$HOME/.ssh/root_terraform_ansible.pub"
if [[ -S "${SSH_AUTH_SOCK:-}" ]]; then
  ssh_add_stdout="$(mktemp)"
  ssh_add_stderr="$(mktemp)"
  ssh_add_exit=0
  ssh-add -L >"$ssh_add_stdout" 2>"$ssh_add_stderr" || ssh_add_exit=$?
  if [[ "$ssh_add_exit" -eq 0 ]]; then
    key_lines="$(cat "$ssh_add_stdout")"
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
    else
      echo "WARN: SSH agent returned no usable public keys for Ansible." >&2
    fi
  else
    if [[ "$ssh_add_exit" -eq 1 ]]; then
      echo "WARN: SSH agent available but has no keys to export for Ansible." >&2
    else
      ssh_add_error="$(tr '\n' ' ' <"$ssh_add_stderr" | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')"
      if [[ -n "$ssh_add_error" ]]; then
        echo "WARN: SSH agent socket is not usable (ssh-add -L exit $ssh_add_exit): $ssh_add_error" >&2
      else
        echo "WARN: SSH agent socket is not usable (ssh-add -L exit $ssh_add_exit)." >&2
      fi
    fi
  fi
  rm -f "$ssh_add_stdout" "$ssh_add_stderr"
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
