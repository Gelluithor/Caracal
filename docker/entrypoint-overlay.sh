#!/bin/bash
# Overlay container: countdown bar drawn on the host X display (and the start-up IP screen once).
set -e
export DISPLAY=${DISPLAY:-:0}
until [ -S /tmp/.X11-unix/X0 ]; do sleep 2; done
if [ ! -f /tmp/caracal-boot-info-shown ]; then
  touch /tmp/caracal-boot-info-shown
  python3 /opt/caracal/player/boot-info.py || true
fi
exec python3 /opt/caracal/player/overlay.py
