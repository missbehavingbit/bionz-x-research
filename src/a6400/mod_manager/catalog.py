"""Catalog of built-in runtime SMOD mods, custom .mod imports, and permanent PMCA tweaks."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .smod import SmodError, parse_smod, validate_mod_filename
from .tools_bundle import (
    ARRIVEDERCI_V5_ASSET,
    MLV2_RELEASE_TAG,
    bundle_root,
    find_arrivederci_v5,
    repo_root,
    user_data_dir,
)

MAX_ACTIVE_RUNTIME_MODS = 12


@dataclass(frozen=True)
class BuiltinModSpec:
    id: str
    title: str
    filename: str
    summary: str
    details: str
    status: str
    target_note: str
    candidate_paths: tuple[str, ...]
    artifact_globs: tuple[str, ...] = ()
    exclusive_group: str | None = None
    requires_ack: bool = False
    warning: str | None = None
    unavailable_reason: str | None = None
    default_enabled: bool = False


BUILTIN_RUNTIME_MODS: tuple[BuiltinModSpec, ...] = (
    BuiltinModSpec(
        id="menu_touch_full",
        title="Menu Touch Full (MTF2)",
        filename="MenuTouchFull.mod",
        summary="Full touch navigation across Sony's menu tabs, page dots, popups, and swipe paging.",
        details=(
            "Adds tab taps, page-dot taps, horizontal swipe paging, 1st-tap row select / "
            "2nd-tap popup open, popup option taps, vertical swipe scrolling, and outside-tap close."
        ),
        status="verified",
        target_note="ILCE-6400 (FW 2.00)",
        candidate_paths=(
            "bundled_mods/MenuTouchFull.mod",
            "mods/menu_touch_full/MenuTouchFull.mod",
        ),
        artifact_globs=("artifacts/tests/mtf-v2-build-*/MenuTouchFull.mod",),
        default_enabled=True,
    ),
    BuiltinModSpec(
        id="arrivederci",
        title="Arrivederci (ARRI Classic Look)",
        filename="Arrivederci.mod",
        summary="ARRI LogC3 Classic tone curve (EC record 31) and fitted Q10 color matrix for S-Log3 / S-Gamut3.Cine.",
        details=(
            "Replaces S-Log3 tone curve and Movie S-Gamut3.Cine matrix (entry B8) when toggled ON in the "
            "camera Mods tab. Applies on menu exit under Mod Loader v2."
        ),
        status="usable",
        target_note="ILCE-6400 (FW 2.00) · Movie PP S-Log3",
        candidate_paths=(
            "bundled_mods/Arrivederci.mod",
            "mods/arri_isp/arrivederci/Arrivederci.mod",
        ),
        artifact_globs=(
            "artifacts/tests/arrivederci-v3-build-*/Arrivederci.mod",
            "artifacts/tests/arrivederci-v2-build-*/Arrivederci.mod",
        ),
        requires_ack=True,
        warning=(
            "Turn Arrivederci OFF in camera MENU -> Mods before power-off "
            "(color matrix persists in Backup.bin)."
        ),
    ),
    BuiltinModSpec(
        id="mods_tab_look",
        title="Mods Tab Icon (ModsTabLook)",
        filename="ModsTabLook.mod",
        summary="Renders the custom MODS tab icon artwork instead of reusing the 6th tab star icon.",
        details=(
            "Relocatable SMOD v2 module with AUTO_ON flag. Automatically enables once listed by Mod Loader v2."
        ),
        status="usable",
        target_note="ILCE-6400 (FW 2.00)",
        candidate_paths=("bundled_mods/ModsTabLook.mod",),
        artifact_globs=("artifacts/tests/mtl-v2-build-*/ModsTabLook.mod",),
        unavailable_reason=(
            "ModsTabLook.mod is not built on disk yet (build via WSL: "
            "mods/mods_tab_look/build_mtl_v2_wsl.sh)."
        ),
    ),
    BuiltinModSpec(
        id="open_gate_sensor",
        title="Open Gate 3:2 (Sensor Stage)",
        filename="OpenGateSensor.mod",
        summary="Experimental 5400x3600 3:2 sensor readout on 4K 24p/25p while keeping the stock 3840x2160 encoder.",
        details=(
            "Patches sensor mode 16/17 records and 24p/25p timings only, leaving libmpr and DMM stock. "
            "Verifies sensor lock before testing the full 3240x2160 canvas build."
        ),
        status="experimental",
        target_note="ILCE-6400 (FW 2.00) · Offline-Verified Only",
        candidate_paths=(
            "bundled_mods/OpenGateSensor.mod",
            "mods/open_gate/OpenGateSensor.mod",
        ),
        exclusive_group="open_gate",
        requires_ack=True,
        warning=(
            "Experimental, offline-verified only. Retargets 4K 24p/25p readout to 5400x3600 3:2 "
            "while keeping stock 3840x2160 encoder canvas. Test this stage before Full."
        ),
    ),
    BuiltinModSpec(
        id="open_gate_32",
        title="Open Gate 3:2 (Full 3240x2160 Canvas)",
        filename="OpenGate32.mod",
        summary="Experimental 5400x3600 3:2 sensor readout plus libmpr/DMM 4K canvas retargeted to 3240x2160.",
        details=(
            "Full SMOD v2 build patching sensor timings plus libmpr resolution record 15 and three 4K DMM "
            "entries. Restores exact stock words on OFF."
        ),
        status="experimental",
        target_note="ILCE-6400 (FW 2.00) · Offline-Verified Only",
        candidate_paths=(
            "bundled_mods/OpenGate32.mod",
            "mods/open_gate/OpenGate32.mod",
        ),
        exclusive_group="open_gate",
        requires_ack=True,
        warning=(
            "Experimental, offline-verified only. Retargets sensor to 5400x3600 3:2 plus libmpr/DMM "
            "4K canvas to 3240x2160. Do not use for unrepeatable work; verify Sensor Stage first."
        ),
    ),
)

SUPPORTED_CATALOG_TARGETS: tuple[str, ...] = ("ilce_6400", "ilce_6400a")

BUILTIN_RUNTIME_MODS_ILCE6400A: tuple[BuiltinModSpec, ...] = (
    BuiltinModSpec(
        id="menu_touch_full",
        title="Menu Touch Full (MTF2)",
        filename="MenuTouchFull.mod",
        summary="Full touch navigation across Sony's menu tabs, page dots, popups, and swipe paging.",
        details=(
            "Relocatable SMOD v2 port for ILCE-6400A (FW 1.01): tab taps, page-dot taps, horizontal swipe "
            "paging, 1st-tap row select / 2nd-tap popup open, popup option taps, vertical swipe scrolling, "
            "and outside-tap close."
        ),
        status="usable",
        target_note="ILCE-6400A (FW 1.01) · QEMU/Unicorn Verified",
        candidate_paths=(
            "bundled_mods/ilce_6400a/MenuTouchFull.mod",
            "mods/ilce_6400a/MenuTouchFull.mod",
        ),
        artifact_globs=("artifacts/tests/mods-ilce6400a-*/mtf/MenuTouchFull.mod",),
        default_enabled=True,
    ),
    BuiltinModSpec(
        id="arrivederci",
        title="Arrivederci v5 (ARRI Classic Look)",
        filename="Arrivederci.mod",
        summary="ARRI LogC3 Classic tone curve, Q10 matrix, SABIN filters, and 3D LUT for ILCE-6400A S-Log3.",
        details=(
            "Port of Arrivederci v5 to ILCE-6400A (FW 1.01): swaps S-Log3 EC gamma (0x03e560c4), B8 "
            "S-Gamut3.Cine matrix (0x0444fdc8), SABIN 4K tap tables, and 3D LUT (0x044b191b / 0x0444ed96)."
        ),
        status="usable",
        target_note="ILCE-6400A (FW 1.01) · Movie PP S-Log3",
        candidate_paths=(
            "bundled_mods/ilce_6400a/Arrivederci.mod",
            "mods/ilce_6400a/Arrivederci.mod",
        ),
        artifact_globs=("artifacts/tests/mods-ilce6400a-*/arrivederci/Arrivederci.mod",),
        requires_ack=True,
        warning=(
            "Turn Arrivederci OFF in camera MENU -> Mods before power-off "
            "(color matrix persists in Backup.bin)."
        ),
    ),
    BuiltinModSpec(
        id="mods_tab_look",
        title="Mods Tab Icon (ModsTabLook)",
        filename="ModsTabLook.mod",
        summary="Renders the custom MODS tab icon artwork instead of reusing the 6th tab star icon.",
        details=(
            "Relocatable SMOD v2 module with AUTO_ON flag targeting ILCE-6400A libObj.so (+0x7e662e). "
            "Automatically enables once listed by Mod Loader v2."
        ),
        status="usable",
        target_note="ILCE-6400A (FW 1.01) · QEMU/Unicorn Verified",
        candidate_paths=(
            "bundled_mods/ilce_6400a/ModsTabLook.mod",
            "mods/ilce_6400a/ModsTabLook.mod",
        ),
        artifact_globs=("artifacts/tests/mods-ilce6400a-*/mtl/ModsTabLook.mod",),
    ),
    BuiltinModSpec(
        id="open_gate_sensor",
        title="Open Gate 3:2 (Sensor Stage)",
        filename="OpenGateSensor.mod",
        summary="Experimental 5400x3600 3:2 sensor readout on 4K 24p/25p while keeping the stock 3840x2160 encoder.",
        details=(
            "Patches ILCE-6400A 1.01 sensor mode 16/17 records (0x03e27de0/0x03e27e2c) and 24p/25p timings "
            "(0x03e2833c/0x03e28504) only, leaving libmpr and DMM stock."
        ),
        status="experimental",
        target_note="ILCE-6400A (FW 1.01) · Offline/QEMU Verified",
        candidate_paths=(
            "bundled_mods/ilce_6400a/OpenGateSensor.mod",
            "mods/ilce_6400a/OpenGateSensor.mod",
        ),
        exclusive_group="open_gate",
        requires_ack=True,
        warning=(
            "Experimental, emulator-verified only. Retargets 4K 24p/25p readout to 5400x3600 3:2 "
            "while keeping stock 3840x2160 encoder canvas. Test this stage before Full."
        ),
    ),
    BuiltinModSpec(
        id="open_gate_32",
        title="Open Gate 3:2 (Full 3240x2160 Canvas)",
        filename="OpenGate32.mod",
        summary="Experimental 5400x3600 3:2 sensor readout plus libmpr/DMM/VPI/VENC 4K canvas retargeted to 3240x2160.",
        details=(
            "Full SMOD v2 port for ILCE-6400A 1.01 patching sensor timings, DMM/VPI/VENC 4K tables, "
            "and runtime-located libmpr.so 4K resolution records. Restores exact stock words on OFF."
        ),
        status="experimental",
        target_note="ILCE-6400A (FW 1.01) · Offline/QEMU Verified",
        candidate_paths=(
            "bundled_mods/ilce_6400a/OpenGate32.mod",
            "mods/ilce_6400a/OpenGate32.mod",
        ),
        exclusive_group="open_gate",
        requires_ack=True,
        warning=(
            "Experimental, emulator-verified only. Retargets sensor to 5400x3600 3:2 plus libmpr/DMM "
            "4K canvas to 3240x2160. Verify Sensor Stage first."
        ),
    ),
    BuiltinModSpec(
        id="open_gate_5400_test",
        title="Open Gate 5400 Test (LIRO Only)",
        filename="OpenGate_5400_Test.mod",
        summary="Experimental 5400x3600 sensor + DMM/VPI/VENC LIRO RAM test leaving libmpr.so untouched.",
        details="Applies the 27 u32 + 14 u16 av-cam.bin 1.01 OpenGate swaps in LIRO RAM with m=skip.",
        status="experimental",
        target_note="ILCE-6400A (FW 1.01) · Diagnostic",
        candidate_paths=(
            "bundled_mods/ilce_6400a/OpenGate_5400_Test.mod",
            "mods/ilce_6400a/OpenGate_5400_Test.mod",
        ),
        exclusive_group="open_gate",
        requires_ack=True,
        warning="Diagnostic LIRO-only OpenGate test; mutually exclusive with other OpenGate stages.",
    ),
    BuiltinModSpec(
        id="rcraw_enable_test",
        title="RCRAW Enable Test",
        filename="RCRAW_Enable_Test.mod",
        summary="Guarded >8-bit RCRAW global enable toggle (0x043f2b04) and AIF0 W1/W2 diagnostic logger.",
        details=(
            "Swaps RCRAW_EN (0x043f2b04) 0 <-> 1 on ILCE-6400A 1.01 and logs RDRAW (0x043f2ec0), "
            "D1534 (0x043f2828), AIF0 W1/W2, and 0xF30317C8 to /tmp_bt/mods/rcraw_enable_test.log."
        ),
        status="experimental",
        target_note="ILCE-6400A (FW 1.01) · Diagnostic",
        candidate_paths=(
            "bundled_mods/ilce_6400a/RCRAW_Enable_Test.mod",
            "mods/ilce_6400a/RCRAW_Enable_Test.mod",
        ),
        requires_ack=True,
        warning="Diagnostic >8-bit RCRAW gate probe; logs state transitions under /tmp_bt/mods/.",
    ),
    BuiltinModSpec(
        id="rcraw_w2_probe",
        title="RCRAW W2 Read-Only Probe",
        filename="RCRAW_W2_Probe.mod",
        summary="Strictly read-only AIF0/AIF1 W1/W2 and SET_RAW_EXTERNAL_OUTPUT (0x044295bc) snapshot.",
        details="Opens /dev/mem O_RDONLY and records AIF0/AIF1 DMA channel registers plus RAW_EXT state.",
        status="usable",
        target_note="ILCE-6400A (FW 1.01) · Read-Only Diagnostic",
        candidate_paths=(
            "bundled_mods/ilce_6400a/RCRAW_W2_Probe.mod",
            "mods/ilce_6400a/RCRAW_W2_Probe.mod",
        ),
    ),
    BuiltinModSpec(
        id="photon_bin_6mp",
        title="PhotonBin 6MP (Low-Light RAW Convergence)",
        filename="PhotonBin6MP.mod",
        summary=(
            "Pre-demosaic 4x4->2x2 Bayer / 2x2 Mono RAW photon convergence into 6 MP .ARW "
            "plus IMX284 Mconv HCG and 14-bit 1H2H analog overrides via nested menu."
        ),
        details=(
            "Combines Option 3 IMX284 floating-diffusion conversion gain (Force HCG +9.8 dB / Auto / LCG) "
            "and Bulb/Silent 14-bit 1H2H readout overrides in av-cam.bin with Option 1 pre-demosaic "
            "4-photosite convergence (3008x2008 / 3000x2000 active) into 16-bit Uncompressed, 14-bit "
            "Uncompressed, or 14-bit Sony cRAW .ARW files. Configurable live via a 6-item nested submenu."
        ),
        status="experimental",
        target_note="ILCE-6400A (FW 1.01) · QEMU/Unicorn Verified",
        candidate_paths=(
            "bundled_mods/ilce_6400a/PhotonBin6MP.mod",
            "mods/ilce_6400a/PhotonBin6MP.mod",
        ),
        artifact_globs=("artifacts/mods/ilce_6400a/photon_bin_6mp/PhotonBin6MP.mod",),
    ),
    BuiltinModSpec(
        id="usb_streaming_test",
        title="USB Streaming Test (QEMU Action)",
        filename="UsbStreamingTest.mod",
        summary="QEMU-guarded command-item mod triggering UsbControl configure/start on ILCE-6400A 1.01.",
        details=(
            "Verifies ILCE-6400A 1.01 libUsbControl.so, libMWF.so, and libc-2.11.2.so FNV-1a and prologue "
            "bytes before dispatching UsbControl events. Refuses execution outside QEMU by default."
        ),
        status="experimental",
        target_note="ILCE-6400A (FW 1.01) · QEMU Only",
        candidate_paths=(
            "bundled_mods/ilce_6400a/UsbStreamingTest.mod",
            "mods/ilce_6400a/UsbStreamingTest.mod",
        ),
        requires_ack=True,
        warning="QEMU-only action item; refuses real-camera execution unless explicitly compiled with override.",
    ),
)

BUILTIN_MOD_FILENAMES: frozenset[str] = frozenset(
    spec.filename.lower() for spec in (*BUILTIN_RUNTIME_MODS, *BUILTIN_RUNTIME_MODS_ILCE6400A)
)


@dataclass(frozen=True)
class TweakSpec:
    id: str
    title: str
    summary: str
    details: str
    risk: str = "permanent_flash"


BUILTIN_PMCA_TWEAKS: tuple[TweakSpec, ...] = (
    TweakSpec(
        id="recLimit",
        title="Unlimited Video Recording",
        summary="Removes the 29m50s continuous movie recording cap.",
        details="Updates Sony backup property for video time limit via PMCA TweakInterface and saves to flash.",
    ),
    TweakSpec(
        id="recLimit4k",
        title="Unlimited 4K Recording",
        summary="Extends the 4K movie recording time cap beyond the factory limit.",
        details="Updates Sony backup property for 4K video time limit via PMCA TweakInterface and saves to flash.",
    ),
    TweakSpec(
        id="language",
        title="Unlock All 35 Languages",
        summary="Enables all 35 Sony menu languages on region-locked camera bodies.",
        details="Enables all language flags in Sony backup storage via PMCA TweakInterface.",
    ),
    TweakSpec(
        id="palNtscSelector",
        title="PAL / NTSC Selector",
        summary="Exposes the PAL / NTSC video standard selector in the Sony Setup menu.",
        details="Enables the PAL/NTSC selector backup property via PMCA TweakInterface.",
    ),
)


def custom_mods_dir(data_dir: Path) -> Path:
    """Return the writable directory storing user-imported `.mod` files."""
    path = Path(data_dir) / "custom_mods"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_mod_candidate(
    spec: BuiltinModSpec,
    base_root: Path,
    b_root: Path,
) -> Path | None:
    candidates: list[Path] = []
    for pattern in spec.artifact_globs:
        matches = sorted(base_root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        candidates.extend(matches)
    for rel in spec.candidate_paths:
        for search_root in (b_root, base_root):
            candidate = search_root / rel
            if candidate not in candidates:
                candidates.append(candidate)

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            parse_smod(candidate.read_bytes(), filename=spec.filename)
            return candidate.resolve()
        except SmodError:
            continue
    return None


def import_custom_mod_bytes(filename: str, data: bytes, data_dir: Path) -> dict[str, object]:
    """Validate and save a custom `.mod` binary into ``custom_mods/``."""
    safe_name = validate_mod_filename(filename)
    if safe_name.lower() in BUILTIN_MOD_FILENAMES:
        raise SmodError(f"Filename {safe_name!r} is reserved by a built-in mod")
    info = parse_smod(data, filename=safe_name)

    dest_dir = custom_mods_dir(data_dir)
    dest = dest_dir / safe_name
    tmp = dest_dir / f".{safe_name}.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return _custom_entry(dest, info)


def import_custom_mod(source_path: Path, data_dir: Path) -> dict[str, object]:
    """Import a `.mod` file from disk into ``custom_mods/`` after full validation."""
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Mod file not found: {source}")
    data = source.read_bytes()
    return import_custom_mod_bytes(source.name, data, data_dir)


def remove_custom_mod(mod_id: str, data_dir: Path) -> bool:
    """Delete a user-imported custom `.mod` file by its catalog ID."""
    if not mod_id.startswith("custom:"):
        raise ValueError("Only custom mods can be removed")
    filename = validate_mod_filename(mod_id.split(":", 1)[1])
    target = custom_mods_dir(data_dir) / filename
    if not target.is_file():
        return False
    target.unlink()
    return True


def _custom_entry(path: Path, info) -> dict[str, object]:
    return {
        "id": f"custom:{path.name}",
        "category": "runtime_mod",
        "persistence": "ram_only",
        "builtin": False,
        "title": info.name,
        "filename": path.name,
        "summary": f"Custom SMOD v{info.version} binary ({info.hook_count} hook(s), {info.file_size} B).",
        "details": "User-imported .mod binary validated with SMOD header and FNV-1a checksum checks.",
        "status": "custom",
        "target_note": "Custom .mod · Verify target firmware before enabling",
        "exclusive_group": None,
        "requires_ack": False,
        "warning": None,
        "default_enabled": False,
        "available": True,
        "unavailable_reason": None,
        "path": str(path.resolve()),
        "smod": info.to_dict(),
    }


def build_catalog(
    *,
    root: Path | None = None,
    data_dir: Path | None = None,
    bundle_dir: Path | None = None,
    target_id: str = "ilce_6400",
) -> dict[str, object]:
    """Build the validated runtime mod and PMCA tweak catalog."""
    if target_id not in SUPPORTED_CATALOG_TARGETS:
        raise ValueError(f"Unsupported catalog target_id: {target_id!r}")
    base_root = repo_root(root)
    b_root = Path(bundle_dir).resolve() if bundle_dir is not None else bundle_root()
    d_dir = user_data_dir(data_dir, root=base_root)

    specs = BUILTIN_RUNTIME_MODS_ILCE6400A if target_id == "ilce_6400a" else BUILTIN_RUNTIME_MODS
    runtime_mods: list[dict[str, object]] = []
    for spec in specs:
        resolved = None
        if spec.id == "arrivederci" and target_id == "ilce_6400":
            resolved = find_arrivederci_v5(root=base_root, data_dir=d_dir, bundle_dir=b_root)
        if resolved is None:
            resolved = _resolve_mod_candidate(spec, base_root, b_root)
        smod_dict = None
        available = resolved is not None
        unavailable_reason = None if available else (
            spec.unavailable_reason or f"Missing {spec.filename}"
        )
        if resolved is not None:
            info = parse_smod(resolved.read_bytes(), filename=spec.filename)
            smod_dict = info.to_dict()

        title = spec.title
        status = spec.status
        summary = spec.summary
        details = spec.details
        release_version = "v5-ilce6400a" if (spec.id == "arrivederci" and target_id == "ilce_6400a" and available) else None
        update_available = None

        if spec.id == "arrivederci" and target_id == "ilce_6400" and smod_dict is not None:
            if smod_dict["sha256"] == ARRIVEDERCI_V5_ASSET["sha256"]:
                release_version = "v5"
                title = "Arrivederci v5 (ARRI Classic Look)"
                status = "verified"
                summary = (
                    "Camera-verified Arrivederci v5 (SMOD v2, E0633): ARRI LogC3 Classic tone "
                    "& color look for S-Log3 / S-Gamut3.Cine."
                )
                details = (
                    f"Official release {MLV2_RELEASE_TAG} (129,444 B SMOD v2, "
                    f"SHA-256 {ARRIVEDERCI_V5_ASSET['sha256'][:8]}...). "
                    "Runs RAM-only through Mod Loader v2; toggle OFF before power-off."
                )
            else:
                release_version = "legacy"
                update_available = "v5 (click Download / Prepare Tools in Tools & Setup)"

        runtime_mods.append(
            {
                "id": spec.id,
                "category": "runtime_mod",
                "persistence": "ram_only",
                "builtin": True,
                "title": title,
                "filename": spec.filename,
                "summary": summary,
                "details": details,
                "status": status,
                "target_note": spec.target_note,
                "exclusive_group": spec.exclusive_group,
                "requires_ack": spec.requires_ack,
                "warning": spec.warning,
                "release_version": release_version,
                "update_available": update_available,
                "default_enabled": bool(spec.default_enabled and available),
                "available": available,
                "unavailable_reason": unavailable_reason,
                "path": str(resolved) if resolved else None,
                "smod": smod_dict,
            }
        )

    c_dir = custom_mods_dir(d_dir)
    for mod_path in sorted(c_dir.glob("*.mod"), key=lambda p: p.name.lower()):
        if mod_path.name.lower() in BUILTIN_MOD_FILENAMES:
            continue
        try:
            validate_mod_filename(mod_path.name)
            info = parse_smod(mod_path.read_bytes(), filename=mod_path.name)
        except SmodError:
            continue
        runtime_mods.append(_custom_entry(mod_path, info))

    pmca_tweaks = [
        {
            "id": spec.id,
            "category": "pmca_tweak",
            "persistence": spec.risk,
            "title": spec.title,
            "summary": spec.summary,
            "details": spec.details,
            "requires_ack": True,
        }
        for spec in BUILTIN_PMCA_TWEAKS
    ]

    return {
        "target_id": target_id,
        "target_model": "ILCE-6400A" if target_id == "ilce_6400a" else "ILCE-6400",
        "firmware_version": "1.01" if target_id == "ilce_6400a" else "2.00",
        "supported_targets": list(SUPPORTED_CATALOG_TARGETS),
        "max_active_runtime_mods": MAX_ACTIVE_RUNTIME_MODS,
        "runtime_mods": runtime_mods,
        "pmca_tweaks": pmca_tweaks,
    }
