"""Start a target-selected original ARM interpreter with a declared syscall policy."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from unicorn import UcError

from project_paths import PROJECT_ROOT
from src.a6400.images.process_elf import inspect_image
from src.a6400.execution.interpreter_probe import InterpreterProbe
from tools.research.guest_layout import resolve_guest
from tools.research.inventory import digest
from src.a6400.execution.guest_uname import extract_kernel_uts
from src.a6400.images.verified_content import VerifiedContent
from src.a6400.images.ext2_metadata import Ext2Metadata
from tools.research.mount_evidence import checked_input, inspect_init, unsupplied_mount_roots
from src.a6400.execution.guest_filesystem import UNKNOWN_ROOTS
from src.a6400.execution.guest_kuser import extract_kuser_template
from src.a6400.targets import get_profile
from tools.research.decode_kernel_kallsyms import decode as decode_kallsyms
from tools.research.process_target_inputs import (
    ILCE_7C_APPLICATION_PATH_EVIDENCE,
    ILCE_7C_MANIFEST,
    load_target_manifest,
    select_a7c_images,
    verify_a7c_interpreter_package_mapping,
)


A6400_FILESYSTEM_MANIFEST = Path("artifacts/inventory/guest-filesystem.json")
A6400_ELF_INVENTORY = Path("artifacts/inventory/elf-inventory.json")
A6400_BASELINE = Path("artifacts/inventory/baseline.json")
A6400_KERNEL = "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha3_unpacked/vmlinux.bin"
A6400_INIT = "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha7_unpacked/sbin/init"
A6400_BOOT_MOUNT_TRACE = Path("artifacts/inventory/boot-mount-trace.json")
A7C_KERNEL_SYMBOLS = (
    "usb_gadgetcore_ioctl", "usb_gadgetcore_register_driver", "usb_gcw_start",
    "usb_gcw_stop", "setup_req", "make_config_desc",
)
_PROBE_RECEIPT_ERRORS = (ValueError, KeyError, StopIteration, OSError, UcError)


def kernel_symbol_crosscheck(profile_symbols, image_symbols, names):
    """Compare named kernel addresses. A missing address is not a match."""
    rows = {}
    profile_symbols = profile_symbols if isinstance(profile_symbols, dict) else {}
    image_symbols = image_symbols if isinstance(image_symbols, dict) else {}
    for name in names:
        profile_addr = profile_symbols.get(name)
        image_addr = image_symbols.get(name)
        rows[name] = {
            'profile': profile_addr,
            'image': image_addr,
            'match': isinstance(profile_addr, int) and profile_addr == image_addr,
        }
    return rows


def _finish_probe_report(report, populate):
    """Attach runner metadata. Keep the probe receipt if that bookkeeping fails."""
    try:
        populate()
    except _PROBE_RECEIPT_ERRORS as error:
        report['probe_status'] = report.get('status')
        report['status'] = 'configuration_error'
        report['camera_fidelity_validated'] = False
        report['error'] = str(error)
    return report


def _load_guest_image(manifest, inventory, guest_path):
    resolution = resolve_guest(manifest['nodes'], guest_path)
    if resolution['status'] != 'resolved':
        raise ValueError(f'Guest path not resolved: {resolution}')
    node = manifest['nodes'][resolution['path']]
    if node['kind'] != 'file':
        raise ValueError('Guest image must be a regular file')
    local = (PROJECT_ROOT / node['extracted_path']).resolve()
    firmware_root = (PROJECT_ROOT / 'firmware').resolve()
    if not local.is_relative_to(firmware_root):
        raise ValueError('Guest image path escapes local firmware directory')
    matches = [entry for entry in inventory if entry['path'] == node['extracted_path']]
    if len(matches) != 1:
        raise ValueError(f'Guest image inventory entry is absent or ambiguous: {node["extracted_path"]}')
    return inspect_image(local, matches[0]['sha256'])


def load_guest_image(manifest, inventory, guest_path):
    """Backward-compatible A6400 helper used by existing offline session tools."""
    return _load_guest_image(manifest, inventory, guest_path)


def _run_a6400(args, target_profile):
    """Keep the established A6400 input/layout behavior behind its target selector."""
    if getattr(target_profile, 'target_id', None) != 'ilce_6400':
        raise ValueError(
            'The ILCE-6400 inventory bundle is not reused for '
            f'{getattr(target_profile, "target_id", None)}'
        )
    if args.interpreter_candidate or args.input_manifest:
        raise ValueError('--interpreter-candidate/--input-manifest are only valid for target ilce_7c')
    manifest = json.loads((PROJECT_ROOT / A6400_FILESYSTEM_MANIFEST).read_text(encoding='utf-8'))
    inventory = json.loads((PROJECT_ROOT / A6400_ELF_INVENTORY).read_text(encoding='utf-8'))['entries']
    uses_baseline = args.syscalls in ('brk-uname', 'files', 'metadata', 'mmap', 'kuser', 'uipc', 'threads')
    expected_inputs = {}
    if uses_baseline:
        baseline = json.loads((PROJECT_ROOT / A6400_BASELINE).read_text(encoding='utf-8'))
        expected_inputs = {item['path']: item['sha256'] for item in baseline['inputs']}
    for image in manifest['images']:
        path = (PROJECT_ROOT / image['path']).resolve()
        if not path.is_relative_to((PROJECT_ROOT / 'firmware').resolve()) or digest(path) != image['sha256']:
            raise ValueError('Original A6400 filesystem image path/hash mismatch')

    entry = args.entry or '/usr/bin/im.elf'
    application = _load_guest_image(manifest, inventory, entry)
    if not application['interpreter']:
        raise ValueError('Application has no PT_INTERP')
    interpreter = _load_guest_image(manifest, inventory, application['interpreter'])
    uname_profile = None
    kuser_template = None
    if args.syscalls in ('brk-uname', 'files', 'metadata', 'mmap', 'kuser', 'uipc', 'threads'):
        expected = expected_inputs[A6400_KERNEL]
        uname_profile = extract_kernel_uts(PROJECT_ROOT / A6400_KERNEL, expected)
        uname_profile['source_path'] = A6400_KERNEL
        if args.syscalls in ('kuser', 'uipc', 'threads'):
            kuser_template = extract_kuser_template(PROJECT_ROOT / A6400_KERNEL, expected)
    filesystem = None
    mount_profile = None
    if args.syscalls in ('files', 'metadata', 'mmap', 'kuser', 'uipc', 'threads'):
        content_loader = VerifiedContent(PROJECT_ROOT, PROJECT_ROOT / 'firmware', expected_inputs)
        filesystem = {'nodes': manifest['nodes'], 'content_loader': content_loader}
        if args.respect_runtime_mounts:
            init = checked_input(PROJECT_ROOT, A6400_INIT, expected_inputs[A6400_INIT])
            roots = unsupplied_mount_roots(inspect_init(init)['mounts'])
            # Preserve the default unknown roots as well as discovered ones.
            filesystem['unknown_roots'] = tuple(sorted(set(UNKNOWN_ROOTS).union(roots)))
            mount_profile = {'source_path': A6400_INIT, 'source_sha256': expected_inputs[A6400_INIT],
                             'unsupplied_mount_candidates': roots,
                             'method': 'conservative static mount targets; success/boot mode not established'}
        if args.syscalls in ('metadata', 'mmap', 'kuser', 'uipc', 'threads'):
            boot_mount_data = None
            if args.syscalls in ('mmap', 'kuser', 'uipc', 'threads'):
                boot_mount_data = json.loads((PROJECT_ROOT / A6400_BOOT_MOUNT_TRACE).read_text(encoding='utf-8'))
                major = boot_mount_data['devnodes_summary']['nflasha']['major']
            for image in manifest['images']:
                source = Ext2Metadata(PROJECT_ROOT / image['path'], image['sha256'])
                indexed = source.index()
                for node in filesystem['nodes'].values():
                    if node.get('image') != Path(image['path']).name:
                        continue
                    metadata = indexed[node['archive_path'] or '/']
                    if any(metadata[key] != node[key] for key in ('mode', 'uid', 'gid')):
                        raise ValueError('ext2 namespace metadata mismatch')
                    if node['kind'] == 'file' and metadata['size'] != node['size']:
                        raise ValueError('ext2 namespace file size mismatch')
                    node_stat = {**metadata, 'image_sha256': image['sha256']}
                    if args.syscalls in ('mmap', 'kuser', 'uipc', 'threads'):
                        # Evidence Record 0014: major 252, nflasha7=0xfc07, nflasha15=0xfc0f
                        part_num = 7 if 'nflasha7' in image['path'] else 15
                        node_stat['st_dev'] = (major << 8) | part_num
                    node['stat_metadata'] = node_stat
    probe = InterpreterProbe(syscall_policy=args.syscalls, uname_profile=uname_profile,
                             filesystem=filesystem, kuser_template=kuser_template,
                             target_profile=target_profile)
    probe.prepare(application, interpreter, [entry, *args.argument])
    report = probe.run(args.instructions, args.timeout_us)

    def populate():
        if args.respect_runtime_mounts:
            report['runtime_mount_profile'] = mount_profile
        report['guest_paths'] = {'application': entry, 'interpreter': application['interpreter']}
        report['filesystem_sources'] = manifest['images']
        report['target_input_selection'] = {
            'target_id': 'ilce_6400', 'selection_mode': 'legacy_a6400_inventory_bundle',
            'target_profile_status': target_profile.status,
            'target_profile_firmware_version': target_profile.firmware_version,
            'filesystem_manifest': A6400_FILESYSTEM_MANIFEST.as_posix(),
            'elf_inventory': A6400_ELF_INVENTORY.as_posix(),
            'baseline': A6400_BASELINE.as_posix() if uses_baseline else None,
            'baseline_used': uses_baseline,
            'kernel_used': args.syscalls in ('brk-uname', 'files', 'metadata', 'mmap', 'kuser', 'uipc', 'threads'),
        }

    return _finish_probe_report(report, populate)


def _run_a7c(args, target_profile):
    if getattr(target_profile, 'target_id', None) != 'ilce_7c':
        raise ValueError(
            'ILCE-7C process inputs are pinned to target ilce_7c and are not reused for '
            f'{getattr(target_profile, "target_id", None)}'
        )
    manifest_path = args.input_manifest or ILCE_7C_MANIFEST
    manifest = load_target_manifest(PROJECT_ROOT, Path(manifest_path), 'ilce_7c')
    if target_profile.status != 'reference' or target_profile.firmware_version != '2.03':
        raise ValueError('ILCE-7C 2.03 profile must remain reference-only')
    if manifest.get('deployment_status') != 'reference_only_not_verified_execution_or_installation_target':
        raise ValueError('A7C input manifest must retain reference-only execution/deployment status')
    supported = manifest.get('syscall_contracts', {}).get('supported', [])
    if args.syscalls not in supported:
        raise ValueError(
            f'A7C syscall policy {args.syscalls!r} has no target-specific verified contract; '
            f'available policy: {", ".join(supported) or "none"}'
        )
    if args.respect_runtime_mounts:
        raise ValueError('A7C has only static mount candidates, not a verified runtime filesystem contract')

    entry = args.entry or manifest['application']['guest_path']
    app_spec, interpreter_spec, kernel_spec, init_spec, rootfs_spec = select_a7c_images(
        PROJECT_ROOT, manifest, entry, args.interpreter_candidate
    )
    application = inspect_image(app_spec['local_path'], app_spec['sha256'])
    interpreter = inspect_image(interpreter_spec['local_path'], interpreter_spec['sha256'])
    if application['type'] != 'ET_EXEC' or interpreter['type'] != 'ET_DYN':
        raise ValueError('A7C process images have unexpected ELF types')
    if application['interpreter'] != interpreter_spec['declared_pt_interp']:
        raise ValueError('Selected A7C interpreter candidate does not match the application PT_INTERP string')
    interpreter_path_mapping = verify_a7c_interpreter_package_mapping(
        PROJECT_ROOT, rootfs_spec, interpreter_spec, interpreter, application['interpreter']
    )

    kernel_raw = kernel_spec['local_path'].read_bytes()
    uts = extract_kernel_uts(kernel_spec['local_path'], kernel_spec['sha256'])
    kuser = extract_kuser_template(kernel_raw, kernel_spec['sha256'])
    decoded = decode_kallsyms(kernel_raw)
    symbols = {item['name']: item['address'] for item in decoded['symbols']}
    symbol_crosscheck = kernel_symbol_crosscheck(
        target_profile.kallsyms, symbols, A7C_KERNEL_SYMBOLS
    )
    if not all(row['match'] for row in symbol_crosscheck.values()):
        raise ValueError(f'A7C kernel identity cross-check against reference profile failed: {symbol_crosscheck}')

    init_analysis = inspect_init(init_spec['local_path'].read_bytes())
    mount_candidates = unsupplied_mount_roots(init_analysis['mounts'])
    application_mount_target = ILCE_7C_APPLICATION_PATH_EVIDENCE['mount_target']
    application_mount_rows = [row for row in init_analysis['mounts']
                              if row['target'] == application_mount_target
                              and 'nflasha15' in row['source']]
    rootfs_mount_rows = [row for row in init_analysis['mounts'] if row['target'] == '/rootfs']
    sequence = init_analysis['main_mount_sequence']
    rootfs_string_index = next((i for i, row in enumerate(sequence)
                                if row.get('literal_string') == '/rootfs'), None)
    chroot_index = next((i for i, row in enumerate(sequence)
                         if rootfs_string_index is not None and i >= rootfs_string_index
                         and row.get('call_symbol') == 'chroot'), None)
    root_transition = (sequence[max(0, rootfs_string_index - 7):chroot_index + 1]
                       if rootfs_string_index is not None and chroot_index is not None else [])
    probe = InterpreterProbe(syscall_policy='stop', target_profile=target_profile)
    probe.prepare(application, interpreter, [entry, *args.argument])
    report = probe.run(args.instructions, args.timeout_us)

    def populate():
        _populate_a7c_report(
            report, entry, application, interpreter_spec, manifest_path, manifest, target_profile,
            app_spec, kernel_spec, uts, kuser, symbol_crosscheck, interpreter_path_mapping,
            init_spec, init_analysis, application_mount_rows, rootfs_spec, rootfs_mount_rows,
            sequence, root_transition, mount_candidates,
        )

    return _finish_probe_report(report, populate)


def _populate_a7c_report(report, entry, application, interpreter_spec, manifest_path, manifest,
                         target_profile, app_spec, kernel_spec, uts, kuser, symbol_crosscheck,
                         interpreter_path_mapping, init_spec, init_analysis, application_mount_rows,
                         rootfs_spec, rootfs_mount_rows, sequence, root_transition, mount_candidates):
    report['guest_paths'] = {'application': entry, 'pt_interp': application['interpreter'],
                             'interpreter_candidate_id': interpreter_spec['id']}
    report['target_input_selection'] = {
        'target_id': 'ilce_7c',
        'target_profile_status': target_profile.status,
        'selection_mode': 'target_manifest_sha256_pinned',
        'manifest_path': Path(manifest_path).as_posix(),
        'firmware_identity': manifest['firmware_identity'],
        'application': {'firmware_path': app_spec['firmware_path'], 'sha256': app_spec['sha256'],
                        'elf_type': application['type'], 'entry': hex(application['entry'])},
        'interpreter': {'candidate_id': interpreter_spec['id'],
                        'firmware_path': interpreter_spec['firmware_path'],
                        'sha256': interpreter_spec['sha256'],
                        'pt_interp': application['interpreter'],
                        'resolution_status': interpreter_spec['resolution_status'],
                        'evidence': interpreter_spec.get('evidence')},
        'kernel': {'firmware_path': kernel_spec['firmware_path'], 'sha256': kernel_spec['sha256'],
                   'uts_source_sha256': uts['source_sha256'], 'uts_file_offset': uts['file_offset'],
                   'uts_fields': uts['fields'],
                   'kuser_template_sha256': hashlib.sha256(kuser).hexdigest(),
                   'profile_usb_symbol_crosscheck': symbol_crosscheck},
        'pt_interp_package_mapping': interpreter_path_mapping,
        'runtime_mount_evidence': {
            'status': 'static_candidates_only', 'source_path': init_spec['firmware_path'],
            'source_sha256': init_spec['sha256'], 'mount_count': len(init_analysis['mounts']),
            'application_mount_candidates': application_mount_rows,
            'application_mount_success_verified': False,
            'rootfs_partition_sha256': rootfs_spec['sha256'],
            'rootfs_mount_rows': rootfs_mount_rows,
            'static_mounts': init_analysis['mounts'],
            'init_main_mount_sequence': sequence,
            'root_transition_instructions': root_transition,
            'root_transition_window': (
                'excerpt from seven instructions before the /rootfs literal through the following '
                'chroot; the full decoded main sequence is init_main_mount_sequence'
            ),
            'unsupplied_mount_candidates': mount_candidates,
            'runtime_mount_success_or_boot_mode_verified': False,
        },
        'guest_filesystem_manifest': manifest['guest_filesystem_manifest'],
        'firmware_inventory': manifest['firmware_inventory'],
        'deployment_status': manifest['deployment_status'],
    }
    report.setdefault('assumptions', []).append(
        'The packaged ext2 symlink and init root-transition path statically map PT_INTERP to this '
        'linker candidate. Runtime mount/pivot success and the target kernel handling of the exact '
        'double-leading-slash spelling were not executed or observed.'
    )
    report['execution_scope'] = {
        'original_executable_image': 'ld-2.11.2.so candidate',
        'original_instructions_executed': report.get('instructions_executed'),
        'im_elf_mapped': True,
        'im_elf_mapping_scope': (
            'experiment image mapped by InterpreterProbe.prepare; runtime mount success is not verified'
        ),
        'im_elf_entry_reached': False,
        'syscall_policy': 'stop',
        'syscall_responses_supplied': report.get('syscall_responses_supplied'),
    }


def _run_selected_target(args, target_profile):
    """Run only the process inputs registered for the selected body."""
    if getattr(target_profile, 'target_id', None) != args.target:
        raise ValueError('Selected target profile does not match the requested target')
    if args.target == 'ilce_6400':
        return _run_a6400(args, target_profile)
    if args.target == 'ilce_7c':
        return _run_a7c(args, target_profile)
    raise ValueError(
        f'No target-scoped process inputs are registered for {args.target}. '
        'The ILCE-6400 inventory and the ILCE-7C manifest are not reused for another body.'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entry', default=None, help='guest application path (target default if omitted)')
    parser.add_argument('--argument', action='append', default=[])
    parser.add_argument('--instructions', type=int, default=100000)
    parser.add_argument('--timeout-us', type=int, default=2000000)
    parser.add_argument('--syscalls', choices=('stop', 'brk', 'brk-uname', 'files', 'metadata', 'mmap', 'kuser', 'uipc', 'threads'), default='stop',
                        help='explicit guest contracts; target must declare support; uname reads of runtime-unknown fields remain guarded')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--respect-runtime-mounts', action='store_true',
                        help='stop at original init mount targets whose runtime contents are unsupplied')
    parser.add_argument('--target', default='ilce_6400', help='registered TargetProfile target_id')
    parser.add_argument('--input-manifest', type=Path,
                        help='target process-input manifest override; currently supported for ilce_7c only')
    parser.add_argument('--interpreter-candidate',
                        help='explicit target-manifest candidate ID when PT_INTERP resolution is unverified')
    args = parser.parse_args()
    target_profile = get_profile(args.target)
    if args.report is None:
        name = {'stop': 'interpreter-probe.json', 'brk': 'interpreter-brk.json',
                'brk-uname': 'interpreter-uname.json', 'files': 'interpreter-files.json',
                'metadata': 'interpreter-metadata.json', 'mmap': 'interpreter-mmap.json',
                'kuser': 'interpreter-kuser.json', 'uipc': 'interpreter-uipc.json',
                'threads': 'interpreter-threads.json'}[args.syscalls]
        args.report = PROJECT_ROOT / 'artifacts/execution' / name
        if args.target != 'ilce_6400':
            args.report = args.report.with_stem(f'{args.report.stem}-{args.target}')
        if args.respect_runtime_mounts:
            args.report = args.report.with_stem(args.report.stem + '-mount-aware')
    if args.syscalls in ('uipc', 'threads') and args.instructions == 100000:
        args.instructions, args.timeout_us = 10000000, 30000000
    elif args.syscalls == 'kuser' and args.instructions == 100000:
        args.instructions, args.timeout_us = 3000000, 15000000
    report = {'status': 'configuration_error', 'camera_fidelity_validated': False,
              'target_id': args.target}
    try:
        if args.respect_runtime_mounts and args.syscalls not in ('files', 'metadata', 'mmap', 'kuser', 'uipc', 'threads'):
            raise ValueError('Runtime mount checks require files, metadata, mmap, kuser, uipc, or threads policy')
        report = _run_selected_target(args, target_profile)
        print(f"Stopped: {report['status']}; original-code instructions: {report['instructions_executed']}")
        if 'boundary' in report:
            print(json.dumps(report['boundary'], indent=2))
        return 0 if report['status'] in ('syscall_boundary', 'memory_boundary') else 2
    except _PROBE_RECEIPT_ERRORS as error:
        if isinstance(report, dict) and (
            'instructions_executed' in report or 'boundary' in report or 'target_input_selection' in report
        ):
            report['probe_status'] = report.get('status')
            report['status'] = 'configuration_error'
            report['camera_fidelity_validated'] = False
            report.setdefault('target_id', args.target)
            report['error'] = str(error)
        else:
            report = {'status': 'configuration_error', 'camera_fidelity_validated': False,
                      'target_id': args.target, 'error': str(error)}
        print(f'Probe configuration failed: {error}')
        return 2
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(f'Report: {args.report}')


if __name__ == '__main__':
    raise SystemExit(main())
