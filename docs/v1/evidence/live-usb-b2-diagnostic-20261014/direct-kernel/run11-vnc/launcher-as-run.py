import hashlib, os, pathlib, socket, struct, subprocess, time, zlib
from datetime import datetime, timezone
OUT=pathlib.Path('/mnt/c/Users/Alex/AppData/Local/Temp/lc-b2-diagnostic-20261015/direct/run11-vnc')
ISO=pathlib.Path('/home/alex/librecore-usb-kms-output-20261013-b2/candidate-bundle/libre_core_autoloader-2026.09.29-x86_64.iso')
KERNEL=pathlib.Path('/mnt/c/Users/Alex/AppData/Local/Temp/lc-b2-diagnostic-20261014/direct/vmlinuz-linux')
INITRD=pathlib.Path('/mnt/c/Users/Alex/AppData/Local/Temp/lc-b2-diagnostic-20261014/direct/initramfs-linux.img')
HMP='/tmp/lc-b2-run11-hmp.sock'; SERIAL=OUT/'serial.log'; START=time.monotonic(); DEADLINE=START+600
EXPECTED={ISO:'afde8ba28025530852547ec5042dd94ecece5a9b1d24f5da141365861cc35cbd',KERNEL:'8ebc2c71271e000540f0a7545afd8e73504fcb724671f3d38c720b241842b901',INITRD:'c7440ebd5164e83e4a28c3f22ba2695af06a0fd29676ae572967ecdb6218e090'}
def utc(): return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()
def write(n,d):
 if isinstance(d,str): d=d.encode()
 p=OUT/n;p.write_bytes(d);return p
def log(s):
 line=f'{utc()} elapsed={time.monotonic()-START:.1f}s {s}'
 with open(OUT/'launcher.log','a',encoding='utf-8') as f:f.write(line+'\n')
 print(line,flush=True)
def ppm_png(path):
 b=path.read_bytes(); parts=b.split(b'\n',3); w,h=map(int,parts[1].split()); raw=parts[3]
 if len(raw)!=w*h*3: raise RuntimeError(f'incomplete PPM {path}: {len(raw)} vs {w*h*3}')
 rows=b''.join(b'\0'+raw[y*w*3:(y+1)*w*3] for y in range(h))
 def chunk(t,d):return struct.pack('>I',len(d))+t+d+struct.pack('>I',zlib.crc32(t+d)&0xffffffff)
 png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')
 path.with_suffix('.png').write_bytes(png);return w,h,len(b)
for p,e in EXPECTED.items():
 a=sha(p)
 if a!=e:raise SystemExit(f'hash mismatch {p}: {a}')
# Retain script outside OUT; remove only prior generated run11 files, not this driver.
for p in OUT.iterdir():
 if p.is_file():p.unlink()
