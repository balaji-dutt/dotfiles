#!/usr/bin/env bash
set -euo pipefail

# Utilities
need_cmd() { command -v "$1" >/dev/null 2>&1; }
have_root() { [ "$(id -u)" -eq 0 ]; }
as_root() { if have_root; then bash -c "$*"; else sudo bash -lc "$*"; fi; }

apt_install() {
  as_root "export DEBIAN_FRONTEND=noninteractive; apt-get update -y && apt-get install -y --no-install-recommends $* && rm -rf /var/lib/apt/lists/*"
}

echo "==> Running dotfiles install"

# Ensure base tools via apt (Debian/Ubuntu images)
if need_cmd apt-get; then
  pkgs=()
  need_cmd zsh    || pkgs+=("zsh")
  need_cmd direnv || pkgs+=("direnv")
  need_cmd curl   || pkgs+=("curl")
  need_cmd wget   || pkgs+=("wget")
  need_cmd git    || pkgs+=("git")
  need_cmd gpg    || pkgs+=("gpg")
  need_cmd jq     || pkgs+=("jq")
  need_cmd tar    || pkgs+=("tar")
  if [ "${#pkgs[@]}" -gt 0 ]; then
    echo "==> Installing via apt: ${pkgs[*]}"
    apt_install "${pkgs[@]}"
  fi
fi

# Container-specific: relax fsck for legacy repos (if env set in devcontainer.json)
if [ "${DEVCONTAINER:-}" = "1" ]; then
  git config --global fsck.zeroPaddedFilemode ignore || true
  git config --global fetch.fsck.zeroPaddedFilemode ignore || true
  git config --global receive.fsck.zeroPaddedFilemode ignore || true
fi

# Antidote (Zsh plugin manager)
if [ ! -d "/opt/antidote" ]; then
  echo "==> Installing Antidote"
  as_root "git clone --depth=1 https://github.com/mattmc3/antidote.git /opt/antidote"
else
  echo "==> Antidote already present"
fi

# zoxide: install Linux binary from GitHub releases (arch-agnostic)
# Set ZOXIDE_VERSION as a container variable to a tag like "0.9.8" to pin, or leave unset/"latest".
install_zoxide_gh() {
  local want="${ZOXIDE_VERSION:-latest}"

  # Resolve target arch
  local arch target
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) target="x86_64-unknown-linux-musl" ;;
    aarch64|arm64) target="aarch64-unknown-linux-musl" ;;
    *) echo "!! Unsupported arch for zoxide: $arch"; return 1 ;;
  esac

  # Resolve version if 'latest'
  if [ "$want" = "latest" ]; then
    if ! command -v jq >/dev/null 2>&1; then
      echo "!! jq not available; set ZOXIDE_VERSION or install jq"; return 1
    fi
    echo "==> Resolving latest zoxide version..."
    local tag
    tag="$(curl -fsSL https://api.github.com/repos/ajeetdsouza/zoxide/releases/latest \
            | jq -r '.tag_name' | sed 's/^v//')"
    [ -n "$tag" ] && [ "$tag" != "null" ] || { echo "!! Unable to resolve latest zoxide"; return 1; }
    want="$tag"
  fi

  # Skip or upgrade
  if command -v zoxide >/dev/null 2>&1; then
    local curr
    curr="$(zoxide --version 2>/dev/null | awk '{print $1}')"
    if [ "$curr" = "$want" ]; then
      echo "==> zoxide $curr already installed; skipping."
      return 0
    else
      echo "==> zoxide $curr found; upgrading to $want"
    fi
  fi

  # Download and install
  local tmpd asset url binpath
  tmpd="$(mktemp -d)"
  asset="zoxide-${want}-${target}.tar.gz"
  url="https://github.com/ajeetdsouza/zoxide/releases/download/v${want}/${asset}"

  echo "==> Installing zoxide ${want} (${target})"
  curl -fsSL "$url" -o "${tmpd}/z.tgz"
  tar -xzf "${tmpd}/z.tgz" -C "$tmpd"

  binpath="$(find "$tmpd" -type f -name zoxide -print -quit)"
  if [ -z "$binpath" ]; then
    echo "!! zoxide binary not found in archive."
    rm -rf "$tmpd"; return 1
  fi

  as_root "install -m 0755 '$binpath' /usr/local/bin/zoxide"
  rm -rf "$tmpd"

  command -v zoxide >/dev/null 2>&1 || { echo "!! zoxide not found after install"; return 1; }
  echo "==> zoxide installed: $(zoxide --version | awk '{print $1}')"
}
install_zoxide_gh || echo "!! zoxide install failed; continuing"

# eza (install via apt repository only; no GitHub fallback)
install_eza_apt() {
  echo "==> Installing eza via apt repository"
  as_root "mkdir -p /etc/apt/keyrings"
  if [ ! -f /etc/apt/keyrings/gierens.gpg ]; then
    curl -fsSL https://raw.githubusercontent.com/eza-community/eza/main/deb.asc | \
      as_root "gpg --dearmor -o /etc/apt/keyrings/gierens.gpg"
  fi
  as_root "chmod 644 /etc/apt/keyrings/gierens.gpg"
  if [ ! -f /etc/apt/sources.list.d/gierens.list ]; then
    echo "deb [signed-by=/etc/apt/keyrings/gierens.gpg] http://deb.gierens.de stable main" | \
      as_root "tee /etc/apt/sources.list.d/gierens.list >/dev/null"
  fi
  apt_install eza
}
if ! need_cmd eza; then
  if need_cmd apt-get; then
    install_eza_apt || echo "!! eza install via apt repo failed; continuing"
  else
    echo "!! apt-get not available. Skipping eza."
  fi
