#!/bin/bash

# --- CONFIGURATION ---
RESTORE_TIMEOUT=1200
CHECK_INTERVAL=60
# ---------------------

STATE="disconnected"
CAFFEINATE_PID=""

while true; do
    VNC_ACTIVE=$(netstat -an | grep "\.5900 " | grep "ESTABLISHED")

    if [ -n "$VNC_ACTIVE" ]; then
        CURRENT_VAL=$(defaults -currentHost read com.apple.screensaver idleTime 2>/dev/null)
        if [ "$CURRENT_VAL" != "0" ]; then
            defaults -currentHost write com.apple.screensaver idleTime 0
            killall -HUP cfprefsd
            # logger "VNC Active: Forced screensaver to Never."
        fi

        # Prevent display from sleeping
        if [ -z "$CAFFEINATE_PID" ] || ! kill -0 "$CAFFEINATE_PID" 2>/dev/null; then
            caffeinate -d &
            CAFFEINATE_PID=$!
            # logger "VNC Active: Started caffeinate (PID: $CAFFEINATE_PID) to prevent display sleep."
        fi

        STATE="connected"
    else
        if [ "$STATE" == "connected" ]; then
            defaults -currentHost write com.apple.screensaver idleTime $RESTORE_TIMEOUT
            killall -HUP cfprefsd
            STATE="disconnected"
            # logger "VNC Disconnected: Restored screensaver to $RESTORE_TIMEOUT."

            # Allow display to sleep again
            if [ -n "$CAFFEINATE_PID" ]; then
                kill "$CAFFEINATE_PID" 2>/dev/null
                CAFFEINATE_PID=""
                # logger "VNC Disconnected: Stopped caffeinate."
            fi
        fi
    fi

    sleep $CHECK_INTERVAL
done