write('iso-before.sha256',f'{EXPECTED[ISO]}  {ISO}\n')
write('input-hashes.txt','\n'.join(f'{sha(p)}  {p}' for p in (ISO,KERNEL,INITRD))+'\n')
write('run-scope.txt',f'run11 same-hash direct-kernel/VNC; started={utc()}; cap=600s; no NIC/writer\n')
write('kernel-command-line.txt','archisobasedir=arch archisosearchuuid=2026-10-13-22-51-40-00 console=tty0 console=ttyS0,115200n8 systemd.log_level=debug systemd.log_target=console systemd.debug_shell=1\n')
write('qemu-version.txt',subprocess.run(['/usr/sbin/qemu-system-x86_64','--version'],capture_output=True,text=True,check=True).stdout)
args=['/usr/sbin/qemu-system-x86_64','-accel','tcg,thread=multi','-m','3072','-smp','4','-kernel',str(KERNEL),'-initrd',str(INITRD),'-append','archisobasedir=arch archisosearchuuid=2026-10-13-22-51-40-00 console=tty0 console=ttyS0,115200n8 systemd.log_level=debug systemd.log_target=console systemd.debug_shell=1','-cdrom',str(ISO),'-boot','d','-no-reboot','-no-shutdown','-vga','std','-display','vnc=127.0.0.1:5','-nic','none','-serial',f'file:{SERIAL}','-monitor',f'unix:{HMP},server=on,wait=off']
write('command.txt','\n'.join(args)+'\n');write('hmp-connection.txt',f'socket={HMP}\nconnected and held open from VM startup\n')
proc=hmp=vnc=None
try:
 try:os.unlink(HMP)
 except FileNotFoundError:pass
 with open(OUT/'qemu-host.log','wb') as hostlog:
  proc=subprocess.Popen(args,stdout=hostlog,stderr=subprocess.STDOUT,start_new_session=True)
  log(f'qemu_pid={proc.pid} started; ISO sha256={sha(ISO)}; no network device')
  for _ in range(200):
   if proc.poll() is not None:raise RuntimeError(f'QEMU exited before monitor connect: {proc.returncode}')
   try:hmp=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);hmp.settimeout(2);hmp.connect(HMP);break
   except OSError:
    if hmp:hmp.close()
    hmp=None;time.sleep(.1)
  if hmp is None:raise RuntimeError('HMP connection unavailable')
  hmp.settimeout(.15)
  def mon(command,label):
   log(f'HMP {label}: {command}');hmp.sendall(command.encode()+b'\n');chunks=[];last=time.monotonic()
   while time.monotonic()<DEADLINE:
    try:
     b=hmp.recv(8192)
     if not b:break
     chunks.append(b);last=time.monotonic()
     if b'(qemu) ' in b''.join(chunks):break
    except socket.timeout:
     if chunks and time.monotonic()-last>.4:break
     if not chunks:break
   text=b''.join(chunks).decode('utf-8','replace');write(f'hmp-{label}.txt',text);return text
  try:write('hmp-banner.txt',hmp.recv(4096).decode('utf-8','replace'))
  except socket.timeout:write('hmp-banner.txt','banner read timed out after monitor connect\n')
  for _ in range(200):
   if proc.poll() is not None:raise RuntimeError(f'QEMU exited before VNC connect: {proc.returncode}')
   try:vnc=socket.create_connection(('127.0.0.1',5905),timeout=2);break
   except OSError:time.sleep(.1)
  if vnc is None:raise RuntimeError('loopback VNC unavailable')
  vnc.settimeout(5)
  def rx(n):
   b=b''
   while len(b)<n:
    z=vnc.recv(n-len(b))
    if not z:raise RuntimeError('VNC closed')
    b+=z
   return b
  proto=rx(12);vnc.sendall(proto);n=rx(1)[0];types=rx(n)
  if 1 not in types:raise RuntimeError(f'unsupported VNC auth: {types!r}')
  vnc.sendall(b'\x01');sec=rx(4)
  if struct.unpack('>I',sec)[0]:raise RuntimeError(f'VNC auth failed {sec!r}')
  vnc.sendall(b'\x01');init=rx(24);width,height=struct.unpack('>HH',init[:4]);nl=struct.unpack('>I',init[20:])[0];name=rx(nl)
  write('vnc-server.txt',f'connected=127.0.0.1:5905\nresolution={width}x{height}\nname={name.decode("utf-8","replace")}\nauth=none; loopback only\n')
  def key(k,d):vnc.sendall(struct.pack('>BBHI',4,int(d),0,k))
  def type_text(text):
   for ch in text:
    shifted=ch.isupper() or ch in '_:+*()<>?|"!@#$%^&{}'
    base=ch.lower() if ch.isupper() else ch
    trans={'_':'-','+':'=',':':';','*':'8','(':'9',')':'0','<':',','>':'.','?':'/','|':'\\','"':"'",'!':'1','@':'2','#':'3','$':'4','%':'5','^':'6','&':'7','{':'[','}':']'}
    base=trans.get(ch,base)
    if shifted:key(0xffe1,True)
    key(ord(base),True);key(ord(base),False)
    if shifted:key(0xffe1,False)
    time.sleep(.01)
   key(0xff0d,True);key(0xff0d,False)
  def snap(label):
   path=OUT/f'{label}.ppm'
   mon(f'screendump {path}',f'screendump-{label}')
   deadline=time.monotonic()+15;expected=None;size=0
   while time.monotonic()<deadline:
    try:
     size=path.stat().st_size
     if size>=20:
      b=path.read_bytes()[:80];parts=b.split(b'\n',3)
      if len(parts)>=4:
       w,h=map(int,parts[1].split());expected=len(parts[0])+1+len(parts[1])+1+len(parts[2])+1+w*h*3
       if size==expected:break
    except (OSError,ValueError):pass
    time.sleep(.25)
   if not path.exists():raise RuntimeError(f'no screenshot created: {path}')
   if expected is None or size!=expected:log(f'WARNING incomplete screenshot {label}: size={size} expected={expected}')
   else:
    w,h,nbytes=ppm_png(path);log(f'complete VGA frame {label}: {w}x{h} bytes={nbytes}')
   return path
  def guest(command,label,delay=1.2):
   if time.monotonic()>DEADLINE-40:log('skip guest command near deadline '+label);return
   write(f'{label}.command.txt',f'utc={utc()}\n{command}\n');log(f'guest input {label}: {command}');type_text(command);time.sleep(delay);snap(label)
  log('QEMU running; HMP and loopback VNC held open; waiting for debug shell serial marker')
  marker=b'debug-shell.service: Changed dead -> running';seen=False;last=0
  while time.monotonic()<min(DEADLINE,START+400):
   if proc.poll() is not None:raise RuntimeError(f'QEMU exited waiting for debug shell: {proc.returncode}')
   try:data=SERIAL.read_bytes()
   except OSError as e:write('serial-read-errors.log',f'{utc()} {e!r}\n');time.sleep(.25);continue
   if marker in data:seen=True;break
   if time.monotonic()-last>=30:log(f'waiting debug-shell marker serial_bytes={len(data)}');last=time.monotonic()
   time.sleep(.5)
  if not seen:raise RuntimeError('debug-shell serial marker absent by 400s')
  log('serial confirms debug-shell.service running')
  mon('sendkey ctrl-alt-f9','tty9-switch');write('tty9-switch-record.txt',f'utc={utc()} elapsed={time.monotonic()-START:.1f}s sent HMP sendkey ctrl-alt-f9 after serial marker\n')
  key_start=time.monotonic();prompt=False;idx=0;next_frame=key_start+15;last_frame=None
  review_deadline=min(DEADLINE-25,START+575)
  while time.monotonic()<review_deadline:
   while time.monotonic()<min(next_frame,review_deadline):
    if proc.poll() is not None:raise RuntimeError(f'QEMU exited during prompt review: {proc.returncode}')
    import select
    ready,_,_=select.select([sys.stdin],[],[],min(.5,min(next_frame,review_deadline)-time.monotonic()))
    if ready:
     token=sys.stdin.readline().strip()
     write(f'frame-{idx:02d}-operator-input.txt',f'{utc()} elapsed={time.monotonic()-START:.1f}s token={token!r}\n')
     if token=='PROMPT' and last_frame is not None:
      prompt=True;write('prompt-confirmation.txt',f'manual visual confirmation for complete frame {last_frame.name} at {utc()}\n');break
     log(f'ignored operator token at prompt gate: {token!r}')
   if prompt or time.monotonic()>=review_deadline:break
   idx+=1
   last_frame=snap(f'vga-after-f9-{idx:02d}')
   log(f'COMPLETE FRAME READY for manual visual review: {last_frame}; no guest text has been sent')
   print(f'FRAME {idx:02d} READY: {last_frame} (enter PROMPT only if this complete frame visibly shows sh-5.3#)',flush=True)
   next_frame=key_start+(idx+1)*15
  if not prompt:raise RuntimeError('no manual visual confirmation of tty9 shell before hard cap; no guest command sent')
  log('visually confirmed tty9 prompt; sending only harmless echo marker')
  guest('echo lc_run11_marker','marker-visible',1.5)
  marker_frame=OUT/'marker-visible.png'
  print(f'MARKER FRAME READY: {marker_frame} (enter MARKER only if shell prompt and lc_run11_marker output are visible)',flush=True)
  marker_ok=False
  while time.monotonic()<DEADLINE-25:
   if proc.poll() is not None:raise RuntimeError(f'QEMU exited awaiting marker review: {proc.returncode}')
   import select
   ready,_,_=select.select([sys.stdin],[],[],.5)
   if ready:
    token=sys.stdin.readline().strip()
    write('marker-operator-input.txt',f'{utc()} elapsed={time.monotonic()-START:.1f}s token={token!r}\n')
    if token=='MARKER':marker_ok=True;break
    log(f'ignored operator token at marker gate: {token!r}')
  if not marker_ok:raise RuntimeError('harmless echo not manually confirmed; no diagnostic commands sent')
  log('harmless marker visually confirmed; start prioritized read-only state collection')
  units=['systemd-nsresourced.service','systemd-nsresourced.socket','modprobe@tun.service','modprobe@overlay.service','systemd-sysext.service','systemd-pcrphase-sysinit.service','var-lib-machines.mount']
  guest('date -u','guest-time',.8);guest('cat /proc/cmdline','kernel-cmdline',.8);guest('uname -a','kernel-release',.8)
  guest('systemctl list-jobs --no-pager','initial-jobs',1.5);guest('systemctl --failed --no-pager','initial-failed',1)
  for unit in units:guest(f'systemctl status -l --no-pager {unit}','status-'+unit.replace('@','-at-').replace('.','-'),1.5)
  props=['ActiveState','SubState','Result','Job','After','Wants','Requires','TimeoutStartUSec']
  for unit in units:
   cmd='systemctl show '+' '.join(f'-p {p}' for p in props)+f' {unit}'
   guest(cmd,'show-'+unit.replace('@','-at-').replace('.','-'),1.5)
  for unit in units:guest(f'journalctl -b -u {unit} --no-pager -n 15','journal-'+unit.replace('@','-at-').replace('.','-'),1.5)
  guest('systemd-analyze critical-chain','critical-chain',1.5);guest('ps -eo pid,stat,wchan:24,args','processes',1.5);guest('cat /proc/modules','proc-modules',1.5)
  # Near-timeout comparison while retaining the same guest/ISO.
  target=START+500
  while time.monotonic()<min(target,DEADLINE-55) and proc.poll() is None:time.sleep(min(15,min(target,DEADLINE-55)-time.monotonic()))
  if proc.poll() is None and time.monotonic()<DEADLINE-40:
   write('near-timeout-capture-time.txt',f'{utc()} elapsed={time.monotonic()-START:.1f}s\n')
   guest('systemctl list-jobs --no-pager','near-timeout-jobs',1.5)
   guest('systemctl --failed --no-pager','near-timeout-failed',1)
   for unit in ['systemd-nsresourced.service','systemd-nsresourced.socket','systemd-pcrphase-sysinit.service','systemd-sysext.service','var-lib-machines.mount']:
    guest(f'systemctl status -l --no-pager {unit}','near-timeout-status-'+unit.replace('@','-at-').replace('.','-'),1.5)
   for unit in ['systemd-nsresourced.service','systemd-nsresourced.socket','systemd-pcrphase-sysinit.service','systemd-sysext.service','var-lib-machines.mount']:
    guest(f'journalctl -b -u {unit} --no-pager -n 20','near-timeout-journal-'+unit.replace('@','-at-').replace('.','-'),1.5)
   guest('systemd-analyze critical-chain','near-timeout-critical-chain',1.5)
  log(f'operator stop before hard cap at elapsed={time.monotonic()-START:.1f}s')
  try:mon('quit','quit')
  except Exception:pass
  try:proc.wait(timeout=8)
  except subprocess.TimeoutExpired:
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait()
  log(f'qemu_exit={proc.returncode}')
finally:
 if vnc:
  try:vnc.close()
  except Exception:pass
 if hmp:
  try:hmp.close()
  except Exception:pass
 if proc and proc.poll() is None:
  proc.terminate()
  try:proc.wait(timeout=5)
  except subprocess.TimeoutExpired:proc.kill();proc.wait()
 try:write('iso-after.sha256',f'{sha(ISO)}  {ISO}\n')
 except Exception as e:write('iso-after-error.txt',repr(e)+'\n')
 write('result.txt',f'elapsed_seconds={time.monotonic()-START:.1f}\nqemu_exit={proc.returncode if proc else "not-started"}\niso_before={EXPECTED[ISO]}\niso_after={sha(ISO)}\nnetwork_device=none\nfirmware_writer=none\nstop_reason=bounded emulator diagnostic; QEMU exit is not guest success\n')
