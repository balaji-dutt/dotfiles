#!/usr/bin/env bash
set -Eeuo pipefail

# --- Logging / debugging ---
LOG_FILE="${LOG_FILE:-/tmp/postCreate.log}"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

timestamp() { date +"%Y-%m-%d %H:%M:%S%z"; }
step() { echo; echo "==== [$(timestamp)] STEP: $* ===="; }
done_step() { echo "==== [$(timestamp)] DONE: $* ===="; }

trim_whitespace() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

bootstrap_local_git_metadata() {
  local workspace="$1"
  local source_git_dir="/tmp/host-workspace-git"
  local target_git_dir="$workspace/.git"

  # macOS-only mount; no-op on other hosts.
  if [[ ! -d "$source_git_dir" ]]; then
    return 0
  fi

  if [[ ! -d "$workspace" ]]; then
    echo "ERROR: Workspace path does not exist: $workspace" >&2
    return 1
  fi

  mkdir -p "$target_git_dir"
  if [[ ! -w "$target_git_dir" ]]; then
    sudo chown -R "$USER:$USER" "$target_git_dir"
  fi

  if [[ -f "$target_git_dir/HEAD" ]]; then
    return 0
  fi

  if [[ ! -f "$source_git_dir/HEAD" ]]; then
    echo "ERROR: Source Git metadata is missing HEAD: $source_git_dir" >&2
    return 1
  fi

  cp -a "$source_git_dir"/. "$target_git_dir"/

  if [[ ! -f "$target_git_dir/HEAD" ]]; then
    echo "ERROR: Seeded Git metadata is incomplete at: $target_git_dir" >&2
    return 1
  fi

  if command -v git >/dev/null 2>&1; then
    if ! git -C "$workspace" rev-parse --verify HEAD >/dev/null 2>&1; then
      echo "ERROR: Seeded Git metadata failed HEAD verification in: $workspace" >&2
      return 1
    fi
  fi

  echo "Initialized local Git metadata volume for workspace: $workspace"
}

ensure_beads_persistence_mounts() {
  local workspace beads_dir shared_server_dir

  workspace="$1"
  beads_dir="$workspace/.beads"
  shared_server_dir="${BEADS_SHARED_SERVER_DIR:-$HOME/.beads/shared-server}"

  if [[ ! -d "$workspace" ]]; then
    echo "ERROR: Workspace path does not exist: $workspace" >&2
    return 1
  fi

  mkdir -p "$beads_dir"

  if [[ ! -w "$beads_dir" ]]; then
    sudo chown "$USER:$USER" "$beads_dir"
  fi

  mkdir -p "$shared_server_dir"
  if [[ ! -w "$shared_server_dir" ]]; then
    sudo chown -R "$USER:$USER" "$shared_server_dir"
  fi

  if [[ ! -f "$beads_dir/config.yaml" && ! -f "$beads_dir/issues.jsonl" ]]; then
    echo "WARN: Beads project metadata not found in $beads_dir; run bd init in the workspace if needed." >&2
  fi

  echo "Beads shared-server state directory: $shared_server_dir"
}

install_custom_ca_certificates() {
  local cert_dir certfiles_raw cert_file src_file dest_file dest_name
  local changed=0
  local -a cert_files=()

  cert_dir="${CERTPATH:-/home/vscode/development/keys}"
  certfiles_raw="${CERTFILES_RAW:-}"

  if [[ -z "$certfiles_raw" ]]; then
    echo "No CERTFILES_RAW configured; skipping custom CA install."
    return 0
  fi

  if [[ ! -d "$cert_dir" ]]; then
    echo "ERROR: Custom CA directory does not exist: $cert_dir" >&2
    return 1
  fi

  IFS=',' read -r -a cert_files <<< "$certfiles_raw"
  for cert_file in "${cert_files[@]}"; do
    cert_file="$(trim_whitespace "$cert_file")"
    [[ -z "$cert_file" ]] && continue

    src_file="$cert_dir/$cert_file"
    if [[ ! -f "$src_file" ]]; then
      echo "ERROR: Custom CA file not found: $src_file" >&2
      return 1
    fi

    dest_name="$(basename "$cert_file")"
    case "$dest_name" in
      *.pem)
        dest_name="${dest_name%.pem}.crt"
        ;;
      *.cert)
        dest_name="${dest_name%.cert}.crt"
        ;;
    esac
    dest_file="/usr/local/share/ca-certificates/$dest_name"

    if [[ -f "$dest_file" ]] && cmp -s "$src_file" "$dest_file"; then
      continue
    fi

    sudo install -m 0644 "$src_file" "$dest_file"
    changed=1
  done

  if [[ "$changed" -eq 1 ]]; then
    sudo update-ca-certificates
  else
    echo "Custom CAs already up to date; no trust-store changes."
  fi
}

