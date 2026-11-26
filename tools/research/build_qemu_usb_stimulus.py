"""Generate identity guards for a QEMU-only original USB-service stimulus."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def _find_dynsym_code(raw: bytes, symbol: str) -> tuple[int, bytes]:
    if raw[:5] != b'\x7fELF\x01':
        raise ValueError('Expected 32-bit little-endian ELF')
    e_phoff, e_shoff, _, _, e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(
        '<IIIHHHHHH', raw, 28
    )
    secs = [struct.unpack_from('<10I', raw, e_shoff + i * e_shentsize) for i in range(e_shnum)]
    shstr = raw[secs[e_shstrndx][4] : secs[e_shstrndx][4] + secs[e_shstrndx][5]]
    dynsym = next(
        (s for s in secs if shstr[s[0] : shstr.index(0, s[0])] == b'.dynsym'),
        None,
    )
    if dynsym is None:
        raise ValueError('Missing .dynsym')
    dynstr_sec = secs[dynsym[6]]
    dynstr = raw[dynstr_sec[4] : dynstr_sec[4] + dynstr_sec[5]]
    target_b = symbol.encode('ascii')
    matches = []
    for off in range(0, dynsym[5], 16):
        st_name, st_value, _, _, _, st_shndx = struct.unpack_from('<IIIBBH', raw, dynsym[4] + off)
        end = dynstr.index(0, st_name)
        if dynstr[st_name:end] == target_b and st_shndx != 0:
            matches.append(st_value)
    if len(matches) != 1:
        raise ValueError('Missing or ambiguous definition ' + symbol)
    va = matches[0]
    for i in range(e_phnum):
        p_type, p_offset, p_vaddr, _, p_filesz, _, _, _ = struct.unpack_from('<8I', raw, e_phoff + i * e_phentsize)
        if p_type == 1 and p_vaddr <= (va & ~1) < p_vaddr + p_filesz:
            foff = p_offset + (va & ~1) - p_vaddr
            return va, raw[foff : foff + 16]
    raise ValueError('Function code absent')

ROOT=Path(__file__).resolve().parents[2]
PARTS=ROOT/'firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev'
PARTS_A6400A=ROOT/'firmware/extracted_a6400a_v101/firmware.tar_unpacked/0700_part_image/dev'
LIBS={
    'usb':('nflasha15_unpacked/lib/libUsbControl.so',0x04857000,'/usr/lib/libUsbControl.so','70cd5742fde9eea78c60905e4015531b4c3ab2668d2cb85c934531f451f1211f'),
    'mwf':('nflasha15_unpacked/lib/libMWF.so',0x040f7000,'/usr/lib/libMWF.so','a79dc0d558e6454e31e57ea3b026364be924d0e78c1b6101015125a249f7e274'),
    'libc':('nflasha7_unpacked/lib/libc-2.11.2.so',0x042c1000,'/lib/libc-2.11.2.so','bceb5cfb7769b54bf9c9d178b8a5f4579531c0f60e673cbcae872a8c7a3508fb'),
}
LIBS_A6400A={
    'usb':('nflasha15_unpacked/lib/libUsbControl.so',0x058e1000,'/usr/lib/libUsbControl.so','87b04eb5f7277926638a1624c07b1cdd84beec4bc3c5e7d89e3c4f207379b3b5'),
    'mwf':('nflasha15_unpacked/lib/libMWF.so',0x04656000,'/usr/lib/libMWF.so','4189dc2c88f7b4bc22537d42f6a35c0e3db8a78749557bda058b086360d238dc'),
    'libc':('nflasha7_unpacked/lib/libc-2.11.2.so',0x042a0000,'/lib/libc-2.11.2.so','bc93700df79ac8f67356e4dbf5ab5c4c656865455e18ef2860acc178f5186bc2'),
}
CAPTURED_HASHES={
    'usb':'b5cea9d2c92a0b0810353900871fa2806ca3064aa5d8cf3ca7db5ddb6f3940e2',
    'mwf':'0903f8f9257e71b1c6bfd76bc74863c686d5a6d13d2b982cccae63b4cd43a7b2',
    'libc':'2b294f4c256aebb2746cacddefc39c33c448a0d7f18c2813daa54109c445b910',
}
FUNCTIONS={
 'CONTROL_INSTANCE':('usb','_ZN10UsbControl11GetInstanceEv'),
 'OBJMSG_CTOR':('mwf','_ZN3MWF6ObjMsgC1Ejj'),
 'OBJMSG_DTOR':('mwf','_ZN3MWF6ObjMsgD1Ev'),
 'PARAM_CTOR':('mwf','_ZN3MWF17ParamStd_TemplateIiLNS_5Param4TypeE7EEC1Ei'),
 'PARAM_DTOR':('mwf','_ZN3MWF17ParamStd_TemplateIiLNS_5Param4TypeE7EED1Ev'),
 'ADD_PARAM':('mwf','_ZN3MWF6ObjMsg8AddParamEjRKNS_5ParamE'),
 'SEND_EVENT':('usb','_ZN3UFW9MsgSender7SendEvtEPN3MWF6ObjMsgE'),
 'MALLOC':('libc','malloc'), 'FREE':('libc','free'),
}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--target',choices=('ilce_6400','ilce_6400a'),default='ilce_6400')
    parser.add_argument('--captured-libs',type=Path,
                        help='explicit E0596 captured ELF profile; all three hashes remain pinned')
    args=parser.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to(ROOT/'artifacts'): raise ValueError('Output must stay under artifacts')
    out.mkdir(parents=True,exist_ok=False)
    lines=[]; checks=[]; loaded={}; report={}
    lib_spec=LIBS_A6400A if args.target=='ilce_6400a' else LIBS
    parts_dir=PARTS_A6400A if args.target=='ilce_6400a' else PARTS
    for key,(rel,base,guest,expected) in lib_spec.items():
        path=(args.captured_libs/Path(rel).name) if args.captured_libs else parts_dir/rel
        if args.captured_libs and args.target=='ilce_6400':expected=CAPTURED_HASHES[key]
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=expected: raise ValueError('Input identity mismatch: '+rel)
        loaded[key]=(raw,base)
        h=2166136261
        for b in raw: h=((h^b)*16777619)&0xffffffff
        ku=key.upper()
        lines.append(f'#define {ku}_BASE_REF 0x{base:08x}u')
        lines.append(f'#define {ku}_FILE_FNV 0x{h:08x}u')
        lines.append(f'#define {ku}_FILE_LEN {len(raw)}u')
        checks.append(f'(check_file("{guest}",0x{h:08x}u,{len(raw)}u) ? 1 : (log_value("file_{key}",0),0))')
        report[key]={'path':str(path.resolve()),'sha256':expected,'fnv':hex(h),'size':len(raw)}
    for label,(key,symbol) in FUNCTIONS.items():
        raw,base=loaded[key]
        va,code=_find_dynsym_code(raw,symbol)
        lines.append(f'#define F_{label} 0x{base+va:08x}u')
        lines.append(f'static const uint8_t usb_expected_{label}[16] __attribute__((used)) = {{'+','.join(hex(b) for b in code)+'};')
        checks.append(f'(same_code(F_{label},usb_expected_{label}) ? 1 : (log_value("code_{label}",F_{label}),0))')
        report[label]={'symbol':symbol,'elf_va':hex(va),'reference':hex(base+va),'bytes':code.hex()}
    usb_slot = 0x058ffac4 if args.target == 'ilce_6400a' else 0x04875ac4
    lines.append(f'#define USB_INTERFACE_SLOT 0x{usb_slot:08x}u')
    # Definition follows the local check helpers in the stimulus source.
    lines.append('#define verify_inputs() ('+' && '.join(checks)+')')
    (out/'usb_symbols.h').write_text('\n'.join(lines)+'\n')
    (out/'inputs.json').write_text(json.dumps(report,indent=2)+'\n')

if __name__=='__main__':main()
