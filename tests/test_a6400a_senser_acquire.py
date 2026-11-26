import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

try:
    import usb.core
except ModuleNotFoundError:
    class _USBError(OSError):
        def __init__(self, strerror, error_code=None, errno=None):
            super().__init__(strerror)
            self.backend_error_code = error_code
            self.errno = errno

    usb = SimpleNamespace(core=SimpleNamespace(USBError=_USBError))

from project_paths import PROJECT_ROOT
from tools.research import a6400a_senser_acquire as acquisition
from tools.research.a6400a_senser_acquire import (
    AUTH, BoundedDriver, BoundedSink, PACKET, START, STOP, ReadOnlySenser, admit_device,
    authenticate, control_request, partition_names,
)


class BoundedSenserTests(unittest.TestCase):
    def driver(self):
        raw = mock.Mock()
        raw.getId.return_value = (0x054C, 0x0336)
        return raw, BoundedDriver(raw, [])

    def test_control_pipe_is_opt_in_and_never_reported_as_success(self):
        class GenericError(Exception):
            pass

        for permitted in (False, True):
            raw, driver = self.driver()

            def stalled(*args):
                try:
                    raise usb.core.USBError("fixture pipe", error_code=-9)
                except usb.core.USBError:
                    raise GenericError()

            raw.vendorRequestOut.side_effect = stalled
            report = {}
            if permitted:
                control_request(driver, START, GenericError, usb.core.USBError, True, report)
                self.assertEqual(report["unacknowledged_controls"][0]["backend_error_code"], -9)
            else:
                with self.assertRaises(GenericError):
                    control_request(driver, START, GenericError, usb.core.USBError, False, report)
            self.assertEqual(raw.vendorRequestOut.call_count, 1)

    def test_empty_transport_read_stops_instead_of_looping(self):
        raw, driver = self.driver()
        raw.read.return_value = b""
        with self.assertRaisesRegex(RuntimeError, "Empty"):
            driver.read(12)
        self.assertEqual(raw.read.call_count, 1)

    def test_authentication_requires_all_original_rounds_and_final_acceptance(self):
        raw, driver = self.driver()
        driver.begin("auth")
        replies = []
        for _ in range(3):
            replies.extend((AUTH.pack(~8 & 0xFFFF, 0, b"challenge"),
                            AUTH.pack(~4 & 0xFFFF, 0, b"")))
        replies.append(AUTH.pack(~6 & 0xFFFF, 0, b"\x01"))
        raw.read.side_effect = replies
        constants = SimpleNamespace(senserKeysSha1=[b"a"] * 3, senserKeysSha256=[b"b"] * 3)
        authenticate(driver, constants, mock.Mock())
        commands = [~struct.unpack_from(">H", call.args[0])[0] & 0xFFFF
                    for call in raw.write.call_args_list]
        self.assertEqual(commands, [1, 3, 1, 3, 1, 3, 5])
        self.assertEqual(raw.read.call_count, 7)

    def test_authentication_refusal_stops_without_retry(self):
        raw, driver = self.driver()
        driver.begin("auth")
        raw.read.return_value = AUTH.pack(~9 & 0xFFFF, 0, b"")
        with self.assertRaisesRegex(RuntimeError, "challenge status"):
            authenticate(driver, SimpleNamespace(), mock.Mock())
        self.assertEqual(raw.write.call_count, 1)
        self.assertEqual(raw.read.call_count, 1)

    def test_only_declared_file_read_is_transmitted(self):
        raw, driver = self.driver()
        driver.begin("file", 8192, "/proc/version")
        for command, path in ((1, "/proc/version"), (2, "/dev/nflasha1")):
            name = path.encode() + b"\0"
            body = struct.pack("<HH", command, len(name)) + name
            packet = PACKET.pack(len(body), 0xFF01, 1, 0, 0, 0, 0) + body
            with self.subTest(command=command, path=path), self.assertRaises(ValueError):
                driver.write(packet)
        raw.write.assert_not_called()

    def test_file_read_requires_matching_reply_and_real_data(self):
        raw, driver = self.driver()
        driver.begin("file", 8192, "/proc/version")
        payload = b"Linux version fixture\n"
        raw.read.side_effect = [PACKET.pack(len(payload), 0xFF01, 1, 0, 0, 0, 1), payload]
        sink = BoundedSink(4096)
        ReadOnlySenser(driver).readFile("/proc/version", sink)
        self.assertEqual(sink.stream.getvalue(), payload)
        self.assertEqual(raw.write.call_count, 1)
        self.assertEqual(raw.read.call_count, 2)

    def test_wrong_sequence_or_refusal_is_not_success(self):
        for function, sequence, version, status in (
            (0xFF01, 2, 0, 1), (0xFF01, 1, 0, 0x80), (0, 1, 0, 1), (0xFF01, 1, 3, 0x80),
        ):
            raw, driver = self.driver()
            driver.begin("file", 8192, "/proc/version")
            raw.read.return_value = PACKET.pack(0, function, sequence, version, 0, 0, status)
            with self.subTest(function=function, sequence=sequence, version=version,
                              status=status), self.assertRaises(RuntimeError):
                ReadOnlySenser(driver).readFile("/proc/version", BoundedSink(4096))
            headers = [event["header"] for event in driver.events
                       if event["operation"] == "file_reply_header"]
            self.assertEqual(headers, [{"size": 0, "function": function, "sequence": sequence,
                                        "version": version, "micon": 0, "offset": 0, "status": status}])

    def test_output_overrun_preserves_partial_and_fails(self):
        sink = BoundedSink(3)
        sink.write(b"ab")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            sink.write(b"cd")
        self.assertEqual(sink.stream.getvalue(), b"ab")
        self.assertEqual(sink.size, 2)

    def test_device_location_and_original_binding_are_required(self):
        baseline = {"vid": 0x054C, "pid": 0x0F0F, "service": "USBSTOR", "locations": ["port"]}
        metadata = {"vid": 0x054C, "pid": 0x0336, "service": "WinUSB",
                    "status": "OK", "error": 0, "locations": ["other"]}
        with self.assertRaisesRegex(ValueError, "location"):
            admit_device(metadata, baseline, "read")
        metadata["locations"] = ["port"]
        admit_device(metadata, baseline, "read")

    def test_partition_inventory_rejects_duplicates_and_no_capture_nodes(self):
        self.assertEqual(partition_names(b"252 1 8192 nflasha1\n252 126 4096 nflashaB0\n"),
                         ["nflasha1", "nflashaB0"])
        for data in (b"252 1 1 nflasha1\n252 1 1 nflasha1\n", b"250 1 1 mmca1\n"):
            with self.assertRaises(ValueError):
                partition_names(data)

    def probe(self, payload, status=1, exit_error=None):
        root = PROJECT_ROOT / "artifacts" / "tests"
        root.mkdir(parents=True, exist_ok=True)
        baseline = {"vid": 0x054C, "pid": 0x0F0F, "service": "USBSTOR",
                    "locations": ["fixture-port"], "status": "OK", "error": 0}
        metadata = dict(baseline, pid=0x0336, service="WinUSB")
        raw, _ = self.driver()
        raw.read.side_effect = [PACKET.pack(len(payload), 0xFF01, 1, 0, 0, 0, status), payload]
        if payload and len(payload) % 512 == 0:
            raw.read.side_effect = [PACKET.pack(len(payload), 0xFF01, 1, 0, 0, 0, status),
                                    payload, b"p" * 512]
        modules = {"generic": SimpleNamespace(GenericUsbException=RuntimeError),
                   "core": usb.core, "constants": SimpleNamespace(), "crypto": mock.Mock(),
                   "util": mock.Mock()}
        with tempfile.TemporaryDirectory(prefix="senser-probe-", dir=root) as folder:
            folder = Path(folder)
            baseline_path = folder / "baseline.json"
            baseline_path.write_text(json.dumps({"metadata_before": baseline}), encoding="utf-8")
            out = folder / "result"
            with mock.patch.multiple(
                acquisition, host_lock=mock.DEFAULT, discover=mock.DEFAULT,
                load_pmca=mock.DEFAULT, connect=mock.DEFAULT, authenticate=mock.DEFAULT,
                wait_mode=mock.DEFAULT,
            ) as patched, mock.patch("builtins.print"):
                patched["discover"].return_value = metadata
                patched["load_pmca"].return_value = modules
                patched["connect"].return_value = (object(), raw)
                patched["wait_mode"].return_value = baseline
                patched["wait_mode"].side_effect = exit_error
                result = acquisition.main(["--action", "probe", "--baseline", str(baseline_path),
                                           "--output-dir", str(out)])
            receipt = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
            files = sorted(path.name for path in out.iterdir())
        return result, receipt, files, raw

    def test_probe_reads_only_version_and_observes_exit_without_capturing_partitions(self):
        payload = b"Linux version fixture\n"
        result, receipt, files, raw = self.probe(payload)
        self.assertEqual(result, 0)
        self.assertEqual(receipt["status"], "file_read_verified")
        self.assertEqual(receipt["file_probe"]["bytes"], len(payload))
        self.assertEqual(receipt["captures"], [])
        self.assertEqual(files, ["proc-version.txt", "receipt.json"])
        self.assertEqual(raw.write.call_count, 1)
        self.assertEqual(raw.read.call_count, 2)
        self.assertEqual(raw.vendorRequestOut.call_args_list,
                         [mock.call(*START, b""), mock.call(*STOP, b"")])

    def test_probe_invalid_version_or_service_refusal_attempts_stop_and_fails(self):
        for payload, status in ((b"not a camera version", 1), (b"Linux version fixture\n", 0x80)):
            with self.subTest(payload=payload, status=status):
                result, receipt, files, raw = self.probe(payload, status)
                self.assertEqual(result, 2)
                self.assertEqual(receipt["status"], "blocked")
                self.assertEqual(receipt["captures"], [])
                self.assertEqual(receipt["metadata_final"]["pid"], 0x0F0F)
                self.assertNotIn("file_probe", receipt)
                self.assertEqual(files, ["receipt.json"])
                self.assertEqual(raw.write.call_count, 1)
                self.assertEqual(raw.vendorRequestOut.call_args_list[-1], mock.call(*STOP, b""))

    def test_probe_enforces_the_exact_4096_byte_output_boundary(self):
        prefix = b"Linux version "
        for size in (4096, 4097):
            with self.subTest(size=size):
                result, receipt, _, raw = self.probe(prefix + b"x" * (size - len(prefix)))
                if size == 4096:
                    self.assertEqual(result, 0)
                    self.assertEqual(receipt["file_probe"]["bytes"], size)
                else:
                    self.assertEqual(result, 2)
                    self.assertEqual(receipt["error_type"], "ValueError")
                    self.assertNotIn("file_probe", receipt)
                self.assertEqual(raw.vendorRequestOut.call_args_list[-1], mock.call(*STOP, b""))

    def test_probe_exit_timeout_preserves_read_evidence_but_does_not_report_completion(self):
        result, receipt, _, raw = self.probe(
            b"Linux version fixture\n", exit_error=TimeoutError("fixture exit not observed"))
        self.assertEqual(result, 2)
        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(receipt["file_probe"]["path"], "/proc/version")
        self.assertEqual(receipt["error_type"], "TimeoutError")
        self.assertEqual(raw.vendorRequestOut.call_args_list[-1], mock.call(*STOP, b""))


if __name__ == "__main__":
    unittest.main()
