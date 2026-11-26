from array import array
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest import mock

try:
    import usb.core
except ModuleNotFoundError:
    class _USBError(OSError):
        def __init__(self, strerror, error_code=None, errno=None):
            super().__init__(strerror)
            self.backend_error_code = error_code
            self.errno = errno

    class _USBTimeoutError(_USBError):
        pass

    class _Device:
        idVendor = 0
        idProduct = 0

        def get_active_configuration(self):
            return []

        def ctrl_transfer(self, *args, **kwargs):
            return 0

        def read(self, *args, **kwargs):
            return b""

        def write(self, *args, **kwargs):
            return 0

    usb = SimpleNamespace(
        core=SimpleNamespace(USBError=_USBError, USBTimeoutError=_USBTimeoutError, Device=_Device)
    )

from project_paths import PROJECT_ROOT
from tools.research import a6400a_fixed_acquire as acquisition
from tools.research.a6400a_fixed_reader import CAPACITY, FRAME_BYTES, FixedPartitionReader, FixedReadError, HEADER
from tools.research.a6400a_senser_acquire import AUTH, START, STOP
from tools.research.a6400a_transfer_controls import FramedTransport, HashSink, fixture_bytes


class InterfaceFixture(list):
    bInterfaceNumber = 0
    bAlternateSetting = 0
    bInterfaceClass = 0xFF


def fixture_metadata():
    baseline = {"vid": 0x054C, "pid": 0x0F0F, "service": "USBSTOR",
                "status": "OK", "error": 0, "locations": ["constructed-port"]}
    return baseline, dict(baseline, pid=0x0336, service="WinUSB")


def fixture_receipts():
    baseline, _ = fixture_metadata()
    timestamp = datetime.now(timezone.utc).isoformat()
    original = {"action": "inspect", "status": "baseline_observed",
                "started_utc": timestamp, "metadata_before": baseline}
    identity = {
        "status": "passed", "updater_initialization": False, "selected_disk_index": 3,
        "metadata": {"usb_pair": "054c:0f0f", "service": "USBSTOR", "status": "OK",
                     "config_error": 0, "camera_count": 1,
                     "disks": [{"disk_index": 3, "lun": 0, "parent_identity_verified": True,
                                "model": "Sony DSC USB Device"}]},
        "result": {"model_info": {"model": "ILCE-6400A", "product_code_wire_hex": "0032858730"},
                   "model_transaction": {"status": "model_reply_received", "ended_utc": timestamp}},
    }
    return original, identity


def fixture_handles():
    device = mock.create_autospec(usb.core.Device, instance=True)
    device.idVendor, device.idProduct = 0x054C, 0x0336
    endpoints = InterfaceFixture([
        SimpleNamespace(bmAttributes=2, bEndpointAddress=0x84, wMaxPacketSize=512),
        SimpleNamespace(bmAttributes=2, bEndpointAddress=0x03, wMaxPacketSize=512),
    ])
    device.get_active_configuration.return_value = [endpoints]
    driver = mock.Mock()
    driver.getId.return_value = (0x054C, 0x0336)
    driver.backend = SimpleNamespace(dev=device)
    driver.epIn, driver.epOut = [0x84], [0x03]
    return device, driver, endpoints


