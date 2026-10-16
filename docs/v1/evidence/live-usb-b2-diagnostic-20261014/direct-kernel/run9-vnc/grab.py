import socket,struct,pathlib,sys,time
host='127.0.0.1'; port=5903
s=socket.create_connection((host,port),3); s.settimeout(5)
def exact(n):
 b=b''
 while len(b)<n: b+=s.recv(n-len(b))
 return b
ver=exact(12); s.sendall(ver); n=exact(1)[0]; types=exact(n); s.sendall(b'\x01'); result=struct.unpack('>I',exact(4))[0]
if result: raise SystemExit('security failed')
s.sendall(b'\x01'); init=exact(24); w,h=struct.unpack('>HH',init[:4]); namelen=struct.unpack('>I',init[20:])[0]; name=exact(namelen)
s.sendall(b'\x00\x00\x00\x00'+struct.pack('>BBBBHHHBBB3x',32,24,0,1,255,255,255,16,8,0))
s.sendall(b'\x02\x00\x00\x01'+struct.pack('>i',0))
time.sleep(.5)
def key(k,d): s.sendall(struct.pack('>BBHI',4,1 if d else 0,0,k))
key(0xffe3,1);key(0xffe9,1);key(0xffc6,1);key(0xffc6,0);key(0xffe9,0);key(0xffe3,0)
time.sleep(1)
s.sendall(struct.pack('>BBHHHH',3,0,0,0,w,h))
msg=exact(4)
if msg[0]!=0: raise SystemExit(f'unexpected server msg {msg[0]}')
nrect=struct.unpack('>H',msg[2:4])[0]; out=bytearray()
for _ in range(nrect):
 hdr=exact(12);x,y,rw,rh,enc=struct.unpack('>HHHHi',hdr)
 if enc!=0: raise SystemExit(f'nonraw encoding {enc}')
 data=exact(rw*rh*4)
 for i in range(0,len(data),4): out.extend((data[i+2],data[i+1],data[i]))
ppm=b'P6\n%d %d\n255\n'%(w,h)+out
p=pathlib.Path(sys.argv[1]);p.write_bytes(ppm);print(f'{w}x{h} {len(ppm)} bytes server={name!r}')
s.close()
