"""The CARACAL web application: session, playlist, website logins, player endpoints, settings and security.

Runs the app in-process with a temporary data folder (CARACAL_DATA). Grafana is replaced by a stub.
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PW = 'admin-password-1'


@pytest.fixture(scope='module')
def app_mod():
    data = Path(tempfile.mkdtemp(prefix='caracal-test-'))
    os.environ['CARACAL_DATA'] = str(data)
    os.environ['CARACAL_FLEET_KEY_FILE'] = str(data / 'fleet-key')
    (data / 'fleet-key').write_text('fleet-test-key')
    spec = importlib.util.spec_from_file_location('caracal_app', ROOT / 'app' / 'main.py')
    mod = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    spec.loader.exec_module(mod)
    mod._grafana_discover = lambda url, tag: [{'title': f'{tag} {i}', 'url': f'{url.rstrip("/")}/d/{tag}{i}'} for i in range(2)]
    mod.request_player_restart = lambda: mod._test_restarts.append(1)
    mod.request_reboot = lambda: mod._test_reboots.append(1)
    mod._test_restarts, mod._test_reboots = [], []
    return mod


def from_loopback(app, port):
    """The app as seen from 127.0.0.1 (the player and the overlay run on the device itself)."""
    async def wrapped(scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            scope = {**scope, 'client': ('127.0.0.1', port)}
        await app(scope, receive, send)
    return wrapped


@pytest.fixture(scope='module')
def client(app_mod):
    c = TestClient(from_loopback(app_mod.app, 50000))
    assert c.get('/api/setup-status').json() == {'needed': True}
    assert c.post('/api/setup', data={'username': 'admin', 'password': 'short'}).status_code == 400
    assert c.post('/api/setup', data={'username': 'admin', 'password': PW}).status_code == 200
    assert c.post('/api/setup', data={'username': 'x', 'password': PW}).status_code == 409
    return c


@pytest.fixture(scope='module')
def local(app_mod):
    """What the player and the overlay send: the key from the data folder, from the loopback address."""
    c = TestClient(from_loopback(app_mod.app, 50001))
    c.headers['X-Caracal-Local'] = (Path(os.environ['CARACAL_DATA']) / 'local.key').read_text().strip()
    return c


@pytest.fixture(scope='module')
def fleet(app_mod):
    c = TestClient(from_loopback(app_mod.app, 50002))
    c.headers['X-Fleet-Key'] = 'fleet-test-key'
    return c


def ids(client):
    return [a['id'] for a in client.get('/api/assets').json()]


def test_session_and_pages(app_mod, client):
    page = client.get('/')
    assert page.status_code == 200 and '{{VERSION}}' not in page.text and 'app.js?v=' in page.text
    assert "script-src 'self'" in page.headers['content-security-policy'] and page.headers['x-frame-options'] == 'DENY'
    assert client.get('/api/me').json()['username'] == 'admin'
    anon = TestClient(app_mod.app)
    assert anon.get('/api/assets').status_code == 401
    assert anon.post('/api/login', data={'username': 'admin', 'password': 'wrong'}).status_code == 401
    assert anon.post('/api/login', data={'username': 'admin', 'password': PW}).status_code == 200
    assert anon.get('/api/assets').status_code == 200
    anon.post('/api/logout')
    assert anon.cookies.get('session') in (None, '')
    s = client.get('/api/system').json()
    assert {'hostname', 'cpu', 'ram', 'disk', 'uptime', 'version', 'runtime'} <= set(s)
    assert client.get('/api/system').headers['cache-control'] == 'no-store'


def test_playlist(app_mod, client, local):
    r = client.post('/api/v9/assets/web', json={'name': 'Web', 'source': 'https://example.com', 'duration': 3, 'scale': '1,25'})
    assert r.status_code == 200 and r.json()['scale'] == 1.25
    web = r.json()['id']
    assert client.post('/api/v9/assets/web', json={'name': 'Bad', 'source': 'ftp://x'}).status_code == 400
    assert client.post('/api/v9/assets/web', json={'name': 'Big', 'source': 'https://x', 'scale': 5}).status_code == 400
    up = client.post('/api/assets/upload', data={'name': 'Pic', 'duration': '10'}, files={'file': ('pic.png', b'\x89PNG\r\n\x1a\n', 'image/png')})
    assert up.status_code == 200
    assert client.post('/api/assets/upload', data={'name': 'Exe'}, files={'file': ('x.exe', b'MZ', 'application/octet-stream')}).status_code == 400
    g = client.post('/api/assets/grafana-tag', data={'name': 'Dash', 'grafana_url': 'https://grafana.example/', 'tag': 'tv', 'duration': '40'})
    assert g.status_code == 200 and g.json()['dashboards'] == 2
    col = g.json()['id']
    items = {a['name']: a for a in client.get('/api/assets').json()}
    assert items['Web']['duration'] == 5 and items['Pic']['kind'] == 'image' and items['Pic']['source'].startswith('/media/')
    media = items['Pic']['source']
    assert client.get(media).status_code == 200

    # editing
    assert client.put(f'/api/assets/{web}', json={'name': 'Web 2', 'source': 'https://example.org', 'duration': 20}).status_code == 200
    assert client.put(f'/api/assets/{web}', json={'name': '', 'source': 'https://example.org'}).status_code == 400
    assert client.put(f'/api/v7/assets/{web}/scale', json={'scale': 0.8}).json()['scale'] == 0.8
    assert client.put(f"/api/v7/assets/{items['Pic']['id']}/scale", json={'scale': 1}).status_code == 400
    assert client.put(f'/api/grafana-collections/{col}', json={'name': 'Dash 2', 'grafana_url': 'https://g2.example', 'tag': 'tv2', 'kiosk': False}).json()['dashboards'] == 2
    assert client.put(f'/api/assets/{web}/dark', json={'dark': 'force'}).json()['dark'] == 'force'
    assert client.put(f"/api/assets/{items['Pic']['id']}/dark", json={'dark': 'dark'}).status_code == 400
    assert client.put('/api/player/settings', json={'web_dark': 'dim'}).status_code == 400
    assert client.put('/api/player/settings', json={'web_dark': 'dark'}).json() == {'web_dark': 'dark'}
    assert client.post('/api/grafana/discover', data={'grafana_url': 'https://g', 'tag': 'x'}).json()['count'] == 2

    # what the player gets
    assert TestClient(app_mod.app).get('/api/player/playlist-expanded').status_code == 403
    expanded = local.get('/api/player/playlist-expanded').json()
    dashboards = [x for x in expanded if x.get('parent_id') == col]
    assert len(dashboards) == 2 and dashboards[0]['id'] == col * 100000 and 'kiosk=1' not in dashboards[0]['source']
    assert dashboards[0]['source'].endswith('?theme=dark') and dashboards[0]['dark'] == 'dark'
    assert next(x for x in expanded if x['id'] == web)['dark'] == 'force'
    assert [x['id'] for x in local.get('/api/player/playlist').json()] == ids(client)
    options = client.get('/api/v5/player/live-options').json()
    assert {'type': 'collection', 'id': col, 'name': 'Dash 2', 'count': 2} in options

    # order
    order = ids(client)
    assert client.put('/api/playlist/reorder', json={'ids': order[::-1]}).json()['ids'] == order[::-1]
    assert ids(client) == order[::-1]
    assert client.put('/api/playlist/reorder', json={'ids': order[:1]}).status_code == 409
    # deleting frees the media file
    client.delete(f"/api/assets/{items['Pic']['id']}")
    assert not (app_mod.MEDIA / Path(media).name).exists()
    client.put('/api/player/settings', json={'web_dark': 'off'})


def test_logins(app_mod, client, local):
    r = client.post('/api/profiles', data={'name': 'Zabbix', 'login_url': 'https://z/login', 'target_url': 'https://z/', 'username': 'u', 'password': 'p'})
    assert r.status_code == 200
    p = client.get('/api/profiles').json()[0]
    assert 'password' not in p and 'username_enc' not in p and p['auth_type'] == 'form'
    web = client.post('/api/v9/assets/web', json={'name': 'Z', 'source': 'https://z/', 'auth_profile_id': p['id']}).json()['id']
    secret = local.get(f"/api/player/profile/{p['id']}").json()
    assert secret['username'] == 'u' and secret['password'] == 'p'
    assert TestClient(app_mod.app).get(f"/api/player/profile/{p['id']}").status_code == 403
    assert client.put(f"/api/profiles/{p['id']}", json={'name': 'Basic', 'auth_type': 'http', 'target_url': 'https://z/', 'username': '', 'password': ''}).status_code == 200
    again = local.get(f"/api/player/profile/{p['id']}").json()
    assert again['auth_type'] == 'http' and again['login_url'] == 'https://z/' and again['password'] == 'p'
    client.delete(f"/api/profiles/{p['id']}")
    assert next(a for a in client.get('/api/assets').json() if a['id'] == web)['auth_profile_id'] is None
    client.delete(f'/api/assets/{web}')


def test_player_control(app_mod, client, local):
    web = client.post('/api/v9/assets/web', json={'name': 'Live', 'source': 'https://live.example'}).json()['id']
    local.post('/api/v2/player/heartbeat', json={'current_id': web, 'current_name': 'Live', 'frozen': False, 'remaining': 9, 'duration': 30})
    st = client.get('/api/v2/player/status').json()
    assert st['current_id'] == web and st['player_online'] is True
    assert client.post('/api/v6/player/control', json={'action': 'jump'}).status_code == 400
    assert client.post('/api/v6/player/control', json={'action': 'show', 'item_id': 999999}).status_code == 404
    cid = client.post('/api/v6/player/control', json={'action': 'freeze', 'item_id': web}).json()['command_id']
    cmd = local.get('/api/v6/player/command').json()
    assert cmd['command_id'] == cid and cmd['action'] == 'freeze' and cmd['item_id'] == web
    restart_before = cmd['restart_id']
    assert client.post('/api/v3/player/restart').status_code == 200 and app_mod._test_restarts
    assert local.get('/api/v6/player/command').json()['restart_id'] == restart_before   # host: systemd restarts it
    assert client.post('/api/system/reboot', json={}).status_code == 400
    assert client.post('/api/system/reboot', json={'confirm': 'REBOOT'}).status_code == 200 and app_mod._test_reboots
    assert client.get('/api/v4/player/overlay-settings').json() == {'enabled': True, 'size': 16}
    assert client.put('/api/v4/player/overlay-settings', json={'enabled': False, 'size': 24}).json()['size'] == 24
    assert client.get('/api/v4/player/overlay-settings').json() == {'enabled': False, 'size': 24}
    assert client.put('/api/v4/player/overlay-settings', json={'size': 0}).status_code == 400
    client.put('/api/v4/player/overlay-settings', json={'enabled': True, 'size': 16})
    client.delete(f'/api/assets/{web}')


def test_fleet_api(app_mod, fleet, client):
    assert TestClient(app_mod.app).get('/api/fleet/v1/snapshot').status_code in (401, 403)
    snap = fleet.get('/api/fleet/v1/snapshot').json()
    assert snap['api_version'] == 2 and {'assets', 'profiles', 'player', 'notifications', 'admin', 'overlay', 'player_settings', 'display'} <= set(snap)
    r = fleet.post('/api/fleet/v1/assets/web', json={'name': 'F', 'source': 'https://f.example', 'dark': 'dark'})
    fid = r.json()['id']
    assert fleet.put(f'/api/fleet/v1/assets/{fid}', json={'duration': 12, 'dark': ''}).status_code == 200
    a = next(x for x in client.get('/api/assets').json() if x['id'] == fid)
    assert a['duration'] == 12 and a['dark'] is None
    assert fleet.post('/api/fleet/v1/control', json={'action': 'show', 'item_id': fid}).json()['ok']
    assert fleet.put('/api/fleet/v1/player/settings', json={'web_dark': 'force'}).json()['web_dark'] == 'force'
    assert fleet.put('/api/fleet/v1/player/overlay', json={'size': 2}).status_code == 400
    assert fleet.delete(f'/api/fleet/v1/assets/{fid}').status_code == 200
    fleet.put('/api/fleet/v1/player/settings', json={'web_dark': 'off'})


def test_display_health(app_mod, local, client):
    assert TestClient(app_mod.app).post('/api/display/health', json={'black': True}).status_code == 403
    local.post('/api/display/health', json={'black': True})
    assert app_mod._display_info()['black_since']
    local.post('/api/display/health', json={'black': False})
    assert app_mod._display_info()['black_since'] is None


def test_removed_patch_endpoints(client):
    """The layers of old admin UIs are gone; the current UI, player and agent do not use them."""
    for method, path in [('post', '/api/assets/url'), ('post', '/api/player/restart'), ('get', '/api/player/status'),
                         ('post', '/api/player/control'), ('post', '/api/v2/player/control'), ('get', '/api/v3/player/overlay-state'),
                         ('put', '/api/assets/reorder'), ('post', '/api/v5/player/control')]:
        # 422: /api/assets/reorder now only matches PUT /api/assets/{id} with an invalid id
        assert getattr(client, method)(path).status_code in (404, 405, 422), path
