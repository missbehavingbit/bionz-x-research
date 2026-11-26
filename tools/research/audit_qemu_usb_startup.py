"""Audit a retained QEMU USB startup receipt; never contact the guest or camera."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('report_dir',type=Path)
    p.add_argument('--expect-blocked',action='store_true')
    p.add_argument('--expect-enumerated',action='store_true')
    args=p.parse_args();directory=args.report_dir.resolve()
    if args.expect_blocked and args.expect_enumerated:raise ValueError('Choose one receipt mode')
    if not directory.is_relative_to(ROOT/'artifacts'):raise ValueError('Receipt must stay under artifacts')
    raw=(directory/'report.json').read_bytes();report=json.loads(raw)
    console=(directory/'console.log').read_bytes()
    text=console.decode('utf-8','replace')
    rows=[]
    for line in text.splitlines():
        if not line.startswith('[USB startup]'):continue
        fields=dict(re.findall(r'(\w+)=([\w]+)',line))
        if 'r0' not in fields:raise ValueError('Trace row lacks register observations')
        rows.append(fields)
    checks=[]
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append(name)
    def seen(kind,offset,reg,value):
        return any(r.get('kind')==kind and int(r['offset'],16)==offset and
                   int(r.get(reg,'-1'),16)==value for r in rows)
    check('owned QEMU observation completed',report['status']=='observed' and report['qemu_exit']==0 and report['owned_exit']==0)
    check('original create entered',seen('infra',0xbbc4,'offset',0xbbc4))
    check('SET_MODE returned zero',seen('infra',0xbbfa,'r0',0))
    guest=report.get('guest') or ''
    if args.expect_blocked:
        check('E305 user return failed',seen('infra',0xbc0c,'r0',0xffffffff))
        check('kernel EBUSY observed',seen('kernel',0x602b817c,'r0',0xfffffff0))
        check('original create returned error 14',seen('infra',0xbf2c,'r0',14))
        check('service error state sampled','service_state 00000007' in guest)
        check('no device packet or RUN',not report['host_lines'] and not report['controller_run'])
    else:
        check('E305 user return zero',seen('infra',0xbc0c,'r0',0))
        check('gadget callback registration requested',seen('kernel',0x602b8844,'r1',0x401ce201))
        check('original descriptors committed',seen('kernel',0x602b8844,'r1',0x400ce203))
        check('original create returned zero',seen('infra',0xbf2c,'r0',0))
        check('service state 3 and created byte 1 sampled',
              'service_state 00000003' in guest and 'gadget_created 00000001' in guest)
        check('guest armed event ring and RUN',report['controller_run'] and
              int(report['attached']['GEVNTADRLO0'],16)!=0 and int(report['attached']['GEVNTSIZ0'],16)==4096)
        packets=[bytes.fromhex(line[5:]) for line in report['host_lines'] if line.startswith('IN 1 ')]
        device=bytes.fromhex('12010002000000404c056f09000101020301')
        if args.expect_enumerated:
            check('strict EP0 selected',report.get('strict_ep0') is True)
            enum=report.get('enumeration') or {}
            check('original descriptors and configuration readback',
                  enum.get('device_hex')==device.hex() and enum.get('configuration_hex')==
                  '0902220001010080fa09040000010300000009211101000122130007058103200001' and
                  enum.get('get_configuration_hex')=='01')
            events=report.get('host_events',[]);completed=[]
            check('complete token ledger',len(events)>0 and len(events)%2==0)
            for i in range(0,len(events),2):
                tx=events[i].get('tx');rx=events[i+1].get('rx')
                check(f'token pair {i//2}',isinstance(tx,str) and isinstance(rx,str))
                if rx.startswith('NAK '):
                    ep=1 if tx=='IN 1' else 0
                    check(f'NAK endpoint {i//2}',rx==f'NAK {ep}')
                    continue
                completed.append((tx,rx))
            expected=[
                ('SETUP 8006000100001200','ACK 0 8'),('IN 1','IN 1 '+device.hex()),('OUT 0 -','ACK 0 0'),
                ('SETUP 0005010000000000','ACK 0 8'),('IN 1','IN 1 '),
                ('SETUP 8006000200000900','ACK 0 8'),('IN 1','IN 1 0902220001010080fa'),('OUT 0 -','ACK 0 0'),
                ('SETUP 8006000200002200','ACK 0 8'),('IN 1','IN 1 '+enum.get('configuration_hex','')),('OUT 0 -','ACK 0 0'),
                ('SETUP 0009010000000000','ACK 0 8'),('IN 1','IN 1 '),
                ('SETUP 8008000000000100','ACK 0 8'),('IN 1','IN 1 01'),('OUT 0 -','ACK 0 0')]
            check('all six original control transfers and status phases',completed==expected)
            check('native address 1',int(report['attached']['DCFG'],16)>>3&0x7f==1)
            check('original UDC configured before disconnect',
                  (report.get('guest_before_disconnect') or '').strip()=='configured')
        else:
            check('original bus-power DEVICE descriptor',packets==[device])
    result={'status':'passed','expected_blocked':args.expect_blocked,'checks':checks,
            'report_sha256':hashlib.sha256(raw).hexdigest(),'console_sha256':hashlib.sha256(console).hexdigest(),
            'auditor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'enumeration_completed':args.expect_enumerated,'uvc_streaming_verified':False}
    (directory/'startup-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'PASS: {len(checks)} receipt checks; UVC remains unverified')

if __name__=='__main__':main()
