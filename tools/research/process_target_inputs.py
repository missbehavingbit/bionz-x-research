"""Target-scoped, hash-checked process-image inputs for InterpreterProbe.

The A6400 continues to use its existing generated inventory bundle. Other targets
must provide a target-tagged input manifest with explicit image hashes and may
declare only the syscall contracts supported by their evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import posixpath
from pathlib import Path
import stat
import struct

from project_paths import PROJECT_ROOT
from src.a6400.images.ext2_metadata import Ext2Metadata


ILCE_7C_MANIFEST = Path("src/a6400/targets/process_input_manifests/ilce_7c_203.json")
ILCE_7C_IDENTITY = {
    "model": "Sony ILCE-7C",
    "firmware_version": "2.03",
    "model_id": "0x91030019",
    "updater_sha256": "e382c3deecb8d0fe8463a2451ddbfa897cd131a1797170d05936fabc4ffaf6f8",
}
ILCE_7C_IMAGE_PINS = {
    "application": "ed01c9b3896febbe44e06bc82ddbe0e989ad58da82da9a7fc8c77a68b4da6cab",
    "interpreter:nflasha7-ld-2.11.2": "c37bf426bf64d644f0d0a428ab6e4366d31b232c96985de7bb20399a7fc5fecc",
    "kernel": "01d7229106f96142ef28c2096994f1b7b2fb6c93cfb69e6d8629c7426aeee7fe",
    "init": "94b8d6bd8b31dc5881beae992cbadda0c4de01b725351cad97b67d1b081bf142",
    "rootfs_image": "0b05aeba3f137c6f736cf7fddd6371e6496c1b1d11237f84c7f540d5d4a84422",
}
ILCE_7C_FIRMWARE_NAMESPACE = "firmware/ilce_7c_203/"
ILCE_7C_APPLICATION_GUEST_PATH = "/usr/bin/im.elf"
ILCE_7C_APPLICATION_PATH_EVIDENCE = {
    "status": "static_init_mount_candidate",
    "mount_target": "/usr",
    "source_partition": "nflasha15",
    "runtime_mount_success_verified": False,
}
ILCE_7C_PT_INTERP = "//lib/ld-linux.so.3"
ILCE_7C_INTERPRETER_CANDIDATE_ID = "nflasha7-ld-2.11.2"
ILCE_7C_INTERPRETER_RESOLUTION = "static_package_mapping_conditional_on_mount_pivot"
ILCE_7C_UNKNOWN_CONTRACTS = [
    "brk", "uname runtime identity", "filesystem", "metadata", "mmap",
    "kuser runtime mapping", "UIPC", "thread scheduling",
]
ILCE_7C_FIRMWARE_PATHS = {
    "application": "firmware/ilce_7c_203/nflasha15/bin/im.elf",
    "interpreter:nflasha7-ld-2.11.2": "firmware/ilce_7c_203/nflasha7/lib/ld-2.11.2.so",
    "kernel": "firmware/ilce_7c_203/nflasha3/vmlinux.bin",
    "init": "firmware/ilce_7c_203/nflasha7/sbin/init",
    "rootfs_image": "firmware/ilce_7c_203/nflasha7/rootfs-partition.img",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_firmware_file(project_root: Path, firmware_path: str, expected_sha256: str) -> Path:
    """Resolve a declared relative firmware path and verify containment and digest."""
    if not isinstance(firmware_path, str) or not firmware_path or Path(firmware_path).is_absolute():
        raise ValueError("Firmware input path must be a nonempty relative path")
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in expected_sha256)):
        raise ValueError("Firmware input requires a lowercase SHA-256 pin")
    firmware_root = (project_root / "firmware").resolve()
    local = (project_root / firmware_path).resolve()
    if not local.is_relative_to(firmware_root):
        raise ValueError("Firmware input path escapes the target firmware directory")
    data = local.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"Firmware input hash mismatch: {firmware_path}")
    return local


def load_target_manifest(project_root: Path, manifest_relative: Path, target_id: str) -> dict:
    """Load metadata only from a target-specific manifest; reject cross-target reuse."""
    path = (project_root / manifest_relative).resolve()
    if not path.is_relative_to(project_root.resolve()):
        raise ValueError("Target input manifest escapes the project root")
    manifest = read_json(path)
    if not isinstance(manifest, dict):
        raise ValueError("Target process-input manifest must contain a JSON object")
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported target process-input manifest schema")
    if manifest.get("target_id") != target_id:
        raise ValueError(
            f"Target input manifest mismatch: requested {target_id}, got {manifest.get('target_id')!r}"
        )
    if target_id == "ilce_7c":
        for field in ("application", "kernel", "init", "rootfs_image", "syscall_contracts",
                      "guest_filesystem_manifest", "firmware_inventory", "runtime_mount_evidence"):
            if not isinstance(manifest.get(field), dict):
                raise ValueError(f"A7C process-input manifest field {field!r} must be an object")
        if manifest.get("firmware_identity") != ILCE_7C_IDENTITY:
            raise ValueError("A7C firmware identity does not match the source-pinned 2.03 manifest")
        if manifest.get("firmware_namespace") != ILCE_7C_FIRMWARE_NAMESPACE:
            raise ValueError("A7C firmware namespace does not match the source-pinned 2.03 namespace")
        application = manifest.get("application", {})
        if application.get("guest_path") != ILCE_7C_APPLICATION_GUEST_PATH:
            raise ValueError("A7C application guest path does not match the source-pinned process image")
        if application.get("guest_path_evidence") != ILCE_7C_APPLICATION_PATH_EVIDENCE:
            raise ValueError("A7C application mount evidence must remain static and runtime-unverified")
        if manifest.get("application", {}).get("sha256") != ILCE_7C_IMAGE_PINS["application"]:
            raise ValueError("A7C application SHA-256 does not match the source-pinned 2.03 manifest")
        candidates = manifest.get("interpreter_candidates", [])
        if (not isinstance(candidates, list) or len(candidates) != 1
                or not isinstance(candidates[0], dict)
                or candidates[0].get("id") != ILCE_7C_INTERPRETER_CANDIDATE_ID):
            raise ValueError("A7C interpreter candidate set does not match the source-pinned 2.03 manifest")
        candidate = candidates[0]
        if (candidate.get("declared_pt_interp") != ILCE_7C_PT_INTERP
                or candidate.get("resolution_status") != ILCE_7C_INTERPRETER_RESOLUTION):
            raise ValueError("A7C PT_INTERP candidate must retain its unresolved static-package status")
        candidate_pins = {f"interpreter:{item.get('id')}": item.get("sha256") for item in candidates}
        candidate_paths = {f"interpreter:{item.get('id')}": item.get("firmware_path") for item in candidates}
        for name, expected in ILCE_7C_IMAGE_PINS.items():
            if name == "application":
                continue
            actual = candidate_pins.get(name) if name.startswith("interpreter:") else manifest.get(name, {}).get("sha256")
            if actual != expected:
                raise ValueError(f"A7C {name} SHA-256 does not match the source-pinned 2.03 manifest")
        actual_paths = {
            "application": manifest.get("application", {}).get("firmware_path"),
            **candidate_paths,
            "kernel": manifest.get("kernel", {}).get("firmware_path"),
            "init": manifest.get("init", {}).get("firmware_path"),
            "rootfs_image": manifest.get("rootfs_image", {}).get("firmware_path"),
        }
        if actual_paths != ILCE_7C_FIRMWARE_PATHS:
            raise ValueError("A7C firmware input paths do not match the source-pinned 2.03 inventory")
        if manifest.get("syscall_contracts", {}).get("supported") != ["stop"]:
            raise ValueError("A7C syscall support must remain limited to the verified stop policy")
        if manifest.get("syscall_contracts", {}).get("unknown") != ILCE_7C_UNKNOWN_CONTRACTS:
            raise ValueError("A7C unresolved runtime contracts must remain explicitly unknown")
        if manifest.get("guest_filesystem_manifest", {}).get("completeness") != "incomplete":
            raise ValueError("A7C guest filesystem completeness is not verified")
        if manifest.get("firmware_inventory", {}).get("status") != "not_supplied":
            raise ValueError("A7C complete ELF inventory is not supplied")
        if manifest.get("runtime_mount_evidence", {}).get("success_or_boot_mode_verified") is not False:
            raise ValueError("A7C runtime mount evidence must remain explicitly unverified")
    return manifest


def select_a7c_images(project_root: Path, manifest: dict, entry: str,
                      interpreter_candidate: str | None) -> tuple[dict, dict, dict, dict, dict]:
    """Hash-check the A7C app, selected linker candidate, kernel and init.

    The candidate is not silently promoted to a PT_INTERP resolution: callers
    must name it explicitly while its resolution_status is unverified.
    """
    namespace = manifest.get("firmware_namespace")
    if namespace != ILCE_7C_FIRMWARE_NAMESPACE:
        raise ValueError("A7C firmware namespace does not match its independently pinned target namespace")
    target_root = (project_root / namespace).resolve()

    def target_file(record):
        path = record.get("firmware_path", "")
        if not path.startswith(namespace):
            raise ValueError(f"Firmware input crosses target namespace {namespace}: {path}")
        local = resolve_firmware_file(project_root, path, record["sha256"])
        if not local.is_relative_to(target_root):
            raise ValueError(f"Firmware input escapes target namespace {namespace}: {path}")
        return local

    app_spec = manifest["application"]
    if entry != ILCE_7C_APPLICATION_GUEST_PATH or app_spec["guest_path"] != ILCE_7C_APPLICATION_GUEST_PATH:
        raise ValueError(f"A7C application is explicitly pinned as {ILCE_7C_APPLICATION_GUEST_PATH}")
    app_path = target_file(app_spec)

    candidate_specs = manifest.get("interpreter_candidates", [])
    if interpreter_candidate is None:
        verified = [item for item in candidate_specs if item.get("resolution_status") == "verified"]
        if len(verified) != 1:
            raise ValueError("A7C PT_INTERP mapping is unresolved; select a named interpreter candidate explicitly")
        candidate_spec = verified[0]
    else:
        matches = [item for item in candidate_specs if item.get("id") == interpreter_candidate]
        if len(matches) != 1:
            raise ValueError(f"Unknown or ambiguous A7C interpreter candidate: {interpreter_candidate}")
        candidate_spec = matches[0]
    interpreter_path = target_file(candidate_spec)
    if candidate_spec.get("resolution_status") != "verified" and interpreter_candidate is None:
        raise ValueError("Unverified interpreter candidates require explicit selection")

    kernel_spec = manifest["kernel"]
    kernel_path = target_file(kernel_spec)
    init_spec = manifest["init"]
    init_path = target_file(init_spec)
    rootfs_spec = manifest["rootfs_image"]
    rootfs_path = target_file(rootfs_spec)
    return (
        {**app_spec, "local_path": app_path},
        {**candidate_spec, "local_path": interpreter_path},
        {**kernel_spec, "local_path": kernel_path},
        {**init_spec, "local_path": init_path},
        {**rootfs_spec, "local_path": rootfs_path},
    )


def _ext2_file_bytes(image: Ext2Metadata, inode_number: int) -> bytes:
    """Read one bounded, non-sparse ext2 file using its checked inode block pointers."""
    inode = image.inode(inode_number)
    size = inode["size"]
    if size > 16 * 1024 * 1024:
        raise ValueError("ext2 path file exceeds the research read bound")
    if not stat.S_ISREG(inode["mode"]) and not stat.S_ISLNK(inode["mode"]):
        raise ValueError("ext2 content request is not a regular file or symlink")
    # Fast symlinks are stored in the inode's i_block bytes.
    if stat.S_ISLNK(inode["mode"]) and size <= 60 and inode["blocks_512"] == 0:
        raw = image.data[inode["inode_file_offset"] + 40:inode["inode_file_offset"] + 40 + size]
        if len(raw) != size:
            raise ValueError("truncated ext2 fast symlink")
        return raw

    def indirect(block_number: int, depth: int):
        if not block_number:
            raise ValueError("sparse ext2 path file is unsupported")
        if depth == 0:
            yield block_number
            return
        raw = image._block(block_number)
        for (child,) in struct.iter_unpack("<I", raw):
            if child:
                yield from indirect(child, depth - 1)

    needed = math.ceil(size / image.block_size)
    blocks = []
    pointers = inode["pointers"]
    for slot, block_number in enumerate(pointers):
        depth = max(0, slot - 11)
        if block_number:
            blocks.extend(indirect(block_number, depth))
        if len(blocks) >= needed:
            break
    if len(blocks) < needed:
        raise ValueError("incomplete ext2 path file blocks")
    return b"".join(image._block(block) for block in blocks[:needed])[:size]


def verify_a7c_interpreter_package_mapping(project_root: Path, rootfs_spec: dict,
                                           interpreter_spec: dict, interpreter_image: dict,
                                           pt_interp: str) -> dict:
    """Verify the A7C rootfs symlink and bytes; report mount/pivot as static only."""
    fs_image = Ext2Metadata(rootfs_spec["local_path"], rootfs_spec["sha256"])
    nodes = fs_image.index()
    # Repeated leading slashes are reduced for this package lookup only. The target
    # kernel's runtime handling of the exact PT_INTERP spelling is not executed here.
    guest_path = "/" + pt_interp.lstrip("/")
    link = nodes.get(guest_path)
    if link is None or not stat.S_ISLNK(link["mode"]):
        raise ValueError(f"A7C package rootfs has no symlink at normalized PT_INTERP path {guest_path}")
    link_target = _ext2_file_bytes(fs_image, link["inode"]).decode("utf-8", errors="strict")
    resolved_path = posixpath.normpath(posixpath.join(posixpath.dirname(guest_path), link_target))
    target = nodes.get(resolved_path)
    if target is None or not stat.S_ISREG(target["mode"]):
        raise ValueError(f"A7C PT_INTERP symlink target is not a package regular file: {resolved_path}")
    raw = _ext2_file_bytes(fs_image, target["inode"])
    content_sha256 = hashlib.sha256(raw).hexdigest()
    candidate_sha256 = hashlib.sha256(interpreter_image["data"]).hexdigest()
    if content_sha256 != candidate_sha256 or content_sha256 != interpreter_spec["sha256"]:
        raise ValueError("A7C PT_INTERP package file does not match the selected linker candidate bytes")
    return {
        "status": "package_static_mapping_verified",
        "pt_interp": pt_interp,
        "lookup_normalization": "collapse leading slashes for ext2 package lookup; target-kernel runtime handling remains unexecuted",
        "rootfs_image_sha256": rootfs_spec["sha256"],
        "rootfs_guest_path": rootfs_spec.get("planned_mount_target"),
        "symlink_guest_path": guest_path,
        "symlink_inode": link["inode"],
        "symlink_target": link_target,
        "resolved_package_path": resolved_path,
        "resolved_inode": target["inode"],
        "resolved_size": target["size"],
        "resolved_file_sha256": content_sha256,
        "candidate_id": interpreter_spec["id"],
        "runtime_mount_or_pivot_success_verified": False,
    }


def require_guest_filesystem_manifest(project_root: Path, manifest: dict, target_id: str) -> dict:
    """Require a complete, matching rootfs manifest before filesystem policies."""
    if not isinstance(manifest, dict):
        raise ValueError("Process-input manifest must contain a JSON object")
    spec = manifest.get("guest_filesystem_manifest", {})
    if not isinstance(spec, dict):
        raise ValueError("Guest filesystem manifest declaration must be an object")
    if spec.get("status") != "available":
        raise ValueError(
            f"{target_id} guest filesystem contract is {spec.get('status', 'unknown')}: "
            f"{spec.get('reason', 'target manifest has no supplied guest filesystem evidence')}"
        )
    if not isinstance(spec.get("path"), str) or not spec["path"]:
        raise ValueError("Guest filesystem manifest path must be a nonempty relative path")
    path = (project_root / spec["path"]).resolve()
    if not path.is_relative_to(project_root.resolve()):
        raise ValueError("Guest filesystem manifest escapes project root")
    filesystem = read_json(path)
    if not isinstance(filesystem, dict):
        raise ValueError("Guest filesystem manifest must contain a JSON object")
    if filesystem.get("target_id") != target_id:
        raise ValueError("Guest filesystem manifest target_id mismatch")
    if filesystem.get("complete") is not True:
        raise ValueError("Guest filesystem manifest is not declared complete")
    return filesystem


def a7c_manifest_path() -> Path:
    return PROJECT_ROOT / ILCE_7C_MANIFEST
