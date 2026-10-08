import os,sqlite3,secrets,time,socket,subprocess
from pathlib import Path
from datetime import datetime,timedelta,timezone
import jwt,psutil
from cryptography.fernet import Fernet
from fastapi import FastAPI,Request,UploadFile,File,Form,HTTPException,Depends
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from passlib.context import CryptContext
DATA=Path(os.getenv('CARACAL_DATA',str(Path.home()/'.local/share/caracal')));MEDIA=DATA/'media';DATA.mkdir(parents=True,exist_ok=True);MEDIA.mkdir(exist_ok=True)
DB=DATA/'hub.db';SF=DATA/'secret.key';VF=DATA/'vault.key'
if not SF.exists():SF.write_text(secrets.token_urlsafe(48));os.chmod(SF,0o600)
if not VF.exists():VF.write_bytes(Fernet.generate_key());os.chmod(VF,0o600)
vault=Fernet(VF.read_bytes());secret=SF.read_text();pwd=CryptContext(schemes=['bcrypt'],deprecated='auto');app=FastAPI(docs_url=None,redoc_url=None)
app.mount('/static',StaticFiles(directory=Path(__file__).parent/'static'),name='static');app.mount('/media',StaticFiles(directory=MEDIA),name='media')
def con():c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def init():
 c=con();c.executescript("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT);CREATE TABLE IF NOT EXISTS assets(id INTEGER PRIMARY KEY,name TEXT,kind TEXT,source TEXT,duration INTEGER DEFAULT 30,position INTEGER DEFAULT 0,auth_profile_id INTEGER);CREATE TABLE IF NOT EXISTS auth_profiles(id INTEGER PRIMARY KEY,name TEXT,login_url TEXT,target_url TEXT,username_enc BLOB,password_enc BLOB,user_selector TEXT,pass_selector TEXT,submit_selector TEXT);""");c.commit();c.close()
init()
# Schema migration for existing installations.
c=con()
columns=[row[1] for row in c.execute("PRAGMA table_info(assets)").fetchall()]
if "scale" not in columns:
 c.execute("ALTER TABLE assets ADD COLUMN scale REAL DEFAULT 1.0")
c.execute("UPDATE assets SET scale=1.0 WHERE scale IS NULL")
# login profiles: 'form' fills in the page's log-in form, 'http' answers the browser's HTTP Basic/Digest pop-up
if "auth_type" not in [row[1] for row in c.execute("PRAGMA table_info(auth_profiles)").fetchall()]:
 c.execute("ALTER TABLE auth_profiles ADD COLUMN auth_type TEXT DEFAULT 'form'")
c.commit();c.close()
def _profile_type(value):return 'http' if str(value or '').strip().lower()=='http' else 'form'
def rows(q,a=()):c=con();r=[dict(x) for x in c.execute(q,a).fetchall()];c.close();return r
def _local_only(req:Request):
 if not req.client or req.client.host not in ('127.0.0.1','::1'):raise HTTPException(403)
def _remove_media(source):
 # deleted images/videos also free their file, unless another item still uses it
 if not str(source or '').startswith('/media/'):return
 path=MEDIA/Path(source).name
 if not rows('SELECT id FROM assets WHERE source=?',(source,)):path.unlink(missing_ok=True)
