# CARACAL node on Docker

The CARACAL node image contains the web admin and API (`app`), the Chromium player (`player`) and the countdown
overlay (`overlay`). The host only provides Docker and the X display (Xorg + Openbox on tty1), which the
containers use through `/tmp/.X11-unix`.

Images: `ghcr.io/gelluithor/caracal-node:<version>` for `linux/arm64` (Raspberry Pi 4/5) and `linux/amd64`, built by
`.github/workflows/docker-image.yml`:

| Git event | Image tags |
|---|---|
| tag `v2026.10.06` | `2026.10.06`, `latest` |
| push to `main` | `edge` |

## Installation

### With CARACAL Fleet (recommended)

In Fleet: **Add device → Prepare SD card** (zero-touch: the device installs itself on the first boot), or
**Add device → New CARACAL node** / **Find devices in the network**, where Fleet connects over SSH. Both install
Docker, the display, CARACAL and the Fleet Agent. Updates are then done from Fleet (*CARACAL updates*):
the agent pulls the new image, checks that CARACAL runs, and restores the previous version on failure.

Existing classic installations (`/opt/caracal`) are converted with **Convert to Docker**. The data in
`/var/lib/caracal` are kept.

### Manually (without Fleet)

On Raspberry Pi OS Lite (64-bit) or DietPi:

1. Install Docker: `curl -fsSL https://get.docker.com | sudo sh`
2. Create the user and the X display. The same steps as `install-node.sh` in CARACAL Fleet, sections
   *[3/7]* and *[5/7]*: user `caracal`, `/home/caracal/.xinitrc`, Openbox and `caracal-display.service`.
3. Copy `compose.yml` and `.env.example` (as `.env`) to `/opt/caracal-node/` and set `CARACAL_IMAGE`,
   `CARACAL_VERSION` and the uid/gid values in `.env` (`id caracal`, `getent group video render`).
4. `cd /opt/caracal-node && sudo docker compose up -d`
5. Open `http://<ip>:8080` and create the administrator.

Update: change `CARACAL_VERSION` in `.env` and run `sudo docker compose pull && sudo docker compose up -d`.

## Data

Everything that matters is stored on the host in `/var/lib/caracal`: database, media, login profiles, player state
and the Chromium profile. Containers can be removed and recreated at any time.

## Differences from the classic installation

- Restarting the player from the admin UI restarts the `player` container.
- Restarting the device from the admin UI needs the CARACAL Fleet Agent on the host, because a container cannot
  reboot the host. Without Fleet, use `sudo reboot`.
