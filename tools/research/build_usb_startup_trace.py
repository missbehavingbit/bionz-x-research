"""Generate passive trace sites from identity-matched original ARM/Thumb code."""
import argparse
import hashlib
import io
import json
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
from elftools.elf.elffile import ELFFile
from tools.research.decode_kernel_kallsyms import decode

ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();out=args.output.resolve()
    if not out.is_relative_to(ROOT/'artifacts'):raise ValueError('Output must stay under artifacts')
    out.mkdir(parents=True,exist_ok=False)
    paths=[ROOT/'artifacts/tests/qemu-usb-infra-e0597/libInfraUsbCmn.so',
           ROOT/'artifacts/qemu-a6400/inputs-moddev-1/nflasha3-Image']
    expected=['733acef066e3e3691f7258e7416b638a7960d99b70f27e85ddc4daad2b5ecef2',
              'd7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618']
    raw,kernel=[f.read_bytes() for f in paths]
    for f,data,digest in zip(paths,[raw,kernel],expected):
        if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Identity mismatch: '+str(f))
    elf=ELFFile(io.BytesIO(raw));text=elf.get_section_by_name('.text')
    sites=[];instructions=[]
    for name in ['_ZN7CUsbSvc12createGadgetEP8CUSB_MES',
                 '_ZN10CUsbGadget12createGadgetEP7CUsbSvcP19CUSB_GADGET_REGINFOP19CUSB_GADGET_CREINFO',
                 '_ZN18CUsbGadgetBusPower9on_createEP7CUsbSvcP19CUSB_GADGET_REGINFOP19CUSB_GADGET_CREINFO']:
        s=elf.get_section_by_name('.dynsym').get_symbol_by_name(name)[0]
        start=s['st_value']&~1;size=s['st_size'];body=text.data()[start-text['sh_addr']:start-text['sh_addr']+size]
        rows=list(Cs(CS_ARCH_ARM,CS_MODE_THUMB).disasm(body,start))
        chosen={start}
        for ins in rows:
            instructions.append(f'{ins.address:08x} {ins.bytes.hex()} {ins.mnemonic} {ins.op_str}')
            if ins.mnemonic in ('bl','blx'):chosen.add(ins.address+ins.size)
            if ins.mnemonic in ('pop','pop.w') and 'pc' in ins.op_str:chosen.add(ins.address)
        for offset in sorted(chosen):
            pos=offset-text['sh_addr'];data=text.data()[pos:pos+16]
            sites.append({'kernel':False,'offset':offset,'bytes':data.hex(),'function':name})
    symbols=decode(kernel)['symbols'];chosen=set()
    for name in ('usb_otgcore_ioctl','usb_gadgetcore_ioctl','fusb_get_mode','fusb_start_gadget'):
        matches=[s['address'] for s in symbols if s['name']==name]
        if len(matches)!=1:raise ValueError('Ambiguous kernel symbol '+name)
        start=matches[0];end=min(s['address'] for s in symbols if s['address']>start)
        chosen.add(start)
        decoder=Cs(CS_ARCH_ARM,CS_MODE_ARM)
        # Decode each aligned candidate independently: inline jump-table words
        # must not stop scanning before the real epilogue. Runtime PC plus
        # the exact 16-byte fingerprint qualifies an executed site.
        rows=[ins for address in range(start,end,4) for ins in decoder.disasm(
            kernel[address-0x60108000:address-0x60108000+4],address,count=1)]
        for ins in rows:
            instructions.append(f'{ins.address:08x} {ins.bytes.hex()} {ins.mnemonic} {ins.op_str}')
            if name=='fusb_start_gadget':chosen.add(ins.address)
            if ('pc' in ins.op_str and ins.mnemonic.startswith(('pop','ldm'))) or (ins.mnemonic=='bx' and ins.op_str=='lr'):
                chosen.add(ins.address)
    for offset in sorted(chosen):
        data=kernel[offset-0x60108000:offset-0x60108000+16]
        sites.append({'kernel':True,'offset':offset,'bytes':data.hex(),'function':'kernel_ioctl'})
    lines=['static const struct { bool kernel; uint32_t offset; uint8_t bytes[16]; } sites[] = {']
    for s in sites:
        lines.append('{'+('true' if s['kernel'] else 'false')+f',0x{s["offset"]:x},'+'{'+','.join('0x'+s['bytes'][i:i+2] for i in range(0,32,2))+'}},')
    lines.append('};')
    (out/'usb_trace_sites.h').write_text('\n'.join(lines)+'\n')
    (out/'sites.json').write_text(json.dumps({'inputs':list(zip(map(str,paths),expected)),'sites':sites},indent=2)+'\n')
    (out/'instructions.txt').write_text('\n'.join(instructions)+'\n')
    print(f'{len(sites)} passive sites generated')

if __name__=='__main__':main()