class FixedUsbTransportTests(unittest.TestCase):
    def adapter(self):
        baseline, metadata = fixture_metadata()
        device, driver, endpoints = fixture_handles()
        events = []
        transport = acquisition.FixedUsbTransport(device, driver, metadata, baseline, events)
        return transport, device, driver, endpoints, events

    def test_primary_endpoint_selection_real_counts_and_empty_arrays_reach_the_reader(self):
        transport, device, driver, _, events = self.adapter()
        wire = FramedTransport([HEADER.pack(512, 0x40, 1, 3, 0, 0, 0)])

        def write(ep, data, timeout):
            self.assertEqual(ep, 0x03)
            return wire.write(data, timeout_ms=timeout)

        def read(ep, size, timeout):
            self.assertEqual(ep, 0x84)
            return array("B", wire.read(size, timeout_ms=timeout))

        device.write.side_effect = write
        device.read.side_effect = read
        sink = HashSink()
        receipt = FixedPartitionReader(transport, expected_bytes=512).read(sink)
        self.assertTrue(receipt.wire_complete)
        self.assertFalse(receipt.cleanup_verified)
        self.assertEqual(sink.size, 512)
        self.assertEqual(device.write.call_args.args[0], 0x03)
        self.assertTrue(all(call.args[0] == 0x84 for call in device.read.call_args_list))
        self.assertEqual([row["acknowledged_bytes"] for row in events
                          if row["operation"] == "fixed_request_result"], [16])
        driver.write.assert_not_called()
        driver.reset.assert_not_called()

    def test_wrong_identity_location_handle_or_endpoint_fails_before_bulk_io(self):
        for variant in ("pid", "location", "handle", "alternate", "packet", "primary"):
            with self.subTest(variant=variant):
                baseline, metadata = fixture_metadata()
                device, driver, endpoints = fixture_handles()
                if variant == "pid":
                    metadata["pid"] = 0x02A9
                elif variant == "location":
                    metadata["locations"] = ["other-port"]
                elif variant == "handle":
                    driver.backend.dev = object()
                elif variant == "alternate":
                    endpoints.bAlternateSetting = 1
                elif variant == "packet":
                    endpoints[0].wMaxPacketSize = 64
                else:
                    driver.epIn = [0x81]
                with self.assertRaises(ValueError):
                    acquisition.FixedUsbTransport(device, driver, metadata, baseline, [])
                device.read.assert_not_called()
                device.write.assert_not_called()

    def test_unacknowledged_or_short_write_never_becomes_a_full_request(self):
        for count in (None, 0, 15, 17):
            with self.subTest(count=count):
                transport, device, _, _, _ = self.adapter()
                device.write.return_value = count
                reader = FixedPartitionReader(transport, expected_bytes=512)
                with self.assertRaises(FixedReadError) as caught:
                    reader.read(HashSink())
                self.assertTrue(caught.exception.receipt["request_attempted"])
                self.assertFalse(caught.exception.receipt["request_sent"])
                self.assertFalse(transport.acknowledged)
                self.assertEqual(device.write.call_count, 1)
                device.read.assert_not_called()

    def test_only_one_literal_request_and_bounded_reads_are_permitted(self):
        transport, device, _, _, _ = self.adapter()
        with self.assertRaises(ValueError):
            transport.read(12, timeout_ms=10)
        with self.assertRaises(ValueError):
            transport.write(b"arbitrary command", timeout_ms=10)
        device.write.assert_not_called()
        device.write.return_value = 16
        transport.write(acquisition.REQUEST, timeout_ms=10)
        with self.assertRaises(ValueError):
            transport.write(acquisition.REQUEST, timeout_ms=10)
        for size, timeout in ((0, 10), (32769, 10), (12, 0), (12, 2001)):
            with self.subTest(size=size, timeout=timeout), self.assertRaises(ValueError):
                transport.read(size, timeout_ms=timeout)
        self.assertEqual(device.write.call_count, 1)
        device.read.assert_not_called()

    def test_read_budget_and_real_deadline_are_not_extended(self):
        baseline, metadata = fixture_metadata()
        device, driver, _ = fixture_handles()
        now = [0.0]
        transport = acquisition.FixedUsbTransport(
            device, driver, metadata, baseline, [], clock=lambda: now[0])
        device.write.return_value = 16
        transport.write(acquisition.REQUEST, timeout_ms=2000)
        now[0] = 119.9999
        device.read.return_value = array("B", b"x")
        transport.read(12, timeout_ms=2000)
        self.assertEqual(device.read.call_args.kwargs["timeout"], 1)
        now[0] = 120.0
        with self.assertRaises(TimeoutError):
            transport.read(12, timeout_ms=2000)
        self.assertEqual(device.read.call_count, 1)

    def test_non_byte_or_oversized_buffers_do_not_become_zero_filled_payload(self):
        for result in (512, array("I", [1]), b"x" * 13):
            with self.subTest(result=type(result).__name__):
                transport, device, _, _, _ = self.adapter()
                device.write.return_value = 16
                device.read.return_value = result
                with self.assertRaises(FixedReadError) as caught:
                    FixedPartitionReader(transport, expected_bytes=512).read(HashSink())
                self.assertFalse(caught.exception.receipt["wire_complete"])
                self.assertEqual(caught.exception.receipt["payload_bytes"], 0)


