"""Build QEMU boot inputs for sony-a6400 from Sony's public a6400 updater alone.

The captured route (qemu_boot_inputs.py) needs a read-only eMMC dump of a camera. This one
needs nothing but the updater package that anyone can download and unpack: it lays the
packaged partitions into an eMMC backing file with the SDM partition table the updater itself
declares, and writes the legacy qemu-a6400/data/ layout that run.sh reads without
A6400_QEMU_DATA_DIR.

What is packaged and what is constructed is recorded per output in manifest.json:

* packaged, copied byte for byte: nflasha3 (/system), nflasha5 (WBI), nflasha7 (rootfs),
  nflasha15 (/usr), the kernel, initrd and av-cam from nflasha3, Backup.bin from 0110_backup;
* constructed: the SDM header (from 0300_partconf/partinf.conf), FAT file systems for the
  vfat partitions (mount.conf geometry), /setting, /lens and /tmp filled from 0800_appli,
  /setting/Backup.bin from the copy 0110_backup/MainDest.txt names,
  zeros for every partition the updater does not carry (zero-filled space is emulator space,
  not camera content), and the sub-micro RX frames from the packaged warm-boot image with
  slot B switched from hibernate to powered as E0651 did for a camera's own image.

Boot the powered menu with
SUBMICRO_FRAMES=<out>/submicro-frames-powered.bin SUBMICRO_FRAMES_SHA=<its SHA-256>.

No camera access, no network, no source writes. Outputs go to a new directory only.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

#: Two 4 MiB boot partitions precede the user area in the sony-a6400 backing file (E0524).
USER_AREA_OFFSET = 8 * 1024 * 1024
SDM_MAGIC = b"8246"
SDM_VERSION = b"1.00"
#: Partitions the updater ships as images, by device number.
PACKAGED_IMAGES = (3, 5, 7, 15)
#: 0800_appli subdirectories and the mount point they populate.
APPLI_DIRS = {"setting": "/setting", "lens": "/lens", "tmp": "/tmp"}
#: The 384-byte SIO ch3 sub-micro RX slots A/C/B in the warm-boot image (E0651).
SUBMICRO_RX = (0x034433A4, 0x03443524)
#: Slot B TLV 0x03 data: hibernate as packaged, powered as E0527 section 0.2 records it.
TLV03_HIBERNATE = bytes.fromhex("00400002")
TLV03_POWERED = bytes.fromhex("00000003")


def parse_partinf(text: str) -> list[dict]:
    """Rows of partinf.conf (start,size,type,flag) in device order, comments skipped."""
    rows = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = [int(value, 16) for value in line.split(",")]
        if len(fields) != 4:
            raise ValueError(f"partinf.conf row is not start,size,type,flag: {line!r}")
        start, size, kind, flag = fields
        rows.append(dict(device=f"nflasha{len(rows) + 1}", start=start, bytes=size,
                         type=kind, flags=flag))
    if not rows:
        raise ValueError("partinf.conf declares no partitions")
    return rows


def parse_mount_conf(text: str) -> dict[str, dict]:
    """Rows of mount.conf (node, mount point, fstype, size, cluster) keyed by device name."""
    table = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        node, mount, fstype, size, cluster = line.split()
        table[node.rsplit("/", 1)[-1]] = dict(mount=mount, fstype=fstype, bytes=int(size),
                                              cluster=int(cluster))
    return table


def encode_sdm_header(rows: list[dict]) -> bytes:
    """The SDM table as the kernel parser reads it (fs/partitions/sdm_partition_table.h)."""
    header = SDM_MAGIC + SDM_VERSION + struct.pack("<I", len(rows)) + bytes(20)
    body = b"".join(struct.pack("<4I", r["start"], r["bytes"], r["type"], r["flags"])
                    for r in rows)
    table = header + body
    first = min(r["start"] for r in rows if r["bytes"])
    if len(table) > first:
        raise ValueError("SDM table overlaps the first partition")
    return table


def backing_size(rows: list[dict]) -> int:
    """Smallest power-of-two file that holds the boot partitions and every user partition."""
    end = max(r["start"] + r["bytes"] for r in rows)
    return 1 << (USER_AREA_OFFSET + end - 1).bit_length()


def submicro_frames_from_wbi(raw: bytes) -> tuple[bytes, bytes]:
    """The packaged A/C/B sub-micro frames, and the same frames with slot B powered.

    The powered copy is the E0651 transformation applied to public data: slot B TLV 0x03
    goes from hibernate to powered and the slot's CRC is restamped; nothing else changes.
    """
    from tools.research.wbi_memory import decode_section, parse_sections

    low, high = SUBMICRO_RX
    _, sections = parse_sections(raw)
    section = next((s for s in sections
                    if s["physical"] <= low and high <= s["physical"] + s["size"]), None)
    if section is None:
        raise ValueError("the warm-boot image does not cover the sub-micro RX frames")
    memory = decode_section(raw, section)[0]
    frames = memory[low - section["physical"]:high - section["physical"]]
    return frames, power_slot_b(frames)


def power_slot_b(frames: bytes) -> bytes:
    """A/C/B frames with slot B TLV 0x03 moved from hibernate to powered and its CRC restamped."""
    from src.a6400.execution.submicro_link import stamp_frame_crc, verify_frame_crc

    if len(frames) != 384:
        raise ValueError("sub-micro RX frames are three 128-byte slots")
    if not all(verify_frame_crc(frames[i:i + 128]) for i in range(0, 384, 128)):
        raise ValueError("a packaged sub-micro frame fails its CRC")
    slot_b = bytearray(frames[256:384])
    position, end, data = 4, 4 + slot_b[2], None
    while position + 4 <= end:
        if slot_b[position] == 0x03:
            data = position + 4
            break
        position += 4 + slot_b[position + 1]
    if data is None or bytes(slot_b[data:data + 4]) != TLV03_HIBERNATE:
        raise ValueError("packaged slot B TLV 0x03 is not the hibernate state")
    slot_b[data:data + 4] = TLV03_POWERED
    return frames[:256] + stamp_frame_crc(slot_b)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(*command: str) -> None:
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                   env={**os.environ, "MTOOLS_SKIP_CHECK": "1", "TZ": "UTC"})


def make_fat(size: int, cluster: int, files: Path | None, work: Path, label: str,
             extra: tuple[Path, ...] = ()) -> Path:
    """An empty or populated FAT image of the given size, deterministic for equal inputs."""
    image = work / f"{label}.img"
    with image.open("wb") as handle:
        handle.truncate(size)
    sectors = max(1, cluster // 512) if cluster else 32
    _run("mkfs.fat", "--invariant", "-i", "00000000", "-s", str(sectors), str(image))
    if files is not None:
        for entry in sorted(files.iterdir()):
            _run("mcopy", "-s", "-m", "-o", "-i", str(image), str(entry), "::/")
    for entry in extra:
        _run("mcopy", "-m", "-o", "-i", str(image), str(entry), "::/" + entry.name)
    return image


def _write_at(disk, offset: int, source: Path, limit: int) -> int:
    size = source.stat().st_size
    if size > limit:
        raise ValueError(f"{source.name} ({size} bytes) exceeds its partition ({limit} bytes)")
    disk.seek(offset)
    with source.open("rb") as stream:
        shutil.copyfileobj(stream, disk, 1 << 20)
    return size


def build(updater: Path, output: Path) -> dict:
    updater, output = updater.resolve(), output.resolve()
    if output.is_relative_to(updater) or updater.is_relative_to(output):
        raise ValueError("outputs must not overlap the updater tree")
    rows = parse_partinf((updater / "0300_partconf/partinf.conf").read_text(errors="replace"))
    mounts = parse_mount_conf((updater / "0100_config/mount.conf").read_text(errors="replace"))
    output.mkdir(parents=True, exist_ok=False)
    devdir = updater / "0700_part_image/dev"
    report = dict(schema_version=1, source="packaged updater", inputs=[], outputs=[],
                  partitions=[])

    def note_input(path: Path) -> None:
        report["inputs"].append(dict(path=str(path.relative_to(updater)),
                                     bytes=path.stat().st_size, sha256=_sha256(path)))

    for name in ("0300_partconf/partinf.conf", "0100_config/mount.conf"):
        note_input(updater / name)

    # backup.ko refuses to load unless /setting/Backup.bin exists (Backup_pri_file and
    # Backup_sec_file both name it); the updater's 0110_backup/MainDest.txt names the copy.
    dest = (updater / "0110_backup/MainDest.txt").read_text(errors="replace").split()
    backup = updater.joinpath("0110_backup", *dest)
    note_input(backup)

    with tempfile.TemporaryDirectory(prefix="a6400-fat-") as tmp, \
            (output / "emmc.img").open("xb") as disk:
        work = Path(tmp)
        staged_backup = work / "Backup.bin"
        shutil.copy2(backup, staged_backup)  # keep the packaged mtime: equal inputs, equal image
        disk.truncate(backing_size(rows))
        disk.seek(USER_AREA_OFFSET)
        disk.write(encode_sdm_header(rows))
        for index, row in enumerate(rows, start=1):
            if not row["bytes"]:
                continue
            offset = USER_AREA_OFFSET + row["start"]
            mount = mounts.get(row["device"], {})
            entry = dict(device=row["device"], start=row["start"], bytes=row["bytes"],
                         mount=mount.get("mount"), fstype=mount.get("fstype"))
            image = devdir / row["device"]
            if index in PACKAGED_IMAGES:
                if not image.is_file():
                    raise ValueError(f"the updater does not carry {row['device']}")
                note_input(image)
                entry.update(origin="packaged",
                             copied=_write_at(disk, offset, image, row["bytes"]),
                             sha256=_sha256(image))
            elif mount.get("fstype") == "vfat":
                source = next((updater / "0800_appli" / sub for sub, point in APPLI_DIRS.items()
                               if point == mount.get("mount")), None)
                if source is not None:
                    for member in sorted(p for p in source.rglob("*") if p.is_file()):
                        note_input(member)
                extra = (staged_backup,) if mount.get("mount") == "/setting" else ()
                fat = make_fat(row["bytes"], mount.get("cluster", 16384), source, work,
                               row["device"], extra)
                _write_at(disk, offset, fat, row["bytes"])
                origin = "constructed FAT, empty" if source is None else \
                    f"constructed FAT from 0800_appli/{source.name}"
                entry.update(origin=origin, sha256=_sha256(fat))
                fat.unlink()
            else:
                entry.update(origin="zero-filled, not carried by the updater")
            report["partitions"].append(entry)

    sysdir = devdir / "nflasha3_unpacked"
    for source, name in (("vmlinux.bin", "nflasha3-Image"), ("initrd.img", "nflasha3-initrd"),
                         ("av-cam.bin", "av-cam.bin")):
        note_input(sysdir / source)
        shutil.copyfile(sysdir / source, output / name)

    wbi = devdir / "nflasha5"
    packaged, powered = submicro_frames_from_wbi(wbi.read_bytes())
    (output / "submicro-frames-packaged.bin").write_bytes(packaged)
    (output / "submicro-frames-powered.bin").write_bytes(powered)

    kemco = updater / "0800_appli/setting/kemco.txt"
    with tarfile.open(output / "setting.tar", "w", format=tarfile.USTAR_FORMAT) as tar:
        for name, path in (("./kemco.txt", kemco), ("./Backup.bin", backup)):
            data = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))

    for path in sorted(output.iterdir()):
        report["outputs"].append(dict(path=path.name, bytes=path.stat().st_size,
                                      sha256=_sha256(path)))
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("updater", type=Path,
                        help="the unpacked updater: the directory holding 0700_part_image")
    parser.add_argument("output", type=Path, help="a new directory, e.g. qemu-a6400/data")
    args = parser.parse_args(argv)
    for tool in ("mkfs.fat", "mcopy"):
        if shutil.which(tool) is None:
            parser.error(f"{tool} is required (dosfstools, mtools)")
    report = build(args.updater, args.output)
    for item in report["partitions"]:
        print(f"{item['device']:>10}  {item['mount'] or '-':<16} {item['origin']}")
    for item in report["outputs"]:
        print(f"{item['sha256']}  {item['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
