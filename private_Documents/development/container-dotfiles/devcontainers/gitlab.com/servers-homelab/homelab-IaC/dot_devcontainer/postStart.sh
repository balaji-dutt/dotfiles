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

log_run_header() {
  local workspace
  workspace="$1"

  echo "==== [$(timestamp)] postStart run ===="
  echo "script=${BASH_SOURCE[0]}"
  echo "workspace=$workspace"
  echo "hostname=$(hostname 2>/dev/null || true)"
  echo "note=postStart runs when the container starts or reopens; check /tmp/postCreate.log for create/recreate runs."
}

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
orbstack_host_sock="${ORBSTACK_HOST_SSH_AUTH_SOCK:-}"
orbstack_relay_script="/tmp/host-homelab-devcontainer/orbstack_ssh_agent_relay.py"
orbstack_relay_log="/tmp/orbstack-ssh-agent-relay.log"
orbstack_relay_pid="${sock_dir}/relay.pid"
refresh_public_selectors() {
  python3 - "$@" <<'PY'
import base64
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

root = Path.home() / '.ssh'
selector = root / 'public_averagegeek_2019-ed25519.pub'
fragment = root / 'config.d/dotfiles-git.conf'
config = root / 'config'
include = 'Include "' + str(fragment).replace('\\', '\\\\').replace('"', '\\"') + '"'
fragment_text = '''Host gitlab.com gitlab github.com github
    User git
    IdentitiesOnly yes
    IdentityFile ~/.ssh/public_averagegeek_2019-ed25519.pub
Host gitlab
    HostName gitlab.com
Host github
    HostName github.com
Host *
'''

def warn(message):
    print('WARN: ' + message, file=sys.stderr)

def safe_node(path, directory=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if directory:
            path.mkdir(mode=0o700)
            info = path.lstat()
        else:
            return
    kind_ok = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not kind_ok or info.st_uid != os.getuid() or (not directory and info.st_nlink != 1):
        raise ValueError('unsafe path')
    path.chmod(0o700 if directory else 0o600)

def atomic_write(path, text):
    safe_node(path.parent, directory=True)
    safe_node(path)
    fd, name = tempfile.mkstemp(prefix='.sset-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        safe_node(path)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def configure():
    safe_node(root, directory=True)
    safe_node(fragment.parent, directory=True)
    for path in (selector, config, fragment):
        safe_node(path)
    if fragment.exists() and fragment.read_text() != fragment_text:
        raise ValueError('unmanaged fragment')
    existing = config.read_text() if config.exists() else ''
    remaining = ''.join(line for line in existing.splitlines(keepends=True)
                        if line.strip() != include)
    atomic_write(fragment, fragment_text)
    atomic_write(config, include + '\n' + remaining)
    for host, hostname in (('gitlab', 'gitlab.com'), ('gitlab.com', 'gitlab.com'),
                           ('github', 'github.com'), ('github.com', 'github.com')):
        result = subprocess.run(['ssh', '-G', host],
                                capture_output=True, text=True, timeout=5)
        values = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition(' ')
            values.setdefault(key, []).append(value)
        if (result.returncode or values.get('hostname') != [hostname]
                or values.get('user') != ['git']
                or values.get('identitiesonly') != ['yes']
                or values.get('identityfile') != ['~/.ssh/public_averagegeek_2019-ed25519.pub']):
            raise ValueError('conflicting SSH config from effective host, user, or additive IdentityFile settings')

def export_keys(source):
    safe_node(root, directory=True)
    lines = Path(source).read_text().splitlines()
    ansible = next((line for comment in ('root_terraform_ansible', 'terraform-ansible')
                    for line in lines if comment in line), None)
    preferred = ansible is not None
    ansible = ansible or next((line for line in lines if line.startswith('ssh-')), None)
    failed = False
    try:
        if not ansible:
            raise ValueError('missing Ansible key')
        atomic_write(root / 'root_terraform_ansible.pub', ansible + '\n')
        if not preferred:
            warn('preferred key comments not found; using first SSH agent key instead.')
        print('INFO: refreshed Ansible public selector.', file=sys.stderr)
    except (OSError, ValueError):
        warn('could not refresh Ansible public selector; keeping existing key file.')
        failed = True
    try:
        candidates = set()
        for line in lines:
            fields = line.split(None, 2)
            if len(fields) != 3 or fields[2] != 'bitbucket-avggeek-2019':
                continue
            blob = base64.b64decode(fields[1], validate=True)
            if len(blob) < 4:
                raise ValueError('invalid key')
            length = int.from_bytes(blob[:4], 'big')
            if blob[4:4 + length] != fields[0].encode():
                raise ValueError('invalid key type')
            candidates.add((fields[0], fields[1]))
        if len(candidates) != 1:
            raise ValueError('missing or ambiguous Git key')
        kind, data = candidates.pop()
        public = kind + ' ' + data + ' bitbucket-avggeek-2019\n'
        check = subprocess.run(['ssh-keygen', '-l', '-f', '-'], input=public.encode(),
                               capture_output=True, timeout=5)
        if check.returncode:
            raise ValueError('invalid public key')
        atomic_write(selector, public)
        print('INFO: refreshed Git public selector.', file=sys.stderr)
    except (OSError, ValueError, subprocess.SubprocessError):
        warn('could not select one valid Git key with exact comment bitbucket-avggeek-2019; keeping existing Git selector.')
        failed = True
    return int(failed)

try:
    if sys.argv[1] == 'configure':
        configure()
        sys.exit(0)
    sys.exit(export_keys(sys.argv[2]))
except ValueError as error:
    warn('Git/Ansible selector setup refused: ' + str(error) + '; inspect container-local ~/.ssh configuration.')
    sys.exit(1)
except (OSError, subprocess.SubprocessError):
    warn('Git/Ansible selector setup failed; check container-local ~/.ssh permissions and SSH tools.')
    sys.exit(1)
PY
}

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

agent_recovery_hint() {
  if [[ -n "$orbstack_host_sock" ]]; then
    echo "WARN: Reopen or rebuild the devcontainer to refresh SSH agent forwarding." >&2
  else
    echo "WARN: Run sset on the WSL host in the owning distro, then retry sset in this container." >&2
  fi
}

git_config_exit=0
refresh_public_selectors configure || git_config_exit=$?
if [[ -n "$orbstack_host_sock" ]]; then
  mkdir -p "$sock_dir"
  chmod 700 "$sock_dir" >/dev/null 2>&1 || true
  ensure_orbstack_relay || true
fi

ssh_add_stdout="$(mktemp)"
ssh_add_stderr="$(mktemp)"
trap 'rm -f "$ssh_add_stdout" "$ssh_add_stderr"' EXIT

agent_ready=0
for ((i=0; i<50; i++)); do
  rc=0
  LC_ALL=C timeout 2 ssh-add -l >"$ssh_add_stdout" 2>"$ssh_add_stderr" || rc=$?
  if [[ "$rc" -eq 0 ]] || { [[ "$rc" -eq 1 && ! -s "$ssh_add_stderr" ]] &&
      [[ "$(<"$ssh_add_stdout")" == "The agent has no identities." ]]; }; then
    agent_ready=1
    break
  fi
  sleep 0.1
done

if [[ "$agent_ready" -ne 1 ]]; then
  echo "WARN: SSH agent did not become ready; keeping existing Git and Ansible key files." >&2
  agent_recovery_hint
  exit 1
fi

ssh_add_exit=0

timeout 5 ssh-add -L >"$ssh_add_stdout" 2>"$ssh_add_stderr" || ssh_add_exit=$?

if [[ "$ssh_add_exit" -eq 0 ]]; then
  refresh_public_selectors export "$ssh_add_stdout" || exit $?
  exit "$git_config_exit"
else
  if [[ "$ssh_add_exit" -eq 1 ]]; then
    echo "WARN: SSH agent available but has no keys to export for Git or Ansible." >&2
  elif [[ "$ssh_add_exit" -eq 124 ]]; then
    echo "WARN: ssh-add -L timed out; SSH agent appears unhealthy." >&2
    agent_recovery_hint
  else
    echo "WARN: SSH agent socket is not usable (ssh-add -L exit $ssh_add_exit); Git and Ansible selectors retained." >&2
    agent_recovery_hint
  fi
  exit 1
fi
EOF

  chmod 700 "$helper_path"
}

post_start_persistence_phase() {
  local workspace_root
  workspace_root="$1"

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
}

post_start_optional_integrations_phase() {
  register_claude_mcp_servers
}

post_start_materialization_phase() {
  local workspace_root persisted_opencode_profiles normalized_persisted_profiles
  workspace_root="$1"

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
}

post_start_runtime_configuration() {
  local workspace_root
  workspace_root="$1"

  post_start_persistence_phase "$workspace_root"
  post_start_optional_integrations_phase
  post_start_materialization_phase "$workspace_root"
}

post_start_check_host_relay() {
  local relay_client
  relay_client="$HOME/.local/bin/devcontainer-host-relay"
  if [[ ! -x "$relay_client" ]] || ! "$relay_client" ping >/dev/null; then
    echo "WARN: devcontainer host relay is unreachable; browser opening and clipboard images may fall back or fail. Check initializeCommand and the host relay connection." >&2
  fi
}

post_start_refresh_ssh() {
  if ! "$HOME/.local/bin/sset"; then
    echo "WARN: sset refresh failed; Git or Ansible may not be able to use SSH keys." >&2
    echo "WARN: If this persists, rerun sset or reopen/rebuild the container." >&2
  fi
}

post_start_main() (
set -Eeuo pipefail

LOG_FILE="${LOG_FILE:-/tmp/postStart.log}"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee "$LOG_FILE") 2>&1

workspace_root="${1:-}"
if [[ -z "$workspace_root" ]] && command -v git >/dev/null 2>&1; then
  workspace_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
workspace_root="${workspace_root:-$PWD}"
log_run_header "$workspace_root"
ensure_git_safe_directories "$workspace_root"
post_start_runtime_configuration "$workspace_root"
post_start_check_host_relay
post_start_refresh_ssh
)

post_start_dispatch() {
  post_start_main "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  post_start_dispatch "$@"
fi
