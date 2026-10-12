"""CARACAL: the web administration and the local APIs of the player, the overlay and the CARACAL Fleet Agent.

Sections: storage and security, session, playlist (websites, media, Grafana collections), website logins, the
player (state, live control, countdown bar), the device, then the CARACAL Fleet API, notifications and their
watchers, sounds and picture, the screen picture, Fleet parity, dark websites and the black screen watch.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sqlite3
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import psutil
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from passlib.context import CryptContext

# ------------------------------------------------------------------ storage and security

DATA = Path(os.getenv('CARACAL_DATA', str(Path.home() / '.local/share/caracal')))
MEDIA = DATA / 'media'
DATA.mkdir(parents=True, exist_ok=True)
MEDIA.mkdir(exist_ok=True)
DB = DATA / 'hub.db'
SF, VF = DATA / 'secret.key', DATA / 'vault.key'
if not SF.exists():
    SF.write_text(secrets.token_urlsafe(48))
    os.chmod(SF, 0o600)
if not VF.exists():
    VF.write_bytes(Fernet.generate_key())
    os.chmod(VF, 0o600)
vault = Fernet(VF.read_bytes())          # website logins are stored encrypted
secret = SF.read_text()                 # signs the session cookies
pwd = CryptContext(schemes=['bcrypt'], deprecated='auto')
STATIC = Path(__file__).parent / 'static'
app = FastAPI(docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=STATIC), name='static')
app.mount('/media', StaticFiles(directory=MEDIA), name='media')
_UPLOAD_MAX = 2 * 1024 ** 3
_IMAGE_EXT, _VIDEO_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}, {'.mp4', '.webm', '.mkv'}
# In Docker (CARACAL_RUNTIME=docker) there is no systemd/sudo: the player restarts itself when asked and the host
# reboot is carried out by the CARACAL Fleet Agent, which reads the request from the Fleet snapshot.
RUNTIME = os.getenv('CARACAL_RUNTIME', 'host')

# Security headers: no framing (clickjacking), no MIME sniffing, no referrer, no plugins. Scripts only from this origin
# (the admin UI has no inline scripts or event handlers); styles allow inline style attributes.
_SECURITY_HEADERS = {
    'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
    'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"}


@app.middleware('http')
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in _SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path.startswith('/api/'):
        response.headers.setdefault('Cache-Control', 'no-store')
    return response


def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def rows(q, a=()):
    c = con()
    r = [dict(x) for x in c.execute(q, a).fetchall()]
    c.close()
    return r


def _columns(c, table):
    return [r[1] for r in c.execute(f'PRAGMA table_info({table})').fetchall()]


def init():
    c = con()
    c.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT);
CREATE TABLE IF NOT EXISTS assets(id INTEGER PRIMARY KEY, name TEXT, kind TEXT, source TEXT, duration INTEGER DEFAULT 30,
  position INTEGER DEFAULT 0, auth_profile_id INTEGER);
CREATE TABLE IF NOT EXISTS auth_profiles(id INTEGER PRIMARY KEY, name TEXT, login_url TEXT, target_url TEXT,
  username_enc BLOB, password_enc BLOB, user_selector TEXT, pass_selector TEXT, submit_selector TEXT);
CREATE TABLE IF NOT EXISTS player_settings(key TEXT PRIMARY KEY, value TEXT);
""")
    # columns added by later versions
    if 'scale' not in _columns(c, 'assets'):
        c.execute('ALTER TABLE assets ADD COLUMN scale REAL DEFAULT 1.0')
    c.execute('UPDATE assets SET scale=1.0 WHERE scale IS NULL')
    if 'dark' not in _columns(c, 'assets'):          # dark mode of a website: NULL = as the device
        c.execute('ALTER TABLE assets ADD COLUMN dark TEXT')
    # login profiles: 'form' fills in the page's log-in form, 'http' answers the browser's HTTP Basic/Digest pop-up
    if 'auth_type' not in _columns(c, 'auth_profiles'):
        c.execute("ALTER TABLE auth_profiles ADD COLUMN auth_type TEXT DEFAULT 'form'")
    c.commit()
    c.close()


init()

