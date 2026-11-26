"""Unit and integration tests for Mod Loader v2 Option B SD auto-boot (FAT32/exFAT reader + ILCE-6400A installer)."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from project_paths import PROJECT_ROOT
from tools.research.a6400a_sd_boot_install import (
    HOOK_NAME,
    PRELOAD_ENTRY,
    READER_NAME,
    SCRIPT_NAME,
    build_worker_script,
    main as installer_main,
    prep_sd_card,
    sha256_bytes,
    validate_sd_build,
)
from tools.research import package_mlv2_sd


def _make_valid_sd_build(
    build_dir: Path,
    *,
    shim_addr: int = 0x05D76800,
    include_usb_mod: bool = False,
    include_arrivederci: bool = False,
) -> dict[str, object]:
    (build_dir / "companion").mkdir(parents=True, exist_ok=True)
    (build_dir / "card").mkdir(parents=True, exist_ok=True)
    (build_dir / "build.json").write_text(
        json.dumps({"shim": {"addr": hex(shim_addr)}, "core": {"sha256": "0" * 64}}),
        encoding="utf-8",
    )

    buf = io.BytesIO()
    members = {
        "guard": b"\x7fELFGUARD_COMPACT",
        "mlv2.req": b"ML2QREQ",
        "core.bin": b"MLCRCORE",
        "MenuTouchFull.mod": b"SMODMTF",
        "ModsTabLook.mod": b"SMODMTL",
    }
    if include_usb_mod:
        members["UsbStreamingTest.mod"] = b"SMODUSB"
    if include_arrivederci:
        members["Arrivederci.mod"] = b"SMODARRI"
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name, data in members.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o600
            tf.addfile(ti, io.BytesIO(data))
    tar_bytes = buf.getvalue()
    tar_sha = sha256_bytes(tar_bytes)
    (build_dir / "card" / "MLV2.TAR").write_bytes(tar_bytes)

    hook_bytes = b"\x7fELF\x01\x01\x01" + b"\x00" * 57 + b"HOOKSO"
    read_bytes = b"\x7fELF\x01\x01\x01" + b"\x00" * 57 + b"CARDREAD"
    sh_bytes = f"#!/bin/sh\nset -- {tar_sha}\necho ok\n".encode("ascii")

    (build_dir / "companion" / HOOK_NAME).write_bytes(hook_bytes)
    (build_dir / "companion" / READER_NAME).write_bytes(read_bytes)
    (build_dir / "companion" / SCRIPT_NAME).write_bytes(sh_bytes)
    return {"tar_bytes": tar_bytes, "tar_sha": tar_sha}


def _build_exfat_image(
    files: list[tuple[str, bytes, bool]],
    *,
    spc_shift: int = 3,  # 8 sectors = 4096-byte clusters
    fragmented_first_file: bool = False,
) -> bytes:
    """Construct a synthetic exFAT volume image for testing card_read.c."""
    sector = 512
    spc = 1 << spc_shift
    clu_size = sector * spc
    total_clusters = 64
    fat_off = 32
    fat_len = 4
    heap_off = 64
    vol_sectors = heap_off + total_clusters * spc
    img = bytearray(vol_sectors * sector)

    # VBR at sector 0
    img[0:3] = b"\xeb\x76\x90"
    img[3:11] = b"EXFAT   "
    struct.pack_into("<Q", img, 72, vol_sectors)
    struct.pack_into("<I", img, 80, fat_off)
    struct.pack_into("<I", img, 84, fat_len)
    struct.pack_into("<I", img, 88, heap_off)
    struct.pack_into("<I", img, 92, total_clusters)
    struct.pack_into("<I", img, 96, 2)  # root_cluster = 2
    img[108] = 9  # 2^9 = 512 B/sector
    img[109] = spc_shift
    img[110] = 1  # 1 FAT
    struct.pack_into("<H", img, 510, 0xAA55)

    def set_fat(clu: int, val: int) -> None:
        struct.pack_into("<I", img, fat_off * sector + clu * 4, val)

    def clu_offset(clu: int) -> int:
        return (heap_off + (clu - 2) * spc) * sector

    set_fat(0, 0xFFFFFFF8)
    set_fat(1, 0xFFFFFFFF)
    set_fat(2, 0xFFFFFFFF)  # single-cluster root dir

    dir_off = clu_offset(2)
    next_free_clu = 3

    for idx, (name, data, is_dir) in enumerate(files):
        if idx == 0 and fragmented_first_file:
            # Place chunks across non-contiguous clusters 6 -> 4 -> 9 -> 3 -> 11!
            chain = [6, 4, 9, 3, 11]
            assert len(data) <= len(chain) * clu_size
            for i, c in enumerate(chain):
                chunk = data[i * clu_size : (i + 1) * clu_size]
                img[clu_offset(c) : clu_offset(c) + len(chunk)] = chunk
                set_fat(c, chain[i + 1] if i + 1 < len(chain) else 0xFFFFFFFF)
            first_clu = chain[0]
            no_fat_chain = False
            next_free_clu = 12
        else:
            needed = max(1, (len(data) + clu_size - 1) // clu_size)
            first_clu = next_free_clu
            next_free_clu += needed
            img[clu_offset(first_clu) : clu_offset(first_clu) + len(data)] = data
            no_fat_chain = True

        name_u16 = name.encode("utf-16le")
        name_Summary_chunks = (len(name) + 14) // 15
        sec_count = 1 + name_Summary_chunks

        # 0x85 File Directory Entry
        e85 = bytearray(32)
        e85[0] = 0x85
        e85[1] = sec_count
        struct.pack_into("<H", e85, 4, 0x10 if is_dir else 0x20)
        img[dir_off : dir_off + 32] = e85
        dir_off += 32

        # 0xC0 Stream Extension Directory Entry
        ec0 = bytearray(32)
        ec0[0] = 0xC0
        ec0[1] = 0x03 if no_fat_chain else 0x01
        ec0[3] = len(name)
        struct.pack_into("<Q", ec0, 8, len(data))
        struct.pack_into("<I", ec0, 20, first_clu)
        struct.pack_into("<Q", ec0, 24, len(data))
        img[dir_off : dir_off + 32] = ec0
        dir_off += 32

        # 0xC1 File Name Directory Entries
        for chunk_i in range(name_Summary_chunks):
            ec1 = bytearray(32)
            ec1[0] = 0xC1
            part = name_u16[chunk_i * 30 : (chunk_i + 1) * 30]
            ec1[2 : 2 + len(part)] = part
            img[dir_off : dir_off + 32] = ec1
            dir_off += 32

    return bytes(img)


def _build_fat32_image(
    files: list[tuple[str, bytes]],
    *,
    use_tot16: bool = False,
) -> bytes:
    """Construct a synthetic FAT32 volume image (8.3 + LFN support) in pure Python."""
    sector = 512
    spc = 2
    clu_size = sector * spc
    rsvd = 32
    nfats = 2
    fatsz32 = 32
    total_sectors = 16384 if use_tot16 else 131072
    data_lba = rsvd + nfats * fatsz32
    img = bytearray(total_sectors * sector)

    img[0:3] = b"\xeb\x58\x90"
    img[3:11] = b"MSDOS5.0"
    struct.pack_into("<H", img, 11, sector)
    img[13] = spc
    struct.pack_into("<H", img, 14, rsvd)
    img[16] = nfats
    struct.pack_into("<H", img, 17, 0)
    struct.pack_into("<H", img, 19, total_sectors if use_tot16 else 0)
    img[21] = 0xF8
    struct.pack_into("<H", img, 22, 0)
    struct.pack_into("<I", img, 32, 0 if use_tot16 else total_sectors)
    struct.pack_into("<I", img, 36, fatsz32)
    struct.pack_into("<I", img, 44, 2)  # root cluster = 2
    struct.pack_into("<H", img, 510, 0xAA55)

    def set_fat(clu: int, val: int) -> None:
        for f_idx in range(nfats):
            struct.pack_into("<I", img, (rsvd + f_idx * fatsz32) * sector + clu * 4, val)

    def clu_offset(clu: int) -> int:
        return (data_lba + (clu - 2) * spc) * sector

    set_fat(0, 0x0FFFFFF8)
    set_fat(1, 0x0FFFFFFF)
    set_fat(2, 0x0FFFFFFF)

    dir_off = clu_offset(2)
    next_clu = 3

    for idx, (name, data) in enumerate(files):
        needed = max(1, (len(data) + clu_size - 1) // clu_size)
        first_clu = next_clu
        for i in range(needed):
            c = first_clu + i
            chunk = data[i * clu_size : (i + 1) * clu_size]
            img[clu_offset(c) : clu_offset(c) + len(chunk)] = chunk
            set_fat(c, (c + 1) if i + 1 < needed else 0x0FFFFFFF)
        next_clu += needed

        # Build short 8.3 name + LFN entry if needed
        stem, _, ext = name.partition(".")
        if len(stem) <= 8 and len(ext) <= 3 and name == name.upper() and "." in name:
            short11 = stem.ljust(8, " ").encode("ascii") + ext.ljust(3, " ").encode("ascii")
            need_lfn = False
        else:
            short11 = f"FIL{idx:03d}~1".encode("ascii") + (ext[:3].upper().ljust(3, " ")).encode("ascii")
            need_lfn = True

        chk = 0
        for b in short11:
            chk = (((chk & 1) << 7) + (chk >> 1) + b) & 0xFF

        if need_lfn:
            padded = name.encode("utf-16le") + b"\x00\x00"
            while len(padded) % 26 != 0:
                padded += b"\xff\xff"
            n_lfn = len(padded) // 26
            offsets = [1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30]
            for order in range(n_lfn, 0, -1):
                lfn = bytearray(32)
                lfn[0] = order | (0x40 if order == n_lfn else 0)
                lfn[11] = 0x0F
                lfn[13] = chk
                seg = padded[(order - 1) * 26 : order * 26]
                for ch_i in range(13):
                    lfn[offsets[ch_i] : offsets[ch_i] + 2] = seg[ch_i * 2 : ch_i * 2 + 2]
                img[dir_off : dir_off + 32] = lfn
                dir_off += 32

        sfn = bytearray(32)
        sfn[0:11] = short11
        sfn[11] = 0x20
        struct.pack_into("<H", sfn, 20, (first_clu >> 16) & 0xFFFF)
        struct.pack_into("<H", sfn, 26, first_clu & 0xFFFF)
        struct.pack_into("<I", sfn, 28, len(data))
        img[dir_off : dir_off + 32] = sfn
        dir_off += 32

    return bytes(img)


class TestOptionBSdInstallerOffline(unittest.TestCase):
    def test_validate_sd_build_prep_card_and_closed_default_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            good_build = tmp_path / "sd-good"
            meta = _make_valid_sd_build(good_build)
            info = validate_sd_build(good_build)
            self.assertEqual(info["tar_sha256"], meta["tar_sha"])
            self.assertEqual(info["shim_addr"], "0x5d76800")

            # Reject wrong target cave (ILCE-6400 0x05662800)
            wrong_cave = tmp_path / "sd-wrong-cave"
            _make_valid_sd_build(wrong_cave, shim_addr=0x05662800)
            with self.assertRaises(ValueError):
                validate_sd_build(wrong_cave)

            # Reject UsbStreamingTest.mod bundled in physical MLV2.TAR
            usb_build = tmp_path / "sd-with-usb"
            _make_valid_sd_build(usb_build, include_usb_mod=True)
            with self.assertRaises(ValueError):
                validate_sd_build(usb_build)

            # Reject Arrivederci.mod bundled in default physical MLV2.TAR (unless allow_arrivederci=True)
            arri_build = tmp_path / "sd-with-arrivederci"
            _make_valid_sd_build(arri_build, include_arrivederci=True)
            with self.assertRaises(ValueError):
                validate_sd_build(arri_build)
            arri_info = validate_sd_build(arri_build, allow_arrivederci=True)
            self.assertIn("Arrivederci.mod", arri_info["tar_members"])

            # Reject tampered MLV2.TAR digest mismatch
            tampered = tmp_path / "sd-tampered"
            _make_valid_sd_build(tampered)
            (tampered / "card" / "MLV2.TAR").write_bytes(b"TAMPERED")
            with self.assertRaises(ValueError):
                validate_sd_build(tampered)

            # Test prep_sd_card preserving DCIM and handling disable markers
            fake_sd = tmp_path / "fake_sd_card"
            (fake_sd / "DCIM" / "100MSDCF").mkdir(parents=True)
            (fake_sd / "DCIM" / "100MSDCF" / "DSC00001.ARW").write_bytes(b"RAW_PHOTO")
            (fake_sd / "MLV2.OFF").write_bytes(b"")

            # Windows C: system-drive guard must block when os.name == 'nt' on SystemDrive
            if os.name == "nt" and fake_sd.drive.upper() == os.environ.get("SystemDrive", "C:").upper():
                with self.assertRaises(ValueError):
                    prep_sd_card(fake_sd, info)

            with mock.patch("tools.research.a6400a_sd_boot_install.os.name", "posix"):
                res1 = prep_sd_card(fake_sd, info, clear_disable_markers=False)
                self.assertEqual(res1["remaining_disable_markers"], ["MLV2.OFF"])
                self.assertEqual((fake_sd / "MLV2.TAR").read_bytes(), meta["tar_bytes"])
                self.assertEqual((fake_sd / "DCIM" / "100MSDCF" / "DSC00001.ARW").read_bytes(), b"RAW_PHOTO")

                res2 = prep_sd_card(fake_sd, info, clear_disable_markers=True)
                self.assertEqual(res2["removed_disable_markers"], ["MLV2.OFF"])
                self.assertEqual(res2["remaining_disable_markers"], [])
                self.assertFalse((fake_sd / "MLV2.OFF").exists())

            # Camera entry modes must fail closed (exit 2) without opening USB
            self.assertEqual(installer_main(["--mode", "install", "--build", str(good_build)]), 2)
            self.assertEqual(installer_main(["--mode", "uninstall"]), 2)
            self.assertEqual(installer_main(["--mode", "full-install", "--build", str(good_build)]), 2)
            self.assertEqual(installer_main(["--mode", "full-uninstall"]), 2)

            # Dry-run under artifacts/tests/ must succeed offline
            dry_out = PROJECT_ROOT / "artifacts" / "tests" / f"sd-dry-run-{os.getpid()}"
            shutil.rmtree(dry_out, ignore_errors=True)
            try:
                rc = installer_main(["--mode", "dry-run", "--build", str(good_build), "--output-dir", str(dry_out)])
                self.assertEqual(rc, 0)
                manifest = json.loads((dry_out / "dry-run-manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["status"], "dry_run_verified")
                self.assertFalse(manifest["usb_contacted"])
            finally:
                shutil.rmtree(dry_out, ignore_errors=True)

    def test_package_mlv2_sd_excludes_arrivederci_and_usb_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for name, extra_args in (("default", []), ("opt-in", ["--include-arrivederci"])):
                bdir = tmp_path / name
                (bdir / "companion").mkdir(parents=True)
                (bdir / "card").mkdir(parents=True)
                (bdir / "build.json").write_text(
                    json.dumps({"shim": {"addr": "0x5d76800"}, "core": {"sha256": "0" * 64}}),
                    encoding="utf-8",
                )
                (bdir / "mlv2.req").write_bytes(b"ML2QREQ")
                (bdir / "core.bin").write_bytes(b"MLCRCORE")
                (bdir / "companion" / "mlv2_guard_compact.elf").write_bytes(b"\x7fELF" + b"\x00" * 60)
                (bdir / "companion" / HOOK_NAME).write_bytes(b"\x7fELF" + b"\x01" * 60)
                (bdir / "companion" / READER_NAME).write_bytes(b"\x7fELF" + b"\x02" * 60)
                package_mlv2_sd.main(["--build", str(bdir), "--target", "ilce_6400a", *extra_args])

            default_info = validate_sd_build(tmp_path / "default")
            self.assertIn("MenuTouchFull.mod", default_info["tar_members"])
            self.assertIn("ModsTabLook.mod", default_info["tar_members"])
            self.assertNotIn("UsbStreamingTest.mod", default_info["tar_members"])
            self.assertNotIn("Arrivederci.mod", default_info["tar_members"])

            with self.assertRaises(ValueError):
                validate_sd_build(tmp_path / "opt-in")
            opt_in_info = validate_sd_build(tmp_path / "opt-in", allow_arrivederci=True)
            self.assertIn("Arrivederci.mod", opt_in_info["tar_members"])
            self.assertNotIn("UsbStreamingTest.mod", opt_in_info["tar_members"])


class TestCardReadAndWorkerScripts(unittest.TestCase):
    def _run_linux_script(self, py_or_sh: str) -> str:
        if os.name != "nt":
            if shutil.which("gcc") is None:
                self.skipTest("gcc not installed")
            return subprocess.check_output(["bash", "-c", py_or_sh], cwd=PROJECT_ROOT, text=True)
        if shutil.which("wsl.exe") is None:
            self.skipTest("wsl.exe not available on Windows host")
        probe = subprocess.run(
            ["wsl.exe", "-d", "REA-Ubuntu-24.04", "--exec", "bash", "-c", "command -v gcc >/dev/null"],
            capture_output=True,
        )
        if probe.returncode != 0:
            self.skipTest("REA-Ubuntu-24.04 gcc not available")
        proc = subprocess.run(
            ["wsl.exe", "-d", "REA-Ubuntu-24.04", "--exec", "bash", "-c", "tr -d '\\r' | bash"],
            input=py_or_sh,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            self.fail(f"Linux script failed ({proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        return proc.stdout

    def test_card_read_fat32_exfat_fragmented_and_kill_switches(self):
        payload = b"TAR_HEADER_" + bytes(range(256)) * 60 + b"_TAR_END"
        fat32_tot16 = _build_fat32_image([("MLV2.TAR", payload)], use_tot16=True)
        fat32_lfn_kill = _build_fat32_image([("MLV2.TAR", payload), ("disable_hook", b"1")], use_tot16=False)
        exfat_contig = _build_exfat_image([("MLV2.TAR", payload, False)])
        exfat_frag = _build_exfat_image([("mlv2.tar", payload, False)], fragmented_first_file=True)
        exfat_off = _build_exfat_image([("MLV2.TAR", payload, False), ("MLV2.OFF", b"", False)])
        exfat_oversize = _build_exfat_image([("MLV2.TAR", b"X" * (4 * 1024 * 1024 + 1), False)], spc_shift=8)

        fixture_dir = PROJECT_ROOT / "artifacts" / "tests" / f"card-read-fix-{os.getpid()}"
        shutil.rmtree(fixture_dir, ignore_errors=True)
        fixture_dir.mkdir(parents=True)
        try:
            (fixture_dir / "expected.bin").write_bytes(payload)
            (fixture_dir / "fat32_tot16.img").write_bytes(fat32_tot16)
            (fixture_dir / "fat32_kill.img").write_bytes(fat32_lfn_kill)
            (fixture_dir / "exfat_contig.img").write_bytes(exfat_contig)
            (fixture_dir / "exfat_frag.img").write_bytes(exfat_frag)
            (fixture_dir / "exfat_off.img").write_bytes(exfat_off)
            (fixture_dir / "exfat_oversize.img").write_bytes(exfat_oversize)

            rel_fix = fixture_dir.relative_to(PROJECT_ROOT).as_posix()
            out = self._run_linux_script(
                f"""set -eu
