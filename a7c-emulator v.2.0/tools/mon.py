import socket,time,sys,re
s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); s.settimeout(3)
def rd():
    b=b''
    try:
        while not b.rstrip().endswith(b'(qemu)'): b+=s.recv(65536)
    except Exception: pass
    return re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',b.decode(errors='replace'))
rd()
for c in sys.argv[2:]:
    s.send((c+'\r\n').encode()); time.sleep(0.5); r=rd(); print(r)
