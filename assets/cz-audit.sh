#!/usr/bin/env bash
set -euo pipefail
exec 3>&2

cmd="${1:-}"; shift || true
relsrc="${1:-}"

# Normalize common user input forms ("./foo", ".\\foo") into repo-relative paths.
relsrc="${relsrc#./}"
relsrc="${relsrc#.\\}"
relsrc="${relsrc#/}"
relsrc="${relsrc#\\}"
relsrc="${relsrc//\\//}"

die(){ echo "ERROR: $*" >&2; exit 2; }
need_rel(){ [[ -n "${relsrc:-}" ]] || die "missing repo-relative source path (e.g. dot_bashrc, ansible/site.yml)"; }
have(){ command -v "$1" >/dev/null 2>&1; }

runtime() {
  if have docker; then echo docker; return 0; fi
  if have podman; then echo podman; return 0; fi
  return 1
}

readonly ANSIBLE_IMAGE="local/ansible-syntax:repo"
readonly POWERSHELL_IMAGE="local/powershell-audit:lts"

repo_root() {
  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  (cd -- "$script_dir/.." && pwd -P)
}

info(){ echo "INFO: $*" >&3; }

# Always run checks from repo root so "repo-relative paths" actually resolve.
ROOT="$(repo_root)"
cd "$ROOT"

# Load default variables for syntax checks.
[[ -f "assets/cz-audit.env" ]] && source "assets/cz-audit.env"

normalize_dir() {
  local dir="$1"
  (cd -- "$dir" 2>/dev/null && pwd -P) || return 1
}

init_chezmoi_source_dir() {
  if [[ -n "${CHEZMOI_SOURCE_DIR:-}" ]]; then
    info "CHEZMOI_SOURCE_DIR override: $CHEZMOI_SOURCE_DIR"
    return 0
  fi

  have chezmoi || return 0
  have git || return 0

  local configured_source configured_real root_real
  configured_source="$(chezmoi source-path 2>/dev/null || true)"
  [[ -n "$configured_source" ]] || return 0

  configured_real="$(normalize_dir "$configured_source")" || return 0
  root_real="$(normalize_dir "$ROOT")" || return 0
  [[ "$configured_real" != "$root_real" ]] || return 0

  git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0

  CHEZMOI_SOURCE_DIR="$ROOT"
  export CHEZMOI_SOURCE_DIR
  info "CHEZMOI_SOURCE_DIR auto-detected: $CHEZMOI_SOURCE_DIR"
}

init_chezmoi_source_dir

cm() {
  if [[ -n "${CHEZMOI_SOURCE_DIR:-}" ]]; then
    chezmoi --source "$CHEZMOI_SOURCE_DIR" "$@"
  else
    chezmoi "$@"
  fi
}

audit_logdir() {
  local d="${CZ_AUDIT_LOGDIR:-.cz-audit}"
  mkdir -p "$d"
  echo "$d"
}