gcc -DMLV2_HOST_TEST -O2 -Wall -Wextra -Werror tools/native_camera/mlv2/card_read.c -o "{rel_fix}/card_read_host"
for img in fat32_tot16.img exfat_contig.img exfat_frag.img; do
    rm -f "{rel_fix}/out.tar"
    MLV2_CARD_READ_IMAGE="{rel_fix}/$img" "{rel_fix}/card_read_host" --present
    MLV2_CARD_READ_IMAGE="{rel_fix}/$img" MLV2_CARD_READ_OUT="{rel_fix}/out.tar" "{rel_fix}/card_read_host"
    cmp -s "{rel_fix}/expected.bin" "{rel_fix}/out.tar"
done
for bad in fat32_kill.img exfat_off.img exfat_oversize.img; do
    if MLV2_CARD_READ_IMAGE="{rel_fix}/$bad" "{rel_fix}/card_read_host" --present; then
        echo "Expected rejection for $bad" >&2
        exit 1
    fi
done
echo "CARD_READ_ALL_PASS"
"""
            )
            self.assertIn("CARD_READ_ALL_PASS", out)
        finally:
            shutil.rmtree(fixture_dir, ignore_errors=True)

    def test_worker_install_and_uninstall_scripts_preserve_and_restore_preload(self):
        fixture_dir = PROJECT_ROOT / "artifacts" / "tests" / f"sdboot-sh-{os.getpid()}"
        shutil.rmtree(fixture_dir, ignore_errors=True)
        fixture_dir.mkdir(parents=True)
        try:
            _make_valid_sd_build(fixture_dir / "build")
            info = validate_sd_build(fixture_dir / "build")
            nonce = "a" * 32
            (fixture_dir / "install.sh").write_bytes(
                build_worker_script(mode="install", nonce=nonce, companions=info["companions"])
            )
            (fixture_dir / "uninstall.sh").write_bytes(build_worker_script(mode="uninstall", nonce=nonce))
            rel = fixture_dir.relative_to(PROJECT_ROOT).as_posix()

            out = self._run_linux_script(
                f"""set -eu
