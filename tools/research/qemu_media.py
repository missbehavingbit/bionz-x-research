"""Use a declared raw SD image and verify offline playback or JPEG output."""

import argparse
import json
from pathlib import Path
import re
import stat
import time
from typing import TYPE_CHECKING

from tools.research.inventory import digest

if TYPE_CHECKING:
    from tools.research.qemu_boot_probe import Qmp


SD_DRIVE = 'a6400-sd'
SD_NODE = 'a6400-sd-image'


def sd_blockdev(image: Path, sha256: str, inputs: Path, run_dir: Path) -> dict:
    if not re.fullmatch(r'[0-9a-f]{64}', sha256):
        raise ValueError('SD_SHA256 must identify the raw card contents (64 lowercase hex digits)')
    image = image.resolve(strict=True)
    info = image.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('The SD medium must be a regular, non-hardlinked image file')
    if image.is_relative_to(inputs.resolve()) or image.is_relative_to(run_dir.resolve()):
        raise ValueError('The SD medium must be outside firmware inputs and the disposable run directory')
    size = info.st_size
    if size < 2**20 or (size <= 2**31 and size & (size - 1)) or size % (512 * 1024):
        raise ValueError('The SD image needs power-of-two SDSC or 512 KiB-aligned SDHC capacity')
    if digest(image) != sha256:
        raise ValueError('SD image SHA-256 mismatch')
    return {'driver': 'raw', 'node-name': SD_NODE,
            'file': {'driver': 'file', 'filename': str(image)}}


def sd_event(qmp: 'Qmp', action: str) -> None:
    if action not in ('insert', 'eject'):
        raise ValueError(f'Unknown SD operation: {action}')
    drives = [drive for drive in qmp.request('query-block') if drive['device'] == SD_DRIVE]
    if len(drives) != 1 or not drives[0].get('qdev') or not drives[0].get('removable'):
        raise ValueError('No attached a6400 removable SD slot')
    drive = drives[0]
    device = drive['qdev']
    if action == 'insert':
        qmp.request('blockdev-insert-medium', {'id': device, 'node-name': SD_NODE})
    else:
        if drive.get('inserted', {}).get('node-name') != SD_NODE:
            raise ValueError('The predeclared SD image is not inserted')
        qmp.request('blockdev-remove-medium', {'id': device})


def verify_photo(before: Path, after: Path, *, reopened: Path | None = None) -> dict:
    """Require one new, independently decodable JPEG in an exported DCIM tree."""
    from PIL import Image

    def photos(root: Path) -> dict[str, Path]:
        dcim = root / 'DCIM'
        if not root.is_dir():
            raise ValueError(f'Card export directory does not exist: {root}')
        return {p.relative_to(dcim).as_posix().upper(): p for p in dcim.glob('*/*')
                if p.is_file() and p.suffix.lower() in ('.jpg', '.jpeg')}

    existing, current = photos(before), photos(after)
    added = current.keys() - existing.keys()
    if len(added) != 1:
        raise ValueError(f'Expected exactly one new DCIM JPEG, found {len(added)}')
    name = added.pop()
    photo = current[name]
    sha256 = digest(photo)
    if any(digest(path) == sha256 for path in existing.values()):
        raise ValueError('New filename contains an unchanged baseline JPEG')
    with Image.open(photo, formats=('JPEG',)) as image:
        image.load()
        width, height = image.size
    result = {'path': f'DCIM/{name}', 'sha256': sha256, 'width': width, 'height': height,
              'verification': 'new decodable JPEG; provenance depends on the replay controls'}
    if reopened is not None:
        if reopened.resolve() in (before.resolve(), after.resolve()):
            raise ValueError('Provide a separate DCIM export from the reopened card')
        persisted = photos(reopened).get(name)
        if persisted is None:
            raise ValueError(f'Reopened card has no DCIM/{name}')
        reopened_sha256 = digest(persisted)
        if reopened_sha256 != sha256:
            raise ValueError(f'Reopened DCIM/{name} does not match the saved JPEG')
        result['reopened_sha256'] = reopened_sha256
    return result


