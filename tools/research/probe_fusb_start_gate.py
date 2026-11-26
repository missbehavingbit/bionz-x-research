"""Bounded original fusb_start_gadget guard execution with declared fixtures."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn import arm_const as A

ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report-dir',type=Path,required=True)
    args=p.parse_args();out=args.report_dir.resolve()
    if not out.is_relative_to(ROOT/'artifacts'):raise ValueError('Output must stay under artifacts')
    out.mkdir(parents=True,exist_ok=False)
    raw=(ROOT/'artifacts/qemu-a6400/inputs-moddev-1/nflasha3-Image').read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    if digest!='d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618':
        raise ValueError('Captured kernel identity mismatch')
    cases=[('gate_clear',2,1,1,0,False,0xfffffff0),('wrong_mode',1,1,1,1,False,0xfffffff0),
           ('cid_clear',2,0,1,1,False,0xfffffff0),('speed_clear',2,1,0,1,False,0xfffffff0),
           ('gate_bit1_only',2,1,1,2,False,0xfffffff0),('gate_set',2,1,1,1,False,0),
           ('gate_all_bits',2,1,1,0xffffffff,False,0),('callback_boundary',2,1,1,1,True,None)]
    rows=[]
    for name,mode,cid,speed,gate,callback,result in cases:
        m=Uc(UC_ARCH_ARM,UC_MODE_ARM)
        context,cb,stack,stop=0x60010000,0x60020000,0x61000000,0x62000000
        target=0x602f0000
        for address in (0x602ac000,0x6051b000,context,cb,stack,stop,target,0xf0200000):m.mem_map(address,4096)
        m.mem_write(0x602ac000,raw[0x602ac000-0x60108000:0x602ad000-0x60108000])
        def put(a,v):m.mem_write(a,struct.pack('<I',v))
        put(0x6051b024,context);put(context+0x80,mode);put(context+0x84,cid);put(context+0x88,speed)
        put(context+0x1c,0xf0200000);put(0xf0200024,gate)
        if callback:put(context+0xbc,cb);put(cb+8,target)
        state={'stop':None,'instructions':0}
        def instruction(cpu,address,size,data):
            state['instructions']+=1
            if address in (stop,target):
                state['stop']='unsupported_callback' if address==target else 'returned'
                cpu.emu_stop()
        m.hook_add(UC_HOOK_CODE,instruction)
        m.reg_write(A.UC_ARM_REG_R0,context+8);m.reg_write(A.UC_ARM_REG_SP,stack+0xf00)
        m.reg_write(A.UC_ARM_REG_LR,stop)
        m.emu_start(0x602aca8c,0,count=100)
        actual=m.reg_read(A.UC_ARM_REG_R0)
        final=struct.unpack('<I',m.mem_read(context+0x80,4))[0]
        if callback:
            assert state['stop']=='unsupported_callback' and final==mode
        else:
            assert state['stop']=='returned' and actual==result
            assert final==(3 if result==0 else mode)
        rows.append({'case':name,'mode':mode,'cid':cid,'speed':speed,'gate':gate,'callback':callback,
                     **state,'r0':hex(actual),'final_mode':final})
    report={'kernel_sha256':digest,'classification':'constructed guard fixtures; not device execution',
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'cases':rows,'status':'passed'}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'PASS: {len(rows)} original guard cases, including unsupported callback stop')

if __name__=='__main__':main()
