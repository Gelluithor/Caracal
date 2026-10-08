import os,time,re,subprocess,requests
from playwright.sync_api import sync_playwright
BASE=os.getenv('CARACAL_BASE','http://127.0.0.1:8080');PROFILE=os.getenv('CARACAL_PROFILE','/var/lib/caracal/chromium')
def get(path,default=None):
 try:r=requests.get(BASE+path,timeout=8);r.raise_for_status();return r.json()
 except Exception as e:print('GET',path,e,flush=True);return default
def post(path,data):
 try:requests.post(BASE+path,json=data,timeout=5)
 except Exception as e:print('POST',path,e,flush=True)
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
def fullscreen():
 if CDP is not None:
  try:
   wid=CDP.send('Browser.getWindowForTarget')['windowId']
   if CDP.send('Browser.getWindowBounds',{'windowId':wid})['bounds'].get('windowState')!='fullscreen':
    CDP.send('Browser.setWindowBounds',{'windowId':wid,'bounds':{'windowState':'fullscreen'}});print('Chromium window switched to fullscreen',flush=True)
  except Exception as e:print('fullscreen',e,flush=True)
 # the window manager is told as well (EWMH); wmctrl changes at most two properties per call
 env=dict(os.environ,DISPLAY=':0',XAUTHORITY='/home/caracal/.Xauthority')
 try:
  for wid in subprocess.check_output(['xdotool','search','--onlyvisible','--class','chromium'],text=True,env=env,stderr=subprocess.DEVNULL).split():
   subprocess.run(['wmctrl','-i','-r',wid,'-b','remove,above,hidden'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);subprocess.run(['wmctrl','-i','-r',wid,'-b','add,fullscreen'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
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

def show(page,a):
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
   page.goto(pr['target_url'],wait_until='domcontentloaded',timeout=45000)
  else:page.goto(a['source'],wait_until='domcontentloaded',timeout=45000)
  z=float(a.get('scale') or 1);page.evaluate("z=>{document.documentElement.style.zoom=String(z);document.body.style.zoom=String(z)}",z)
  auto_accept_all_cookies(page)
 elif a['kind']=='image':page.set_content(f'<body style="margin:0;background:#000;overflow:hidden"><img src="{BASE+a["source"]}" style="width:100vw;height:100vh;object-fit:contain"></body>')
 else:page.set_content(f'<body style="margin:0;background:#000;overflow:hidden"><video src="{BASE+a["source"]}" autoplay muted loop style="width:100vw;height:100vh;object-fit:contain"></video></body>')
def run():
 global CDP
 CDP=None;w,h=display_size();os.makedirs(PROFILE,exist_ok=True)
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(PROFILE,headless=False,executable_path='/usr/bin/chromium',ignore_default_args=['--enable-automation'],no_viewport=True,ignore_https_errors=True,args=['--kiosk','--start-fullscreen','--start-maximized','--window-position=0,0',f'--window-size={w},{h}','--force-device-scale-factor=1','--disable-blink-features=AutomationControlled','--no-first-run','--no-default-browser-check','--noerrdialogs','--disable-infobars','--disable-session-crashed-bubble','--autoplay-policy=no-user-gesture-required','--password-store=basic','--use-mock-keychain','--disable-dev-shm-usage','--no-sandbox'])
  page=ctx.pages[0] if ctx.pages else ctx.new_page();CDP=ctx.new_cdp_session(page);time.sleep(2);fullscreen();idx=0;cur=None;mode='normal';collection_id=None;collection_index=0;remaining=0;duration=0;last_cmd=-1;last_fs=0;restart_id=None
  while True:
   items=get('/api/player/playlist-expanded',[]) or [];cmd=get('/api/v6/player/command',{}) or {};cid=int(cmd.get('command_id') or 0)
   # a restart requested from the admin UI (Docker: the container restarts the player after it exits)
   if restart_id is None:restart_id=cmd.get('restart_id',0)
   elif cmd.get('restart_id',restart_id)!=restart_id:print('PLAYER RESTART REQUESTED',flush=True);raise SystemExit(0)
   if cid!=last_cmd:
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
    if cur is not None:cur=None;page.set_content('<body style="margin:0;background:#000;color:#fff;display:grid;place-items:center;height:100vh;font:30px system-ui">CARACAL<br><small>Playlist je prázdný</small></body>')
    post('/api/v2/player/heartbeat',{'current_id':None,'current_name':'','frozen':False,'collection_frozen':False,'remaining':None,'duration':None});time.sleep(1);continue
   if cur is None:cur=items[idx%len(items)];show(page,cur);duration=max(5,int(cur.get('duration') or 30));remaining=duration;fullscreen()
   frozen=mode=='single-freeze';collection_frozen=mode in ('collection-freeze','collection-temporary')
   post('/api/v2/player/heartbeat',{'current_id':cur['id'],'current_name':cur['name'],'frozen':frozen,'collection_frozen':collection_frozen,'collection_id':collection_id,'remaining':None if frozen else remaining,'duration':duration})
   if time.time()-last_fs>4:fullscreen();last_fs=time.time()
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
if __name__=='__main__':
 while True:
  try:run()
  except Exception as e:print('PLAYER RESTART',e,flush=True);time.sleep(4)
