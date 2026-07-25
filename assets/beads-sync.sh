#!/usr/bin/env bash
set -euo pipefail

# beads-sync - Beads/Dolt sync helper for this repo (macOS / Linux / WSL2).
# PowerShell 7 sibling: assets/beads-sync.ps1
#
# WHY THIS EXISTS
# ---------------
# `bd dolt pull` cannot succeed in this repo. During its own pull path, bd writes
# rows into `ignored_schema_migrations` immediately before calling dolt merge.
# That table matches a `dolt_ignore` pattern, so:
#
#   - dolt refuses to stage it   -> `bd dolt commit` reports "nothing to commit"
#   - dolt merge refuses to run against ANY dirty working set, ignored or not
#
# bd therefore deadlocks itself. Verified 2026-07-25 on a provably clean working
# set: `bd dolt pull` still failed with "cannot merge with uncommitted changes"
# and left the table dirty afterwards.
#
# *** DO NOT "SIMPLIFY" THE pull PATH TO CALL `bd dolt pull`. ***
#
# Cleaning the table first does not help - bd re-dirties it inside the same
# invocation. The pull must run as ONE dolt SQL session (reset the ignored
# tables, then dolt_pull) with no bd process in between. This was tried the
# naive way first and it failed on the Windows clone.
#
# SCOPE: only `pull` is affected. Every other bd command works normally. `push`
# is not broken either - it is wrapped here only to bundle the server restart
# that gives the server a live SSH_AUTH_SOCK.
#
# This is a workaround for an upstream bd bug. If bd stops dirtying the table
# before merging, the `pull` command here can be deleted and plain `bd dolt pull`
# used again; `status`, `clean` and `push` do not depend on it.

usage() {
  cat >&2 <<'EOF'
usage: ./assets/beads-sync.sh <command> [--dry-run] [--backup]

commands:
  status   Show server, dirty tables, and whether a sync is safe (read-only)
  clean    Restore dirty dolt_ignore'd tables from HEAD
  pull     Restart server, then clean + dolt_pull in ONE dolt session
  push     Restart server, then bd dolt commit + bd dolt push

flags:
  --dry-run  Print what would run; change nothing
  --backup   Run `bd export --all` to a timestamped file first
EOF
  exit 2
}

die() { echo "ERROR: $*" >&2; exit 2; }
info() { echo "INFO: $*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

# Strip the private remote URL out of anything we echo. The repo is public.
redact() { sed -E 's#(git\+ssh://|ssh://|https://)[^[:space:]"]*#\1<REDACTED>#g'; }

repo_root() {
  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  (cd -- "$script_dir/.." && pwd -P)
}

COMMAND="${1:-}"; shift || true
DRY_RUN=0
DO_BACKUP=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --backup)  DO_BACKUP=1 ;;
    -h|--help) usage ;;
    *) die "unknown flag: $1" ;;
  esac
  shift
done

case "$COMMAND" in
  status|clean|pull|push) ;;
  ""|-h|--help) usage ;;
  *) die "unknown command: $COMMAND" ;;
esac

ROOT="$(repo_root)"
cd "$ROOT"

have dolt || die "dolt not found on PATH"
have bd || die "bd not found on PATH"
[[ -f .beads/metadata.json ]] || die ".beads/metadata.json not found; is this a Beads repo?"

# Connection details come from metadata.json so this also works for other Beads
# databases (e.g. the devcontainer's hliac), not just dots.
read -r DB DB_HOST DB_USER <<EOF
$(python3 - <<'PY'
import json
m = json.load(open('.beads/metadata.json'))
print(m.get('dolt_database', 'beads'),
      m.get('dolt_server_host', '127.0.0.1'),
      m.get('dolt_server_user', 'root'))
PY
)
EOF
[[ -n "${DB:-}" ]] || die "could not read dolt_database from .beads/metadata.json"

# Override the target database. Used to exercise the refusal path against a
# scratch database without touching real issue data, and handy when a checkout
# serves more than one Beads database.
DB="${BEADS_SYNC_DB:-$DB}"

PORT="${BEADS_DOLT_SERVER_PORT:-}"
if [[ -z "$PORT" && -f .beads/dolt-server.port ]]; then
  PORT="$(tr -d '[:space:]' < .beads/dolt-server.port)"
fi
[[ -n "$PORT" ]] || die "no Dolt port; run 'bd dolt start' first"

dolt_sql() {
  dolt --host "$DB_HOST" --port "$PORT" --user "$DB_USER" --password '' --no-tls \
    --use-db "$DB" sql "$@"
}

# One query does the classification: every dirty table, flagged with whether it
# matches a dolt_ignore pattern. LIKE matching happens in SQL so we never have to
# reimplement pattern globbing (dolt_ignore uses patterns such as "wisp_%").
dirty_csv() {
  dolt_sql -r csv -q "
    select s.table_name,
           (select count(*) from dolt_ignore i
             where i.ignored = 1 and s.table_name like i.pattern) as is_ignored
      from dolt_status s;" 2>/dev/null | tail -n +2
}

