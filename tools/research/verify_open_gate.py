"""Verify a new, playable 3:2 clip in before/after exports of a recording directory."""

import argparse
import json
from pathlib import Path
import subprocess

from tools.research.inventory import digest


CLIP_SUFFIXES = {'.mp4', '.mov', '.mts', '.avi'}
TOOL_TIMEOUT = 180


def _clips(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        raise ValueError(f'Clip export directory does not exist: {directory}')
    clips = {}
    for path in directory.iterdir():
        if path.suffix.lower() not in CLIP_SUFFIXES:
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Clip export entry must be a regular file: {path}')
        key = path.name.upper()
        if key in clips:
            raise ValueError(f'Ambiguous clip name in export: {path.name}')
        clips[key] = path
    return clips


def _run(*command: str) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=TOOL_TIMEOUT,
                                stdin=subprocess.DEVNULL, check=False)
    except FileNotFoundError as error:
        raise RuntimeError(f'{command[0]} is required on PATH') from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f'{command[0]} exceeded the {TOOL_TIMEOUT}-second verification bound') from error
    if result.returncode or result.stderr.strip():
        raise ValueError(f'{command[0]} could not read/decode the clip: '
                         f'{result.stderr.strip()[:500] or f"exit {result.returncode}"}')
    return result.stdout


def verify_open_gate(before: Path, after: Path, width: int, height: int) -> dict:
    """Check new bytes, exact geometry and full decoding; not recording provenance."""
    if min(width, height) <= 0 or 2 * width != 3 * height:
        raise ValueError('Expected dimensions must have an exact 3:2 aspect ratio')
    existing, current = _clips(before), _clips(after)
    added = current.keys() - existing.keys()
    if len(added) != 1:
        raise ValueError(f'Expected exactly one new clip, found {len(added)}')
    clip = current[added.pop()].resolve()
    size = clip.stat().st_size
    sha256 = digest(clip)
    if any(path.stat().st_size == size and digest(path) == sha256 for path in existing.values()):
        raise ValueError('New filename contains an unchanged baseline clip')

    probe = json.loads(_run(
        'ffprobe', '-v', 'error', '-protocol_whitelist', 'file', '-select_streams', 'V',
        '-show_entries', 'stream=codec_name,width,height,sample_aspect_ratio,display_aspect_ratio,nb_frames'
        ':stream_side_data=rotation:stream_tags=rotate', '-of', 'json', str(clip)))
    streams = probe.get('streams', [])
    if len(streams) != 1:
        raise ValueError(f'Expected exactly one video stream, found {len(streams)}')
    stream = streams[0]
    if (stream.get('width'), stream.get('height')) != (width, height):
        raise ValueError(f'Expected {width}x{height} video, got {stream.get("width")}x{stream.get("height")}')
    rotations = [stream.get('tags', {}).get('rotate')]
    rotations.extend(data.get('rotation') for data in stream.get('side_data_list', []))
    if (stream.get('sample_aspect_ratio') != '1:1' or stream.get('display_aspect_ratio') != '3:2'
            or any(value not in (None, 0, '0') for value in rotations)):
        raise ValueError('Video must display as unrotated, square-pixel 3:2')

    progress = _run(
        'ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-err_detect', 'explode',
        '-protocol_whitelist', 'file', '-i', str(clip), '-map', '0:V:0', '-map', '0:a?',
        '-fps_mode', 'passthrough', '-f', 'null', '-nostats', '-progress', 'pipe:1', '-')
    fields = dict(line.split('=', 1) for line in progress.splitlines() if '=' in line)
    frames, time_us = fields.get('frame', ''), fields.get('out_time_us', '')
    if (fields.get('progress') != 'end' or not frames.isdecimal() or int(frames) < 2
            or not time_us.isdecimal() or int(time_us) <= 0):
        raise ValueError('Full playback did not reach EOF with at least two frames and positive time')
    declared = stream.get('nb_frames', '')
    if declared.isdecimal() and int(frames) != int(declared):
        raise ValueError(f'Decoded {frames} frames, but the clip declares {declared}')
    if digest(clip) != sha256:
        raise ValueError('Clip bytes changed during verification')
    return {
        'path': clip.name, 'bytes': size, 'sha256': sha256, 'codec': stream.get('codec_name'),
        'width': width, 'height': height, 'sample_aspect_ratio': '1:1', 'display_aspect_ratio': '3:2',
        'decoded_frames': int(frames), 'decoded_time_us': int(time_us),
        'verification': 'new decodable clip; recording provenance depends on the replay controls',
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--after', required=True, type=Path)
    parser.add_argument('--size', required=True, type=int, nargs=2, metavar=('WIDTH', 'HEIGHT'))
    args = parser.parse_args()
    try:
        result = verify_open_gate(args.before, args.after, *args.size)
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
