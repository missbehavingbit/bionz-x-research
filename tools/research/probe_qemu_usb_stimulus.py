"""Bounded original-mod guard checks; stop before the first firmware function call."""
import argparse
import io
import json
from pathlib import Path

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, UC_HOOK_INTR
from unicorn import arm_const as A
from tools.research.build_qemu_usb_stimulus import LIBS, PARTS
from tools.research.pack_mod import extract_elf_text


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('build',type=Path)
    args=p.parse_args()
    _,code,symbols=extract_elf_text((args.build/'stimulus.elf').read_bytes())
    identities=json.loads((args.build/'inputs.json').read_text())
    reports=[]
    for case in ('no_marker','wrong_file','wrong_code','matching'):
        m=Uc(UC_ARCH_ARM,UC_MODE_THUMB)
        base,stack,stop=0x30000000,0x50000000,0x5e000000
        m.mem_map(base,0x10000);m.mem_write(base,code)
        m.mem_map(stack,0x10000);m.mem_map(stop,4096);m.mem_write(stop,b'\x00\xbe')
        files={'/proc/cmdline':b'console=ttyAM0,115200n8'}
        if case!='no_marker':files['/tmp_bt/qemu-usb-service.allow']=b''
        for key,(_,ref,path,_) in LIBS.items():
            raw=Path(identities[key]['path']).read_bytes();files[path]=raw
            elf=ELFFile(io.BytesIO(raw))
            loads=[s for s in elf.iter_segments() if s['p_type']=='PT_LOAD']
            end=max(s['p_vaddr']+s['p_memsz'] for s in loads)
            m.mem_map(ref,(end+4095)&~4095)
            for s in loads:m.mem_write(ref+s['p_vaddr'],s.data())
        if case=='wrong_file':files['/usr/lib/libUsbControl.so']=b'wrong'
        if case=='wrong_code':
            address=int(identities['CONTROL_INSTANCE']['reference'],16)&~1
            m.mem_write(address,b'\x00\x00')
        fds={};log=[];state={'stop':None}
        call=int(identities['CONTROL_INSTANCE']['reference'],16)&~1
        def string(address):
            data=bytearray()
            for i in range(256):
                b=bytes(m.mem_read(address+i,1))
                if b==b'\0':return data.decode()
                data+=b
            raise ValueError('Unterminated path')
        def interrupt(cpu,number,data):
            syscall=cpu.reg_read(A.UC_ARM_REG_R7)
            a,b,c=[cpu.reg_read(r) for r in (A.UC_ARM_REG_R0,A.UC_ARM_REG_R1,A.UC_ARM_REG_R2)]
            if syscall==5:
                path=string(a)
                if path=='/tmp_bt/usb-service-probe.log':
                    fd=100;fds[fd]=[path,0];result=fd
                elif path in files:
                    fd=max([2,*fds])+1;fds[fd]=[path,0];result=fd
                else:result=-2
            elif syscall==3:
                path,offset=fds[a];block=files[path][offset:offset+c]
                cpu.mem_write(b,block);fds[a][1]+=len(block);result=len(block)
            elif syscall==4:
                if fds[a][0]!='/tmp_bt/usb-service-probe.log':raise AssertionError('Unexpected write')
                log.append(bytes(cpu.mem_read(b,c)).decode());result=c
            elif syscall==6:del fds[a];result=0
            else:raise AssertionError(f'Unsupported syscall {syscall}')
            cpu.reg_write(A.UC_ARM_REG_R0,result&0xffffffff)
        def instruction(cpu,address,size,data):
            if address in (stop,call):
                state['stop']='firmware_call_boundary' if address==call else 'returned'
                cpu.emu_stop()
        m.hook_add(UC_HOOK_INTR,interrupt);m.hook_add(UC_HOOK_CODE,instruction)
        m.reg_write(A.UC_ARM_REG_SP,stack+0xff00);m.reg_write(A.UC_ARM_REG_LR,stop|1)
        m.reg_write(A.UC_ARM_REG_R0,1)
        m.emu_start(base+symbols['usb_probe_on'],0,count=12000000)
        expected='firmware_call_boundary' if case=='matching' else 'returned'
        if state['stop']!=expected:raise AssertionError((case,state))
        reports.append({'case':case,**state,'log':''.join(log)})
    (args.build/'guard-checks.json').write_text(json.dumps(reports,indent=2)+'\n')
    print(json.dumps(reports,indent=2))

if __name__=='__main__':main()
