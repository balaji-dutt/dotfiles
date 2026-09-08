#!/usr/bin/env bash

timestamp() { date +"%Y-%m-%d %H:%M:%S%z"; }

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
common_file="$script_dir/devcontainer-common.sh"
if [[ ! -r "$common_file" ]]; then
  echo "ERROR: Shared devcontainer helper not found: $common_file" >&2
  return 1 2>/dev/null || exit 1
fi
# shellcheck source=private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/devcontainer-common.sh
source "$common_file" || {
  echo "ERROR: Failed to source shared devcontainer helper: $common_file" >&2
  return 1 2>/dev/null || exit 1
}

step() { echo; echo "==== [$(timestamp)] STEP: $* ===="; }
done_step() { echo "==== [$(timestamp)] DONE: $* ===="; }

log_run_header() {
  local workspace
  workspace="$1"

  echo "==== [$(timestamp)] postCreate run ===="
  echo "script=${BASH_SOURCE[0]}"
  echo "workspace=$workspace"
  echo "hostname=$(hostname 2>/dev/null || true)"
  echo "note=postCreate runs only when the container is created or recreated; check /tmp/postStart.log for restart/reopen runs."
}

trim_whitespace() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

bootstrap_local_git_metadata() {
  local workspace="$1"
  local source_git_dir="${2:-/tmp/host-workspace-git}"
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

install_dolt_if_missing() {
  local dolt_version machine arch asset_name download_url tmp_dir archive_path
  local extracted_bin

  if command -v dolt >/dev/null 2>&1; then
    dolt version
    return 0
  fi

  dolt_version="${DOLT_VERSION:-}"
  if [[ -z "$dolt_version" ]]; then
    echo "ERROR: DOLT_VERSION is not set and dolt is missing from PATH." >&2
    return 1
  fi

  machine="$(uname -m)"
  case "$machine" in
    x86_64|amd64)
      arch="amd64"
      ;;
    aarch64|arm64)
      arch="arm64"
      ;;
    *)
      echo "ERROR: Unsupported Dolt architecture: $machine" >&2
      return 1
      ;;
  esac

  asset_name="dolt-linux-$arch.tar.gz"
  download_url="https://github.com/dolthub/dolt/releases/download/v$dolt_version/$asset_name"
  tmp_dir="$(mktemp -d)"
  archive_path="$tmp_dir/$asset_name"
  extracted_bin="$tmp_dir/dolt-linux-$arch/bin/dolt"

  (
    trap 'rm -rf "$tmp_dir"' EXIT

    echo "Installing Dolt v$dolt_version from $download_url"
    curl -fsSL "$download_url" -o "$archive_path"
    tar -xzf "$archive_path" -C "$tmp_dir"

    if [[ ! -x "$extracted_bin" ]]; then
      echo "ERROR: Dolt binary not found after extracting $asset_name" >&2
      exit 1
    fi

    if [[ -w /usr/local/bin ]]; then
      install -m 0755 "$extracted_bin" /usr/local/bin/dolt
    else
      sudo install -m 0755 "$extracted_bin" /usr/local/bin/dolt
    fi
  ) || return 1

  dolt version
}

