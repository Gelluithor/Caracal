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
c.commit();c.close()
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
def profiles(u=Depends(auth)):return rows('SELECT id,name,login_url,target_url,user_selector,pass_selector,submit_selector FROM auth_profiles')
@app.post('/api/profiles')
def profile(name:str=Form(...),login_url:str=Form(...),target_url:str=Form(...),username:str=Form(...),password:str=Form(...),user_selector:str=Form('input[name="name"],input[name="username"]'),pass_selector:str=Form('input[name="password"]'),submit_selector:str=Form('button[type="submit"],input[type="submit"]'),u=Depends(auth)):
 c=con();c.execute('INSERT INTO auth_profiles(name,login_url,target_url,username_enc,password_enc,user_selector,pass_selector,submit_selector) VALUES(?,?,?,?,?,?,?,?)',(name,login_url,target_url,vault.encrypt(username.encode()),vault.encrypt(password.encode()),user_selector,pass_selector,submit_selector));c.commit();c.close();return {'ok':1}
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
    login = str(data.get("login_url", "")).strip()
    target = str(data.get("target_url", "")).strip()
    us = str(data.get("user_selector", "")).strip()
    ps = str(data.get("pass_selector", "")).strip()
    ss = str(data.get("submit_selector", "")).strip()
    if not name or not login.startswith(("http://", "https://")) or not target.startswith(("http://", "https://")) or not us or not ps or not ss:
        c.close()
        raise HTTPException(400, "Zkontroluj povinna pole a URL")
    username = str(data.get("username", ""))
    password = str(data.get("password", ""))
    ue = vault.encrypt(username.encode()) if username.strip() else r["username_enc"]
    pe = vault.encrypt(password.encode()) if password else r["password_enc"]
    c.execute("UPDATE auth_profiles SET name=?,login_url=?,target_url=?,username_enc=?,password_enc=?,user_selector=?,pass_selector=?,submit_selector=? WHERE id=?", (name,login,target,ue,pe,us,ps,ss,profile_id))
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
_FLEET_PROFILE_COLUMNS='SELECT id,name,login_url,target_url,user_selector,pass_selector,submit_selector FROM auth_profiles'
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
 return {'api_version':2,'runtime':RUNTIME,'version':_caracal_version(),'requests':requests,'assets':rows('SELECT * FROM assets ORDER BY position,id'),'profiles':rows(_FLEET_PROFILE_COLUMNS+' ORDER BY name COLLATE NOCASE,id'),'player':player}

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
 c=con();q=c.execute('INSERT INTO auth_profiles(name,login_url,target_url,username_enc,password_enc,user_selector,pass_selector,submit_selector) VALUES(?,?,?,?,?,?,?,?)',(f['name'],f['login_url'],f['target_url'],vault.encrypt(username.encode()),vault.encrypt(password.encode()),f['user_selector'],f['pass_selector'],f['submit_selector']));c.commit();c.close()
 return {'ok':True,'id':q.lastrowid}

@app.put('/api/fleet/v1/profiles/{profile_id}')
async def fleet_update_profile(profile_id:int,req:Request):
 # empty username or password = keep the stored one
 _fleet_auth(req);d=await req.json();r=rows('SELECT * FROM auth_profiles WHERE id=?',(profile_id,))
 if not r:raise HTTPException(404,'Login profile not found')
 f=_fleet_profile_fields(d,r[0]);username=str(d.get('username') or '');password=str(d.get('password') or '')
 ue=vault.encrypt(username.encode()) if username.strip() else r[0]['username_enc'];pe=vault.encrypt(password.encode()) if password else r[0]['password_enc']
 c=con();c.execute('UPDATE auth_profiles SET name=?,login_url=?,target_url=?,username_enc=?,password_enc=?,user_selector=?,pass_selector=?,submit_selector=? WHERE id=?',(f['name'],f['login_url'],f['target_url'],ue,pe,f['user_selector'],f['pass_selector'],f['submit_selector'],profile_id));c.commit();c.close()
 return {'ok':True}

@app.delete('/api/fleet/v1/profiles/{profile_id}')
def fleet_delete_profile(profile_id:int,req:Request):
 # pages that used the profile stay in the playlist without automatic login
 _fleet_auth(req)
 if not rows('SELECT id FROM auth_profiles WHERE id=?',(profile_id,)):raise HTTPException(404,'Login profile not found')
 c=con();n=c.execute('UPDATE assets SET auth_profile_id=NULL WHERE auth_profile_id=?',(profile_id,)).rowcount;c.execute('DELETE FROM auth_profiles WHERE id=?',(profile_id,));c.commit();c.close()
 return {'ok':True,'unassigned':n}