ensure_claude_persistence_links() {
  local claude_persist_dir claude_config_persist_dir
  local claude_config_dir claude_home_config claude_home_config_persist

  claude_persist_dir="/home/vscode/persistent-data/claude"
  claude_config_persist_dir="$claude_persist_dir/config"
  claude_config_dir="$HOME/.claude"
  claude_home_config="$HOME/.claude.json"
  claude_home_config_persist="$claude_persist_dir/.claude.json"

  mkdir -p "$claude_config_persist_dir"

  if [[ -L "$claude_config_dir" ]]; then
    ln -sfn "$claude_config_persist_dir" "$claude_config_dir"
  else
    if [[ -d "$claude_config_dir" ]]; then
      if [[ -n "$(find "$claude_config_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
        if [[ -n "$(find "$claude_config_persist_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
          cp -an "$claude_config_dir"/. "$claude_config_persist_dir"/
        else
          cp -a "$claude_config_dir"/. "$claude_config_persist_dir"/
        fi
      fi
      rm -rf "$claude_config_dir"
    elif [[ -e "$claude_config_dir" ]]; then
      rm -f "$claude_config_dir"
    fi

    ln -sfn "$claude_config_persist_dir" "$claude_config_dir"
  fi

  if [[ -L "$claude_home_config" ]]; then
    ln -sfn "$claude_home_config_persist" "$claude_home_config"
  else
    if [[ -f "$claude_home_config" ]]; then
      if [[ ! -e "$claude_home_config_persist" ]] || \
        ! cmp -s "$claude_home_config" "$claude_home_config_persist"; then
        cp -a "$claude_home_config" "$claude_home_config_persist"
      fi
      rm -f "$claude_home_config"
    elif [[ -e "$claude_home_config" ]]; then
      rm -rf "$claude_home_config"
    fi

    ln -sfn "$claude_home_config_persist" "$claude_home_config"
  fi
}

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

WORKSPACE_PATH="${1:-$PWD}"
step "Bootstrap local Git metadata volume (if mounted)"
bootstrap_local_git_metadata "$WORKSPACE_PATH"
done_step "Bootstrap local Git metadata volume (if mounted)"

# --- 1) Permissions / base packages ---
step "Fix ownership for persistent-data"
if [[ -d /home/vscode/persistent-data ]]; then
  sudo chown -R vscode:vscode /home/vscode/persistent-data
fi
done_step "Fix ownership for persistent-data"

step "Prepare Beads persistence mounts"
ensure_beads_persistence_mounts "$WORKSPACE_PATH"
done_step "Prepare Beads persistence mounts"

step "apt-get update"
sudo apt-get update
done_step "apt-get update"

step "Install custom CA certificates (if configured)"
install_custom_ca_certificates
done_step "Install custom CA certificates (if configured)"

step "Install locales + generate en_GB.UTF-8"
sudo apt-get install -y locales
# Un-comment en_GB.UTF-8 line if present
if grep -qE '^\s*#\s*en_GB\.UTF-8\s+UTF-8' /etc/locale.gen; then
  sudo sed -i 's/^\s*#\s*\(en_GB\.UTF-8\s\+UTF-8\)/\1/' /etc/locale.gen
fi
sudo locale-gen
done_step "Install locales + generate en_GB.UTF-8"

step "Install python3-pip + python3-venv + ripgrep"
sudo apt-get install -y python3-pip python3-venv ripgrep unzip
done_step "Install python3-pip + python3-venv + ripgrep"

# --- 2) uv install ---
step "Install uv (user) and ensure PATH"
python3 -m pip install --user "uv==0.11.8" # renovate: datasource=pypi depName=uv versioning=pep440
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
    # Keep CI=1 for the noninteractive bootstrap, but do not pass it to npm
    # lifecycle scripts. Some npm tools, such as @beads/bd, skip native binary
    # downloads when CI is set and leave only a broken JavaScript shim behind.
    env -u CI npm install -g "$pkg"

    if [[ "$pkg" == @beads/bd@* ]]; then
      bd version
      command -v dolt
      dolt version
    fi
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

# --- 5b) MCP server binaries ---
step "Install MCP server binaries (hop)"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64)  HOP_ARCH="amd64" ;;
  aarch64) HOP_ARCH="arm64" ;;
  *)       echo "WARN: Unsupported architecture $ARCH for MCP binaries; skipping." >&2 ;;
esac

if [[ -n "${HOP_VERSION:-}" && -n "${HOP_ARCH:-}" ]]; then
  echo "[mcp] installing hop v${HOP_VERSION} (${HOP_ARCH})"
  curl -fsSL "https://github.com/danmartuszewski/hop/releases/download/v${HOP_VERSION}/hop_linux_${HOP_ARCH}.tar.gz" \
    | sudo tar xz -C /usr/local/bin hop
  hop version || echo "WARN: hop version check failed"
else
  echo "WARN: HOP_VERSION not set; skipping hop install."
fi

if [[ -n "${TF_MCP_VERSION:-}" ]]; then
  echo "[mcp] building terraform-mcp-server v${TF_MCP_VERSION} (no pre-built binaries available)"
  TF_MCP_GO_VERSION="1.24.3"
  TF_MCP_GO_ARCH="${HOP_ARCH:-amd64}"
  TF_MCP_GOROOT="/tmp/go-tf-mcp"
  TF_MCP_GOPATH="/tmp/go-tf-mcp-path"

  curl -fsSL "https://go.dev/dl/go${TF_MCP_GO_VERSION}.linux-${TF_MCP_GO_ARCH}.tar.gz" \
    | tar xz -C /tmp
  mv /tmp/go "$TF_MCP_GOROOT"

  GOROOT="$TF_MCP_GOROOT" GOPATH="$TF_MCP_GOPATH" GOBIN="$TF_MCP_GOPATH/bin" \
    "$TF_MCP_GOROOT/bin/go" install \
    "github.com/hashicorp/terraform-mcp-server/cmd/terraform-mcp-server@v${TF_MCP_VERSION}"

  sudo install -m 0755 "$TF_MCP_GOPATH/bin/terraform-mcp-server" /usr/local/bin/terraform-mcp-server
  terraform-mcp-server --help 2>&1 | head -1 || true

  sudo rm -rf "$TF_MCP_GOROOT" "$TF_MCP_GOPATH"
  echo "[mcp] cleaned up temporary Go toolchain"
else
  echo "WARN: TF_MCP_VERSION not set; skipping terraform-mcp-server install."
fi
done_step "Install MCP server binaries"

# --- 6) Claude symlinks / setup ---
step "Setup Claude config symlinks and permissions"
ensure_claude_persistence_links
mkdir -p /home/vscode/.claude/commands

ln -sf /tmp/host-claude/private_settings.json /home/vscode/.claude/settings.json || true
ln -sf /tmp/host-claude/AGENTS.md /home/vscode/.claude/AGENTS.md || true
ln -sf /home/vscode/.claude/AGENTS.md /home/vscode/.claude/CLAUDE.md || true
ln -sf /tmp/host-claude/commands/todo.md /home/vscode/.claude/commands/todo.md || true

if [[ -f /tmp/host-claude/executable_commit-docs.sh ]]; then
  rm -f /home/vscode/.claude/commit-docs.sh
  install -m 0755 /tmp/host-claude/executable_commit-docs.sh /home/vscode/.claude/commit-docs.sh
else
  echo "WARN: Claude commit-docs helper not found; keeping existing helper." >&2
fi
done_step "Setup Claude config symlinks and permissions"

# --- 7) Source container env + dotfiles ---
step "Source /tmp/host-container-configs/container_env (if present)"
if [[ -f /tmp/host-container-configs/container_env ]]; then
  # shellcheck disable=SC1091
  source /tmp/host-container-configs/container_env
else
  echo "/tmp/host-container-configs/container_env not found; skipping."
fi
done_step "Source /tmp/host-container-configs/container_env (if present)"

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

step "Prime OpenCode/AoE persistent-data symlinks before install"
mkdir -p \
  /home/vscode/persistent-data/opencode/{config,cache,share,state} \
  "$HOME/.cache" \
  "$HOME/.local/share" \
  "$HOME/.local/state"

ensure_agent_of_empires_persistence_link
ln -sfn /home/vscode/persistent-data/opencode/config "$HOME/.config/opencode"
ln -sfn /home/vscode/persistent-data/opencode/cache "$HOME/.cache/opencode"
ln -sfn /home/vscode/persistent-data/opencode/share "$HOME/.local/share/opencode"
ln -sfn /home/vscode/persistent-data/opencode/state "$HOME/.local/state/opencode"
done_step "Prime OpenCode/AoE persistent-data symlinks before install"

step "Run host dotfiles installer (if present)"
SRC=/home/vscode/.host-dotfiles
if [[ -d "$SRC" ]]; then
  if [[ -f "$SRC/install.sh" ]]; then
    # If your install.sh can be interactive, pass whatever non-interactive flags it supports.
    # Keep secret propagation behavior for installer-time env vars.
    OP_SERVICE_ACCOUNT_TOKEN="${OP_SERVICE_ACCOUNT_TOKEN:-}"
    echo "Running $SRC/install.sh"
    OP_SERVICE_ACCOUNT_TOKEN="$OP_SERVICE_ACCOUNT_TOKEN" bash "$SRC/install.sh"
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

load_opencode_env_file

step "Refresh OpenCode workspace model overrides"
if [[ -f /tmp/host-homelab-devcontainer/opencode-sync-workspace-overrides.sh ]]; then
  install -m 0755 /tmp/host-homelab-devcontainer/opencode-sync-workspace-overrides.sh \
    "$HOME/.local/bin/opencode-sync-workspace-overrides"
  if ! "$HOME/.local/bin/opencode-sync-workspace-overrides" "${OPENCODE_PROFILES:-${OPENCODE_PROFILE:-defaults}}" "$WORKSPACE_PATH"; then
    echo "WARN: OpenCode workspace override sync failed."
  fi
else
  echo "OpenCode workspace override helper not found; skipping."
fi
done_step "Refresh OpenCode workspace model overrides"

step "postCreate complete"
echo "Log saved to: $LOG_FILE"
done_step "postCreate complete"
