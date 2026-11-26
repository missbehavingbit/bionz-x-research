"""Offline ILCE-6400A Jiritsu guards. No camera and no USB."""
import json
import hashlib
from pathlib import Path
import re
import shutil
import struct
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.a6400.targets.usb_debugger_compat import get_debugger_target
from tools.deploy_native_usb_debugger import deploy
from tools.research.a6400a_jiritsu import (
    A6400A_V101_LIBRARY_COMMANDS,
    CHUNK_BYTES,
    JiritsuUnverified,
    JiritsuStatusError,
    RamChunkWorker,
    ZV_E10_PUBLIC_COMMANDS,
    chunk_read_command,
    ephemeral_chunk_worker_script,
    ephemeral_runner,
    get_file_payload,
    jiritsu_crypt,
    jiritsu_key,
    parse_adjust_status,
    plan_partition_chunk_dump,
    readable_storage,
    verify_chunk,
)
from tools.research.a6400a_jiritsu_bootstrap import camera_entry_closed, main as bootstrap_main
from tools.research.a6400a_fixed_reader import HEADER, TRANSFER_BYTES
from tools.research.a6400a_jiritsu_worker import (
    JiritsuFiles, NativeRamChunkWorker, acquire, parse_inventory, write_ownership_receipt,
)
from tools.research.a6400a_fixed_acquire import load_receipt
from tools.research.a6400a_ram_probe import (
    CONFIG as RAM_PROBE_CONFIG, EXT2_MAGIC, HEADER as RAM_HEADER, RECORD as RAM_RECORD, PATHS as RAM_PATHS,
    TMPFS_MAGIC, RamWorkspaceProbe, decode_native, probe_config, prove_network_ram, prove_swap_inactive,
)

ROOT = Path(__file__).resolve().parents[1]
WBI = ROOT / "artifacts/dumps/a6400a/nflasha5.bin"


