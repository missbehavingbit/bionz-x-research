#!/usr/bin/env python3
"""Prepare emulator inputs from a Sony ILCE-7C dump (camera_dump_gui layout).

    python3 tools/a7c_extract.py "/path/to/Sony A7C dump"

Reads only the dump; writes into ./fw and ./img of this repository:
  fw/VMLINUX.BIN   /system/vmlinux.bin  (raw ARM Image -> PA 0x00108000)
  fw/INITRD.IMG    /system/initrd.img   (ext2 ramdisk  -> PA 0x00700000)
  fw/proc_cmdline  stock kernel command line (bootloader "kemco" string)
  fw/submicro_frames.bin  the power micro's two status frames (slot A, slot B)
                   from the camera's warm-boot snapshot (nflasha5), with the
                   "powering off" flag the snapshot was taken with cleared
  img/a7c_emmc.img eMMC: boot0 (nflashaB0) + boot1 (nflashaB1) + user area
                   (whole-disk nflasha), as is.

/setting/Backup.bin and /system/av-cam.bin are not extracted: the sony-a7c
machine copies them from the eMMC into RAM at every reset, like Sony's
bootloader.

Pure Python 3, no third-party modules.
"""
import hashlib
import json
import os
import struct
import sys



def sony_partitions(disk):
    """Sony '82461.00' table at LBA 0: entries {start, size, flag, -1} in bytes."""
    with open(disk, 'rb') as f:
        hdr = f.read(0x400)
    if not hdr.startswith(b'82461.00'):
        raise SystemExit('nflasha.raw: no Sony 82461.00 partition table at offset 0')
    parts = {}
    for i in range(48):
        start, size, flag, _ = struct.unpack_from('<IIII', hdr, 0x20 + 16 * i)
        if size:
            parts[i + 1] = (start, size, flag)
    return parts


