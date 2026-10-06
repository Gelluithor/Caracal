#!/bin/bash
# Keeps the Chromium player fullscreen. The player switches Chromium to fullscreen itself over DevTools; this watchdog
# only tells the window manager (EWMH) when a window is not fullscreen and never sends keys: F11 toggles fullscreen,
# so pressing it on a window that was already fullscreen brought the ordinary window back.
# The countdown overlay keeps itself above the player (overlay.py).
export DISPLAY=${DISPLAY:-:0}
export XAUTHORITY=${XAUTHORITY:-/home/caracal/.Xauthority}
sleep 3
LAST=""
while true; do
  IDS=$(xdotool search --onlyvisible --class 'chromium' 2>/dev/null || true)
  for W in $IDS; do
    if ! xprop -id "$W" _NET_WM_STATE 2>/dev/null | grep -q _NET_WM_STATE_FULLSCREEN; then
      # wmctrl changes at most two properties per call
      wmctrl -i -r "$W" -b remove,above,hidden 2>/dev/null || true
      wmctrl -i -r "$W" -b add,fullscreen 2>/dev/null || true
    fi
    if [ "$W" != "$LAST" ]; then
      xdotool windowactivate "$W" 2>/dev/null || true
      LAST="$W"
    fi
  done
  sleep 2
done