_login_fails={}
# In Docker (CARACAL_RUNTIME=docker) there is no systemd/sudo: the player restarts itself when asked and the
# host reboot is carried out by the CARACAL Fleet Agent, which reads the request from the Fleet snapshot.
RUNTIME=os.getenv('CARACAL_RUNTIME','host')
def request_player_restart():
 if RUNTIME!='docker':subprocess.Popen(['sudo','/bin/systemctl','restart','caracal-player.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);return True
 state=_cc2_read();state['restart_player_request']=int(state.get('restart_player_request',0))+1;_cc2_write(state);return True
def request_reboot():
 if RUNTIME!='docker':subprocess.Popen(['sudo','/bin/systemctl','reboot'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);return True
 state=_cc2_read();state['reboot_request']=int(state.get('reboot_request',0))+1;_cc2_write(state);return True
def auth(req:Request):
 try:uid=int(jwt.decode(req.cookies.get('session',''),secret,algorithms=['HS256'])['sub'])
 except:raise HTTPException(401)
 r=rows('SELECT id,username FROM users WHERE id=?',(uid,));
 if not r:raise HTTPException(401)
 return r[0]
def tok(i):return jwt.encode({'sub':str(i),'exp':datetime.now(timezone.utc)+timedelta(days=1)},secret,algorithm='HS256')
@app.get('/')
def home():return FileResponse(Path(__file__).parent/'static/index.html')
@app.get('/api/setup-status')
def setup_status():return {'needed':not bool(rows('SELECT id FROM users LIMIT 1'))}
@app.post('/api/setup')
def setup(username:str=Form(...),password:str=Form(...)):
 if rows('SELECT id FROM users LIMIT 1'):raise HTTPException(409,'Setup completed')
 if len(password)<10:raise HTTPException(400,'Heslo musí mít nejméně 10 znaků')
 c=con();x=c.execute('INSERT INTO users(username,password_hash) VALUES(?,?)',(username,pwd.hash(password)));c.commit();i=x.lastrowid;c.close();r=JSONResponse({'ok':1});r.set_cookie('session',tok(i),httponly=True,samesite='strict');return r
@app.post('/api/login')
def login(request:Request,username:str=Form(...),password:str=Form(...)):
 ip=request.client.host if request.client else '';now=time.time();recent=[t for t in _login_fails.get(ip,[]) if t>now-300]
 if len(recent)>=10:raise HTTPException(429,'Příliš mnoho pokusů o přihlášení, zkuste to za 5 minut')
 r=rows('SELECT * FROM users WHERE username=?',(username,));
 if not r or not pwd.verify(password,r[0]['password_hash']):_login_fails[ip]=recent+[now];raise HTTPException(401,'Chybné přihlášení')
 _login_fails.pop(ip,None)
 x=JSONResponse({'ok':1});x.set_cookie('session',tok(r[0]['id']),httponly=True,samesite='strict');return x
@app.get('/api/me')
def me(u=Depends(auth)):return u
@app.post('/api/logout')
def logout():r=JSONResponse({'ok':1});r.delete_cookie('session');return r
@app.get('/api/assets')
def assets(u=Depends(auth)):return rows('SELECT * FROM assets ORDER BY position,id')
@app.get('/api/player/playlist')
def playlist(req:Request):_local_only(req);return rows('SELECT * FROM assets ORDER BY position,id')
@app.post('/api/assets/url')
def url(name:str=Form(...),source:str=Form(...),duration:int=Form(30),auth_profile_id:str=Form(''),u=Depends(auth)):
 if not source.startswith(('http://','https://')):raise HTTPException(400,'Neplatná URL')
 profile_id = int(auth_profile_id) if auth_profile_id and auth_profile_id.strip() else None
 c=con();p=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM assets').fetchone()[0];c.execute('INSERT INTO assets(name,kind,source,duration,position,auth_profile_id) VALUES(?,?,?,?,?,?)',(name,'web',source,max(5,duration),p,profile_id));c.commit();c.close();return {'ok':1}
@app.post('/api/assets/upload')
async def upload(name:str=Form(...),duration:int=Form(15),file:UploadFile=File(...),u=Depends(auth)):
 ext=Path(file.filename or '').suffix.lower();kind='image' if ext in {'.png','.jpg','.jpeg','.webp','.gif'} else 'video' if ext in {'.mp4','.webm','.mkv'} else None
 if not kind:raise HTTPException(400,'Nepodporovaný soubor')
 fn=secrets.token_hex(10)+ext
 with (MEDIA/fn).open('wb') as f:
  while chunk:=await file.read(1048576):f.write(chunk)
 c=con();p=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM assets').fetchone()[0];c.execute('INSERT INTO assets(name,kind,source,duration,position) VALUES(?,?,?,?,?)',(name,kind,'/media/'+fn,max(5,duration),p));c.commit();c.close();return {'ok':1}
@app.delete('/api/assets/{i}')
def delete(i:int,u=Depends(auth)):
 r=rows('SELECT source FROM assets WHERE id=?',(i,));c=con();c.execute('DELETE FROM assets WHERE id=?',(i,));c.commit();c.close()
 if r:_remove_media(r[0]['source'])
 return {'ok':1}
@app.get('/api/profiles')
def profiles(u=Depends(auth)):return rows("SELECT id,name,login_url,target_url,user_selector,pass_selector,submit_selector,COALESCE(auth_type,'form') AS auth_type FROM auth_profiles")
@app.post('/api/profiles')
def profile(name:str=Form(...),login_url:str=Form(''),target_url:str=Form(...),username:str=Form(...),password:str=Form(...),user_selector:str=Form('input[name="name"],input[name="username"]'),pass_selector:str=Form('input[name="password"]'),submit_selector:str=Form('button[type="submit"],input[type="submit"]'),auth_type:str=Form('form'),u=Depends(auth)):
 auth_type=_profile_type(auth_type);login_url=login_url.strip() or (target_url.strip() if auth_type=='http' else '')
 if not target_url.strip().startswith(('http://','https://')) or not login_url.startswith(('http://','https://')):raise HTTPException(400,'URL musí začínat http:// nebo https://')
 c=con();c.execute('INSERT INTO auth_profiles(name,login_url,target_url,username_enc,password_enc,user_selector,pass_selector,submit_selector,auth_type) VALUES(?,?,?,?,?,?,?,?,?)',(name,login_url,target_url.strip(),vault.encrypt(username.encode()),vault.encrypt(password.encode()),user_selector,pass_selector,submit_selector,auth_type));c.commit();c.close();return {'ok':1}
@app.get('/api/player/profile/{i}')
def player_profile(i:int,req:Request):
 if req.client.host not in ('127.0.0.1','::1'):raise HTTPException(403)
 c=con();r=c.execute('SELECT * FROM auth_profiles WHERE id=?',(i,)).fetchone();c.close()
 if not r:raise HTTPException(404)
 d=dict(r);d['username']=vault.decrypt(d.pop('username_enc')).decode();d['password']=vault.decrypt(d.pop('password_enc')).decode();return d
@app.get('/api/system')
def system(u=Depends(auth)):
 try:t=float(Path('/sys/class/thermal/thermal_zone0/temp').read_text())/1000
 except:t=None
 return {'hostname':socket.gethostname(),'cpu':psutil.cpu_percent(),'ram':psutil.virtual_memory().percent,'disk':psutil.disk_usage('/').percent,'temperature':t,'uptime':time.time()-psutil.boot_time()}
@app.post('/api/player/restart')
def restart(u=Depends(auth)):request_player_restart();return {'ok':1}


@app.put("/api/assets/{asset_id}")
async def update_asset(
    asset_id: int,
    request: Request,
    u=Depends(auth),
):
    data = await request.json()

    name = str(
        data.get("name", "")
    ).strip()

    source = str(
        data.get("source", "")
    ).strip()

    try:
        duration = max(
            5,
            int(data.get("duration", 30)),
        )
    except (TypeError, ValueError):
        raise HTTPException(
            400,
            "Neplatná doba zobrazení",
        )

    raw_profile = data.get("auth_profile_id")

    if raw_profile in (
        None,
        "",
        "null",
    ):
        profile_id = None
    else:
        try:
            profile_id = int(raw_profile)
        except (TypeError, ValueError):
            raise HTTPException(
                400,
                "Neplatný přihlašovací profil",
            )

    if not name:
        raise HTTPException(
            400,
            "Název nesmí být prázdný",
        )

    connection = con()

    current = connection.execute(
        "SELECT * FROM assets WHERE id=?",
        (asset_id,),
    ).fetchone()

    if not current:
        connection.close()
        raise HTTPException(
            404,
            "Položka nebyla nalezena",
        )

    if current["kind"] == "web":
        if not source.startswith(
            (
                "http://",
                "https://",
            )
        ):
            connection.close()
            raise HTTPException(
                400,
                "URL musí začínat http:// nebo https://",
            )
    else:
        source = current["source"]
        profile_id = None

    connection.execute(
        """
        UPDATE assets
        SET name=?,
            source=?,
            duration=?,
            auth_profile_id=?
        WHERE id=?
        """,
        (
            name,
            source,
            duration,
            profile_id,
            asset_id,
        ),
    )

    connection.commit()
    connection.close()

    return {
        "ok": True,
    }


# PROFILE_EDIT_API_V4
@app.put("/api/profiles/{profile_id}")
async def edit_profile_v4(profile_id: int, request: Request, u=Depends(auth)):
    data = await request.json()
    c = con()
    r = c.execute("SELECT * FROM auth_profiles WHERE id=?", (profile_id,)).fetchone()
    if not r:
        c.close()
        raise HTTPException(404, "Profil nebyl nalezen")
    name = str(data.get("name", "")).strip()
    auth_type = _profile_type(data.get("auth_type", r["auth_type"]))
    login = str(data.get("login_url", "")).strip()
    target = str(data.get("target_url", "")).strip()
    us = str(data.get("user_selector", "")).strip()
    ps = str(data.get("pass_selector", "")).strip()
    ss = str(data.get("submit_selector", "")).strip()
    if auth_type == "http":
        # the browser's pop-up has no page and no form: only the address and the credentials matter
        login = login or target
        us = us or r["user_selector"] or 'input[name="username"]'
        ps = ps or r["pass_selector"] or 'input[name="password"]'
        ss = ss or r["submit_selector"] or 'button[type="submit"]'
    if not name or not login.startswith(("http://", "https://")) or not target.startswith(("http://", "https://")) or not us or not ps or not ss:
        c.close()
        raise HTTPException(400, "Zkontroluj povinna pole a URL")
    username = str(data.get("username", ""))
    password = str(data.get("password", ""))
    ue = vault.encrypt(username.encode()) if username.strip() else r["username_enc"]
    pe = vault.encrypt(password.encode()) if password else r["password_enc"]
    c.execute("UPDATE auth_profiles SET name=?,login_url=?,target_url=?,username_enc=?,password_enc=?,user_selector=?,pass_selector=?,submit_selector=?,auth_type=? WHERE id=?", (name,login,target,ue,pe,us,ps,ss,auth_type,profile_id))
    c.commit()
    c.close()
    return {"ok": True}

# CARACAL_SCALE_DELETE_V2
@app.put('/api/assets/{asset_id}/scale')
async def save_scale(asset_id:int,request:Request,u=Depends(auth)):
 data=await request.json()
 try:s=float(data.get('scale',1))
 except:raise HTTPException(400,'Neplatne meritko')
 if not .5<=s<=3:raise HTTPException(400,'Meritko musi byt 0.5 az 3.0')
 c=con();c.execute('update assets set scale=? where id=?',(s,asset_id));c.commit();c.close();return {'ok':True}
@app.delete('/api/profiles/{profile_id}')
def drop_profile(profile_id:int,u=Depends(auth)):
 c=con();c.execute('update assets set auth_profile_id=NULL where auth_profile_id=?',(profile_id,));c.execute('delete from auth_profiles where id=?',(profile_id,));c.commit();c.close();return {'ok':True}


# CARACAL_REBOOT_API_V1
@app.post("/api/system/reboot")
async def reboot_device(request: Request, u=Depends(auth)):
    data = await request.json()
    if data.get("confirm") != "REBOOT":
        raise HTTPException(400, "Restart nebyl potvrzen")
    request_reboot()
    return {"ok": True}


# CARACAL_LIVE_CONTROL_V1
import json as _caracal_json
import threading as _caracal_threading
_CARACAL_STATE_FILE = DATA / "player-state.json"
_CARACAL_STATE_LOCK = _caracal_threading.Lock()
def _caracal_default_state():
 return {"current_id":None,"current_name":"","force_id":None,"frozen":False,"remaining":None,"duration":None,"updated":0,"player_online":False}
def _caracal_read_state():
 with _CARACAL_STATE_LOCK:
  try:
   data=_caracal_json.loads(_CARACAL_STATE_FILE.read_text(encoding="utf-8"))
   state=_caracal_default_state();state.update(data);return state
  except Exception:return _caracal_default_state()
def _caracal_write_state(state):
 with _CARACAL_STATE_LOCK:
  tmp=_CARACAL_STATE_FILE.with_suffix(".tmp")
  tmp.write_text(_caracal_json.dumps(state,ensure_ascii=False),encoding="utf-8")
  tmp.replace(_CARACAL_STATE_FILE)
@app.get("/api/player/status")
def caracal_player_status(u=Depends(auth)):
 state=_caracal_read_state();state["player_online"]=(time.time()-float(state.get("updated") or 0))<8;return state
@app.get("/api/player/control-state")
def caracal_player_control_state(req:Request):
 if req.client.host not in ("127.0.0.1","::1"):raise HTTPException(403)
 state=_caracal_read_state();return {"force_id":state.get("force_id"),"frozen":bool(state.get("frozen"))}
@app.post("/api/player/heartbeat")
async def caracal_player_heartbeat(req:Request):
 if req.client.host not in ("127.0.0.1","::1"):raise HTTPException(403)
 data=await req.json();state=_caracal_read_state()
 for key in ("current_id","current_name","remaining","duration"):
  if key in data:state[key]=data[key]
 state["updated"]=time.time();state["player_online"]=True;_caracal_write_state(state);return {"ok":True}
@app.post("/api/player/control")
async def caracal_player_control(req:Request,u=Depends(auth)):
 data=await req.json();action=data.get("action");state=_caracal_read_state()
 if action=="show":
  aid=int(data.get("asset_id"));
  if not rows("SELECT id FROM assets WHERE id=?",(aid,)):raise HTTPException(404,"Položka nebyla nalezena")
  state["force_id"]=aid;state["frozen"]=False
 elif action=="freeze":
  aid=data.get("asset_id") or state.get("current_id")
  if not aid:raise HTTPException(400,"Není vybraná položka")
  state["force_id"]=int(aid);state["frozen"]=True;state["remaining"]=None
 elif action=="unfreeze":
  state["force_id"]=None;state["frozen"]=False
 elif action=="next":
  state["force_id"]=None;state["frozen"]=False;state["remaining"]=0
 else:raise HTTPException(400,"Neznámá akce")
 state["updated"]=time.time();_caracal_write_state(state);return {"ok":True,"state":state}


# CARACAL_LIVE_CONTROL_V2
import json as _cc2_json
import threading as _cc2_threading
_CC2_FILE=DATA/"player-control-v2.json"
_CC2_LOCK=_cc2_threading.Lock()
def _cc2_default():
 return {"command_id":0,"action":"","asset_id":None,"current_id":None,"current_name":"","frozen":False,"remaining":None,"duration":None,"updated":0}
def _cc2_read():
 with _CC2_LOCK:
  try:
   x=_cc2_default();x.update(_cc2_json.loads(_CC2_FILE.read_text(encoding="utf-8")));return x
  except Exception:return _cc2_default()
def _cc2_write(x):
 with _CC2_LOCK:
  t=_CC2_FILE.with_suffix(".tmp");t.write_text(_cc2_json.dumps(x,ensure_ascii=False),encoding="utf-8");t.replace(_CC2_FILE)
@app.get("/api/v2/player/status")
def cc2_status(u=Depends(auth)):
 x=_cc2_read();x["player_online"]=(time.time()-float(x.get("updated") or 0))<8;return x
@app.get("/api/v2/player/command")
def cc2_command(req:Request):
 if req.client.host not in ("127.0.0.1","::1"):raise HTTPException(403)
 x=_cc2_read();return {"command_id":x.get("command_id",0),"action":x.get("action",""),"asset_id":x.get("asset_id")}
@app.post("/api/v2/player/heartbeat")
async def cc2_heartbeat(req:Request):
 if req.client.host not in ("127.0.0.1","::1"):raise HTTPException(403)
 d=await req.json();x=_cc2_read()
 for k in ("current_id","current_name","frozen","remaining","duration"):
  if k in d:x[k]=d[k]
 x["updated"]=time.time();_cc2_write(x);return {"ok":True}
@app.post("/api/v2/player/control")
async def cc2_control(req:Request,u=Depends(auth)):
 d=await req.json();a=str(d.get("action",""));aid=d.get("asset_id")
 if a not in ("show","freeze","unfreeze","next"):raise HTTPException(400,"Neznámá akce")
 if a in ("show","freeze"):
  if aid is None:raise HTTPException(400,"Vyber položku")
  aid=int(aid)
  if not rows("SELECT id FROM assets WHERE id=?",(aid,)):raise HTTPException(404,"Položka nenalezena")
 x=_cc2_read();x["command_id"]=int(x.get("command_id",0))+1;x["action"]=a;x["asset_id"]=int(aid) if aid is not None else None;x["updated"]=time.time();_cc2_write(x);return {"ok":True,"command_id":x["command_id"]}


# CARACAL_KIOSK_FIX_V3
@app.post("/api/v3/player/restart")
def caracal_restart_player_v3(u=Depends(auth)):
 request_player_restart()
 return {"ok":True}
@app.post("/api/v3/player/overlay")
async def caracal_overlay_v3(request:Request,u=Depends(auth)):
 data=await request.json();enabled=bool(data.get("enabled"));state=_cc2_read() if "_cc2_read" in globals() else {}
 state["overlay_enabled"]=enabled
 if "_cc2_write" in globals():_cc2_write(state)
 return {"ok":True,"enabled":enabled}
@app.get("/api/v3/player/overlay-state")
def caracal_overlay_state_v3(u=Depends(auth)):
 state=_cc2_read() if "_cc2_read" in globals() else {}
 return {"enabled":bool(state.get("overlay_enabled",True))}


# CARACAL_PLAYLIST_REORDER_V1
@app.put("/api/assets/reorder")
async def caracal_reorder_assets(request: Request, u=Depends(auth)):
    data = await request.json()
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "Chybí pořadí playlistu")
    try:
        ids = [int(x) for x in ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "Neplatné ID položky")
    existing = [row["id"] for row in rows("SELECT id FROM assets ORDER BY position,id")]
    if len(ids) != len(existing) or set(ids) != set(existing):
        raise HTTPException(400, "Seznam položek neodpovídá playlistu")
    c = con()
    for position, asset_id in enumerate(ids):
        c.execute("UPDATE assets SET position=? WHERE id=?", (position, asset_id))
    c.commit(); c.close()
    return {"ok": True, "ids": ids}


# CARACAL_REORDER_OVERLAY_SIZE_V2
@app.put("/api/playlist/reorder")
async def caracal_playlist_reorder_v2(request: Request, u=Depends(auth)):
    data = await request.json()
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "Chybí pořadí playlistu")
    try:
        ids = [int(value) for value in ids]
    except (TypeError, ValueError):
        raise HTTPException(400, "Neplatné ID položky")
    existing = [row["id"] for row in rows("SELECT id FROM assets ORDER BY position,id")]
    if len(ids) != len(existing) or set(ids) != set(existing):
        raise HTTPException(409, "Playlist se mezitím změnil. Obnov stránku a zkus to znovu.")
    c = con()
    for position, asset_id in enumerate(ids):
        c.execute("UPDATE assets SET position=? WHERE id=?", (position, asset_id))
    c.commit(); c.close()
    return {"ok": True, "ids": ids}

@app.get("/api/v4/player/overlay-settings")
def caracal_overlay_settings_v4(u=Depends(auth)):
    state = _cc2_read() if "_cc2_read" in globals() else {}
    return {"enabled": bool(state.get("overlay_enabled", True)), "size": int(state.get("overlay_size", 16))}

@app.put("/api/v4/player/overlay-settings")
async def caracal_overlay_settings_save_v4(request: Request, u=Depends(auth)):
    data = await request.json()
    state = _cc2_read() if "_cc2_read" in globals() else {}
    enabled = bool(data.get("enabled", state.get("overlay_enabled", True)))
    try:
        size = int(data.get("size", state.get("overlay_size", 16)))
    except (TypeError, ValueError):
        raise HTTPException(400, "Neplatná velikost odpočtu")
    if size < 1:
        raise HTTPException(400, "Velikost odpočtu musí být kladné celé číslo")
    state["overlay_enabled"] = enabled
    state["overlay_size"] = size
    if "_cc2_write" in globals(): _cc2_write(state)
    return {"ok": True, "enabled": enabled, "size": size}


# CARACAL_GRAFANA_TAG_COLLECTION_V1
import json as _grafana_json
import urllib.parse as _grafana_urlparse
import urllib.request as _grafana_urlrequest
import ssl as _grafana_ssl

def _grafana_discover(base_url, tag):
 base=base_url.rstrip('/')
 query=_grafana_urlparse.urlencode({'tag':tag,'type':'dash-db','limit':5000})
 request=_grafana_urlrequest.Request(base+'/api/search?'+query,headers={'Accept':'application/json','User-Agent':'CARACAL/1.0'})
 context=_grafana_ssl._create_unverified_context()
 try:
  with _grafana_urlrequest.urlopen(request,timeout=15,context=context) as response:
   data=_grafana_json.loads(response.read().decode('utf-8'))
 except Exception as error:
  raise HTTPException(400,'Grafana API není dostupné pro Guest účet: '+str(error))
 dashboards=[]
 for item in data:
  if item.get('type')!='dash-db' or not item.get('url'):continue
  dashboards.append({'title':item.get('title') or item.get('uid') or 'Grafana dashboard','url':base+item['url']})
 return dashboards

@app.post('/api/assets/grafana-tag')
def add_grafana_tag_collection(name:str=Form(...),grafana_url:str=Form(...),tag:str=Form(...),duration:int=Form(60),scale:float=Form(1.0),kiosk:bool=Form(True),u=Depends(auth)):
 name=name.strip();grafana_url=grafana_url.strip().rstrip('/');tag=tag.strip()
 if not name or not tag:raise HTTPException(400,'Vyplň název a tag')
 if not grafana_url.startswith(('http://','https://')):raise HTTPException(400,'URL Grafany musí začínat http:// nebo https://')
 if not .5<=scale<=3:raise HTTPException(400,'Měřítko musí být 0.5 až 3.0')
 found=_grafana_discover(grafana_url,tag)
 if not found:raise HTTPException(404,'Pod tímto tagem nebyl nalezen žádný dashboard dostupný pro Guest účet')
 config={'grafana_url':grafana_url,'tag':tag,'kiosk':bool(kiosk)}
 c=con();position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM assets').fetchone()[0]
 c.execute('INSERT INTO assets(name,kind,source,duration,position,auth_profile_id,scale) VALUES(?,?,?,?,?,NULL,?)',(name,'grafana-tag',_grafana_json.dumps(config,ensure_ascii=False),max(5,duration),position,scale));c.commit();c.close()
 return {'ok':True,'dashboards':len(found)}

@app.post('/api/grafana/discover')
def test_grafana_tag(grafana_url:str=Form(...),tag:str=Form(...),u=Depends(auth)):
 result=_grafana_discover(grafana_url.strip(),tag.strip())
 return {'ok':True,'count':len(result),'dashboards':result[:50]}

def expanded_player_playlist():
 output=[]
 for asset in rows('SELECT * FROM assets ORDER BY position,id'):
  if asset.get('kind')!='grafana-tag':
   output.append(asset);continue
  try:
   config=_grafana_json.loads(asset.get('source') or '{}')
   discovered=_grafana_discover(config.get('grafana_url',''),config.get('tag',''))
   for index,dashboard in enumerate(discovered):
    separator='&' if '?' in dashboard['url'] else '?'
    url=dashboard['url']
    if config.get('kiosk',True):url=url+separator+'kiosk=1'
    output.append({'id':int(asset['id'])*100000+index,'parent_id':asset['id'],'name':asset['name']+' · '+dashboard['title'],'kind':'web','source':url,'duration':asset['duration'],'position':asset['position'],'auth_profile_id':None,'scale':asset.get('scale') or 1.0})
  except Exception as error:
   print('Grafana collection error',asset.get('id'),error,flush=True)
 return output

@app.get('/api/player/playlist-expanded')
def expanded_player_playlist_route(req:Request):
 _local_only(req);return expanded_player_playlist()


# CARACAL_GRAFANA_COLLECTION_EDIT_V1
@app.put('/api/grafana-collections/{asset_id}')
async def update_grafana_collection(asset_id:int,request:Request,u=Depends(auth)):
 data=await request.json();c=con();row=c.execute('SELECT * FROM assets WHERE id=? AND kind=?',(asset_id,'grafana-tag')).fetchone()
 if not row:c.close();raise HTTPException(404,'Grafana kolekce nebyla nalezena')
 try:scale=float(data.get('scale',row['scale'] or 1.0));duration=max(5,int(data.get('duration',row['duration'] or 60)))
 except (TypeError,ValueError):c.close();raise HTTPException(400,'Neplatné měřítko nebo doba zobrazení')
 if not .5<=scale<=3:c.close();raise HTTPException(400,'Měřítko musí být 50 až 300 %')
 config=_grafana_json.loads(row['source'] or '{}');name=str(data.get('name',row['name'])).strip() or row['name'];tag=str(data.get('tag',config.get('tag',''))).strip();grafana_url=str(data.get('grafana_url',config.get('grafana_url',''))).strip().rstrip('/');kiosk=bool(data.get('kiosk',config.get('kiosk',True)))
 if not grafana_url.startswith(('http://','https://')):c.close();raise HTTPException(400,'URL Grafany musí začínat http:// nebo https://')
 if not tag:c.close();raise HTTPException(400,'Tag nesmí být prázdný')
 found=_grafana_discover(grafana_url,tag)
 if not found:c.close();raise HTTPException(404,'Pod tímto tagem nebyl nalezen žádný dashboard')
 config={'grafana_url':grafana_url,'tag':tag,'kiosk':kiosk};c.execute('UPDATE assets SET name=?,source=?,duration=?,scale=? WHERE id=?',(name,_grafana_json.dumps(config,ensure_ascii=False),duration,scale,asset_id));c.commit();c.close();return {'ok':True,'dashboards':len(found),'scale':scale}


# CARACAL_GRAFANA_LIVE_SELECT_V1
@app.get('/api/v5/player/live-options')
def caracal_live_options_v5(u=Depends(auth)):
    raw=rows('SELECT * FROM assets ORDER BY position,id')
    expanded=expanded_player_playlist()
    options=[]
    for asset in raw:
        if asset.get('kind')=='grafana-tag':
            children=[x for x in expanded if x.get('parent_id')==asset['id']]
            options.append({'type':'collection','id':asset['id'],'name':asset['name'],'count':len(children)})
            for child in children:
                options.append({'type':'dashboard','id':child['id'],'parent_id':asset['id'],'name':child['name']})
        else:
            options.append({'type':'asset','id':asset['id'],'name':asset['name'],'kind':asset['kind']})
    return options

@app.get('/api/v5/player/command')
def caracal_command_v5(req:Request):
    if req.client.host not in ('127.0.0.1','::1'):raise HTTPException(403)
    state=_cc2_read();return {'command_id':state.get('v5_command_id',0),'action':state.get('v5_action',''),'item_id':state.get('v5_item_id'),'collection_id':state.get('v5_collection_id')}

@app.post('/api/v5/player/control')
async def caracal_control_v5(req:Request,u=Depends(auth)):
    data=await req.json();action=str(data.get('action',''));item_id=data.get('item_id');collection_id=data.get('collection_id')
    if action not in ('show','freeze','freeze_collection','unfreeze','next'):raise HTTPException(400,'Neznámá akce')
    expanded=expanded_player_playlist();raw=rows('SELECT * FROM assets ORDER BY position,id')
    if action in ('show','freeze'):
        try:item_id=int(item_id)
        except:raise HTTPException(400,'Vyber dashboard nebo položku')
        if not any(int(x['id'])==item_id for x in expanded):raise HTTPException(404,'Vybraná položka už není dostupná')
    if action=='freeze_collection':
        try:collection_id=int(collection_id)
        except:raise HTTPException(400,'Vyber Grafana kolekci')
        if not any(x['id']==collection_id and x['kind']=='grafana-tag' for x in raw):raise HTTPException(404,'Grafana kolekce nebyla nalezena')
        if not any(x.get('parent_id')==collection_id for x in expanded):raise HTTPException(404,'Kolekce neobsahuje žádné dostupné dashboardy')
    state=_cc2_read();state['v5_command_id']=int(state.get('v5_command_id',0))+1;state['v5_action']=action;state['v5_item_id']=item_id;state['v5_collection_id']=collection_id;state['updated']=time.time();_cc2_write(state)
    return {'ok':True,'command_id':state['v5_command_id']}


# CARACAL_COLLECTION_CONTROL_FIX_V2
@app.post('/api/v6/player/control')
async def caracal_control_v6(req:Request,u=Depends(auth)):
 data=await req.json();action=str(data.get('action',''));item_id=data.get('item_id');collection_id=data.get('collection_id')
 if action not in ('show','freeze','show_collection','freeze_collection','unfreeze','next'):raise HTTPException(400,'Neznámá akce')
 expanded=expanded_player_playlist();raw=rows('SELECT * FROM assets ORDER BY position,id')
 if action in ('show','freeze'):
  try:item_id=int(item_id)
  except:raise HTTPException(400,'Vyber položku nebo dashboard')
  if not any(int(x['id'])==item_id for x in expanded):raise HTTPException(404,'Vybraná položka není dostupná')
 if action in ('show_collection','freeze_collection'):
  try:collection_id=int(collection_id)
  except:raise HTTPException(400,'Vyber Grafana kolekci')
  if not any(x['id']==collection_id and x['kind']=='grafana-tag' for x in raw):raise HTTPException(404,'Grafana kolekce nebyla nalezena')
  if not any(x.get('parent_id')==collection_id for x in expanded):raise HTTPException(404,'Kolekce neobsahuje dostupné dashboardy')
 state=_cc2_read();state['v6_command_id']=int(state.get('v6_command_id',0))+1;state['v6_action']=action;state['v6_item_id']=item_id;state['v6_collection_id']=collection_id;state['updated']=time.time();_cc2_write(state)
 return {'ok':True,'command_id':state['v6_command_id']}
@app.get('/api/v6/player/command')
def caracal_command_v6(req:Request):
 if req.client.host not in ('127.0.0.1','::1'):raise HTTPException(403)
 s=_cc2_read();return {'command_id':s.get('v6_command_id',0),'action':s.get('v6_action',''),'item_id':s.get('v6_item_id'),'collection_id':s.get('v6_collection_id'),'restart_id':s.get('restart_player_request',0)}


# CARACAL_SCALE_PERSIST_FIX_V7
@app.put('/api/v7/assets/{asset_id}/scale')
async def caracal_save_scale_v7(asset_id:int,request:Request,u=Depends(auth)):
 data=await request.json()
 try:scale=float(data.get('scale'))
 except (TypeError,ValueError):raise HTTPException(400,'Neplatné měřítko')
 if scale<0.5 or scale>3.0:raise HTTPException(400,'Měřítko musí být 50 až 300 %')
 c=con();asset=c.execute('SELECT id,kind FROM assets WHERE id=?',(asset_id,)).fetchone()
 if not asset:c.close();raise HTTPException(404,'Položka nebyla nalezena')
 if asset['kind'] not in ('web','grafana-tag'):c.close();raise HTTPException(400,'Měřítko lze měnit jen u webu nebo Grafana kolekce')
 c.execute('UPDATE assets SET scale=? WHERE id=?',(scale,asset_id));c.commit()
 saved=c.execute('SELECT scale FROM assets WHERE id=?',(asset_id,)).fetchone()['scale'];c.close()
 return {'ok':True,'asset_id':asset_id,'scale':float(saved)}


# CARACAL_ADD_WEB_SCALE_FIX_V9
@app.post('/api/v9/assets/web')
async def caracal_add_web_v9(request:Request,u=Depends(auth)):
 data=await request.json()
 name=str(data.get('name','')).strip()
 source=str(data.get('source','')).strip()
 if not name:raise HTTPException(400,'Vyplň název')
 if not source.startswith(('http://','https://')):raise HTTPException(400,'URL musí začínat http:// nebo https://')
 try:duration=max(5,int(data.get('duration',30)))
 except (TypeError,ValueError):raise HTTPException(400,'Neplatná doba zobrazení')
 raw_scale=str(data.get('scale','1')).strip().replace(',','.')
 try:scale=float(raw_scale)
 except (TypeError,ValueError):raise HTTPException(400,'Neplatné měřítko')
 if scale<0.5 or scale>3.0:raise HTTPException(400,'Měřítko musí být 0,5 až 3,0')
 raw_profile=data.get('auth_profile_id')
 try:profile_id=int(raw_profile) if raw_profile not in (None,'','null') else None
 except (TypeError,ValueError):raise HTTPException(400,'Neplatný přihlašovací profil')
 if profile_id is not None and not rows('SELECT id FROM auth_profiles WHERE id=?',(profile_id,)):raise HTTPException(404,'Přihlašovací profil nebyl nalezen')
 c=con();position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM assets').fetchone()[0]
 cursor=c.execute('INSERT INTO assets(name,kind,source,duration,position,auth_profile_id,scale) VALUES(?,?,?,?,?,?,?)',(name,'web',source,duration,position,profile_id,scale));asset_id=cursor.lastrowid;c.commit()
 saved=c.execute('SELECT scale FROM assets WHERE id=?',(asset_id,)).fetchone()['scale'];c.close()
 return {'ok':True,'id':asset_id,'scale':float(saved)}


# CARACAL_FLEET_API_V2
# Local API for the CARACAL Fleet Agent (CARACAL Fleet repository, docs/LOCAL-API.md).
# Authenticated with the shared key in /etc/caracal-fleet-key, written by the agent when it is enrolled.
# Replaces the hand-applied CARACAL_FLEET_API_V1 patch: the snapshot returns the live v2 player state.
import hmac as _fleet_hmac
_FLEET_KEY_FILE=Path(os.getenv('CARACAL_FLEET_KEY_FILE','/etc/caracal-fleet-key'))
_FLEET_MEDIA_MAX=2*1024**3
_FLEET_IMAGE={'.png','.jpg','.jpeg','.webp','.gif'};_FLEET_VIDEO={'.mp4','.webm','.mkv'}
def _fleet_auth(req:Request):
 try:key=_FLEET_KEY_FILE.read_text().strip()
 except OSError:raise HTTPException(503,'Fleet Agent is not configured')
 if not key or not _fleet_hmac.compare_digest(req.headers.get('X-Fleet-Key',''),key):raise HTTPException(401,'Invalid Fleet key')
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

@app.get('/api/fleet/v1/snapshot')
def fleet_snapshot(req:Request):
 _fleet_auth(req);state=_cc2_read();state['player_online']=(time.time()-float(state.get('updated') or 0))<8
 player={k:state.get(k) for k in ('current_id','current_name','frozen','collection_frozen','collection_id','remaining','duration','updated','player_online')}
 requests={'reboot':int(state.get('reboot_request',0)),'restart_player':int(state.get('restart_player_request',0))}
 return {'api_version':2,'runtime':RUNTIME,'version':_caracal_version(),'requests':requests,'assets':rows('SELECT * FROM assets ORDER BY position,id'),'profiles':rows(_FLEET_PROFILE_COLUMNS+' ORDER BY name COLLATE NOCASE,id'),'player':player,'notifications':_fleet_notifications()}

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
 duration=_fleet_duration(d.get('duration',30));scale=_fleet_scale(d.get('scale',1));profile_id=_fleet_profile_id(d.get('auth_profile_id'))
 c=con();q=c.execute('INSERT INTO assets(name,kind,source,duration,position,scale,auth_profile_id) VALUES(?,?,?,?,?,?,?)',(name,'web',source,duration,_fleet_position(c),scale,profile_id));c.commit();c.close()
 return {'ok':True,'id':q.lastrowid}

@app.post('/api/fleet/v1/assets/grafana-tag')
async def fleet_add_grafana_tag(req:Request):
 _fleet_auth(req);d=await req.json();name=str(d.get('name','')).strip()
 if not name:raise HTTPException(400,'Name must not be empty')
 config=_fleet_grafana_config(d);duration=_fleet_duration(d.get('duration',60));scale=_fleet_scale(d.get('scale',1))
 c=con();q=c.execute('INSERT INTO assets(name,kind,source,duration,position,auth_profile_id,scale) VALUES(?,?,?,?,?,NULL,?)',(name,'grafana-tag',_grafana_json.dumps(config,ensure_ascii=False),duration,_fleet_position(c),scale));c.commit();c.close()
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
  try:current=_grafana_json.loads(a['source'] or '{}')
  except ValueError:current={}
  source=_grafana_json.dumps(_fleet_grafana_config(d,current),ensure_ascii=False)
 c=con();c.execute('UPDATE assets SET name=?,source=?,duration=?,scale=?,auth_profile_id=? WHERE id=?',(name,source,duration,scale,profile_id,asset_id));c.commit();c.close()
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


# CARACAL_NOTIFY_V1
# On-screen notifications. Apps send them to /api/notify/v1 with their own token (one token per app, revocable,
# rate limited). The app keeps a single queue and the overlay shows one notification at a time, asking
# GET /api/notify/overlay what to show. Queue rules: higher level first, the same 'key' replaces a waiting
# notification, a full queue drops the oldest of the lowest level, waiting notifications expire.
# See docs/NOTIFICATIONS.md.
import base64 as _ntf_base64,hashlib as _ntf_hashlib,re as _ntf_re,threading as _ntf_threading,urllib.parse as _ntf_urlparse
_NTF_LOCK=_ntf_threading.Lock()
_NTF_LEVELS={'info':1,'success':1,'warning':2,'critical':3}
_NTF_CRITICAL={'critical','crit','error','err','fatal','emergency','alert','disaster','high','highest','blocker','immediate','page','urgent','max','down','5'}
_NTF_WARNING={'warning','warn','average','degraded','major','4'}
_NTF_SUCCESS={'success','ok','resolved','up','good','recovered'}
_NTF_POSITIONS=('top-right','top-left','top','bottom-right','bottom-left','bottom','center')
_NTF_DEFAULTS={'enabled':'1','position':'top-right','duration':'8','max_queue':'20','scale':'100','sound':'off','volume':'70','sound_device':'','history_max':'500','history_days':'7'}
# sound: off, critical (critical only), warning (warning and critical), all; the overlay plays a chime per level
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
# notifications.sound: NULL = by the sound setting, 0 = silent, 1 = always (unless sound is off)
if 'sound' not in [r[1] for r in c.execute('PRAGMA table_info(notifications)').fetchall()]:c.execute('ALTER TABLE notifications ADD COLUMN sound INTEGER')
c.commit();c.close()
# notifications.done: 0 waiting or on screen, 1 shown, 2 dropped or expired, 3 removed by the administrator

def _ntf_hash(token):return _ntf_hashlib.sha256(token.encode()).hexdigest()
def _ntf_settings():
 s=dict(_NTF_DEFAULTS);s.update({r['key']:r['value'] for r in rows('SELECT key,value FROM notify_settings')})
 return {'enabled':s['enabled']=='1','position':s['position'] if s['position'] in _NTF_POSITIONS else 'top-right','duration':int(s['duration']),'max_queue':int(s['max_queue']),'scale':int(s['scale']),'sound':s['sound'] if s['sound'] in _NTF_SOUNDS else 'off','volume':int(s['volume']),'sound_device':s['sound_device'],'history_max':int(s['history_max']),'history_days':int(s['history_days'])}
def _ntf_text(value,limit):
 text=_ntf_re.sub(r'[\x00-\x08\x0b-\x1f\x7f]','',str(value if value is not None else '')).strip()
 text=_ntf_re.sub(r'\n{3,}','\n\n',text)
 return text[:limit-1]+'…' if len(text)>limit else text
def _ntf_level(value,default='info'):
 v=str(value if value is not None else '').strip().lower()
 if v in _NTF_CRITICAL:return 'critical'
 if v in _NTF_WARNING:return 'warning'
 if v in _NTF_SUCCESS:return 'success'
 return 'info' if v in ('info','information','notice','low','min','default','1','2','3') else default
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
 return {'title':title,'message':message,'level':level,'key':_ntf_text(d.get('key') or d.get('dedup_key') or d.get('tag') or headers.get('x-key'),200) or None,'duration':_ntf_duration(d.get('duration') or headers.get('x-duration')),'source':_ntf_text(d.get('source') or d.get('app'),60) or None,'sound':_ntf_sound(d['sound'] if 'sound' in d else headers.get('x-sound'))}

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
      c.execute('UPDATE notifications SET title=?,message=?,level=?,priority=?,source=?,duration=MAX(duration,?) WHERE id=?',(it['title'],it['message'],it['level'],priority,src,duration,row['id']))
      result['updated']+=1;result['ids'].append(row['id']);continue
    own=c.execute('SELECT COUNT(*) FROM notifications WHERE done=0 AND shown_at IS NULL AND COALESCE(token_id,0)=?',(token_id or 0,)).fetchone()[0]
    if own>=_NTF_PER_SOURCE and not _ntf_evict(c,priority,token_id or 0):result['dropped']+=1;continue
    # the loop also trims a queue that is longer than a newly lowered limit
    waiting=lambda:c.execute('SELECT COUNT(*) FROM notifications WHERE done=0 AND shown_at IS NULL').fetchone()[0]
    while waiting()>=s['max_queue'] and _ntf_evict(c,priority):pass
    if waiting()>=s['max_queue']:result['dropped']+=1;continue
    q=c.execute('INSERT INTO notifications(token_id,source,title,message,level,priority,key,duration,created,sound) VALUES(?,?,?,?,?,?,?,?,?,?)',(token_id,src,it['title'],it['message'],it['level'],priority,it['key'],duration,now,it.get('sound')))
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
  try:token=_ntf_base64.b64decode(header[6:].strip()).decode().partition(':')[2].strip()
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
  try:return _grafana_json.loads(text)
  except ValueError:raise HTTPException(400,'Invalid JSON')
 if 'x-www-form-urlencoded' in ctype:return {k:v[-1] for k,v in _ntf_urlparse.parse_qs(text).items() if k!='token'}
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
  current['sound']=s['sound']!='off' and cur['sound']!=0 and (cur['sound']==1 or (cur['priority'] or 1)>=_NTF_SOUNDS[s['sound']])
 return {'enabled':s['enabled'],'position':s['position'],'scale':s['scale'],'volume':s['volume'],'sound_device':s['sound_device'],'current':current,'waiting':waiting}

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
 if s['position'] not in _NTF_POSITIONS:raise HTTPException(400,'Neplatná pozice')
 if not 3<=s['duration']<=120:raise HTTPException(400,'Doba zobrazení musí být 3 až 120 s')
 if not 1<=s['max_queue']<=200:raise HTTPException(400,'Fronta musí mít 1 až 200 míst')
 if not 50<=s['scale']<=300:raise HTTPException(400,'Velikost musí být 50 až 300 %')
 if s['sound'] not in _NTF_SOUNDS:raise HTTPException(400,'Neplatné nastavení zvuku')
 if not 0<=s['volume']<=100:raise HTTPException(400,'Hlasitost musí být 0 až 100 %')
 # ALSA device name passed to aplay -D, e.g. default, hdmi:CARD=vc4hdmi0,DEV=0, plughw:1,0
 if not _ntf_re.fullmatch(r'[A-Za-z0-9:=,._-]{0,100}',s['sound_device']):raise HTTPException(400,'Neplatný název zvukového zařízení')
 if not 50<=s['history_max']<=5000:raise HTTPException(400,'Historie musí mít 50 až 5000 záznamů')
 if not 1<=s['history_days']<=90:raise HTTPException(400,'Historii jde držet 1 až 90 dní')
 before=_ntf_settings();c=con()
 for k in _NTF_DEFAULTS:c.execute('INSERT OR REPLACE INTO notify_settings(key,value) VALUES(?,?)',(k,('1' if s[k] else '0') if k=='enabled' else str(s[k])))
 c.commit();c.close()
 changes=', '.join(f'{k}: {before[k]} → {s[k]}' for k in _NTF_DEFAULTS if before[k]!=s[k])
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


# CARACAL_NOTIFY_WATCHERS_V1
# Watchers: the node itself asks another app's REST API (new tickets, issues, orders, ...) every few seconds and
# puts a notification into the queue for every item whose ID it has not seen yet. The first check only remembers
# what is already there. Credentials are encrypted with the vault key and never returned by the API.
_WCH_LOCK=_ntf_threading.Lock()
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
  return '' if v is None else _grafana_json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else str(v)
 return _ntf_re.sub(r'\{([^{}]+)\}',value,str(template or ''))
def _wch_credentials(row):
 try:return _grafana_json.loads(vault.decrypt(row['credentials_enc']).decode()) if row and row['credentials_enc'] else {}
 except Exception:return {}

def _wch_error_text(error):
 # the most useful part of an error response: RFC 6749 fields, common JSON shapes, or the text of a page
 try:raw=error.read(65536).decode('utf-8','replace')
 except Exception:return ''
 try:
  d=_grafana_json.loads(raw)
  if isinstance(d,dict):
   nested=d.get('error') if isinstance(d.get('error'),dict) else {}
   parts=[d.get('error') if isinstance(d.get('error'),str) else None,d.get('error_description'),nested.get('message'),d.get('message'),d.get('detail'),d.get('title')]
   text=' – '.join(dict.fromkeys(str(x) for x in parts if x))
   if text:return text[:300]
 except ValueError:pass
 text=_ntf_re.sub(r'\s+',' ',_ntf_re.sub(r'<(script|style)[^>]*>.*?</\1>|<[^>]+>',' ',raw,flags=_ntf_re.S|_ntf_re.I)).strip()
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
 for key,value in _ntf_urlparse.parse_qsl(w['oauth_extra'] or ''):data.setdefault(key,value)
 headers={'Accept':'application/json','Content-Type':'application/x-www-form-urlencoded','User-Agent':'CARACAL/1.0'}
 client_id=w['oauth_client_id'] or '';client_secret=creds.get('client_secret') or ''
 if w['oauth_client_auth']=='basic':
  q=lambda x:_ntf_urlparse.quote(x,safe='')
  headers['Authorization']='Basic '+_ntf_base64.b64encode(f'{q(client_id)}:{q(client_secret)}'.encode()).decode()
 else:
  if client_id:data['client_id']=client_id
  if client_secret:data['client_secret']=client_secret
 context=None if w['verify_tls'] else _grafana_ssl._create_unverified_context()
 try:
  request=_grafana_urlrequest.Request(w['oauth_token_url'],data=_ntf_urlparse.urlencode(data).encode(),headers=headers,method='POST')
  with _grafana_urlrequest.urlopen(request,timeout=20,context=context) as response:body=response.read(1024**2)
 except _grafana_urlrequest.HTTPError as error:
  # e.g. "invalid_client – Client authentication failed"; servers that do not follow RFC 6749 get their text shown
  sent=', '.join(sorted(data))+(', Basic' if 'Authorization' in headers else '')
  raise ValueError(f'OAuth2 token: HTTP {error.code} {_wch_error_text(error) or error.reason} (odesláno: {sent})')
 except Exception as error:raise ValueError(f'OAuth2 token nedostupný: {getattr(error,"reason",None) or error}')
 try:answer=_grafana_json.loads(body.decode('utf-8','replace'));token=str(answer['access_token'])
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
    c=con();c.execute('UPDATE notify_watchers SET credentials_enc=? WHERE id=?',(vault.encrypt(_grafana_json.dumps(stored).encode()),watcher_id));c.commit();c.close()
 return token

def _wch_fetch(w,creds,watcher_id=None):
 context=None if w['verify_tls'] else _grafana_ssl._create_unverified_context()
 def get(fresh=False):
  headers={'Accept':'application/json','User-Agent':'CARACAL/1.0'};secret=str(creds.get('secret') or '')
  if w['auth_type']=='bearer':headers['Authorization']='Bearer '+secret
  elif w['auth_type']=='basic':headers['Authorization']='Basic '+_ntf_base64.b64encode(f"{creds.get('username') or ''}:{secret}".encode()).decode()
  elif w['auth_type']=='header':headers[w['auth_header'] or 'Authorization']=secret
  elif w['auth_type']=='oauth2':headers['Authorization']='Bearer '+_wch_oauth_token(w,creds,watcher_id,fresh)
  with _grafana_urlrequest.urlopen(_grafana_urlrequest.Request(w['url'],headers=headers),timeout=20,context=context) as response:return response.read(_WCH_RESPONSE_MAX+1)
 try:
  try:raw=get()
  except _grafana_urlrequest.HTTPError as error:
   # a cached OAuth2 token may have been revoked before it expired: one more try with a new one
   if error.code!=401 or w['auth_type']!='oauth2':raise
   raw=get(fresh=True)
 except ValueError:raise
 except _grafana_urlrequest.HTTPError as error:raise ValueError(f'HTTP {error.code} {_wch_error_text(error) or error.reason}')
 except Exception as error:raise ValueError(f'Nedostupné: {getattr(error,"reason",None) or error}')
 if len(raw)>_WCH_RESPONSE_MAX:raise ValueError('Odpověď je větší než 5 MB')
 try:data=_grafana_json.loads(raw.decode('utf-8',errors='replace'))
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
  seen=_grafana_json.loads(w['seen'] or '[]');known=set(seen);new=[(i,it) for i,it in items if i not in known]
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
  c=con();c.execute('UPDATE notify_watchers SET seen=?,initialized=1,last_check=?,last_error=NULL,last_count=?,last_new=? WHERE id=? AND revision=?',(_grafana_json.dumps(seen),now,len(items),len(new) if w['initialized'] else 0,w['id'],w['revision']));c.commit();c.close()
  return {'ok':True,'count':len(items),'new':len(new) if w['initialized'] else 0,'sent':sent,'first':not w['initialized']}

def _wch_loop():
 while True:
  try:
   for w in rows('SELECT * FROM notify_watchers WHERE enabled=1'):
    if time.time()>=float(w['last_check'] or 0)+int(w['interval'] or 60):_wch_check(w)
  except Exception as error:print('watcher error',error,flush=True)
  time.sleep(5)
@app.on_event('startup')
def _wch_start():_ntf_threading.Thread(target=_wch_loop,daemon=True,name='caracal-watchers').start()

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
 out['credentials_enc']=vault.encrypt(_grafana_json.dumps(creds).encode()) if creds else None
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


# CARACAL_FLEET_NOTIFY_V1
# Notifications managed by CARACAL Fleet: settings, the queue and watchers (agent contract: docs/LOCAL-API.md in the
# CARACAL Fleet repository). Authenticated with the Fleet key; the node's audit log names "CARACAL Fleet".
_FLEET_ACTOR='CARACAL Fleet'
def _fleet_notifications():
 # what the snapshot reports: never tokens, credentials or the IDs a watcher has seen
 waiting=rows('SELECT COUNT(*) AS n FROM notifications WHERE done=0 AND shown_at IS NULL')[0]['n']
 current=rows('SELECT title,message,level FROM notifications WHERE done=0 AND shown_at IS NOT NULL AND shown_at+duration>? LIMIT 1',(time.time(),))
 return {'settings':_ntf_settings(),'waiting':waiting,'current':current[0] if current else None,
  'watchers':rows(f'SELECT {_WCH_COLUMNS} FROM notify_watchers ORDER BY name COLLATE NOCASE,id'),
  'tokens':rows('SELECT COUNT(*) AS n FROM notify_tokens WHERE enabled=1')[0]['n']}
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
