"""Native qtest of strict EP0 ownership, status ZLP, NRDY and explicit boundaries."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu-dir',type=Path,required=True)
    p.add_argument('--report-dir',type=Path,required=True)
    p.add_argument('--strict-bulk-out',action='store_true',
                   help='also validate bounded bulk OUT with a separate native qtest fixture')
    args=p.parse_args();out=args.report_dir.resolve()
    if not out.is_relative_to(ROOT/'artifacts'):raise ValueError('Output must stay under artifacts')
    out.mkdir(parents=True,exist_ok=False)
    binary=args.qemu_dir.resolve()/'build/qemu-system-arm'
    checks=[]
    with tempfile.TemporaryDirectory(prefix='strict-ep0-qtest-') as temp:
        scratch=Path(temp);qp=scratch/'qtest';hp=scratch/'host'
        command=[str(binary),'-M','sony-a6400,dwc3-gadget=on','-accel','qtest','-display','none',
                 '-qtest',f'unix:{qp},server=on,wait=off',
                 '-chardev',f'socket,id=host,path={hp},server=on,wait=off',
                 '-global','cxd90045-dwc3.chardev=host','-global','cxd90045-dwc3.strict-ep0=on']
        if args.strict_bulk_out:
            command += ['-global', 'cxd90045-dwc3.strict-bulk=on']
        with (out/'qemu.log').open('wb') as log:
            proc=subprocess.Popen(command,stdout=log,stderr=log)
            try:
                def connect(path):
                    channel=socket.socket(socket.AF_UNIX);channel.settimeout(2)
                    for _ in range(100):
                        try:channel.connect(str(path));return channel
                        except (FileNotFoundError,ConnectionRefusedError):
                            if proc.poll() is not None:raise RuntimeError('QEMU exited')
                            time.sleep(.05)
                    raise TimeoutError(str(path))
                with connect(qp) as test,connect(hp) as host:
                    stream=test.makefile('rwb',buffering=0)
                    def qt(line):
                        stream.write((line+'\n').encode());return stream.readline().decode().strip()
                    def write(address,value):
                        assert qt(f'writel {address:#x} {value:#x}')=='OK'
                    def read(address):
                        reply=qt(f'readl {address:#x}');assert reply.startswith('OK ')
                        return int(reply[3:],16)
                    def check(name,condition):
                        if not condition:raise AssertionError(name)
                        checks.append(name)
                    def token(line,expected):
                        host.sendall((line+'\n').encode())
                        reply=b''
                        while b'\n' not in reply:
                            chunk=host.recv(4096)
                            if not chunk:
                                raise EOFError(f'No host reply for {line[:60]}')
                            reply+=chunk
                        check(line[:60],reply.decode()==expected+'\n')
                    def arm(ep,kind,length,extra=0,address=0x13000):
                        trb=0x12000+ep*0x100
                        for i,value in enumerate([address,0,length,(kind<<4)|0x803|extra]):write(trb+4*i,value)
                        reg=0xf020c800+ep*16
                        write(reg+4,trb);write(reg+8,0);write(reg+12,0x406)
                        return trb
                    def ack_events():write(0xf020c40c,read(0xf020c40c))
                    write(0xf020c400,0x10000);write(0xf020c408,4096)
                    write(0xf020c720,3);write(0xf020c704,0x80000000)
                    for ep in (0,1):
                        reg=0xf020c800+16*ep
                        write(reg+4,0x500);write(reg+8,0);write(reg+12,0x401)
                    host.sendall(b'CONN\n')
                    setup=arm(0,2,8)
                    token('SETUP 8006000100000400','ACK 0 8')
                    check('SETUP releases only owned TRB',read(setup+12)&1==0)
                    ack_events();write(0x13000,0x44332211)
                    data=arm(1,5,4)
                    check('IN remains owned before host token',read(data+12)&1==1)
                    host.settimeout(.05)
                    try:unsolicited=host.recv(4096)
                    except socket.timeout:unsolicited=b''
                    check('no automatic IN reply',unsolicited==b'');host.settimeout(2)
                    token('OUT 0 -','ERROR control-direction-or-stage')
                    check('wrong direction preserves IN ownership',read(data+12)&1==1)
                    token('IN 1','IN 1 11223344')
                    check('IN token releases TRB',read(data+12)&1==0)
                    ack_events()
                    token('OUT 0 -','NAK 0')
                    check('status NRDY event encoded',read(0x10008)==0x20c0)
                    count=read(0xf020c40c)
                    token('OUT 0 -','NAK 0')
                    check('repeated NAK does not duplicate pending NRDY',read(0xf020c40c)==count)
                    ack_events();status=arm(0,4,64)
                    check('NAK token was not queued across arm',read(status+12)&1==1)
                    token('OUT 0 -','ACK 0 0')
                    check('status ZLP releases TRB',read(status+12)&1==0)
                    check('status OUT residual preserved',read(status+8)==64)
                    ack_events();setup=arm(0,2,8)
                    token('SETUP 8006zz0100000400','ERROR setup-encoding-or-length')
                    check('malformed SETUP preserves ownership',read(setup+12)&1==1)
                    token('SETUP '+'00'*1100,'ERROR host-line-too-long')
                    check('overflow line not partially executed',read(setup+12)&1==1)
                    token('SETUP 0005010000000000','ACK 0 8');ack_events()
                    status=arm(1,3,0)
                    check('status IN waits for token',read(status+12)&1==1)
                    token('IN 1','IN 1 ')
                    check('status IN ZLP releases TRB',read(status+12)&1==0)
                    ack_events();arm(0,2,8);token('SETUP 8006000100000400','ACK 0 8');ack_events()
                    data=arm(1,5,4,extra=4)
                    token('IN 1','ERROR unsupported-trb-length-flags-or-chain')
                    check('chain boundary preserves HWO',read(data+12)&1==1)
                    data=arm(1,5,2048)
                    token('IN 1','ERROR unsupported-trb-length-flags-or-chain')
                    check('large transfer not silently truncated',read(data+12)&1==1)
                    data=arm(1,8,4)
                    token('IN 1','ERROR trb-control-type')
                    check('unsupported LINK type preserves HWO',read(data+12)&1==1)
                    data=arm(1,5,4,address=0xf020c100)
                    token('IN 1','ERROR payload-dma-read')
                    check('MMIO payload not fabricated as RAM',read(data+12)&1==1)
                    if args.strict_bulk_out:
                        # Independent ring state from the EP0 assertions above.
                        write(0xf020c704,0x40000000)
                        write(0xf020c400,0x10000);write(0xf020c408,4096)
                        reg=0xf020c800+6*16
                        write(reg+4,(4<<25)|0x500);write(reg+8,(512<<3)|(2<<1))
                        write(reg+12,0x401)
                        write(0xf020c720,1<<6);write(0xf020c704,0x80000000)
                        command_out='OUT 4 0c0000000100011000000000'
                        token(command_out,'NAK 4')
                        check('bulk NRDY is routed to enabled physical EP6',read(0x10000)==0xcc)
                        token(command_out,'NAK 4')
                        check('bulk NRDY is not duplicated',read(0xf020c40c)==4)
                        ack_events()
                        data=arm(6,1,512)
                        write(0x13000,0xaaaaaaaa);write(0x1300c,0xaabbccdd)
                        check('NAK does not queue the command for a future TRB',
                              bool(read(data+12)&1) and read(0x13000)==0xaaaaaaaa)
                        token('OUT 4 0czz','ERROR out-encoding')
                        token('OUT 5 0c00','ERROR unsupported-host-command')
                        token(command_out,'ACK 4 12')
                        check('bulk OUT bytes are written without copying beyond the token',
                              (read(0x13000),read(0x13004),read(0x13008),read(0x1300c))==
                              (12,0x10010001,0,0xaabbccdd))
                        check('short bulk OUT preserves residual and transfers ownership',
                              read(data+8)==500 and read(data+12)&1==0)
                        check('complete event includes physical EP6, LST and short',
                              read(0x10004)==0xa04c and read(0xf020c40c)==4)
                        ack_events()
                        data=arm(6,1,8)
                        token(command_out,'ERROR unsupported-bulk-out-length')
                        check('oversized token leaves HWO and residual alone',
                              read(data+8)==8 and bool(read(data+12)&1))
                        data=arm(6,1,512,extra=4)
                        token(command_out,'ERROR unsupported-bulk-out-trb')
                        check('chained bulk OUT is untouched',bool(read(data+12)&1))
                        data=arm(6,1,512,address=0xf020c100)
                        token(command_out,'ERROR payload-dma-write')
                        check('non-RAM payload does not retire the bulk descriptor',
                              bool(read(data+12)&1) and read(0xf020c40c)==0)
                        host.sendall(b'DISC\n')
                        token(command_out,'NAK 4')
                        # E0476 Status Pre-Arm + Logical Bulk IN multi-TRB checks (independent ring state)
                        write(0xf020c704,0x40000000)
                        write(0xf020c400,0x10000);write(0xf020c408,4096)
                        for ep in (0,1):
                            reg=0xf020c800+16*ep
                            write(reg+4,0x500);write(reg+8,0);write(reg+12,0x401)
                        write(0xf020c720,3);write(0xf020c704,0x80000000)
                        host.sendall(b'CONN\n')
                        ack_events()
                        arm(0,2,8)
                        token('SETUP a181000102001a00','ACK 0 8')
                        ack_events()
                        write(0x16000,0x01010000)
                        arm(1,5,26,address=0x16000)
                        token('IN 1','IN 1 00000101'+'00'*22)
                        ack_events()
                        prearm_status=arm(0,4,64)
                        token('OUT 0 -','ACK 0 0')
                        check('pre-armed STATUS3 retires without NRDY',
                              (read(prearm_status+12)&1)==0 and read(0x10008)==0x8040)
                        ack_events()
                        reg_in=0xf020c800+7*16
                        write(reg_in+4,(9<<25)|0x500);write(reg_in+8,(512<<3)|(2<<1))
                        write(reg_in+12,0x401)
                        write(0xf020c720,(1<<6)|(1<<7)|3)
                        trb_base=0x14000
                        write(0x15000,0x00008d0c);write(0x15004,0x00000000);write(0x15008,0xd9ff0000)
                        # TRB 0: 8 bytes, LST=0 (ctrl=0x811) -> HWO|IOC|NORMAL
                        write(trb_base+0,0x15000);write(trb_base+4,0);write(trb_base+8,8);write(trb_base+12,(1<<4)|0x801)
                        # TRB 1: 4 bytes, LST=1 (ctrl=0x813) -> HWO|LST|IOC|NORMAL
                        write(trb_base+16,0x15008);write(trb_base+20,0);write(trb_base+24,4);write(trb_base+28,(1<<4)|0x803)
                        write(reg_in+4,trb_base);write(reg_in+8,0);write(reg_in+12,0x406)
                        token('IN 9','IN 9 0c8d000000000000')
                        check('logical Bulk IN 9 resolves physical EP7 TRB0',
                              (read(trb_base+12)&1)==0 and (read(trb_base+28)&1)==1)
                        ack_events()
                        token('IN 9','IN 9 0000ffd9')
                        check('logical Bulk IN 9 advances to TRB1 and completes LST',(read(trb_base+28)&1)==0)
                        check('logical Bulk IN 9 TRB0 XferComplete event',read(0x1000c)==0x4e)
                        check('logical Bulk IN 9 TRB1 XferComplete LST event',read(0x10010)==0x804e)
                        ack_events()
                        host.sendall(b'DISC\n')
                    stream.close()
            finally:
                proc.terminate();proc.wait(10)
    report={'status':'passed','classification':'constructed native qtest fixtures',
            'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'checks':checks}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'PASS: {len(checks)} native USB token contract checks')

if __name__=='__main__':main()
