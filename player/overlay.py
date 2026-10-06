import json,time,tkinter as tk,subprocess,os
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
 root.after(FRAME_MS,tick)
root.after(FRAME_MS,tick);root.mainloop()
