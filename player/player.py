import os,time,re,subprocess,requests
from playwright.sync_api import sync_playwright
BASE=os.getenv('CARACAL_BASE','http://127.0.0.1:8080');PROFILE=os.getenv('CARACAL_PROFILE','/var/lib/caracal/chromium')
# the app's local endpoints want the key from its data directory (only processes of this device can read it)
LOCAL_KEY_FILE=os.getenv('CARACAL_LOCAL_KEY_FILE',os.path.join(os.getenv('CARACAL_DATA','/var/lib/caracal'),'local.key'))
_local_key=None
def local_headers():
 global _local_key
 if not _local_key:
  try:_local_key=open(LOCAL_KEY_FILE).read().strip()
  except OSError:return {}
 return {'X-Caracal-Local':_local_key}
# one kept-alive connection to the app instead of a new one for every request (several a second)
session=requests.Session()
def get(path,default=None):
 global _local_key
 try:
  r=session.get(BASE+path,timeout=8,headers=local_headers())
  if r.status_code==403:_local_key=None   # the key was created again (fresh data directory)
  r.raise_for_status();return r.json()
 except Exception as e:print('GET',path,e,flush=True);return default
def post(path,data):
 try:session.post(BASE+path,json=data,timeout=5,headers=local_headers())
 except Exception as e:print('POST',path,e,flush=True)
def meminfo():
 out={}
 try:
  for line in open('/proc/meminfo'):
   key,value=line.split(':',1);out[key]=int(value.split()[0])//1024
 except (OSError,ValueError):pass
 return out
# Small boards (Raspberry Pi 4 with 1 GB): Chromium in its low-end mode with fewer renderer processes, and a
# browser restart between two items when memory runs out (long-running dashboards slowly take more).
TOTAL_MB=meminfo().get('MemTotal',0)
LOW_MEMORY=os.getenv('CARACAL_LOW_MEMORY','auto').lower() in ('1','true','yes') or (os.getenv('CARACAL_LOW_MEMORY','auto').lower()=='auto' and 0<TOTAL_MB<=1536)
def chromium_args(w,h,rect=None):
 # rect: the main area of a layout with zones (an ordinary window without a frame instead of the full screen)
 place=[f'--window-position={rect[0]},{rect[1]}',f'--window-size={rect[2]},{rect[3]}'] if rect else ['--kiosk','--start-fullscreen','--start-maximized','--window-position=0,0',f'--window-size={w},{h}']
 args=place+['--force-device-scale-factor=1','--disable-blink-features=AutomationControlled','--no-first-run','--no-default-browser-check','--noerrdialogs','--disable-infobars','--disable-session-crashed-bubble','--autoplay-policy=no-user-gesture-required','--password-store=basic','--use-mock-keychain','--disable-dev-shm-usage','--no-sandbox',
  # nothing a signage screen needs: background downloads, updates of components, sync, extensions, translation
  '--disable-background-networking','--disable-component-update','--disable-sync','--disable-extensions','--disable-default-apps','--disable-breakpad',f'--disk-cache-size={32 if LOW_MEMORY else 64}000000']
 features=['Translate','MediaRouter','OptimizationHints','AutofillServerCommunication','CalculateNativeWinOcclusion']
 if LOW_MEMORY:
  args+=['--enable-low-end-device-mode','--renderer-process-limit=2','--process-per-site']
  features+=['site-per-process','IsolateOrigins','BackForwardCache']
 args.append('--disable-features='+','.join(features))
 return args+[x for x in os.getenv('CARACAL_CHROMIUM_FLAGS','').split() if x.startswith('--')]
def memory_low():
 m=meminfo();total=m.get('MemTotal',0);available=m.get('MemAvailable',total)
 return bool(total) and available<max(80,total*.08)
def display_size():
 try:
  out=subprocess.check_output(['xrandr','--current'],text=True);m=re.search(r' connected(?: primary)? (\d+)x(\d+)\+',out)
  if m:return int(m.group(1)),int(m.group(2))
 except Exception:pass
 return 1920,1080
