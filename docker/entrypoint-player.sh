#!/bin/bash
# Player container: waits for the host X display and the CARACAL app, then runs Chromium via Playwright.
set -e
export DISPLAY=${DISPLAY:-:0}
until [ -S /tmp/.X11-unix/X0 ]; do echo 'waiting for the X display'; sleep 2; done
until curl -fsS "${CARACAL_BASE:-http://127.0.0.1:8080}/api/setup-status" >/dev/null 2>&1; do sleep 2; done
rm -f "$CARACAL_PROFILE/SingletonLock" "$CARACAL_PROFILE/SingletonSocket" "$CARACAL_PROFILE/SingletonCookie"
/opt/caracal/player/fullscreen-watchdog.sh &
exec /opt/caracal/.venv/bin/python /opt/caracal/player/player.py
