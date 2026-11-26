"""Prepare the E0572 diagnostic sector from the original eMMC backing.

The patched instruction is viewUnified2.so file offset 0x40a374 (`mov r0,r4` -> `movs r0,#1`).
Each known backing image holds that file sector at its own disk offset; the sector must match
its recorded SHA-256 exactly. Prints the disk offset to write the patched sector to.
"""

import hashlib
from pathlib import Path
import sys

KNOWN = (
    # legacy update-package image (qemu-a6400/data/emmc.img, E0572)
    (0x360EAA00, "ce879a8884e540e4eb1d811dea56d434545be93a771d64127950cf5d13ec9ecc"),
    # camera-captured prepared image (E0524 emmc 18e37ec3...), camera viewUnified2.so build
    (0x37EA5200, "3374dcd783dbc7dfad3f4945578b63c856cdb75ddf5edf2624fe3c50c0a451f3"),
)


def main():
    source, destination = map(Path, sys.argv[1:])
    with source.open("rb") as disk:
        for offset, expected in KNOWN:
            disk.seek(offset)
            original = disk.read(512)
            if hashlib.sha256(original).hexdigest() == expected:
                break
        else:
            raise SystemExit("E0572 rear LCD overlay source sector does not match")
    if original[0x174:0x176] != bytes.fromhex("2046"):
        raise SystemExit("E0572 rear LCD overlay instruction does not match")
    patched = original[:0x174] + bytes.fromhex("0120") + original[0x176:]
    with destination.open("xb") as sector:
        sector.write(patched)
    print(hex(offset))


if __name__ == "__main__":
    main()
