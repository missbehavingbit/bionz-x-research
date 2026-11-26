"""Package a Mod Loader v2 build for the camera-side SD worker."""

import argparse
import hashlib
import io
import tarfile
from pathlib import Path

from project_paths import PROJECT_ROOT


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--target", choices=("ilce_6400", "ilce_6400a"), default="ilce_6400")
    parser.add_argument(
        "--include-arrivederci",
        action="store_true",
        help="Explicitly bundle Arrivederci.mod into ILCE-6400A MLV2.TAR (color matrix persists in Backup.bin if powered off while ON).",
    )
    parser.add_argument(
        "--mod",
        type=Path,
        action="append",
        default=[],
        help="Extra .mod file to include instead of the default MenuTouchFull+icon bundle",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        action="append",
        default=[],
        help="Extra directory (e.g. sabin, grain) to include at archive root",
    )
    args = parser.parse_args(argv)
    build = args.build

    guard_path = build / "companion/mlv2_guard_compact.elf"
    if not guard_path.is_file():
        guard_path = build / "mlv2_guard_arm"
    members = {
        "guard": guard_path.read_bytes(),
        "mlv2.req": (build / "mlv2.req").read_bytes(),
        "core.bin": (build / "core.bin").read_bytes(),
    }
    if args.mod or args.dir:
        for mod_path in args.mod:
            members[mod_path.name] = mod_path.read_bytes()
        for dir_path in args.dir:
            for item in sorted(dir_path.iterdir()):
                if item.is_file():
                    members[f"{dir_path.name}/{item.name}"] = item.read_bytes()
    elif args.target == "ilce_6400a":
        a6400a_dir = PROJECT_ROOT / "mods/ilce_6400a"
        # UsbStreamingTest.mod is a QEMU-only diagnostic action and refuses on hardware.
        # Arrivederci's color matrix can be persisted by Sony's shutdown Backup.bin if left ON at
        # power-off, so it stays outside the automatic SD package unless --include-arrivederci is set.
        excluded = {"UsbStreamingTest.mod"}
        if not args.include_arrivederci:
            excluded.add("Arrivederci.mod")
        for mod_path in sorted(a6400a_dir.glob("*.mod"), key=lambda p: p.name):
            if mod_path.name in excluded:
                continue
            members[mod_path.name] = mod_path.read_bytes()
    else:
        touch = PROJECT_ROOT / "mods/menu_touch_full/MenuTouchFull.mod"
        icon = PROJECT_ROOT / (
            "artifacts/publish/mod-loader-sd/artifacts/hardware/sd-standalone-v1/"
            "boot-mod-loader/ModsTabIcon.rle"
        )
        if not icon.is_file():
            icon = PROJECT_ROOT / "mods/mods_tab_look/mods_tab_icon.rle"
        verified_assets = (
            ("MenuTouchFull.mod", touch, "334a3dd491d04a87e5318694ac652fca65dfe7cd2575e7e6886620bc282b1b80"),
            ("ModsTabIcon.rle", icon, "1690720dce59a39505284190f049425e8fcceef3c6c302288c19b3ff17ae0fc6"),
        )
        for member_name, path, expected in verified_assets:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError(f"Asset identity mismatch: {path}")
            members[member_name] = data

    # Arrivederci's matrix can be persisted by Sony's backup. It stays outside
    # the automatic package until a verified backup-safe implementation exists.
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w", format=tarfile.USTAR_FORMAT) as card:
        for name, data in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o600
            card.addfile(member, io.BytesIO(data))
    payload = archive.getvalue()
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("SD archive exceeds the camera reader limit")

    sh_template = (
        "tools/native_camera/mlv2/sd_boot_ilce6400a.sh"
        if args.target == "ilce_6400a"
        else "tools/native_camera/mlv2/sd_boot.sh"
    )
    body = (PROJECT_ROOT / sh_template).read_text(encoding="ascii")
    header, body = body.split("\n", 1)
    digest = hashlib.sha256(payload).hexdigest()
    (build / "card").mkdir(parents=True, exist_ok=True)
    (build / "companion").mkdir(parents=True, exist_ok=True)
    with (build / "card/MLV2.TAR").open("wb") as output:
        output.write(payload)
    with (build / "companion/mlv2_sd_boot.sh").open("w", encoding="ascii", newline="\n") as output:
        output.write(f"{header}\nset -- {digest}\n{body}")
    print(f"Packaged MLV2.TAR ({len(payload)} B, sha256={digest[:12]}...) with {len(members)} entries")


if __name__ == "__main__":
    main()
