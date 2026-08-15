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
# SCOPE: only `pull` needs the one-session SQL workaround. `push` is routed here
# too so an empty Dolt remote cannot fall through to bd's git-origin derivation,
# and to restart the server with a live SSH_AUTH_SOCK. Other bd commands work
# normally.
#
# This is a workaround for an upstream bd bug. If bd stops dirtying the table
# before merging, the `pull` command here can be deleted and plain `bd dolt pull`
# used again; the push remote guard remains independently useful.

usage() {
  cat >&2 <<'EOF'
usage: ./assets/beads-sync.sh <command> [--dry-run] [--backup] [--if-due]

commands:
  status   Show server, dirty tables, and whether a sync is safe (read-only)
  clean    Restore dirty dolt_ignore'd tables from HEAD
  pull     Restart server, then clean + dolt_pull in ONE dolt session
  push     Restart server, then bd dolt commit + bd dolt push
  snapshot Export a JSONL recovery snapshot to the shared snapshot root
  init     Rebuild this peer from the sync remote after `.beads/dolt` was
           dropped (bd init + dolt_fetch + hard reset; see docs/beads.md)

flags:
  --dry-run     Print what would run; change nothing
  --backup      pull/push only: abort when the pre-sync snapshot fails
  --if-due      snapshot only: respect the automatic snapshot throttle
  --prefix <p>  init only: issue prefix passed to bd init (default: the
                dolt_database name; throwaway - the reset adopts the remote's)
EOF
  exit 2
}

die() { echo "ERROR: $*" >&2; exit 2; }
info() { echo "INFO: $*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }

resolve_managed_executable() {
  local tool="$1" candidate

  candidate="$(type -P "$tool" || true)"
  if [[ -n "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi

  # WSL2 owns real files in local-bin; macOS local-bin links to mise shims.
  for candidate in \
    "$HOME/.local/bin/$tool" \
    "${XDG_DATA_HOME:-$HOME/.local/share}/mise/shims/$tool"; do
    if [[ -x "$candidate" && ! -d "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

# Strip the private remote URL out of anything we echo. The repo is public.
redact() { sed -E 's#(git\+ssh://|ssh://|https://)[^[:space:]"]*#\1<REDACTED>#g'; }

repo_root() {
  local script_dir
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  (cd -- "$script_dir/.." && pwd -P)
}

# All operations below this point touch the shared filesystem. The parent
# always runs this private worker in a separate process group with a deadline.
snapshot_worker() {
  python3 - "$@" <<'PY'
import datetime
import os
import pathlib
import shutil
import sys
import time

root, database, machine, source, count, due, interval, retention = sys.argv[1:]
interval = int(interval)
retention = int(retention)
due = due == "1"
if os.environ.get("BD_SNAPSHOT_TEST_DELAY_SECONDS"):
    time.sleep(float(os.environ["BD_SNAPSHOT_TEST_DELAY_SECONDS"]))
root_path = pathlib.Path(root)
machine_dir = root_path / "beads-snapshots" / database / machine
lock_dir = machine_dir / ".snapshot.lock"
lock_held = False
published_tmp = None

try:
    if not root_path.is_dir():
        raise RuntimeError(f"snapshot root is unavailable: {root}")
    machine_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(machine_dir, os.W_OK):
        raise RuntimeError(f"snapshot directory is not writable: {machine_dir}")

    try:
        lock_dir.mkdir()
        lock_held = True
    except FileExistsError:
        try:
            lock_age = time.time() - lock_dir.stat().st_mtime
        except OSError:
            lock_age = 0
        if lock_age > max(300, interval * 2):
            shutil.rmtree(lock_dir, ignore_errors=True)
            try:
                lock_dir.mkdir()
            except FileExistsError:
                raise RuntimeError("snapshot worker is already active")
            lock_held = True
        elif due:
            print("SKIP: snapshot worker is already active")
            raise SystemExit(0)
        else:
            raise RuntimeError("snapshot worker is already active")

    stale_after = max(300, interval * 2)
    for stale_tmp in machine_dir.glob(f".{database}-{machine}-*.tmp"):
        try:
            if time.time() - stale_tmp.stat().st_mtime > stale_after:
                stale_tmp.unlink(missing_ok=True)
        except OSError:
            pass

    completed = sorted(
        machine_dir.glob(f"{database}-{machine}-*.jsonl"),
        key=lambda item: (item.stat().st_mtime, item.name),
        reverse=True,
    )
    if due and completed and time.time() - completed[0].stat().st_mtime < interval:
        print(f"SKIP: newest snapshot is still inside the {interval}-second window")
        raise SystemExit(0)

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final_path = machine_dir / f"{database}-{machine}-{stamp}-{count}.jsonl"
    published_tmp = machine_dir / f".{final_path.name}.{os.getpid()}.tmp"
    with open(source, "rb") as src, open(published_tmp, "xb") as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(published_tmp, final_path)
    published_tmp = None

    completed = sorted(
        machine_dir.glob(f"{database}-{machine}-*.jsonl"),
        key=lambda item: (item.stat().st_mtime, item.name),
        reverse=True,
    )
    for old_snapshot in completed[retention:]:
        old_snapshot.unlink(missing_ok=True)
    print(f"SNAPSHOT: {final_path}")
except SystemExit:
    raise
except Exception as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    raise SystemExit(1)
finally:
    if published_tmp is not None:
        try:
            published_tmp.unlink()
        except OSError:
            pass
    if lock_held:
        try:
            lock_dir.rmdir()
        except OSError:
            pass
PY
}

if [[ "${1:-}" == "__snapshot-worker" ]]; then
  shift
  snapshot_worker "$@"
  exit $?
fi

COMMAND="${1:-}"; shift || true
DRY_RUN=0
DO_BACKUP=0
IF_DUE=0
PREFIX_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --backup)  DO_BACKUP=1 ;;
    --if-due)  IF_DUE=1 ;;
    --prefix)
      shift
      [[ $# -gt 0 && -n "$1" ]] || die "--prefix needs a value"
      PREFIX_OVERRIDE="$1"
      ;;
    -h|--help) usage ;;
    *) die "unknown flag: $1" ;;
  esac
  shift
done

case "$COMMAND" in
  status|clean|pull|push|snapshot|init) ;;
  ""|-h|--help) usage ;;
  *) die "unknown command: $COMMAND" ;;
esac

[[ -z "$PREFIX_OVERRIDE" || "$COMMAND" == "init" ]] || die "--prefix is init-only"
[[ "$IF_DUE" -eq 0 || "$COMMAND" == "snapshot" ]] || die "--if-due is snapshot-only"
[[ "$DO_BACKUP" -eq 0 || "$COMMAND" == "pull" || "$COMMAND" == "push" ]] || die "--backup is pull/push-only"

ROOT="$(repo_root)"
cd "$ROOT"
SCRIPT_PATH="$ROOT/assets/beads-sync.sh"

BD_EXE="$(resolve_managed_executable bd || true)"
[[ -n "$BD_EXE" ]] || die "bd executable not found on PATH or in managed locations"
[[ -f .beads/metadata.json ]] || die ".beads/metadata.json not found; is this a Beads repo?"

DOLT_EXE=""
if [[ "$COMMAND" != "snapshot" ]]; then
  DOLT_EXE="$(resolve_managed_executable dolt || true)"
  [[ -n "$DOLT_EXE" ]] || die "dolt executable not found on PATH or in managed locations"
fi

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
# init establishes the server itself; snapshot exports through bd without
# requiring an external Dolt CLI or a pre-existing port file.
if [[ "$COMMAND" != "init" && "$COMMAND" != "snapshot" ]]; then
  [[ -n "$PORT" ]] || die "no Dolt port; run 'bd dolt start' first"
fi

dolt_sql() {
  [[ -n "$DOLT_EXE" ]] || die "dolt not resolved for command: $COMMAND"
  "$DOLT_EXE" --host "$DB_HOST" --port "$PORT" --user "$DB_USER" --password '' --no-tls \
    --use-db "$DB" sql "$@"
}

require_dolt_server() {
  if ! dolt_sql -q "select 1;" >/dev/null 2>&1; then
    die "Dolt server is unavailable at ${DB_HOST}:${PORT}; run 'bd dolt start' first"
  fi
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

# The private sync remote URL. Never echo it unredacted - the repo is public.
# Falls through to BD_SYNC_REMOTE when the file is absent OR lacks the key,
# matching the two documented ways of providing the URL (docs/beads.md).
sync_remote_url() {
  local from_file=""
  if [[ -f .beads/config.local.yaml ]]; then
    from_file="$(python3 - <<'PY'
import re, sys
text = open('.beads/config.local.yaml').read()
m = re.search(r'^\s*remote:\s*"?([^"\n]+?)"?\s*$', text, re.M)
sys.stdout.write(m.group(1) if m else '')
PY
)"
  fi
  printf '%s' "${from_file:-${BD_SYNC_REMOTE:-}}"
}

# Best-effort listener check. Returning 1 when we cannot check is acceptable:
# bd init fails loudly on a genuine port collision anyway.
port_in_use() {
  local p="$1"
  if have ss; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${p}\$"
  elif have nc; then
    nc -z 127.0.0.1 "$p" 2>/dev/null
  else
    return 1
  fi
}

# bd init must take the from-zero LOCAL path. If it can see a git 'origin' or
# a configured sync remote it prints "initialized from git remote!" and takes
# the clone-ish path instead - inheriting the poisoned migration cursor with
# NO wisp tables (verified 2026-08-01 in a test clone; the wisp assertion in
# cmd_init caught it). cmd_init hides both for the duration of bd init; this
# restores them and is safe to run twice (EXIT trap + explicit call).
INIT_HOLD_ORIGIN=0
restore_init_holds() {
  if [[ "$INIT_HOLD_ORIGIN" -eq 1 ]]; then
    INIT_HOLD_ORIGIN=0
    git remote rename beads-init-hold origin 2>/dev/null ||
      echo "WARNING: could not rename git remote 'beads-init-hold' back to 'origin' - fix manually." >&2
  fi
  # The sentinel file IS the state; no flag needed and safe to run twice.
  if [[ -f .beads/config.local.yaml.init-hold ]]; then
    mv .beads/config.local.yaml.init-hold .beads/config.local.yaml
  fi
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

normalize_snapshot_component() {
  python3 - "$1" "$2" <<'PY'
import re
import sys
value = re.sub(r'[^a-z0-9._-]+', '-', sys.argv[1].lower()).strip('._-')
print(value or sys.argv[2])
PY
}

snapshot_root_and_machine() {
  local root="${BD_SNAPSHOT_ROOT:-}" platform host distro machine
  host="$(normalize_snapshot_component "$(hostname 2>/dev/null || true)" host)"

  if [[ -n "${WSL_DISTRO_NAME:-}" ]] || { [[ -r /proc/version ]] && command grep -qi microsoft /proc/version; }; then
    platform=wsl
    [[ -n "$root" ]] || root=/mnt/devdrive
    distro="$(normalize_snapshot_component "${WSL_DISTRO_NAME:-wsl}" wsl)"
    machine="${host}-${distro}"
  elif [[ "$(uname -s 2>/dev/null || true)" == Darwin ]]; then
    platform=macos
    [[ -n "$root" ]] || root=/Volumes/devdrive
    machine="${host}-${platform}"
  else
    platform=linux
    machine="${host}-${platform}"
  fi

  machine="$(normalize_snapshot_component "${BD_SNAPSHOT_MACHINE:-$machine}" machine)"
  printf '%s\n%s\n' "$root" "$machine"
}

validate_snapshot_settings() {
  local interval="$1" retention="$2" deadline="$3"
  [[ "$interval" =~ ^[0-9]+$ ]] || die "BD_SNAPSHOT_INTERVAL_SECONDS must be a non-negative integer"
  [[ "$retention" =~ ^[1-9][0-9]*$ ]] || die "BD_SNAPSHOT_RETENTION must be a positive integer"
  [[ "$deadline" =~ ^[1-9][0-9]*$ ]] || die "BD_SNAPSHOT_DEADLINE_SECONDS must be a positive integer"
}

automatic_snapshots_enabled() {
  case "${BD_AUTO_SNAPSHOT:-1}" in
    0|false|FALSE|no|NO|off|OFF) return 1 ;;
  esac
  return 0
}

attempt_is_recent() {
  local marker="$1" interval="$2"
  python3 - "$marker" "$interval" <<'PY'
import os
import sys
import time
try:
    recent = time.time() - os.path.getmtime(sys.argv[1]) < int(sys.argv[2])
except OSError:
    recent = False
raise SystemExit(0 if recent else 1)
PY
}

run_snapshot_worker_with_deadline() {
  local deadline="$1"
  shift
  python3 - "$deadline" "$SCRIPT_PATH" "$@" <<'PY'
import os
import signal
import subprocess
import sys

deadline = int(sys.argv[1])
command = [sys.argv[2], "__snapshot-worker", *sys.argv[3:]]
try:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
except Exception as exc:
    print(f"ERROR: could not start snapshot worker: {exc}", file=sys.stderr)
    raise SystemExit(1)
try:
    stdout, stderr = process.communicate(timeout=deadline)
except subprocess.TimeoutExpired:
    os.killpg(process.pid, signal.SIGKILL)
    stdout, stderr = process.communicate()
    if stdout:
        print(stdout, end="")
    if stderr:
        print(stderr, end="", file=sys.stderr)
    print(f"ERROR: shared snapshot operation exceeded {deadline}s deadline", file=sys.stderr)
    raise SystemExit(124)
if stdout:
    print(stdout, end="")
if stderr:
    print(stderr, end="", file=sys.stderr)
raise SystemExit(process.returncode)
PY
}

cmd_snapshot() {
  local interval="${BD_SNAPSHOT_INTERVAL_SECONDS:-600}"
  local retention="${BD_SNAPSHOT_RETENTION:-10}"
  local deadline="${BD_SNAPSHOT_DEADLINE_SECONDS:-2}"
  local root machine cache_dir marker local_tmp count rc snapshot_identity

  validate_snapshot_settings "$interval" "$retention" "$deadline"
  snapshot_identity="$(snapshot_root_and_machine)"
  root="${snapshot_identity%%$'\n'*}"
  machine="${snapshot_identity#*$'\n'}"
  [[ "$machine" != "$snapshot_identity" ]] || machine=machine

  if [[ "$IF_DUE" -eq 1 ]] && ! automatic_snapshots_enabled; then
    info "automatic JSONL snapshots are disabled by BD_AUTO_SNAPSHOT"
    return 0
  fi
  if [[ -z "$root" ]]; then
    if [[ "$IF_DUE" -eq 1 ]]; then
      info "no default snapshot root on this platform; skipping"
      return 0
    fi
    echo "ERROR: no snapshot root configured; set BD_SNAPSHOT_ROOT" >&2
    return 2
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would export bd export --all and publish under $root/beads-snapshots/$DB/$machine"
    return 0
  fi

  cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/beads-snapshots"
  mkdir -p "$cache_dir"
  marker="$cache_dir/${DB}-${machine}.attempt"
  if [[ "$IF_DUE" -eq 1 ]] && attempt_is_recent "$marker" "$interval"; then
    info "snapshot attempt is still inside the ${interval}-second window; skipping"
    return 0
  fi
  : > "$marker"

  local_tmp="$(mktemp "${TMPDIR:-/tmp}/${DB}-${machine}-snapshot.XXXXXX")"
  if ! BD_EXPORT_GIT_ADD=false "$BD_EXE" export --all -o "$local_tmp" >/dev/null; then
    rm -f -- "$local_tmp"
    if [[ "$IF_DUE" -eq 1 ]]; then
      echo "WARNING: automatic Beads JSONL snapshot skipped: bd export --all failed; no snapshot published" >&2
      return 0
    fi
    echo "ERROR: bd export --all failed; no snapshot published" >&2
    return 1
  fi
  if ! count="$(python3 - "$local_tmp" <<'PY'
import json
import sys
count = 0
with open(sys.argv[1], encoding='utf-8') as stream:
    for line in stream:
        if not line.strip():
            continue
        json.loads(line)
        count += 1
if count == 0:
    raise SystemExit(1)
print(count)
PY
  )"; then
    rm -f -- "$local_tmp"
    if [[ "$IF_DUE" -eq 1 ]]; then
      echo "WARNING: automatic Beads JSONL snapshot skipped: bd export produced empty or invalid JSONL; no snapshot published" >&2
      return 0
    fi
    echo "ERROR: bd export produced empty or invalid JSONL; no snapshot published" >&2
    return 1
  fi

  if run_snapshot_worker_with_deadline "$deadline" \
      "$root" "$DB" "$machine" "$local_tmp" "$count" "$IF_DUE" "$interval" "$retention"; then
    rc=0
  else
    rc=$?
  fi
  rm -f -- "$local_tmp"

  if [[ "$rc" -ne 0 && "$IF_DUE" -eq 1 ]]; then
    echo "WARNING: automatic Beads JSONL snapshot skipped (exit $rc)" >&2
    return 0
  fi
  return "$rc"
}