mkdir -p "{rel}/setting" "{rel}/etc" "{rel}/stage"
: > "{rel}/setting/Backup.bin"
: > "{rel}/etc/toprc"
printf '/setting/other_helper.so\\n' > "{rel}/etc/ld.so.preload"
cp "{rel}/build/companion/{HOOK_NAME}" "{rel}/stage/sdboot_{nonce}_hook"
cp "{rel}/build/companion/{SCRIPT_NAME}" "{rel}/stage/sdboot_{nonce}_sh"
cp "{rel}/build/companion/{READER_NAME}" "{rel}/stage/sdboot_{nonce}_read"

MLV2_TEST_SKIP_MOUNT_CHECK=1 \\
MLV2_TEST_OUT_LOG="$PWD/{rel}/out1.log" \\
MLV2_TEST_SETTING_ROOT="$PWD/{rel}/setting" \\
MLV2_TEST_ETC_ROOT="$PWD/{rel}/etc" \\
MLV2_TEST_STAGE_DIR="$PWD/{rel}/stage" \\
MLV2_TEST_DONE_FILE="$PWD/{rel}/done_install" \\
sh "{rel}/install.sh"

grep -qx "__A6400A_SDBOOT_DONE__:{nonce}:INSTALLED" "{rel}/done_install"
grep -qx "/setting/other_helper.so" "{rel}/etc/ld.so.preload"
grep -qx "{PRELOAD_ENTRY}" "{rel}/etc/ld.so.preload"
cmp -s "{rel}/build/companion/{HOOK_NAME}" "{rel}/setting/{HOOK_NAME}"
cmp -s "{rel}/build/companion/{SCRIPT_NAME}" "{rel}/setting/{SCRIPT_NAME}"
cmp -s "{rel}/build/companion/{READER_NAME}" "{rel}/setting/{READER_NAME}"