install_terraform_mcp_server() {
  local version machine arch asset_name checksums_name base_url tmp_dir
  local archive_path checksums_path extract_dir extracted_bin expected actual

  version="${TF_MCP_VERSION:-}"
  if [[ -z "$version" ]]; then
    echo "WARN: TF_MCP_VERSION not set; skipping terraform-mcp-server install."
    return 0
  fi

  machine="$(uname -m)"
  case "$machine" in
    x86_64|amd64)
      arch="amd64"
      ;;
    aarch64|arm64)
      arch="arm64"
      ;;
    *)
      echo "ERROR: Unsupported terraform-mcp-server architecture: $machine" >&2
      return 1
      ;;
  esac

  asset_name="terraform-mcp-server_${version}_linux_${arch}.zip"
  checksums_name="terraform-mcp-server_${version}_SHA256SUMS"
  base_url="https://releases.hashicorp.com/terraform-mcp-server/${version}"
  tmp_dir="$(mktemp -d /tmp/terraform-mcp-server.XXXXXX)"
  archive_path="$tmp_dir/$asset_name"
  checksums_path="$tmp_dir/$checksums_name"
  extract_dir="$tmp_dir/extracted"
  extracted_bin="$extract_dir/terraform-mcp-server"

  (
    trap 'rm -rf "$tmp_dir"' EXIT

    echo "[mcp] installing terraform-mcp-server v${version} (${arch})"
    curl -fsSL --retry 3 "$base_url/$checksums_name" -o "$checksums_path"
    curl -fsSL --retry 3 "$base_url/$asset_name" -o "$archive_path"

    expected=$(
      awk -v asset="$asset_name" \
        '$2 == asset || $2 == "*" asset { print $1; exit }' \
        "$checksums_path"
    )
    if [[ -z "$expected" ]]; then
      echo "ERROR: No checksum found for $asset_name." >&2
      exit 1
    fi

    actual="$(sha256sum "$archive_path" | awk '{print $1}')"
    if [[ "$expected" != "$actual" ]]; then
      echo "ERROR: SHA256 mismatch for $asset_name." >&2
      echo "  expected: $expected" >&2
      echo "  actual:   $actual" >&2
      exit 1
    fi

    mkdir -p "$extract_dir"
    unzip -q "$archive_path" -d "$extract_dir"
    if [[ ! -f "$extracted_bin" ]]; then
      echo "ERROR: terraform-mcp-server binary not found after extracting $asset_name." >&2
      exit 1
    fi

    sudo install -m 0755 "$extracted_bin" /usr/local/bin/terraform-mcp-server
  ) || return 1

  terraform-mcp-server --help 2>&1 | head -1 || true
}

install_claude_code_apt_package() {
  local claude_code_version apt_keyring apt_source_list installed_version
  local claude_version_output

  claude_code_version="${CLAUDE_CODE_VERSION:-}"
  if [[ -z "$claude_code_version" ]]; then
    echo "ERROR: CLAUDE_CODE_VERSION is not set." >&2
    return 1
  fi

  apt_keyring="/etc/apt/keyrings/claude-code.asc"
  apt_source_list="/etc/apt/sources.list.d/claude-code.list"

  sudo install -d -m 0755 /etc/apt/keyrings
  curl -fsSL "https://downloads.claude.ai/keys/claude-code.asc" \
    | sudo tee "$apt_keyring" >/dev/null
  sudo chmod 0644 "$apt_keyring"

  printf '%s\n' \
    "deb [signed-by=$apt_keyring] https://downloads.claude.ai/claude-code/apt/latest latest main" \
    | sudo tee "$apt_source_list" >/dev/null

  sudo apt-get update

  if command -v npm >/dev/null 2>&1 \
    && npm list -g @anthropic-ai/claude-code >/dev/null 2>&1; then
    echo "Removing old global npm @anthropic-ai/claude-code package before apt install."
    env -u CI npm uninstall -g @anthropic-ai/claude-code || \
      echo "WARN: npm uninstall of @anthropic-ai/claude-code failed; continuing to apt install." >&2
  fi

  sudo apt-get install -y "claude-code=${claude_code_version}-1"

  installed_version="$(dpkg-query -W -f='${Version}' claude-code 2>/dev/null || true)"
  if [[ "$installed_version" != "${claude_code_version}-1" ]]; then
    echo "ERROR: claude-code package version mismatch: expected ${claude_code_version}-1, got ${installed_version:-<missing>}." >&2
    return 1
  fi

  hash -r
  claude_version_output="$(claude --version 2>/dev/null || true)"
  if [[ "$claude_version_output" != "$claude_code_version"* ]]; then
    echo "ERROR: claude --version mismatch: expected prefix ${claude_code_version}, got ${claude_version_output:-<missing>}." >&2
    return 1
  fi

  echo "Claude Code ${claude_code_version} installed from Anthropic apt package."
}