def verify_playback(fixture: Path, empty: Path, inserted: Path,
                    image_box: tuple[int, int, int, int] | None = None) -> dict:
    """Compare QEMU RGB captures against a high-contrast playback fixture."""
    from PIL import Image, ImageChops, ImageStat

    sample_size = (64, 48)
    with Image.open(fixture, formats=('JPEG',)) as photo:
        expected = photo.convert('RGB').resize(sample_size, Image.Resampling.BOX)
    scores = []
    head_size = None
    for path in (empty, inserted):
        with Image.open(path, formats=('PPM',)) as display:
            if head_size is not None and display.size != head_size:
                raise ValueError('Compare the same QEMU head geometry in both captures')
            head_size = display.size
            box = image_box or (0, 0, *head_size)
            left, top, right, bottom = box
            if not (0 <= left < right <= head_size[0] and 0 <= top < bottom <= head_size[1]):
                raise ValueError('Playback image box must fit inside the QEMU head')
            actual = display.convert('RGB').crop(box).resize(sample_size, Image.Resampling.BOX)
            scores.append(max(ImageStat.Stat(ImageChops.difference(expected, actual)).mean))
    empty_error, inserted_error = scores
    threshold = 12
    detail = f'empty error {empty_error:.2f}; inserted error {inserted_error:.2f}; limit {threshold}'
    if empty_error <= threshold:
        raise ValueError(f'Empty-card control already matches the fixture ({detail})')
    if inserted_error > threshold:
        raise ValueError(f'Inserted-card capture does not show the fixture ({detail})')
    return {'fixture_sha256': digest(fixture), 'empty_head_sha256': digest(empty),
            'inserted_head_sha256': digest(inserted), 'image_box': box,
            'worst_channel_mae': {'empty': empty_error, 'inserted': inserted_error},
            'mae_limit': threshold}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    sd = commands.add_parser('sd-blockdev', help='Validate a raw card and print its QEMU block node')
    sd.add_argument('--image', type=Path, required=True)
    sd.add_argument('--sha256', required=True)
    sd.add_argument('--inputs', type=Path, required=True)
    sd.add_argument('--run-dir', type=Path, required=True)
    media = commands.add_parser('sd-event', help='Insert or eject the predeclared card')
    media.add_argument('action', choices=('insert', 'eject'))
    media.add_argument('--qmp', type=Path, required=True)
    playback = commands.add_parser('verify-playback', help='Compare empty and inserted QEMU PPMs')
    playback.add_argument('--fixture', type=Path, required=True)
    playback.add_argument('--empty', type=Path, required=True)
    playback.add_argument('--inserted', type=Path, required=True)
    playback.add_argument('--image-box', type=int, nargs=4, metavar=('LEFT', 'TOP', 'RIGHT', 'BOTTOM'))
    capture = commands.add_parser('verify-photo', help='Decode a newly saved JPEG independently')
    capture.add_argument('--before', type=Path, required=True)
    capture.add_argument('--after', type=Path, required=True)
    capture.add_argument('--reopened', type=Path,
                         help='Separate DCIM export after reopening the card in a new QEMU process')
    args = parser.parse_args()
    try:
        if args.command == 'sd-blockdev':
            print(json.dumps(sd_blockdev(args.image, args.sha256, args.inputs, args.run_dir)))
        elif args.command == 'sd-event':
            from tools.research.qemu_boot_probe import Qmp

            qmp = Qmp(args.qmp, time.monotonic() + 10)
            try:
                sd_event(qmp, args.action)
            finally:
                qmp.close()
        elif args.command == 'verify-playback':
            box = tuple(args.image_box) if args.image_box is not None else None
            print(json.dumps(verify_playback(args.fixture, args.empty, args.inserted, box), indent=2))
        else:
            print(json.dumps(verify_photo(args.before, args.after, reopened=args.reopened), indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
