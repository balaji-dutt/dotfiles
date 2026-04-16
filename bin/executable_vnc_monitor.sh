#!/bin/bash

# --- CONFIGURATION ---
RESTORE_TIMEOUT=600
CHECK_INTERVAL=60
# ---------------------

STATE="disconnected"
CAFFEINATE_PID=""
CAFFEINATE_PID_FILE="/tmp/com.user.vncmonitor.caffeinate.pid"
CLEANUP_DONE=""

read_idle_time() {
    defaults -currentHost read com.apple.screensaver idleTime 2>/dev/null
}

write_idle_time() {
    defaults -currentHost write com.apple.screensaver idleTime -int "$1"
}

normalize_idle_time_type_if_numeric() {
    CURRENT_VAL="$(read_idle_time)"
    case "$CURRENT_VAL" in
        ''|*[!0-9]*)
            return
            ;;
    esac

    CURRENT_TYPE="$(defaults -currentHost read-type com.apple.screensaver idleTime 2>/dev/null)"
    if [ "$CURRENT_TYPE" != "Type is integer" ]; then
        write_idle_time "$CURRENT_VAL"
        killall -HUP cfprefsd
    fi
}

ensure_disconnected_idle_time() {
    if [ "$(read_idle_time)" != "$RESTORE_TIMEOUT" ]; then
        write_idle_time "$RESTORE_TIMEOUT"
        killall -HUP cfprefsd
    fi
}

if [ "$1" == "--cleanup" ]; then
    CAFFEINATE_PID_FILE="/tmp/com.user.vncmonitor.caffeinate.pid"

    if [ -f "$CAFFEINATE_PID_FILE" ]; then
        EXISTING_PID="$(cat "$CAFFEINATE_PID_FILE" 2>/dev/null)"
        if [ -n "$EXISTING_PID" ] && kill -0 "$EXISTING_PID" 2>/dev/null; then
            kill "$EXISTING_PID" 2>/dev/null
        fi
        rm -f "$CAFFEINATE_PID_FILE"
    fi

    ensure_disconnected_idle_time

    exit 0
fi

cleanup() {
    if [ -n "$CLEANUP_DONE" ]; then
        return
    fi
    CLEANUP_DONE=1

    if [ -n "$CAFFEINATE_PID" ]; then
        kill "$CAFFEINATE_PID" 2>/dev/null
        CAFFEINATE_PID=""
    fi

    rm -f "$CAFFEINATE_PID_FILE"

    ensure_disconnected_idle_time
}

trap 'cleanup; exit 0' SIGTERM SIGINT
trap cleanup EXIT

if [ -f "$CAFFEINATE_PID_FILE" ]; then
    EXISTING_PID="$(cat "$CAFFEINATE_PID_FILE" 2>/dev/null)"
    if [ -n "$EXISTING_PID" ] \
        && kill -0 "$EXISTING_PID" 2>/dev/null \
        && ps -p "$EXISTING_PID" -o comm= 2>/dev/null | grep -q "^caffeinate$"; then
        CAFFEINATE_PID="$EXISTING_PID"
    else
        rm -f "$CAFFEINATE_PID_FILE"
    fi
fi

normalize_idle_time_type_if_numeric

while true; do
    VNC_ACTIVE=$(netstat -an | grep "\.5900 " | grep "ESTABLISHED")

    if [ -n "$VNC_ACTIVE" ]; then
        CURRENT_VAL=$(read_idle_time)
        if [ "$CURRENT_VAL" != "0" ]; then
            write_idle_time 0
            killall -HUP cfprefsd
            # logger "VNC Active: Forced screensaver to Never."
        fi

        # Prevent display/system from sleeping while VNC is active
        # Use a PID file so we can avoid duplicates across restarts.
        if [ -z "$CAFFEINATE_PID" ] || ! kill -0 "$CAFFEINATE_PID" 2>/dev/null; then
            caffeinate -d -i &
            CAFFEINATE_PID=$!
            echo "$CAFFEINATE_PID" >"$CAFFEINATE_PID_FILE"
            # logger "VNC Active: Started caffeinate (PID: $CAFFEINATE_PID) to prevent display sleep."
        fi

        # Declare user activity so idle-time lock/logout timers don't trigger
        caffeinate -u -t "$((CHECK_INTERVAL + 5))" >/dev/null 2>&1 &

        STATE="connected"
    else
        ensure_disconnected_idle_time
        STATE="disconnected"

        # Allow display/system to sleep again
        if [ -n "$CAFFEINATE_PID" ]; then
            kill "$CAFFEINATE_PID" 2>/dev/null
            CAFFEINATE_PID=""
            rm -f "$CAFFEINATE_PID_FILE"
            # logger "VNC Disconnected: Stopped caffeinate."
        fi
    fi

    sleep $CHECK_INTERVAL
done