install_ansible_mcp_server_wrapper() {
  local shim_dir shim wrapper_path smoke_output smoke_exit smoke_text

  if ! command -v ansible-mcp-server >/dev/null 2>&1; then
    echo "WARN: ansible-mcp-server not found; skipping fixed wrapper install." >&2
    return 0
  fi

  shim_dir="/usr/local/lib/ansible-mcp-fixed"
  shim="$shim_dir/register-require.cjs"
  wrapper_path="/usr/local/bin/ansible-mcp-server-fixed"

  sudo install -d -m 0755 "$shim_dir"
  printf 'globalThis.require = require;\n' | sudo tee "$shim" >/dev/null
  sudo chmod 0644 "$shim"

  sudo tee "$wrapper_path" >/dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cli="$(npm root -g)/@ansible/ansible-mcp-server/dist/cli.cjs"
if [[ ! -r "$cli" ]]; then
  echo "ansible-mcp-server cli.cjs not found: $cli" >&2
  exit 1
fi
exec node "$cli" "$@"
EOF
  sudo chmod 0755 "$wrapper_path"

  smoke_output="$(mktemp)"
  smoke_exit=0
  timeout 3s "$wrapper_path" --stdio </dev/null >"$smoke_output" 2>&1 || smoke_exit=$?
  smoke_text="$(<"$smoke_output")"
  rm -f "$smoke_output"

  case "$smoke_exit" in
    0|124)
      echo "Installed ansible MCP wrapper: $wrapper_path"
      ;;
    *)
      if [[ "$smoke_text" == *require* || "$smoke_text" == *ReferenceError* || "$smoke_text" == *process* ]]; then
        echo "ERROR: ansible-mcp-server-fixed failed startup smoke test:" >&2
        printf '%s\n' "$smoke_text" >&2
        return "$smoke_exit"
      fi

      echo "WARN: ansible-mcp-server-fixed smoke test exited $smoke_exit; keeping wrapper." >&2
      if [[ -n "$smoke_text" ]]; then
        printf '%s\n' "$smoke_text" >&2
      fi
      ;;
  esac
}

npm_package_name_from_spec() {
  local spec
  spec="$1"

  case "$spec" in
    @*/*@*) printf '%s\n' "${spec%@*}" ;;
    @*/*) printf '%s\n' "$spec" ;;
    *@*) printf '%s\n' "${spec%@*}" ;;
    *) printf '%s\n' "$spec" ;;
  esac
}

npm_allow_scripts_for_package() {
  local pkg_name
  pkg_name="$1"

  case "$pkg_name" in
    opencode-ai|@fission-ai/openspec|@beads/bd)
      printf '%s\n' "$pkg_name"
      ;;
    *)
      return 0
      ;;
  esac
}

prepare_npm_cache() {
  local cache_dir

  cache_dir="${npm_config_cache:-/home/vscode/persistent-data/npm-cache}"
  install -d -m 0700 "$cache_dir"
  export npm_config_cache="$cache_dir"
}

promptfoo_runtime_platform_package_spec() {
  local lock_file arch package_name

  lock_file="$1"
  arch="$(node -p 'process.arch')"
  case "$arch" in
    arm64|x64)
      package_name="@libsql/linux-${arch}-gnu"
      ;;
    *)
      echo "ERROR: Unsupported Promptfoo runtime architecture: $arch" >&2
      return 1
      ;;
  esac

  node --input-type=module - "$lock_file" "$package_name" <<'NODE'
import fs from 'node:fs';

const [lockFile, packageName] = process.argv.slice(2);
const lock = JSON.parse(fs.readFileSync(lockFile, 'utf8'));
const entry = lock.packages?.[`node_modules/${packageName}`];
if (!entry?.optional || typeof entry.version !== 'string') {
  console.error(`Missing optional lockfile entry for ${packageName}`);
  process.exit(1);
}
console.log(`${packageName}@${entry.version}`);
NODE
}