sync_boundary_snapshot() {
  local mode="$1"
  local saved_if_due="$IF_DUE"
  local rc=0
  if [[ "$mode" == due ]]; then IF_DUE=1; else IF_DUE=0; fi
  cmd_snapshot || rc=$?
  IF_DUE="$saved_if_due"
  [[ "$rc" -ne 0 ]] || return 0
  if [[ "$DO_BACKUP" -eq 1 ]]; then
    die "required pre-sync snapshot failed (exit $rc)"
  fi
  echo "WARNING: pre-sync Beads JSONL snapshot failed (exit $rc); continuing" >&2
  return 0
}

restart_server() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would restart the Dolt server (live SSH_AUTH_SOCK for the fetch)"
    return 0
  fi
  info "restarting Dolt server so it inherits this shell's SSH_AUTH_SOCK"
  "$BD_EXE" dolt stop >&2 || true
  "$BD_EXE" dolt start >&2
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
  sync_boundary_snapshot forced
  restart_server

  local tables sql remote branch
  tables="$(safe_reset_list)"
  remote="$(remote_name)"
  [[ -n "$remote" ]] || die "no Dolt remote configured; see docs/beads.md"

  # Pass the branch explicitly: an init-built peer (beads-sync init) has no
  # default remote configured for its branch - only clones get that - and a
  # branchless dolt_pull errors with "you must specify a branch" there.
  branch="$(dolt_sql -r csv -q "select active_branch() as branch;" 2>/dev/null | tail -n +2)"
  [[ -n "$branch" ]] || die "could not determine the active Dolt branch"

  # THE WHOLE POINT: the reset and the merge run in ONE dolt session. Do not
  # split these, and do not call `bd dolt pull` instead - bd dirties the working
  # set inside its own pull and then fails to merge on its own dirt.
  sql="$(checkout_sql "$tables")call dolt_pull('${remote}','${branch}');"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would run: $sql"
    return 0
  fi

  info "pulling from '${remote}' (reset + merge in one session)"
  dolt_sql -q "$sql" 2>&1 | tr -d '\r' | redact
}

