"""Per-body compatibility registry for the native Sony USB debugger.

Each qualified body/firmware entry pins the exact ``libusbcmd.so`` digest and
the metadata-normalized ``usb_extcmd.ko`` digest verified via
``compare_metadata_only``. Adding a new BIONZ X body requires pulling its
``usb_extcmd.ko`` and ``libusbcmd.so`` (read-only), verifying ABI/metadata
equivalence, and adding a ``DebuggerTargetSpec`` entry here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Mapping

from tools.research.elf_metadata_identity import (
    compare_metadata_only,
    normalize_elf_metadata,
)

# Shared normalized usb_extcmd.ko SHA-256 (after zeroing .note.gnu.build-id and .comment)
# and byte-identical libusbcmd.so SHA-256 observed across ILCE-6400 (2.00) and ILCE-7C (2.00).
VERIFIED_USB_EXTCMD_SIZE = 14808
VERIFIED_USB_EXTCMD_NORMALIZED_SHA256 = (
    "9d7c97bdb8015cde3882d39379a18ebb2b0678ba5b517e50e620a5f54019d0d8"
)
VERIFIED_LIBUSBCMD_SIZE = 15948
VERIFIED_LIBUSBCMD_SHA256 = (
    "9fbf3339792e40caa73faa63d6b2dfa962ca4c47853320b49ff589595283ba71"
)
# Official Sony ILCE-6400 FW 2.00 updater image copy (E0090 / E0091): identical across all
# executable, symbol, relocation, GOT, dynamic, and unwind sections; differs only in .comment (48 B).
VERIFIED_LIBUSBCMD_UPDATER_SIZE = 15996
VERIFIED_LIBUSBCMD_UPDATER_SHA256 = (
    "9f2dd49dfae785aad9fbc1abf53c6d4179b8540c3e68ee1ca06eb2497bb71644"
)
VERIFIED_LIBUSBCMD_VARIANTS: tuple[tuple[int, str], ...] = (
    (VERIFIED_LIBUSBCMD_SIZE, VERIFIED_LIBUSBCMD_SHA256),
    (VERIFIED_LIBUSBCMD_UPDATER_SIZE, VERIFIED_LIBUSBCMD_UPDATER_SHA256),
)


def is_verified_libusbcmd(data: bytes) -> bool:
    """Return True iff ``data`` matches a verified FW 2.00 ``libusbcmd.so`` digest."""
    return (len(data), hashlib.sha256(data).hexdigest()) in VERIFIED_LIBUSBCMD_VARIANTS

# ZV-E10 (FW 2.00 - 2.03) driver pair:
# Built with Sony CE Linux 8.2.7.6 / GCC 4.5.1 (build 20220614).
# libusbcmd.so is byte-identical to ILCE-6400A 1.01 (SHA 23b94deb...; differing from
# a6400 only by a 46-byte .comment record + 2-byte e_shoff pad -> +48 B).
# usb_extcmd.ko is 14,796 B (-12 B vs a6400, matching a6400A 1.01 toolchain generation).
ZVE10_USB_EXTCMD_SIZE = 14796
ZVE10_USB_EXTCMD_NORMALIZED_SHA256 = (
    "1083cadde9dda2534585c1571fc1b320781fdeccb43cfc648e1d342dba55620f"
)
ZVE10_LIBUSBCMD_SIZE = 15996
ZVE10_LIBUSBCMD_SHA256 = (
    "23b94deb094d31f160cfe1d46330f2425e94d4801dfccb66720c4cf4b81cfb7e"
)

DEFAULT_PAYLOAD_DAEMON = "devcrashday_a6400/a6400_usb_debugd.elf"
DEFAULT_PAYLOAD_LAUNCHER = "devcrashday_a6400/hook/usb_debug_launcher.so"

ZVE10_PAYLOAD_DAEMON = "artifacts/native/zve10-usb-debugger/zve10_usb_debugd.elf"
ZVE10_PAYLOAD_LAUNCHER = "artifacts/native/zve10-usb-debugger/usb_debug_launcher.so"

DEBUGGER_RELEASE_REPO = "ivica00/sony-a6400-research"
DEBUGGER_RELEASE_TAG = "usb-debugger-binaries-v1"
PAYLOAD_ASSETS: Mapping[str, Mapping[str, object]] = {
    DEFAULT_PAYLOAD_DAEMON: {
        "filename": "a6400_usb_debugd.elf",
        "size": 11384,
        "sha256": "c5bb9c143a0e354c6474c186f61afb1fca2fd775742372a38a0f1301e1838f5f",
        "url": f"https://github.com/{DEBUGGER_RELEASE_REPO}/releases/download/{DEBUGGER_RELEASE_TAG}/a6400_usb_debugd.elf",
    },
    DEFAULT_PAYLOAD_LAUNCHER: {
        "filename": "usb_debug_launcher.so",
        "size": 6284,
        "sha256": "a6f368cc3ce4efc00c1b4f094271a0b99aebba9a7940492c60dd7c50033fa5e4",
        "url": f"https://github.com/{DEBUGGER_RELEASE_REPO}/releases/download/{DEBUGGER_RELEASE_TAG}/usb_debug_launcher.so",
    },
}
PMCA_ARCHIVE_URL = "https://github.com/ma1co/Sony-PMCA-RE/archive/refs/heads/master.zip"


@dataclass(frozen=True)
class DebuggerTargetSpec:
    """Declarative compatibility contract for native USB debugger deployment."""

    target_id: str
    model_name: str
    firmware_versions: tuple[str, ...]
    qualification: str
    usb_extcmd_size: int
    usb_extcmd_normalized_sha256: str
    usb_extcmd_exact_sha256s: tuple[str, ...]
    usb_extcmd_reference_paths: tuple[str, ...]
    libusbcmd_size: int
    libusbcmd_sha256: str
    libusbcmd_variants: tuple[tuple[int, str], ...] = ()
    payload_daemon: str = DEFAULT_PAYLOAD_DAEMON
    payload_launcher: str = DEFAULT_PAYLOAD_LAUNCHER
    bootstrap_mode: str = "senser_ff01"
    notes: str = ""

    def accepted_libusbcmd_variants(self) -> tuple[tuple[int, str], ...]:
        """Return deduplicated ``(size, sha256)`` tuples accepted for ``libusbcmd.so``."""
        seen: list[tuple[int, str]] = [(self.libusbcmd_size, self.libusbcmd_sha256)]
        for pair in self.libusbcmd_variants:
            if pair not in seen:
                seen.append(pair)
        return tuple(seen)


USB_DEBUGGER_TARGETS: Mapping[str, DebuggerTargetSpec] = {
    "ilce_6400": DebuggerTargetSpec(
        target_id="ilce_6400",
        model_name="ILCE-6400",
        firmware_versions=("2.00",),
        qualification="verified",
        usb_extcmd_size=VERIFIED_USB_EXTCMD_SIZE,
        usb_extcmd_normalized_sha256=VERIFIED_USB_EXTCMD_NORMALIZED_SHA256,
        usb_extcmd_exact_sha256s=(
            "fca6bfcf5e0c3c4098d33b810a2788eac1e8d8bbefe072e8792d203d69854719",
        ),
        usb_extcmd_reference_paths=(
            "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/kmod/usb_extcmd.ko",
            "artifacts/hardware/ilce-7c-2.00-pull/usb_extcmd.ko",
        ),
        libusbcmd_size=VERIFIED_LIBUSBCMD_SIZE,
        libusbcmd_sha256=VERIFIED_LIBUSBCMD_SHA256,
        libusbcmd_variants=(
            (VERIFIED_LIBUSBCMD_UPDATER_SIZE, VERIFIED_LIBUSBCMD_UPDATER_SHA256),
        ),
        notes="Primary verified hardware target (E0090, E0091, E0092, E0401; both FW 2.00 libusbcmd.so builds).",
    ),
    "ilce_7c": DebuggerTargetSpec(
        target_id="ilce_7c",
        model_name="ILCE-7C",
        firmware_versions=("2.00",),
        qualification="verified",
        usb_extcmd_size=VERIFIED_USB_EXTCMD_SIZE,
        usb_extcmd_normalized_sha256=VERIFIED_USB_EXTCMD_NORMALIZED_SHA256,
        usb_extcmd_exact_sha256s=(
            "db7383e9ad731cc23d5334dffdd4756e53dbb6aa9e32039dc503f2e2d8a246ef",
        ),
        usb_extcmd_reference_paths=(
            "artifacts/hardware/ilce-7c-2.00-pull/usb_extcmd.ko",
            "firmware/extracted_a6400/firmware.tar_unpacked/0700_part_image/dev/nflasha15_unpacked/kmod/usb_extcmd.ko",
        ),
        libusbcmd_size=VERIFIED_LIBUSBCMD_SIZE,
        libusbcmd_sha256=VERIFIED_LIBUSBCMD_SHA256,
        notes=(
            "Verified on live ILCE-7C (FW 2.00): libusbcmd.so is byte-identical to ILCE-6400; "
            "usb_extcmd.ko is metadata-only equivalent; info/ps/exec verified in normal MSC."
        ),
    ),
    "ilce_6400a": DebuggerTargetSpec(
        target_id="ilce_6400a",
        model_name="ILCE-6400A",
        firmware_versions=("1.01",),
        qualification="reference",
        usb_extcmd_size=14796,
        usb_extcmd_normalized_sha256="unverified",
        usb_extcmd_exact_sha256s=(
            "d47d05ec891d18c8582baf19e413967c5d3fd5c36438d76f6d2e8a7f112428e3",
        ),
        usb_extcmd_reference_paths=(),
        libusbcmd_size=15996,
        libusbcmd_sha256="23b94deb094d31f160cfe1d46330f2425e94d4801dfccb66720c4cf4b81cfb7e",
        bootstrap_mode="jiritsu_ram_only",
        notes=(
            "E0506 package identity. The Senser FileControl installer does not apply. "
            "Normalized usb_extcmd equivalence is intentionally unverified, so the old "
            "installer cannot treat this body as compatible."
        ),
    ),
    "zv_e10": DebuggerTargetSpec(
        target_id="zv_e10",
        model_name="ZV-E10",
        firmware_versions=("2.00", "2.01", "2.02", "2.03"),
        qualification="verified",
        usb_extcmd_size=ZVE10_USB_EXTCMD_SIZE,
        usb_extcmd_normalized_sha256=ZVE10_USB_EXTCMD_NORMALIZED_SHA256,
        usb_extcmd_exact_sha256s=(
            "98cb54e3d3600f754fc348f553f1d248b64bf2105e461239f60fe88db3d0473a",
        ),
        usb_extcmd_reference_paths=(
            "artifacts/hardware/zve10-pull/usb_extcmd.ko",
        ),
        libusbcmd_size=ZVE10_LIBUSBCMD_SIZE,
        libusbcmd_sha256=ZVE10_LIBUSBCMD_SHA256,
        payload_daemon=ZVE10_PAYLOAD_DAEMON,
        payload_launcher=ZVE10_PAYLOAD_LAUNCHER,
        bootstrap_mode="senser_ff01",
        notes=(
            "Verified on live ZV-E10 (FW 2.02, body_version 100.110.035). "
            "Native USB debugger and MTF2 touch layer verified over Senser terminal protocol."
        ),
    ),
}


def list_debugger_targets() -> tuple[str, ...]:
    """Return sorted target IDs registered for native USB debugger deployment."""
    return tuple(sorted(USB_DEBUGGER_TARGETS))


def get_debugger_target(target_id: str) -> DebuggerTargetSpec:
    """Look up a registered debugger target spec by ID."""
    key = target_id.strip().lower().replace("-", "_")
    if key == "zve10":
        key = "zv_e10"
    if key not in USB_DEBUGGER_TARGETS:
        known = ", ".join(list_debugger_targets())
        raise KeyError(
            f"Unknown debugger target {target_id!r}; qualified targets: {known}"
        )
    return USB_DEBUGGER_TARGETS[key]


def find_debugger_target_by_model(
    model_name: str,
    firmware_version: str | None = None,
) -> DebuggerTargetSpec | None:
    """Match a camera MSC model string (and optional firmware) to a registered spec."""
    normalized_model = model_name.strip().upper()
    if not normalized_model:
        return None
    fw = firmware_version.strip() if firmware_version else None
    candidates = [
        spec for spec in USB_DEBUGGER_TARGETS.values()
        if spec.model_name.upper() == normalized_model
    ]
    if not candidates:
        return None
    if fw is not None:
        for spec in candidates:
            if fw in spec.firmware_versions:
                return spec
    return candidates[0]


def load_reference_usb_extcmd(spec: DebuggerTargetSpec, root: Path) -> tuple[Path | None, bytes | None]:
    """Load the first existing reference ``usb_extcmd.ko`` for ``spec``, if present."""
    for rel in spec.usb_extcmd_reference_paths:
        candidate = root / rel
        if candidate.is_file():
            return candidate, candidate.read_bytes()
    return None, None


def verify_camera_debugger_compatibility(
    spec: DebuggerTargetSpec,
    actual_ko: bytes,
    actual_lib: bytes,
    reference_ko: bytes | None = None,
) -> dict:
    """Verify pulled camera ``usb_extcmd.ko`` and ``libusbcmd.so`` against ``spec``."""
    actual_ko_sha = hashlib.sha256(actual_ko).hexdigest()
    actual_lib_sha = hashlib.sha256(actual_lib).hexdigest()

    accepted_libs = spec.accepted_libusbcmd_variants()
    lib_match = (len(actual_lib), actual_lib_sha) in accepted_libs

    if reference_ko is not None:
        ko_comparison = compare_metadata_only(actual_ko, reference_ko)
        ko_norm = ko_comparison.get("normalized_camera_sha256")
        ko_equiv = bool(ko_comparison.get("equivalent"))
    else:
        norm_info = normalize_elf_metadata(actual_ko)
        ko_norm = norm_info.get("normalized_sha256")
        ko_equiv = bool(norm_info.get("valid")) and ko_norm == spec.usb_extcmd_normalized_sha256
        ko_comparison = {
            "equivalent": ko_equiv,
            "normalized_camera_sha256": ko_norm,
            "normalized_expected_sha256": spec.usb_extcmd_normalized_sha256,
            "reason": norm_info.get("reason"),
        }

    ko_match = (
        len(actual_ko) == spec.usb_extcmd_size
        and ko_equiv
        and ko_norm == spec.usb_extcmd_normalized_sha256
    )

    reasons: list[str] = []
    if not lib_match:
        expected_str = " or ".join(f"{sz} B / {sha}" for sz, sha in accepted_libs)
        reasons.append(
            f"libusbcmd.so mismatch (got {len(actual_lib)} B / {actual_lib_sha}, "
            f"expected {expected_str})"
        )
    if not ko_match:
        reasons.append(
            "usb_extcmd.ko mismatch: "
            + str(ko_comparison.get("reason") or f"normalized={ko_norm}")
        )

    return {
        "compatible": lib_match and ko_match,
        "target_id": spec.target_id,
        "model_name": spec.model_name,
        "qualification": spec.qualification,
        "libusbcmd_match": lib_match,
        "usb_extcmd_match": ko_match,
        "exact_usb_extcmd_match": actual_ko_sha in spec.usb_extcmd_exact_sha256s,
        "actual_usb_extcmd_bytes": len(actual_ko),
        "actual_usb_extcmd_sha256": actual_ko_sha,
        "actual_libusbcmd_bytes": len(actual_lib),
        "actual_libusbcmd_sha256": actual_lib_sha,
        "ko_comparison": ko_comparison,
        "reason": "; ".join(reasons) if reasons else None,
    }
