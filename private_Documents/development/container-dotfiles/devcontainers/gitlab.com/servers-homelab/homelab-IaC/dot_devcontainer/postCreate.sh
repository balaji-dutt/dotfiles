#!/usr/bin/env bash
set -Eeuo pipefail

# --- Logging / debugging ---
LOG_FILE="${LOG_FILE:-/tmp/postCreate.log}"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

timestamp() { date +"%Y-%m-%d %H:%M:%S%z"; }
step() { echo; echo "==== [$(timestamp)] STEP: $* ===="; }
done_step() { echo "==== [$(timestamp)] DONE: $* ===="; }

on_error() {
  local exit_code=$?
  echo
  echo "!!!! [$(timestamp)] ERROR (exit $exit_code) at line ${BASH_LINENO[0]}: ${BASH_COMMAND}"
  echo "!!!! See log: $LOG_FILE"
  exit "$exit_code"
}
trap on_error ERR

# Print commands as they run in debug mode (very helpful for "where did it hang?")
[[ "${POSTCREATE_DEBUG:-0}" == "1" ]] && set -x

# Make package installs non-interactive
export DEBIAN_FRONTEND=noninteractive
export CI=1

# --- 1) Permissions / base packages ---
step "Fix ownership for persistent-data"
if [[ -d /home/vscode/persistent-data ]]; then
  sudo chown -R vscode:vscode /home/vscode/persistent-data
fi
done_step "Fix ownership for persistent-data"

step "apt-get update"
sudo apt-get update
done_step "apt-get update"

step "Install locales + generate en_GB.UTF-8"
sudo apt-get install -y locales
# Un-comment en_GB.UTF-8 line if present
if grep -qE '^\s*#\s*en_GB\.UTF-8\s+UTF-8' /etc/locale.gen; then
  sudo sed -i 's/^\s*#\s*\(en_GB\.UTF-8\s\+UTF-8\)/\1/' /etc/locale.gen
fi
sudo locale-gen
done_step "Install locales + generate en_GB.UTF-8"

step "Install python3-pip + python3-venv"
sudo apt-get install -y python3-pip python3-venv
done_step "Install python3-pip + python3-venv"

# --- 2) uv install ---
step "Install uv (user) and ensure PATH"
python3 -m pip install --user "uv==0.9.18"
export PATH="$HOME/.local/bin:$PATH"
hash -r
command -v uv
uv --version
done_step "Install uv (user) and ensure PATH"

# --- 3) NVM + Node ---
step "Setup NVM_DIR and ensure nvm is installed"
if [[ -s "/usr/local/share/nvm/nvm.sh" ]]; then
  export NVM_DIR="/usr/local/share/nvm"
else
  export NVM_DIR="$HOME/.nvm"

  # If nvm not installed, install it.
  if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
    : "${NVM_VERSION:?NVM_VERSION env var is required when nvm is not preinstalled}"
    # Avoid profile modifications in container
    PROFILE=/dev/null bash -c "curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v${NVM_VERSION}/install.sh | bash"
  fi
fi

# shellcheck disable=SC1090
. "$NVM_DIR/nvm.sh"
nvm --version
done_step "Setup NVM_DIR and ensure nvm is installed"

step "Install Node version: ${NODE_VERSION:-<missing>}"
: "${NODE_VERSION:?NODE_VERSION env var is required}"
nvm install "${NODE_VERSION}"
node -v
npm -v
done_step "Install Node version: ${NODE_VERSION}"

# --- 4) Global npm packages ---
step "Install global npm packages from /tmp/host-homelab-configs/npm_packages.txt"
if [[ -f /tmp/host-homelab-configs/npm_packages.txt ]]; then
  # helpful visibility
  echo "--- npm packages file ---"
  sed -n '1,200p' /tmp/host-homelab-configs/npm_packages.txt || true
  echo "-------------------------"

  while IFS= read -r pkg || [[ -n "${pkg:-}" ]]; do
    [[ -z "${pkg// /}" ]] && continue
    echo "[npm] installing: $pkg"
    npm install -g "$pkg"
  done < /tmp/host-homelab-configs/npm_packages.txt
else
  echo "No /tmp/host-homelab-configs/npm_packages.txt found; skipping."
fi
done_step "Install global npm packages"

# Re-export in case npm changed shell hash / PATH during install
step "Re-ensure PATH for user installs"
export PATH="$HOME/.local/bin:$PATH"
hash -r
done_step "Re-ensure PATH for user installs"