remote_name() {
  dolt_sql -r csv -q "select name from dolt_remotes limit 1;" 2>/dev/null | tail -n +2
}

# Echo the ignored (safe to reset) dirty tables; die if anything else is dirty.
safe_reset_list() {
  local csv unsafe=() safe=()
  csv="$(dirty_csv)"
  [[ -z "$csv" ]] && return 0

  local table flag
  while IFS=, read -r table flag; do
    [[ -z "$table" ]] && continue
    if [[ "$flag" == "1" ]]; then safe+=("$table"); else unsafe+=("$table"); fi
  done <<< "$csv"

  if [[ ${#unsafe[@]} -gt 0 ]]; then
    echo "ERROR: refusing to reset - these dirty tables are NOT dolt_ignore'd:" >&2
    local t
    for t in "${unsafe[@]}"; do echo "ERROR:   $t" >&2; done
    echo "ERROR: that is real data. Run 'bd dolt commit' first." >&2
    exit 2
  fi

  printf '%s\n' "${safe[@]:-}"
}

backup_if_asked() {
  [[ "$DO_BACKUP" -eq 1 ]] || return 0
  local out
  out="$HOME/${DB}-backup-$(date +%Y%m%d-%H%M%S).jsonl"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would run: bd export --all -o $out"
  else
    bd export --all -o "$out" >&2
    info "backup written: $out"
  fi
}

restart_server() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would restart the Dolt server (live SSH_AUTH_SOCK for the fetch)"
    return 0
  fi
  info "restarting Dolt server so it inherits this shell's SSH_AUTH_SOCK"
  bd dolt stop >&2 || true
  bd dolt start >&2
  PORT="$(tr -d '[:space:]' < .beads/dolt-server.port)"
}

cmd_status() {
  echo "Dolt server: ${DB_HOST}:${PORT}  (database: ${DB})"
  local csv
  csv="$(dirty_csv)"
  if [[ -z "$csv" ]]; then
    echo "Working set clean - sync is safe."
    return 0
  fi
  echo "Dirty tables:"
  local table flag found_unsafe=0
  while IFS=, read -r table flag; do
    [[ -z "$table" ]] && continue
    if [[ "$flag" == "1" ]]; then
      echo "  $table  (ignored by dolt_ignore - safe to reset)"
    else
      echo "  $table  (NOT ignored - real data)"
      found_unsafe=1
    fi
  done <<< "$csv"
  if [[ "$found_unsafe" -eq 1 ]]; then
    echo "Real uncommitted data present. Run 'bd dolt commit' before syncing."
    return 1
  fi
  echo "Only bookkeeping tables dirty - safe to clean."
}

# Build "call dolt_checkout('HEAD','--','<table>');" for each resettable table.
checkout_sql() {
  local tables="$1" sql="" table
  while read -r table; do
    [[ -z "$table" ]] && continue
    sql+="call dolt_checkout('HEAD', '--', '${table}'); "
  done <<< "$tables"
  printf '%s' "$sql"
}

cmd_clean() {
  local tables sql
  tables="$(safe_reset_list)"
  if [[ -z "${tables//[[:space:]]/}" ]]; then
    info "nothing to clean; working set has no dirty ignored tables"
    return 0
  fi
  sql="$(checkout_sql "$tables")"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would run: $sql"
    return 0
  fi
  dolt_sql -q "$sql" >/dev/null
  info "reset: $(echo "$tables" | tr '\n' ' ')"
}

cmd_pull() {
  backup_if_asked
  restart_server

  local tables sql remote
  tables="$(safe_reset_list)"
  remote="$(remote_name)"
  [[ -n "$remote" ]] || die "no Dolt remote configured; see docs/beads.md"

  # THE WHOLE POINT: the reset and the merge run in ONE dolt session. Do not
  # split these, and do not call `bd dolt pull` instead - bd dirties the working
  # set inside its own pull and then fails to merge on its own dirt.
  sql="$(checkout_sql "$tables")call dolt_pull('${remote}');"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would run: $sql"
    return 0
  fi

  info "pulling from '${remote}' (reset + merge in one session)"
  dolt_sql -q "$sql" 2>&1 | tr -d '\r' | redact
}

cmd_push() {
  backup_if_asked
  restart_server
  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would run: bd dolt commit && bd dolt push"
    return 0
  fi
  # push does not merge, so the deadlock does not apply and bd is fine here.
  bd dolt commit 2>&1 | redact || true
  bd dolt push 2>&1 | redact
}

case "$COMMAND" in
  status) cmd_status ;;
  clean)  cmd_clean ;;
  pull)   cmd_pull ;;
  push)   cmd_push ;;
esac
