#!/bin/bash
set -e
export HOME=/home/caracal
export DISPLAY=:0
export XAUTHORITY=/home/caracal/.Xauthority
export CARACAL_BASE=http://127.0.0.1:8080
export CARACAL_PROFILE=/var/lib/caracal/chromium
until curl -fsS http://127.0.0.1:8080/api/setup-status >/dev/null 2>&1; do sleep 2; done
until [ -S /tmp/.X11-unix/X0 ]; do sleep 2; done
pkill -u caracal -f '/usr/bin/chromium' 2>/dev/null || true
rm -f /var/lib/caracal/chromium/SingletonLock /var/lib/caracal/chromium/SingletonSocket /var/lib/caracal/chromium/SingletonCookie
/opt/caracal/player/fullscreen-watchdog.sh &
WATCHDOG_PID=$!
trap 'kill $WATCHDOG_PID 2>/dev/null || true' EXIT
exec /opt/caracal/.venv/bin/python /opt/caracal/player/player.py
