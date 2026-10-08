import json,time,tkinter as tk,subprocess,os,threading,urllib.request
from pathlib import Path
STATE=Path('/var/lib/caracal/player-control-v2.json')
ENV=dict(os.environ,DISPLAY=':0',XAUTHORITY='/home/caracal/.Xauthority')
FPS=50
FRAME_MS=max(10,round(1000/FPS))
root=tk.Tk(className='CaracalOverlay')
root.withdraw();root.overrideredirect(True);root.attributes('-topmost',True);root.attributes('-alpha',0.92)
root.configure(bg='#080d14');root.title('CARACAL Progress')
canvas=tk.Canvas(root,highlightthickness=0,borderwidth=0,bg='#111827')
canvas.pack(fill='both',expand=True)
last_raise=0
last_remaining=None
last_duration=None
last_state_update=0.0
smoothed_progress=0.0
last_frame=time.monotonic()

def raise_window():
 global last_raise
 root.deiconify();root.lift();root.attributes('-topmost',True)
 if time.time()-last_raise>1:
  try:
   subprocess.run(['wmctrl','-i','-r',str(root.winfo_id()),'-b','add,above,sticky,skip_taskbar'],env=ENV,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  except Exception:pass
  last_raise=time.time()

# On-screen notifications. The app owns the queue; GET /api/notify/overlay says what to show right now,
# so this only animates one notification at a time.
NOTIFY_URL=os.getenv('CARACAL_BASE','http://127.0.0.1:8080').rstrip('/')+'/api/notify/overlay'
LEVELS={'info':('ℹ','#3b82f6'),'success':('✓','#22c55e'),'warning':('⚠','#f59e0b'),'critical':('✖','#ef4444')}
TOAST_BG='#111926';TOAST_FG='#f8fafc';TOAST_MUTED='#94a3b8';ANIM=0.25
notify_data={}

# Notification sounds: a short chime per level, generated here (no sound files), played with ALSA aplay
# (or paplay / pw-play). The app decides whether a notification has a sound; volume and device come with it.
import math,shutil,struct,tempfile,wave
SOUND_DIR=Path(tempfile.gettempdir())/'caracal-sounds'
# (frequency Hz, start s, length s) notes per level
CHIMES={'info':[(880,0,.18),(1175,.14,.32)],'success':[(660,0,.14),(880,.11,.14),(1320,.22,.36)],
 'warning':[(740,0,.16),(740,.26,.22)],'critical':[(988,0,.12),(988,.17,.12),(988,.34,.12),(988,.62,.12),(988,.79,.12),(988,.96,.16)]}
def sound_file(level,volume):
 path=SOUND_DIR/f'{level}-{volume}.wav'
 if path.exists():return path
 SOUND_DIR.mkdir(parents=True,exist_ok=True);rate=44100;notes=CHIMES.get(level,CHIMES['info'])
 length=int(rate*(max(start+dur for _,start,dur in notes)+.05));samples=[0.0]*length
 for freq,start,dur in notes:
  first=int(start*rate);count=int(dur*rate)
  for i in range(count):
   if first+i>=length:break
   t=i/rate;env=min(1.0,t/.008)*math.exp(-t*(9 if level in ('info','success') else 4))
   samples[first+i]+=env*(math.sin(2*math.pi*freq*t)+.25*math.sin(4*math.pi*freq*t))
 peak=max(1e-6,max(abs(x) for x in samples));gain=.85*(volume/100)**2*32767/peak   # squared: closer to perceived loudness
 tmp=path.with_suffix('.tmp')
 with wave.open(str(tmp),'wb') as out:
  out.setnchannels(1);out.setsampwidth(2);out.setframerate(rate)
  out.writeframes(b''.join(struct.pack('<h',int(x*gain)) for x in samples))
 tmp.replace(path);return path
def play_sound(level,volume,device):
 def run():
  try:
   volume_pct=max(0,min(100,int(volume)))
   if volume_pct==0:return
   path=sound_file(level,volume_pct)
   if os.name=='nt':
    import winsound;winsound.PlaySound(str(path),winsound.SND_FILENAME);return
   for player in (['aplay','-q']+(['-D',device] if device else []),['paplay'],['pw-play']):
    if shutil.which(player[0]):
     subprocess.run(player+[str(path)],env=ENV,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10);return
   print('notification sound: no aplay, paplay or pw-play found',flush=True)
  except Exception as error:print('notification sound error',error,flush=True)
 threading.Thread(target=run,daemon=True).start()

def poll_notifications():
 global notify_data
 while True:
  try:
   with urllib.request.urlopen(NOTIFY_URL,timeout=3) as response:data=json.loads(response.read().decode('utf-8'))
   data['received']=time.monotonic()
  except Exception:data={}
  notify_data=data
  time.sleep(1)
toast=tk.Toplevel(root,bg=TOAST_BG);toast.withdraw();toast.overrideredirect(True);toast.attributes('-topmost',True);toast.title('CARACAL Notification')
stripe=tk.Frame(toast,width=10,bg=LEVELS['info'][1]);stripe.pack(side='left',fill='y')
body=tk.Frame(toast,bg=TOAST_BG);body.pack(side='left',fill='both',expand=True)
head=tk.Frame(body,bg=TOAST_BG);head.pack(fill='x')
toast_source=tk.Label(head,bg=TOAST_BG,anchor='w');toast_source.pack(side='left')
toast_waiting=tk.Label(head,bg=TOAST_BG,fg=TOAST_MUTED,anchor='e');toast_waiting.pack(side='right')
toast_title=tk.Label(body,bg=TOAST_BG,fg=TOAST_FG,anchor='w',justify='left');toast_title.pack(fill='x')
toast_message=tk.Label(body,bg=TOAST_BG,fg='#cbd5e1',anchor='w',justify='left')
toast_bar=tk.Canvas(body,height=4,bg='#1f2937',highlightthickness=0,borderwidth=0);toast_bar.pack(side='bottom',fill='x')
toast_bar_fill=toast_bar.create_rectangle(0,0,0,4,fill=LEVELS['info'][1],outline='')
toast_shown=None;toast_phase='hidden';toast_phase_start=0.0;toast_content=None;toast_geometry='';toast_raise=0.0;toast_width=400

def toast_build(cur,data):
 # (re)fill the notification window; sizes follow the screen height and the scale set in the admin UI
 global toast_content,toast_width
 sw=root.winfo_screenwidth();sh=root.winfo_screenheight();scale=max(.5,min(3,float(data.get('scale') or 100)/100))
 title_px=max(14,int(sh/38*scale));text_px=max(11,int(sh/54*scale));small_px=max(10,int(sh/72*scale));pad=max(10,int(sh/70*scale))
 toast_width=int(min(sw*.9,max(320,sw*(.42 if data.get('position') in ('top','bottom','center') else .32)*scale)))
 icon,color=LEVELS.get(cur.get('level'),LEVELS['info']);waiting=int(data.get('waiting') or 0)
 stripe.config(bg=color,width=max(6,int(pad*.6)));toast_bar.itemconfig(toast_bar_fill,fill=color)
 head.pack_configure(padx=pad,pady=(pad,int(pad*.3)))
 toast_source.config(text=f"{icon}  {cur.get('source') or 'CARACAL'}",fg=color,font=('DejaVu Sans',-small_px,'bold'))
 toast_waiting.config(text=f'+{waiting}' if waiting else '',font=('DejaVu Sans',-small_px,'bold'))
 toast_title.config(text=cur.get('title') or cur.get('message') or '',font=('DejaVu Sans',-title_px,'bold'),wraplength=toast_width-pad*3)
 toast_title.pack_configure(padx=pad,pady=(0,pad if not cur.get('title') or not cur.get('message') else int(pad*.4)))
 if cur.get('title') and cur.get('message'):
  toast_message.config(text=cur['message'],font=('DejaVu Sans',-text_px),wraplength=toast_width-pad*3);toast_message.pack(fill='x',padx=pad,pady=(0,pad),after=toast_title)
 else:toast_message.pack_forget()
 toast_bar.config(height=max(3,int(pad*.35)))
 toast_content=(cur.get('id'),cur.get('title'),cur.get('message'),cur.get('level'),cur.get('source'),waiting,data.get('scale'),data.get('position'))

def toast_place(position,progress,bar_height):
 # progress 0..1 of the slide/fade animation; bottom positions stay above the countdown bar
 global toast_geometry
 sw=root.winfo_screenwidth();sh=root.winfo_screenheight();w=toast_width;h=toast.winfo_reqheight();m=int(min(sw,sh)*.035)
 x=sw-w-m if position.endswith('right') else m if position.endswith('left') else (sw-w)//2
 y=m if position.startswith('top') else sh-h-m-bar_height if position.startswith('bottom') else (sh-h)//2
 offset=int((1-progress)*max(30,w*.15))
 if position.endswith('right'):x+=offset
 elif position.endswith('left'):x-=offset
 elif position.startswith('top'):y-=offset
 elif position.startswith('bottom'):y+=offset
 geometry=f'{w}x{h}+{x}+{y}'
 if geometry!=toast_geometry:
  # apply now: a mapped override-redirect window is otherwise only moved when Tk next runs idle tasks,
  # which nothing else does while the countdown bar is off, so the toast stayed at the animation start
  toast.geometry(geometry);toast.update_idletasks();toast_geometry=geometry
 try:toast.attributes('-alpha',.96*max(0.0,min(1.0,progress)))
 except Exception:pass

def notify_tick(now,bar_height):
 global toast_shown,toast_phase,toast_phase_start,toast_raise
 data=notify_data;cur=data.get('current') if data.get('enabled') else None
 remaining=0.0
 if cur:
  remaining=float(cur.get('remaining') or 0)-(now-float(data.get('received') or now))
  if remaining<=0:cur=None   # hide on time; the next poll brings the next notification
 want=cur.get('id') if cur else None
 if toast_shown is not None and want!=toast_shown:
  if toast_phase!='out':toast_phase='out';toast_phase_start=now
  if now-toast_phase_start>=ANIM:toast.withdraw();toast_shown=None;toast_phase='hidden';toast_content_reset()
 if toast_shown is None and cur:
  toast_build(cur,data);toast_shown=want;toast_phase='in';toast_phase_start=now;toast.update_idletasks();toast_place(data.get('position') or 'top-right',0,bar_height);toast.deiconify()
  # only for a notification that has just appeared, not when the overlay restarts in the middle of one
  if cur.get('sound') and remaining>=float(cur.get('duration') or 0)-3:play_sound(cur.get('level') or 'info',data.get('volume',70),data.get('sound_device') or '')
 elif cur and toast_shown==want and toast_content!=(cur.get('id'),cur.get('title'),cur.get('message'),cur.get('level'),cur.get('source'),int(data.get('waiting') or 0),data.get('scale'),data.get('position')):
  toast_build(cur,data);toast.update_idletasks()   # the sender updated it (same key) or the queue length changed
 if toast_shown is None:return
 t=min(1.0,(now-toast_phase_start)/ANIM)
 progress=1-(1-t)**3 if toast_phase=='in' else 1-t*t if toast_phase=='out' else 1.0
 if toast_phase=='in' and t>=1:toast_phase='shown'
 toast_place(data.get('position') or 'top-right',progress,bar_height)
 if cur:
  width=toast_bar.winfo_width();toast_bar.coords(toast_bar_fill,0,0,int(width*max(0.0,min(1.0,remaining/max(1.0,float(cur.get('duration') or 1))))),20)
 toast.lift();toast.attributes('-topmost',True)
 if time.time()-toast_raise>1:
  try:subprocess.run(['wmctrl','-i','-r',str(toast.winfo_id()),'-b','add,above,sticky,skip_taskbar'],env=ENV,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  except Exception:pass
  toast_raise=time.time()
def toast_content_reset():
 global toast_content,toast_geometry
 toast_content=None;toast_geometry=''
threading.Thread(target=poll_notifications,daemon=True).start()

def read_state():
 try:return json.loads(STATE.read_text(encoding='utf-8'))
 except Exception:return {}

def tick():
 global last_remaining,last_duration,last_state_update,smoothed_progress,last_frame
 now=time.monotonic();dt=max(0.001,min(0.1,now-last_frame));last_frame=now
 d=read_state();enabled=bool(d.get('overlay_enabled',True));online=time.time()-float(d.get('updated') or 0)<8
 try:height=max(2,min(160,int(d.get('overlay_size',8))))
 except Exception:height=8
 if not enabled or not online:
  root.withdraw();last_remaining=None;last_duration=None;last_state_update=now
 else:
  width=root.winfo_screenwidth();y=root.winfo_screenheight()-height
  root.geometry(f'{width}x{height}+0+{y}');root.update_idletasks();canvas.config(width=width,height=height);canvas.delete('all')
  canvas.create_rectangle(0,0,width,height,fill='#111827',outline='')
  frozen=bool(d.get('frozen'));collection=bool(d.get('collection_frozen'))
  if frozen or collection:
   smoothed_progress=1.0;color='#7c3aed' if collection else '#e85d3f';canvas.create_rectangle(0,0,width,height,fill=color,outline='')
  else:
   try:duration=max(1.0,float(d.get('duration') or 1));remaining=max(0.0,float(d.get('remaining') or 0))
   except Exception:duration=1.0;remaining=0.0
   # Heartbeat changes only once per second. Remember its arrival time and interpolate continuously between heartbeats.
   if remaining!=last_remaining or duration!=last_duration:
    if last_remaining is None or duration!=last_duration or remaining>last_remaining+1.5:
     smoothed_progress=max(0.0,min(1.0,1.0-remaining/duration))
    last_remaining=remaining;last_duration=duration;last_state_update=now
   elapsed=max(0.0,now-last_state_update)
   target=max(0.0,min(1.0,1.0-(max(0.0,remaining-elapsed)/duration)))
   # Short exponential easing hides timing jitter without visibly lagging behind the real countdown.
   smoothing=min(1.0,dt*14.0)
   smoothed_progress += (target-smoothed_progress)*smoothing
   if smoothed_progress>0.999 or remaining<=0:smoothed_progress=1.0
   fill=max(2,int(width*smoothed_progress))
   canvas.create_rectangle(0,0,fill,height,fill='#e85d3f',outline='')
  raise_window()
 try:notify_tick(now,height if enabled and online else 0)
 except Exception as error:print('notification overlay error',error,flush=True)
 root.after(FRAME_MS,tick)
root.after(FRAME_MS,tick);root.mainloop()
