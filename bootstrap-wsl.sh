#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

prompt_github_api_token() {
    if [ -n "${GITHUB_API_TOKEN:-}" ]; then
        return 0
    fi

    if [ ! -t 0 ]; then
        log_error "GITHUB_API_TOKEN is required but no TTY is available to prompt for it"
        exit 1
    fi

    echo
    log_warn "GitHub API token required"
    log_warn "Some setup steps use GitHub API (via lastversion) and will rate-limit without a token."
    read -r -s -p "Enter GITHUB_API_TOKEN: " GITHUB_API_TOKEN
    echo

    if [ -z "${GITHUB_API_TOKEN:-}" ]; then
        log_error "GITHUB_API_TOKEN cannot be empty"
        exit 1
    fi

    export GITHUB_API_TOKEN
}

# Detect distro
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO_ID="${ID}"
    DISTRO_VERSION="${VERSION_ID}"
else
    log_error "Cannot detect distribution"
    exit 1
fi

log_info "Detected: ${DISTRO_ID} ${DISTRO_VERSION}"

prompt_github_api_token

# Update and install essentials
log_info "Installing essential packages..."
sudo apt update
sudo apt install -y git curl python3-pip python3-venv pipx

# Ensure pipx path
pipx ensurepath
export PATH="$HOME/.local/bin:$PATH"

# Install Ansible via pipx
if ! command -v ansible-playbook &> /dev/null; then
    log_info "Installing Ansible via pipx..."
    pipx install --include-deps ansible
    pipx inject ansible jmespath  # Needed for json_query filter
else
    log_info "Ansible already installed"
fi

# Define path to dotfiles location.
DOTFILES_LOCAL_PATH="${1:-$HOME/Documents/development/dotfiles}"

# Install Ansible collections
log_info "Installing Ansible collections..."
ansible-galaxy collection install -r "${DOTFILES_LOCAL_PATH}/ansible/requirements.yml"

# Install lastversion (needed for chezmoi templates)
log_info "Installing lastversion via pipx..."
pipx install lastversion

# Install chezmoi
if ! command -v chezmoi &> /dev/null; then
    log_info "Installing chezmoi..."
    sh -c "$(curl -fsLS get.chezmoi.io)" -- -b "$HOME/.local/bin"
else
    log_info "chezmoi already installed"
fi

# Initialize chezmoi with local dotfiles repo
log_info "Ensuring chezmoi cache directory exists..."
mkdir -p "$HOME/.cache/chezmoi"

if [ ! -f "$HOME/.config/chezmoi/chezmoi.toml" ]; then
    if [ -d "$DOTFILES_LOCAL_PATH" ]; then
        log_info "Initializing chezmoi with local repo: ${DOTFILES_LOCAL_PATH}..."
        chezmoi init --source="${DOTFILES_LOCAL_PATH}"
    else
        log_error "Dotfiles repo not found at ${DOTFILES_LOCAL_PATH}"
        log_error "Please clone your dotfiles repo first:"
        log_error "  mkdir -p ~/Documents/development"
        log_error "  git clone <your-repo-url> ${DOTFILES_LOCAL_PATH}"
        exit 1
    fi
else
    log_info "chezmoi already initialized"
fi

log_info "Bootstrap complete!"
log_info "Starting a new login shell with GITHUB_API_TOKEN in the environment."
log_info "Next: run 'chezmoi apply'"

if [ -n "${SHELL:-}" ] && [ -x "${SHELL}" ]; then
    exec "${SHELL}" -l
else
    exec bash -l
fi