install_global_npm_package() {
  local pkg pkg_name allow_scripts started_at finished_at rc
  local -a npm_args

  pkg="$1"
  pkg_name="$(npm_package_name_from_spec "$pkg")"
  allow_scripts="$(npm_allow_scripts_for_package "$pkg_name")"
  npm_args=(install -g)

  if [[ -n "$allow_scripts" ]]; then
    npm_args+=("--allow-scripts=$allow_scripts")
  fi

  started_at="$(date +%s)"
  echo "[npm] start package=$pkg mode=global epoch=$started_at"
  # Some package lifecycle scripts skip native downloads when CI is set.
  if env -u CI npm "${npm_args[@]}" "$pkg"; then
    rc=0
  else
    rc=$?
  fi
  finished_at="$(date +%s)"
  echo "[npm] finish package=$pkg status=$rc elapsed_seconds=$((finished_at - started_at))"
  return "$rc"
}

install_promptfoo_runtime() {
  local source_dir runtime_dir platform_package_spec started_at finished_at rc
  local package_file lock_file

  source_dir="${1:-/tmp/host-homelab-configs/promptfoo-runtime}"
  runtime_dir="${2:-$HOME/.local/share/promptfoo-runtime}"
  package_file="$source_dir/package.json"
  lock_file="$source_dir/package-lock.json"

  if [[ ! -r "$package_file" || ! -r "$lock_file" ]]; then
    echo "ERROR: Promptfoo runtime manifest or lockfile is missing in $source_dir" >&2
    return 1
  fi

  install -d -m 0700 "$runtime_dir"
  install -m 0600 "$package_file" "$runtime_dir/package.json"
  install -m 0600 "$lock_file" "$runtime_dir/package-lock.json"
  platform_package_spec="$(promptfoo_runtime_platform_package_spec "$lock_file")" || return 1

  started_at="$(date +%s)"
  echo "[npm] start package=promptfoo-runtime mode=local omit_optional=true epoch=$started_at"
  if (
    cd "$runtime_dir" || exit 1
    env -u CI npm ci --omit=optional --timing &&
    env -u CI npm install --no-save --omit=optional "$platform_package_spec"
  ); then
    rc=0
  else
    rc=$?
  fi
  finished_at="$(date +%s)"
  echo "[npm] finish package=promptfoo-runtime status=$rc elapsed_seconds=$((finished_at - started_at))"
  return "$rc"
}

verify_promptfoo_runtime() {
  local runtime_dir

  runtime_dir="${1:-$HOME/.local/share/promptfoo-runtime}"

  (
    cd "$runtime_dir"
    node --input-type=module <<'NODE'
for (const packageName of [
  'promptfoo',
  '@opencode-ai/sdk',
  '@anthropic-ai/claude-agent-sdk',
  '@anthropic-ai/sdk',
]) {
  import.meta.resolve(packageName);
}
NODE
  ) || return 1

  "$runtime_dir/node_modules/.bin/promptfoo" --version >/dev/null || return 1
  echo "Promptfoo runtime ready in $runtime_dir"
}

retire_mnemo_persistence_link() {
  local actual_target link_path target_dir

  link_path="${1:-$HOME/.mnemo}"
  target_dir="${2:-/home/vscode/persistent-data/mnemo}"

  if [[ ! -L "$link_path" ]]; then
    if [[ -e "$link_path" ]]; then
      echo "Preserving non-symlink mnemo data path: $link_path"
    fi
    return 0
  fi

  actual_target="$(readlink "$link_path")"
  if [[ "$actual_target" != "$target_dir" ]]; then
    echo "Preserving mnemo symlink with a different target: $link_path"
    return 0
  fi

  command rm -f -- "$link_path"
  if [[ -e "$link_path" || -L "$link_path" ]]; then
    echo "ERROR: failed to retire mnemo persistence symlink: $link_path" >&2
    return 1
  fi

  echo "Retired mnemo persistence symlink; data remains at $target_dir"
}