class JiritsuGuardTests(unittest.TestCase):
    def test_public_command_map_cannot_build_a_camera_packet(self):
        with self.assertRaises(JiritsuUnverified):
            get_file_payload("/version.txt", ZV_E10_PUBLIC_COMMANDS)

    def test_packaged_library_ids_still_cannot_build_a_camera_packet(self):
        self.assertEqual(A6400A_V101_LIBRARY_COMMANDS.get_file, 8)
        self.assertEqual(A6400A_V101_LIBRARY_COMMANDS.set_path, 15)
        self.assertEqual(A6400A_V101_LIBRARY_COMMANDS.set_bytes, 16)
        self.assertNotEqual(A6400A_V101_LIBRARY_COMMANDS.get_file, ZV_E10_PUBLIC_COMMANDS.get_file)
        with self.assertRaises(JiritsuUnverified):
            get_file_payload("/version.txt", A6400A_V101_LIBRARY_COMMANDS)

    def test_camera_modes_stop_before_usb(self):
        self.assertEqual(camera_entry_closed("check"), 2)
        self.assertEqual(camera_entry_closed("jiritsu-dump"), 2)

    def test_crypt_round_trip_uses_the_documented_key_length(self):
        self.assertEqual(len(jiritsu_key()), 16)
        try:
            import Cryptodome.Cipher.AES  # noqa: F401
        except ImportError:
            self.skipTest("Cryptodome is not installed")
        payload = b"ILCE-6400A jiritsu fixture"
        self.assertEqual(jiritsu_crypt(jiritsu_crypt(payload)), payload)

    def test_adjust_status_is_a_leading_word(self):
        status, body = parse_adjust_status(b"\x06\x00\x00\x10rest")
        self.assertEqual(status, 0x10000006)
        self.assertEqual(body, b"rest")

    def test_ephemeral_runner_stays_on_ram(self):
        script = ephemeral_runner("/tmp_bt/a6400_usb_debugd.elf")
        self.assertIn(b"A6400_EPHEMERAL_RAM=1", script)
        self.assertNotIn(b"/setting", script)
        self.assertNotIn(b"ld.so.preload", script)
        with self.assertRaises(ValueError):
            ephemeral_runner("/setting/a6400_usb_debugd.elf")

    def test_storage_allowlist_rejects_nflasha23_and_other_devices(self):
        self.assertEqual(readable_storage("/dev/nflasha5"), "/dev/nflasha5")
        with self.assertRaises(ValueError):
            readable_storage("/dev/nflasha23")
        with self.assertRaises(ValueError):
            readable_storage("/dev/mmca1")

    def test_chunk_command_is_read_only_and_bounded(self):
        command = chunk_read_command("/dev/nflasha5", "/tmp_bt/a6400a-chunk", 4096, 8192)
        self.assertIn("busybox dd if=/dev/nflasha5", command)
        self.assertIn("sha256sum", command)
        self.assertNotIn(" of=/dev/", command)
        with self.assertRaises(ValueError):
            chunk_read_command("/dev/nflasha23", "/tmp_bt/a6400a-chunk", 0, 4096)
        with self.assertRaises(ValueError):
            chunk_read_command("/dev/nflasha5", "/tmp_bt/a6400a-chunk", 1, 4096)

    def test_chunk_hash_must_match(self):
        data = b"a" * 4096
        digest = verify_chunk(data, __import__("hashlib").sha256(data).hexdigest() + "  chunk\n", 4096)
        self.assertEqual(len(digest), 64)
        with self.assertRaises(ValueError):
            verify_chunk(data, "0" * 64 + "  chunk\n", 4096)

    def test_a6400a_registry_is_not_a_senser_installer_target(self):
        spec = get_debugger_target("ilce_6400a")
        self.assertEqual(spec.bootstrap_mode, "jiritsu_ram_only")
        self.assertEqual(spec.qualification, "reference")
        with self.assertRaises(RuntimeError):
            deploy(object(), spec)

    def test_boot_area_storage_nodes_are_allowed(self):
        self.assertEqual(readable_storage("/dev/nflashaB0"), "/dev/nflashaB0")
        self.assertEqual(readable_storage("/dev/nflashaB1"), "/dev/nflashaB1")
        cmd = chunk_read_command("/dev/nflashaB0", "/tmp_network/a6400a-dump", 0, CHUNK_BYTES)
        self.assertIn("busybox rm -f /tmp_network/a6400a-dump/chunk", cmd)
        self.assertIn("busybox dd if=/dev/nflashaB0", cmd)

    def test_plan_partition_chunk_dump_reuses_single_ram_slot(self):
        plan = plan_partition_chunk_dump("/dev/nflashaB0")
        self.assertEqual(plan["partition"], "/dev/nflashaB0")
        self.assertEqual(plan["total_bytes"], 4_194_304)
        self.assertEqual(plan["chunk_bytes"], 524_288)
        self.assertEqual(plan["chunk_count"], 8)
        self.assertEqual(plan["max_ram_bytes"], 524_288)
        self.assertEqual(plan["ram_chunk_path"], "/tmp_network/a6400a-dump/chunk")
        self.assertIn("dlopen", plan["dlopen_constraint"])
        for idx, chunk in enumerate(plan["chunks"]):
            self.assertEqual(chunk["index"], idx)
            self.assertEqual(chunk["offset"], idx * 524_288)
            self.assertEqual(chunk["ram_chunk_path"], "/tmp_network/a6400a-dump/chunk")
            self.assertTrue(chunk["camera_command"].startswith("busybox rm -f /tmp_network/a6400a-dump/chunk && "))
            self.assertEqual(chunk["cleanup_command"], "busybox rm -f /tmp_network/a6400a-dump/chunk")
        with self.assertRaises(ValueError):
            plan_partition_chunk_dump("/dev/nflashaB0", chunk_bytes=CHUNK_BYTES + 4096)
        with self.assertRaises(ValueError):
            plan_partition_chunk_dump("/dev/nflashaB0", total_bytes=4097)
        with self.assertRaises(ValueError):
            plan_partition_chunk_dump("/dev/nflasha23")

    def test_chunk_worker_has_exclusive_ram_directory_and_identified_startup_log(self):
        script = ephemeral_chunk_worker_script("/tmp_network/a6400a-dump")
        self.assertIn(b"/proc/mounts", script)
        self.assertIn(b'tmpfs', script)
        self.assertIn(b"/tmp_network/a6400a-dump/ready", script)
        self.assertNotIn(b"mkdir -p", script)
        self.assertNotIn(b"busybox awk", script)
        self.assertIn(b"__A6400A_WORKER_LOG__:", script)
        self.assertNotIn(b"/setting", script)
        self.assertNotIn(b"ld.so.preload", script)
        with self.assertRaises(ValueError):
            ephemeral_chunk_worker_script("/setting/dump")
        for directory in ("/tmp_network/a;false", "/tmp_bt/a/../b", "/tmp_bt/a\nb",
                          "/tmp_network/a'bad", "/tmp_network/a b"):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                ephemeral_chunk_worker_script(directory)

    def test_bootstrap_plan_chunk_dump_mode_writes_artifact(self):
        out_dir = ROOT / "artifacts/tests/test-a6400a-jiritsu-chunk-plan"
        report_path = out_dir / "chunk-plan.json"
        try:
            rc = bootstrap_main(
                [
                    "--mode",
                    "plan-chunk-dump",
                    "--partition",
                    "/dev/nflashaB0",
                    "--report",
                    str(report_path),
                ]
            )
            self.assertEqual(rc, 0)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["chunk_count"], 8)
            self.assertEqual(payload["max_ram_bytes"], CHUNK_BYTES)
            self.assertIn("/tmp_network/a6400a-dump/ready", payload["worker_script"])
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    @unittest.skipUnless(WBI.is_file(), "local ILCE-6400A nflasha5 image is absent")
    def test_local_wbi_contains_the_jiritsu_service_strings(self):
        from tools.research.a6400a_jiritsu import audit_wbi

        report = audit_wbi(WBI)
        self.assertEqual(report["magic"], "WBI1")
        self.assertEqual(report["missing"], [])
        self.assertGreater(report["string_counts"]["JIRITSU_GET_FILE"], 0)
        self.assertGreater(report["string_counts"]["/tmp/jiritsu/libJit.so"], 0)


