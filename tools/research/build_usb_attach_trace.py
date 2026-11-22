"""Build identity-pinned sites for usb_startup_trace.c's passive QEMU observer."""
import argparse
import hashlib
import io
import json
from pathlib import Path

from elftools.elf.elffile import ELFFile

from project_paths import PROJECT_ROOT
from src.a6400.execution.guest_fusb_startup import KERNEL_BASE, KERNEL_SHA256


LIBRARIES = {
    'libObj.so': ('e88b7e0fc61b8c98ac06c1979e6ebdd3fa8a2a49384b3fd5ffc8d06da4174ef1', {
        0x4AA01A: 'camera_backup_read',
        0x4AA01E: 'camera_backup_return',
        0x4AA094: 'camera_readiness_byte',
        0x4AA098: 'camera_readiness_backup_value',
        0x4AA0A0: 'camera_set_not_ready',
        0x4AA102: 'camera_set_ready',
        0x4AA194: 'camera_neutral_clear',
        0x4AB912: 'camera_neutral_set',
        0x4E5248: 'call_camera_neutral_enable',
        0x4E524E: 'call_camera_readiness',
        0x4E65D0: 'tail_call_camera_neutral_clear',
        0x4E671C: 'call_camera_neutral_clear',
        0x8622C6: 'usb_input_dispatch_decision',
        0x8624B0: 'usb_input_callback_send',
    }),
    'viewUnified2.so': ('fa534aa2160fc3e9c93119a85bdd9f1988b5f9f64b62c43fad178fabe4fa823d', {
        0x2ADFB8: 'action_open_usb',
        0x2A524C: 'connect_usb_at_pre_stable',
        0x2AC00A: 'check_usb_and_clock_ready',
        0x2AC03A: 'check_usb_and_clock_ready_result',
        0x2B271A: 'check_usb_and_clock_ready_first_view',
        0x2B2704: 'first_view_root_readiness_value',
        0x2B2712: 'first_view_readiness_result',
        0x2B278E: 'read_usb_event_status_first_view',
        0x41AD18: 'release_usb_key_inhibit',
    }),
    'libUsbControl.so': ('b5cea9d2c92a0b0810353900871fa2806ca3064aa5d8cf3ca7db5ddb6f3940e2', {
        0x12980: 'set_jack_status',
        0x12C78: 'start_gadget',
    }),
}


def read_pinned(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f'Captured input identity mismatch: {path}')
    return raw


def make_sites(kernel_path, library_dir):
    raw = read_pinned(kernel_path, KERNEL_SHA256)
    address = 0x602AD2AC
    offset = address - KERNEL_BASE
    yield {'module': 'kernel', 'address': address, 'name': 'original_fusb_vbus',
           'bytes': raw[offset:offset + 16].hex(), 'sha256': KERNEL_SHA256}
    for name, (digest, offsets) in LIBRARIES.items():
        elf = ELFFile(io.BytesIO(read_pinned(library_dir / name, digest)))
        text = elf.get_section_by_name('.text')
        data = text.data()
        for address, label in offsets.items():
            offset = address - text['sh_addr']
            if not 0 <= offset <= len(data) - 16:
                raise ValueError(f'Invalid .text address in {name}: {address:#x}')
            yield {'module': name, 'address': address, 'name': label,
                   'bytes': data[offset:offset + 16].hex(), 'sha256': digest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--libraries', type=Path, required=True)
    parser.add_argument('--report-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.report_dir.resolve()
    if not output.is_relative_to(PROJECT_ROOT / 'artifacts'):
        parser.error('Report directory must remain under artifacts')
    sites = list(make_sites(args.kernel, args.libraries))
    output.mkdir(parents=True, exist_ok=False)
    header = ['#define USB_STOCK_TRACE 1',
              'static const struct { bool kernel; uint32_t offset; uint8_t bytes[16]; } sites[] = {']
    for site in sites:
        data = ', '.join(f'0x{x:02x}' for x in bytes.fromhex(site['bytes']))
        kernel = 'true' if site['module'] == 'kernel' else 'false'
        header.append(f'    {{{kernel}, {site["address"]:#x}, {{{data}}}}},')
    header.append('};')
    (output / 'usb_trace_sites.h').write_text('\n'.join(header) + '\n', encoding='ascii')
    (output / 'sites.json').write_text(json.dumps(sites, indent=2) + '\n', encoding='ascii')


if __name__ == '__main__':
    main()