# --- 5) uv tools ---
step "Install uv tools from /tmp/host-homelab-configs/uv_tools.txt"
if [[ -f /tmp/host-homelab-configs/uv_tools.txt ]]; then
  echo "--- uv tools file ---"
  sed -n '1,200p' /tmp/host-homelab-configs/uv_tools.txt || true
  echo "----------------------"

  while IFS= read -r tool || [[ -n "${tool:-}" ]]; do
    [[ -z "${tool// /}" ]] && continue
    echo "[uv] installing tool: $tool"
    uv tool install "$tool"
  done < /tmp/host-homelab-configs/uv_tools.txt
else
  echo "No /tmp/host-homelab-configs/uv_tools.txt found; skipping."
fi
done_step "Install uv tools"

# --- 6) Claude symlinks / setup ---
step "Setup Claude config symlinks and permissions"
mkdir -p /home/vscode/.claude/commands /home/vscode/.claude-code-router/auth

ln -sf /tmp/host-claude/private_settings.json /home/vscode/.claude/settings.json || true
ln -sf /tmp/host-container-configs/config.json /home/vscode/.claude-code-router/config.json || true
ln -sf /tmp/host-claude-code-router/plugins /home/vscode/.claude-code-router/plugins || true
ln -sf /tmp/host-claude/AGENTS.md /home/vscode/.claude/AGENTS.md || true
ln -sf /home/vscode/.claude/AGENTS.md /home/vscode/.claude/CLAUDE.md || true
ln -sf /tmp/host-claude/commands/todo.md /home/vscode/.claude/commands/todo.md || true
ln -sf /tmp/host-claude/executable_commit-docs.sh /home/vscode/.claude/commit-docs.sh || true

chmod +x /home/vscode/.claude/commit-docs.sh || true
done_step "Setup Claude config symlinks and permissions"

# --- 7) superclaude (non-interactive) ---
step "superclaude install (non-interactive if present)"
if command -v superclaude >/dev/null 2>&1; then
  # Common hang source: interactive installer. Force non-interactive.
  superclaude install --force
else
  echo "superclaude not found; skipping."
fi
done_step "superclaude install"

# --- 8) Source container env + dotfiles ---
step "Source /tmp/host-container-configs/container_env (if present)"
if [[ -f /tmp/host-container-configs/container_env ]]; then
  # shellcheck disable=SC1091
  source /tmp/host-container-configs/container_env
else
  echo "/tmp/host-container-configs/container_env not found; skipping."
fi
done_step "Source /tmp/host-container-configs/container_env (if present)"

step "Prime OpenCode persistent-data symlinks before install"
mkdir -p \
  /home/vscode/persistent-data/opencode/{config,cache,share,state} \
  "$HOME/.config" \
  "$HOME/.cache" \
  "$HOME/.local/share" \
  "$HOME/.local/state"

ln -sfn /home/vscode/persistent-data/opencode/config "$HOME/.config/opencode"
ln -sfn /home/vscode/persistent-data/opencode/cache "$HOME/.cache/opencode"
ln -sfn /home/vscode/persistent-data/opencode/share "$HOME/.local/share/opencode"
ln -sfn /home/vscode/persistent-data/opencode/state "$HOME/.local/state/opencode"
done_step "Prime OpenCode persistent-data symlinks before install"

step "Run host dotfiles installer (if present)"
SRC=/home/vscode/.host-dotfiles
if [[ -d "$SRC" ]]; then
  if [[ -f "$SRC/install.sh" ]]; then
    # If your install.sh can be interactive, pass whatever non-interactive flags it supports.
    # We keep your TAVILY_API_KEY propagation behavior.
    TAVILY_API_KEY="${TAVILY_API_KEY:-}"
    echo "Running $SRC/install.sh"
    TAVILY_API_KEY="$TAVILY_API_KEY" bash "$SRC/install.sh"
  else
    echo "Copying dotfiles from $SRC -> $HOME"
    cp -R "$SRC"/. "$HOME"/
  fi
else
  echo "$SRC not found; skipping."
fi
done_step "Run host dotfiles installer (if present)"

# --- 9) Antidote ---
step "Load Antidote (if present)"
if [[ -f /opt/antidote/antidote.zsh ]]; then
  zsh -lc 'source /opt/antidote/antidote.zsh && antidote load' || true
else
  echo "Antidote not present; skipping."
fi
done_step "Load Antidote (if present)"

step "postCreate complete"
echo "Log saved to: $LOG_FILE"
done_step "postCreate complete"
