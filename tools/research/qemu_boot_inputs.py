"""Prepare reproducible QEMU inputs from a declared, read-only a6400 capture.

No camera access, filesystem mounts, firmware patches, or source writes.
Unacquired disk ranges require explicit permission to zero-fill an emulator image.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import tarfile

from project_paths import PROJECT_ROOT
from src.a6400.execution.guest_vfat import VfatImage
from src.a6400.execution.nflasha_boot_set import (
    BOOT_PARAMS, INITRD_LOAD, KERNEL_PHYSICAL_LOAD, detect_image_format,
)
from tools.research.inventory import digest

DEFAULT_CONFIG = PROJECT_ROOT / 'qemu-a6400/inputs.local.json'
AVCAM_LOAD = 0x035C6000
EMMC_BOOT_PARTITION_BYTES = 4 * 1024 ** 2
EMMC_USER_AREA_OFFSET = 2 * EMMC_BOOT_PARTITION_BYTES
OUTPUTS = ('emmc.img', 'nflasha3-Image', 'nflasha3-initrd', 'av-cam.bin',
           'Backup.bin', 'kemco.txt', 'serial-cmdline.txt', 'setting.tar')


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _source(root: Path, name: str) -> Path:
    if not name or '/' in name or '\\' in name or name in ('.', '..'):
        raise ValueError(f'Capture filename is not a basename: {name!r}')
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise ValueError(f'Capture file is absent or escapes its declared root: {name}')
    return path


def _read_checked(root: Path, name: str, expected: str, *, limit: int) -> bytes:
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError(f'Missing or invalid expected SHA-256 for {name}')
    path = _source(root, name)
    if path.stat().st_size > limit:
        raise ValueError(f'Capture file exceeds the read bound: {name}')
    data = path.read_bytes()
    if len(data) > limit or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f'Capture SHA-256 mismatch: {name}')
    return data


def parse_checksums(data: bytes) -> dict[str, str]:
    result = {}
    for line in data.decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64}) [ *]([^/\\]+)', line)
        if not match or match[2] in result or match[2] in ('.', '..'):
            raise ValueError('Malformed or duplicate SHA256SUMS.txt entry')
        result[match[2]] = match[1]
    if not result:
        raise ValueError('Empty SHA256SUMS.txt')
    return result


def parse_sdm_partitions(header: bytes) -> list[dict]:
    """Decode the SDM layout also described by fwtool.sony.flash."""
    if len(header) < 32 or header[:8] != b'82461.00':
        raise ValueError('Unsupported SDM partition-table magic/version')
    count = struct.unpack_from('<I', header, 8)[0]
    if not 1 <= count <= 32 or 32 + 16 * count > len(header):
        raise ValueError('Invalid or truncated SDM partition count')
    rows = []
    for index in range(count):
        start, size, kind, flags = struct.unpack_from('<4I', header, 32 + index * 16)
        if start % 512 or size % 512 or start + size > 1 << 32:
            raise ValueError('Unaligned or overflowing SDM partition extent')
        if size and (not flags & 1 or start < 32 + 16 * count):
            raise ValueError('Unsupported inactive partition or header overlap')
        rows.append(dict(device=f'nflasha{index + 1}', start=start, bytes=size,
                         type=kind, flags=flags))
    occupied = sorted((row['start'], row['start'] + row['bytes'])
                      for row in rows if row['bytes'])
    if not occupied or any(left[1] > right[0] for left, right in zip(occupied, occupied[1:])):
        raise ValueError('Empty or overlapping SDM partition extents')
    return rows


def _fat_members(data: bytes, names: tuple[str, ...]) -> dict[str, bytes]:
    image = VfatImage(data, expected_size=len(data))
    wanted = {name.casefold(): name for name in names}
    result = {}
    for entry in image.list_dir():
        name = wanted.get(entry.display_name.casefold())
        if name is None:
            continue
        if entry.is_dir or name in result:
            raise ValueError(f'Ambiguous FAT boot member: {name}')
        content = image.read_file(entry)
        if len(content) != entry.size:
            raise ValueError(f'Truncated FAT boot member: {name}')
        result[name] = content
    if set(result) != set(names):
        raise ValueError(f'Missing FAT boot members: {sorted(set(names) - set(result))}')
    return result


def _memory_size(text: str) -> int:
    match = re.fullmatch(r'(0x[0-9a-fA-F]+|[0-9]+)([KMG]?)', text)
    if not match:
        raise ValueError(f'Unsupported boot memory size: {text!r}')
    return int(match[1], 0) * {'': 1, 'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}[match[2]]


def boot_contract(files: dict[str, bytes]) -> dict:
    if detect_image_format(files['nflasha3-Image']) != 'raw_arm_kernel':
        raise ValueError('Main boot requires an unmodified raw ARM kernel')
    if detect_image_format(files['nflasha3-initrd']) != 'ext2':
        raise ValueError('Main boot requires the captured ext2 initrd')
    avcam, backup = files['av-cam.bin'], files['Backup.bin']
    if len(avcam) < 16 or avcam[4:8] != b'ORIL':
        raise ValueError('Missing LIRO av-cam header')
    if len(backup) < 16 or backup[4:8] != b'\xaa' * 4:
        raise ValueError('Missing Backup.bin cookie')
    backup_load = struct.unpack_from('<I', backup)[0]
    if backup_load % 4:
        raise ValueError('Unaligned Backup.bin preload address')
    tokens = files['kemco.txt'].decode('ascii').split()
    if not tokens or tokens[0] != 'kemco' or tokens[-1] != '*':
        raise ValueError('Expected the complete main kemco block')
    tokens = tokens[1:-1]
    if tokens.count('console=ttynull') != 1:
        raise ValueError('Expected exactly one stock ttynull console token')
    reserved = []
    for token in tokens:
        if token.startswith('memrsv='):
            size, address = token.removeprefix('memrsv=').split('@')
            start = _memory_size(address.split(':')[0])
            reserved.append((start, start + _memory_size(size)))
    preloads = [('Backup.bin', backup_load, len(backup)),
                ('av-cam.bin', AVCAM_LOAD, len(avcam))]
    for name, start, size in preloads:
        if not any(low <= start and start + size <= high for low, high in reserved):
            raise ValueError(f'{name} preload is not covered by the captured memrsv contract')
    if backup_load < AVCAM_LOAD + len(avcam) and AVCAM_LOAD < backup_load + len(backup):
        raise ValueError('Boot preloads overlap')
    serial = []
    for token in tokens:
        serial.extend(('console=ttyAM0,115200n8', 'amba2.console=1')
                      if token == 'console=ttynull' else (token,))
    return dict(kernel_load=KERNEL_PHYSICAL_LOAD, initrd_load=INITRD_LOAD,
                boot_params=BOOT_PARAMS, backup_load=backup_load, avcam_load=AVCAM_LOAD,
                avcam_load_evidence='E0521 liro.ko adrs=035c6000; retained hand-off model',
                cmdline=' '.join(serial),
                cmdline_change='Only replace ttynull with the PL011 serial console')


def prepare(config_path: Path, output: Path, *, allow_unacquired_zero_fill: bool = False) -> dict:
    config_path, output = config_path.resolve(), output.resolve()
    if not output.is_relative_to((PROJECT_ROOT / 'artifacts').resolve()):
        raise ValueError('Boot output must remain under artifacts')
    if output == (PROJECT_ROOT / 'artifacts').resolve():
        raise ValueError('Choose a new boot-input subdirectory')
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if config.get('schema_version') != 1:
        raise ValueError('Unsupported workstation input configuration')
    target_id = config.get('target_id', 'ilce_6400')
    if target_id != 'ilce_6400':
        raise ValueError(
            f'Captured QEMU inputs are pinned to ilce_6400 and are not reused for {target_id}'
        )
    root = Path(config['dump_dir']).resolve()
    if output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('Boot outputs must not overlap the original capture')
    output.mkdir(parents=True, exist_ok=False)
    report = dict(schema_version=2, status='running', target_id=target_id, inputs=[])
    try:
        sums_data = _read_checked(root, 'SHA256SUMS.txt', config['checksums_sha256'], limit=1024 ** 2)
        sums = parse_checksums(sums_data)
        if sums.get('manifest.json') != config['manifest_sha256']:
            raise ValueError('Capture manifest and checksum list disagree')
        manifest_data = _read_checked(root, 'manifest.json', config['manifest_sha256'], limit=16 * 1024 ** 2)
        capture = json.loads(manifest_data.decode('utf-8-sig'))
        header = _read_checked(root, 'nflasha-head-1m.bin', sums['nflasha-head-1m.bin'], limit=1024 ** 2)
        inventory_data = _read_checked(root, 'system-inventory.json', sums['system-inventory.json'],
                                       limit=8 * 1024 ** 2)
        inventory = json.loads(inventory_data.decode('utf-8-sig'))['entries']['proc_partitions']
        if inventory['status'] != 0 or inventory['truncated']:
            raise ValueError('Captured /proc/partitions is incomplete')
        capacities = re.findall(r'^\s*\d+\s+0\s+(\d+)\s+nflasha\s*$', inventory['output'], re.MULTILINE)
        if len(capacities) != 1:
            raise ValueError('Captured eMMC capacity is ambiguous')
        captured_capacity = int(capacities[0]) * 1024
        if not 0 < captured_capacity <= 4 * 1024 ** 3:
            raise ValueError('Captured eMMC capacity exceeds this a6400 preparation bound')
        disk_size = 1 << (captured_capacity + EMMC_USER_AREA_OFFSET - 1).bit_length()
        rows = parse_sdm_partitions(header)
        occupied = sorted((row for row in rows if row['bytes']), key=lambda row: row['start'])
        prefix_size = occupied[0]['start']
        if prefix_size > len(header) or occupied[-1]['start'] + occupied[-1]['bytes'] > captured_capacity:
            raise ValueError('Partition layout exceeds the captured header or eMMC capacity')
        gaps, end = [], EMMC_USER_AREA_OFFSET + prefix_size
        for row in occupied:
            start = EMMC_USER_AREA_OFFSET + row['start']
            if start > end:
                gaps.append(dict(start=end, bytes=start - end))
            end = start + row['bytes']
        if end < disk_size:
            gaps.append(dict(start=end, bytes=disk_size - end))
        if gaps and not allow_unacquired_zero_fill:
            raise ValueError('Unacquired disk ranges require --allow-unacquired-zero-fill; '
                             'zero-filled emulator space is not observed camera content')
        report['inputs'].extend(
            dict(path=str(root / name), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            for name, data in [('SHA256SUMS.txt', sums_data), ('manifest.json', manifest_data),
                               ('nflasha-head-1m.bin', header), ('system-inventory.json', inventory_data)])
        inputs = report['inputs']
        selected = {}
        with (output / 'emmc.img').open('xb') as disk:
            def copy_device(device, offset, size):
                identity = capture['devices'][device]
                if identity['status'] != 'complete' or identity.get('error') is not None:
                    raise ValueError(f'Partition capture is not complete: {device}')
                name = identity['image']
                path = _source(root, name)
                expected = identity['sha256']
                if (sums.get(name) != expected or identity['declared_size'] != size
                        or path.stat().st_size != size):
                    raise ValueError(f'Partition size or declared identities disagree: {device}')
                value, copied = hashlib.sha256(), 0
                disk.seek(offset)
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 ** 2), b''):
                        if copied + len(chunk) > size:
                            raise ValueError(f'Partition grew during copying: {device}')
                        disk.write(chunk)
                        value.update(chunk)
                        copied += len(chunk)
                if copied != size or value.hexdigest() != expected:
                    raise ValueError(f'Partition SHA-256 mismatch: {device}')
                record = dict(path=str(path), bytes=copied, sha256=expected)
                inputs.append(record)
                selected[device] = record
            disk.truncate(disk_size)
            for index in range(2):
                copy_device(f'nflashaB{index}', index * EMMC_BOOT_PARTITION_BYTES,
                            EMMC_BOOT_PARTITION_BYTES)
            disk.seek(EMMC_USER_AREA_OFFSET)
            disk.write(header[:prefix_size])
            for row in occupied:
                copy_device(row['device'], EMMC_USER_AREA_OFFSET + row['start'], row['bytes'])
        main_data = _read_checked(root, Path(selected['nflasha3']['path']).name,
                                  selected['nflasha3']['sha256'], limit=64 * 1024 ** 2)
        setting_data = _read_checked(root, Path(selected['nflasha2']['path']).name,
                                     selected['nflasha2']['sha256'], limit=64 * 1024 ** 2)
        main = _fat_members(main_data, ('vmlinux.bin', 'initrd.img', 'av-cam.bin'))
        files = {'nflasha3-Image': main['vmlinux.bin'], 'nflasha3-initrd': main['initrd.img'],
                 'av-cam.bin': main['av-cam.bin']}
        files.update(_fat_members(setting_data, ('Backup.bin', 'kemco.txt')))
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
        source_notes = []
        final = root / 'manifest-final.json'
        if final.is_file() and 'manifest-final.json' in sums and digest(final) != sums['manifest-final.json']:
            source_notes.append('Unused manifest-final.json differs from SHA256SUMS.txt. '
                                'Neither was rewritten; this build uses the independently checked manifest.json.')
        outputs = {name: dict(bytes=(output / name).stat().st_size, sha256=digest(output / name))
                   for name in OUTPUTS}
        report.update(status='completed', source_kind='captured raw partitions; no package substitution',
                      inputs=inputs, source_notes=source_notes,
                      partitions=[dict(row, backing_offset=EMMC_USER_AREA_OFFSET + row['start'])
                                  for row in rows],
                      boot_contract=contract, captured_user_area_bytes=captured_capacity,
                      hardware_boot_partition_bytes=EMMC_BOOT_PARTITION_BYTES,
                      user_area_offset=EMMC_USER_AREA_OFFSET,
                      emulated_disk_bytes=disk_size,
                      unacquired_ranges=gaps,
                      unacquired_policy='explicit zero fill in QEMU backing-file coordinates; emulation only',
                      outputs=outputs,
                      tool_sources={name: digest(PROJECT_ROOT / name) for name in (
                          'tools/research/qemu_boot_inputs.py', 'src/a6400/execution/guest_vfat.py',
                          'src/a6400/execution/nflasha_boot_set.py')},
                      limitations=[
                          'Partition captures were not atomic across mounted writable filesystems.',
                          'Unacquired ranges and capacity rounding are emulator construction, not a whole-disk dump.',
                          'Boot-ROM execution, device timing, peripheral completeness and physical safety are not established.',
                          'Only used capture inputs are checked; unrelated archives and auxiliary manifests are not certified.',
                      ])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        _write_json(output / 'boot-manifest.json', report)
    return report


def verify(output: Path, *, check_sources: bool = False) -> dict:
    output = output.resolve()
    report = json.loads((output / 'boot-manifest.json').read_text(encoding='utf-8'))
    if report.get('schema_version') != 2:
        raise ValueError('Unsupported boot-input manifest schema; reprepare the current eMMC backing layout')
    if report.get('status') != 'completed':
        raise ValueError('Boot-input manifest is not completed')
    if set(report['outputs']) != set(OUTPUTS):
        raise ValueError('Boot-input output set differs from the supported contract')
    for name, identity in report['outputs'].items():
        path = _source(output, name)
        if path.stat().st_size != identity['bytes'] or digest(path) != identity['sha256']:
            raise ValueError(f'Prepared boot-input identity mismatch: {name}')
    files = {name: (output / name).read_bytes() for name in
             ('nflasha3-Image', 'nflasha3-initrd', 'av-cam.bin', 'Backup.bin', 'kemco.txt')}
    if boot_contract(files) != report['boot_contract']:
        raise ValueError('Prepared boot contract differs from its manifest')
    if (output / 'serial-cmdline.txt').read_bytes() != (report['boot_contract']['cmdline'] + '\n').encode('ascii'):
        raise ValueError('Prepared serial command line differs from its manifest')
    if check_sources:
        for identity in report['inputs']:
            path = Path(identity['path'])
            if path.stat().st_size != identity['bytes'] or digest(path) != identity['sha256']:
                raise ValueError(f'Original capture changed: {path.name}')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    build = actions.add_parser('prepare', help='Create new hash-checked inputs under artifacts')
    build.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    build.add_argument('--report-dir', type=Path, required=True)
    build.add_argument('--allow-unacquired-zero-fill', action='store_true')
    check = actions.add_parser('verify', help='Verify every prepared input before booting')
    check.add_argument('--report-dir', type=Path, required=True)
    check.add_argument('--sources', action='store_true', help='Also recheck original capture inputs')
    args = parser.parse_args()
    try:
        report = (prepare(args.config, args.report_dir,
                          allow_unacquired_zero_fill=args.allow_unacquired_zero_fill)
                  if args.action == 'prepare'
                  else verify(args.report_dir, check_sources=args.sources))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(2, f'{type(error).__name__}: {error}\n')
    print(json.dumps(dict(status=report['status'], report_dir=str(args.report_dir.resolve()),
                          emulated_disk_bytes=report['emulated_disk_bytes'],
                          emmc_sha256=report['outputs']['emmc.img']['sha256'],
                          source_notes=report['source_notes']), indent=2))


if __name__ == '__main__':
    main()
