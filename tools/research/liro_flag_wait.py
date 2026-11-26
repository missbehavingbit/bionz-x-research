"""Reconstruct the captured a6400 Task-43 wait from stopped QEMU RAM receipts.

Offline observation only. No QEMU command, firmware call or completion is sent.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import struct

from project_paths import PROJECT_ROOT

KERNEL_SHA256 = "d7a9ae09d2195c1b5151e60f3ff1344715b619631cfad608d9a5fcf7a876c618"
LIRO_SHA256 = "ca5fa9ecc990c2ceda755887563dcd2291fb49f56a4260d895818e7f6782f535"
AVCAM_SHA256 = "dd8f8d0cc74ad4b70100e5728cf8b159b5155e7fb50eaf1147b99c9054740209"
USR_SHA256 = "70d2613fcd24b13cfc4d75e98f435171fdfc50a6bc64d408e2df42ffb8197064"
TASKS_OFFSET = 0x780
FLAG_LOCKS_OFFSET = 0xF78
TWAI_FLG_SLEEP_RETURN = 0x3BE8
CALLERS = {
    0x63A67DC3: "first_display_initialization_wait",
    0x63A67DED: "display_initialization_first_poll_loop",
    0x63A67E1F: "display_initialization_second_poll_loop",
    0x63D8B81F: "display_task_main_loop",
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class RecordedMemory:
    def __init__(self, root, rows):
        if not 1 <= len(rows) <= 128:
            raise ValueError("Expected 1..128 bounded RAM receipts")
        self.regions = []
        total = 0
        root = root.resolve()
        for row in rows:
            relative = PurePosixPath(row["file"])
            path = root.joinpath(*relative.parts).resolve()
            size, address = row["bytes"], row["address"]
            if (relative.is_absolute() or not path.is_relative_to(root)
                    or row["space"] != "guest-virtual-cpu0"
                    or not 0 < size <= 4096 or address % 4 or size % 4
                    or not 0x5F000000 <= address < address + size <= 0xB0000000
                    or address // 4096 != (address + size - 1) // 4096):
                raise ValueError("RAM receipt escapes the declared namespace or page bound")
            total += size
            if total > 512 * 1024 or path.stat().st_size != size:
                raise ValueError("RAM receipt exceeds its byte bound")
            data = path.read_bytes()
            if sha256(data) != row["sha256"]:
                raise ValueError("RAM receipt SHA-256 mismatch")
            self.regions.append((address, data))

    def read(self, address, size):
        if not 0 < size <= 8192 or address % 4 or size % 4:
            raise ValueError("Read exceeds the aligned object/stack bound")
        result = bytearray()
        while len(result) < size:
            current = address + len(result)
            matches = [(base, data) for base, data in self.regions
                       if base <= current < base + len(data)]
            if not matches:
                raise ValueError(f"Unobserved RAM at {current:#x}")
            count = min(size - len(result),
                        *(base + len(data) - current for base, data in matches),
                        *(base - current for base, _ in self.regions
                          if current < base < address + size))
            copies = [data[current - base:current - base + count]
                      for base, data in matches]
            if any(copy != copies[0] for copy in copies[1:]):
                raise ValueError("Conflicting overlapping RAM receipts")
            result.extend(copies[0])
        return bytes(result)


def _frames(stack, base):
    fp, sp, pc = struct.unpack_from("<3I", stack, 0x38)
    frames = []
    for _ in range(16):
        frames.append(dict(fp=fp, sp=sp, pc=pc))
        if fp % 4 or fp < sp + 12 or not base <= fp - 12 < fp + 4 < base + len(stack):
            return frames
        next_fp, next_sp, next_pc = struct.unpack_from("<3I", stack, fp - base - 12)
        if not fp + 4 <= next_sp < base + len(stack):
            return frames
        fp, sp, pc = next_fp, next_sp, next_pc
    raise ValueError("Saved stack exceeds the sixteen-frame bound")


def task_control(read, bss, task_id):
    if not 0x5F000000 <= bss < 0x60000000 - 0x1000 or bss % 4:
        raise ValueError("LIRO .bss is outside the module namespace")
    if not 1 <= task_id <= 255:
        raise ValueError("LIRO task ID is outside 1..255")
    _, address = struct.unpack("<2I", read(bss + TASKS_OFFSET + (task_id - 1) * 8, 8))
    words = struct.unpack("<40I", read(address, 160))
    if words[6] != task_id:
        raise ValueError("LIRO task control-block identity mismatch")
    return address, words


def decode_flag_wait(read, bss, text):
    if not 0x5F000000 <= text < 0x60000000 - 0x4000 or text % 4:
        raise ValueError("LIRO .text is outside the module namespace")
    control, words = task_control(read, bss, 43)
    if words[2] != 4 or words[3] != 8 or not 1 <= words[4] <= 64:
        raise ValueError("Task 43 is not in the qualified event-flag wait")
    linux_state, _, stack_base = struct.unpack("<3I", read(words[5], 12))
    if linux_state == 0 or stack_base % 8192:
        raise ValueError("Task 43 is runnable or has an invalid saved stack")
    stack = read(stack_base, 8192)
    frames = _frames(stack, stack_base)
    matches = [frame for frame in frames if frame["pc"] == text + TWAI_FLG_SLEEP_RETURN]
    if len(matches) != 1:
        raise ValueError("Expected one original twai_flg sleep frame")
    fp = matches[0]["fp"]
    if not stack_base <= fp - 0x54 < fp + 0x1C <= stack_base + len(stack):
        raise ValueError("twai_flg locals escape the confirmed stack")
    obj, mask, mode = struct.unpack_from("<3I", stack, fp - stack_base - 0x3C)
    timeout = struct.unpack_from("<i", stack, fp - stack_base + 4)[0]
    caller = struct.unpack_from("<I", stack, fp - stack_base + 0x18)[0]
    lock = bss + FLAG_LOCKS_OFFSET + (words[4] - 1) * 8
    if words[0x90 // 4] != obj + 0x10 or words[0x94 // 4] != lock:
        raise ValueError("Saved flag object disagrees with the task wait linkage")
    if mode not in (0, 1) or mask == 0:
        raise ValueError("Unsupported flag-wait mode or empty mask")
    flag = struct.unpack("<8I", read(obj, 32))
    pattern = flag[3]
    satisfied = bool(pattern & mask) if mode == 1 else pattern & mask == mask
    return dict(
        task_id=43, entry=words[0x4C // 4], control_address=control,
        linux_task=words[5], linux_state=linux_state, stack_base=stack_base,
        flag_id=words[4], flag_object=obj, flag_pattern=pattern,
        requested_mask=mask, wait_mode=mode,
        wait_mode_name="TWF_ORW" if mode == 1 else "TWF_ANDW",
        timeout=timeout, predicate_satisfied=satisfied,
        sleep_return=text + TWAI_FLG_SLEEP_RETURN, caller_return=caller,
        phase=CALLERS.get(caller, "unclassified_original_caller"),
        wait_list_head=obj + 0x10, wait_list_links=list(flag[4:6]),
        saved_frames=frames,
        limitation="A stopped wait is not proof of a sole global deadlock or a completed boot.",
    )


def _section(transcript, name):
    matches = re.findall(r"__LIRO_" + name + r"__:(0x[0-9a-fA-F]{8})(?![0-9a-fA-F])",
                         transcript)
    if len(matches) != 1:
        raise ValueError(f"Expected one current-run LIRO {name} observation")
    return int(matches[0], 16)


def replay(probe_path, inputs, liro, avcam):
    raw_probe = probe_path.read_bytes()
    probe = json.loads(raw_probe)
    if (probe["status"] != "completed" or probe["stopped_state"]["running"]
            or not probe["owned_process_exited"] or probe["qemu_exit_code"] != 0):
        raise ValueError("Expected a completed, stopped snapshot and an exited owned QEMU")
    manifest_raw = (inputs / "boot-manifest.json").read_bytes()
    if sha256(manifest_raw) != probe["inputs_manifest_sha256"]:
        raise ValueError("Boot-input manifest differs from the observed run")
    manifest = json.loads(manifest_raw)
    usr = [entry for entry in manifest["inputs"]
           if PureWindowsPath(entry["path"]).name == "nflasha15.bin"]
    if len(usr) != 1 or usr[0]["sha256"] != USR_SHA256:
        raise ValueError("Boot manifest has no unique qualified captured usr image")
    identities = {}
    for name, path, expected, limit in (
            ("kernel", inputs / "nflasha3-Image", KERNEL_SHA256, 8 * 1024 ** 2),
            ("liro", liro, LIRO_SHA256, 128 * 1024),
            ("avcam", avcam, AVCAM_SHA256, 17 * 1024 ** 2)):
        if path.stat().st_size > limit or sha256(path.read_bytes()) != expected:
            raise ValueError(f"Unsupported {name} identity for this wait layout")
        identities[name] = expected
    for name, expected in (("nflasha3-Image", KERNEL_SHA256), ("av-cam.bin", AVCAM_SHA256)):
        if manifest["outputs"][name]["sha256"] != expected:
            raise ValueError("Boot manifest is not the selected captured instruction profile")
    console = (probe_path.parent / "console.log").read_bytes()
    if sha256(console) != probe["console_sha256"]:
        raise ValueError("Console receipt SHA-256 mismatch")
    transcript = console.decode("utf-8", errors="replace")
    bss, text = _section(transcript, "BSS"), _section(transcript, "TEXT")
    memory = RecordedMemory(probe_path.parent, probe["liro_memory_reads"])
    wait = decode_flag_wait(memory.read, bss, text)
    _, task65 = task_control(memory.read, bss, 65)
    return dict(
        status="observed", probe_sha256=sha256(raw_probe),
        tool_sha256=sha256(Path(__file__).read_bytes()), identities=identities,
        inputs_manifest_sha256=sha256(manifest_raw), liro_bss=bss, liro_text=text,
        task43=wait,
        task65=dict(state_bits=task65[2], pending_activations=task65[8],
                    entry=task65[0x4C // 4], dormant=bool(task65[2] & 0x10)),
        limitations=["Offline reconstruction of one all-CPU-stopped QEMU snapshot.",
                     "No wait, interrupt, application state or hardware response was injected."],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--liro", type=Path, required=True)
    parser.add_argument("--avcam", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = args.report_dir.resolve()
        if not output.is_relative_to((PROJECT_ROOT / "artifacts").resolve()):
            raise ValueError("Reports must stay under artifacts")
        report = replay(args.probe, args.inputs, args.liro, args.avcam)
        output.mkdir(parents=True, exist_ok=False)
        (output / "wait.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"{type(error).__name__}: {error}\n")
    print(json.dumps(report["task43"], indent=2))


if __name__ == "__main__":
    main()