MLV2_TEST_SKIP_MOUNT_CHECK=1 \\
MLV2_TEST_OUT_LOG="$PWD/{rel}/out2.log" \\
MLV2_TEST_SETTING_ROOT="$PWD/{rel}/setting" \\
MLV2_TEST_ETC_ROOT="$PWD/{rel}/etc" \\
MLV2_TEST_STAGE_DIR="$PWD/{rel}/stage" \\
MLV2_TEST_DONE_FILE="$PWD/{rel}/done_uninstall" \\
sh "{rel}/uninstall.sh"

grep -qx "__A6400A_SDBOOT_DONE__:{nonce}:UNINSTALLED" "{rel}/done_uninstall"
[ ! -e "{rel}/setting/{HOOK_NAME}" ]
[ ! -e "{rel}/setting/{SCRIPT_NAME}" ]
[ ! -e "{rel}/setting/{READER_NAME}" ]
[ "$(cat "{rel}/etc/ld.so.preload")" = "/setting/other_helper.so" ]

# Also verify uninstall deletes /etc/ld.so.preload completely when mlv2_sd_boot_hook.so was the sole entry!
rm -f "{rel}/etc/ld.so.preload" "{rel}/etc/ld.so.preload.stock_bak"
MLV2_TEST_SKIP_MOUNT_CHECK=1 \\
MLV2_TEST_OUT_LOG="$PWD/{rel}/out3.log" \\
MLV2_TEST_SETTING_ROOT="$PWD/{rel}/setting" \\
MLV2_TEST_ETC_ROOT="$PWD/{rel}/etc" \\
MLV2_TEST_STAGE_DIR="$PWD/{rel}/stage" \\
MLV2_TEST_DONE_FILE="$PWD/{rel}/done_install2" \\
sh "{rel}/install.sh"
MLV2_TEST_SKIP_MOUNT_CHECK=1 \\
MLV2_TEST_OUT_LOG="$PWD/{rel}/out4.log" \\
MLV2_TEST_SETTING_ROOT="$PWD/{rel}/setting" \\
MLV2_TEST_ETC_ROOT="$PWD/{rel}/etc" \\
MLV2_TEST_STAGE_DIR="$PWD/{rel}/stage" \\
MLV2_TEST_DONE_FILE="$PWD/{rel}/done_uninstall2" \\
sh "{rel}/uninstall.sh"
[ ! -e "{rel}/etc/ld.so.preload" ]
echo "WORKER_SCRIPTS_ALL_PASS"
"""
            )
            self.assertIn("WORKER_SCRIPTS_ALL_PASS", out)
        finally:
            shutil.rmtree(fixture_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