# The player and the overlay prove that they run on this device with a key only processes of this device can read
# (DATA/local.key, mode 600). The loopback address alone is not enough: a reverse proxy on the same host (as
# recommended for HTTPS) also connects from 127.0.0.1, and these endpoints return e.g. decrypted website logins.
_LOCAL_KEY_FILE = DATA / 'local.key'
if not _LOCAL_KEY_FILE.exists():
    _fd = os.open(_LOCAL_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(_fd, secrets.token_urlsafe(32).encode())
    os.close(_fd)
_LOCAL_KEY = _LOCAL_KEY_FILE.read_text().strip()


def _local_only(req: Request):
    if not req.client or req.client.host not in ('127.0.0.1', '::1') or \
            not secrets.compare_digest(req.headers.get('X-Caracal-Local', ''), _LOCAL_KEY):
        raise HTTPException(403)


def _remove_media(source):
    """A deleted picture or video also frees its file, unless another item still uses it."""
    if not str(source or '').startswith('/media/'):
        return
    if not rows('SELECT id FROM assets WHERE source=?', (source,)):
        (MEDIA / Path(source).name).unlink(missing_ok=True)


def request_player_restart():
    if RUNTIME != 'docker':
        subprocess.Popen(['sudo', '/bin/systemctl', 'restart', 'caracal-player.service'], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    state = _cc2_read()
    state['restart_player_request'] = int(state.get('restart_player_request', 0)) + 1
    _cc2_write(state)
    return True


def request_reboot():
    if RUNTIME != 'docker':
        subprocess.Popen(['sudo', '/bin/systemctl', 'reboot'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    state = _cc2_read()
    state['reboot_request'] = int(state.get('reboot_request', 0)) + 1
    _cc2_write(state)
    return True


# ------------------------------------------------------------------ session

_login_fails = {}


def _pw_mark(password_hash):
    """Part of the session: a new password ends the sessions signed with the old one."""
    return hashlib.sha256(str(password_hash or '').encode()).hexdigest()[:16]


def auth(req: Request):
    try:
        claims = jwt.decode(req.cookies.get('session', ''), secret, algorithms=['HS256'])
        uid = int(claims['sub'])
    except Exception:
        raise HTTPException(401)
    r = rows('SELECT id,username,password_hash FROM users WHERE id=?', (uid,))
    if not r or claims.get('pw') != _pw_mark(r[0]['password_hash']):
        raise HTTPException(401)
    return {'id': r[0]['id'], 'username': r[0]['username']}


def tok(i):
    r = rows('SELECT password_hash FROM users WHERE id=?', (i,))
    return jwt.encode({'sub': str(i), 'pw': _pw_mark(r[0]['password_hash'] if r else ''),
                       'exp': datetime.now(timezone.utc) + timedelta(days=1)}, secret, algorithm='HS256')


def _session(uid):
    r = JSONResponse({'ok': 1})
    r.set_cookie('session', tok(uid), httponly=True, samesite='strict')
    return r


@app.get('/')
def home():
    # the version in the addresses of the scripts and styles: a new CARACAL never runs with old files from the cache
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    version = _caracal_version() or str(int((STATIC / 'app.js').stat().st_mtime))
    return HTMLResponse(html.replace('{{VERSION}}', version), headers={'Cache-Control': 'no-cache'})


@app.get('/api/setup-status')
def setup_status():
    return {'needed': not rows('SELECT id FROM users LIMIT 1')}


@app.post('/api/setup')
def setup(username: str = Form(...), password: str = Form(...)):
    if rows('SELECT id FROM users LIMIT 1'):
        raise HTTPException(409, 'Setup completed')
    if len(password) < 10:
        raise HTTPException(400, 'Heslo musí mít nejméně 10 znaků')
    c = con()
    uid = c.execute('INSERT INTO users(username,password_hash) VALUES(?,?)', (username, pwd.hash(password))).lastrowid
    c.commit()
    c.close()
    return _session(uid)


@app.post('/api/login')
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = request.client.host if request.client else ''
    now = time.time()
    recent = [t for t in _login_fails.get(ip, []) if t > now - 300]
    if len(recent) >= 10:
        raise HTTPException(429, 'Příliš mnoho pokusů o přihlášení, zkuste to za 5 minut')
    r = rows('SELECT * FROM users WHERE username=?', (username,))
    if not r or not pwd.verify(password, r[0]['password_hash']):
        _login_fails[ip] = recent + [now]
        raise HTTPException(401, 'Chybné přihlášení')
    _login_fails.pop(ip, None)
    return _session(r[0]['id'])


@app.get('/api/me')
def me(u=Depends(auth)):
    return u


@app.post('/api/logout')
def logout():
    r = JSONResponse({'ok': 1})
    r.delete_cookie('session')
    return r


# ------------------------------------------------------------------ playlist

def _next_position(c):
    return c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM assets').fetchone()[0]


def _scale(value, message='Měřítko musí být 0,5 až 3,0'):
    try:
        scale = float(str(value).strip().replace(',', '.'))
    except (TypeError, ValueError):
        raise HTTPException(400, 'Neplatné měřítko')
    if not 0.5 <= scale <= 3.0:
        raise HTTPException(400, message)
    return scale


def _duration(value, default=30):
    try:
        return max(5, int(value if value not in (None, '') else default))
    except (TypeError, ValueError):
        raise HTTPException(400, 'Neplatná doba zobrazení')


def _profile_ref(value):
    """A login profile of a website: None, or the id of an existing profile."""
    if value in (None, '', 'null'):
        return None
    try:
        pid = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, 'Neplatný přihlašovací profil')
    if not rows('SELECT id FROM auth_profiles WHERE id=?', (pid,)):
        raise HTTPException(404, 'Přihlašovací profil nebyl nalezen')
    return pid


@app.get('/api/assets')
def assets(u=Depends(auth)):
    return rows('SELECT * FROM assets ORDER BY position,id')


@app.post('/api/v9/assets/web')
async def add_web(request: Request, u=Depends(auth)):
    data = await request.json()
    name, source = str(data.get('name', '')).strip(), str(data.get('source', '')).strip()
    if not name:
        raise HTTPException(400, 'Vyplň název')
    if not source.startswith(('http://', 'https://')):
        raise HTTPException(400, 'URL musí začínat http:// nebo https://')
    duration, scale, profile_id = _duration(data.get('duration'), 30), _scale(data.get('scale', 1)), _profile_ref(data.get('auth_profile_id'))
    c = con()
    asset_id = c.execute('INSERT INTO assets(name,kind,source,duration,position,auth_profile_id,scale) VALUES(?,?,?,?,?,?,?)',
                         (name, 'web', source, duration, _next_position(c), profile_id, scale)).lastrowid
    c.commit()
    c.close()
    return {'ok': True, 'id': asset_id, 'scale': scale}


@app.post('/api/assets/upload')
async def upload(name: str = Form(...), duration: int = Form(15), file: UploadFile = File(...), u=Depends(auth)):
    ext = Path(file.filename or '').suffix.lower()
    kind = 'image' if ext in _IMAGE_EXT else 'video' if ext in _VIDEO_EXT else None
    if not kind:
        raise HTTPException(400, 'Nepodporovaný soubor')
    fn, size = secrets.token_hex(10) + ext, 0
    try:
        with (MEDIA / fn).open('wb') as f:
            while chunk := await file.read(1048576):
                size += len(chunk)
                if size > _UPLOAD_MAX:
                    raise HTTPException(413, 'Soubor je větší než 2 GB')
                f.write(chunk)
    except BaseException:
        (MEDIA / fn).unlink(missing_ok=True)
        raise
    c = con()
    c.execute('INSERT INTO assets(name,kind,source,duration,position) VALUES(?,?,?,?,?)',
              (name, kind, '/media/' + fn, max(5, duration), _next_position(c)))
    c.commit()
    c.close()
    return {'ok': 1}


@app.put('/api/assets/{asset_id}')
async def update_asset(asset_id: int, request: Request, u=Depends(auth)):
    """Name, display time and (websites) address and login profile; the size and dark mode have their own routes."""
    data = await request.json()
    name, source = str(data.get('name', '')).strip(), str(data.get('source', '')).strip()
    duration, profile_id = _duration(data.get('duration'), 30), _profile_ref(data.get('auth_profile_id'))
    if not name:
        raise HTTPException(400, 'Název nesmí být prázdný')
    current = rows('SELECT * FROM assets WHERE id=?', (asset_id,))
    if not current:
        raise HTTPException(404, 'Položka nebyla nalezena')
    if current[0]['kind'] == 'web':
        if not source.startswith(('http://', 'https://')):
            raise HTTPException(400, 'URL musí začínat http:// nebo https://')
    else:
        source, profile_id = current[0]['source'], None
    c = con()
    c.execute('UPDATE assets SET name=?,source=?,duration=?,auth_profile_id=? WHERE id=?', (name, source, duration, profile_id, asset_id))
    c.commit()
    c.close()
    return {'ok': True}


@app.put('/api/v7/assets/{asset_id}/scale')
async def save_scale(asset_id: int, request: Request, u=Depends(auth)):
    scale = _scale((await request.json()).get('scale'), 'Měřítko musí být 50 až 300 %')
    a = rows('SELECT kind FROM assets WHERE id=?', (asset_id,))
    if not a:
        raise HTTPException(404, 'Položka nebyla nalezena')
    if a[0]['kind'] not in ('web', 'grafana-tag'):
        raise HTTPException(400, 'Měřítko lze měnit jen u webu nebo Grafana kolekce')
    c = con()
    c.execute('UPDATE assets SET scale=? WHERE id=?', (scale, asset_id))
    c.commit()
    c.close()
    return {'ok': True, 'asset_id': asset_id, 'scale': scale}


@app.delete('/api/assets/{i}')
def delete(i: int, u=Depends(auth)):
    r = rows('SELECT source FROM assets WHERE id=?', (i,))
    c = con()
    c.execute('DELETE FROM assets WHERE id=?', (i,))
    c.commit()
    c.close()
    if r:
        _remove_media(r[0]['source'])
    return {'ok': 1}


@app.put('/api/playlist/reorder')
async def reorder(request: Request, u=Depends(auth)):
    ids = (await request.json()).get('ids')
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, 'Chybí pořadí playlistu')
    try:
        ids = [int(x) for x in ids]
    except (TypeError, ValueError):
        raise HTTPException(400, 'Neplatné ID položky')
    existing = [r['id'] for r in rows('SELECT id FROM assets ORDER BY position,id')]
    if len(ids) != len(existing) or set(ids) != set(existing):
        raise HTTPException(409, 'Playlist se mezitím změnil. Obnov stránku a zkus to znovu.')
    c = con()
    for position, asset_id in enumerate(ids):
        c.execute('UPDATE assets SET position=? WHERE id=?', (position, asset_id))
    c.commit()
    c.close()
    return {'ok': True, 'ids': ids}


# Grafana collections: every dashboard with a tag, found through Grafana's guest access. The player asks for the
# expanded playlist every few seconds; Grafana is asked at most once a minute per collection and a short outage keeps
# the last known dashboards instead of emptying the playlist.
_GRAFANA_CACHE, _GRAFANA_TTL, _GRAFANA_LOCK = {}, 60, threading.Lock()


def _grafana_cached(base_url, tag, max_age=_GRAFANA_TTL):
    key = (str(base_url or '').rstrip('/'), str(tag or ''))
    hit = _GRAFANA_CACHE.get(key)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    with _GRAFANA_LOCK:
        hit = _GRAFANA_CACHE.get(key)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        try:
            return _grafana_discover(*key)
        except HTTPException:
            if hit:
                _GRAFANA_CACHE[key] = (time.time() - max_age + 15, hit[1])   # stale, tried again in 15 s
                return hit[1]
            raise


def _grafana_discover(base_url, tag):
    base = base_url.rstrip('/')
    query = urllib.parse.urlencode({'tag': tag, 'type': 'dash-db', 'limit': 5000})
    request = urllib.request.Request(base + '/api/search?' + query, headers={'Accept': 'application/json', 'User-Agent': 'CARACAL/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=15, context=ssl._create_unverified_context()) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception as error:
        raise HTTPException(400, 'Grafana API není dostupné pro Guest účet: ' + str(error))
    dashboards = [{'title': item.get('title') or item.get('uid') or 'Grafana dashboard', 'url': base + item['url']}
                  for item in data if item.get('type') == 'dash-db' and item.get('url')]
    if len(_GRAFANA_CACHE) > 200:
        _GRAFANA_CACHE.clear()
    _GRAFANA_CACHE[(base, str(tag or ''))] = (time.time(), dashboards)
    return dashboards


def _grafana_fields(name, grafana_url, tag):
    name, grafana_url, tag = str(name or '').strip(), str(grafana_url or '').strip().rstrip('/'), str(tag or '').strip()
    if not name or not tag:
        raise HTTPException(400, 'Vyplň název a tag')
    if not grafana_url.startswith(('http://', 'https://')):
        raise HTTPException(400, 'URL Grafany musí začínat http:// nebo https://')
    found = _grafana_discover(grafana_url, tag)
    if not found:
        raise HTTPException(404, 'Pod tímto tagem nebyl nalezen žádný dashboard dostupný pro Guest účet')
    return name, grafana_url, tag, found


@app.post('/api/assets/grafana-tag')
def add_grafana_collection(name: str = Form(...), grafana_url: str = Form(...), tag: str = Form(...), duration: int = Form(60),
                           scale: float = Form(1.0), kiosk: bool = Form(True), u=Depends(auth)):
    scale = _scale(scale)
    name, grafana_url, tag, found = _grafana_fields(name, grafana_url, tag)
    config = {'grafana_url': grafana_url, 'tag': tag, 'kiosk': bool(kiosk)}
    c = con()
    asset_id = c.execute('INSERT INTO assets(name,kind,source,duration,position,auth_profile_id,scale) VALUES(?,?,?,?,?,NULL,?)',
                         (name, 'grafana-tag', json.dumps(config, ensure_ascii=False), max(5, duration), _next_position(c), scale)).lastrowid
    c.commit()
    c.close()
    return {'ok': True, 'dashboards': len(found), 'id': asset_id}


@app.put('/api/grafana-collections/{asset_id}')
async def update_grafana_collection(asset_id: int, request: Request, u=Depends(auth)):
    data = await request.json()
    row = rows("SELECT * FROM assets WHERE id=? AND kind='grafana-tag'", (asset_id,))
    if not row:
        raise HTTPException(404, 'Grafana kolekce nebyla nalezena')
    row, config = row[0], json.loads(row[0]['source'] or '{}')
    scale = _scale(data.get('scale', row['scale'] or 1.0), 'Měřítko musí být 50 až 300 %')
    duration = _duration(data.get('duration'), row['duration'] or 60)
    name, grafana_url, tag, found = _grafana_fields(data.get('name') or row['name'], data.get('grafana_url', config.get('grafana_url', '')),
                                                    data.get('tag', config.get('tag', '')))
    config = {'grafana_url': grafana_url, 'tag': tag, 'kiosk': bool(data.get('kiosk', config.get('kiosk', True)))}
    c = con()
    c.execute('UPDATE assets SET name=?,source=?,duration=?,scale=? WHERE id=?', (name, json.dumps(config, ensure_ascii=False), duration, scale, asset_id))
    c.commit()
    c.close()
    return {'ok': True, 'dashboards': len(found), 'scale': scale}


@app.post('/api/grafana/discover')
def try_grafana_tag(grafana_url: str = Form(...), tag: str = Form(...), u=Depends(auth)):
    result = _grafana_discover(grafana_url.strip(), tag.strip())
    return {'ok': True, 'count': len(result), 'dashboards': result[:50]}


def expanded_player_playlist():
    """The playlist as the player plays it: a Grafana collection becomes its dashboards (id = collection * 100000 +
    index), every website gets its resulting dark mode."""
    output, web_dark = [], _web_dark()
    for asset in rows('SELECT * FROM assets ORDER BY position,id'):
        if asset.get('kind') != 'grafana-tag':
            if asset.get('kind') == 'web':
                asset['dark'] = _dark_of(asset, web_dark)
            output.append(asset)
            continue
        dark = _dark_of(asset, web_dark)
        try:
            config = json.loads(asset.get('source') or '{}')
            for index, dashboard in enumerate(_grafana_cached(config.get('grafana_url', ''), config.get('tag', ''))):
                url = dashboard['url']
                params = (['kiosk=1'] if config.get('kiosk', True) else []) + (['theme=dark'] if dark != 'off' else [])
                if params:
                    url += ('&' if '?' in url else '?') + '&'.join(params)
                output.append({'id': int(asset['id']) * 100000 + index, 'parent_id': asset['id'],
                               'name': asset['name'] + ' · ' + dashboard['title'], 'kind': 'web', 'source': url,
                               'duration': asset['duration'], 'position': asset['position'], 'auth_profile_id': None,
                               'scale': asset.get('scale') or 1.0, 'dark': 'dark' if dark == 'force' else dark})
        except Exception as error:
            print('Grafana collection error', asset.get('id'), error, flush=True)
    return output


# ------------------------------------------------------------------ website logins

def _profile_type(value):
    return 'http' if str(value or '').strip().lower() == 'http' else 'form'


@app.get('/api/profiles')
def profiles(u=Depends(auth)):
    return rows("SELECT id,name,login_url,target_url,user_selector,pass_selector,submit_selector,"
                "COALESCE(auth_type,'form') AS auth_type FROM auth_profiles")


@app.post('/api/profiles')
def add_profile(name: str = Form(...), login_url: str = Form(''), target_url: str = Form(...), username: str = Form(...),
                password: str = Form(...), user_selector: str = Form('input[name="name"],input[name="username"]'),
                pass_selector: str = Form('input[name="password"]'),
                submit_selector: str = Form('button[type="submit"],input[type="submit"]'), auth_type: str = Form('form'),
                u=Depends(auth)):
    auth_type = _profile_type(auth_type)
    login_url = login_url.strip() or (target_url.strip() if auth_type == 'http' else '')
    if not target_url.strip().startswith(('http://', 'https://')) or not login_url.startswith(('http://', 'https://')):
        raise HTTPException(400, 'URL musí začínat http:// nebo https://')
    c = con()
    c.execute('INSERT INTO auth_profiles(name,login_url,target_url,username_enc,password_enc,user_selector,pass_selector,'
              'submit_selector,auth_type) VALUES(?,?,?,?,?,?,?,?,?)',
              (name, login_url, target_url.strip(), vault.encrypt(username.encode()), vault.encrypt(password.encode()),
               user_selector, pass_selector, submit_selector, auth_type))
    c.commit()
    c.close()
    return {'ok': 1}


@app.put('/api/profiles/{profile_id}')
async def edit_profile(profile_id: int, request: Request, u=Depends(auth)):
    """Empty user name or password keep the stored ones; an HTTP login needs no form and no selectors."""
    data = await request.json()
    r = rows('SELECT * FROM auth_profiles WHERE id=?', (profile_id,))
    if not r:
        raise HTTPException(404, 'Profil nebyl nalezen')
    r = r[0]
    auth_type = _profile_type(data.get('auth_type', r['auth_type']))
    name = str(data.get('name', '')).strip()
    login, target = str(data.get('login_url', '')).strip(), str(data.get('target_url', '')).strip()
    us, ps, ss = (str(data.get(k, '')).strip() for k in ('user_selector', 'pass_selector', 'submit_selector'))
    if auth_type == 'http':
        login = login or target
        us = us or r['user_selector'] or 'input[name="username"]'
        ps = ps or r['pass_selector'] or 'input[name="password"]'
        ss = ss or r['submit_selector'] or 'button[type="submit"]'
    if not name or not login.startswith(('http://', 'https://')) or not target.startswith(('http://', 'https://')) or not us or not ps or not ss:
        raise HTTPException(400, 'Zkontroluj povinna pole a URL')
    username, password = str(data.get('username', '')), str(data.get('password', ''))
    ue = vault.encrypt(username.encode()) if username.strip() else r['username_enc']
    pe = vault.encrypt(password.encode()) if password else r['password_enc']
    c = con()
    c.execute('UPDATE auth_profiles SET name=?,login_url=?,target_url=?,username_enc=?,password_enc=?,user_selector=?,'
              'pass_selector=?,submit_selector=?,auth_type=? WHERE id=?', (name, login, target, ue, pe, us, ps, ss, auth_type, profile_id))
    c.commit()
    c.close()
    return {'ok': True}


@app.delete('/api/profiles/{profile_id}')
def delete_profile(profile_id: int, u=Depends(auth)):
    c = con()
    c.execute('UPDATE assets SET auth_profile_id=NULL WHERE auth_profile_id=?', (profile_id,))
    c.execute('DELETE FROM auth_profiles WHERE id=?', (profile_id,))
    c.commit()
    c.close()
    return {'ok': True}


# ------------------------------------------------------------------ player

# The player's state and the commands for it live in one small file shared by the app's workers: the player reports
# what it shows every second (/api/v2/player/heartbeat) and asks for the latest command (/api/v6/player/command).
# The key names stay as they are: the file outlives updates.
_CC2_FILE = DATA / 'player-control-v2.json'
_CC2_LOCK = threading.Lock()


def _cc2_read():
    with _CC2_LOCK:
        state = {'current_id': None, 'current_name': '', 'frozen': False, 'remaining': None, 'duration': None, 'updated': 0}
        try:
            state.update(json.loads(_CC2_FILE.read_text(encoding='utf-8')))
        except Exception:
            pass
        return state


def _cc2_write(state):
    with _CC2_LOCK:
        tmp = _CC2_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        tmp.replace(_CC2_FILE)


@app.get('/api/player/playlist')
def player_playlist(req: Request):
    _local_only(req)
    return rows('SELECT * FROM assets ORDER BY position,id')


@app.get('/api/player/playlist-expanded')
def player_playlist_expanded(req: Request):
    _local_only(req)
    return expanded_player_playlist()


@app.get('/api/player/profile/{i}')
def player_profile(i: int, req: Request):
    """The login of a website with its credentials, only for the player of this device."""
    _local_only(req)
    r = rows('SELECT * FROM auth_profiles WHERE id=?', (i,))
    if not r:
        raise HTTPException(404)
    d = r[0]
    d['username'] = vault.decrypt(d.pop('username_enc')).decode()
    d['password'] = vault.decrypt(d.pop('password_enc')).decode()
    return d


@app.post('/api/v2/player/heartbeat')
async def player_heartbeat(req: Request):
    _local_only(req)
    d = await req.json()
    state = _cc2_read()
    for k in ('current_id', 'current_name', 'frozen', 'remaining', 'duration'):
        if k in d:
            state[k] = d[k]
    state['updated'] = time.time()
    _cc2_write(state)
    return {'ok': True}


@app.get('/api/v2/player/status')
def player_status(u=Depends(auth)):
    state = _cc2_read()
    state['player_online'] = time.time() - float(state.get('updated') or 0) < 8
    return state


@app.get('/api/v5/player/live-options')
def live_options(u=Depends(auth)):
    """What the live control can show: items, Grafana collections and their single dashboards."""
    expanded, options = expanded_player_playlist(), []
    for asset in rows('SELECT * FROM assets ORDER BY position,id'):
        if asset.get('kind') == 'grafana-tag':
            children = [x for x in expanded if x.get('parent_id') == asset['id']]
            options.append({'type': 'collection', 'id': asset['id'], 'name': asset['name'], 'count': len(children)})
            options += [{'type': 'dashboard', 'id': x['id'], 'parent_id': asset['id'], 'name': x['name']} for x in children]
        else:
            options.append({'type': 'asset', 'id': asset['id'], 'name': asset['name'], 'kind': asset['kind']})
    return options


def _player_command(action, item_id=None, collection_id=None):
    """Validate a live control command and hand it to the player (used by the admin UI and by Fleet)."""
    if action not in ('show', 'freeze', 'show_collection', 'freeze_collection', 'unfreeze', 'next'):
        raise HTTPException(400, 'Neznámá akce')
    if action in ('show', 'freeze', 'show_collection', 'freeze_collection'):
        expanded = expanded_player_playlist()
        if action in ('show', 'freeze'):
            try:
                item_id = int(item_id)
            except (TypeError, ValueError):
                raise HTTPException(400, 'Vyber položku nebo dashboard')
            if not any(int(x['id']) == item_id for x in expanded):
                raise HTTPException(404, 'Vybraná položka není dostupná')
        else:
            try:
                collection_id = int(collection_id)
            except (TypeError, ValueError):
                raise HTTPException(400, 'Vyber Grafana kolekci')
            if not rows("SELECT id FROM assets WHERE id=? AND kind='grafana-tag'", (collection_id,)):
                raise HTTPException(404, 'Grafana kolekce nebyla nalezena')
            if not any(x.get('parent_id') == collection_id for x in expanded):
                raise HTTPException(404, 'Kolekce neobsahuje dostupné dashboardy')
    # 'updated' is the player's liveness timestamp, so it is not touched here
    state = _cc2_read()
    state['v6_command_id'] = int(state.get('v6_command_id', 0)) + 1
    state.update(v6_action=action, v6_item_id=item_id, v6_collection_id=collection_id)
    _cc2_write(state)
    return state['v6_command_id']


@app.post('/api/v6/player/control')
async def player_control(req: Request, u=Depends(auth)):
    d = await req.json()
    return {'ok': True, 'command_id': _player_command(str(d.get('action', '')), d.get('item_id'), d.get('collection_id'))}


@app.get('/api/v6/player/command')
def player_command(req: Request):
    _local_only(req)
    s = _cc2_read()
    return {'command_id': s.get('v6_command_id', 0), 'action': s.get('v6_action', ''), 'item_id': s.get('v6_item_id'),
            'collection_id': s.get('v6_collection_id'), 'restart_id': s.get('restart_player_request', 0)}


@app.post('/api/v3/player/restart')
def restart_player(u=Depends(auth)):
    request_player_restart()
    return {'ok': True}


def _overlay_state():
    state = _cc2_read()
    return {'enabled': bool(state.get('overlay_enabled', True)), 'size': int(state.get('overlay_size', 16))}


def _save_overlay(data):
    """The countdown bar at the bottom of the screen: on/off and its height."""
    state, current = _cc2_read(), _overlay_state()
    enabled = bool(data.get('enabled', current['enabled']))
    try:
        size = int(data.get('size', current['size']))
    except (TypeError, ValueError):
        raise HTTPException(400, 'Neplatná velikost odpočtu')
    if size < 1:
        raise HTTPException(400, 'Velikost odpočtu musí být kladné celé číslo')
    state['overlay_enabled'], state['overlay_size'] = enabled, size
    _cc2_write(state)
    return {'ok': True, 'enabled': enabled, 'size': size}


@app.get('/api/v4/player/overlay-settings')
def overlay_settings(u=Depends(auth)):
    return _overlay_state()


@app.put('/api/v4/player/overlay-settings')
async def save_overlay_settings(request: Request, u=Depends(auth)):
    return _save_overlay(await request.json())


# ------------------------------------------------------------------ the device

@app.get('/api/system')
def system(u=Depends(auth)):
    try:
        temperature = float(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000
    except (OSError, ValueError):
        temperature = None
    return {'hostname': socket.gethostname(), 'cpu': psutil.cpu_percent(), 'ram': psutil.virtual_memory().percent,
            'disk': psutil.disk_usage('/').percent, 'temperature': temperature, 'uptime': time.time() - psutil.boot_time(),
            'version': _caracal_version(), 'runtime': RUNTIME}


@app.post('/api/system/reboot')
async def reboot_device(request: Request, u=Depends(auth)):
    if (await request.json()).get('confirm') != 'REBOOT':
        raise HTTPException(400, 'Restart nebyl potvrzen')
    request_reboot()
    return {'ok': True}


# ------------------------------------------------------------------ CARACAL Fleet API
# Local API for the CARACAL Fleet Agent (CARACAL Fleet repository, docs/LOCAL-API.md).
# Authenticated with the shared key in /etc/caracal-fleet-key, written by the agent when it is enrolled.
# Replaces the hand-applied CARACAL_FLEET_API_V1 patch: the snapshot returns the live v2 player state.
_FLEET_KEY_FILE=Path(os.getenv('CARACAL_FLEET_KEY_FILE','/etc/caracal-fleet-key'))
_FLEET_MEDIA_MAX=2*1024**3
_FLEET_IMAGE={'.png','.jpg','.jpeg','.webp','.gif'};_FLEET_VIDEO={'.mp4','.webm','.mkv'}
def _fleet_auth(req:Request):
 try:key=_FLEET_KEY_FILE.read_text().strip()
 except OSError:raise HTTPException(503,'Fleet Agent is not configured')
 if not key or not hmac.compare_digest(req.headers.get('X-Fleet-Key',''),key):raise HTTPException(401,'Invalid Fleet key')
def _fleet_duration(value):
 try:return max(5,int(float(value)))
 except (TypeError,ValueError):raise HTTPException(400,'Invalid duration')
def _fleet_scale(value):
 try:scale=float(str(value).replace(',','.'))
 except (TypeError,ValueError):raise HTTPException(400,'Invalid scale')
 if not .5<=scale<=3:raise HTTPException(400,'Scale must be 0.5 to 3.0')
 return scale
def _caracal_version():
 try:return (Path(__file__).resolve().parent.parent/'VERSION').read_text().strip()
 except OSError:return os.getenv('CARACAL_VERSION','')
def _fleet_position(c):return c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM assets').fetchone()[0]
# login profiles of web pages; the credentials never leave the node
_FLEET_PROFILE_COLUMNS="SELECT id,name,login_url,target_url,user_selector,pass_selector,submit_selector,COALESCE(auth_type,'form') AS auth_type FROM auth_profiles"
_FLEET_SELECTORS={'user_selector':'input[name="name"],input[name="username"]','pass_selector':'input[name="password"]','submit_selector':'button[type="submit"],input[type="submit"]'}
def _fleet_profile_id(value):
 # None, '' and 0 mean "without login"; 400 rather than 404 so the agent does not report a missing endpoint
 if value in (None,'','null',0,'0'):return None
 try:profile_id=int(value)
 except (TypeError,ValueError):raise HTTPException(400,'Invalid login profile')
 if not rows('SELECT id FROM auth_profiles WHERE id=?',(profile_id,)):raise HTTPException(400,'Login profile not found')
 return profile_id
def _fleet_profile_fields(d,current=None):
 current=current or {};out={}
 for key,limit in (('name',200),('login_url',4000),('target_url',4000)):
  out[key]=str(d.get(key,current.get(key,''))).strip()[:limit]
 out['auth_type']=_profile_type(d.get('auth_type',current.get('auth_type')))
 if out['auth_type']=='http' and not out['login_url']:out['login_url']=out['target_url']
 if not out['name']:raise HTTPException(400,'Name must not be empty')
 if not out['login_url'].startswith(('http://','https://')) or not out['target_url'].startswith(('http://','https://')):raise HTTPException(400,'URL must start with http:// or https://')
 for key,default in _FLEET_SELECTORS.items():
  out[key]=str(d.get(key) or current.get(key) or default).strip()[:1000]
 return out
def _fleet_grafana_config(d,current=None):
 current=current or {}
 url=str(d.get('grafana_url',current.get('grafana_url',''))).strip().rstrip('/');tag=str(d.get('tag',current.get('tag',''))).strip()
 if not url.startswith(('http://','https://')):raise HTTPException(400,'Grafana URL must start with http:// or https://')
 if not tag:raise HTTPException(400,'Tag must not be empty')
 return {'grafana_url':url,'tag':tag,'kiosk':bool(d.get('kiosk',current.get('kiosk',True)))}

def _fleet_collections():
 # the dashboards of each Grafana collection, so CARACAL Fleet can show or freeze a single one
 out=[]
 for asset in rows("SELECT id,name,source FROM assets WHERE kind='grafana-tag' ORDER BY position,id"):
  item={'id':asset['id'],'name':asset['name'],'dashboards':[]}
  try:
   config=json.loads(asset.get('source') or '{}')
   for index,dashboard in enumerate(_grafana_cached(config.get('grafana_url',''),config.get('tag',''))[:500]):
    item['dashboards'].append({'id':int(asset['id'])*100000+index,'name':dashboard['title'],'source':dashboard['url']})
  except Exception as error:item['error']=str(getattr(error,'detail',error))[:300]
  out.append(item)
 return out
@app.get('/api/fleet/v1/snapshot')
def fleet_snapshot(req:Request):
 _fleet_auth(req);state=_cc2_read();state['player_online']=(time.time()-float(state.get('updated') or 0))<8
 player={k:state.get(k) for k in ('current_id','current_name','frozen','collection_frozen','collection_id','remaining','duration','updated','player_online')}
 requests={'reboot':int(state.get('reboot_request',0)),'restart_player':int(state.get('restart_player_request',0))}
 return {'api_version':2,'runtime':RUNTIME,'version':_caracal_version(),'requests':requests,'assets':rows('SELECT * FROM assets ORDER BY position,id'),'profiles':rows(_FLEET_PROFILE_COLUMNS+' ORDER BY name COLLATE NOCASE,id'),'player':player,'notifications':_fleet_notifications(),
  'admin':_fleet_admin_state(),'overlay':{'enabled':bool(state.get('overlay_enabled',True)),'size':int(state.get('overlay_size',16))},
  'collections':_fleet_collections(),'screenshot':_shot_info(),'player_settings':_player_settings(),'display':_display_info()}

@app.post('/api/fleet/v1/control')
async def fleet_control(req:Request):
 _fleet_auth(req);d=await req.json();action=str(d.get('action',''));item_id=d.get('item_id');collection_id=d.get('collection_id')
 if action not in ('show','freeze','show_collection','freeze_collection','unfreeze','next'):raise HTTPException(400,'Unknown action')
 if action in ('show','freeze','show_collection','freeze_collection'):
  expanded=expanded_player_playlist()
  if action in ('show','freeze'):
   try:item_id=int(item_id)
   except (TypeError,ValueError):raise HTTPException(400,'Select item')
   if not any(int(x['id'])==item_id for x in expanded):raise HTTPException(404,'Item unavailable')
  else:
   try:collection_id=int(collection_id)
   except (TypeError,ValueError):raise HTTPException(400,'Select collection')
   if not rows("SELECT id FROM assets WHERE id=? AND kind='grafana-tag'",(collection_id,)):raise HTTPException(404,'Collection unavailable')
   if not any(x.get('parent_id')==collection_id for x in expanded):raise HTTPException(404,'Collection has no available dashboards')
 # 'updated' is the player's liveness timestamp, so it is not touched here
 state=_cc2_read();state['v6_command_id']=int(state.get('v6_command_id',0))+1;state['v6_action']=action;state['v6_item_id']=item_id;state['v6_collection_id']=collection_id;_cc2_write(state)
 return {'ok':True,'command_id':state['v6_command_id']}

@app.post('/api/fleet/v1/assets/web')
async def fleet_add_web(req:Request):
 _fleet_auth(req);d=await req.json();name=str(d.get('name','')).strip();source=str(d.get('source','')).strip()
 if not name or not source.startswith(('http://','https://')):raise HTTPException(400,'Invalid name or URL')
 duration=_fleet_duration(d.get('duration',30));scale=_fleet_scale(d.get('scale',1));profile_id=_fleet_profile_id(d.get('auth_profile_id'));dark=_dark_value(d.get('dark'))
 c=con();q=c.execute('INSERT INTO assets(name,kind,source,duration,position,scale,auth_profile_id,dark) VALUES(?,?,?,?,?,?,?,?)',(name,'web',source,duration,_fleet_position(c),scale,profile_id,dark));c.commit();c.close()
 return {'ok':True,'id':q.lastrowid}

@app.post('/api/fleet/v1/assets/grafana-tag')
async def fleet_add_grafana_tag(req:Request):
 _fleet_auth(req);d=await req.json();name=str(d.get('name','')).strip()
 if not name:raise HTTPException(400,'Name must not be empty')
 config=_fleet_grafana_config(d);duration=_fleet_duration(d.get('duration',60));scale=_fleet_scale(d.get('scale',1));dark=_dark_value(d.get('dark'))
 c=con();q=c.execute('INSERT INTO assets(name,kind,source,duration,position,auth_profile_id,scale,dark) VALUES(?,?,?,?,?,NULL,?,?)',(name,'grafana-tag',json.dumps(config,ensure_ascii=False),duration,_fleet_position(c),scale,dark));c.commit();c.close()
 return {'ok':True,'id':q.lastrowid}

@app.post('/api/fleet/v1/assets/upload')
async def fleet_upload(req:Request):
 # authenticate before the multipart body is parsed
 _fleet_auth(req);form=await req.form();upload=form.get('file')
 if upload is None or not hasattr(upload,'read'):raise HTTPException(400,'File missing')
 ext=Path(upload.filename or '').suffix.lower();kind='image' if ext in _FLEET_IMAGE else 'video' if ext in _FLEET_VIDEO else None
 if not kind:raise HTTPException(400,'Unsupported file type')
 name=(str(form.get('name') or '').strip() or Path(upload.filename).stem)[:200];duration=_fleet_duration(form.get('duration') or 15)
 fn=secrets.token_hex(10)+ext;size=0
 try:
  with (MEDIA/fn).open('wb') as f:
   while chunk:=await upload.read(1048576):
    size+=len(chunk)
    if size>_FLEET_MEDIA_MAX:raise HTTPException(413,'File too large')
    f.write(chunk)
 except BaseException:
  (MEDIA/fn).unlink(missing_ok=True);raise
 c=con();q=c.execute('INSERT INTO assets(name,kind,source,duration,position) VALUES(?,?,?,?,?)',(name,kind,'/media/'+fn,duration,_fleet_position(c)));c.commit();c.close()
 return {'ok':True,'id':q.lastrowid,'kind':kind}

@app.get('/api/fleet/v1/assets/{asset_id}/file')
def fleet_asset_file(asset_id:int,req:Request):
 _fleet_auth(req);r=rows('SELECT * FROM assets WHERE id=?',(asset_id,))
 if not r or r[0]['kind'] not in ('image','video') or not str(r[0]['source']).startswith('/media/'):raise HTTPException(404,'No media file')
 path=MEDIA/Path(r[0]['source']).name
 if not path.is_file():raise HTTPException(404,'Media file missing')
 return FileResponse(path,filename=path.name)

@app.put('/api/fleet/v1/assets/{asset_id}')
async def fleet_update_asset(asset_id:int,req:Request):
 _fleet_auth(req);d=await req.json();r=rows('SELECT * FROM assets WHERE id=?',(asset_id,))
 if not r:raise HTTPException(404,'Item not found')
 a=r[0];name=str(d.get('name',a['name'])).strip() or a['name'];duration=_fleet_duration(d.get('duration',a['duration']));scale=_fleet_scale(d.get('scale',a.get('scale') or 1));source=a['source'];profile_id=a['auth_profile_id']
 if a['kind']=='web' and 'source' in d:
  source=str(d['source']).strip()
  if not source.startswith(('http://','https://')):raise HTTPException(400,'URL must start with http:// or https://')
 if a['kind']=='web' and 'auth_profile_id' in d:profile_id=_fleet_profile_id(d['auth_profile_id'])
 if a['kind']=='grafana-tag' and any(k in d for k in ('grafana_url','tag','kiosk')):
  try:current=json.loads(a['source'] or '{}')
  except ValueError:current={}
  source=json.dumps(_fleet_grafana_config(d,current),ensure_ascii=False)
 dark=_dark_value(d['dark']) if 'dark' in d and a['kind'] in ('web','grafana-tag') else a.get('dark')
 c=con();c.execute('UPDATE assets SET name=?,source=?,duration=?,scale=?,auth_profile_id=?,dark=? WHERE id=?',(name,source,duration,scale,profile_id,dark,asset_id));c.commit();c.close()
 return {'ok':True}

@app.delete('/api/fleet/v1/assets/{asset_id}')
def fleet_delete_asset(asset_id:int,req:Request):
 _fleet_auth(req);r=rows('SELECT source FROM assets WHERE id=?',(asset_id,));c=con();c.execute('DELETE FROM assets WHERE id=?',(asset_id,));c.commit();c.close()
 if r:_remove_media(r[0]['source'])
 return {'ok':True}

@app.put('/api/fleet/v1/playlist/reorder')
async def fleet_reorder(req:Request):
 _fleet_auth(req);d=await req.json()
 try:ids=[int(x) for x in (d.get('ids') or d.get('order') or [])]
 except (TypeError,ValueError):raise HTTPException(400,'Invalid item id')
 if sorted(ids)!=sorted(x['id'] for x in rows('SELECT id FROM assets')):raise HTTPException(409,'Playlist changed, send the complete order')
 c=con()
 for position,asset_id in enumerate(ids):c.execute('UPDATE assets SET position=? WHERE id=?',(position,asset_id))
 c.commit();c.close();return {'ok':True}

@app.post('/api/fleet/v1/profiles')
async def fleet_add_profile(req:Request):
 _fleet_auth(req);d=await req.json();f=_fleet_profile_fields(d);username=str(d.get('username') or '');password=str(d.get('password') or '')
 if not username.strip() or not password:raise HTTPException(400,'Username and password are required')
 c=con();q=c.execute('INSERT INTO auth_profiles(name,login_url,target_url,username_enc,password_enc,user_selector,pass_selector,submit_selector,auth_type) VALUES(?,?,?,?,?,?,?,?,?)',(f['name'],f['login_url'],f['target_url'],vault.encrypt(username.encode()),vault.encrypt(password.encode()),f['user_selector'],f['pass_selector'],f['submit_selector'],f['auth_type']));c.commit();c.close()
 return {'ok':True,'id':q.lastrowid}

@app.put('/api/fleet/v1/profiles/{profile_id}')
async def fleet_update_profile(profile_id:int,req:Request):
 # empty username or password = keep the stored one
 _fleet_auth(req);d=await req.json();r=rows('SELECT * FROM auth_profiles WHERE id=?',(profile_id,))
 if not r:raise HTTPException(404,'Login profile not found')
 f=_fleet_profile_fields(d,r[0]);username=str(d.get('username') or '');password=str(d.get('password') or '')
 ue=vault.encrypt(username.encode()) if username.strip() else r[0]['username_enc'];pe=vault.encrypt(password.encode()) if password else r[0]['password_enc']
 c=con();c.execute('UPDATE auth_profiles SET name=?,login_url=?,target_url=?,username_enc=?,password_enc=?,user_selector=?,pass_selector=?,submit_selector=?,auth_type=? WHERE id=?',(f['name'],f['login_url'],f['target_url'],ue,pe,f['user_selector'],f['pass_selector'],f['submit_selector'],f['auth_type'],profile_id));c.commit();c.close()
 return {'ok':True}

@app.delete('/api/fleet/v1/profiles/{profile_id}')
def fleet_delete_profile(profile_id:int,req:Request):
 # pages that used the profile stay in the playlist without automatic login
 _fleet_auth(req)
 if not rows('SELECT id FROM auth_profiles WHERE id=?',(profile_id,)):raise HTTPException(404,'Login profile not found')
 c=con();n=c.execute('UPDATE assets SET auth_profile_id=NULL WHERE auth_profile_id=?',(profile_id,)).rowcount;c.execute('DELETE FROM auth_profiles WHERE id=?',(profile_id,));c.commit();c.close()
 return {'ok':True,'unassigned':n}


# ------------------------------------------------------------------ notifications
# On-screen notifications. Apps send them to /api/notify/v1 with their own token (one token per app, revocable,
# rate limited). The app keeps a single queue and the overlay shows one notification at a time, asking
# GET /api/notify/overlay what to show. Queue rules: higher level first, the same 'key' replaces a waiting
# notification, a full queue drops the oldest of the lowest level, waiting notifications expire.
# See docs/NOTIFICATIONS.md.
_NTF_LOCK=threading.Lock()
_NTF_LEVELS={'info':1,'success':1,'warning':2,'critical':3}
_NTF_CRITICAL={'critical','crit','error','err','fatal','emergency','alert','disaster','high','highest','blocker','immediate','page','urgent','max','down','5'}
_NTF_WARNING={'warning','warn','average','degraded','major','4'}
_NTF_SUCCESS={'success','ok','resolved','up','good','recovered'}
_NTF_POSITIONS=('top-right','top-left','top','bottom-right','bottom-left','bottom','center')
_NTF_DEFAULTS={'enabled':'1','position':'top-right','duration':'8','max_queue':'20','scale':'100','sound':'off','volume':'70','sound_device':'','history_max':'500','history_days':'7','style':''}
# The look of notifications on the screen, drawn by the overlay with Tk: colours, an icon per level, the shape (a stripe,
# a solid colour or a border), the font, the width (100 = a banner across the screen), opacity, what is shown and the
# animation. Edited visually in the admin UI (and in CARACAL Fleet) by static/notify-style.js.
# With Pillow the overlay draws the notification as a picture (player/notify_render.py), which adds rounded corners,
# a gradient, see-through or blurred (glass) background, a shadow, a border colour, an uploaded picture and sizes.
_NTF_STYLE={'bg':'#111926','title':'#f8fafc','text':'#cbd5e1','muted':'#94a3b8','fill':'stripe','stripe':100,'font':'DejaVu Sans','bold':True,
 'opacity':100,'width':0,'align':'left','icon':True,'source':True,'waiting':True,'progress':True,'animation':'slide','speed':250,
 'radius':14,'bg2':'#111926','gradient':'none','bg_opacity':100,'blur':0,'shadow':40,'border_color':'','stripe_pos':'left',
 'image':False,'image_pos':'left','image_size':25,'image_align':'center','image_shape':'rounded','image_dim':45,'title_size':100,'text_size':100,'padding':100,'margin':100,
 'progress_pos':'bottom','progress_size':100,
 'levels':{'info':{'color':'#3b82f6','icon':'ℹ'},'success':{'color':'#22c55e','icon':'✓'},'warning':{'color':'#f59e0b','icon':'⚠'},'critical':{'color':'#ef4444','icon':'✖'}}}
_NTF_STYLE_CHOICES={'fill':('stripe','solid','border'),'font':('DejaVu Sans','DejaVu Serif','DejaVu Sans Mono'),'align':('left','center'),'animation':('slide','fade','none'),
 'gradient':('none','vertical','horizontal','diagonal'),'stripe_pos':('left','right','top','bottom'),'image_pos':('left','right','top','bottom','background'),'image_align':('top','center','bottom'),'image_shape':('square','rounded','circle'),'progress_pos':('bottom','top')}
_NTF_STYLE_RANGES={'stripe':(0,300),'opacity':(50,100),'width':(0,100),'speed':(100,1500),'radius':(0,60),'bg_opacity':(0,100),'blur':(0,100),'shadow':(0,100),
 'image_size':(10,60),'image_dim':(0,90),'title_size':(50,250),'text_size':(50,250),'padding':(50,250),'margin':(0,300),'progress_size':(50,400)}
def _ntf_style(d,base=None):
 # missing keys keep the base (the stored look); raises ValueError with what is wrong
 out=json.loads(json.dumps(base or _NTF_STYLE))
 if not isinstance(d,dict):raise ValueError('vzhled musí být objekt')
 color=lambda v:isinstance(v,str) and re.fullmatch(r'#[0-9a-fA-F]{6}',v)
 for k in ('bg','title','text','muted','bg2'):
  if k in d:
   if not color(d[k]):raise ValueError(f'{k}: barva ve tvaru #RRGGBB')
   out[k]=d[k].lower()
 if 'border_color' in d:   # '' = the colour of the level
  if d['border_color'] not in ('',None) and not color(d['border_color']):raise ValueError('border_color: barva ve tvaru #RRGGBB nebo prázdné')
  out['border_color']=(d['border_color'] or '').lower()
 for k,choices in _NTF_STYLE_CHOICES.items():
  if k in d:
   if d[k] not in choices:raise ValueError(f"{k}: {', '.join(choices)}")
   out[k]=d[k]
 for k,(lo,hi) in _NTF_STYLE_RANGES.items():
  if k in d:
   try:v=int(d[k])
   except (TypeError,ValueError):raise ValueError(f'{k}: číslo')
   if not lo<=v<=hi or (k=='width' and 0<v<20):raise ValueError(f'{k}: {lo}–{hi}'+(' (0 = automaticky, jinak 20–100)' if k=='width' else ''))
   out[k]=v
 for k in ('bold','icon','source','waiting','progress','image'):
  if k in d:out[k]=bool(d[k])
 levels=d.get('levels') or {}
 if not isinstance(levels,dict):raise ValueError('levels: objekt')
 for level,v in levels.items():
  if level not in out['levels'] or not isinstance(v,dict):raise ValueError('levels: info, success, warning, critical')
  if 'color' in v:
   if not color(v['color']):raise ValueError(f'{level}: barva ve tvaru #RRGGBB')
   out['levels'][level]['color']=v['color'].lower()
  if 'icon' in v:
   icon=str(v['icon'] or '').strip()
   # Tk on X11 cannot draw colour emoji (it can even crash on them), so icons stay in the Basic Multilingual Plane
   if len(icon)>3 or any(ord(ch)<32 or ord(ch)>0xFFFF or ord(ch)==0xFE0F for ch in icon):raise ValueError(f'{level}: ikona nejvýš 3 znaky bez barevných emoji')
   out['levels'][level]['icon']=icon
 return out
def _ntf_style_stored(raw):
 try:return _ntf_style(json.loads(raw) if raw else {})
 except (ValueError,TypeError):return _ntf_style({})
# sound: which levels play a chime by themselves - off (none), critical, warning (and critical), all.
# A notification sent with sound=true always plays; volume 0 mutes the screen completely.
_NTF_SOUNDS={'off':99,'critical':3,'warning':2,'all':1}
_NTF_TTL={1:900,2:1800,3:3600}   # seconds a notification may wait in the queue, by priority
_NTF_PER_SOURCE=10               # waiting notifications per token, so one chatty app cannot fill the queue
_NTF_BODY_MAX=65536;_NTF_BATCH_MAX=5
_NTF_EMPTY={'ok':True,'queued':0,'updated':0,'dropped':0,'ids':[]}
_ntf_hits={};_ntf_fails={}
c=con();c.executescript("""CREATE TABLE IF NOT EXISTS notify_tokens(id INTEGER PRIMARY KEY,name TEXT,token_hash TEXT UNIQUE,prefix TEXT,rate_per_min INTEGER DEFAULT 30,enabled INTEGER DEFAULT 1,created REAL,last_used REAL);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY,token_id INTEGER,source TEXT,title TEXT,message TEXT,level TEXT,priority INTEGER,key TEXT,duration INTEGER,created REAL,shown_at REAL,done INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS notifications_queue ON notifications(done,priority,id);
CREATE TABLE IF NOT EXISTS notify_settings(key TEXT PRIMARY KEY,value TEXT);""")
# notifications.sound: NULL = by the sound setting, 0 = silent, 1 = always
if 'sound' not in [r[1] for r in c.execute('PRAGMA table_info(notifications)').fetchall()]:c.execute('ALTER TABLE notifications ADD COLUMN sound INTEGER')
# notifications.look: JSON with the picture of this one notification ({"image": false} or a place and size), NULL = the look
if 'look' not in [r[1] for r in c.execute('PRAGMA table_info(notifications)').fetchall()]:c.execute('ALTER TABLE notifications ADD COLUMN look TEXT')
c.commit();c.close()
# notifications.done: 0 waiting or on screen, 1 shown, 2 dropped or expired, 3 removed by the administrator

def _ntf_hash(token):return hashlib.sha256(token.encode()).hexdigest()
def _ntf_settings():
 s=dict(_NTF_DEFAULTS);s.update({r['key']:r['value'] for r in rows('SELECT key,value FROM notify_settings')})
 return {'enabled':s['enabled']=='1','position':s['position'] if s['position'] in _NTF_POSITIONS else 'top-right','duration':int(s['duration']),'max_queue':int(s['max_queue']),'scale':int(s['scale']),'sound':s['sound'] if s['sound'] in _NTF_SOUNDS else 'off','volume':int(s['volume']),'sound_device':s['sound_device'],'history_max':int(s['history_max']),'history_days':int(s['history_days']),'style':_ntf_style_stored(s['style'])}
def _ntf_text(value,limit):
 text=re.sub(r'[\x00-\x08\x0b-\x1f\x7f]','',str(value if value is not None else '')).strip()
 text=re.sub(r'\n{3,}','\n\n',text)
 return text[:limit-1]+'…' if len(text)>limit else text
def _ntf_level(value,default='info'):
 v=str(value if value is not None else '').strip().lower()
 if v in _NTF_CRITICAL:return 'critical'
 if v in _NTF_WARNING:return 'warning'
 if v in _NTF_SUCCESS:return 'success'
 return 'info' if v in ('info','information','notice','low','min','default','1','2','3') else default
def _ntf_picture(d,headers=None):
 # per-notification picture: image = none | left | right | top | bottom | background (or true/false), image_size 10-60 %;
 # None when the notification does not say (the look decides)
 headers=headers or {}
 value=d.get('image') if 'image' in d else headers.get('x-image')
 size=d.get('image_size') if 'image_size' in d else headers.get('x-image-size')
 if value in (None,'') and size in (None,''):return None
 out={}
 if value not in (None,''):
  v=str(value).strip().lower()
  if isinstance(value,bool) or v in ('true','false','1','0','yes','no','on','off','default'):
   if v!='default':out['image']=v in ('true','1','yes','on')
  elif v in ('none','hide','hidden'):out['image']=False
  elif v in _NTF_STYLE_CHOICES['image_pos']:out.update(image=True,image_pos=v)
  else:raise HTTPException(400,'image: none, left, right, top, bottom or background')
 if size not in (None,''):
  try:n=int(float(size))
  except (TypeError,ValueError):raise HTTPException(400,'image_size: 10 to 60')
  lo,hi=_NTF_STYLE_RANGES['image_size']
  if not lo<=n<=hi:raise HTTPException(400,f'image_size: {lo} to {hi}')
  out['image_size']=n
 return json.dumps(out) if out else None
def _ntf_sound(value):
 # per-notification override: true / false, or None for the screen's sound setting
 if value is None or value=='':return None
 if isinstance(value,bool):return int(value)
 v=str(value).strip().lower()
 if v in ('1','true','yes','on','ano'):return 1
 if v in ('0','false','no','off','ne','none','silent'):return 0
 return None
def _ntf_duration(value):
 if value in (None,''):return None
 try:return max(3,min(120,int(float(value))))
 except (TypeError,ValueError):raise HTTPException(400,'Invalid duration')

def _ntf_item(d,headers=None):
 # generic JSON / form / plain text notification; the field aliases cover most webhook senders
 headers=headers or {}
 title=_ntf_text(d.get('title') or d.get('subject') or d.get('summary') or headers.get('title') or headers.get('x-title'),120)
 message=_ntf_text(next((d[k] for k in ('message','text','body','msg','description','content') if d.get(k) not in (None,'')),''),600)
 if not title and not message:raise HTTPException(400,'Notification needs a title or a message')
 level=_ntf_level(d.get('level') or d.get('severity') or d.get('priority') or d.get('type') or headers.get('x-level') or headers.get('priority') or headers.get('x-priority'))
 return {'title':title,'message':message,'level':level,'key':_ntf_text(d.get('key') or d.get('dedup_key') or d.get('tag') or headers.get('x-key'),200) or None,'duration':_ntf_duration(d.get('duration') or headers.get('x-duration')),'source':_ntf_text(d.get('source') or d.get('app'),60) or None,'sound':_ntf_sound(d['sound'] if 'sound' in d else headers.get('x-sound')),'look':_ntf_picture(d,headers)}

def _ntf_from_alerts(p):
 # Grafana alerting and Prometheus Alertmanager webhooks
 out=[]
 for a in [x for x in p['alerts'] if isinstance(x,dict)]:
  labels=a.get('labels') or {};ann=a.get('annotations') or {};resolved=str(a.get('status') or p.get('status'))=='resolved'
  name=str(labels.get('alertname') or p.get('title') or 'Alert');where=str(labels.get('instance') or labels.get('host') or labels.get('job') or '')
  message=str(ann.get('summary') or ann.get('description') or ann.get('message') or '')
  out.append({'title':_ntf_text(name,120),'message':_ntf_text((where+' · ' if where and where not in message else '')+message,600),'level':'success' if resolved else _ntf_level(labels.get('severity'),'warning'),'key':'alert:'+str(a.get('fingerprint') or name+where)[:180],'duration':None,'source':None,'sound':None})
 if len(out)>_NTF_BATCH_MAX:
  # a large alert group becomes one notification instead of a flood
  worst=max(out,key=lambda x:_NTF_LEVELS[x['level']])['level'];names=sorted({x['title'] for x in out})
  return [{'title':_ntf_text(p.get('title') or f'{len(out)} alerts',120),'message':_ntf_text(', '.join(names[:12])+(' …' if len(names)>12 else ''),600),'level':worst,'key':'alertgroup:'+str(p.get('groupKey') or '')[:180],'duration':None,'source':None,'sound':None}]
 return out

def _ntf_parse(payload,headers):
 if isinstance(payload,dict) and isinstance(payload.get('alerts'),list):return _ntf_from_alerts(payload)
 if isinstance(payload,dict) and isinstance(payload.get('heartbeat'),dict):
  # Uptime Kuma: heartbeat.status 1 = up, 0 = down
  hb=payload['heartbeat'];mon=payload.get('monitor') or {};name=mon.get('name') or 'Monitor';up=hb.get('status')==1
  return [{'title':_ntf_text(f"{name}: {'UP' if up else 'DOWN'}",120),'message':_ntf_text(hb.get('msg'),600),'level':'success' if up else 'critical','key':'kuma:'+str(mon.get('id') or name),'duration':None,'source':None,'sound':None}]
 batch=payload.get('notifications') if isinstance(payload,dict) and isinstance(payload.get('notifications'),list) else payload if isinstance(payload,list) else None
 if batch is not None:
  if not batch or len(batch)>_NTF_BATCH_MAX or not all(isinstance(x,dict) for x in batch):raise HTTPException(400,f'Send 1 to {_NTF_BATCH_MAX} notifications at once')
  return [_ntf_item(x) for x in batch]
 if isinstance(payload,dict):return [_ntf_item(payload,headers)]
 raise HTTPException(400,'Unsupported notification format')

_NTF_AUDIT_MAX=2000;_NTF_AUDIT_DAYS=90;_ntf_cleaned=[0.0]
c=con();c.execute('CREATE TABLE IF NOT EXISTS notify_audit(id INTEGER PRIMARY KEY,ts REAL,actor TEXT,ip TEXT,action TEXT,detail TEXT)');c.commit();c.close()
def _ntf_audit(req,actor,action,detail=''):
 # who did what with notifications: administrators' changes and refused senders; never tokens or secrets
 name=actor.get('username') if isinstance(actor,dict) else str(actor or '')
 ip=req.client.host if req is not None and req.client else ''
 c=con();c.execute('INSERT INTO notify_audit(ts,actor,ip,action,detail) VALUES(?,?,?,?,?)',(time.time(),name,ip,action,_ntf_text(detail,500)));c.commit();c.close()
def _ntf_expire(c,now):
 for priority,ttl in _NTF_TTL.items():c.execute('UPDATE notifications SET done=2 WHERE done=0 AND shown_at IS NULL AND priority=? AND created<?',(priority,now-ttl))
 # history (shown, dropped, removed) and the audit log are limited by age and count, so the SD card stays small;
 # trimmed once a minute rather than on every overlay poll
 if now-_ntf_cleaned[0]<60:return
 _ntf_cleaned[0]=now;s=_ntf_settings()
 c.execute('DELETE FROM notifications WHERE done>0 AND created<?',(now-s['history_days']*86400,))
 c.execute('DELETE FROM notifications WHERE done>0 AND id<=(SELECT id FROM notifications WHERE done>0 ORDER BY id DESC LIMIT 1 OFFSET ?)',(s['history_max'],))
 c.execute('DELETE FROM notify_audit WHERE ts<?',(now-_NTF_AUDIT_DAYS*86400,))
 c.execute('DELETE FROM notify_audit WHERE id<=(SELECT id FROM notify_audit ORDER BY id DESC LIMIT 1 OFFSET ?)',(_NTF_AUDIT_MAX,))
 # in-memory counters of addresses and tokens that were not used for a while (copied: requests change them)
 for store,age in ((_ntf_fails,300),(_ntf_hits,60),(_ntf_rate_logged,60)):
  for key,value in list(store.items()):
   last=value[-1] if isinstance(value,list) and value else value if isinstance(value,float) else 0
   if last<now-age:store.pop(key,None)
def _ntf_evict(c,priority,token_id=None):
 # drops the oldest waiting notification of the lowest level, but never one more important than the new one
 own=' AND COALESCE(token_id,0)=?' if token_id is not None else '';args=(token_id,) if token_id is not None else ()
 row=c.execute('SELECT id FROM notifications WHERE done=0 AND shown_at IS NULL'+own+' AND priority<=? ORDER BY priority,id LIMIT 1',(*args,priority)).fetchone()
 if row:c.execute('UPDATE notifications SET done=2 WHERE id=?',(row['id'],))
 return bool(row)
def _ntf_enqueue(token_id,source,items):
 s=_ntf_settings()
 if not s['enabled']:raise HTTPException(503,'Notifications are disabled on this screen')
 now=time.time();result=dict(_NTF_EMPTY,ids=[])
 with _NTF_LOCK:
  c=con()
  try:
   _ntf_expire(c,now)
   for it in items:
    priority=_NTF_LEVELS[it['level']];src=it.get('source') or source
    duration=it['duration'] or (max(15,s['duration']) if priority==3 else s['duration'])
    if it['key']:
     row=c.execute('SELECT id FROM notifications WHERE done=0 AND key=? AND COALESCE(token_id,0)=? ORDER BY id DESC LIMIT 1',(it['key'],token_id or 0)).fetchone()
     if row:
      c.execute('UPDATE notifications SET title=?,message=?,level=?,priority=?,source=?,duration=MAX(duration,?),look=? WHERE id=?',(it['title'],it['message'],it['level'],priority,src,duration,it.get('look'),row['id']))
      result['updated']+=1;result['ids'].append(row['id']);continue
    own=c.execute('SELECT COUNT(*) FROM notifications WHERE done=0 AND shown_at IS NULL AND COALESCE(token_id,0)=?',(token_id or 0,)).fetchone()[0]
    if own>=_NTF_PER_SOURCE and not _ntf_evict(c,priority,token_id or 0):result['dropped']+=1;continue
    # the loop also trims a queue that is longer than a newly lowered limit
    waiting=lambda:c.execute('SELECT COUNT(*) FROM notifications WHERE done=0 AND shown_at IS NULL').fetchone()[0]
    while waiting()>=s['max_queue'] and _ntf_evict(c,priority):pass
    if waiting()>=s['max_queue']:result['dropped']+=1;continue
    q=c.execute('INSERT INTO notifications(token_id,source,title,message,level,priority,key,duration,created,sound,look) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(token_id,src,it['title'],it['message'],it['level'],priority,it['key'],duration,now,it.get('sound'),it.get('look')))
    result['queued']+=1;result['ids'].append(q.lastrowid)
   c.commit()
  finally:c.close()
 return result

def _ntf_token(req:Request):
 ip=req.client.host if req.client else '';now=time.time();recent=[t for t in _ntf_fails.get(ip,[]) if t>now-300]
 if len(recent)>=20:raise HTTPException(429,'Too many invalid tokens, try again in 5 minutes')
 header=req.headers.get('authorization','');token=''
 if header.lower().startswith('bearer '):token=header[7:].strip()
 elif header.lower().startswith('basic '):
  # Basic auth for senders that only support it: any user name, the token as the password
  try:token=base64.b64decode(header[6:].strip()).decode().partition(':')[2].strip()
  except Exception:token=''
 token=token or req.headers.get('x-caracal-token','').strip() or req.query_params.get('token','').strip()
 r=rows('SELECT * FROM notify_tokens WHERE token_hash=?',(_ntf_hash(token),)) if token else []
 if not r or not r[0]['enabled']:
  # logged for the first refusal from an address and when it gets blocked, not for every attempt
  if not recent or len(recent)==19:_ntf_audit(req,'','Odmítnutý token' if recent==[] else 'Adresa zablokována na 5 minut','chybí token' if not token else 'neplatný nebo zakázaný token')
  _ntf_fails[ip]=recent+[now];raise HTTPException(401,'Invalid or missing notification token',headers={'WWW-Authenticate':'Bearer'})
 _ntf_fails.pop(ip,None);return r[0]
_ntf_rate_logged={}
def _ntf_rate(key,limit,label='',req=None):
 now=time.time();hits=[t for t in _ntf_hits.get(key,[]) if t>now-60]
 if len(hits)>=limit:
  _ntf_hits[key]=hits
  if now-_ntf_rate_logged.get(key,0.0)>60:_ntf_rate_logged[key]=now;_ntf_audit(req,label,'Překročen limit oznámení',f'{limit} za minutu')
  raise HTTPException(429,'Too many notifications, slow down',headers={'Retry-After':str(int(60-(now-hits[0]))+1)})
 _ntf_hits[key]=hits+[now]
async def _ntf_payload(req:Request):
 try:length=int(req.headers.get('content-length') or 0)
 except ValueError:length=0
 if length>_NTF_BODY_MAX:raise HTTPException(413,'Notification too large')
 body=await req.body()
 if len(body)>_NTF_BODY_MAX:raise HTTPException(413,'Notification too large')
 ctype=req.headers.get('content-type','').lower();text=body.decode('utf-8',errors='replace').strip()
 if 'json' in ctype or text[:1] in ('{','['):
  try:return json.loads(text)
  except ValueError:raise HTTPException(400,'Invalid JSON')
 if 'x-www-form-urlencoded' in ctype:return {k:v[-1] for k,v in urllib.parse.parse_qs(text).items() if k!='token'}
 # plain text body (curl -d, ntfy style): title, level etc. from headers or the query string
 d={k:v for k,v in req.query_params.items() if k!='token'};d['message']=text or d.get('message','');return d
async def _ntf_receive(req:Request,token_id,source):
 items=_ntf_parse(await _ntf_payload(req),{k.lower():v for k,v in req.headers.items()})
 return _ntf_enqueue(token_id,source,items) if items else dict(_NTF_EMPTY,ids=[])

@app.post('/api/notify/v1')
async def notify_send(req:Request):
 token=_ntf_token(req);_ntf_rate(token['token_hash'],int(token['rate_per_min'] or 30),token['name'],req)
 c=con();c.execute('UPDATE notify_tokens SET last_used=? WHERE id=?',(time.time(),token['id']));c.commit();c.close()
 return await _ntf_receive(req,token['id'],token['name'])

@app.get('/api/notify/overlay')
def notify_overlay(req:Request):
 # polled by the overlay about once a second; this is also what moves the queue forward
 _local_only(req);s=_ntf_settings();now=time.time()
 with _NTF_LOCK:
  c=con()
  try:
   _ntf_expire(c,now)
   cur=c.execute('SELECT * FROM notifications WHERE done=0 AND shown_at IS NOT NULL ORDER BY shown_at LIMIT 1').fetchone()
   if cur and now>=cur['shown_at']+cur['duration']:c.execute('UPDATE notifications SET done=1 WHERE id=?',(cur['id'],));cur=None
   if not cur and s['enabled']:
    cur=c.execute('SELECT * FROM notifications WHERE done=0 AND shown_at IS NULL ORDER BY priority DESC,id LIMIT 1').fetchone()
    if cur:c.execute('UPDATE notifications SET shown_at=? WHERE id=?',(now,cur['id']));cur=dict(cur);cur['shown_at']=now
   waiting=c.execute('SELECT COUNT(*) FROM notifications WHERE done=0 AND shown_at IS NULL').fetchone()[0];c.commit()
  finally:c.close()
 current=None
 if cur and s['enabled']:
  current={k:cur[k] for k in ('id','title','message','level','source','duration')};current['remaining']=max(0.0,cur['shown_at']+cur['duration']-now)
  try:current['look']=json.loads(cur['look']) if cur['look'] else {}
  except (ValueError,IndexError,KeyError):current['look']={}
  current['sound']=cur['sound']==1 or (cur['sound']!=0 and (cur['priority'] or 1)>=_NTF_SOUNDS[s['sound']])
 return {'enabled':s['enabled'],'position':s['position'],'scale':s['scale'],'volume':s['volume'],'sound_device':s['sound_device'],'sounds':{k:v['sha256'][:16] for k,v in _snd_meta().items()},'style':s['style'],'current':current,'waiting':waiting,
  'image':(_img_meta().get('sha256') or '')[:16],'screenshot':_shot_pending()}

# administration
@app.get('/api/notify/settings')
def notify_settings(u=Depends(auth)):return _ntf_settings()
@app.put('/api/notify/settings')
async def notify_settings_save(req:Request,u=Depends(auth)):return _ntf_save_settings(await req.json(),req,u)
def _ntf_save_settings(d,req,actor):
 # missing keys keep their value, so CARACAL Fleet can change only some of them
 if not isinstance(d,dict):raise HTTPException(400,'Neplatné nastavení')
 s=_ntf_settings()
 try:
  s['enabled']=bool(d.get('enabled',s['enabled']));s['position']=str(d.get('position',s['position']))
  s['duration']=int(d.get('duration',s['duration']));s['max_queue']=int(d.get('max_queue',s['max_queue']));s['scale']=int(d.get('scale',s['scale']))
  s['sound']=str(d.get('sound',s['sound']));s['volume']=int(d.get('volume',s['volume']));s['sound_device']=str(d.get('sound_device',s['sound_device']) or '').strip()
  s['history_max']=int(d.get('history_max',s['history_max']));s['history_days']=int(d.get('history_days',s['history_days']))
 except (TypeError,ValueError):raise HTTPException(400,'Neplatné nastavení')
 if 'style' in d:
  try:s['style']=_ntf_style(d['style'],s['style'])
  except ValueError as e:raise HTTPException(400,f'Neplatný vzhled oznámení – {e}')
 if s['position'] not in _NTF_POSITIONS:raise HTTPException(400,'Neplatná pozice')
 if not 3<=s['duration']<=120:raise HTTPException(400,'Doba zobrazení musí být 3 až 120 s')
 if not 1<=s['max_queue']<=200:raise HTTPException(400,'Fronta musí mít 1 až 200 míst')
 if not 50<=s['scale']<=300:raise HTTPException(400,'Velikost musí být 50 až 300 %')
 if s['sound'] not in _NTF_SOUNDS:raise HTTPException(400,'Neplatné nastavení zvuku')
 if not 0<=s['volume']<=100:raise HTTPException(400,'Hlasitost musí být 0 až 100 %')
 # ALSA device name passed to aplay -D, e.g. default, hdmi:CARD=vc4hdmi0,DEV=0, plughw:1,0
 if not re.fullmatch(r'[A-Za-z0-9:=,._-]{0,100}',s['sound_device']):raise HTTPException(400,'Neplatný název zvukového zařízení')
 if not 50<=s['history_max']<=5000:raise HTTPException(400,'Historie musí mít 50 až 5000 záznamů')
 if not 1<=s['history_days']<=90:raise HTTPException(400,'Historii jde držet 1 až 90 dní')
 before=_ntf_settings();c=con()
 for k in _NTF_DEFAULTS:c.execute('INSERT OR REPLACE INTO notify_settings(key,value) VALUES(?,?)',(k,('1' if s[k] else '0') if k=='enabled' else json.dumps(s[k],ensure_ascii=False) if k=='style' else str(s[k])))
 c.commit();c.close()
 changes=', '.join('vzhled oznámení' if k=='style' else f'{k}: {before[k]} → {s[k]}' for k in _NTF_DEFAULTS if before[k]!=s[k])
 if changes:_ntf_audit(req,actor,'Nastavení změněno',changes)
 return s
@app.get('/api/notify/tokens')
def notify_tokens(u=Depends(auth)):return rows('SELECT id,name,prefix,rate_per_min,enabled,created,last_used FROM notify_tokens ORDER BY name COLLATE NOCASE,id')
@app.post('/api/notify/tokens')
async def notify_token_create(req:Request,u=Depends(auth)):
 d=await req.json();name=_ntf_text(d.get('name'),60)
 if not name:raise HTTPException(400,'Vyplň název aplikace')
 try:rate=max(1,min(600,int(d.get('rate_per_min') or 30)))
 except (TypeError,ValueError):raise HTTPException(400,'Neplatný limit')
 token='crc_'+secrets.token_urlsafe(32)
 c=con();q=c.execute('INSERT INTO notify_tokens(name,token_hash,prefix,rate_per_min,enabled,created) VALUES(?,?,?,?,1,?)',(name,_ntf_hash(token),token[:10],rate,time.time()));c.commit();c.close()
 _ntf_audit(req,u,'Token vytvořen',f'{name} ({token[:10]}…), {rate}/min')
 # the token is returned only now; the node keeps just its hash
 return {'ok':True,'id':q.lastrowid,'token':token}
@app.put('/api/notify/tokens/{token_id}')
async def notify_token_update(token_id:int,req:Request,u=Depends(auth)):
 d=await req.json();r=rows('SELECT * FROM notify_tokens WHERE id=?',(token_id,))
 if not r:raise HTTPException(404,'Token nebyl nalezen')
 name=_ntf_text(d.get('name',r[0]['name']),60) or r[0]['name']
 try:rate=max(1,min(600,int(d.get('rate_per_min',r[0]['rate_per_min']))))
 except (TypeError,ValueError):raise HTTPException(400,'Neplatný limit')
 enabled=1 if d.get('enabled',r[0]['enabled']) else 0
 c=con();c.execute('UPDATE notify_tokens SET name=?,rate_per_min=?,enabled=? WHERE id=?',(name,rate,enabled,token_id));c.commit();c.close()
 _ntf_audit(req,u,'Token '+('povolen' if enabled and not r[0]['enabled'] else 'zakázán' if r[0]['enabled'] and not enabled else 'upraven'),f"{name} ({r[0]['prefix']}…), {rate}/min")
 return {'ok':True}
@app.delete('/api/notify/tokens/{token_id}')
def notify_token_delete(token_id:int,req:Request,u=Depends(auth)):
 r=rows('SELECT name,prefix FROM notify_tokens WHERE id=?',(token_id,))
 c=con();c.execute('DELETE FROM notify_tokens WHERE id=?',(token_id,));c.commit();c.close()
 if r:_ntf_audit(req,u,'Token smazán',f"{r[0]['name']} ({r[0]['prefix']}…)")
 return {'ok':True}
@app.get('/api/notify/queue')
def notify_queue(u=Depends(auth)):
 now=time.time();columns='id,source,title,message,level,priority,duration,created,shown_at,done'
 current=rows(f'SELECT {columns} FROM notifications WHERE done=0 AND shown_at IS NOT NULL AND shown_at+duration>? LIMIT 1',(now,))
 return {'now':now,'current':current[0] if current else None,
  'waiting':rows(f'SELECT {columns} FROM notifications WHERE done=0 AND shown_at IS NULL ORDER BY priority DESC,id LIMIT 200'),
  'history':rows(f'SELECT {columns} FROM notifications WHERE done>0 ORDER BY id DESC LIMIT 20'),
  'history_count':rows('SELECT COUNT(*) AS n FROM notifications WHERE done>0')[0]['n']}
@app.post('/api/notify/test')
async def notify_test(req:Request,u=Depends(auth)):
 d=await req.json();result=_ntf_enqueue(None,'CARACAL',[_ntf_item(d)]);_ntf_audit(req,u,'Testovací oznámení',str(d.get('title') or d.get('message') or ''));return result
@app.post('/api/notify/skip')
def notify_skip(req:Request,u=Depends(auth)):
 with _NTF_LOCK:c=con();n=c.execute('UPDATE notifications SET done=3 WHERE done=0 AND shown_at IS NOT NULL').rowcount;c.commit();c.close()
 if n:_ntf_audit(req,u,'Oznámení přeskočeno')
 return {'ok':True}
@app.post('/api/notify/clear')
def notify_clear(req:Request,u=Depends(auth)):return _ntf_clear(req,u)
def _ntf_clear(req,actor):
 with _NTF_LOCK:c=con();n=c.execute('UPDATE notifications SET done=3 WHERE done=0').rowcount;c.commit();c.close()
 _ntf_audit(req,actor,'Fronta vyprázdněna',f'{n} oznámení')
 return {'ok':True,'cleared':n}
@app.delete('/api/notify/queue/{notification_id}')
def notify_remove(notification_id:int,req:Request,u=Depends(auth)):
 with _NTF_LOCK:c=con();n=c.execute('UPDATE notifications SET done=3 WHERE id=? AND done=0',(notification_id,)).rowcount;c.commit();c.close()
 if n:_ntf_audit(req,u,'Oznámení odebráno z fronty',f'#{notification_id}')
 return {'ok':True}
@app.post('/api/notify/history/clear')
def notify_history_clear(req:Request,u=Depends(auth)):
 with _NTF_LOCK:c=con();n=c.execute('DELETE FROM notifications WHERE done>0').rowcount;c.commit();c.close()
 _ntf_audit(req,u,'Historie oznámení smazána',f'{n} záznamů')
 return {'ok':True,'deleted':n}
@app.get('/api/notify/audit')
def notify_audit_log(limit:int=200,u=Depends(auth)):
 return {'count':rows('SELECT COUNT(*) AS n FROM notify_audit')[0]['n'],'max':_NTF_AUDIT_MAX,'days':_NTF_AUDIT_DAYS,'entries':rows('SELECT * FROM notify_audit ORDER BY id DESC LIMIT ?',(max(1,min(_NTF_AUDIT_MAX,limit)),))}
@app.post('/api/notify/audit/clear')
def notify_audit_clear(req:Request,u=Depends(auth)):
 # the clearing itself stays in the log, so it is visible who removed the rest
 c=con();n=c.execute('DELETE FROM notify_audit').rowcount;c.commit();c.close()
 _ntf_audit(req,u,'Audit log smazán',f'{n} záznamů');return {'ok':True,'deleted':n}

@app.post('/api/fleet/v1/notify')
async def fleet_notify(req:Request):
 # same formats as /api/notify/v1, authenticated with the Fleet key
 _fleet_auth(req);_ntf_rate('fleet',120,'CARACAL Fleet',req);return await _ntf_receive(req,0,'CARACAL Fleet')


# ------------------------------------------------------------------ notification watchers
# Watchers: the node itself asks another app's REST API (new tickets, issues, orders, ...) every few seconds and
# puts a notification into the queue for every item whose ID it has not seen yet. The first check only remembers
# what is already there. Credentials are encrypted with the vault key and never returned by the API.
_WCH_LOCK=threading.Lock()
_WCH_AUTH=('none','bearer','basic','header','oauth2')
_WCH_GRANTS=('client_credentials','password','refresh_token')
_WCH_SECRETS=('username','secret','client_secret','refresh_token')   # stored encrypted in credentials_enc
_WCH_RESPONSE_MAX=5*1024**2;_WCH_SEEN_MAX=5000
c=con();c.executescript("""CREATE TABLE IF NOT EXISTS notify_watchers(id INTEGER PRIMARY KEY,name TEXT,url TEXT,auth_type TEXT DEFAULT 'none',auth_header TEXT,credentials_enc BLOB,list_path TEXT,id_field TEXT,title_template TEXT,message_template TEXT,level TEXT DEFAULT 'info',level_field TEXT,interval INTEGER DEFAULT 60,verify_tls INTEGER DEFAULT 1,enabled INTEGER DEFAULT 1,
seen TEXT DEFAULT '[]',initialized INTEGER DEFAULT 0,revision INTEGER DEFAULT 0,last_check REAL,last_error TEXT,last_count INTEGER,last_new INTEGER,created REAL);""")
_wch_existing=[r[1] for r in c.execute('PRAGMA table_info(notify_watchers)').fetchall()]
for _column in ('oauth_token_url','oauth_grant','oauth_client_id','oauth_scope','oauth_extra','oauth_client_auth'):
 if _column not in _wch_existing:c.execute(f'ALTER TABLE notify_watchers ADD COLUMN {_column} TEXT')
c.commit();c.close()
_WCH_COLUMNS='id,name,url,auth_type,auth_header,list_path,id_field,title_template,message_template,level,level_field,interval,verify_tls,enabled,initialized,last_check,last_error,last_count,last_new,oauth_token_url,oauth_grant,oauth_client_id,oauth_scope,oauth_extra,oauth_client_auth,credentials_enc IS NOT NULL AS has_credentials'

def _wch_get(obj,path):
 # dot path into JSON: "data.tickets", "fields.summary", "items.0.id"
 for part in [x for x in str(path or '').split('.') if x]:
  if isinstance(obj,dict):obj=obj.get(part)
  elif isinstance(obj,list) and part.lstrip('-').isdigit():
   try:obj=obj[int(part)]
   except IndexError:return None
  else:return None
 return obj
def _wch_render(template,item):
 def value(match):
  v=_wch_get(item,match.group(1).strip())
  return '' if v is None else json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else str(v)
 return re.sub(r'\{([^{}]+)\}',value,str(template or ''))
def _wch_credentials(row):
 try:return json.loads(vault.decrypt(row['credentials_enc']).decode()) if row and row['credentials_enc'] else {}
 except Exception:return {}

def _wch_error_text(error):
 # the most useful part of an error response: RFC 6749 fields, common JSON shapes, or the text of a page
 try:raw=error.read(65536).decode('utf-8','replace')
 except Exception:return ''
 try:
  d=json.loads(raw)
  if isinstance(d,dict):
   nested=d.get('error') if isinstance(d.get('error'),dict) else {}
   parts=[d.get('error') if isinstance(d.get('error'),str) else None,d.get('error_description'),nested.get('message'),d.get('message'),d.get('detail'),d.get('title')]
   text=' – '.join(dict.fromkeys(str(x) for x in parts if x))
   if text:return text[:300]
 except ValueError:pass
 text=re.sub(r'\s+',' ',re.sub(r'<(script|style)[^>]*>.*?</\1>|<[^>]+>',' ',raw,flags=re.S|re.I)).strip()
 return text[:300]

# OAuth2 access tokens, kept in memory until shortly before they expire: (watcher id, revision) -> (token, valid until)
_wch_tokens={}
def _wch_oauth_token(w,creds,watcher_id=None,fresh=False):
 cache=(watcher_id,w.get('revision')) if watcher_id and w.get('revision') is not None else None
 hit=_wch_tokens.get(cache) if cache and not fresh else None
 if hit and hit[1]>time.time():return hit[0]
 grant=w['oauth_grant'] or 'client_credentials';data={'grant_type':grant}
 if grant=='password':data.update(username=creds.get('username') or '',password=creds.get('secret') or '')
 elif grant=='refresh_token':
  if not creds.get('refresh_token'):raise ValueError('OAuth2: chybí refresh token')
  data['refresh_token']=creds['refresh_token']
 if w['oauth_scope']:data['scope']=w['oauth_scope']
 for key,value in urllib.parse.parse_qsl(w['oauth_extra'] or ''):data.setdefault(key,value)
 headers={'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded','User-Agent':'CARACAL/1.0'}
 client_id=w['oauth_client_id'] or '';client_secret=creds.get('client_secret') or ''
 if w['oauth_client_auth']=='basic':
  q=lambda x:urllib.parse.quote(x,safe='')
  headers['Authorization']='Basic '+base64.b64encode(f'{q(client_id)}:{q(client_secret)}'.encode()).decode()
 else:
  if client_id:data['client_id']=client_id
  if client_secret:data['client_secret']=client_secret
 context=None if w['verify_tls'] else ssl._create_unverified_context()
 try:
  request=urllib.request.Request(w['oauth_token_url'],data=urllib.parse.urlencode(data).encode(),headers=headers,method='POST')
  with urllib.request.urlopen(request,timeout=20,context=context) as response:body=response.read(1024**2)
 except urllib.request.HTTPError as error:
  # e.g. "invalid_client – Client authentication failed"; servers that do not follow RFC 6749 get their text shown
  sent=', '.join(sorted(data))+(', Basic' if 'Authorization' in headers else '')
  raise ValueError(f'OAuth2 token: HTTP {error.code} {_wch_error_text(error) or error.reason} (odesláno: {sent})')
 except Exception as error:raise ValueError(f'OAuth2 token nedostupný: {getattr(error,"reason",None) or error}')
 try:answer=json.loads(body.decode('utf-8','replace'));token=str(answer['access_token'])
 except (ValueError,KeyError,TypeError):raise ValueError('OAuth2: odpověď neobsahuje access_token')
 try:expires=max(30,int(float(answer.get('expires_in') or 3600)))
 except (TypeError,ValueError):expires=3600
 if cache:_wch_tokens[cache]=(token,time.time()+expires-max(10,min(60,expires//10)))
 rotated=answer.get('refresh_token')
 if grant=='refresh_token' and rotated and rotated!=creds.get('refresh_token'):
  # the server replaced the refresh token: store the new one, the old one may no longer work
  creds['refresh_token']=rotated
  if watcher_id:
   r=rows('SELECT credentials_enc FROM notify_watchers WHERE id=?',(watcher_id,))
   if r:
    stored=_wch_credentials(r[0]);stored['refresh_token']=rotated
    c=con();c.execute('UPDATE notify_watchers SET credentials_enc=? WHERE id=?',(vault.encrypt(json.dumps(stored).encode()),watcher_id));c.commit();c.close()
 return token

def _wch_fetch(w,creds,watcher_id=None):
 context=None if w['verify_tls'] else ssl._create_unverified_context()
 def get(fresh=False):
  headers={'Accept':'application/json','User-Agent':'CARACAL/1.0'};secret=str(creds.get('secret') or '')
  if w['auth_type']=='bearer':headers['Authorization']='Bearer '+secret
  elif w['auth_type']=='basic':headers['Authorization']='Basic '+base64.b64encode(f"{creds.get('username') or ''}:{secret}".encode()).decode()
  elif w['auth_type']=='header':headers[w['auth_header'] or 'Authorization']=secret
  elif w['auth_type']=='oauth2':headers['Authorization']='Bearer '+_wch_oauth_token(w,creds,watcher_id,fresh)
  with urllib.request.urlopen(urllib.request.Request(w['url'],headers=headers),timeout=20,context=context) as response:return response.read(_WCH_RESPONSE_MAX+1)
 try:
  try:raw=get()
  except urllib.request.HTTPError as error:
   # a cached OAuth2 token may have been revoked before it expired: one more try with a new one
   if error.code!=401 or w['auth_type']!='oauth2':raise
   raw=get(fresh=True)
 except ValueError:raise
 except urllib.request.HTTPError as error:raise ValueError(f'HTTP {error.code} {_wch_error_text(error) or error.reason}')
 except Exception as error:raise ValueError(f'Nedostupné: {getattr(error,"reason",None) or error}')
 if len(raw)>_WCH_RESPONSE_MAX:raise ValueError('Odpověď je větší než 5 MB')
 try:data=json.loads(raw.decode('utf-8',errors='replace'))
 except ValueError:raise ValueError('Odpověď není JSON')
 items=_wch_get(data,w['list_path']) if w['list_path'] else data
 if isinstance(items,dict) and w['list_path']:items=list(items.values())
 if not isinstance(items,list):
  keys=', '.join(list(data.keys())[:15]) if isinstance(data,dict) else type(data).__name__
  raise ValueError(f'Na cestě "{w["list_path"] or "(kořen)"}" není seznam. Klíče v odpovědi: {keys}')
 out=[]
 for item in items:
  iid=_wch_get(item,w['id_field']) if isinstance(item,dict) else None
  if iid not in (None,''):out.append((str(iid),item))
 if items and not out:raise ValueError(f'Položky nemají pole "{w["id_field"]}". Pole první položky: '+', '.join(list(items[0].keys())[:20] if isinstance(items[0],dict) else []))
 return out

def _wch_note(w,iid,item):
 title=_ntf_text(_wch_render(w['title_template'],item),120) or f"{w['name']}: {iid}"
 level=_ntf_level(_wch_get(item,w['level_field']),w['level']) if w['level_field'] else w['level']
 return {'title':title,'message':_ntf_text(_wch_render(w['message_template'],item),600),'level':level,'key':_ntf_text(iid,180),'duration':None,'source':None,'sound':None}

def _wch_check(w):
 # one check of one watcher; returns a short status for the admin UI
 with _WCH_LOCK:
  # re-read inside the lock so a manual check and the background loop never use the same old 'seen' list
  r=rows('SELECT * FROM notify_watchers WHERE id=?',(w['id'],))
  if not r:return {'ok':False,'error':'Hlídač nebyl nalezen'}
  w=r[0];now=time.time()
  try:items=_wch_fetch(w,_wch_credentials(w),w['id'])
  except ValueError as error:
   c=con();c.execute('UPDATE notify_watchers SET last_check=?,last_error=? WHERE id=?',(now,str(error)[:300],w['id']));c.commit();c.close()
   return {'ok':False,'error':str(error)}
  seen=json.loads(w['seen'] or '[]');known=set(seen);new=[(i,it) for i,it in items if i not in known]
  sent=0
  if w['initialized'] and new:
   notes=[_wch_note(w,i,it) for i,it in new]
   if len(notes)>_NTF_BATCH_MAX:
    # many new items at once (e.g. after an outage) become one notification
    notes=[{'title':_ntf_text(f"{w['name']}: {len(notes)} nových",120),'message':_ntf_text('\n'.join(x['title'] for x in notes[:8])+('\n…' if len(notes)>8 else ''),600),'level':max((x['level'] for x in notes),key=lambda x:_NTF_LEVELS[x]),'key':None,'duration':None,'source':None,'sound':None}]
   try:sent=_ntf_enqueue(-w['id'],w['name'],notes)['queued']
   except HTTPException:pass   # notifications are turned off on this screen
  current=[i for i,_ in items];current_set=set(current)
  # every ID in the current list is kept (dropping one would announce it again next time); IDs that left the list
  # are remembered only up to the limit, so a ticket that comes back (reopened) is not announced again soon
  seen=current+[x for x in seen if x not in current_set][:max(0,_WCH_SEEN_MAX-len(current))]
  # a watcher edited during the check keeps its reset state (revision check)
  c=con();c.execute('UPDATE notify_watchers SET seen=?,initialized=1,last_check=?,last_error=NULL,last_count=?,last_new=? WHERE id=? AND revision=?',(json.dumps(seen),now,len(items),len(new) if w['initialized'] else 0,w['id'],w['revision']));c.commit();c.close()
  return {'ok':True,'count':len(items),'new':len(new) if w['initialized'] else 0,'sent':sent,'first':not w['initialized']}

def _wch_loop():
 while True:
  try:
   for w in rows('SELECT * FROM notify_watchers WHERE enabled=1'):
    if time.time()>=float(w['last_check'] or 0)+int(w['interval'] or 60):_wch_check(w)
  except Exception as error:print('watcher error',error,flush=True)
  time.sleep(5)
@app.on_event('startup')
def _wch_start():threading.Thread(target=_wch_loop,daemon=True,name='caracal-watchers').start()

def _wch_fields(d,current=None):
 current=current or {};out={}
 for key,limit in (('name',60),('url',4000),('auth_header',100),('list_path',200),('id_field',200),('title_template',500),('message_template',1000),('level_field',200)):
  out[key]=str(d.get(key,current.get(key)) or '').strip()[:limit]
 for key,limit in (('oauth_token_url',4000),('oauth_client_id',500),('oauth_scope',1000),('oauth_extra',1000)):
  out[key]=str(d.get(key,current.get(key)) or '').strip()[:limit]
 out['oauth_grant']=str(d.get('oauth_grant',current.get('oauth_grant')) or 'client_credentials')
 out['oauth_client_auth']='basic' if d.get('oauth_client_auth',current.get('oauth_client_auth'))=='basic' else 'body'
 if out['oauth_grant'] not in _WCH_GRANTS:raise HTTPException(400,'Neplatný typ OAuth2 grantu')
 if not out['name']:raise HTTPException(400,'Vyplň název')
 if not out['url'].startswith(('http://','https://')):raise HTTPException(400,'URL musí začínat http:// nebo https://')
 out['id_field']=out['id_field'] or 'id'
 out['auth_type']=str(d.get('auth_type',current.get('auth_type')) or 'none')
 if out['auth_type'] not in _WCH_AUTH:raise HTTPException(400,'Neplatný typ přihlášení')
 out['level']=_ntf_level(d.get('level',current.get('level')))
 try:out['interval']=max(15,min(86400,int(d.get('interval',current.get('interval')) or 60)))
 except (TypeError,ValueError):raise HTTPException(400,'Neplatný interval')
 out['verify_tls']=1 if d.get('verify_tls',current.get('verify_tls',1)) not in (False,0,'0','false','') else 0
 out['enabled']=1 if d.get('enabled',current.get('enabled',1)) not in (False,0,'0','false','') else 0
 # empty username / secret = keep the stored one
 creds=_wch_credentials(current) if current else {}
 for key in _WCH_SECRETS:
  if str(d.get(key) or '').strip():creds[key]=str(d[key]).strip()
 if out['auth_type']=='oauth2':
  if not out['oauth_token_url'].startswith(('http://','https://')):raise HTTPException(400,'Token URL musí začínat http:// nebo https://')
  if out['oauth_grant']=='client_credentials' and not out['oauth_client_id']:raise HTTPException(400,'Vyplň Client ID')
  if out['oauth_grant']=='password' and not (creds.get('username') and creds.get('secret')):raise HTTPException(400,'Vyplň uživatele a heslo')
  if out['oauth_grant']=='refresh_token' and not creds.get('refresh_token'):raise HTTPException(400,'Vlož refresh token')
 out['credentials_enc']=vault.encrypt(json.dumps(creds).encode()) if creds else None
 return out

@app.get('/api/notify/watchers')
def notify_watchers(u=Depends(auth)):return rows(f'SELECT {_WCH_COLUMNS} FROM notify_watchers ORDER BY name COLLATE NOCASE,id')
@app.post('/api/notify/watchers')
async def notify_watcher_create(req:Request,u=Depends(auth)):return _wch_create(await req.json(),req,u)
def _wch_create(d,req,actor):
 f=_wch_fields(d);keys=list(f)
 c=con();q=c.execute(f'INSERT INTO notify_watchers({",".join(keys)},created) VALUES({",".join("?"*len(keys))},?)',(*f.values(),time.time()));c.commit();c.close()
 _ntf_audit(req,actor,'Hlídač vytvořen',f"{f['name']} – {f['url'].split('?')[0]}")
 return {'ok':True,'id':q.lastrowid}
@app.put('/api/notify/watchers/{watcher_id}')
async def notify_watcher_update(watcher_id:int,req:Request,u=Depends(auth)):return _wch_update(watcher_id,await req.json(),req,u)
def _wch_update(watcher_id,d,req,actor):
 r=rows('SELECT * FROM notify_watchers WHERE id=?',(watcher_id,))
 if not r:raise HTTPException(404,'Hlídač nebyl nalezen')
 f=_wch_fields(d,r[0])
 # another URL or list means other items: start again without notifying about the existing ones
 reset=any(f[k]!=r[0][k] for k in ('url','list_path','id_field'))
 c=con();c.execute(f'UPDATE notify_watchers SET {",".join(k+"=?" for k in f)},revision=revision+1'+(",seen='[]',initialized=0,last_check=NULL" if reset else '')+' WHERE id=?',(*f.values(),watcher_id));c.commit();c.close()
 changed=[k for k in f if k!='credentials_enc' and f[k]!=r[0][k]]+(['přihlašovací údaje'] if f['credentials_enc']!=r[0]['credentials_enc'] and any(str(d.get(k) or '').strip() for k in _WCH_SECRETS) else [])
 if changed:_ntf_audit(req,actor,'Hlídač '+('zapnut' if changed==['enabled'] and f['enabled'] else 'vypnut' if changed==['enabled'] else 'upraven'),f"{f['name']}: "+', '.join(changed))
 return {'ok':True,'reset':reset}
@app.delete('/api/notify/watchers/{watcher_id}')
def notify_watcher_delete(watcher_id:int,req:Request,u=Depends(auth)):return _wch_delete(watcher_id,req,u)
def _wch_delete(watcher_id,req,actor):
 r=rows('SELECT name FROM notify_watchers WHERE id=?',(watcher_id,))
 if not r:raise HTTPException(404,'Hlídač nebyl nalezen')
 c=con();c.execute('DELETE FROM notify_watchers WHERE id=?',(watcher_id,));c.commit();c.close()
 _ntf_audit(req,actor,'Hlídač smazán',r[0]['name'])
 return {'ok':True}
@app.post('/api/notify/watchers/{watcher_id}/check')
def notify_watcher_check(watcher_id:int,u=Depends(auth)):
 r=rows('SELECT * FROM notify_watchers WHERE id=?',(watcher_id,))
 if not r:raise HTTPException(404,'Hlídač nebyl nalezen')
 return _wch_check(r[0])
@app.post('/api/notify/watchers/preview')
async def notify_watcher_preview(req:Request,u=Depends(auth)):
 # tries a configuration from the edit dialog without saving it or sending anything
 d=await req.json();current=None
 if d.get('id'):
  r=rows('SELECT * FROM notify_watchers WHERE id=?',(int(d['id']),));current=r[0] if r else None
 w=_wch_fields(d,current)
 if w['auth_type']=='oauth2' and w['oauth_grant']=='refresh_token' and not current:
  # trying it could replace the pasted refresh token before the watcher is saved
  raise HTTPException(400,'S refresh tokenem hlídač nejdřív ulož a vyzkoušej tlačítkem Zkontrolovat teď')
 try:items=_wch_fetch(w,_wch_credentials(w),current['id'] if current else None)
 except ValueError as error:raise HTTPException(400,str(error))
 return {'ok':True,'count':len(items),'samples':[_wch_note(w,i,it) for i,it in items[:3]]}


# ------------------------------------------------------------------ notifications from CARACAL Fleet
# Notifications managed by CARACAL Fleet: settings, the queue and watchers (agent contract: docs/LOCAL-API.md in the
# CARACAL Fleet repository). Authenticated with the Fleet key; the node's audit log names "CARACAL Fleet".
_FLEET_ACTOR='CARACAL Fleet'
def _fleet_notifications():
 # what the snapshot reports: never tokens, credentials or the IDs a watcher has seen
 waiting=rows('SELECT COUNT(*) AS n FROM notifications WHERE done=0 AND shown_at IS NULL')[0]['n']
 current=rows('SELECT title,message,level FROM notifications WHERE done=0 AND shown_at IS NOT NULL AND shown_at+duration>? LIMIT 1',(time.time(),))
 return {'settings':_ntf_settings(),'waiting':waiting,'current':current[0] if current else None,
  'watchers':rows(f'SELECT {_WCH_COLUMNS} FROM notify_watchers ORDER BY name COLLATE NOCASE,id'),
  'tokens':rows('SELECT COUNT(*) AS n FROM notify_tokens WHERE enabled=1')[0]['n'],'sounds':_snd_meta(),'image':_img_meta(),
  # the queue (not the messages of the history: those stay on the node and are fetched on demand)
  'queue':[{**x,'title':_ntf_text(x['title'],120),'message':_ntf_text(x['message'],200)} for x in rows('SELECT id,source,title,message,level,priority,created FROM notifications WHERE done=0 AND shown_at IS NULL ORDER BY priority DESC,id LIMIT 30')],
  'history_count':rows('SELECT COUNT(*) AS n FROM notifications WHERE done>0')[0]['n'],
  'audit_count':rows('SELECT COUNT(*) AS n FROM notify_audit')[0]['n'],
  'token_list':rows('SELECT id,name,prefix,rate_per_min,enabled,created,last_used FROM notify_tokens ORDER BY name COLLATE NOCASE,id')}
async def _fleet_json(req:Request):
 try:d=await req.json()
 except ValueError:raise HTTPException(400,'Invalid JSON')
 if not isinstance(d,dict):raise HTTPException(400,'Invalid JSON')
 return d
@app.put('/api/fleet/v1/notify/settings')
async def fleet_notify_settings(req:Request):
 _fleet_auth(req);return _ntf_save_settings(await _fleet_json(req),req,_FLEET_ACTOR)
@app.post('/api/fleet/v1/notify/clear')
def fleet_notify_clear(req:Request):
 _fleet_auth(req);return _ntf_clear(req,_FLEET_ACTOR)
@app.post('/api/fleet/v1/notify/watchers')
async def fleet_watcher_create(req:Request):
 _fleet_auth(req);return _wch_create(await _fleet_json(req),req,_FLEET_ACTOR)
@app.put('/api/fleet/v1/notify/watchers/{watcher_id}')
async def fleet_watcher_update(watcher_id:int,req:Request):
 _fleet_auth(req);return _wch_update(watcher_id,await _fleet_json(req),req,_FLEET_ACTOR)
@app.delete('/api/fleet/v1/notify/watchers/{watcher_id}')
def fleet_watcher_delete(watcher_id:int,req:Request):
 _fleet_auth(req);return _wch_delete(watcher_id,req,_FLEET_ACTOR)
@app.post('/api/fleet/v1/notify/watchers/{watcher_id}/check')
def fleet_watcher_check(watcher_id:int,req:Request):
 _fleet_auth(req);r=rows('SELECT * FROM notify_watchers WHERE id=?',(watcher_id,))
 if not r:raise HTTPException(404,'Hlídač nebyl nalezen')
 return _wch_check(r[0])


# ------------------------------------------------------------------ notification sounds
# Custom notification sounds: one MP3 per level instead of the generated chime, uploaded in the admin UI or from
# CARACAL Fleet. Stored as DATA/notify-sounds/<level>.mp3 (details in notify_settings 'sound_file_<level>'); the
# overlay downloads it once, converts it to WAV with mpg123 and plays at most 15 s with the volume of the settings.
_SND_DIR=DATA/'notify-sounds';_SND_DIR.mkdir(exist_ok=True)
_SND_MAX=5*1024**2
def _snd_level(level):
 if level not in _NTF_LEVELS:raise HTTPException(404,'Neznámá úroveň oznámení')
 return level
def _snd_meta():
 out={}
 for r in rows("SELECT key,value FROM notify_settings WHERE key LIKE 'sound_file_%'"):
  level=r['key'][len('sound_file_'):]
  if level in _NTF_LEVELS and (_SND_DIR/f'{level}.mp3').is_file():
   try:out[level]=json.loads(r['value'])
   except ValueError:pass
 return out
def _snd_is_mp3(head):
 # ID3 tag or an MPEG audio frame header (11 sync bits)
 return head[:3]==b'ID3' or (len(head)>1 and head[0]==0xFF and head[1]&0xE0==0xE0)
async def _snd_store(level,upload,req,actor):
 level=_snd_level(level)
 if upload is None or not hasattr(upload,'read'):raise HTTPException(400,'Chybí soubor')
 name=_ntf_text(Path(upload.filename or 'zvuk.mp3').name,120) or 'zvuk.mp3'
 tmp=_SND_DIR/f'{level}.part';size=0;digest=hashlib.sha256()
 try:
  with tmp.open('wb') as f:
   while chunk:=await upload.read(1048576):
    if not size and not _snd_is_mp3(chunk):raise HTTPException(400,'Soubor není MP3')
    size+=len(chunk)
    if size>_SND_MAX:raise HTTPException(413,'Zvuk může mít nejvýš 5 MB')
    digest.update(chunk);f.write(chunk)
  if not size:raise HTTPException(400,'Prázdný soubor')
  tmp.replace(_SND_DIR/f'{level}.mp3')
 finally:tmp.unlink(missing_ok=True)
 meta={'name':name,'size':size,'sha256':digest.hexdigest(),'uploaded':time.time()}
 c=con();c.execute('INSERT OR REPLACE INTO notify_settings(key,value) VALUES(?,?)',('sound_file_'+level,json.dumps(meta,ensure_ascii=False)));c.commit();c.close()
 _ntf_audit(req,actor,'Zvuk oznámení nahrán',f'{level}: {name}')
 return {'ok':True,'level':level,**meta}
def _snd_delete(level,req,actor):
 level=_snd_level(level);had=level in _snd_meta()
 (_SND_DIR/f'{level}.mp3').unlink(missing_ok=True)
 c=con();c.execute('DELETE FROM notify_settings WHERE key=?',('sound_file_'+level,));c.commit();c.close()
 if had:_ntf_audit(req,actor,'Zvuk oznámení vrácen na výchozí',level)
 return {'ok':True,'level':level}
def _snd_file(level):
 path=_SND_DIR/f'{_snd_level(level)}.mp3'
 if not path.is_file():raise HTTPException(404,'Vlastní zvuk není nahraný')
 return FileResponse(path,media_type='audio/mpeg')

@app.get('/api/notify/sounds')
def notify_sounds(u=Depends(auth)):return _snd_meta()
@app.post('/api/notify/sounds/{level}')
async def notify_sound_upload(level:str,req:Request,file:UploadFile=File(...),u=Depends(auth)):return await _snd_store(level,file,req,u)
@app.delete('/api/notify/sounds/{level}')
def notify_sound_delete(level:str,req:Request,u=Depends(auth)):return _snd_delete(level,req,u)
@app.get('/api/notify/sounds/{level}/file')
def notify_sound_file(level:str,u=Depends(auth)):return _snd_file(level)
@app.get('/api/notify/sounds/{level}/overlay')
def notify_sound_overlay(level:str,req:Request):
 # the overlay downloads the sound from here (only on the device itself)
 _local_only(req);return _snd_file(level)
@app.post('/api/fleet/v1/notify/sounds/{level}')
async def fleet_notify_sound_upload(level:str,req:Request):
 # authenticate before the multipart body is parsed
 _fleet_auth(req);form=await req.form();return await _snd_store(level,form.get('file'),req,_FLEET_ACTOR)
@app.delete('/api/fleet/v1/notify/sounds/{level}')
def fleet_notify_sound_delete(level:str,req:Request):
 _fleet_auth(req);return _snd_delete(level,req,_FLEET_ACTOR)


# ------------------------------------------------------------------ the picture of the notification look
# A picture for the notification look (a logo, a photo, a background): PNG, JPEG, GIF or WebP up to 5 MB, stored as
# DATA/notify-image (details in notify_settings 'image_file'). Shown when the look has 'image' on; the overlay
# downloads it once per version (the overlay poll carries the first 16 characters of its SHA-256).
_IMG_FILE=DATA/'notify-image';_IMG_MAX=5*1024**2
_IMG_TYPES=((b'\x89PNG\r\n\x1a\n','image/png'),(b'\xff\xd8\xff','image/jpeg'),(b'GIF87a','image/gif'),(b'GIF89a','image/gif'))
def _img_type(head):
 for magic,kind in _IMG_TYPES:
  if head.startswith(magic):return kind
 return 'image/webp' if head[:4]==b'RIFF' and head[8:12]==b'WEBP' else ''
def _img_meta():
 r=rows("SELECT value FROM notify_settings WHERE key='image_file'")
 if not r or not _IMG_FILE.is_file():return {}
 try:return json.loads(r[0]['value'])
 except ValueError:return {}
async def _img_store(upload,req,actor):
 if upload is None or not hasattr(upload,'read'):raise HTTPException(400,'Chybí soubor')
 name=_ntf_text(Path(upload.filename or 'obrazek').name,120) or 'obrazek'
 tmp=DATA/'notify-image.part';size=0;digest=hashlib.sha256();kind=''
 try:
  with tmp.open('wb') as f:
   while chunk:=await upload.read(1048576):
    if not size:
     kind=_img_type(chunk[:16])
     if not kind:raise HTTPException(400,'Obrázek musí být PNG, JPEG, GIF nebo WebP')
    size+=len(chunk)
    if size>_IMG_MAX:raise HTTPException(413,'Obrázek může mít nejvýš 5 MB')
    digest.update(chunk);f.write(chunk)
  if not size:raise HTTPException(400,'Prázdný soubor')
  tmp.replace(_IMG_FILE)
 finally:tmp.unlink(missing_ok=True)
 meta={'name':name,'size':size,'type':kind,'sha256':digest.hexdigest(),'uploaded':time.time()}
 c=con();c.execute("INSERT OR REPLACE INTO notify_settings(key,value) VALUES('image_file',?)",(json.dumps(meta,ensure_ascii=False),));c.commit();c.close()
 _ntf_audit(req,actor,'Obrázek oznámení nahrán',name)
 return {'ok':True,**meta}
def _img_delete(req,actor):
 had=bool(_img_meta());_IMG_FILE.unlink(missing_ok=True)
 c=con();c.execute("DELETE FROM notify_settings WHERE key='image_file'");c.commit();c.close()
 if had:_ntf_audit(req,actor,'Obrázek oznámení odebrán','')
 return {'ok':True}
def _img_file():
 meta=_img_meta()
 if not meta:raise HTTPException(404,'Obrázek není nahraný')
 return FileResponse(_IMG_FILE,media_type=meta.get('type') or 'application/octet-stream',headers={'Cache-Control':'no-cache'})
@app.get('/api/notify/image')
def notify_image(u=Depends(auth)):return _img_meta()
@app.post('/api/notify/image')
async def notify_image_upload(req:Request,file:UploadFile=File(...),u=Depends(auth)):return await _img_store(file,req,u)
@app.delete('/api/notify/image')
def notify_image_delete(req:Request,u=Depends(auth)):return _img_delete(req,u)
@app.get('/api/notify/image/file')
def notify_image_file(u=Depends(auth)):return _img_file()
@app.get('/api/notify/image/overlay')
def notify_image_overlay(req:Request):
 _local_only(req);return _img_file()
@app.post('/api/fleet/v1/notify/image')
async def fleet_notify_image_upload(req:Request):
 _fleet_auth(req);form=await req.form();return await _img_store(form.get('file'),req,_FLEET_ACTOR)
@app.delete('/api/fleet/v1/notify/image')
def fleet_notify_image_delete(req:Request):
 _fleet_auth(req);return _img_delete(req,_FLEET_ACTOR)


# ------------------------------------------------------------------ the screen picture
# What the TV shows right now. The admin UI (or CARACAL Fleet) asks for a picture; the overlay, which runs on the
# device's display, sees the request in its next poll (about a second), reads the whole screen (page, countdown and
# notifications) and uploads a JPEG here. Kept as DATA/screenshot.jpg until the next one.
_SHOT_FILE=DATA/'screenshot.jpg';_SHOT_MAX=8*1024**2;_SHOT_WAIT=60
_shot={'request':'','requested':0.0,'id':'','taken':0.0,'error':''}
def _shot_info():
 info={k:_shot[k] for k in ('request','requested','id','taken','error')}
 if not info['taken'] and _SHOT_FILE.is_file():info['taken']=_SHOT_FILE.stat().st_mtime   # after a restart
 info['pending']=bool(_shot_pending())
 return info
def _shot_pending():
 return _shot['request'] if _shot['request'] and _shot['request']!=_shot['id'] and time.time()-_shot['requested']<_SHOT_WAIT else ''
def _shot_request():
 if not _shot_pending():_shot.update(request=secrets.token_hex(6),requested=time.time(),error='')
 return _shot['request']
@app.post('/api/screenshot')
def screenshot_request(u=Depends(auth)):return {'ok':True,'id':_shot_request()}
@app.get('/api/screenshot')
def screenshot_info(u=Depends(auth)):return _shot_info()
@app.get('/api/screenshot.jpg')
def screenshot_file(u=Depends(auth)):
 if not _SHOT_FILE.is_file():raise HTTPException(404,'Zatím žádný snímek obrazovky')
 return FileResponse(_SHOT_FILE,media_type='image/jpeg',headers={'Cache-Control':'no-store'})
@app.post('/api/screenshot/upload')
async def screenshot_upload(req:Request,id:str='',error:str=''):
 _local_only(req)
 if not id or id!=_shot['request']:raise HTTPException(409,'Snímek nebyl vyžádán')
 if error:_shot.update(id=id,error=_ntf_text(error,300));return {'ok':True}
 body=await req.body()
 if len(body)>_SHOT_MAX or body[:3]!=b'\xff\xd8\xff':raise HTTPException(400,'Očekává se JPEG')
 tmp=_SHOT_FILE.with_suffix('.part');tmp.write_bytes(body);tmp.replace(_SHOT_FILE)
 _shot.update(id=id,taken=time.time(),error='')
 return {'ok':True}
@app.post('/api/fleet/v1/screenshot')
async def fleet_screenshot(req:Request):
 # asks for a picture and waits for it (up to 15 s); answers the JPEG itself
 _fleet_auth(req);request=_shot_request();deadline=time.time()+15
 while time.time()<deadline and _shot['id']!=request:await asyncio.sleep(.25)
 if _shot['id']!=request:raise HTTPException(504,'The screen did not answer; is the overlay running (caracal-overlay)?')
 if _shot['error']:raise HTTPException(409,_shot['error'])
 return FileResponse(_SHOT_FILE,media_type='image/jpeg',headers={'Cache-Control':'no-store'})


# ------------------------------------------------------------------ what the web administration offers, also for CARACAL Fleet
# What the node's web administration can do, CARACAL Fleet can do as well: the web administrator account, the
# countdown on the TV, the notification queue, history and audit log, the node's notification tokens, trying a
# watcher before saving it and trying a Grafana tag. Authenticated with the Fleet key.
def _fleet_admin_state():
 r=rows('SELECT username FROM users ORDER BY id LIMIT 1')
 return {'configured':bool(r),'username':r[0]['username'] if r else ''}
@app.post('/api/fleet/v1/admin')
async def fleet_admin(req:Request):
 # creates the web administrator (before the first-run setup) or sets a new name and password of the existing one
 _fleet_auth(req);d=await _fleet_json(req);username=str(d.get('username') or '').strip();password=str(d.get('password') or '')
 if not username or len(username)>64 or any(ch.isspace() for ch in username):raise HTTPException(400,'Invalid user name')
 if not 10<=len(password)<=200:raise HTTPException(400,'Password must have 10 to 200 characters')
 c=con();r=c.execute('SELECT id FROM users ORDER BY id LIMIT 1').fetchone()
 if r and c.execute('SELECT 1 FROM users WHERE username=? AND id!=?',(username,r['id'])).fetchone():c.close();raise HTTPException(409,'User name already used')
 if r:c.execute('UPDATE users SET username=?,password_hash=? WHERE id=?',(username,pwd.hash(password),r['id']))
 else:c.execute('INSERT INTO users(username,password_hash) VALUES(?,?)',(username,pwd.hash(password)))
 c.commit();c.close();_login_fails.clear()
 print('CARACAL Fleet','changed' if r else 'created','the web administrator',username,flush=True)
 return {'ok':True,'created':not r,'username':username}
@app.put('/api/fleet/v1/player/overlay')
async def fleet_overlay(req:Request):
 # the countdown bar of the current page on the TV
 _fleet_auth(req);d=await _fleet_json(req);state=_cc2_read()
 enabled=bool(d.get('enabled',state.get('overlay_enabled',True)))
 try:size=int(d.get('size',state.get('overlay_size',16)))
 except (TypeError,ValueError):raise HTTPException(400,'Invalid size')
 if not 4<=size<=200:raise HTTPException(400,'Size must be 4 to 200 px')
 state['overlay_enabled']=enabled;state['overlay_size']=size;_cc2_write(state)
 return {'ok':True,'enabled':enabled,'size':size}
@app.post('/api/fleet/v1/notify/skip')
def fleet_notify_skip(req:Request):_fleet_auth(req);return notify_skip(req,_FLEET_ACTOR)
@app.delete('/api/fleet/v1/notify/queue/{notification_id}')
def fleet_notify_remove(notification_id:int,req:Request):_fleet_auth(req);return notify_remove(notification_id,req,_FLEET_ACTOR)
@app.get('/api/fleet/v1/notify/log')
def fleet_notify_log(req:Request,limit:int=100):
 # history and audit log on demand (they can hold message texts, so they are not part of every heartbeat)
 _fleet_auth(req);limit=max(1,min(200,limit))
 history=rows('SELECT id,source,title,message,level,priority,created,shown_at,done FROM notifications WHERE done>0 ORDER BY id DESC LIMIT ?',(limit,))
 return {'history':history,'history_count':rows('SELECT COUNT(*) AS n FROM notifications WHERE done>0')[0]['n'],
  'audit':rows('SELECT * FROM notify_audit ORDER BY id DESC LIMIT ?',(limit,)),'audit_count':rows('SELECT COUNT(*) AS n FROM notify_audit')[0]['n']}
@app.post('/api/fleet/v1/notify/history/clear')
def fleet_notify_history_clear(req:Request):_fleet_auth(req);return notify_history_clear(req,_FLEET_ACTOR)
@app.post('/api/fleet/v1/notify/audit/clear')
def fleet_notify_audit_clear(req:Request):_fleet_auth(req);return notify_audit_clear(req,_FLEET_ACTOR)
@app.put('/api/fleet/v1/notify/tokens/{token_id}')
async def fleet_notify_token_update(token_id:int,req:Request):
 # name, limit and enabled; new tokens are created in the node's administration (a token is shown only once)
 _fleet_auth(req);return await notify_token_update(token_id,req,_FLEET_ACTOR)
@app.delete('/api/fleet/v1/notify/tokens/{token_id}')
def fleet_notify_token_delete(token_id:int,req:Request):_fleet_auth(req);return notify_token_delete(token_id,req,_FLEET_ACTOR)
@app.post('/api/fleet/v1/notify/watchers/preview')
async def fleet_notify_watcher_preview(req:Request):_fleet_auth(req);return await notify_watcher_preview(req,_FLEET_ACTOR)
@app.post('/api/fleet/v1/grafana/discover')
async def fleet_grafana_discover(req:Request):
 _fleet_auth(req);d=await _fleet_json(req);url=str(d.get('grafana_url') or '').strip().rstrip('/');tag=str(d.get('tag') or '').strip()
 if not url.startswith(('http://','https://')):raise HTTPException(400,'Grafana URL must start with http:// or https://')
 if not tag:raise HTTPException(400,'Tag must not be empty')
 found=_grafana_discover(url,tag);return {'ok':True,'count':len(found),'dashboards':found[:50]}


# ------------------------------------------------------------------ websites in dark mode
# Websites in dark mode: a setting of the node for every website and Grafana collection, and a choice of each item
# (NULL = as the node). 'dark' asks the pages for their own dark look (prefers-color-scheme: dark), 'force' also
# darkens pages that have none (Chromium's automatic dark mode). Grafana dashboards get its dark theme.
_DARK_MODES=('off','dark','force')
def _dark_value(value,item=True):
 # an item may follow the node (None); the node's own setting must be one of the modes
 v=str(value or '').strip().lower()
 if item and v in ('','auto','null','none'):return None
 if v not in _DARK_MODES:raise HTTPException(400,'Dark mode must be off, dark or force')
 return v
def _web_dark():
 r=rows("SELECT value FROM player_settings WHERE key='web_dark'")
 return r[0]['value'] if r and r[0]['value'] in _DARK_MODES else 'off'
def _dark_of(asset,default):return asset.get('dark') if asset.get('dark') in _DARK_MODES else default
def _player_settings():return {'web_dark':_web_dark()}
def _save_player_settings(d):
 if not isinstance(d,dict):raise HTTPException(400,'Expected a JSON object')
 if 'web_dark' in d:
  c=con();c.execute("INSERT OR REPLACE INTO player_settings(key,value) VALUES('web_dark',?)",(_dark_value(d['web_dark'],False),));c.commit();c.close()
 return _player_settings()
def _set_asset_dark(asset_id,value):
 dark=_dark_value(value);c=con();a=c.execute('SELECT kind FROM assets WHERE id=?',(asset_id,)).fetchone()
 if not a:c.close();raise HTTPException(404,'Položka nebyla nalezena')
 if a['kind'] not in ('web','grafana-tag'):c.close();raise HTTPException(400,'Tmavý režim jde nastavit jen u webu nebo kolekce Grafany')
 c.execute('UPDATE assets SET dark=? WHERE id=?',(dark,asset_id));c.commit();c.close()
 return {'ok':True,'dark':dark}
@app.get('/api/player/settings')
def player_settings(u=Depends(auth)):return _player_settings()
@app.put('/api/player/settings')
async def player_settings_save(request:Request,u=Depends(auth)):return _save_player_settings(await request.json())
@app.put('/api/assets/{asset_id}/dark')
async def asset_dark(asset_id:int,request:Request,u=Depends(auth)):
 d=await request.json();return _set_asset_dark(asset_id,d.get('dark') if isinstance(d,dict) else None)
@app.put('/api/fleet/v1/player/settings')
async def fleet_player_settings(req:Request):_fleet_auth(req);return {'ok':True,**_save_player_settings(await _fleet_json(req))}


# ------------------------------------------------------------------ black screen watch
# Black screen watch: the overlay reports once a minute whether the whole screen is black. A website that stays black
# for 5 minutes gets the player restarted (at most every 30 minutes); a black picture or video is left alone, it may
# be meant so. CARACAL Fleet sees the state in the snapshot.
_BLACK_RESTART_AFTER,_BLACK_RESTART_EVERY=300,1800
_display={'black_since':None,'checked':0.0,'restarted':0.0}
def _display_info():
 fresh=time.time()-_display['checked']<300
 return {'black_since':_display['black_since'] if fresh else None,'checked':_display['checked'] or None,'restarted':_display['restarted'] or None}
def _current_kind():
 cur=_cc2_read().get('current_id')
 if cur is None:return ''
 try:cur=int(cur)
 except (TypeError,ValueError):return ''
 if cur>=100000:return 'web'   # a dashboard of a Grafana collection
 r=rows('SELECT kind FROM assets WHERE id=?',(cur,))
 return r[0]['kind'] if r else ''
@app.post('/api/display/health')
async def display_health(req:Request):
 _local_only(req);d=await req.json();now=time.time()
 black=bool(isinstance(d,dict) and d.get('black'))
 _display['checked']=now
 if not black:_display['black_since']=None;return {'ok':True}
 _display['black_since']=_display['black_since'] or now
 if now-_display['black_since']>=_BLACK_RESTART_AFTER and now-_display['restarted']>=_BLACK_RESTART_EVERY and _current_kind()=='web':
  print('The screen has been black for',int(now-_display['black_since']),'s on a website: restarting the player',flush=True)
  _display['restarted']=now;request_player_restart()
 return {'ok':True,'black_since':_display['black_since']}
