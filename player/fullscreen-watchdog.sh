#!/bin/bash
export DISPLAY=:0
export XAUTHORITY=/home/caracal/.Xauthority
sleep 3
LAST=""
while true; do
  IDS=$(xdotool search --onlyvisible --class 'chromium' 2>/dev/null || true)
  for W in $IDS; do
    wmctrl -i -r "$W" -b remove,above,hidden 2>/dev/null || true
    wmctrl -i -r "$W" -b add,fullscreen,maximized_vert,maximized_horz 2>/dev/null || true
    xdotool windowmove "$W" 0 0 2>/dev/null || true
    xdotool windowsize "$W" 100% 100% 2>/dev/null || true
    if [ "$W" != "$LAST" ]; then
      xdotool windowactivate --sync "$W" 2>/dev/null || true
      sleep 1
      xdotool key --window "$W" F11 2>/dev/null || true
      LAST="$W"
    fi
  done
  OIDS=$(xdotool search --name 'CARACAL Countdown' 2>/dev/null || true)
  for O in $OIDS; do
    wmctrl -i -r "$O" -b add,above,sticky,skip_taskbar 2>/dev/null || true
    xdotool windowraise "$O" 2>/dev/null || true
  done
  sleep 2
done
