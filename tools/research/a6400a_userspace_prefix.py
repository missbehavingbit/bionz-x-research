"""Own-image a6400A 1.01 loader prefixes with explicit syscall policies; no devices."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat

from project_paths import PROJECT_ROOT
from src.a6400.execution.guest_uname import extract_kernel_uts
from src.a6400.execution.interpreter_probe import InterpreterProbe
from src.a6400.images.ext2_metadata import Ext2Metadata
from src.a6400.images.process_elf import inspect_image
from src.a6400.targets import get_profile
from tools.research.a6400a_service_contracts import PACKAGE

PARTITIONS = PACKAGE / "firmware.tar_unpacked" / "0700_part_image" / "dev"
INPUTS = {
    "root_image": (PARTITIONS / "nflasha7",
                   "45052699b0f6a765a2652185a64818327195f60f3d60078bfcd6f8b8564698ee"),
    "application": (PARTITIONS / "nflasha15_unpacked" / "bin" / "im.elf",
                    "de26c4b208d9d4524a0d0e76474b626ddd5902a868c2b271d19ec1c7caffdbe5"),
    "interpreter": (PARTITIONS / "nflasha7_unpacked" / "lib" / "ld-2.11.2.so",
                    "c37bf426bf64d644f0d0a428ab6e4366d31b232c96985de7bb20399a7fc5fecc"),
    "kernel": (PARTITIONS / "nflasha3_unpacked" / "vmlinux.bin",
               "9d51ab60df8246f10cb2afb12fb504d7f148fae437ee404f5b9837cc3b8f85ca"),
}
POLICIES = ("stop", "brk", "brk-uname")


def run_prefix(policy: str = "stop", instructions: int = 200000,
               timeout_us: int = 3000000) -> dict:
    if policy not in POLICIES or not 1 <= instructions <= 1000000 or not 1 <= timeout_us <= 5000000:
        raise ValueError("Prefix policy or execution budget is outside the declared offline profile")
    root_path, root_hash = INPUTS["root_image"]
    metadata = Ext2Metadata(root_path, root_hash)
    link = metadata.index()["/lib/ld-linux.so.3"]
    if not stat.S_ISLNK(link["mode"]) or link["blocks_512"] != 0 or not 0 < link["size"] <= 60:
        raise ValueError("Original interpreter link is outside the declared fast-symlink profile")
    offset = link["inode_file_offset"] + 40
    target = metadata.data[offset:offset + link["size"]].decode("ascii")
    if target != "ld-2.11.2.so":
        raise ValueError("Original guest symlink selects a different interpreter")
    application = inspect_image(*INPUTS["application"])
    interpreter = inspect_image(*INPUTS["interpreter"])
    if application["interpreter"] not in ("/lib/ld-linux.so.3", "//lib/ld-linux.so.3"):
        raise ValueError("Application does not select the original guest interpreter link")
    uname = None
    if policy == "brk-uname":
        uname = extract_kernel_uts(*INPUTS["kernel"])
        uname["source_path"] = str(INPUTS["kernel"][0].relative_to(PROJECT_ROOT))
    probe = InterpreterProbe(syscall_policy=policy, uname_profile=uname,
                             target_profile=get_profile("ilce_6400a"))
    probe.prepare(application, interpreter, ["/usr/bin/im.elf"])
    result = probe.run(instructions, timeout_us)
    roles = ("root_image", "application", "interpreter") + (("kernel",) if uname is not None else ())
    return {
        "target_id": "ilce_6400a", "camera_operations": 0, "policy": policy,
        "instruction_budget": instructions, "timeout_us": timeout_us,
        "guest_interpreter_link": target,
        "inputs": {name: {"path": str(INPUTS[name][0].relative_to(PROJECT_ROOT)),
                           "sha256": INPUTS[name][1]} for name in roles},
        "result": result,
        "limitations": [
            "Own 1.01 update-package images; no live installed bytes or normal boot arguments are attested.",
            "Only existing guest brk and optional compiled-kernel uname contracts are enabled.",
            "Runtime-dependent uname fields stay guarded; no filesystem, IPC, device or kuser service is supplied.",
            "An access request for ld.so.preload is a boundary, not permission to supply, suppress or install a preload.",
            "A bounded loader prefix is not a linked application, booted kernel, rendered camera UI or firmware dump.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=POLICIES, default="stop")
    parser.add_argument("--instructions", type=int, default=200000)
    parser.add_argument("--timeout-us", type=int, default=3000000)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if not output.is_relative_to(PROJECT_ROOT / "artifacts"):
        parser.error("Prefix receipts must remain under artifacts")
    output.mkdir(parents=True, exist_ok=False)
    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "status": "in_progress",
              "camera_operations": 0, "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        report.update(run_prefix(args.policy, args.instructions, args.timeout_us))
        report["status"] = report["result"]["status"]
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        report.update(status="blocked", error_type=type(error).__name__, error=str(error))
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "policy", "camera_operations", "error")
                      if key in report}, indent=2))
    return 0 if report["status"] == "syscall_boundary" else 2


if __name__ == "__main__":
    raise SystemExit(main())
