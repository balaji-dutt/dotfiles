#!/bin/bash

# --- CONFIGURATION ---
RESTORE_TIMEOUT=600
CHECK_INTERVAL=60
# ---------------------

STATE="disconnected"
CAFFEINATE_PID=""
CAFFEINATE_PID_FILE="/tmp/com.user.vncmonitor.caffeinate.pid"
IDLETIME_STATE_FILE="/tmp/com.user.vncmonitor.idleTime"
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

save_idle_time_state_if_missing() {
    if [ -f "$IDLETIME_STATE_FILE" ]; then
        return
    fi

    CURRENT_VAL="$(read_idle_time)"
    case "$CURRENT_VAL" in
        ''|*[!0-9]*)
            CURRENT_VAL="$RESTORE_TIMEOUT"
            ;;
    esac

    printf '%s\n' "$CURRENT_VAL" >"$IDLETIME_STATE_FILE"
}

restore_idle_time_state() {
    if [ ! -f "$IDLETIME_STATE_FILE" ]; then
        return
    fi

    TARGET_VAL="$(cat "$IDLETIME_STATE_FILE" 2>/dev/null)"
    case "$TARGET_VAL" in
        ''|*[!0-9]*)
            TARGET_VAL="$RESTORE_TIMEOUT"
            ;;
    esac

    CURRENT_VAL="$(read_idle_time)"
    if [ "$CURRENT_VAL" != "$TARGET_VAL" ]; then
        write_idle_time "$TARGET_VAL"
        killall -HUP cfprefsd
    fi

    rm -f "$IDLETIME_STATE_FILE"
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

    restore_idle_time_state

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

    restore_idle_time_state
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
        save_idle_time_state_if_missing

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
        if [ "$STATE" == "connected" ] || [ -f "$IDLETIME_STATE_FILE" ]; then
            restore_idle_time_state
            STATE="disconnected"
            # logger "VNC Disconnected: Restored screensaver idleTime."

            # Allow display/system to sleep again
            if [ -n "$CAFFEINATE_PID" ]; then
                kill "$CAFFEINATE_PID" 2>/dev/null
                CAFFEINATE_PID=""
                rm -f "$CAFFEINATE_PID_FILE"
                # logger "VNC Disconnected: Stopped caffeinate."
            fi
        fi
    fi

    sleep $CHECK_INTERVAL
done
