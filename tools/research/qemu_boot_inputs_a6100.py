"""Prepare QEMU boot inputs from a read-only ILCE-6100 (plain, V1.00) fastboot partition dump.

The dump layout is the a6100 fastboot fetch set: nflashaB0/B1 (4 MiB eMMC boot
partitions), nflasha_head plus nflashaN.img at the user-area offsets listed in
partitions.txt, and SHA256SUMS. Output matches qemu_boot_inputs.OUTPUTS and its
boot-manifest.json passes `qemu_boot_inputs verify`, so run.sh accepts it through
A6400_QEMU_DATA_DIR. The dump is only read; nothing in it is modified.
"""
from __future__ import annotations

import argparse
import io
import re
import struct
import tarfile
from pathlib import Path

from project_paths import PROJECT_ROOT
from tools.research.inventory import digest
from tools.research.qemu_boot_inputs import (
    AVCAM_LOAD, EMMC_BOOT_PARTITION_BYTES, EMMC_USER_AREA_OFFSET, OUTPUTS,
    _fat_members, _write_json, boot_contract, parse_checksums,
)

# av-cam.bin is linked at VA 0x635C6000 (header word 3 = entry_pre VA); the
# kernel maps it at VA - 0x60000000, which is the AVCAM_LOAD qemu_boot_inputs uses.
AVCAM_LINK_VA = 0x635C6000
AVCAM_VA_TO_PA = 0x60000000


def parse_partitions(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = re.fullmatch(r'(nflasha\w*) start=([0-9a-f]{8}) size=([0-9a-f]{8}) dev=([0-9a-f]{8})', line.strip())
        if m and m[1] != 'nflasha':  # 'nflasha' is the whole user area, not a member
            rows.append(dict(device=m[1], start=int(m[2], 16), bytes=int(m[3], 16), dev=int(m[4], 16)))
    return rows


def prepare(dump: Path, output: Path) -> dict:
    dump, output = dump.resolve(strict=True), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema_version=2, status='running', target_id='ilce_6100',
                  firmware='ILCE-6100 V1.00 (body dump, not the 6100A package)', inputs=[])
    try:
        sums = parse_checksums((dump / 'SHA256SUMS').read_bytes())
        sums = {name.lstrip('*'): sha for name, sha in sums.items()}

        def checked(name: str) -> Path:
            path = dump / name
            if digest(path) != sums[name]:
                raise ValueError(f'SHA-256 mismatch: {name}')
            report['inputs'].append(dict(path=str(path), bytes=path.stat().st_size, sha256=sums[name]))
            return path

        rows = parse_partitions((dump / 'partitions.txt').read_text())
        boot = [r for r in rows if r['dev'] != 0]
        user = sorted([r for r in rows if r['dev'] == 0], key=lambda r: r['start'])
        head = checked('nflasha_head.img')
        user.insert(0, dict(device='nflasha_head', start=0, bytes=head.stat().st_size, dev=0))
        end = 0
        for row in user:  # the dump must cover the user area with no gaps or overlaps
            if row['start'] != end:
                raise ValueError(f"{row['device']} starts at {row['start']:#x}, expected {end:#x}")
            end = row['start'] + row['bytes']
        captured_end = EMMC_USER_AREA_OFFSET + end
        disk_size = 1 << (captured_end - 1).bit_length()  # QEMU SD needs a power-of-two size
        with (output / 'emmc.img').open('xb') as disk:
            disk.truncate(disk_size)
            for row in boot:
                index = row['dev'] - 1
                if row['device'] != f'nflashaB{index}' or row['bytes'] != EMMC_BOOT_PARTITION_BYTES:
                    raise ValueError(f"Unexpected boot partition row: {row}")
                row['backing_offset'] = index * EMMC_BOOT_PARTITION_BYTES
            for row in user:
                row['backing_offset'] = EMMC_USER_AREA_OFFSET + row['start']
            for row in boot + user:
                path = checked(f"{row['device']}.img")
                if path.stat().st_size != row['bytes']:
                    raise ValueError(f"{path.name} is {path.stat().st_size} bytes, partition is {row['bytes']}")
                disk.seek(row['backing_offset'])
                with path.open('rb') as src:
                    while chunk := src.read(16 * 1024 ** 2):
                        disk.write(chunk)

        main_data = (dump / 'nflasha3.img').read_bytes()
        setting_data = (dump / 'nflasha2.img').read_bytes()
        main = _fat_members(main_data, ('vmlinux.bin', 'initrd.img', 'av-cam.bin'))
        files = {'nflasha3-Image': main['vmlinux.bin'], 'nflasha3-initrd': main['initrd.img'],
                 'av-cam.bin': main['av-cam.bin']}
        files.update(_fat_members(setting_data, ('Backup.bin', 'kemco.txt')))
        entry_va = struct.unpack_from('<I', files['av-cam.bin'], 12)[0]
        if entry_va & ~0xFFF != AVCAM_LINK_VA or AVCAM_LINK_VA - AVCAM_VA_TO_PA != AVCAM_LOAD:
            raise ValueError(f'av-cam.bin entry VA {entry_va:#x} does not match load PA {AVCAM_LOAD:#x}')
        contract = boot_contract(files)
        files['serial-cmdline.txt'] = (contract['cmdline'] + '\n').encode('ascii')
        for name, data in files.items():
            with (output / name).open('xb') as stream:
                stream.write(data)
        with tarfile.open(output / 'setting.tar', mode='x', format=tarfile.USTAR_FORMAT) as archive:
            for name in ('Backup.bin', 'kemco.txt'):
                entry = tarfile.TarInfo('./' + name)
                entry.size, entry.mode = len(files[name]), 0o644
                archive.addfile(entry, io.BytesIO(files[name]))
        report.update(
            status='completed', source_notes=[], source_kind='captured raw partitions (a6100 fastboot fetch); no package substitution',
            partitions=boot + user, boot_contract=contract,
            avcam_load_source=f'a6100 av-cam.bin header entry VA {entry_va:#x}, linked at {AVCAM_LINK_VA:#x}',
            hardware_boot_partition_bytes=EMMC_BOOT_PARTITION_BYTES, user_area_offset=EMMC_USER_AREA_OFFSET,
            emulated_disk_bytes=disk_size,
            unacquired_ranges=[dict(start=captured_end, bytes=disk_size - captured_end)],
            unacquired_policy='zero fill past the dumped nflasha user area; emulation only',
            outputs={name: dict(bytes=(output / name).stat().st_size, sha256=digest(output / name))
                     for name in OUTPUTS},
            tool_sources={name: digest(PROJECT_ROOT / name) for name in (
                'tools/research/qemu_boot_inputs_a6100.py', 'tools/research/qemu_boot_inputs.py')},
            limitations=['Partitions were fetched one by one, not atomically.',
                         'Board model and sub-micro frames are a6400-derived; a6100 peripherals are not established.'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        _write_json(output / 'boot-manifest.json', report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dump', type=Path, required=True)
    parser.add_argument('--report-dir', type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.dump, args.report_dir)
    print(report['status'], report['outputs']['emmc.img']['sha256'])


if __name__ == '__main__':
    main()