install_opencode_env_file() {
  local src dest profile_lines tmp_file

  src="${1:-/tmp/host-container-configs/opencode.env}"
  dest="${2:-/home/vscode/persistent-data/opencode/config/opencode.env}"
  profile_lines=""
  tmp_file=""

  if [[ ! -f "$src" ]]; then
    echo "WARN: $src not found; run ./assets/sync-devcontainer-all.sh or ./assets/render-container-configs.sh on the host, then rebuild/restart the container. Keeping existing env file if present." >&2
    return 0
  fi

  mkdir -p "$(dirname "$dest")"
  profile_lines="$(mktemp "${dest}.profiles.XXXXXX")"

  if [[ -r "$dest" ]]; then
    awk '
      /^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILES=/ { print; next }
      /^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILE=/ { print; next }
    ' "$dest" > "$profile_lines"
  fi

  install -m 0600 "$src" "$dest"

  if [[ -s "$profile_lines" ]]; then
    tmp_file="$(mktemp "${dest}.XXXXXX")"
    awk '
      /^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILES=/ { next }
      /^[[:space:]]*(export[[:space:]]+)?OPENCODE_PROFILE=/ { next }
      { print }
    ' "$dest" > "$tmp_file"
    cat "$profile_lines" >> "$tmp_file"
    mv -f "$tmp_file" "$dest"
    chmod 600 "$dest" 2>/dev/null || true
    tmp_file=""
  fi

  rm -f "$profile_lines" ${tmp_file:+"$tmp_file"}
}

post_create_persistence_phase() {
  step "Setup Claude config symlinks and permissions"
  ensure_claude_persistence_links
  install_claude_managed_asset_links
  register_claude_mcp_servers
  done_step "Setup Claude config symlinks and permissions"

  step "Source /tmp/host-container-configs/container_env (if present)"
  if [[ -f /tmp/host-container-configs/container_env ]]; then
    # shellcheck disable=SC1091
    source /tmp/host-container-configs/container_env
  else
    echo "/tmp/host-container-configs/container_env not found; skipping."
  fi
  done_step "Source /tmp/host-container-configs/container_env (if present)"

  step "Retire mnemo persistence symlink"
  retire_mnemo_persistence_link
  done_step "Retire mnemo persistence symlink"

  step "Prime OpenCode/AoE persistent-data symlinks before install"
  mkdir -p \
    /home/vscode/persistent-data/opencode/{config,cache,share,state} \
    "$HOME/.cache" \
    "$HOME/.local/share" \
    "$HOME/.local/state"

  ensure_agent_of_empires_persistence_link
  ensure_opencode_persistence_links
  done_step "Prime OpenCode/AoE persistent-data symlinks before install"

  step "Install generated OpenCode env file (if present)"
  install_opencode_env_file
  done_step "Install generated OpenCode env file (if present)"
}

post_create_materialization_phase() {
  step "Install OpenCode managed assets"
  materialize_opencode_managed_assets
  done_step "Install OpenCode managed assets"
}

post_create_workspace_override_phase() {
  local workspace_path
  workspace_path="$1"

  load_opencode_env_file

  step "Refresh OpenCode workspace model overrides"
  if [[ -f /tmp/host-homelab-devcontainer/opencode-sync-workspace-overrides.sh ]]; then
    install -m 0755 /tmp/host-homelab-devcontainer/opencode-sync-workspace-overrides.sh \
      "$HOME/.local/bin/opencode-sync-workspace-overrides"
    if ! "$HOME/.local/bin/opencode-sync-workspace-overrides" "${OPENCODE_PROFILES:-${OPENCODE_PROFILE:-defaults}}" "$workspace_path"; then
      echo "WARN: OpenCode workspace override sync failed."
    fi
  else
    echo "OpenCode workspace override helper not found; skipping."
  fi
  done_step "Refresh OpenCode workspace model overrides"
}

on_error() {
  local exit_code=$?
  echo
  echo "!!!! [$(timestamp)] ERROR (exit $exit_code) at line ${BASH_LINENO[0]}: ${BASH_COMMAND}"
  echo "!!!! See log: $LOG_FILE"
  exit "$exit_code"
}

