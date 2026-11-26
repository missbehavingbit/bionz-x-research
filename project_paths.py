"""Paths shared by preserved research tools; no device behavior lives here."""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
LEGACY_ARTIFACTS = PROJECT_ROOT / "artifacts" / "legacy"
REFERENCE_ASSETS = PROJECT_ROOT / "assets" / "reference"


def a6400_dump_dir(root=PROJECT_ROOT):
    """Resolve the declared capture, retaining legacy paths on unconfigured clones."""
    root = Path(root)
    config_path = root / 'qemu-a6400' / 'inputs.local.json'
    if not config_path.is_file():
        return root / 'artifacts' / 'dumps' / 'a6400-nflasha-readonly'
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if config.get('schema_version') != 1 or config.get('target_id', 'ilce_6400') != 'ilce_6400':
        raise ValueError('Unsupported a6400 capture configuration')
    raw = config.get('dump_dir')
    if not isinstance(raw, str) or not raw:
        raise ValueError('Capture dump_dir must be an absolute directory')
    windows = PureWindowsPath(raw)
    if sys.platform.startswith('linux') and windows.drive:
        if not re.fullmatch(r'[A-Za-z]:', windows.drive) or not windows.root:
            raise ValueError('Capture path must be a rooted Windows drive path')
        directory = Path('/mnt') / windows.drive[0].lower() / Path(*windows.parts[1:])
    else:
        directory = Path(raw)
    if not directory.is_absolute():
        raise ValueError('Capture dump_dir must be an absolute directory')
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise ValueError('Capture dump_dir is not a directory')
    for filename, field, limit in (
            ('manifest.json', 'manifest_sha256', 16 * 1024 ** 2),
            ('SHA256SUMS.txt', 'checksums_sha256', 1024 ** 2)):
        expected = config.get(field)
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError(f'Missing or invalid capture integrity pin: {field}')
        path = directory / filename
        if path.stat().st_size > limit:
            raise ValueError(f'Capture metadata exceeds its read bound: {filename}')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError(f'Capture SHA-256 mismatch: {filename}')
    return directory


def legacy_output(filename):
    LEGACY_ARTIFACTS.mkdir(parents=True, exist_ok=True)
    return str(LEGACY_ARTIFACTS / filename)