NONCE = "a" * 32
RAM = "/tmp_network/a6400a-" + NONCE


class FileFixture:
    def __init__(self, source: bytes, *, corrupt_chunk: int | None = None):
        self.source = source
        self.corrupt_chunk = corrupt_chunk
        self.input = b""
        self.output = b"stale result"
        self.staged = None
        self.events = []

    def read(self, path, limit):
        self.events.append(("read", path, limit))
        if path == RAM + "/ready":
            return f"__A6400A_WORKER__:{NONCE}\nUid:\t0\t0\t0\t0\n".encode()
        if path == RAM + "/output":
            return self.output
        if path == RAM + "/chunk":
            if self.staged is None:
                raise AssertionError("No staged RAM chunk")
            return self.staged
        raise AssertionError("Fixture forbids direct block/proc/other file reads")

    def write(self, path, data, *, verify=True):
        self.events.append(("write", path, verify))
        if path == RAM + "/input":
            self.input = data
            return
        if path != RAM + "/go" or verify:
            raise AssertionError("Only the consumed commit flag skips readback")
        match = re.fullmatch(rb"printf '%s\\n' '([^']+)'\n(.*)\n", self.input, re.S)
        if match is None:
            raise AssertionError("Unexpected fixture request")
        marker, command = match.groups()
        extent = re.search(rb"dd if=/dev/nflashaB0 .* bs=4096 skip=(\d+) count=(\d+)", command)
        if extent:
            if self.staged is not None:
                raise AssertionError("Next chunk started before the previous RAM slot was cleaned")
            offset, length = int(extent[1]) * 4096, int(extent[2]) * 4096
            self.events.append(("stage", offset, length))
            self.staged = self.source[offset:offset + length]
            result = hashlib.sha256(self.staged).hexdigest().encode() + b"  " + (RAM + "/chunk\n").encode()
            if self.corrupt_chunk == offset // CHUNK_BYTES:
                self.staged = bytes([self.staged[0] ^ 1]) + self.staged[1:]
        elif command == ("busybox rm -f " + RAM + "/chunk").encode():
            self.events.append(("cleanup",))
            self.staged = None
            result = b""
        else:
            raise AssertionError("Fixture command is outside the bounded read/cleanup profile")
        self.output = marker + b"\n" + result + b"\n__A6400A_RC__:0\n"


class RamChunkWorkerTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / "artifacts" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.source = bytes(range(256)) * ((2 * CHUNK_BYTES + 4096) // 256)

    def worker(self, files):
        worker = RamChunkWorker(files, RAM, NONCE, sleep=lambda _: None)
        worker.wait_ready()
        return worker

    def test_multiple_usb_file_pulls_reuse_one_bounded_ram_slot(self):
        files = FileFixture(self.source)
        result = self.worker(files).capture("/dev/nflashaB0", len(self.source), self.output)
        self.assertEqual((self.output / "nflashaB0.bin").read_bytes(), self.source)
        self.assertEqual(result["sha256"], hashlib.sha256(self.source).hexdigest())
        self.assertTrue(result["host_readback_verified"])
        self.assertEqual([row["bytes"] for row in result["chunks"]], [CHUNK_BYTES, CHUNK_BYTES, 4096])
        self.assertEqual([event for event in files.events if event[0] in ("stage", "cleanup")], [
            ("stage", 0, CHUNK_BYTES), ("cleanup",),
            ("stage", CHUNK_BYTES, CHUNK_BYTES), ("cleanup",),
            ("stage", 2 * CHUNK_BYTES, 4096), ("cleanup",),
        ])
        self.assertFalse((self.output / "nflashaB0.bin.partial").exists())

    def test_bad_chunk_keeps_verified_prefix_and_failed_bytes_without_advancing(self):
        files = FileFixture(self.source, corrupt_chunk=1)
        worker = self.worker(files)
        with self.assertRaisesRegex(ValueError, "hashes differ"):
            worker.capture("/dev/nflashaB0", len(self.source), self.output)
        self.assertFalse((self.output / "nflashaB0.bin").exists())
        self.assertEqual((self.output / "nflashaB0.bin.partial").read_bytes(), self.source[:CHUNK_BYTES])
        self.assertEqual((self.output / "chunk-1-unverified.bin").stat().st_size, CHUNK_BYTES)
        self.assertEqual(worker.capture_receipt["verified_bytes"], CHUNK_BYTES)
        self.assertEqual(len([event for event in files.events if event[0] == "stage"]), 2)

    def test_cancelled_capture_does_not_request_a_chunk(self):
        files = FileFixture(self.source)
        cancel = Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            self.worker(files).capture("/dev/nflashaB0", len(self.source), self.output, cancel=cancel)
        self.assertFalse(any(event[0] == "stage" for event in files.events))
        self.assertFalse((self.output / "nflashaB0.bin").exists())

    def test_identified_startup_failure_is_not_a_ready_worker_or_an_unresolved_timeout(self):
        class RefusedFiles(FileFixture):
            def read(self, path, limit):
                if path == RAM + "/ready":
                    raise JiritsuStatusError(0x10000006)
                if path == "/tmp/jiritsu/out.txt":
                    return f"__A6400A_WORKER_LOG__:{NONCE}\n__A6400A_INIT_ERROR__:31\n".encode()
                return super().read(path, limit)

        worker = RamChunkWorker(RefusedFiles(self.source), RAM, NONCE, sleep=lambda _: None)
        with self.assertRaisesRegex(RuntimeError, "initialization refused"):
            worker.wait_ready()
        self.assertTrue(worker.initialization_failed)
        self.assertFalse(worker.ready)
        self.assertFalse(worker.failed)

    def test_stale_output_times_out_without_replaying_or_accepting_the_next_command(self):
        files = FileFixture(self.source)
        worker = self.worker(files)
        clock = iter(range(0, 1000, 10))
        worker.clock = lambda: next(clock)
        files.output = b"stale"
        original_write = files.write

        def write(path, data, *, verify=True):
            original_write(path, data, verify=verify)
            files.output = b"previous sequence\n__A6400A_RC__:0\n"

        files.write = write
        with self.assertRaises(TimeoutError):
            worker.command("busybox rm -f " + RAM + "/chunk")
        committed = len([event for event in files.events if event[:2] == ("write", RAM + "/go")])
        self.assertEqual(committed, 1)
        with self.assertRaisesRegex(RuntimeError, "unresolved"):
            worker.command("busybox rm -f " + RAM + "/chunk")


class WireFixture:
    def __init__(self, data: bytes, *, status=0, sequence=1, short_write=False):
        payload = struct.pack("<I", status) + data
        self.parts = [HEADER.pack(len(payload), 0x40, sequence, 3, 0, 0, 0)]
        for offset in range(0, len(payload), TRANSFER_BYTES):
            piece = payload[offset:offset + TRANSFER_BYTES]
            self.parts.append(piece)
            if len(piece) % 512 == 0:
                self.parts.append(b"")
        self.short_write = short_write
        self.writes = []

    def write(self, data, *, timeout_ms):
        self.writes.append(data)
        return len(data) - int(self.short_write)

    def read(self, length, *, timeout_ms):
        part = self.parts.pop(0)
        if len(part) > length:
            self.parts.insert(0, part[length:])
        return part[:length]


class JiritsuFileWireTests(unittest.TestCase):
    def test_owner_policy_and_excluded_partition_fail_before_usb_or_lock_acquisition(self):
        args = SimpleNamespace(
            mode="jiritsu-dump", owner_authorized_jiritsu_workspace=False,
            confirm_firmware_101=True, partitions=["/dev/nflashaB0"], partition="/dev/nflashaB0",
        )
        with patch("tools.research.a6400a_jiritsu_worker.senser.host_lock") as lock:
            with self.assertRaisesRegex(ValueError, "owner-approved"):
                acquire(args)
            args.owner_authorized_jiritsu_workspace = True
            args.partitions = ["/dev/nflasha23"]
            with self.assertRaisesRegex(ValueError, "not allowed"):
                acquire(args)
            lock.assert_not_called()

    def test_chunk_reply_uses_exact_legacy_framing_and_consumes_only_its_terminators(self):
        payload = b"x" * CHUNK_BYTES
        wire = WireFixture(payload)
        files = JiritsuFiles(wire, RAM, [])
        self.assertEqual(files.read(RAM + "/chunk", CHUNK_BYTES), payload)
        self.assertEqual(wire.parts, [])
        self.assertTrue(files.in_sync)
        self.assertEqual(wire.writes[0][HEADER.size:HEADER.size + 4], b"\x01\x10\x08\0")
        with self.assertRaises(ValueError):
            files.read("/dev/nflashaB0", CHUNK_BYTES)
        self.assertEqual(len(wire.writes), 1)

    def test_bad_request_ack_or_reply_sequence_permanently_retires_the_channel(self):
        for wire in (WireFixture(b"x", short_write=True), WireFixture(b"x", sequence=2)):
            with self.subTest(short=wire.short_write):
                files = JiritsuFiles(wire, RAM, [])
                with self.assertRaises((OSError, ValueError)):
                    files.read("/version.txt", 64)
                self.assertFalse(files.in_sync)
                with self.assertRaisesRegex(RuntimeError, "unresolved"):
                    files.read("/version.txt", 64)
                self.assertEqual(len(wire.writes), 1)

    def test_explicit_stat_failure_is_not_empty_file_success_or_a_desynchronised_transfer(self):
        files = JiritsuFiles(WireFixture(b"", status=0x10000006), RAM, [])
        with self.assertRaises(JiritsuStatusError) as caught:
            files.read("/version.txt", 64)
        self.assertEqual(caught.exception.status, 0x10000006)
        self.assertTrue(files.in_sync)

    def test_owned_cleanup_refuses_changed_bytes_before_any_delete(self):
        wire = WireFixture(b"other")
        files = JiritsuFiles(wire, RAM, [])
        with self.assertRaisesRegex(ValueError, "refusing deletion"):
            files.remove_owned("/tmp/jiritsu/run.sh", b"owned")
        self.assertEqual(len(wire.writes), 1)
        self.assertEqual(struct.unpack_from("<HH", wire.writes[0], HEADER.size), (0x1001, 8))

    def test_inventory_uses_measured_geometry_and_excludes_internal_data_region(self):
        data = (b"__UPTIME__\n123.0 0.0\n__PARTITIONS__\n"
                b"179 0 8 nflashaB0\n179 1 4 nflasha23\n"
                b"__SIZES__\nnflashaB0 16\nnflasha23 8\n")
        self.assertEqual(parse_inventory(data), (123.0, {"/dev/nflashaB0": 8192}))
        with self.assertRaisesRegex(ValueError, "geometry disagree"):
            parse_inventory(data.replace(b"nflashaB0 16", b"nflashaB0 32"))

    def test_native_configuration_is_bound_to_its_nonce_and_explicit_private_reader_policy(self):
        with self.assertRaisesRegex(ValueError, "private tmpfs"):
            probe_config(NONCE, partition_dump=True)
        config = probe_config(NONCE, private_tmpfs=True, partition_dump=True)
        files = JiritsuFiles(WireFixture(b""), "/tmp/jiritsu/a6400a-" + NONCE, [],
                             probe=True, private_tmpfs=True, partition_dump=True)
        with patch.object(files, "exchange", side_effect=[b"", b"", config]) as exchange:
            files.write(RAM_PROBE_CONFIG, config)
            self.assertEqual(exchange.call_args_list[1].args, (16, b"\x00\xff\x00" + config, 0))
        with patch.object(files, "exchange") as exchange:
            with self.assertRaisesRegex(ValueError, "nonce/mount/reader"):
                files.write(RAM_PROBE_CONFIG, probe_config("f" * 32, private_tmpfs=True, partition_dump=True))
            exchange.assert_not_called()


def native_ram_frame(*, device=256, stat_result=0):
    header = RAM_HEADER.pack(b"A6400A_RAM_V2", 321, 0, 0, 3, 0, 0, -2147483648)
    records = b"".join(RAM_RECORD.pack(path.encode(), stat_result, 0, device,
                                      EXT2_MAGIC, 1024, 8192, 4096, 0) for path in RAM_PATHS)
    return header + records + struct.pack("<I", 0x52414D21)


PRIVATE_RAM = "/tmp/jiritsu/a6400a-" + NONCE


def private_ram_frame(*, flags=14, blocks=512, operation=1, result=0, rollback=-2147483648):
    header = RAM_HEADER.pack(b"A6400A_RAM_V2", 321, 0, 0, 3, operation, result, rollback)
    first = RAM_RECORD.pack(PRIVATE_RAM.encode(), -2 if operation == 3 else 0, 0, 10,
                            TMPFS_MAGIC, 4096, blocks, 512, flags)
    rest = b"".join(RAM_RECORD.pack(path.encode(), 0, 0, 64519,
                                   EXT2_MAGIC, 1024, 4823, 271, 0) for path in RAM_PATHS[1:])
    return header + first + rest + struct.pack("<I", 0x52414D21)


RAM_DIAGNOSTIC = (
    f"__A6400A_WORKER_LOG__:{NONCE}\n"
    "Uid:\t0\t0\t0\t0\n"
    "UPTIME START 1.0\n"
    "SWAP_STATE Header=1 Active=0 Valid=1\n"
    "MI 1 1 0:1 / rootfs rootfs\n"
    "MI 2 1 1:0 / ext2 /dev/root\n"
    "P 1 0 8192 ram0\n"
    "B 1 ramdisk\n"
    "U ram0 MAJOR 1\nU ram0 MINOR 0\nU ram0 DEVNAME ram0\n"
    "S ram0 16384\n"
    f"__A6400A_PROBE_META__:{NONCE}\n"
).encode()


class RamProofTests(unittest.TestCase):
    def test_active_missing_or_unqualified_swap_cannot_be_treated_as_ram_only(self):
        self.assertEqual(prove_swap_inactive(RAM_DIAGNOSTIC)["active"], 0)
        for evidence in (
            b"", b"SWAP_STATE Header=1 Active=1 Valid=1\n",
            b"SWAP_STATE Header=0 Active=0 Valid=0\n",
        ):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                prove_swap_inactive(evidence)

    def test_private_mount_requires_actual_tmpfs_flags_and_exact_capacity(self):
        evidence = decode_native(private_ram_frame(), private_path=PRIVATE_RAM)
        proof = prove_network_ram(evidence, RAM_DIAGNOSTIC, private_path=PRIVATE_RAM)
        self.assertEqual((proof.kind, proof.path), ("private-tmpfs", PRIVATE_RAM))
        for frame in (private_ram_frame(flags=6), private_ram_frame(blocks=1024),
                      private_ram_frame(result=-19), private_ram_frame(operation=0)):
            with self.subTest(frame=frame[:RAM_HEADER.size]), self.assertRaises(ValueError):
                prove_network_ram(decode_native(frame, private_path=PRIVATE_RAM),
                                  RAM_DIAGNOSTIC, private_path=PRIVATE_RAM)

    def test_private_namespace_is_not_enabled_by_a_generic_worker(self):
        with self.assertRaises(ValueError):
            JiritsuFiles(WireFixture(b"x"), PRIVATE_RAM, [], private_tmpfs=True)
        self.assertEqual(
            JiritsuFiles(WireFixture(b"x"), PRIVATE_RAM, [], probe=True, private_tmpfs=True).ram_dir,
            PRIVATE_RAM,
        )

    def test_native_device_and_independent_ram_disk_evidence_resolve_overlapping_mounts(self):
        proof = prove_network_ram(decode_native(native_ram_frame()), RAM_DIAGNOSTIC)
        self.assertEqual(proof.kind, "ext2-on-ramdisk")
        self.assertEqual(proof.device, 256)
        self.assertEqual(proof.path, "/tmp_network")

    def test_flash_ext2_missing_device_identity_and_mismatched_geometry_are_not_ram(self):
        for frame, diagnostic in (
            (native_ram_frame(device=0xB307), RAM_DIAGNOSTIC),
            (native_ram_frame(stat_result=-5), RAM_DIAGNOSTIC),
            (native_ram_frame(), RAM_DIAGNOSTIC.replace(b"DEVNAME ram0", b"DEVNAME nflasha7")),
            (native_ram_frame(), RAM_DIAGNOSTIC.replace(b"S ram0 16384", b"S ram0 8192")),
            (native_ram_frame(), RAM_DIAGNOSTIC.replace(b"B 1 ramdisk", b"B 1 unknown")),
        ):
            with self.subTest(diagnostic=diagnostic), self.assertRaises(ValueError):
                prove_network_ram(decode_native(frame), diagnostic)

    def test_incomplete_or_uncommitted_native_evidence_is_never_decoded_as_zero_success(self):
        valid = native_ram_frame()
        for invalid in (valid[:-1], valid + b"\0", valid[:-4] + bytes(4)):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                decode_native(invalid)

    def test_probe_mode_rejects_partition_selection_before_host_lock(self):
        args = SimpleNamespace(mode="qualify-ram", owner_authorized_jiritsu_workspace=True,
                               confirm_firmware_101=True, partitions=["/dev/nflashaB0"],
                               partition="/dev/nflashaB0")
        with patch("tools.research.a6400a_jiritsu_worker.senser.host_lock") as lock:
            with self.assertRaisesRegex(ValueError, "cannot request firmware"):
                acquire(args)
            lock.assert_not_called()


class ProbeFixture:
    def __init__(self, *, device=256, corrupt=False, private=False):
        self.native = private_ram_frame() if private else native_ram_frame(device=device)
        self.ram_dir = PRIVATE_RAM if private else RAM
        self.private = private
        self.log = RAM_DIAGNOSTIC
        self.data = b""
        self.acks = []
        self.corrupt = corrupt
        self.reads = 0

    def read(self, path, limit):
        if path == "/tmp/jiritsu/out.txt":
            return self.log
        if path.endswith(".meta"):
            return self.native
        if path == self.ram_dir + "/chunk":
            self.reads += 1
            if self.corrupt and self.reads == 5:
                return self.data[:-1] + bytes([self.data[-1] ^ 1])
            return self.data
        raise AssertionError("Probe attempted an undeclared file or device read")

    def write(self, path, data, *, verify=True):
        if not path.endswith(".ack"):
            raise AssertionError("Probe attempted an undeclared upload")
        nonce, phase, device, magic = data.decode().split()
        if nonce != NONCE:
            raise AssertionError("Wrong probe nonce")
        self.acks.append(phase)
        if phase == "cleanup":
            self.data = b""
            if self.private:
                self.native = private_ram_frame(operation=3, rollback=0)
            self.log += f"UPTIME CLEAN 3.0\n__A6400A_PROBE_CLEAN__:{NONCE}\n".encode()
            return
        size = 4096 if phase == "4k" else CHUNK_BYTES
        self.data = (b" " * (4096 - 10) + b"A6400A_RAM") * (size // 4096)
        self.log += (
            f"UPTIME {phase} 2.0\n"
            f"__A6400A_PROBE_DATA__:{phase}:{NONCE}\n"
            + hashlib.sha256(self.data).hexdigest() + "  " + self.ram_dir + "/chunk\n"
        ).encode()


class NativeDumpFixture(ProbeFixture):
    def __init__(self, *, refuse_second=False):
        super().__init__(private=True)
        self.source = bytes(range(256)) * (CHUNK_BYTES // 256) + bytes(reversed(range(256))) * 16
        self.input = b""
        self.output = b""
        self.commands = []
        self.refuse_second = refuse_second

    def read(self, path, limit):
        if path == self.ram_dir + "/output":
            return self.output
        return super().read(path, limit)

    def write(self, path, data, *, verify=True):
        if path == self.ram_dir + "/input":
            self.input = data
            return
        if path != self.ram_dir + "/go":
            return super().write(path, data, verify=verify)
        if data != b"go\n" or verify:
            raise AssertionError("Native dump did not use its consumed commit marker")
        nonce, operation, sequence, device, magic, *arguments = self.input.decode().split()
        if (nonce, device, magic) != (NONCE, "10", str(TMPFS_MAGIC)):
            raise AssertionError("Native dump changed its qualified RAM binding")
        self.commands.append((operation, arguments))
        result = 0
        if operation == "remove":
            self.data = b""
            body = b""
        elif operation == "chunk":
            partition, offset, count, capacity = arguments
            if partition != "/dev/nflashaB0" or int(capacity) != len(self.source):
                raise AssertionError("Native dump requested an unadmitted source")
            if 8256 - len(self.data) // 1024 < 8192:
                result, body = 65, b"DUMP_REFUSED resident-slot memory guard\n"
            elif self.refuse_second and int(offset):
                result, body = 65, b"DUMP_REFUSED live-capacity Result=-1\n"
            else:
                self.data = self.source[int(offset):int(offset) + int(count)]
                body = (b"__A6400A_CHUNK__\n" + hashlib.sha256(self.data).hexdigest().encode()
                        + ("  " + self.ram_dir + "/chunk\n").encode())
        else:
            raise AssertionError("Native dump attempted an undeclared operation")
        self.output = (f"__A6400A_CMD_{NONCE}_{sequence}__\n".encode()
                       + body + f"\n__A6400A_RC__:{result}\n".encode())


class RamRoundtripTests(unittest.TestCase):
    def test_native_reader_cannot_start_before_the_six_verified_roundtrips(self):
        probe = RamWorkspaceProbe(ProbeFixture(private=True), PRIVATE_RAM, NONCE, {}, private_tmpfs=True)
        with self.assertRaisesRegex(ValueError, "six verified"):
            NativeRamChunkWorker(probe)

    def test_native_reader_saves_full_and_tail_chunks_without_shell_commands(self):
        files = NativeDumpFixture()
        probe = RamWorkspaceProbe(files, PRIVATE_RAM, NONCE, {}, private_tmpfs=True, sleep=lambda _: None)
        probe.run(self.output, self.output / "patterns")
        worker = NativeRamChunkWorker(probe)
        receipt = worker.capture("/dev/nflashaB0", len(files.source), self.output)
        self.assertEqual((self.output / receipt["file"]).read_bytes(), files.source)
        self.assertEqual([row["bytes"] for row in receipt["chunks"]], [CHUNK_BYTES, 4096])
        self.assertEqual([row[0] for row in files.commands], ["remove", "chunk", "remove", "chunk", "remove"])
        self.assertTrue(probe.report["ram_proof_slot_retired"])
        with self.assertRaisesRegex(ValueError, "shell commands"):
            worker.command("printf unqualified")
        before = len(files.commands)
        with self.assertRaises(ValueError):
            worker.capture("/dev/nflasha23", 4096, self.output)
        self.assertEqual(len(files.commands), before)
        probe.cleanup()
        self.assertTrue(probe.report["private_tmpfs_unmounted_and_removed"])

    def test_native_refusal_preserves_only_the_prior_verified_prefix(self):
        files = NativeDumpFixture(refuse_second=True)
        probe = RamWorkspaceProbe(files, PRIVATE_RAM, NONCE, {}, private_tmpfs=True, sleep=lambda _: None)
        probe.run(self.output, self.output / "patterns")
        worker = NativeRamChunkWorker(probe)
        with self.assertRaisesRegex(RuntimeError, "live-capacity"):
            worker.capture("/dev/nflashaB0", len(files.source), self.output)
        self.assertEqual(worker.capture_receipt["verified_bytes"], CHUNK_BYTES)
        self.assertEqual((self.output / "nflashaB0.bin.partial").read_bytes(), files.source[:CHUNK_BYTES])
        self.assertFalse((self.output / "nflashaB0.bin").exists())
        self.assertEqual(probe.report["native_chunk_commands"][-1]["status"], "refused")
        probe.cleanup()

    def test_poll_tolerates_transient_empty_file_status_before_atomic_commit(self):
        class EmptyThenReadyFiles(NativeDumpFixture):
            def __init__(self):
                super().__init__()
                self.transient_statuses = [0x10000008, 0x1000000A]

            def read(self, path, limit):
                if path == self.ram_dir + "/output" and self.transient_statuses:
                    raise JiritsuStatusError(self.transient_statuses.pop(0))
                return super().read(path, limit)

        files = EmptyThenReadyFiles()
        probe = RamWorkspaceProbe(files, PRIVATE_RAM, NONCE, {}, private_tmpfs=True, sleep=lambda _: None)
        probe.run(self.output, self.output / "patterns")
        worker = NativeRamChunkWorker(probe)
        receipt = worker.capture("/dev/nflashaB0", len(files.source), self.output)
        self.assertTrue(receipt["complete"])
        self.assertEqual(files.transient_statuses, [])
        probe.cleanup()

    def test_long_wire_receipt_preserves_bounded_cleanup_ownership_and_source_identity(self):
        report = {
            "status": "worker_initialization_refused", "identity": {"model": "ILCE-6400A"},
            "camera_file_writes": True, "loader_upload_attempted": True,
            "worker_ram_dir": PRIVATE_RAM, "worker_script_sha256": "f" * 64,
            "events": ["constructed wire event " * 5000],
        }
        source = self.output / "receipt.json"
        source.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "64-KiB"):
            load_receipt(source)
        owned_path = write_ownership_receipt(self.output, report)
        owned, _ = load_receipt(owned_path)
        self.assertEqual(owned["worker_ram_dir"], PRIVATE_RAM)
        self.assertEqual(owned["source_receipt_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertNotIn("events", owned)
        self.assertEqual(json.loads(source.read_text())["events"], report["events"])

    def test_private_ram_probe_verifies_all_reads_then_acknowledges_unmount_and_removal(self):
        files = ProbeFixture(private=True)
        report = {}
        probe = RamWorkspaceProbe(files, PRIVATE_RAM, NONCE, report, private_tmpfs=True, sleep=lambda _: None)
        result = probe.run(self.output, self.output / "received")
        self.assertEqual(len(result["roundtrips"]), 6)
        probe.cleanup()
        self.assertTrue(report["private_tmpfs_unmounted_and_removed"])
        self.assertEqual(report["private_tmpfs_cleanup_evidence"]["operation"], 3)

    def setUp(self):
        root = ROOT / "artifacts" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)

    def test_six_real_file_shapes_are_verified_and_saved_before_cleanup_authorization(self):
        files = ProbeFixture()
        report = {}
        probe = RamWorkspaceProbe(files, RAM, NONCE, report, sleep=lambda _: None)
        result = probe.run(self.output, self.output / "received")
        self.assertEqual([row["bytes"] for row in result["roundtrips"]], [4096] * 3 + [CHUNK_BYTES] * 3)
        self.assertEqual(files.acks, ["4k", "512k"])
        self.assertEqual(len(list((self.output / "received").glob("*.bin"))), 6)
        probe.cleanup()
        self.assertEqual(files.acks[-1], "cleanup")
        self.assertTrue(report["ram_probe_data_removed"])

    def test_unproved_ram_never_authorizes_a_pattern_write(self):
        files = ProbeFixture(device=0xB307)
        probe = RamWorkspaceProbe(files, RAM, NONCE, {}, sleep=lambda _: None)
        with self.assertRaises(ValueError):
            probe.run(self.output, self.output / "received")
        self.assertEqual(files.acks, [])
        self.assertFalse((self.output / "received").exists())

    def test_proved_private_tmpfs_with_active_swap_does_not_authorize_test_data(self):
        files = ProbeFixture(private=True)
        files.log = files.log.replace(b"SWAP_STATE Header=1 Active=0", b"SWAP_STATE Header=1 Active=1")
        probe = RamWorkspaceProbe(files, PRIVATE_RAM, NONCE, {}, private_tmpfs=True, sleep=lambda _: None)
        with self.assertRaisesRegex(ValueError, "no active swap"):
            probe.run(self.output, self.output / "received")
        self.assertEqual(files.acks, [])
        self.assertEqual(files.reads, 0)

    def test_corrupt_read_stops_further_transfers_and_retains_verified_files(self):
        files = ProbeFixture(corrupt=True)
        report = {}
        probe = RamWorkspaceProbe(files, RAM, NONCE, report, sleep=lambda _: None)
        with self.assertRaisesRegex(ValueError, "hashes differ"):
            probe.run(self.output, self.output / "received")
        self.assertEqual(files.reads, 5)
        self.assertEqual(len(report["ram_roundtrips"]), 4)
        self.assertEqual(len(list((self.output / "received").glob("*.bin"))), 5)
        self.assertEqual((self.output / "received" / report["ram_probe_unverified"]["file"]).stat().st_size, CHUNK_BYTES)


if __name__ == "__main__":
    unittest.main()