cmd_push() {
  local remote
  remote="$(remote_name)"
  [[ -n "$remote" ]] || die "no Dolt remote configured; refusing push; see docs/beads.md"

  if [[ "$DO_BACKUP" -eq 1 ]]; then
    sync_boundary_snapshot forced
  else
    sync_boundary_snapshot due
  fi
  restart_server
  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would run: bd dolt commit && bd dolt push"
    return 0
  fi
  # push does not merge, so the deadlock does not apply and bd is fine here.
  "$BD_EXE" dolt commit 2>&1 | redact || true
  "$BD_EXE" dolt push 2>&1 | redact
}

# Rebuild this peer from the sync remote without cloning. Proven 2026-08-01:
# a fresh `bd init` creates every dolt_ignore'd local table with an honest
# migration cursor, and `dolt reset --hard` to the fetched remote head adopts
# the tracked tables/history while PRESERVING the ignored tables (dolt treats
# them like git untracked files). Cloning cannot reach this state - clones
# inherit the tracked migration cursor with none of the clone-local tables.
#
# Sequencing rule (docs/beads.md): push from a current peer FIRST so the
# adopted cursor is fresh and no ignored migrations re-run on this machine.
cmd_init() {
  [[ "$DO_BACKUP" -eq 0 ]] || die "init does not take --backup: there is no database to export yet"

  # The destructive step stays human: this command never deletes data.
  [[ ! -e .beads/dolt ]] || die ".beads/dolt already exists; init only rebuilds a dropped data dir. Run './assets/beads-sync.sh snapshot' (or export manually), move .beads/dolt aside yourself, then re-run."

  local url
  url="$(sync_remote_url)"
  [[ -n "$url" ]] || die "no sync remote: set sync.remote in .beads/config.local.yaml or export BD_SYNC_REMOTE (see docs/beads.md)"

  local db_prefix="${PREFIX_OVERRIDE:-$DB}"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would: bd dolt stop; assert BEADS_DOLT_SERVER_PORT (if set) has no other listener"
    info "[dry-run] would: bd init --server --non-interactive --skip-agents --skip-hooks --prefix ${db_prefix}"
    info "[dry-run] would: assert new server serves the database init just created (project id match)"
    info "[dry-run] would: replace any auto-derived dolt remote with the private sync remote"
    info "[dry-run] would: call dolt_fetch + dolt_reset('--hard','origin/main') in ONE session"
    info "[dry-run] would: patch .beads/metadata.json project_id from the adopted database"
    return 0
  fi

  # Trap 1: wrong-server attach. direnv exports a per-checkout port, WSL2
  # distros share 127.0.0.1, and bd init happily adopts whatever answers on
  # the advertised port (2026-08-01: that stamped a live database with a
  # scratch project identity). Stop this checkout's server, then refuse to
  # continue if the port still has a listener - it belongs to someone else.
  "$BD_EXE" dolt stop >/dev/null 2>&1 || true
  if [[ -n "${BEADS_DOLT_SERVER_PORT:-}" ]] && port_in_use "$BEADS_DOLT_SERVER_PORT"; then
    die "port ${BEADS_DOLT_SERVER_PORT} still has a listener after 'bd dolt stop'; another checkout or distro owns it. Stop that server or unset BEADS_DOLT_SERVER_PORT."
  fi
  # Never trust an inherited data-dir override from another checkout's direnv.
  export BEADS_DOLT_CLI_DIR="$ROOT/.beads/dolt"

  local head_before
  head_before="$(git rev-parse HEAD)"

  # Force the from-zero LOCAL init path: hide the git origin, the local sync
  # config and BD_SYNC_REMOTE while bd init runs (see restore_init_holds).
  # $url is already in memory, so nothing downstream needs the hidden file.
  # The INT trap matters: bash does not reliably fire EXIT on Ctrl-C, and the
  # failure mode is a checkout left with its origin renamed.
  trap restore_init_holds EXIT
  trap 'restore_init_holds; exit 130' INT
  if [[ -f .beads/config.local.yaml ]]; then
    mv .beads/config.local.yaml .beads/config.local.yaml.init-hold
  fi
  if git remote get-url origin >/dev/null 2>&1; then
    git remote get-url beads-init-hold >/dev/null 2>&1 &&
      die "git remote 'beads-init-hold' already exists; resolve that first"
    info "temporarily renaming git remote 'origin' so bd init cannot derive from it"
    git remote rename origin beads-init-hold >/dev/null
    INIT_HOLD_ORIGIN=1
  fi

  info "running bd init (fresh local database, prefix '${db_prefix}')"
  env -u BD_SYNC_REMOTE "$BD_EXE" init --server --non-interactive --skip-agents --skip-hooks --prefix "$db_prefix" >&2

  restore_init_holds
  trap - EXIT INT

  # bd init rewrote metadata.json and started a server; re-read both. The
  # BEADS_SYNC_DB override still wins, matching the top of this script.
  if [[ -z "${BEADS_SYNC_DB:-}" ]]; then
    DB="$(python3 -c "import json; m=json.load(open('.beads/metadata.json')); print(m.get('dolt_database','beads'))")"
  fi
  PORT="$(tr -d '[:space:]' < .beads/dolt-server.port)"
  [[ -d ".beads/dolt/$DB" ]] || die "bd init did not create .beads/dolt/${DB} in this checkout - wrong server?"

  # The assertion the 2026-08-01 accident lacked: the server we are talking to
  # must be serving the database init just created.
  local meta_pid db_pid
  meta_pid="$(python3 -c "import json; print(json.load(open('.beads/metadata.json'))['project_id'])")"
  db_pid="$(dolt_sql -r csv -q "select value from metadata where \`key\`='_project_id';" 2>/dev/null | tail -n +2)"
  [[ -n "$db_pid" && "$db_pid" == "$meta_pid" ]] || die "project id mismatch: metadata.json has ${meta_pid}, server database has ${db_pid:-nothing}. Refusing - this looks like another checkout's server."

  # Trap 2: bd init auto-derives a Dolt remote from the git origin, and in
  # this repo the git origin is the PUBLIC dotfiles repo. Remove every remote
  # that is not the private sync remote before anything can push to it.
  local name r_url
  while IFS=, read -r name r_url; do
    [[ -z "$name" ]] && continue
    if [[ "$r_url" != "$url" ]]; then
      info "removing auto-derived dolt remote '${name}' (URL does not match sync.remote)"
      dolt_sql -q "call dolt_remote('remove','${name}');" >/dev/null
    fi
  done < <(dolt_sql -r csv -q "select name, url from dolt_remotes;" 2>/dev/null | tail -n +2)

  local remote
  remote="$(remote_name)"
  if [[ -z "$remote" ]]; then
    # On failure dolt echoes the whole statement - URL included - on stderr.
    # Route stderr through redact; stdout (the status table) is just noise.
    dolt_sql -q "call dolt_remote('add','origin','${url}');" 2>&1 >/dev/null | redact >&2
    remote="origin"
  fi

  # bd init is also known to leak sync.remote into tracked .beads/config.yaml.
  if [[ -n "$(git status --porcelain -- .beads/config.yaml)" ]]; then
    info "bd init modified tracked .beads/config.yaml (sync.remote leak) - restoring from HEAD"
    git restore --source=HEAD --worktree -- .beads/config.yaml
  fi
  local head_after
  head_after="$(git rev-parse HEAD)"
  if [[ "$head_after" != "$head_before" ]]; then
    echo "WARNING: bd init created git commits (${head_before} -> ${head_after})." >&2
    echo "WARNING: review 'git log ${head_before}..HEAD'; reset ONLY after reviewing:" >&2
    echo "WARNING:   git reset --hard ${head_before}" >&2
  fi

  # Fetch + hard reset in ONE dolt session, same rule as pull: no bd process
  # in between to dirty the working set.
  info "fetching from '${remote}' and hard-resetting to ${remote}/main (one session)"
  dolt_sql -q "call dolt_fetch('${remote}'); call dolt_reset('--hard','${remote}/main');" 2>&1 | tr -d '\r' | redact

  local issues wisps
  issues="$(dolt_sql -r csv -q "select count(*) from issues;" 2>/dev/null | tail -n +2)"
  wisps="$(dolt_sql -r csv -q "show tables like 'wisp%';" 2>/dev/null | tail -n +2 | grep -c . || true)"
  [[ "${issues:-0}" -gt 0 ]] || die "issues table is empty after the reset - remote adoption failed"
  [[ "$wisps" -eq 6 ]] || die "expected 6 wisp tables after the reset, found ${wisps}. Dolt no longer preserves dolt_ignore'd tables across reset - STOP; see docs/beads.md before retrying."

  # Trap 3: the tracked `metadata` table rode in with the reset, so the DB now
  # carries the shared project identity. Point metadata.json at it or bd
  # refuses to connect (PROJECT IDENTITY MISMATCH).
  db_pid="$(dolt_sql -r csv -q "select value from metadata where \`key\`='_project_id';" 2>/dev/null | tail -n +2)"
  [[ -n "$db_pid" ]] || die "could not read _project_id from the database after the reset"
  # Swap only the UUID string so bd's own formatting (indentation, trailing
  # newline or lack of it) survives byte-for-byte - the file is git-tracked
  # and reformatting it would churn every peer.
  python3 - "$db_pid" <<'PY'
import json, sys
path = '.beads/metadata.json'
text = open(path).read()
current = json.loads(text)['project_id']
open(path, 'w').write(text.replace(current, sys.argv[1]))
PY
  info "patched .beads/metadata.json project_id to the adopted database identity"

  # Trap 4: if the adopted migration cursor trails this bd version, the first
  # bd command re-runs the missing ignored migrations (idempotent on Linux;
  # avoid entirely by pushing from a current peer before running init).
  if ! "$BD_EXE" list --limit 1 >/dev/null 2>&1; then
    echo "WARNING: 'bd list' failed after init; run 'bd doctor' before using this checkout." >&2
  fi

  echo "init complete: ${issues} issues adopted from the sync remote."
  echo "Next: run 'bd doctor'. If it reports a Repo Fingerprint error, do NOT run"
  echo "'bd migrate --update-repo-id' without reading docs/beads.md - repo_id is a"
  echo "tracked value shared by every peer."
}

case "$COMMAND" in
  status|clean|pull|push) require_dolt_server ;;
esac

case "$COMMAND" in
  status) cmd_status ;;
  clean)  cmd_clean ;;
  pull)   cmd_pull ;;
  push)   cmd_push ;;
  snapshot) cmd_snapshot ;;
  init)   cmd_init ;;
esac