else
  echo "==> eza already present"
fi

# fzf: install latest/pinned Linux binary from GitHub releases (arch-agnostic)
# Set FZF_VERSION as a container variable to a tag like "0.66.0" or "latest".
install_fzf_gh() {
  local want_version="${FZF_VERSION:-latest}"
  local arch norm_arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) norm_arch="amd64" ;;
    aarch64|arm64) norm_arch="arm64" ;;
    *) echo "!! Unsupported arch for fzf binary: $arch"; return 1 ;;
  esac

  # Resolve "latest" using GitHub API (requires jq)
  if [ "$want_version" = "latest" ]; then
    if ! need_cmd jq; then
      echo "!! jq not available; cannot resolve latest fzf. Install jq or set FZF_VERSION."
      return 1
    fi
    echo "==> Resolving latest fzf version from GitHub..."
    local tag
    tag="$(curl -fsSL https://api.github.com/repos/junegunn/fzf/releases/latest | jq -r '.tag_name' | sed 's/^v//')"
    [ -n "$tag" ] && [ "$tag" != "null" ] || { echo "!! Unable to resolve latest fzf version."; return 1; }
    want_version="$tag"
  fi

  # If already installed and version matches, skip
  if need_cmd fzf; then
    local curr
    curr="$(fzf --version 2>/dev/null | awk '{print $1}')"
    if [ "$curr" = "$want_version" ]; then
      echo "==> fzf $curr already installed; skipping."
      return 0
    else
      echo "==> fzf $curr found; upgrading to $want_version"
    fi
  fi

  local tmpd asset url
  tmpd="$(mktemp -d)"
  asset="fzf-${want_version}-linux_${norm_arch}.tar.gz"
  url="https://github.com/junegunn/fzf/releases/download/v${want_version}/${asset}"

  echo "==> Installing fzf ${want_version} (${norm_arch})"
  curl -fsSL "$url" -o "${tmpd}/fzf.tgz"
  # Extract directly to /usr/local/bin (only 'fzf' file inside tar)
  as_root "tar -xzf '${tmpd}/fzf.tgz' -C /usr/local/bin fzf"
  as_root "chmod 0755 /usr/local/bin/fzf"
  rm -rf "$tmpd"

  # Verify
  if ! need_cmd fzf; then
    echo "!! fzf not found after install."
    return 1
  fi
  echo "==> fzf installed: $(fzf --version | awk '{print $1}')"
}
install_fzf_gh || echo "!! fzf install failed; continuing"

# Make zsh default shell (best-effort; VS Code settings can also set terminal default)
if need_cmd zsh; then
  current_shell="$(getent passwd "$(id -un)" | cut -d: -f7 || echo "")"
  zsh_path="$(command -v zsh)"
  if [ -n "$zsh_path" ] && [ "$current_shell" != "$zsh_path" ]; then
    echo "==> Setting default shell to zsh for $(id -un)"
    as_root "chsh -s '$zsh_path' '$(id -un)'" || true
  fi
fi

install_and_patch_terraform_py() {
  echo "==> Setting up terraform.py"

  # Define variables for clarity and easy maintenance
  local url="https://raw.githubusercontent.com/nbering/terraform-inventory/master/terraform.py"
  local target_dir="$HOME/.local/bin"
  local target_file="$target_dir/terraform.py"
  local patch_file="/home/vscode/.host-dotfiles/terraform.patch"

  # Ensure the target directory exists
  mkdir -p "$target_dir"

  echo "==> Downloading terraform.py..."
  curl -fsSL -o "$target_file" "$url"

  # Make it executable
  chmod +x "$target_file"

  # Apply the patch if it exists
  if [ -f "$patch_file" ]; then
      echo "==> Applying patch from $patch_file..."
      # Use process substitution to feed the patch to stdin after stripping the git diff header
      patch -i <(sed '1d' "$patch_file") -r - -s -d "$(dirname "$target_file")" "$(basename "$target_file")"
      echo "==> Patch applied successfully."
  else
      echo "!! Warning: Patch file not found at $patch_file. Skipping patch."
  fi
}

# Copy dotfiles from the mounted host folder if present
SRC="/home/vscode/.host-dotfiles"
if [ -d "$SRC" ]; then
  echo "==> Syncing dotfiles from $SRC"
  # Use rsync to be more efficient and exclude setup files from being copied to $HOME
  rsync -a \
  --exclude='.git' \
  --exclude='install.sh' \
  --exclude='assets/' \
  --exclude='.zcompdump*' \
  --exclude='.DS_Store' \
  --exclude='*.patch' \
  "$SRC/" "$HOME/"
  # Now, run the custom installer for terraform.py which needs the patch file from the source
  install_and_patch_terraform_py || echo "!! terraform.py setup failed; continuing"
fi

echo "==> Dotfiles install complete."
