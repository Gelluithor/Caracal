# CARACAL

Digital signage for Raspberry Pi 4/5 and PCs: a fullscreen Chromium player for web pages, Grafana dashboards,
images and videos with a web admin. Many screens can be managed centrally with
[CARACAL Fleet](https://github.com/TaurAnnun/caracal-fleet).

- Czech and English admin UI with dark mode and built-in help
- web pages, images and videos in one playlist, drag-and-drop ordering
- per-item duration and zoom (when adding, editing or live with a slider)
- encrypted login profiles with automatic sign-in, automatic acceptance of common cookie banners
- Grafana collections: all dashboards with a tag, read through Grafana's guest access on every cycle
- live control: show or freeze an item or a whole collection, skip to the next item, resume
- smooth progress bar at the bottom of the screen, splash screen and boot screen with the IP address
- fullscreen watchdog, player and device restart from the admin UI

## Installation

### Docker (recommended)

The app, the player and the overlay run as containers from `ghcr.io/taurannun/caracal-node` (arm64 and amd64).
The device only runs Docker, the X display and, optionally, the CARACAL Fleet Agent.

- **With CARACAL Fleet:** prepare an SD card in Fleet (zero-touch) or let Fleet install a Raspberry Pi over SSH.
  Fleet also updates the nodes.
- **Without Fleet:** see [docker/README.md](docker/README.md).

### Classic installation

On Raspberry Pi OS Lite or DietPi (Raspberry Pi 4/5):

```bash
git clone https://github.com/TaurAnnun/caracal.git
cd caracal
sudo bash install.sh
```

Open `http://<device-ip>:8080` and create the administrator. Running `install.sh` again updates the application;
the data in `/var/lib/caracal` are kept. To remove CARACAL: `sudo bash uninstall.sh` (`--purge` also removes the
data).

## Data

Everything is stored in `/var/lib/caracal`: database, media, encrypted login profiles, player state and the
Chromium profile.

## CARACAL Fleet API

The CARACAL Fleet Agent talks to the node only through its local Fleet API (`/api/fleet/v1/*`, section
`CARACAL_FLEET_API_V2` in `app/main.py`). The API is protected by the key in `/etc/caracal-fleet-key` (classic) or
`/var/lib/caracal/.fleet-key` (Docker), which the agent writes when it is enrolled. Without the key the API answers
503 and exposes nothing.

## Security

- The player playlist (`/api/player/playlist`, `/api/player/playlist-expanded`) is only served to the device
  itself, because it contains internal addresses.
- Sign-in to the admin UI is limited to 10 failed attempts per 5 minutes per address.
- Deleting an image or a video from the playlist also deletes its file.

## Releases

Raise the version in `VERSION` and push a tag `v<version>` (e.g. `v2026.10.07`). GitHub Actions builds the image
`ghcr.io/<owner>/caracal-node:<version>` and `:latest` for arm64 and amd64. Pushes to `main` build `:edge`.
CARACAL Fleet reads the available versions from the registry.

Nodes with the classic installation can also be updated from Fleet with a source archive of this repository
(*Download ZIP*), or converted to Docker.
