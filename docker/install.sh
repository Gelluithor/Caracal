#!/bin/bash
# One-command installation of CARACAL on Docker, without CARACAL Fleet: Raspberry Pi OS Lite (64-bit), DietPi,
# Debian or Ubuntu (arm64, amd64).
#
#   curl -fsSL https://raw.githubusercontent.com/Gelluithor/Caracal/main/docker/install.sh | sudo bash
#   curl -fsSL https://raw.githubusercontent.com/Gelluithor/Caracal/main/docker/install.sh | sudo bash -s -- --version 2026.10.25
#
# Options: --version VERSION (default latest), --image IMAGE (default ghcr.io/gelluithor/caracal-node),
# --docker-pool RANGE (Docker's networks out of 172.16.0.0/12), --reinstall [--wipe-data].
#
# It runs the node installer of CARACAL Fleet without a hub, the same one Fleet uses: Docker, the X display, the
# CARACAL containers in /opt/caracal-node, data in /var/lib/caracal. A classic installation (/opt/caracal) is
# converted and its data are kept. The node can join CARACAL Fleet later (Add device -> Fleet Agent only).
set -euo pipefail

FLEET_RAW=${CARACAL_FLEET_RAW:-https://raw.githubusercontent.com/Gelluithor/Caracal-Fleet/main/hub/bootstrap}

# everything runs from this function: with "curl ... | sudo bash" the script itself comes on stdin, so bash has to
# read all of it before anything starts, and the installers get /dev/null instead of the rest of the script
main() {
  local args=() f   # WORK stays global: the EXIT trap runs after main has returned
  while [ $# -gt 0 ]; do
    case "$1" in
      --version|--image|--docker-pool) [ $# -ge 2 ] || { echo "$1 needs a value" >&2; exit 2; }; args+=("$1" "$2"); shift 2;;
      --reinstall|--wipe-data) args+=("$1"); shift;;
      *) echo "Unknown argument: $1" >&2; exit 2;;
    esac
  done
  [ "$(id -u)" -eq 0 ] || { echo 'Run as root: ... | sudo bash' >&2; exit 1; }
  command -v apt-get >/dev/null || { echo 'Supported systems: Raspberry Pi OS, DietPi, Debian and Ubuntu.' >&2; exit 3; }
  if ! command -v curl >/dev/null; then
    apt-get update -qq </dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates >/dev/null </dev/null
  fi

  WORK=$(mktemp -d)
  trap 'rm -rf "$WORK"' EXIT
  echo '==> Downloading the CARACAL node installer'
  for f in install-node.sh caracal-compose.yml; do
    curl -fsSL --max-time 120 "$FLEET_RAW/$f" -o "$WORK/$f"
  done
  bash "$WORK/install-node.sh" ${args[@]+"${args[@]}"} </dev/null
}

main "$@"
