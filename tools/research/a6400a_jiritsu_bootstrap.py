"""Offline ILCE-6400A Jiritsu audit, chunk planner, and closed camera-entry points.

``audit-wbi`` reads a local WBI1 image and writes a string-count receipt.
``plan-chunk-dump`` builds a single-slot 512 KiB RAM-chunk plan offline.
Live ``check``/``jiritsu-dump`` require an explicit experimental workspace-write
policy and fresh identity/driver receipts. Default camera entry remains closed.
``build-loader`` compiles and ABI-checks the authored fork/exec constructor.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research.a6400a_jiritsu import (  # noqa: E402
    CHUNK_BYTES,
    JiritsuUnverified,
    ZV_E10_PUBLIC_COMMANDS,
    audit_wbi,
    ephemeral_chunk_worker_script,
    plan_partition_chunk_dump,
)

DEFAULT_IMAGE = ROOT / "artifacts/dumps/a6400a/nflasha5.bin"


def camera_entry_closed(mode: str) -> int:
    """Refuse every mode that would talk to a camera."""
    try:
        ZV_E10_PUBLIC_COMMANDS.require_verified()
    except JiritsuUnverified as exc:
        print(f"{mode}: {exc}")
        print("No USB device was opened and no camera command was sent.")
        return 2
    return 0


def _write_artifacts_report(destination: Path, report: dict) -> None:
    if not destination.resolve().is_relative_to(ROOT / "artifacts"):
        raise ValueError("Report path must stay under artifacts")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=(
            "audit-wbi",
            "plan-chunk-dump",
            "check",
            "start-ram-debugger",
            "jiritsu-dump",
            "build-loader",
            "cleanup-worker",
            "build-ram-probe",
            "qualify-ram",
        ),
    )
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--partition", default="/dev/nflashaB0")
    parser.add_argument("--ram-dir", default="/tmp_network/a6400a-dump")
    parser.add_argument("--chunk-bytes", type=int, default=CHUNK_BYTES)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--identity-receipt", type=Path)
    parser.add_argument("--loader-manifest", type=Path)
    parser.add_argument("--owned-receipt", type=Path)
    parser.add_argument("--ram-probe-manifest", type=Path)
    parser.add_argument("--private-tmpfs", action="store_true",
                        help="Explicitly permit an exclusive 2-MiB nodev/nosuid/noexec tmpfs for RAM qualification")
    parser.add_argument("--native-reader", action="store_true",
                        help="Opt in to native partition reads only after six verified private-RAM roundtrips")
    parser.add_argument("--partitions", nargs="+")
    parser.add_argument("--owner-authorized-jiritsu-workspace", action="store_true")
    parser.add_argument("--confirm-firmware-101", action="store_true")
    parser.add_argument("--permit-control-pipe", action="store_true")
    args = parser.parse_args(argv)
    if args.native_reader and (args.mode != "jiritsu-dump" or not args.private_tmpfs):
        parser.error("--native-reader requires --mode jiritsu-dump --private-tmpfs")
    if args.private_tmpfs and args.mode != "qualify-ram" and not args.native_reader:
        parser.error("--private-tmpfs requires --mode qualify-ram or --native-reader")
    if args.mode == "build-loader":
        if args.output_dir is None:
            parser.error("--build-loader requires --output-dir")
        from tools.research.a6400a_jiritsu_worker import build_loader
        return build_loader(args.output_dir)
    if args.mode == "build-ram-probe":
        if args.output_dir is None:
            parser.error("--mode build-ram-probe requires --output-dir")
        from tools.research.a6400a_ram_probe import build_executable
        return build_executable(args.output_dir)
    if args.mode in ("check", "jiritsu-dump", "cleanup-worker", "qualify-ram") and args.owner_authorized_jiritsu_workspace:
        if (not args.confirm_firmware_101 or args.output_dir is None
                or args.baseline is None or args.identity_receipt is None
                or (args.mode != "check" and args.loader_manifest is None)
                or (args.mode == "cleanup-worker" and args.owned_receipt is None)):
            parser.error("Live worker requires firmware confirmation, fresh receipts, output, and a loader build for dumping")
        if (args.mode == "qualify-ram" or args.native_reader) and args.ram_probe_manifest is None:
            parser.error("RAM qualification and native reading require --ram-probe-manifest")
        if args.ram_dir != "/tmp_network/a6400a-dump" or args.chunk_bytes != CHUNK_BYTES:
            parser.error("--ram-dir/--chunk-bytes are planning-only; live capture uses a nonce directory and fixed 512-KiB chunks")
        if args.partitions and args.partition != "/dev/nflashaB0":
            parser.error("Choose either --partition or --partitions, not both")
        from tools.research.a6400a_jiritsu_worker import acquire
        return acquire(args)
    if args.mode == "plan-chunk-dump":
        try:
            plan = plan_partition_chunk_dump(
                args.partition,
                ram_dir=args.ram_dir,
                chunk_bytes=args.chunk_bytes,
            )
            nonce = uuid.uuid4().hex
            worker_script = ephemeral_chunk_worker_script(args.ram_dir, nonce=nonce).decode("ascii")
        except ValueError as exc:
            print(str(exc))
            return 2
        report = {**plan, "worker_nonce": nonce, "worker_script": worker_script}
        destination = (
            args.report
            if args.report is not None
            else ROOT / "artifacts/analysis/a6400a-wbi-jiritsu/chunk-plan.json"
        )
        try:
            _write_artifacts_report(destination, report)
        except ValueError as exc:
            print(str(exc))
            return 2
        print(destination)
        return 0
    if args.mode != "audit-wbi":
        return camera_entry_closed(args.mode)
    if not args.image.is_file():
        print(f"WBI image not found: {args.image}")
        return 2
    report = audit_wbi(args.image)
    destination = args.report
    if destination is None:
        destination = ROOT / "artifacts/analysis/a6400a-wbi-jiritsu/audit.json"
    try:
        _write_artifacts_report(destination, report)
    except ValueError as exc:
        print(str(exc))
        return 2
    print(destination)
    if report["missing"]:
        print("missing: " + ", ".join(report["missing"]))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
