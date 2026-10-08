# On-screen notifications

Other apps can show pop-up notifications on the CARACAL screen. They are drawn by the overlay on top of the
playlist, one at a time, from a queue kept by the node.

Set it up in the admin UI under **Notifications**: create one token per app, choose the position, size and display
time, send a test notification, and watch or clear the queue.

There are two ways to get notifications to the screen:

- **push**: the app sends them to the CARACAL API (webhooks, scripts), see [API](#api),
- **pull**: CARACAL asks the app's API on its own every few seconds and announces new items, see [Watchers](#watchers).
  Use this for helpdesks and other systems that cannot send webhooks.

With [CARACAL Fleet](https://github.com/Gelluithor/Caracal-Fleet) the same can be managed centrally: sending
notifications to one or many screens, the settings and the watchers (tab *Notifications* of a device), and a
notification API with one token for many screens. Fleet uses the node's Fleet API (`/api/fleet/v1/notify*`, see
`docs/LOCAL-API.md` in the Fleet repository); changes made by Fleet appear in the audit log as "CARACAL Fleet".

## Queue rules

- One notification is on screen at a time; more important ones go first (`critical` > `warning` > `info`/`success`),
  otherwise first come, first served. The number of waiting notifications is shown as `+N`.
- A notification with the same `key` as one that is still waiting (or on screen) **replaces** it instead of adding a new
  one. Alert webhooks use this automatically, so "resolved" replaces "firing" that has not been shown yet.
- A full queue (20 by default) drops the oldest least important waiting notification, never a more important one.
- One app can have at most 10 waiting notifications, and every token has a rate limit (30 requests per minute by
  default, HTTP 429 above it).
- Waiting notifications expire: `info`/`success` after 15 minutes, `warning` after 30, `critical` after 60.
- History and audit log are limited, see [History and audit log](#history-and-audit-log).
- A large alert group (more than 5 alerts in one webhook) becomes a single notification.

## History and audit log

The node keeps only a limited amount of data, so the SD card does not fill up:

- **Waiting notifications**: at most the queue limit (20 by default, up to 200), and they expire (see above).
- **History** of shown, dropped and removed notifications: at most **500 entries and 7 days** by default, whichever
  comes first. Both limits can be set in **Notifications → Settings** (50–5000 entries, 1–90 days). **Clear history**
  deletes it at once; waiting notifications stay.
- **Audit log**: at most 2000 entries and 90 days. It records who changed the settings, created, edited, disabled or
  deleted a token or a watcher, sent a test, skipped or removed notifications or cleared the queue, the history or the
  audit log, with the time and the IP address. It also records refused tokens (the first one from an address and
  when the address gets blocked) and apps over their rate limit (at most once a minute). It never contains tokens,
  passwords or the query part of watcher URLs. **Clear audit log** deletes it; the clearing itself stays recorded.
- **Watchers** remember the IDs of the items in the current list and at most 5000 older ones.

Old entries are removed once a minute. API: `POST /api/notify/history/clear`, `GET /api/notify/audit?limit=200`,
`POST /api/notify/audit/clear` (administrator session).

## Sound

The device can play a short chime when a notification appears, a different one for each level (info and OK a soft
chime, warning two beeps, critical urgent beeping). In **Notifications → Settings**:

- **Sound**: off (default), critical only, warning and critical, or all notifications,
- **Volume**: 0–100 %,
- **Sound output (ALSA)**: empty for the default output, or a device such as `hdmi:CARD=vc4hdmi0,DEV=0` (Raspberry Pi
  HDMI 0) or `plughw:1,0`. `aplay -L` on the device lists the names.

A single notification can override the setting with `"sound": true` or `"sound": false` (header `X-Sound: yes/no`);
nothing plays while sound is off. The chime plays when the notification appears, not again when the overlay restarts.

The overlay plays the sound with `aplay` (ALSA), or `paplay` / `pw-play` when those are available. The Docker image
and `install.sh` install `alsa-utils`. In Docker, the `overlay` service in `docker/compose.yml` gets `/dev/snd` and the
host `audio` group (`CARACAL_AUDIO_GID`, 29 on Raspberry Pi OS and Debian). Nodes installed by CARACAL Fleet need this
compose file too. A TV usually plays HDMI audio only when it is not muted and HDMI audio is enabled on the Raspberry Pi.

## Security

- Every app has its own token (`crc_…`). It is shown once when created; the node stores only its SHA-256 hash.
  A token can be disabled or deleted at any time without affecting other apps.
- The token is sent as `Authorization: Bearer <token>`, as `X-Caracal-Token: <token>`, or as the password of HTTP Basic
  auth (any user name). `?token=` in the URL also works for senders that cannot set headers, but URLs end up in logs, so
  use it only when there is no other option.
- 20 invalid tokens from one address within 5 minutes block that address for 5 minutes.
- Bodies are limited to 64 KB, titles to 120 and messages to 600 characters; text is shown as plain text.
- The node serves plain HTTP on port 8080. Outside a trusted network, put it behind a reverse proxy with HTTPS
  (Caddy, Traefik, nginx) so the token is not sent in clear text.

## Watchers

A watcher calls a JSON API (GET) at a fixed interval (15 s to 24 h, 60 s by default), finds the list of items in
the response and remembers their IDs. Every item with an ID it has not seen before becomes a notification. The first
check only remembers what is already there, so existing tickets are not announced. More than 5 new items in one check
become a single notification, e.g. after the app was unreachable. Watcher notifications go through the same queue and
limits as any other.

Set them up under **Notifications → Watchers**. Presets fill in the fields for **Zammad, Jira, Redmine, Freshdesk,
GitLab and GitHub**: replace the server address, the project and the filter, and enter the API key. **Try it** shows
how many items were found and how the first notifications would look, without saving or sending anything. When it
fails, it shows the HTTP error, or the keys found in the response so that the right path can be picked.

| Field | Meaning |
|---|---|
| API URL | GET address returning JSON, ideally only open or new items, newest first |
| Authentication | none, `Bearer` token, Basic (user + password or API key), a custom header (`X-API-Key: …`) or OAuth2 |
| Path to the list | where the list is in the response, e.g. `issues`, `data.tickets`; empty when the response is the list |
| Item ID field | `id` by default; nested paths work (`ticket.id`) |
| Title, text | templates with item fields in braces: `New ticket #{id}: {subject}`, `{fields.reporter.displayName}` |
| Level | fixed level, or a field whose value sets it (`priority.name`: Urgent/High → critical, Major → warning) |

Credentials are encrypted with the node's vault key (the same as login profiles) and are never returned by the API.
Leaving them empty when editing keeps the stored ones. Changing the URL, the list path or the ID field starts the watcher
again without announcing the existing items.

### OAuth2

With **OAuth2** the watcher gets an access token from the token URL, uses it as `Authorization: Bearer`, keeps it
until shortly before `expires_in` and then gets a new one. When the API answers 401, the watcher gets a new token and
tries once more, in case the old token was revoked early.

| Grant | Use | Fields |
|---|---|---|
| Client credentials | server to server: Microsoft Graph (Entra ID), Keycloak, Auth0, Zendesk, … | Client ID, Client Secret |
| Password | a service user: ServiceNow, GLPI, … | Client ID, Client Secret if required, user, password |
| Refresh token | access on behalf of a user (Jira OAuth, Google, …) | Client ID, Client Secret, refresh token |

- **Scope** is sent when it is filled in (`https://graph.microsoft.com/.default`).
- **Extra token parameters** are added to the token request, e.g. `audience=https://api.example.com` for Auth0.
- **Client authentication**: the Client Secret is sent in the request body (`client_secret_post`, the default) or in a
  Basic header (`client_secret_basic`).
- **Refresh token**: you obtain it once (for example with the provider's OAuth playground or CLI) and paste it in.
  When the server sends a new refresh token, CARACAL stores it in place of the old one. Save a watcher with a refresh
  token before trying it, because the first token request may replace the pasted token.
- **Token errors** from the server are shown in **Try it** and on the watcher, e.g.
  `OAuth2 token: HTTP 401 invalid_client – Client authentication failed`.

CARACAL does not open the provider's login page itself (the authorization code flow), because it runs on plain HTTP
in the local network and most providers accept only HTTPS redirect addresses.

Presets with OAuth2: **Microsoft 365** (new e-mails in a shared mailbox through Microsoft Graph; the app registration
needs the `Mail.Read` application permission) and **ServiceNow** (new incidents, password grant).

API (administrator session): `GET/POST /api/notify/watchers`, `PUT/DELETE /api/notify/watchers/{id}`,
`POST /api/notify/watchers/{id}/check` (check now), `POST /api/notify/watchers/preview` (try a configuration).

## API

`POST http://<device>:8080/api/notify/v1`

| Field | Meaning |
|---|---|
| `title` | heading (aliases `subject`, `summary`) |
| `message` | text (aliases `text`, `body`, `msg`, `description`, `content`) |
| `level` | `info`, `success`, `warning`, `critical` (also `severity`, `priority` 1–5, `error`, `ok`, `down`, …) |
| `duration` | seconds on screen, 3–120 (default from the settings; `critical` at least 15) |
| `key` | deduplication key, e.g. `backup-db01` |
| `source` | name shown on the notification (default: the token's app name) |
| `sound` | `true` / `false`: play or do not play a sound regardless of the level (default: by the sound setting) |

At least `title` or `message` is required. Up to 5 notifications can be sent at once as a JSON array or as
`{"notifications": [...]}`.

Response: `{"ok": true, "queued": 1, "updated": 0, "dropped": 0, "ids": [42]}`. Errors: 401 bad token, 413 too large,
429 rate limit (with `Retry-After`), 503 notifications are turned off on this screen.

Accepted bodies:

- **JSON** with the fields above,
- **Grafana alerting / Prometheus Alertmanager** webhooks (`alerts` array): one notification per alert, `severity`
  label sets the level, resolved alerts are `success`,
- **Uptime Kuma** webhooks (`heartbeat` + `monitor`): DOWN is `critical`, UP is `success`,
- **plain text** (the body is the message; `Title`, `X-Level` or `Priority`, `X-Duration`, `X-Key`, `X-Sound` headers),
- **form data** (`application/x-www-form-urlencoded`) with the fields above.

CARACAL Fleet can send the same bodies to `POST /api/fleet/v1/notify` with its `X-Fleet-Key`.

## Examples

```bash
# JSON
curl -X POST http://caracal.local:8080/api/notify/v1 \
  -H "Authorization: Bearer crc_..." -H "Content-Type: application/json" \
  -d '{"title":"Backup finished","message":"DB01, 42 GB","level":"success","key":"backup-db01"}'

# plain text
curl -H "Authorization: Bearer crc_..." -H "Title: Deploy" -H "X-Level: warning" \
  -d "Deploying version 2.4" http://caracal.local:8080/api/notify/v1

# watch a log file and send every error line
tail -Fn0 /var/log/app.log | grep --line-buffered ERROR | while read -r line; do
  curl -s -H "Authorization: Bearer crc_..." -H "Title: app.log" -H "X-Level: critical" \
    --data-binary "$line" http://caracal.local:8080/api/notify/v1 >/dev/null
done
```

```powershell
Invoke-RestMethod -Method Post -Uri "http://caracal.local:8080/api/notify/v1" `
  -Headers @{Authorization="Bearer crc_..."} -ContentType "application/json; charset=utf-8" `
  -Body (@{title="Printer"; message="Toner is empty"; level="warning"} | ConvertTo-Json)
```

```python
import requests
requests.post("http://caracal.local:8080/api/notify/v1", timeout=5,
              headers={"Authorization": "Bearer crc_..."},
              json={"title": "Job done", "message": "Import finished", "level": "success"})
```

### Apps

- **Grafana** (Alerting → Contact points → Webhook): URL `http://<device>:8080/api/notify/v1`, *Authorization header*
  scheme `Bearer`, credentials = token.
- **Alertmanager**:
  ```yaml
  receivers:
    - name: caracal
      webhook_configs:
        - url: http://<device>:8080/api/notify/v1
          http_config:
            authorization: {type: Bearer, credentials: crc_...}
  ```
- **Uptime Kuma** (Notification type *Webhook*, body *Preset – application/json*): URL as above, *Additional Headers*
  `{"Authorization": "Bearer crc_..."}`.
- **Home Assistant**:
  ```yaml
  rest_command:
    caracal:
      url: http://<device>:8080/api/notify/v1
      method: POST
      headers: {Authorization: "Bearer crc_..."}
      content_type: application/json
      payload: '{"title": "{{ title }}", "message": "{{ message }}", "level": "{{ level | default(''info'') }}"}'
  ```
- **Zabbix**, **Jenkins**, **GitLab CI**, cron jobs, scripts: any HTTP request with the token works; see the examples above.
