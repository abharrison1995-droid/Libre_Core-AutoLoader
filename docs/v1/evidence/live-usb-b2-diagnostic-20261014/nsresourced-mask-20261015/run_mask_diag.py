import datetime, hashlib, json, os, pathlib, signal, socket, subprocess, sys, time

run = pathlib.Path(sys.argv[1])
iso = pathlib.Path('/home/alex/librecore-usb-kms-output-20261013-b2/candidate-bundle/libre_core_autoloader-2026.09.29-x86_64.iso')
kernel = pathlib.Path('/mnt/c/Users/Alex/AppData/Local/Temp/lc-b2-diagnostic-20261014/direct/vmlinuz-linux')
initrd = pathlib.Path('/mnt/c/Users/Alex/AppData/Local/Temp/lc-b2-diagnostic-20261014/direct/initramfs-linux.img')
sock_path = pathlib.Path('/tmp/lc-b2-nsresourced-mask-20261015.sock')
append = 'archisobasedir=arch archisosearchuuid=2026-10-13-22-51-40-00 console=tty0 console=ttyS0,115200n8 systemd.log_level=debug systemd.log_target=console systemd.debug_shell=1 systemd.mask=systemd-nsresourced.service systemd.mask=systemd-nsresourced.socket'
argv = ['qemu-system-x86_64','-accel','tcg,thread=multi','-m','3072','-smp','4','-kernel',str(kernel),'-initrd',str(initrd),'-append',append,'-cdrom',str(iso),'-boot','d','-no-reboot','-no-shutdown','-vga','std','-display','vnc=127.0.0.1:49','-nic','none','-serial',f'file:{run / "serial.log"}','-monitor',f'unix:{sock_path},server=on,wait=off']
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()
def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
for p in (iso,kernel,initrd):
    if not p.is_file(): raise SystemExit(f'missing input: {p}')
if sock_path.exists(): raise SystemExit(f'refusing existing monitor socket: {sock_path}')
(run/'qemu-version.txt').write_text(subprocess.check_output(['qemu-system-x86_64','--version'],text=True))
(run/'inputs-before.sha256').write_text('\n'.join(f'{sha(p)}  {p}' for p in (iso,kernel,initrd))+'\n')
(run/'command.txt').write_text('QEMU argv JSON:\n'+json.dumps(argv,indent=2)+'\n\nKernel append:\n'+append+'\n\nSafety: -nic none; no writable disk; no firmware writer.\n')
started=utc(); mono=time.monotonic(); hostlog=(run/'qemu-host.log').open('wb')
proc=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=hostlog,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
(run/'launcher.log').write_text(f'launch_utc={started}\nqemu_pid={proc.pid}\n')
monitor=None
try:
    deadline=mono+25
    while time.monotonic()<deadline and proc.poll() is None:
        try:
            monitor=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); monitor.settimeout(1)
            monitor.connect(str(sock_path)); break
        except OSError:
            if monitor: monitor.close()
            monitor=None; time.sleep(.2)
    if monitor is None:
        (run/'launcher.log').write_text((run/'launcher.log').read_text()+f'monitor_connect=failed\nqemu_poll={proc.poll()}\n')
    else:
        monitor.settimeout(.15)
        try: greeting=monitor.recv(8192).decode('utf-8','replace')
        except socket.timeout: greeting=''
        with (run/'monitor.log').open('w',encoding='utf-8') as mf:
            mf.write(f'connected_utc={utc()}\n{greeting}\n')
            def mon(cmd, wait=1.2):
                mf.write(f'[{utc()}] > {cmd}\n'); mf.flush()
                monitor.sendall((cmd+'\r\n').encode())
                end=time.monotonic()+wait; out=[]
                while time.monotonic()<end:
                    try:
                        data=monitor.recv(8192)
                        if not data: break
                        out.append(data.decode('utf-8','replace'))
                    except socket.timeout: continue
                result=''.join(out); mf.write(result+'\n'); mf.flush(); return result
            checkpoints=[45,90,135,180,225,270]
            next_index=0
            with (run/'checkpoints.txt').open('w',encoding='utf-8') as cf:
                while time.monotonic()-mono < 280 and proc.poll() is None:
                    elapsed=time.monotonic()-mono
                    serial=run/'serial.log'
                    if next_index<len(checkpoints) and elapsed>=checkpoints[next_index]:
                        stamp=utc(); size=serial.stat().st_size if serial.exists() else 0
                        tail=''
                        if serial.exists():
                            tail=serial.read_bytes()[-6000:].decode('utf-8','replace').replace('\x00','')
                        cf.write(f'[{stamp}] elapsed={elapsed:.1f}s process={proc.poll()} serial_bytes={size}\n')
                        cf.write('serial_tail_begin\n'+tail+'\nserial_tail_end\n'); cf.flush()
                        frame=run/f'frame-{int(checkpoints[next_index]):03d}s.ppm'
                        mon(f'screendump {frame}',1.5)
                        next_index+=1
                    time.sleep(.25)
                elapsed=time.monotonic()-mono
                (run/'launcher.log').write_text((run/'launcher.log').read_text()+f'pre_stop_elapsed={elapsed:.2f}s\nqemu_poll_before_stop={proc.poll()}\n')
                if proc.poll() is None and monitor:
                    mon('info status',.5)
                    finalframe=run/'frame-final.ppm'; mon(f'screendump {finalframe}',1.5)
                    mon('quit',.5)
finally:
    if proc.poll() is None:
        try: os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait(timeout=3)
    ended=utc(); elapsed=time.monotonic()-mono
    hostlog.close()
    if monitor:
        try: monitor.close()
        except OSError: pass
    (run/'inputs-after.sha256').write_text('\n'.join(f'{sha(p)}  {p}' for p in (iso,kernel,initrd))+'\n')
    (run/'result.txt').write_text(f'start_utc={started}\nend_utc={ended}\nelapsed_seconds={elapsed:.2f}\nqemu_exit={proc.returncode}\nstop_reason=bounded diagnostic stopped by HMP quit near 280 seconds (hard cap 300 seconds)\niso_unchanged={sha(iso)==(run/"inputs-before.sha256").read_text().splitlines()[0].split()[0]}\nkernel_sha256={sha(kernel)}\ninitramfs_sha256={sha(initrd)}\nnetwork_device=none\nwritable_disk=none\nfirmware_writer=none\n')