class FixedAcquisitionTests(unittest.TestCase):
    def test_receipt_identity_hashes_the_exact_bounded_input_and_rejects_other_namespaces(self):
        root = PROJECT_ROOT / "artifacts" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fixed-receipt-", dir=root) as folder:
            path = Path(folder) / "receipt.json"
            raw = b'{"scope":"constructed metadata"}\n'
            path.write_bytes(raw)
            record, digest = acquisition.load_receipt(path)
            self.assertEqual(record, {"scope": "constructed metadata"})
            self.assertEqual(digest, hashlib.sha256(raw).hexdigest())
            path.write_bytes(b" " * 65537)
            with self.assertRaisesRegex(ValueError, "64-KiB"):
                acquisition.load_receipt(path)
            with self.assertRaisesRegex(ValueError, "repository artifacts"):
                acquisition.load_receipt(PROJECT_ROOT / "README.md")

    def test_identity_receipts_require_fresh_measured_model_and_consistent_parent(self):
        for variant in ("stale", "future", "model", "updater", "disk", "baseline"):
            with self.subTest(variant=variant):
                baseline, identity = fixture_receipts()
                if variant in ("stale", "future"):
                    shift = timedelta(minutes=-31 if variant == "stale" else 1)
                    identity["result"]["model_transaction"]["ended_utc"] = (
                        datetime.now(timezone.utc) + shift).isoformat()
                elif variant == "model":
                    identity["result"]["model_info"]["model"] = "ILCE-6400"
                elif variant == "updater":
                    identity["updater_initialization"] = True
                elif variant == "disk":
                    identity["selected_disk_index"] = 8
                else:
                    baseline["status"] = "service_entered"
                with self.assertRaises(ValueError):
                    acquisition.admit_receipts(baseline, identity)

    def scenario(self, *, failure=None, cancel_transfer=False, pipe=False, repeat=False,
                 size=65536, signals=False, no_space=False):
        root = PROJECT_ROOT / "artifacts" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fixed-acquire-", dir=root) as folder:
            folder = Path(folder)
            baseline, identity = fixture_receipts()
            baseline_path, identity_path = folder / "baseline.json", folder / "identity.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            identity_path.write_text(json.dumps(identity), encoding="utf-8")
            _, metadata = fixture_metadata()
            if failure == "hid":
                metadata.update(pid=0x0994, service="HidUsb")
            elif failure == "normal":
                metadata.update(pid=0x0F0F)
            device, driver, _ = fixture_handles()
            replies = []
            for _ in range(3):
                replies.extend((AUTH.pack(~8 & 0xFFFF, 0, b"constructed challenge"),
                                AUTH.pack(~4 & 0xFFFF, 0, b"")))
            replies.append(AUTH.pack(~6 & 0xFFFF, 0, b"\x01"))
            driver.read.side_effect = replies
            wire_headers = [HEADER.pack(size - offset, 0x40, 1, 3, 0, 0, 0)
                            for offset in range(0, size, FRAME_BYTES)]
            if failure == "header":
                wire_headers = [HEADER.pack(0, 0x40, 1, 3, 0, 0, 0x80)]
            wire = FramedTransport(wire_headers, max_fragment=100 if failure == "fragment" else 32768)
            event = Event()
            calls = [0]
            lock_active = [False]
            history = []
            installed_signal = [None]
            old_signal = object()

            @contextmanager
            def host_lock():
                lock_active[0] = True
                history.append("lock-enter")
                try:
                    yield
                finally:
                    lock_active[0] = False
                    history.append("lock-exit")

            def read(ep, count, timeout):
                calls[0] += 1
                self.assertTrue(lock_active[0])
                self.assertEqual(ep, 0x84)
                if failure == "timeout" and calls[0] == 4:
                    raise usb.core.USBTimeoutError("constructed timeout")
                if failure == "fragment" and calls[0] == 3:
                    raise usb.core.USBTimeoutError("constructed mid-fragment timeout")
                if cancel_transfer and calls[0] == 2:
                    if signals:
                        installed_signal[0](acquisition.signal.SIGINT, None)
                    else:
                        event.set()
                return array("B", wire.read(count, timeout_ms=timeout))

            def write(ep, data, timeout):
                self.assertTrue(lock_active[0])
                self.assertEqual(ep, 0x03)
                return wire.write(data, timeout_ms=timeout)

            class GenericError(Exception):
                pass

            def control(request, value, index, data):
                self.assertTrue(lock_active[0])
                self.assertEqual(data, b"")
                self.assertIn((request, value, index), (START, STOP))
                history.append("stop" if (request, value, index) == STOP else "start")
                if pipe and (request, value, index) == STOP:
                    try:
                        raise usb.core.USBError("constructed PIPE", error_code=-9)
                    except usb.core.USBError:
                        raise GenericError()

            def dispose(handle):
                self.assertTrue(lock_active[0])
                self.assertIs(handle, device)
                history.append("dispose")

            def wait_mode(*args):
                self.assertTrue(lock_active[0])
                self.assertEqual(args, ((0x0F0F,), ["constructed-port"]))
                history.append("normal-observed")
                return baseline["metadata_before"]

            def signal_handler(number, handler):
                self.assertEqual(number, acquisition.signal.SIGINT)
                self.assertTrue(lock_active[0])
                if handler is old_signal:
                    history.append("signal-restored")
                else:
                    installed_signal[0] = handler
                    history.append("signal-installed")
                return old_signal

            device.read.side_effect, device.write.side_effect = read, write
            driver.vendorRequestOut.side_effect = control
            modules = {"generic": SimpleNamespace(GenericUsbException=GenericError),
                       "core": usb.core, "constants": SimpleNamespace(
                           senserKeysSha1=[b"fixture"] * 3, senserKeysSha256=[b"fixture"] * 3),
                       "crypto": mock.Mock(), "util": SimpleNamespace(dispose_resources=dispose)}
            out = folder / "capture"
            arguments = ["--baseline", str(baseline_path), "--identity-receipt", str(identity_path),
                         "--confirm-firmware-101", "--output-dir", str(out)]
            if pipe:
                arguments.append("--permit-control-pipe")
            with mock.patch.multiple(
                    acquisition.senser, host_lock=host_lock,
                    discover=mock.Mock(return_value=metadata), load_pmca=mock.Mock(return_value=modules),
                    connect=mock.Mock(return_value=(device, driver)), wait_mode=wait_mode,
            ), mock.patch.object(acquisition, "CAPACITY", size), mock.patch.object(
                    acquisition, "ATTEMPT_ROOT", folder / "attempts"), mock.patch(
                    "builtins.print"), mock.patch.object(acquisition.signal, "signal", side_effect=signal_handler), mock.patch.object(
                    acquisition.shutil, "disk_usage", return_value=SimpleNamespace(
                        free=0 if no_space else size + 16 * 1024 * 1024)):
                result = acquisition.main(arguments, cancel=None if signals else event)
                report = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
                paths = sorted(path.name for path in out.iterdir())
                data = (out / "nflasha5.bin").read_bytes() if (out / "nflasha5.bin").is_file() else None
                again = None
                if repeat:
                    arguments[-1] = str(folder / "second-capture")
                    second_result = acquisition.main(arguments, cancel=Event())
                    second_report = json.loads(
                        (folder / "second-capture" / "receipt.json").read_text(encoding="utf-8"))
                    again = second_result, second_report
            return result, report, paths, data, history, device, driver, again

    def test_complete_constructed_session_reads_hashes_and_exits_under_one_lock(self):
        result, report, paths, data, history, device, driver, _ = self.scenario()
        self.assertEqual(result, 0)
        self.assertEqual(report["status"], "fixed_partition_captured_exit_acknowledged")
        self.assertEqual(data, fixture_bytes(0, 65536))
        self.assertEqual(report["captures"][0]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertTrue(report["captures"][0]["host_hash_verified"])
        self.assertFalse(report["cleanup_verified"])
        self.assertFalse(report["all_partitions_captured"])
        self.assertIn("before-fixed-request.json", paths)
        self.assertNotIn("nflasha5.bin.partial", paths)
        self.assertEqual(history, ["lock-enter", "start", "stop", "dispose", "normal-observed", "lock-exit"])
        self.assertEqual(driver.read.call_count, 7)
        self.assertEqual(driver.write.call_count, 7)
        self.assertEqual(device.write.call_count, 1)

    def test_exact_sixty_mib_profile_is_saved_and_read_back_through_the_real_adapter_api(self):
        result, report, _, data, _, device, _, _ = self.scenario(size=CAPACITY)
        self.assertEqual(result, 0)
        self.assertEqual(len(data), CAPACITY)
        self.assertEqual(report["transfer"]["frames"], 60)
        self.assertEqual(report["transfer"]["zero_length_packets"], 1920)
        self.assertEqual(report["transfer"]["payload_bytes"], 62_914_560)
        self.assertEqual(report["captures"][0]["bytes"], 62_914_560)
        self.assertEqual(report["captures"][0]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(device.read.call_count, 3900)
        self.assertEqual(device.write.call_count, 1)

    def test_incomplete_or_refused_transfer_preserves_partial_and_defers_unqualified_stop(self):
        for failure in ("timeout", "header", "fragment"):
            with self.subTest(failure=failure):
                result, report, paths, data, history, _, _, _ = self.scenario(failure=failure)
                self.assertEqual(result, 2)
                self.assertEqual(report["status"], "blocked")
                self.assertIsNone(data)
                self.assertIn("nflasha5.bin.partial", paths)
                self.assertFalse(report["exit_control_attempted"])
                self.assertTrue(report["recovery_required"])
                self.assertFalse(report["transfer"]["wire_complete"])
                if failure == "fragment":
                    self.assertEqual(report["transfer"]["output_bytes"], 100)
                self.assertNotIn("stop", history)
                self.assertIn("dispose", history)

    def test_unexpected_hid_requires_attention_without_any_command_or_driver_change(self):
        for failure, recovery in (("hid", True), ("normal", False)):
            with self.subTest(failure=failure):
                result, report, _, _, history, device, driver, _ = self.scenario(failure=failure)
                self.assertEqual(result, 2)
                self.assertEqual(report["recovery_required"], recovery)
                self.assertEqual(report["events"], [])
                self.assertEqual(report["captures"], [])
                self.assertNotIn("start", history)
                device.write.assert_not_called()
                driver.vendorRequestOut.assert_not_called()

    def test_cooperative_cancel_drains_before_exit_without_promoting_partial_output(self):
        result, report, paths, data, history, _, _, _ = self.scenario(cancel_transfer=True)
        self.assertEqual(result, 2)
        self.assertEqual(report["transfer"]["status"], "cancelled_drained")
        self.assertTrue(report["transfer"]["wire_complete"])
        self.assertEqual(report["captures"], [])
        self.assertIsNone(data)
        self.assertIn("nflasha5.bin.partial", paths)
        self.assertIn("stop", history)
        self.assertFalse(report["cleanup_verified"])

    def test_sigint_uses_cooperative_drain_and_restores_the_previous_handler(self):
        result, report, _, _, history, _, _, _ = self.scenario(cancel_transfer=True, signals=True)
        self.assertEqual(result, 2)
        self.assertEqual(report["transfer"]["status"], "cancelled_drained")
        self.assertTrue(report["transfer"]["wire_complete"])
        self.assertTrue(report["cancel_requested"])
        self.assertEqual(history.count("signal-installed"), 1)
        self.assertEqual(history.count("signal-restored"), 1)
        self.assertLess(history.index("stop"), history.index("signal-restored"))

    def test_insufficient_host_space_sends_no_fixed_request_or_one_shot_marker(self):
        result, report, paths, _, history, device, _, _ = self.scenario(no_space=True)
        self.assertEqual(result, 2)
        self.assertEqual(report["captures"], [])
        self.assertNotIn("one_shot_marker", report)
        self.assertNotIn("nflasha5.bin.partial", paths)
        device.write.assert_not_called()
        self.assertIn("stop", history)

    def test_pipe_exit_does_not_discard_real_bytes_or_claim_acknowledged_recovery(self):
        result, report, _, data, history, _, _, _ = self.scenario(pipe=True)
        self.assertEqual(result, 2)
        self.assertEqual(report["status"], "fixed_partition_captured_recovery_required")
        self.assertEqual(len(data), 65536)
        self.assertTrue(report["normal_usb_observed"])
        self.assertFalse(report["exit_control_acknowledged"])
        self.assertTrue(report["recovery_required"])
        self.assertEqual(history.count("stop"), 1)
        self.assertEqual(report["unacknowledged_controls"][0]["backend_error_code"], -9)

    def test_same_baseline_cannot_replay_a_request_into_an_unresolved_session(self):
        _, _, _, _, history, device, _, again = self.scenario(repeat=True)
        result, report = again
        self.assertEqual(result, 2)
        self.assertTrue(report["prior_attempt_unresolved"])
        self.assertEqual(report["captures"], [])
        self.assertEqual(device.write.call_count, 1)
        self.assertEqual(history.count("start"), 1)
        self.assertEqual(history.count("stop"), 1)

    def test_firmware_confirmation_is_required_before_any_discovery_or_output_creation(self):
        with mock.patch.object(acquisition.senser, "discover") as discover, mock.patch(
                "sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                acquisition.main(["--baseline", "missing.json", "--identity-receipt", "missing.json",
                                  "--output-dir", str(PROJECT_ROOT / "artifacts" / "tests" / "not-created")])
            discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