# Fullscreen is requested from Chromium itself over DevTools (the session is opened in run()). Playwright must not
# emulate a viewport: it then resizes the window to the viewport and current Chromium versions leave fullscreen
# for that, which showed the page in an ordinary window on Docker nodes (Debian Chromium).
CDP=None
MAIN=None   # (x, y, w, h) of the main area when the screen has zones
def fullscreen(check_only=False):
 # check_only (the periodic check): the window manager is only called when Chromium is not fullscreen
 if MAIN:return place_main(check_only)
 if CDP is not None:
  try:
   wid=CDP.send('Browser.getWindowForTarget')['windowId']
   if CDP.send('Browser.getWindowBounds',{'windowId':wid})['bounds'].get('windowState')!='fullscreen':
    CDP.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'windowState':'fullscreen'}});print('Chromium window switched to fullscreen',flush=True)
   elif check_only:return
  except Exception as e:print('fullscreen',e,flush=True)
 # the window manager is told as well (EWMH); wmctrl changes at most two properties per call
 env=dict(os.environ,DISPLAY=':0',XAUTHORITY='/home/caracal/.Xauthority')
 try:
  for wid in subprocess.check_output(['xdotool','search','--onlyvisible','--class','chromium'],text=True,env=env,stderr=subprocess.DEVNULL).split():
   subprocess.run(['wmctrl','-i','-r',wid,'-b','remove,above,hidden'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);subprocess.run(['wmctrl','-i','-r',wid,'-b','add,fullscreen'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 except Exception:pass



# Zones (a side panel, a bar at the bottom) next to the playlist: each zone is a small Chromium window without a frame
# showing a widget of the node; the player's own window takes the rest of the screen.
XENV=lambda:dict(os.environ,DISPLAY=os.getenv('DISPLAY',':0'))
zones={'layout':None,'procs':{}}
def layout_rects(lay,w,h):
 side,tick=lay.get('side'),lay.get('ticker')
 th=int(h*tick['height']/100) if tick else 0
 sw=int(w*side['width']/100) if side else 0
 left=bool(side) and side.get('position')=='left'
 out=[]
 if side:out.append(('side',(0 if left else w-sw,0,sw,h-th),side['widget']))
 if tick:out.append(('ticker',(0,h-th,w,th),tick['widget']))
 return (sw if left else 0,0,w-sw,h-th),out
def undecorate(wid,rect):
 # no frame (Motif hints, honoured by Openbox), at its place and above the player
 env=XENV()
 for cmd in (['xprop','-id',wid,'-f','_MOTIF_WM_HINTS','32c','-set','_MOTIF_WM_HINTS','2, 0, 0, 0, 0'],
             ['wmctrl','-i','-r',wid,'-b','remove,fullscreen,maximized_vert,maximized_horz'],
             ['wmctrl','-i','-r',wid,'-e','0,%d,%d,%d,%d'%rect]):
  subprocess.run(cmd,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def windows(cls):
 try:return subprocess.check_output(['xdotool','search','--onlyvisible','--class',cls],text=True,env=XENV(),stderr=subprocess.DEVNULL).split()
 except Exception:return []
def place_main(check_only=False):
 if CDP is not None:
  try:
   wid=CDP.send('Browser.getWindowForTarget')['windowId'];b=CDP.send('Browser.getWindowBounds',{'windowId':wid})['bounds']
   if b.get('windowState')!='normal':CDP.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'windowState':'normal'}})
   if (b.get('left'),b.get('top'),b.get('width'),b.get('height'))!=MAIN:CDP.send('Browser.setWindowBounds',{'windowId':wid,'bounds':dict(zip(('left','top','width','height'),MAIN))})
   elif check_only:return
  except Exception as e:print('layout',e,flush=True)
 for wid in windows('^chromium$|^Chromium$'):undecorate(wid,MAIN)
def start_zones(lay,w,h):
 global MAIN
 stop_zones()
 if not lay:MAIN=None;return
 MAIN,specs=layout_rects(lay,w,h)
 for name,rect,widget in specs:
  args=['/usr/bin/chromium','--app='+BASE+'/widgets?'+widget,'--class=caracal-zone-'+name,'--user-data-dir=/tmp/caracal-zone-'+name,
        '--window-position=%d,%d'%rect[:2],'--window-size=%d,%d'%rect[2:],'--force-device-scale-factor=1','--no-first-run','--noerrdialogs',
        '--disable-infobars','--no-sandbox','--disable-dev-shm-usage','--disable-background-networking','--disable-component-update','--disable-sync']
  zones['procs'][name]=(subprocess.Popen(args,env=XENV(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL),rect,args)
 zones['layout']=lay
def stop_zones():
 for proc,_r,_a in zones['procs'].values():
  try:proc.terminate()
  except Exception:pass
 zones['procs'].clear()
def watch_zones():
 for name,(proc,rect,args) in list(zones['procs'].items()):
  if proc.poll() is not None:   # a zone that closed comes back
   zones['procs'][name]=(subprocess.Popen(args,env=XENV(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL),rect,args)
  for wid in windows('caracal-zone-'+name):undecorate(wid,rect)
 for wid in windows('caracal-zone'):subprocess.run(['wmctrl','-i','-r',wid,'-b','add,above'],env=XENV(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

# A page that does not load (no network, the site is down): a quiet card instead of a broken page, the playlist goes on.
OFFLINE_PAGE='''<body style="margin:0;height:100vh;display:grid;place-items:center;background:#10131a;color:#f4f6fa;font-family:DejaVu Sans,sans-serif">
<div style="text-align:center"><div id="t" style="font-size:22vh;font-weight:700;font-variant-numeric:tabular-nums"></div>
<div style="font-size:3.4vh;color:#9aa3b5;margin-top:3vh">%s</div></div>
<script>const t=()=>document.getElementById('t').textContent=new Date().toLocaleTimeString('cs-CZ',{hour:'2-digit',minute:'2-digit'});t();setInterval(t,1000)</script></body>'''
def offline(page,a,error):
 import html
 print('Item',a.get('name'),'did not load:',str(error)[:200],flush=True)
 try:page.set_content(OFFLINE_PAGE%html.escape(str(a.get('name') or '')))
 except Exception:pass

# CARACAL_COOKIE_ACCEPT_V2
COOKIE_ACCEPT_EXACT={
 'accept','accept all','accept all cookies','allow','allow all','allow all cookies','agree','i agree','consent','ok','got it','continue',
 'přijmout','přijmout vše','přijmout všechny','přijmout všechny cookies','přijmout všechny soubory cookie','povolit vše','povolit všechny','souhlasím','souhlasit se vším','akceptovat vše','rozumím','pokračovat',
 'alle akzeptieren','alles akzeptieren','alle zulassen','tout accepter','accepter tout','aceptar todo','aceptar todas','accetta tutto','accetta tutti','alles accepteren','accepteer alles','zaakceptuj wszystko','zgadzam się'
}
COOKIE_POSITIVE=('accept all','allow all','přijmout vše','přijmout všechny','povolit vše','povolit všechny','souhlasit se vším','alle akzeptieren','alles akzeptieren','tout accepter','aceptar todo','accetta tutto','alles accepteren','zaakceptuj wszystko')
COOKIE_NEGATIVE=('reject','decline','deny','necessary','essential','settings','manage','preferences','odmítnout','zamítnout','nezbytné','nastavení','spravovat','vybrat','upravit')
COOKIE_SELECTORS=['#onetrust-accept-btn-handler','#didomi-notice-agree-button','#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll','[data-testid="uc-accept-all-button"]','.fc-cta-consent','.qc-cmp2-summary-buttons button[mode="primary"]','.cmpboxbtnyes','.cc-allow','.js-cookie-consent-agree','button[id*="accept-all" i]','button[class*="accept-all" i]','button[data-action*="accept" i]']
def _cookie_norm(value):
 return re.sub(r'\s+',' ',str(value or '').strip().lower())
def _cookie_visible_candidates(frame):
 try:return frame.locator('button, [role="button"], input[type="button"], input[type="submit"], a').all()
 except Exception:return []
def _cookie_click_shadow(frame):
 try:
  return bool(frame.evaluate(r"""({exact,positive,negative})=>{const norm=s=>(s||'').toLowerCase().replace(/\s+/g,' ').trim();const scan=root=>{for(const el of root.querySelectorAll('*')){if(el.shadowRoot){const x=scan(el.shadowRoot);if(x)return x}const role=el.getAttribute&&el.getAttribute('role');const tag=(el.tagName||'').toLowerCase();if(!(tag==='button'||tag==='a'||tag==='input'||role==='button'))continue;const t=norm(el.innerText||el.value||el.getAttribute('aria-label')||el.title);if(!t||negative.some(x=>t.includes(x)))continue;if(exact.includes(t)||positive.some(x=>t.includes(x))){const r=el.getBoundingClientRect();if(r.width>0&&r.height>0){el.click();return true}}}return false};return scan(document)}""",{'exact':list(COOKIE_ACCEPT_EXACT),'positive':list(COOKIE_POSITIVE),'negative':list(COOKIE_NEGATIVE)}))
 except Exception:return False
def auto_accept_all_cookies(page):
 deadline=time.time()+8.0
 while time.time()<deadline:
  for frame in list(page.frames):
   for selector in COOKIE_SELECTORS:
    try:
     el=frame.locator(selector).first
     if el.count() and el.is_visible(timeout=100):el.click(timeout=1000,force=True);print('Cookie banner accepted:',selector,flush=True);page.wait_for_timeout(300);return True
    except Exception:pass
   for el in _cookie_visible_candidates(frame):
    try:
     if not el.is_visible(timeout=80):continue
     text=_cookie_norm(el.inner_text(timeout=100) or el.get_attribute('value') or el.get_attribute('aria-label') or el.get_attribute('title'))
     if not text or any(x in text for x in COOKIE_NEGATIVE):continue
     if text in COOKIE_ACCEPT_EXACT or any(x in text for x in COOKIE_POSITIVE):
      el.click(timeout=1000,force=True);print('Cookie banner accepted:',text,flush=True);page.wait_for_timeout(300);return True
    except Exception:pass
   if _cookie_click_shadow(frame):print('Cookie banner accepted in shadow DOM',flush=True);page.wait_for_timeout(300);return True
  page.wait_for_timeout(350)
 print('Cookie banner: no accept-all button found',flush=True)
 return False


# CARACAL_HTTP_AUTH_V1
# HTTP Basic/Digest log-in (the browser's own user name / password pop-up). The credentials go into the address
# (https://user:password@server/...): Chromium answers the server's challenge itself, no pop-up appears, and it keeps
# them for the further requests of that server. They are used only for the server of the profile.
from urllib.parse import quote as _quote,urlsplit as _urlsplit,urlunsplit as _urlunsplit
def _origin(url):
 p=_urlsplit(url);return (p.scheme.lower(),(p.hostname or '').lower(),p.port or (443 if p.scheme.lower()=='https' else 80))
def _with_credentials(url,user,password):
 p=_urlsplit(url);host=p.netloc.rsplit('@',1)[-1]
 return _urlunsplit((p.scheme,f"{_quote(user or '',safe='')}:{_quote(password or '',safe='')}@{host}",p.path,p.query,p.fragment))
def show_http_auth(page,a,pr):
 # the playlist item's own address when it is on the profile's server, otherwise the profile's address
 url=a['source'] if _origin(a['source'])==_origin(pr['target_url']) else pr['target_url']
 secret=_with_credentials(url,pr['username'],pr['password'])
 try:
  # 1) the address with the credentials only logs Chromium in ('commit' = the server accepted them);
  # 2) the page itself is then opened without them: a page whose address contains credentials cannot make
  #    requests to relative addresses (fetch fails), which breaks dashboards. Chromium reuses the log-in.
  page.goto(secret,wait_until='commit',timeout=45000)
  page.goto(url,wait_until='domcontentloaded',timeout=45000)
 except Exception as e:
  message=str(e).replace(secret,url)   # never log the password
  if 'ERR_INVALID_AUTH_CREDENTIALS' not in message:raise RuntimeError(message) from None
  # wrong user name or password: an explanation on the screen instead of restarting the player over and over
  print('HTTP login',pr.get('name'),'failed: wrong user name or password for',url,flush=True)
  # Chromium loads its own error page right after the failure: wait for it, then replace it with ours
  error_page='data:text/html;charset=utf-8,'+_quote('<body style="margin:0;background:#0b1018;color:#f8fafc;display:grid;place-items:center;height:100vh;font:28px DejaVu Sans,sans-serif;text-align:center"><div>CARACAL<br><small style="color:#94a3b8">HTTP přihlášení selhalo: špatné jméno nebo heslo v profilu</small></div></body>')
  try:page.wait_for_url(lambda u:u.startswith('chrome-error:'),timeout=3000)
  except Exception:pass
  for _ in range(3):
   try:page.goto(error_page,timeout=10000);break
   except Exception:page.wait_for_timeout(500)

# Dark mode of websites (item 'dark' from the playlist): 'dark' asks the page for its own dark look
# (prefers-color-scheme), 'force' also darkens pages without one (Chromium's automatic dark mode). Applied only when
# it changes; a new browser starts without it.
dark_applied=None
def apply_dark(page,mode):
 global dark_applied
 mode=mode if mode in ('dark','force') else 'off'
 if mode==dark_applied:return
 try:page.emulate_media(color_scheme='dark' if mode!='off' else 'light')
 except Exception as e:print('dark mode',e,flush=True)
 try:
  if CDP is not None:CDP.send('Emulation.setAutoDarkModeOverride',{'enabled':True} if mode=='force' else {})
 except Exception as e:print('automatic dark mode',e,flush=True)
 dark_applied=mode
# Cameras: the streams of a camera item play in mpv windows over the browser, as a grid (Chromium cannot play RTSP).
# They stay for the item's display time; a stream that drops is started again.
CAMERA_TAG='caracal-camera'
cameras={'procs':[],'item':None,'specs':[]}
def camera_grid(n,w,h,layout='auto'):
 cols={'1':1,'2':2,'2x2':2,'3x3':3}.get(layout) or (1 if n<=1 else 2 if n<=4 else 3)
 rows=max(1,-(-n//cols)) if layout!='2' else 1
 cols=max(cols,-(-n//rows)) if layout=='2' else cols
 cw,ch=w//cols,h//rows
 return [(cw,ch,(i%cols)*cw,(i//cols)*ch) for i in range(n)]
def camera_args(url,cell,audio=False):
 cw,ch,x,y=cell
 return ['mpv','--no-config','--really-quiet','--title='+CAMERA_TAG,'--no-border','--ontop','--no-osc','--no-input-default-bindings',
  '--input-vo-keyboard=no','--cursor-autohide=always','--keepaspect=yes','--profile=low-latency','--untimed','--cache=no',
  '--rtsp-transport=tcp','--hwdec=auto-safe','--network-timeout=10',f'--geometry={cw}x{ch}+{x}+{y}']+([] if audio else ['--no-audio'])+[url]
def stop_cameras():
 for p in cameras['procs']:
  try:p.terminate()
  except Exception:pass
 for p in cameras['procs']:
  try:p.wait(3)
  except Exception:
   try:p.kill()
   except Exception:pass
 cameras.update(procs=[],item=None,specs=[])
def start_cameras(a):
 import shutil
 cam=get('/api/player/camera/'+str(a['id']))
 if not cam or not cam.get('streams'):return 'Kamera nemá žádný stream'
 if not shutil.which('mpv'):return 'Pro kamery chybí přehrávač mpv'
 ox,oy,w,h=MAIN or (0,0)+display_size();env=dict(os.environ,DISPLAY=os.getenv('DISPLAY',':0'))
 cells=[(cw,ch,x+ox,y+oy) for cw,ch,x,y in camera_grid(len(cam['streams']),w,h,cam.get('layout','auto'))]
 specs=[camera_args(s['url'],cells[i],cam.get('audio') and i==0) for i,s in enumerate(cam['streams'])]
 cameras.update(item=a['id'],specs=specs,procs=[subprocess.Popen(x,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) for x in specs])
 return ''
def watch_cameras():
 # a camera that dropped (network, reboot of the camera) is started again
 env=dict(os.environ,DISPLAY=os.getenv('DISPLAY',':0'))
 for i,p in enumerate(cameras['procs']):
  if p.poll() is not None:
   print('camera stream ended, starting it again',flush=True)
   cameras['procs'][i]=subprocess.Popen(cameras['specs'][i],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def show(page,a):
 if cameras['procs']:stop_cameras()
 apply_dark(page,a.get('dark') if a['kind']=='web' else 'off')
 if a['kind']=='takeover':   # an emergency message from CARACAL Fleet
  page.goto(BASE+'/takeover',wait_until='domcontentloaded',timeout=45000)
  if MAIN:   # the whole screen: the zones step aside
   try:CDP.send('Browser.setWindowBounds',{'windowId':CDP.send('Browser.getWindowForTarget')['windowId'],'bounds':{'left':0,'top':0,'width':display_size()[0],'height':display_size()[1]}})
   except Exception:pass
   stop_zones()
  return
 if a['kind']=='camera':
  error=start_cameras(a)
  page.set_content('<body style="margin:0;background:#000;color:#94a3b8;display:grid;place-items:center;height:100vh;font:26px DejaVu Sans,sans-serif">'+(error or '')+'</body>')
  return
 if a['kind']=='web':
  pid=a.get('auth_profile_id');pr=get('/api/player/profile/'+str(pid)) if pid else None
  if pr and pr.get('auth_type')=='http':show_http_auth(page,a,pr)
  elif pr:
   page.goto(pr['login_url'],wait_until='domcontentloaded',timeout=45000);pw=page.locator(pr['pass_selector'])
   if pw.count():
    # a wrong selector must not stop the player: the target page is shown anyway and the error is logged
    try:
     page.locator(pr['user_selector']).first.fill(pr['username'],timeout=10000);pw.first.fill(pr['password'],timeout=10000)
     try:pw.first.press('Enter',timeout=5000)
     except Exception:page.locator(pr['submit_selector']).first.click(force=True,timeout=5000)
     try:page.wait_for_load_state('domcontentloaded',timeout=15000)
     except Exception:pass
     page.wait_for_timeout(1500)
    except Exception as e:print('Login',pr.get('name'),'failed:',e,flush=True)
   try:page.goto(pr['target_url'],wait_until='domcontentloaded',timeout=45000)
   except Exception as e:offline(page,a,e);return
  else:
   try:page.goto(a['source'],wait_until='domcontentloaded',timeout=45000)
   except Exception as e:offline(page,a,e);return
  z=float(a.get('scale') or 1);page.evaluate("z=>{document.documentElement.style.zoom=String(z);document.body.style.zoom=String(z)}",z)
  auto_accept_all_cookies(page)
 elif a['kind']=='image':page.set_content(f'<body style="margin:0;background:#000;overflow:hidden"><img src="{BASE+a["source"]}" style="width:100vw;height:100vh;object-fit:contain"></body>')
 else:page.set_content(f'<body style="margin:0;background:#000;overflow:hidden"><video src="{BASE+a["source"]}" autoplay muted loop style="width:100vw;height:100vh;object-fit:contain"></video></body>')
resume_index=0   # where the playlist continues after the browser was restarted
def run():
 global CDP,resume_index,dark_applied
 CDP=None;dark_applied=None;w,h=display_size();os.makedirs(PROFILE,exist_ok=True)
 stop_cameras();subprocess.run(['pkill','-f','--title='+CAMERA_TAG],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)   # left over by a restart
 subprocess.run(['pkill','-f','--class=caracal-zone-'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 with sync_playwright() as pw:
  layout=get('/api/player/layout',{}) or {}
  start_zones(layout,w,h)
  ctx=pw.chromium.launch_persistent_context(PROFILE,headless=False,executable_path='/usr/bin/chromium',ignore_default_args=['--enable-automation'],no_viewport=True,ignore_https_errors=True,args=chromium_args(w,h,MAIN))
  if LOW_MEMORY:print(f'Small memory ({TOTAL_MB} MB): Chromium runs in its low-end mode',flush=True)
  page=ctx.pages[0] if ctx.pages else ctx.new_page();CDP=ctx.new_cdp_session(page);time.sleep(2);fullscreen();idx=resume_index;cur=None;mode='normal';collection_id=None;collection_index=0;remaining=0;duration=0;last_cmd=-1;last_fs=0;restart_id=None;items=[];items_at=0
  while True:
   # the playlist changes rarely: every 5 s (and right after a command); commands are checked every second
   if time.time()-items_at>=5:
    items=get('/api/player/playlist-expanded',items) or [];items_at=time.time()
    takeover=any(x.get('kind')=='takeover' for x in items)
    if not takeover and (get('/api/player/layout',layout) or {})!=layout:raise RuntimeError('screen layout changed')
    if MAIN and not takeover and not zones['procs']:raise RuntimeError('emergency message ended, zones come back')
   cmd=get('/api/v6/player/command',{}) or {};cid=int(cmd.get('command_id') or 0)
   # a restart requested from the admin UI (Docker: the container restarts the player after it exits)
   if restart_id is None:restart_id=cmd.get('restart_id',0)
   elif cmd.get('restart_id',restart_id)!=restart_id:print('PLAYER RESTART REQUESTED',flush=True);raise SystemExit(0)
   if cid!=last_cmd:
    if last_cmd!=-1:items=get('/api/player/playlist-expanded',items) or [];items_at=time.time()
    last_cmd=cid;action=cmd.get('action');item_id=cmd.get('item_id');requested_collection=cmd.get('collection_id')
    if action in ('show','freeze'):
     target=next((x for x in items if int(x['id'])==int(item_id)),None)
     if target:cur=target;show(page,cur);duration=max(5,int(cur.get('duration') or 30));remaining=duration;mode='single-freeze' if action=='freeze' else 'temporary';collection_id=None;fullscreen()
    elif action in ('show_collection','freeze_collection'):
     collection_id=int(requested_collection);group=[x for x in items if x.get('parent_id')==collection_id]
     if group:collection_index=0;cur=group[0];show(page,cur);duration=max(5,int(cur.get('duration') or 30));remaining=duration;mode='collection-freeze' if action=='freeze_collection' else 'collection-temporary';fullscreen()
    elif action=='unfreeze':mode='normal';collection_id=None;cur=None;remaining=0
    elif action=='next':
     if mode=='collection-freeze' and collection_id:
      group=[x for x in items if x.get('parent_id')==collection_id]
      if group:collection_index=(collection_index+1)%len(group);cur=group[collection_index];show(page,cur);duration=max(5,int(cur.get('duration') or 30));remaining=duration
     else:
      mode='normal';collection_id=None
      if cur:
       try:idx=(next(i for i,x in enumerate(items) if int(x['id'])==int(cur['id']))+1)%len(items)
       except Exception:idx=(idx+1)%len(items) if items else 0
      cur=None;remaining=0
   if not items:
    if cameras['procs']:stop_cameras()
    if cur is not None:cur=None;page.set_content('<body style="margin:0;background:#000;color:#fff;display:grid;place-items:center;height:100vh;font:30px system-ui">CARACAL<br><small>Playlist je prázdný</small></body>')
    post('/api/v2/player/heartbeat',{'current_id':None,'current_name':'','frozen':False,'collection_frozen':False,'remaining':None,'duration':None});time.sleep(1);continue
   if cur is None:cur=items[idx%len(items)];show(page,cur);duration=max(5,int(cur.get('duration') or 30));remaining=duration;fullscreen()
   frozen=mode=='single-freeze';collection_frozen=mode in ('collection-freeze','collection-temporary')
   post('/api/v2/player/heartbeat',{'current_id':cur['id'],'current_name':cur['name'],'frozen':frozen,'collection_frozen':collection_frozen,'collection_id':collection_id,'remaining':None if frozen else remaining,'duration':duration})
   if time.time()-last_fs>4:
    if zones['procs']:watch_zones()
    if cameras['procs']:watch_cameras()
    else:fullscreen(True)
    last_fs=time.time()
   time.sleep(1)
   if not frozen:
    remaining-=1
    if remaining<=0:
     if collection_frozen and collection_id:
      group=[x for x in items if x.get('parent_id')==collection_id]
      if group:
       collection_index+=1
       if mode=='collection-temporary' and collection_index>=len(group):
        mode='normal';collection_id=None;cur=None;idx=(idx+1)%len(items);continue
       collection_index=collection_index%len(group);cur=group[collection_index];show(page,cur);duration=max(5,int(cur.get('duration') or 30));remaining=duration;continue
     if mode=='temporary':
      try:idx=(next(i for i,x in enumerate(items) if int(x['id'])==int(cur['id']))+1)%len(items)
      except Exception:idx=0
     else:idx=(idx+1)%len(items)
     mode='normal';cur=None
     if memory_low():resume_index=idx;raise RuntimeError('memory almost full, restarting the browser')
if __name__=='__main__':
 while True:
  try:run()
  except Exception as e:print('PLAYER RESTART',e,flush=True);time.sleep(4)
