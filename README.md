# CARACAL

Digital signage for Raspberry Pi 4/5 and PCs: a fullscreen Chromium player for web pages, Grafana dashboards,
images and videos with a web admin. Many screens can be managed centrally with
[CARACAL Fleet](https://github.com/Gelluithor/Caracal-Fleet).

![CARACAL web administration: the screen now, freezing an item, what the TV shows, the playlist, the notification look
and a test notification on the screen](docs/caracal.gif)

- Czech and English admin UI with dark mode and built-in help, in the same look as CARACAL Fleet
- web pages, images and videos in one playlist, drag-and-drop ordering
- per-item duration and zoom (when adding, editing or live with a slider)
- encrypted login profiles with automatic sign-in (log-in forms and HTTP Basic/Digest, the browser's pop-up), automatic acceptance of common cookie banners
- Grafana collections: all dashboards with a tag, read through Grafana's guest access on every cycle
- websites in dark mode for the whole device or per item: the sites' own dark look, or forced dark for every page
- live control: show or freeze an item or a whole collection, skip to the next item, resume
- smooth progress bar at the bottom of the screen, splash screen and boot screen with the IP address
- fullscreen watchdog, player and device restart from the admin UI
- on-screen pop-up notifications from other apps (Grafana, Alertmanager, Uptime Kuma, scripts, …) through a token
  protected API, or watchers that ask an app's API for new items (Zammad, Jira, Redmine, Freshdesk, GitLab, GitHub,
  any JSON API), shown one at a time from a queue, see [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md)

## Installation

### Docker (recommended)

The app, the player and the overlay run as containers from `ghcr.io/gelluithor/caracal-node` (arm64 and amd64).
The device only runs Docker, the X display and, optionally, the CARACAL Fleet Agent.

- **With CARACAL Fleet:** prepare an SD card in Fleet (zero-touch) or let Fleet install a Raspberry Pi over SSH.
  Fleet also updates the nodes and can create the administrator of the admin UI during the installation.
- **Without Fleet:** see [docker/README.md](docker/README.md).

### Classic installation

On Raspberry Pi OS Lite or DietPi (Raspberry Pi 4/5):

```bash
git clone https://github.com/Gelluithor/Caracal.git
cd caracal
sudo bash install.sh
```

Open `http://<device-ip>:8080` and create the administrator. Running `install.sh` again updates the application;
the data in `/var/lib/caracal` are kept. To remove CARACAL: `sudo bash uninstall.sh` (`--purge` also removes the
data).

### Small boards (Raspberry Pi 4 with 1 GB)

CARACAL runs on a Raspberry Pi 4 with 1 GB of memory. On boards with up to 1.5 GB the player starts Chromium in its
low-end mode (fewer renderer processes, smaller caches, no background services) and restarts the browser between
two items when memory runs out; the countdown overlay draws at 30 frames per second. The installation (also the
one by CARACAL Fleet) turns on compressed swap in memory (zram) on boards with up to 2 GB. `CARACAL_LOW_MEMORY=1`
or `0` forces the mode on or off, `CARACAL_CHROMIUM_FLAGS` adds Chromium options, `CARACAL_OVERLAY_FPS` sets the
overlay frame rate. Very heavy dashboards still need memory: prefer fewer panels per dashboard on 1 GB boards.

## Data

Everything is stored in `/var/lib/caracal`: database, media, encrypted login profiles, player state and the
Chromium profile.

## CARACAL Fleet API

The CARACAL Fleet Agent talks to the node only through its local Fleet API (`/api/fleet/v1/*`, section
`CARACAL_FLEET_API_V2` in `app/main.py`). The API is protected by the key in `/etc/caracal-fleet-key` (classic) or
`/var/lib/caracal/.fleet-key` (Docker), which the agent writes when it is enrolled. Without the key the API answers
503 and exposes nothing. Everything the admin UI does is available through it as well, including the administrator
account (`POST /api/fleet/v1/admin`: created, or a new name and password that end its current sessions).

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
