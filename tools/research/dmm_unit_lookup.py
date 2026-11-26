"""Read-only DMM unit lookup from the package ``setting/DmmConfig.bin``.

Runs the original ``dmm.ko`` extent/unit table parsers in the same bounded
cold-module slice as :mod:`tools.research.dmm_config_trace`, then reports the
configured extent chain and size for requested DMM unit types.

This is configuration parsing only. It does not allocate, does not produce a
runtime physical base or DMM handle, and executes no kernel, IPC or device path.
Use it to identify what a caller's unit number means, never to synthesize a
service reply.
"""
import argparse
import json


def parse_tables():
    """Return (tables, input_hashes) after both original parsers complete."""
    # Imported here so the pure table decoder (describe_unit) needs no emulator deps.
    from tools.research.dmm_config_trace import (CONFIG, MODULE, PROJECT_ROOT,
                                                 DmmConfigProbe)
    from tools.research.mount_evidence import checked_input

    inventory = PROJECT_ROOT / 'artifacts/inventory'
    baseline = json.loads((inventory / 'baseline.json').read_text(encoding='utf-8'))
    expected = {row['path']: row['sha256'] for row in baseline['inputs']}
    raw = checked_input(PROJECT_ROOT, MODULE, expected[MODULE])
    config = checked_input(PROJECT_ROOT, CONFIG, expected[CONFIG])
    probe = DmmConfigProbe(raw, config)
    cursors = []
    for name in ('dmm_ext_tbl_Init', 'dmm_unit_tbl_Init'):
        result = probe.call(name)
        if result['status'] != 'isolated_function_return' or result['return_value'] != 0:
            raise RuntimeError(f'{name} did not complete: {result["status"]}')
        cursors.append({'function': name, 'config_cursor': result['config_cursor'],
                        'instructions': result['instructions_executed']})
    tables = probe.tables()
    tables['cursors'] = cursors
    return tables, {MODULE: expected[MODULE], CONFIG: expected[CONFIG]}


def describe_unit(tables, unit_type):
    """Describe one unit: parent headers, or a leaf extent and its owning parent."""
    parents = {item['header'][3]: item for item in tables['extents']}
    leaves = {}
    for item in list(tables['root_extents']) + list(tables['extents']):
        owner = item['header'][3]
        for child in item['children']:
            leaves.setdefault(child[2], []).append({'parent': owner, 'size': child[3]})

    rows = [unit for unit in tables['units'] if unit['unit'] == unit_type]
    if not rows:
        return None
    described = []
    for unit in rows:
        extents = []
        for extent_id in unit['extents']:
            header = parents.get(extent_id)
            if header is not None:
                extents.append({
                    'extent': extent_id,
                    'kind': 'parent',
                    'header': [f'0x{word:08x}' for word in header['header'][:6]],
                    'leaves': [{'extent': child[2], 'size': child[3]}
                               for child in header['children']],
                })
            elif extent_id in leaves:
                extents.append({'extent': extent_id, 'kind': 'leaf',
                                'owners': leaves[extent_id]})
            else:
                extents.append({'extent': extent_id, 'kind': 'missing'})
        described.append({'unit': unit_type, 'extents': extents})
    return described


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('units', nargs='+', type=lambda value: int(value, 0),
                        help='DMM unit types to look up (e.g. 0x4092e)')
    parser.add_argument('--json', action='store_true', help='emit JSON instead of text')
    args = parser.parse_args()

    tables, hashes = parse_tables()
    report = {'inputs': hashes,
              'cursors': tables['cursors'],
              'extent_headers': len(tables['extents']),
              'unit_records': len(tables['units']),
              'units': {}}
    for unit_type in args.units:
        report['units'][f'0x{unit_type:08x}'] = describe_unit(tables, unit_type)

    if args.json:
        print(json.dumps(report, indent=2))
        return

    print(f"dmm.ko {hashes[MODULE]}")
    print(f"DmmConfig.bin {hashes[CONFIG]}")
    for cursor in report['cursors']:
        print(f"  {cursor['function']}: cursor {cursor['config_cursor']}, "
              f"{cursor['instructions']} instructions")
    print(f"extent headers {report['extent_headers']}, unit records {report['unit_records']}")
    for name, described in report['units'].items():
        if described is None:
            print(f"{name}: not present in the unit table")
            continue
        for row in described:
            print(f"{name}: extents {[hex(e['extent']) for e in row['extents']]}")
            for extent in row['extents']:
                kind = extent['kind']
                if kind == 'parent':
                    print(f"  extent 0x{extent['extent']:03x} parent header {extent['header']}")
                    for leaf in extent['leaves']:
                        print(f"    leaf extent 0x{leaf['extent']:03x} "
                              f"size 0x{leaf['size']:x} ({leaf['size']} bytes)")
                elif kind == 'leaf':
                    for owner in extent['owners']:
                        print(f"  extent 0x{extent['extent']:03x} leaf under parent "
                              f"0x{owner['parent']:03x} size 0x{owner['size']:x} "
                              f"({owner['size']} bytes)")
                else:
                    print(f"  extent 0x{extent['extent']:03x} not found in parsed tables")


if __name__ == '__main__':
    main()
