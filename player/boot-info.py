import socket,subprocess,time,tkinter as tk,os
SHOW_SECONDS=45
PORT=8080
ENV=dict(os.environ,DISPLAY=':0',XAUTHORITY='/home/caracal/.Xauthority')
def addresses():
 values=[]
 try:
  for ip in subprocess.check_output(['hostname','-I'],text=True).split():
   if ':' not in ip and not ip.startswith('127.') and ip not in values:values.append(ip)
 except Exception:pass
 return values
def lines():
 ips=addresses()
 if not ips:return ['CARACAL JE PŘIPRAVEN','Síť se připojuje…','Po připojení otevři správu na portu 8080']
 return ['CARACAL JE PŘIPRAVEN']+[f'http://{ip}:{PORT}' for ip in ips[:3]]+[f'Zařízení: {socket.gethostname()}','Tato obrazovka zmizí automaticky.']
root=tk.Tk(className='CaracalBootInfo');root.overrideredirect(True);root.attributes('-topmost',True);root.configure(bg='#0b1018');root.title('CARACAL Setup Address')
root.geometry(f'{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0')
root.update_idletasks();sw=root.winfo_screenwidth();sh=root.winfo_screenheight()
# 12% safe area on every edge protects text from TV overscan.
safe_w=max(500,int(sw*.76));safe_h=max(330,int(sh*.70))
box=tk.Frame(root,width=safe_w,height=safe_h,bg='#111926',highlightthickness=1,highlightbackground='#334155')
box.place(relx=.5,rely=.5,anchor='center');box.pack_propagate(False)
inner=tk.Frame(box,bg='#111926');inner.place(relx=.5,rely=.5,anchor='center',relwidth=.9)
logo_size=max(22,min(38,int(sh/27)));url_size=max(15,min(28,int(sh/38)));small_size=max(10,min(15,int(sh/70)))
logo=tk.Label(inner,text='CARACAL',bg='#111926',fg='#ff7355',font=('DejaVu Sans',logo_size,'bold'));logo.pack(pady=(0,max(15,int(sh*.025))))
status=tk.Label(inner,text='',justify='center',wraplength=int(safe_w*.82),bg='#111926',fg='#f8fafc',font=('DejaVu Sans',url_size,'bold'));status.pack(fill='x')
hint=tk.Label(inner,text='Webovou adresu můžeš později zjistit příkazem hostname -I.',justify='center',wraplength=int(safe_w*.78),bg='#111926',fg='#94a3b8',font=('DejaVu Sans',small_size));hint.pack(pady=(max(14,int(sh*.025)),0))
started=time.time()
def raise_window():
 root.lift();root.attributes('-topmost',True)
 try:subprocess.run(['wmctrl','-r','CARACAL Setup Address','-b','add,above,sticky,skip_taskbar'],env=ENV,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 except Exception:pass
def tick():
 current=lines();title=current.pop(0);logo.config(text=title);status.config(text='\n\n'.join(current));raise_window()
 if time.time()-started>=SHOW_SECONDS:root.destroy();return
 root.after(1000,tick)
root.bind('<Escape>',lambda e:root.destroy());root.after(250,tick);root.mainloop()