audit_clean_logs_once() {
  [[ "${CZ_AUDIT_CLEAN_LOGS:-1}" -eq 1 ]] || return 0
  local d; d="$(audit_logdir)"
  rm -f "$d"/*.log 2>/dev/null || true
}

sanitize_key() {
  # Turn "ansible/tasks/foo.yml" into "ansible__tasks__foo.yml"
  echo "${1//[^A-Za-z0-9._-]/_}" | tr '/' '_'
}

audit_clean_logs_once

# Capture stdout+stderr of a command without letting `set -e` abort the script.
# Sets global AUDIT_OUT, AUDIT_RC.
audit_capture() {
  AUDIT_OUT=""
  AUDIT_RC=0
  set +e
  AUDIT_OUT="$("$@" 2>&1)"
  AUDIT_RC=$?
  set -e
}

# Handle a check result in a way that is "agent-friendly":
# - By default, advisory tools (lint/shellcheck/etc.) do NOT print their scary output.
# - Output is saved to a log file and you get a single INFO line.
# - Strict mode prints the full output and returns non-zero.
#
# Env controls:
#   CZ_AUDIT_STRICT=1                     -> enforce all advisory checks (fail)
#   CZ_AUDIT_SHOW=1                       -> print tool output even when not failing
#   CZ_AUDIT_STRICT_<CHECK>=1             -> enforce just one check (e.g. CZ_AUDIT_STRICT_ANSIBLE_LINT=1)
#   CZ_AUDIT_SHOW_<CHECK>=1               -> show output for just one check
audit_handle() {
  local check="$1"        # e.g. ANSIBLE_LINT, SHELLCHECK, YAML, TOML, CHEZMOI_DOCTOR
  local subject="${2:-}"  # file path or label
  local advisory="${3:-1}" # 1=advisory (default), 0=enforced (always fail on rc!=0)

  local strict_var="CZ_AUDIT_STRICT_${check}"
  local show_var="CZ_AUDIT_SHOW_${check}"

  local strict="${!strict_var-}"
  local show="${!show_var-}"

  [[ -z "${strict:-}" ]] && strict="${CZ_AUDIT_STRICT:-0}"
  [[ -z "${show:-}"   ]] && show="${CZ_AUDIT_SHOW:-0}"

  # If no output and success, be quiet.
  if [[ $AUDIT_RC -eq 0 ]]; then
    if [[ "$show" == "1" && -n "${AUDIT_OUT:-}" ]]; then
      printf '%s\n' "$AUDIT_OUT" >&2
    fi
    return 0
  fi

  # Enforced checks always fail and print output.
  if [[ "$advisory" == "0" ]]; then
    printf '%s\n' "$AUDIT_OUT" >&2
    return "$AUDIT_RC"
  fi

  # Advisory checks:
  if [[ "$strict" == "1" ]]; then
    printf '%s\n' "$AUDIT_OUT" >&2
    return "$AUDIT_RC"
  fi

  # Non-strict advisory: log output, print one INFO line.
  local logdir; logdir="$(audit_logdir)"
  local key; key="$(sanitize_key "${subject:-unknown}")"
  local logfile="$logdir/${check}.${key}.log"
  printf '%s\n' "$AUDIT_OUT" >"$logfile"

  info "${check} found issues (advisory). Full output saved to: $logfile"
  info "Set ${strict_var}=1 (or CZ_AUDIT_STRICT=1) to enforce; set ${show_var}=1 (or CZ_AUDIT_SHOW=1) to print output."
  return 0
}

audit_unavailable() {
  local check="$1"
  local subject="${2:-unknown}"
  local advisory="${3:-1}"
  local strict_var="CZ_AUDIT_STRICT_${check}"
  local strict="${!strict_var-}"

  [[ -z "${strict:-}" ]] && strict="${CZ_AUDIT_STRICT:-0}"

  if [[ "$advisory" == "0" || "$strict" == "1" ]]; then
    [[ -n "${AUDIT_OUT:-}" ]] && printf '%s\n' "$AUDIT_OUT" >&2
    return "${AUDIT_RC:-127}"
  fi

  if [[ -n "${AUDIT_OUT:-}" ]]; then
    local logdir key logfile
    logdir="$(audit_logdir)"
    key="$(sanitize_key "$subject")"
    logfile="$logdir/${check}.${key}.unavailable.log"
    printf '%s\n' "$AUDIT_OUT" >"$logfile"
    info "${check} unavailable; check skipped. Details saved to: $logfile"
  else
    info "${check} unavailable; check skipped."
  fi
  info "Set ${strict_var}=1 (or CZ_AUDIT_STRICT=1) to require this validator."
  return 0
}

audit_finish() {
  local check="$1"
  local subject="${2:-unknown}"
  local advisory="${3:-1}"

  case "$AUDIT_RC" in
    125|126|127) audit_unavailable "$check" "$subject" "$advisory" ;;
    *) audit_handle "$check" "$subject" "$advisory" ;;
  esac
}

ensure_container_image() {
  local rt="$1"
  local image="$2"
  local dockerfile="$3"

  if "$rt" image inspect "$image" >/dev/null 2>&1; then
    return 0
  fi

  info "Building missing audit image $image from $dockerfile; this may access the network."
  if ! "$rt" build -f "$dockerfile" -t "$image" "$ROOT"; then
    printf 'Unable to build required audit image: %s\n' "$image" >&2
    return 127
  fi
}

shellcheck_container() {
  local file_rel="$1"
  local rt; rt="$(runtime)" || { printf 'shellcheck and docker/podman are unavailable\n' >&2; return 127; }
  # koalaman/shellcheck image uses shellcheck as ENTRYPOINT
  "$rt" run --rm -v "$ROOT:/work" -w /work koalaman/shellcheck:stable \
    "$file_rel"
}

ansible_container_syntax() {
  local file_rel="$1"
  local rt; rt="$(runtime)" || { printf 'ansible-playbook and docker/podman are unavailable\n' >&2; return 127; }
  ensure_container_image "$rt" "$ANSIBLE_IMAGE" "assets/Dockerfile.ansible-syntax" || return 127
  "$rt" run --rm -t \
    -v "$ROOT:/work" -w /work \
    "$ANSIBLE_IMAGE" ansible-playbook -i localhost, --syntax-check "$file_rel"
}

ansible_container_lint() {
  local file_rel="$1"
  local rt; rt="$(runtime)" || { printf 'ansible-lint and docker/podman are unavailable\n' >&2; return 127; }
  ensure_container_image "$rt" "$ANSIBLE_IMAGE" "assets/Dockerfile.ansible-syntax" || return 127
  local cfg="ansible/.ansible-lint.yml"
  "$rt" run --rm -t \
    -v "$ROOT:/work" -w /work \
    "$ANSIBLE_IMAGE" ansible-lint -c "$cfg" "$file_rel"
}

is_wsl() {
  [[ -r /proc/sys/kernel/osrelease ]] || return 1
  grep -qi microsoft /proc/sys/kernel/osrelease
}

powershell_parser_script() {
  local source_label="${1//\'/\'\'}"
  # shellcheck disable=SC2016 # PowerShell variable must remain literal.
  printf '$sourceLabel = '\''%s'\''\n' "$source_label"
  cat <<'POWERSHELL'
$sourceText = [Console]::In.ReadToEnd()
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseInput(
  $sourceText,
  $sourceLabel,
  [ref]$tokens,
  [ref]$parseErrors
)
foreach ($parseError in $parseErrors) {
  $extent = $parseError.Extent
  [Console]::Error.WriteLine(
    '{0}:{1}:{2}: {3}',
    $sourceLabel,
    $extent.StartLineNumber,
    $extent.StartColumnNumber,
    $parseError.Message
  )
}
if ($parseErrors.Count -gt 0) { exit 1 }
POWERSHELL
}

parse_powershell_content() {
  local source_label="$1"
  local content_file="$2"
  local parser
  parser="$(powershell_parser_script "$source_label")"

  if have pwsh; then
    pwsh -NoLogo -NoProfile -NonInteractive -Command "$parser" <"$content_file"
    return
  fi

  if is_wsl; then
    local host_pwsh=""
    if have pwsh.exe; then
      host_pwsh="$(command -v pwsh.exe)"
    elif [[ -x "/mnt/c/Program Files/PowerShell/7/pwsh.exe" ]]; then
      host_pwsh="/mnt/c/Program Files/PowerShell/7/pwsh.exe"
    fi

    if [[ -n "$host_pwsh" ]]; then
      "$host_pwsh" -NoLogo -NoProfile -NonInteractive -Command "$parser" <"$content_file"
      return
    fi
  fi

  local rt
  rt="$(runtime)" || { printf 'pwsh and docker/podman are unavailable\n' >&2; return 127; }
  ensure_container_image "$rt" "$POWERSHELL_IMAGE" "assets/Dockerfile.powershell-audit" || return 127
  "$rt" run --rm -i --entrypoint pwsh "$POWERSHELL_IMAGE" \
    -NoLogo -NoProfile -NonInteractive -Command "$parser" <"$content_file"
}

srcdir(){ cm source-path; }
destdir(){ cm target-path; }

target_from_source_rel() {
  cm target-path "$(srcdir)/$relsrc"
}

target_rel_from_source_rel() {
  local t d
  t="$(target_from_source_rel)"
  d="$(destdir)"
  echo "${t#"$d"/}"
}

is_managed_source_rel() {
  # If the source entry doesn't exist (e.g. user passed dot_bashrc instead of
  # dot_bashrc.tmpl), treat as unmanaged instead of failing.
  if [[ ! -e "$(srcdir)/$relsrc" ]]; then
    info "source file not found, skipping for audit verification: $relsrc"
    return 1
  fi

  local rel
  rel="$(target_rel_from_source_rel)"
  [[ -n "${rel:-}" ]] || return 1

  cm managed | grep -Fx "$rel" >/dev/null 2>&1
}

is_chezmoi_config_file() {
  case "$relsrc" in
    .chezmoiignore|.chezmoiignore.tmpl|.chezmoiremove|.chezmoiremove.tmpl|.chezmoi.toml|.chezmoi.toml.tmpl|.chezmoidata.*|.chezmoiroot)
      return 0 ;;
    *) return 1 ;;
  esac
}

classify() {
  need_rel

  # Special/config files: treat as repo-only config
  if is_chezmoi_config_file; then
    echo "chezmoi-config:$relsrc"
    return 0
  fi

  # Chezmoi scripts need source-level syntax checks even when chezmoi reports
  # their generated targets as managed.
  if [[ "$relsrc" == .chezmoiscripts/* ]]; then
    echo "chezmoiscript:$relsrc"
    return 0
  fi

  # Normal managed-target detection
  if is_managed_source_rel; then
    echo "managed:$(target_from_source_rel)"
    return 0
  fi

  case "$relsrc" in
    ansible/*)         echo "ansible:$relsrc" ;;
    assets/*)          echo "assets:$relsrc" ;;
    configs/*)         echo "configs:$relsrc" ;;
    docs/*|CLAUDE.md|AGENTS.md|README.md|TODO.md) echo "docs:$relsrc" ;;
    bootstrap-wsl.sh)  echo "bootstrap:$relsrc" ;;
    *)                 echo "repo:$relsrc" ;;
  esac
}

dryrun_if_managed() {
  need_rel
  if is_chezmoi_config_file; then
    info "chezmoi-config file; skipping apply/diff: $relsrc"
    info "Run: ./assets/cz-audit.sh check $relsrc"
    return 0
  fi
  if ! is_managed_source_rel; then
    info "Not a managed chezmoi target: $relsrc"
    info "Classification: $(classify)"
    return 0
  fi
  local t
  t="$(target_from_source_rel)"
  cm --use-builtin-diff --no-pager diff --verbose "$t"
  cm apply --use-builtin-diff --no-pager --dry-run --verbose "$t"
}

check_shell_file_rel() {
  local file_rel="$1"

  # Enforced: bash syntax must be valid.
  bash -n "$file_rel"

  # Advisory: shellcheck is useful, but noisy; don’t print raw output by default.
  if have shellcheck; then
    audit_capture shellcheck "$file_rel"
  else
    audit_capture shellcheck_container "$file_rel"
  fi
  audit_finish "SHELLCHECK" "$file_rel" 1
}

check_ansible_file_rel() {
  local file_rel="$1"

  # Enforced: syntax-check should be clean; suppress warnings unless failing.
  if have ansible-playbook; then
    audit_capture ansible-playbook -i localhost, --syntax-check "$file_rel"
  else
    audit_capture ansible_container_syntax "$file_rel"
  fi
  # advisory=0 (enforced)
  audit_finish "ANSIBLE_SYNTAX" "$file_rel" 0

  # Advisory: ansible-lint; do not print raw output by default.
  if have ansible-lint; then
    # Use repo-local config if present; otherwise fall back to default behavior.
    if [[ -f "ansible/.ansible-lint.yml" ]]; then
      audit_capture ansible-lint -c "ansible/.ansible-lint.yml" "$file_rel"
    else
      audit_capture ansible-lint "$file_rel"
    fi
  else
    audit_capture ansible_container_lint "$file_rel"
  fi
  audit_finish "ANSIBLE_LINT" "$file_rel" 1
}

check_configs_file_rel() {
  local file_rel="$1"
  case "$file_rel" in
    *.yml|*.yaml)
      if have python3; then
        # Advisory: YAML parse. If PyYAML missing or parse fails, log it but don’t spam output by default.
        audit_capture python3 - <<'PY' "$file_rel"
import sys
try:
  import yaml
except Exception:
  print("PyYAML not installed; YAML validator unavailable", file=sys.stderr)
  sys.exit(127)
with open(sys.argv[1], "r", encoding="utf-8") as f:
  yaml.safe_load(f)
print("YAML OK")
PY
        audit_finish "YAML" "$file_rel" 1
      else
        AUDIT_OUT="python3 not available; YAML validator unavailable"
        AUDIT_RC=127
        audit_unavailable "YAML" "$file_rel" 1
      fi
      ;;
    *.toml)
      if have python3; then
        audit_capture python3 - <<'PY' "$file_rel"
import sys
try:
  import tomllib
except Exception:
  print("tomllib not available (need Python 3.11+); TOML validator unavailable", file=sys.stderr)
  sys.exit(127)
with open(sys.argv[1], "rb") as f:
  tomllib.load(f)
print("TOML OK")
PY
        audit_finish "TOML" "$file_rel" 1
      else
        AUDIT_OUT="python3 not available; TOML validator unavailable"
        AUDIT_RC=127
        audit_unavailable "TOML" "$file_rel" 1
      fi
      ;;
  esac
}

check_powershell_file_rel() {
  local file_rel="$1"
  local tmp
  tmp="$(mktemp)"

  if [[ "$file_rel" == *.ps1.tmpl ]]; then
    if ! cm execute-template -f "$(srcdir)/$file_rel" >"$tmp"; then
      rm -f "$tmp"
      return 1
    fi
  elif [[ "$file_rel" == *.ps1 ]]; then
    if ! cp -- "$file_rel" "$tmp"; then
      rm -f "$tmp"
      return 1
    fi
  else
    rm -f "$tmp"
    return 0
  fi

  audit_capture parse_powershell_content "$file_rel" "$tmp"
  rm -f "$tmp"
  audit_finish "POWERSHELL_PARSE" "$file_rel" 0
  info "PowerShell renders and parses OK: $file_rel"
}

check_chezmoi_config() {
  local abs
  abs="$(srcdir)/$relsrc"

  info "Validating chezmoi config file: $relsrc"

  # If templated, ensure it renders on THIS machine.
  if [[ "$relsrc" == *.tmpl ]]; then
    if [[ "$relsrc" == ".chezmoi.toml.tmpl" ]]; then
      cm execute-template --init -f "$abs" >/dev/null
    else
      cm execute-template -f "$abs" >/dev/null
    fi
    info "Template renders OK: $relsrc"
  fi

  # Advisory: may emit warnings; capture and suppress by default.
  audit_capture cm doctor
  audit_handle "CHEZMOI_DOCTOR" "$relsrc" 1
}

check() {
  need_rel
  local kind
  kind="$(classify)"
  info "Classification: $kind"

  case "$kind" in
    chezmoi-config:*)
      check_chezmoi_config
      ;;
    managed:*)
      dryrun_if_managed
      ;;
    bootstrap:*)
      check_shell_file_rel "$relsrc"
      ;;
    chezmoiscript:*)
      if [[ "$relsrc" == *.sh.tmpl ]]; then
        local tmp
        tmp="$(mktemp)"
        cm execute-template -f "$(srcdir)/$relsrc" >"$tmp"
        bash -n "$tmp"
        rm -f "$tmp"
      elif [[ "$relsrc" == *.sh ]]; then
        check_shell_file_rel "$relsrc"
      elif [[ "$relsrc" == *.ps1 || "$relsrc" == *.ps1.tmpl ]]; then
        check_powershell_file_rel "$relsrc"
      else
        info "No automated check for $relsrc. Review manually."
      fi
      ;;
    ansible:*)
      if [[ -f "$relsrc" ]]; then
        check_ansible_file_rel "$relsrc"
      else
        info "ansible path isn't a file; run against a playbook (e.g. ansible/site.yml)"
      fi
      ;;
    configs:*)
      if [[ -f "$relsrc" ]]; then
        check_configs_file_rel "$relsrc"
      fi
      ;;
    assets:*)
      if [[ "$relsrc" == *.sh ]]; then
        check_shell_file_rel "$relsrc"
      elif [[ "$relsrc" == *.ps1 || "$relsrc" == *.ps1.tmpl ]]; then
        check_powershell_file_rel "$relsrc"
      else
        info "Assets changed; run project-specific checks if any."
      fi
      ;;
    docs:*|repo:*)
      if [[ "$relsrc" == *.ps1 || "$relsrc" == *.ps1.tmpl ]]; then
        check_powershell_file_rel "$relsrc"
      else
        info "Repo-only file; no chezmoi apply/diff required."
      fi
      ;;
  esac
}

case "$cmd" in
  classify) need_rel; classify ;;
  dryrun-if-managed) need_rel; dryrun_if_managed ;;
  check) need_rel; check ;;
  *) die "usage: assets/cz-audit {classify|dryrun-if-managed|check} <repo-relative-source-path>" ;;
esac
