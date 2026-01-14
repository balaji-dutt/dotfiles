#!/usr/bin/env bash
set -euo pipefail

cmd="${1:-}"; shift || true
relsrc="${1:-}"

die(){ echo "ERROR: $*" >&2; exit 2; }
need_rel(){ [[ -n "${relsrc:-}" ]] || die "missing repo-relative source path (e.g. dot_bashrc, ansible/site.yml)"; }
have(){ command -v "$1" >/dev/null 2>&1; }

runtime() {
  if have docker; then echo docker; return 0; fi
  if have podman; then echo podman; return 0; fi
  return 1
}

shellcheck_container() {
  local file_rel="$1"
  local rt; rt="$(runtime)" || { echo "No docker/podman; shellcheck skipped" >&2; return 0; }
  "$rt" run --rm -v "$PWD:/work" -w /work koalaman/shellcheck:stable \
    shellcheck "$file_rel" || true
}

ansible_container_syntax() {
  local file_rel="$1"
  local rt; rt="$(runtime)" || { echo "No docker/podman; ansible syntax-check skipped" >&2; return 0; }
  "$rt" run --rm -t -v "$PWD:/work" -w /work quay.io/ansible/ansible-runner:stable \
    ansible-playbook --syntax-check "$file_rel"
}

ansible_container_lint() {
  local file_rel="$1"
  local rt; rt="$(runtime)" || { echo "No docker/podman; ansible-lint skipped" >&2; return 0; }
  "$rt" run --rm -t -v "$PWD:/work" -w /work quay.io/ansible/ansible-runner:stable \
    sh -lc "command -v ansible-lint >/dev/null 2>&1 && ansible-lint '$file_rel' || exit 0" || true
}

srcdir(){ chezmoi source-path; }
destdir(){ chezmoi target-path; }

target_from_source_rel() {
  chezmoi target-path "$(srcdir)/$relsrc"
}

target_rel_from_source_rel() {
  local t d
  t="$(target_from_source_rel)"
  d="$(destdir)"
  echo "${t#"$d"/}"
}

is_managed_source_rel() {
  local rel
  rel="$(target_rel_from_source_rel)"
  chezmoi managed | grep -Fx "$rel" >/dev/null 2>&1
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

  # Normal managed-target detection
  if is_managed_source_rel; then
    echo "managed:$(target_from_source_rel)"
    return 0
  fi

  case "$relsrc" in
    .chezmoiscripts/*) echo "chezmoiscript:$relsrc" ;;
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
    echo "chezmoi-config file; skipping apply/diff: $relsrc" >&2
    echo "Run: ./tools/cz-audit check $relsrc" >&2
    return 0
  fi
  if ! is_managed_source_rel; then
    echo "Not a managed chezmoi target: $relsrc" >&2
    echo "Classification: $(classify)" >&2
    return 0
  fi
  local t
  t="$(target_from_source_rel)"
  chezmoi --use-builtin-diff --no-pager diff --verbose "$t"
  chezmoi apply --use-builtin-diff --no-pager --dry-run --verbose "$t"
}

check_shell_file_rel() {
  local file_rel="$1"
  bash -n "$file_rel"
  if have shellcheck; then
    shellcheck "$file_rel" || true
  else
    shellcheck_container "$file_rel"
  fi
}

check_ansible_file_rel() {
  local file_rel="$1"
  if have ansible-playbook; then
    ansible-playbook --syntax-check "$file_rel"
    if have ansible-lint; then ansible-lint "$file_rel" || true; else ansible_container_lint "$file_rel"; fi
  else
    ansible_container_syntax "$file_rel"
    ansible_container_lint "$file_rel"
  fi
}

check_configs_file_rel() {
  local file_rel="$1"
  case "$file_rel" in
    *.yml|*.yaml)
      if have python3; then
        python3 - <<'PY' "$file_rel" || true
import sys
try:
  import yaml
except Exception:
  print("PyYAML not installed; YAML parse skipped", file=sys.stderr); sys.exit(0)
with open(sys.argv[1], "r", encoding="utf-8") as f:
  yaml.safe_load(f)
print("YAML OK")
PY
      else
        echo "python3 not available; YAML parse skipped" >&2
      fi
      ;;
    *.toml)
      if have python3; then
        python3 - <<'PY' "$file_rel" || true
import sys
try:
  import tomllib
except Exception:
  print("tomllib not available (need Python 3.11+); TOML parse skipped", file=sys.stderr); sys.exit(0)
with open(sys.argv[1], "rb") as f:
  tomllib.load(f)
print("TOML OK")
PY
      else
        echo "python3 not available; TOML parse skipped" >&2
      fi
      ;;
  esac
}

check_chezmoi_config() {
  local abs
  abs="$(srcdir)/$relsrc"

  echo "Validating chezmoi config file: $relsrc" >&2

  # If templated, ensure it renders on THIS machine.
  if [[ "$relsrc" == *.tmpl ]]; then
    chezmoi execute-template -f "$abs" >/dev/null
    echo "Template renders OK: $relsrc" >&2
  fi

  # General sanity check (may emit warnings; we don't fail hard on them here).
  chezmoi doctor || true
}

check() {
  need_rel
  kind="$(classify)"
  echo "Classification: $kind" >&2

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
        tmp="$(mktemp)"
        chezmoi execute-template -f "$(srcdir)/$relsrc" >"$tmp"
        bash -n "$tmp"
        rm -f "$tmp"
      elif [[ "$relsrc" == *.sh ]]; then
        check_shell_file_rel "$relsrc"
      else
        echo "No automated check for $relsrc (non-shell chezmoi script). Review manually." >&2
      fi
      ;;
    ansible:*)
      if [[ -f "$relsrc" ]]; then
        check_ansible_file_rel "$relsrc"
      else
        echo "ansible path isn't a file; run against a playbook (e.g. ansible/site.yml)" >&2
      fi
      ;;
    configs:*)
      [[ -f "$relsrc" ]] && check_configs_file_rel "$relsrc"
      ;;
    assets:*)
      if [[ "$relsrc" == *.sh ]]; then check_shell_file_rel "$relsrc"; else echo "Assets changed; run project-specific checks if any." >&2; fi
      ;;
    docs:*|repo:*)
      echo "Repo-only file; no chezmoi apply/diff required." >&2
      ;;
  esac
}

case "$cmd" in
  classify) need_rel; classify ;;
  dryrun-if-managed) need_rel; dryrun_if_managed ;;
  check) need_rel; check ;;
  *) die "usage: assets/cz-audit {classify|dryrun-if-managed|check} <repo-relative-source-path>" ;;
esac