post_create_main() (
set -Eeuo pipefail

LOG_FILE="${LOG_FILE:-/tmp/postCreate.log}"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee "$LOG_FILE") 2>&1
trap on_error ERR

# Print commands as they run in debug mode (very helpful for "where did it hang?")
[[ "${POSTCREATE_DEBUG:-0}" == "1" ]] && set -x

# Make package installs non-interactive
export DEBIAN_FRONTEND=noninteractive
export CI=1

WORKSPACE_PATH="${1:-$PWD}"
log_run_header "$WORKSPACE_PATH"

step "Bootstrap local Git metadata volume (if mounted)"
bootstrap_local_git_metadata "$WORKSPACE_PATH"
done_step "Bootstrap local Git metadata volume (if mounted)"

# --- 1) Permissions / base packages ---
step "Fix ownership for persistent-data"
if [[ -d /home/vscode/persistent-data ]]; then
  sudo chown -R vscode:vscode /home/vscode/persistent-data
fi
done_step "Fix ownership for persistent-data"

step "Prepare persistent npm cache"
prepare_npm_cache
done_step "Prepare persistent npm cache"

step "Configure Git safe directories"
ensure_git_safe_directories "$WORKSPACE_PATH"
done_step "Configure Git safe directories"

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
python3 -m pip install --user "uv==0.12.9" # renovate: datasource=pypi depName=uv versioning=pep440
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

step "Install Claude Code from Anthropic apt package"
install_claude_code_apt_package
done_step "Install Claude Code from Anthropic apt package"

# --- 4) Global npm packages ---
step "Install global npm packages from /tmp/host-homelab-configs/npm_packages.txt"
effective_npm_cache="$(npm config get cache)"
echo "[npm] node=$(node --version) npm=$(npm --version) cache=$effective_npm_cache"
if [[ "$effective_npm_cache" != "$npm_config_cache" ]]; then
  echo "ERROR: npm cache mismatch: expected $npm_config_cache, got $effective_npm_cache" >&2
  exit 1
fi
if [[ -f /tmp/host-homelab-configs/npm_packages.txt ]]; then
  # Global npm installs have no project package.json for npm approve-scripts to
  # update. Keep script approvals scoped to the reviewed install invocation so
  # future packages still warn until reviewed.
  # helpful visibility
  echo "--- npm packages file ---"
  sed -n '1,200p' /tmp/host-homelab-configs/npm_packages.txt || true
  echo "-------------------------"
  while IFS= read -r pkg || [[ -n "${pkg:-}" ]]; do
    [[ -z "${pkg// /}" ]] && continue
    install_global_npm_package "$pkg"

    if [[ "$pkg" == @beads/bd@* ]]; then
      bd version
    fi
  done < /tmp/host-homelab-configs/npm_packages.txt

else
  echo "No /tmp/host-homelab-configs/npm_packages.txt found; skipping."
fi
done_step "Install global npm packages"

step "Install lockfile-backed Promptfoo runtime"
install_promptfoo_runtime
verify_promptfoo_runtime
done_step "Install lockfile-backed Promptfoo runtime"

step "Install ansible MCP server fixed wrapper"
install_ansible_mcp_server_wrapper
done_step "Install ansible MCP server fixed wrapper"

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

