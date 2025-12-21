#!/bin/bash

# --- CONFIGURATION ---
RESTORE_TIMEOUT=1200
CHECK_INTERVAL=60
# ---------------------

STATE="disconnected"

while true; do
    VNC_ACTIVE=$(netstat -an | grep "\.5900 " | grep "ESTABLISHED")

    if [ -n "$VNC_ACTIVE" ]; then
        CURRENT_VAL=$(defaults -currentHost read com.apple.screensaver idleTime 2>/dev/null)
        if [ "$CURRENT_VAL" != "0" ]; then
            defaults -currentHost write com.apple.screensaver idleTime 0
            killall -HUP cfprefsd[cite]
            # logger "VNC Active: Forced screensaver to Never."
        fi
        STATE="connected"
    else
        if [ "$STATE" == "connected" ]; then
            defaults -currentHost write com.apple.screensaver idleTime $RESTORE_TIMEOUT
            killall -HUP cfprefsd
            STATE="disconnected"
            # logger "VNC Disconnected: Restored screensaver to $RESTORE_TIMEOUT."
        fi
    fi

    sleep $CHECK_INTERVAL
done