class Fat16:
    """Minimal read-only FAT12/16 root-directory reader (8.3 names)."""

    def __init__(self, f, base):
        self.f, self.base = f, base
        b = self.read(0, 512)
        self.bps, self.spc, res, nfat, self.nroot = struct.unpack_from('<HBHBH', b, 11)
        tot16 = struct.unpack_from('<H', b, 19)[0]
        spf = struct.unpack_from('<H', b, 22)[0]
        tot = tot16 or struct.unpack_from('<I', b, 32)[0]
        self.fat_off = res * self.bps
        self.root_off = (res + nfat * spf) * self.bps
        self.data_off = self.root_off + ((self.nroot * 32 + self.bps - 1) // self.bps) * self.bps
        nclus = (tot - self.data_off // self.bps) // self.spc
        self.fat12 = nclus < 4085
        self.fat = self.read(self.fat_off, spf * self.bps)

    def read(self, off, n):
        self.f.seek(self.base + off)
        return self.f.read(n)

    def next(self, c):
        if self.fat12:
            v = struct.unpack_from('<H', self.fat, c + c // 2)[0]
            return v >> 4 if c & 1 else v & 0xfff
        return struct.unpack_from('<H', self.fat, 2 * c)[0]

    def root(self):
        d = self.read(self.root_off, self.nroot * 32)
        for i in range(self.nroot):
            e = d[32 * i:32 * i + 32]
            if e[0] == 0:
                break
            if e[0] == 0xE5 or e[11] & 0x08 or e[11] == 0x0F:
                continue
            name = e[:8].decode('latin1').rstrip()
            ext = e[8:11].decode('latin1').rstrip()
            yield (name + ('.' + ext if ext else '')).upper(), e

    def get(self, want):
        end = 0xff8 if self.fat12 else 0xfff8
        for name, e in self.root():
            if name == want.upper():
                c = struct.unpack_from('<H', e, 26)[0]
                size = struct.unpack_from('<I', e, 28)[0]
                csz = self.spc * self.bps
                out = bytearray()
                while 2 <= c < end and len(out) < size:
                    out += self.read(self.data_off + (c - 2) * csz, csz)
                    c = self.next(c)
                return bytes(out[:size])
        raise SystemExit('file %s not found in FAT partition' % want)


SM_FRAMES_PA = 0x034433A4      # A7C firmware: RX slot A; slot B at +0x100


def crc16(f):
    """CRC-16/CCITT-FALSE over bytes 0..125 (stored LE at 126..127)."""
    c = 0xFFFF
    for x in f[:126]:
        c ^= x << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021 if c & 0x8000 else c << 1) & 0xFFFF
    return c


def frame_ok(f, kind):
    return f[0] == kind and f[126] | f[127] << 8 == crc16(f)


def submicro_frames(nflasha5):
    """RX slot A + slot B (256 bytes) from the WBI snapshot."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from wbi import Wbi
    w = Wbi(nflasha5)
    try:
        raw = w.read(SM_FRAMES_PA, 0x180)
        f = bytearray(raw[:128] + raw[0x100:0x180])
    except SystemExit:
        f = bytearray(256)
    if not (frame_ok(f[:128], 0x20) and frame_ok(f[128:], 0x30)):
        # other firmware: scan the av-cam carve-out for the frame pair
        f = None
        for i, (addr, _v, osize, _o, _s) in enumerate(w.sections):
            if not 0x02E68000 <= addr < 0x048C6000:
                continue
            d = w.section(i)
            for o in range(0, len(d) - 0x180, 4):
                for gap in (0x100, 0x80):          # A7C: +0x100, a5000: +0x80
                    if d[o] == 0x20 and d[o + gap] == 0x30 and \
                            frame_ok(d[o:o + 128], 0x20) and \
                            frame_ok(d[o + gap:o + gap + 128], 0x30):
                        f = bytearray(d[o:o + 128] + d[o + gap:o + gap + 128])
                        break
                if f:
                    break
            if f:
                break
        if not f:
            raise SystemExit('nflasha5: sub-micro frames not found in the snapshot')
    # slot B TLV 0x03 data[1] bit 6 = "powering off" (snapshot taken then)
    b = f[128:]
    o, end = 4, min(4 + b[2], 126)
    while o + 4 <= end:
        if b[o] == 0x03 and b[o + 1] >= 2:
            b[o + 5] &= ~0x40
            break
        o += 4 + b[o + 1]
    for k, fr in ((0, f[:128]), (128, b)):
        c = crc16(fr)
        fr[126], fr[127] = c & 0xFF, c >> 8
        f[k:k + 128] = fr
    return bytes(f)


def copy_into(dst, src, chunk=8 << 20):
    with open(src, 'rb') as s:
        while True:
            b = s.read(chunk)
            if not b:
                return
            dst.write(b)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    dump = sys.argv[1]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pdir = os.path.join(dump, 'partitions')
    disk = os.path.join(pdir, 'nflasha.raw')
    fw = os.path.join(here, 'fw')
    img = os.path.join(here, 'img')
    os.makedirs(fw, exist_ok=True)
    os.makedirs(img, exist_ok=True)

    man = os.path.join(dump, 'manifest.json')
    if os.path.exists(man) and os.environ.get('A7C_SKIP_SHA') != '1':
        want = {p['name']: p['sha256'] for p in json.load(open(man))['partitions']}
        h = hashlib.sha256()
        with open(disk, 'rb') as f:
            for b in iter(lambda: f.read(8 << 20), b''):
                h.update(b)
        ok = h.hexdigest() == want.get('nflasha')
        print('nflasha.raw sha256 %s (%s)' % (h.hexdigest()[:16], 'matches manifest' if ok else 'MISMATCH'))
        if not ok:
            raise SystemExit('dump does not match manifest.json; set A7C_SKIP_SHA=1 to override')

    parts = sony_partitions(disk)
    print('partitions:', ', '.join('nflasha%d@%#x+%#x' % (n, s, z) for n, (s, z, _) in parts.items()))
    with open(disk, 'rb') as f:
        system = Fat16(f, parts[3][0])
        for src, dst in (('VMLINUX.BIN', 'VMLINUX.BIN'), ('INITRD.IMG', 'INITRD.IMG')):
            data = system.get(src)
            open(os.path.join(fw, dst), 'wb').write(data)
            print('  /system/%-12s %9d bytes -> fw/%s' % (src.lower(), len(data), dst))
        # sanity check what the machine's bootloader stand-in will load
        setting = Fat16(f, parts[2][0])
        backup = setting.get('BACKUP.BIN')
        avcam = system.get('AV-CAM.BIN')
        if avcam[4:8] != b'ORIL':
            raise SystemExit('/system/av-cam.bin: no LIRO magic')
        print('  /setting/backup.bin  %9d bytes (machine loads it at PA %#x)'
              % (len(backup), struct.unpack_from('<I', backup)[0]))
        print('  /system/av-cam.bin   %9d bytes (machine loads it at PA %#x)'
              % (len(avcam), (struct.unpack_from('<I', avcam, 12)[0] & ~0xfff) - 0x60000000))

    cmd = os.path.join(dump, 'metadata', 'proc_cmdline')
    if os.path.exists(cmd):
        open(os.path.join(fw, 'proc_cmdline'), 'w').write(open(cmd).read().strip() + '\n')
        print('  metadata/proc_cmdline -> fw/proc_cmdline')

    sm = submicro_frames(os.path.join(pdir, 'nflasha5.raw'))
    open(os.path.join(fw, 'submicro_frames.bin'), 'wb').write(sm)
    print('  nflasha5 (warm-boot snapshot) -> fw/submicro_frames.bin: power lever %s, '
          'clock %s' % ('ON' if sm[28] & 1 else 'OFF', sm[6:13].hex(' ')))

    out = os.path.join(img, 'a7c_emmc.img')
    with open(out, 'wb') as o:
        for p in ('nflashaB0.raw', 'nflashaB1.raw'):
            bp = os.path.join(pdir, p)
            if os.path.getsize(bp) != 4 << 20:
                raise SystemExit('%s: expected 4 MiB eMMC boot partition' % p)
            copy_into(o, bp)
        copy_into(o, disk)
    print('  eMMC image -> img/a7c_emmc.img (%d MiB)' % (os.path.getsize(out) >> 20))


if __name__ == '__main__':
    main()