if [[ -n "${CBM_VERSION:-}" && -n "${HOP_ARCH:-}" ]]; then
  if [[ -n "${CBM_CACHE_DIR:-}" ]]; then
    install -d -m 0700 "$CBM_CACHE_DIR"
  fi

  CBM_INSTALLED=""
  if command -v codebase-memory-mcp >/dev/null 2>&1; then
    CBM_INSTALLED=$(
      codebase-memory-mcp --version 2>/dev/null \
        | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' \
        | head -1 \
        || true
    )
  fi

  if [[ "$CBM_INSTALLED" == "$CBM_VERSION" ]]; then
    echo "[mcp] codebase-memory-mcp ${CBM_INSTALLED} already installed."
  else
    echo "[mcp] installing codebase-memory-mcp v${CBM_VERSION} (${HOP_ARCH})"
    CBM_ASSET="codebase-memory-mcp-linux-${HOP_ARCH}-portable.tar.gz"
    CBM_BASE_URL="https://github.com/DeusData/codebase-memory-mcp/releases/download/v${CBM_VERSION}"
    CBM_TMP=$(mktemp -d /tmp/codebase-memory-mcp.XXXXXX)

    curl -fsSL --retry 3 "${CBM_BASE_URL}/checksums.txt" \
      -o "${CBM_TMP}/checksums.txt"
    curl -fsSL --retry 3 "${CBM_BASE_URL}/${CBM_ASSET}" \
      -o "${CBM_TMP}/${CBM_ASSET}"

    CBM_EXPECTED=$(
      awk -v asset="$CBM_ASSET" '$2 == asset { print $1; exit }' \
        "${CBM_TMP}/checksums.txt"
    )
    if [[ -z "$CBM_EXPECTED" ]]; then
      echo "ERROR: No checksum found for ${CBM_ASSET}." >&2
      exit 1
    fi

    CBM_ACTUAL=$(sha256sum "${CBM_TMP}/${CBM_ASSET}" | awk '{print $1}')
    if [[ "$CBM_EXPECTED" != "$CBM_ACTUAL" ]]; then
      echo "ERROR: SHA256 mismatch for ${CBM_ASSET}." >&2
      echo "  expected: $CBM_EXPECTED" >&2
      echo "  actual:   $CBM_ACTUAL" >&2
      exit 1
    fi

    tar -xzf "${CBM_TMP}/${CBM_ASSET}" -C "$CBM_TMP" codebase-memory-mcp
    sudo install -m 0755 "${CBM_TMP}/codebase-memory-mcp" \
      /usr/local/bin/codebase-memory-mcp
    rm -rf "$CBM_TMP"
    codebase-memory-mcp --version
  fi

  CBM_AUTO_INDEX=$(codebase-memory-mcp config get auto_index)
  if [[ "$CBM_AUTO_INDEX" != "true" ]]; then
    codebase-memory-mcp config set auto_index true
  fi
  CBM_AUTO_INDEX=$(codebase-memory-mcp config get auto_index)
  if [[ "$CBM_AUTO_INDEX" != "true" ]]; then
    echo "ERROR: codebase-memory-mcp auto_index verification failed." >&2
    exit 1
  fi
  echo "[mcp] codebase-memory-mcp auto_index enabled for the active cache."
else
  echo "WARN: CBM_VERSION not set or architecture unsupported; skipping codebase-memory-mcp install."
fi

step "Install MCP server binary (terraform-mcp-server)"
install_terraform_mcp_server
done_step "Install MCP server binaries"

