#!/bin/bash

set -u

PATHS_FILE="${NFS_DOT_CLEAN_PATHS_FILE:-$HOME/.config/nfs-dot-clean/paths}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/nfs-dot-clean"
LOCK_DIR="$STATE_DIR/lock"
PID_FILE="$STATE_DIR/dot-clean.pid"
TIMEOUT_SECONDS="${NFS_DOT_CLEAN_TIMEOUT_SECONDS:-120}"

MOUNT_BIN="/sbin/mount"
DOT_CLEAN_BIN="/usr/sbin/dot_clean"

log_warn() {
    printf 'nfs-dot-clean: %s\n' "$*" >&2
}

trim_line() {
    local value="$1"

    value="${value#"${value%%[!$' \t']*}"}"
    value="${value%"${value##*[!$' \t']}"}"

    printf '%s\n' "$value"
}

expand_path() {
    local value="$1"

    case "$value" in
        '~')
            printf '%s\n' "$HOME"
            ;;
        '~/'*)
            printf '%s/%s\n' "$HOME" "${value#~/}"
            ;;
        *)
            printf '%s\n' "$value"
            ;;
    esac
}

strip_trailing_slashes() {
    local value="$1"

    while [ "${#value}" -gt 1 ] && [ "${value%/}" != "$value" ]; do
        value="${value%/}"
    done

    printf '%s\n' "$value"
}

path_is_under_mount() {
    local path="$1"
    local mountpoint

    while IFS= read -r mountpoint; do
        [ -n "$mountpoint" ] || continue

        if [ "$path" = "$mountpoint" ] || [ "${path#"$mountpoint/"}" != "$path" ]; then
            return 0
        fi
    done

    return 1
}

read_nfs_mounts() {
    "$MOUNT_BIN" -t nfs 2>/dev/null | while IFS= read -r line; do
        case "$line" in
            *' on '*' (nfs,'*|*' on '*' (nfs)'*)
                line="${line#* on }"
                line="${line% (nfs*}"
                strip_trailing_slashes "$line"
                ;;
        esac
    done
}

previous_child_is_alive() {
    local previous_pid=""

    [ -f "$PID_FILE" ] || return 1
    IFS= read -r previous_pid <"$PID_FILE" || previous_pid=""

    if [ -n "$previous_pid" ] && kill -0 "$previous_pid" 2>/dev/null; then
        return 0
    fi

    rm -f "$PID_FILE"
    return 1
}

acquire_lock() {
    local lock_pid=""

    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" >"$LOCK_DIR/pid"
        return 0
    fi

    if [ -f "$LOCK_DIR/pid" ]; then
        IFS= read -r lock_pid <"$LOCK_DIR/pid" || lock_pid=""
        if [ -n "$lock_pid" ] && ! kill -0 "$lock_pid" 2>/dev/null; then
            rm -rf "$LOCK_DIR"
            if mkdir "$LOCK_DIR" 2>/dev/null; then
                printf '%s\n' "$$" >"$LOCK_DIR/pid"
                return 0
            fi
        fi
    fi

    return 1
}

release_lock() {
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || true
}

report_dot_clean_failure() {
    local path="$1"
    local log_file="$2"
    local line=""
    local privacy_hint=""
    local saw_operation_not_permitted=""

    log_warn "dot_clean failed for: $path"

    while IFS= read -r line || [ -n "$line" ]; do
        [ -n "$line" ] || continue
        log_warn "dot_clean: $line"
        case "$line" in
            *'Operation not permitted'*)
                saw_operation_not_permitted=1
                ;;
        esac
    done <"$log_file"

    if [ -n "$saw_operation_not_permitted" ]; then
        privacy_hint="macOS privacy may be blocking launchd access; grant Full Disk Access"
        privacy_hint="$privacy_hint to /bin/bash and /usr/sbin/dot_clean, then kickstart"
        privacy_hint="$privacy_hint com.user.nfs-dot-clean"
        log_warn "$privacy_hint"
    fi
}

run_dot_clean_with_timeout() {
    local path="$1"
    local child_pid=""
    local elapsed=0
    local status
    local dot_clean_log=""

    dot_clean_log="$STATE_DIR/dot-clean.$$.$RANDOM.err"
    : >"$dot_clean_log" || {
        log_warn "failed to create dot_clean log: $dot_clean_log"
        return 1
    }

    "$DOT_CLEAN_BIN" -m "$path" >"$dot_clean_log" 2>&1 &
    child_pid="$!"
    printf '%s\n' "$child_pid" >"$PID_FILE"

    while kill -0 "$child_pid" 2>/dev/null; do
        if [ "$elapsed" -ge "$TIMEOUT_SECONDS" ]; then
            log_warn "dot_clean timed out after ${TIMEOUT_SECONDS}s for: $path"
            kill "$child_pid" 2>/dev/null || true
            sleep 2
            kill -0 "$child_pid" 2>/dev/null && kill -KILL "$child_pid" 2>/dev/null || true

            if kill -0 "$child_pid" 2>/dev/null; then
                log_warn "dot_clean child still alive; future runs will skip while PID $child_pid exists"
                report_dot_clean_failure "$path" "$dot_clean_log"
                rm -f "$dot_clean_log"
                return 124
            fi

            wait "$child_pid" 2>/dev/null || true
            report_dot_clean_failure "$path" "$dot_clean_log"
            rm -f "$dot_clean_log"
            rm -f "$PID_FILE"
            return 124
        fi

        sleep 1
        elapsed=$((elapsed + 1))
    done

    wait "$child_pid"
    status="$?"
    if [ "$status" -ne 0 ]; then
        report_dot_clean_failure "$path" "$dot_clean_log"
    fi
    rm -f "$dot_clean_log"
    rm -f "$PID_FILE"
    return "$status"
}

main() {
    local nfs_mounts=""
    local line=""
    local path=""
    local status=0

    [ -r "$PATHS_FILE" ] || exit 0

    mkdir -p "$STATE_DIR" || {
        log_warn "failed to create state directory: $STATE_DIR"
        exit 1
    }

    case "$TIMEOUT_SECONDS" in
        ''|*[!0-9]*)
            log_warn "invalid timeout, using 120 seconds: $TIMEOUT_SECONDS"
            TIMEOUT_SECONDS=120
            ;;
    esac

    if ! acquire_lock; then
        exit 0
    fi
    trap 'release_lock' EXIT
    trap 'release_lock; exit 130' HUP INT TERM

    if previous_child_is_alive; then
        exit 0
    fi

    if [ ! -x "$DOT_CLEAN_BIN" ]; then
        log_warn "dot_clean not found or not executable: $DOT_CLEAN_BIN"
        exit 1
    fi

    nfs_mounts="$(read_nfs_mounts)"
    [ -n "$nfs_mounts" ] || exit 0

    while IFS= read -r line || [ -n "$line" ]; do
        line="$(trim_line "$line")"

        case "$line" in
            ''|'#'*)
                continue
                ;;
        esac

        path="$(expand_path "$line")"
        path="$(strip_trailing_slashes "$path")"

        if ! printf '%s\n' "$nfs_mounts" | path_is_under_mount "$path"; then
            continue
        fi

        if ! run_dot_clean_with_timeout "$path"; then
            if previous_child_is_alive; then
                exit 1
            fi
            status=1
        fi
    done <"$PATHS_FILE"

    exit "$status"
}

main "$@"