# --- 5c) plannotator CLI ---
# Claude Code's plannotator plugin invokes a bare `plannotator` command from
# PATH; the plugin itself does not ship the CLI. Pin + install it here so
# every container rebuild matches PLANNOTATOR_VERSION (set in
# devcontainer.json.tmpl, sourced from .chezmoidata.yaml).
step "Install plannotator CLI"
if [[ -n "${PLANNOTATOR_VERSION:-}" ]]; then
  case "$ARCH" in
    x86_64)  PLANNOTATOR_ARCH="x64" ;;
    aarch64) PLANNOTATOR_ARCH="arm64" ;;
    *)
      echo "WARN: unsupported architecture $ARCH for plannotator; skipping."
      PLANNOTATOR_ARCH=""
      ;;
  esac

  if [[ -n "$PLANNOTATOR_ARCH" ]]; then
    PLANNOTATOR_BIN="$HOME/.local/bin/plannotator"
    PLANNOTATOR_INSTALLED=""
    if [[ -x "$PLANNOTATOR_BIN" ]]; then
      PLANNOTATOR_INSTALLED=$(
        "$PLANNOTATOR_BIN" --version 2>/dev/null \
          | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' \
          | head -1 \
          || true
      )
    fi

    if [[ "$PLANNOTATOR_INSTALLED" == "$PLANNOTATOR_VERSION" ]]; then
      echo "[plannotator] ${PLANNOTATOR_INSTALLED} already installed; nothing to do."
    else
      echo "[plannotator] installing v${PLANNOTATOR_VERSION} (${PLANNOTATOR_ARCH})"
      PLANNOTATOR_BASE_URL="https://github.com/backnotprop/plannotator/releases/download/v${PLANNOTATOR_VERSION}"
      PLANNOTATOR_ASSET="plannotator-linux-${PLANNOTATOR_ARCH}"
      PLANNOTATOR_TMP=$(mktemp -d /tmp/plannotator.XXXXXX)

      curl -fsSL "${PLANNOTATOR_BASE_URL}/${PLANNOTATOR_ASSET}.sha256" \
        -o "${PLANNOTATOR_TMP}/${PLANNOTATOR_ASSET}.sha256"
      curl -fsSL "${PLANNOTATOR_BASE_URL}/${PLANNOTATOR_ASSET}" \
        -o "${PLANNOTATOR_TMP}/${PLANNOTATOR_ASSET}"

      PLANNOTATOR_EXPECTED=$(awk '{print $1}' "${PLANNOTATOR_TMP}/${PLANNOTATOR_ASSET}.sha256")
      PLANNOTATOR_ACTUAL=$(sha256sum "${PLANNOTATOR_TMP}/${PLANNOTATOR_ASSET}" | awk '{print $1}')
      if [[ "$PLANNOTATOR_EXPECTED" != "$PLANNOTATOR_ACTUAL" ]]; then
        echo "ERROR: SHA256 mismatch for ${PLANNOTATOR_ASSET}" >&2
        echo "  expected: $PLANNOTATOR_EXPECTED" >&2
        echo "  actual:   $PLANNOTATOR_ACTUAL" >&2
        rm -rf "$PLANNOTATOR_TMP"
        exit 1
      fi

      mkdir -p "$HOME/.local/bin"
      install -m 0755 "${PLANNOTATOR_TMP}/${PLANNOTATOR_ASSET}" "$PLANNOTATOR_BIN"
      rm -rf "$PLANNOTATOR_TMP"
      "$PLANNOTATOR_BIN" --version 2>/dev/null || echo "WARN: plannotator --version check failed"
    fi
  fi
else
  echo "WARN: PLANNOTATOR_VERSION not set; skipping plannotator install."
fi
done_step "Install plannotator CLI"

post_create_persistence_phase

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

post_create_materialization_phase

step "Install Better Beads Kanban VSIX (if VS Code CLI is available)"
if ! install_better_beads_kanban_vscode_extension; then
  echo "WARN: Better Beads Kanban VSIX install failed; continuing container setup." >&2
fi
done_step "Install Better Beads Kanban VSIX (if VS Code CLI is available)"

step "Ensure Dolt CLI"
install_dolt_if_missing
done_step "Ensure Dolt CLI"

step "Verify Beads CLI tooling"
if command -v bd >/dev/null 2>&1; then
  bd version
  command -v dolt
  dolt version
else
  echo "bd not found; skipping Beads Dolt verification."
fi
done_step "Verify Beads CLI tooling"

# --- 9) Antidote ---
step "Load Antidote (if present)"
if [[ -f /opt/antidote/antidote.zsh ]]; then
  zsh -lc 'source /opt/antidote/antidote.zsh && antidote load' || true
else
  echo "Antidote not present; skipping."
fi
done_step "Load Antidote (if present)"

post_create_workspace_override_phase "$WORKSPACE_PATH"

step "postCreate complete"
echo "Log saved to: $LOG_FILE"
done_step "postCreate complete"
)

post_create_dispatch() {
  post_create_main "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  post_create_dispatch "$@"
fi
