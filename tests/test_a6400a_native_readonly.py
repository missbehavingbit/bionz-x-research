from contextlib import redirect_stderr
import ctypes
import io
import struct
import unittest
from unittest import mock

from tools.research.a6400a_native_readonly import (
    INQUIRY_CDB, MODEL_CDB, MODEL_REQUEST, A6400A, IDENTITIES, CameraIdentity,
    admit_request, build_discover, parse_model_info, NativeTransport, ScsiCommandError, ScsiPacket,
    SonyBusy, main, probe_identity, read_model_info, select_target,
)
from tools.research.a6400a_debugger_presence import (
    DEBUGGER_CDB, PresenceTransport, probe_debugger_channel,
)


def inquiry(vendor=b"Sony"):
    raw = bytearray(36)
    raw[4] = 31
    raw[8:16] = vendor.ljust(8)
    raw[16:32] = b"DSC".ljust(16)
    raw[32:36] = b"1.00"
    return bytes(raw)


def model_info(model=b"ILCE-6400A"):
    payload = struct.pack("<I", 0) + bytes(4) + bytes([len(model)]) + model
    payload += bytes.fromhex("0381030017") + b"SECR"
    return (struct.pack("<IHH8x", len(payload), 1, 1) + payload).ljust(8192, b"\0")


class TargetAdmissionTests(unittest.TestCase):
    def metadata(self):
        return {"usb_pair": "054c:0f0f", "service": "USBSTOR", "status": "OK",
                "config_error": 0, "camera_count": 1,
                "disks": [{"disk_index": 7, "lun": 0, "model": "Sony DSC USB Device",
                           "parent_identity_verified": True},
                          {"disk_index": 8, "lun": 1, "model": "Sony DSC USB Device",
                           "parent_identity_verified": True}]}

    def test_selects_parent_verified_lun_not_a_drive_letter_or_secondary_disk(self):
        self.assertEqual(select_target(self.metadata()), 7)

    def test_other_sony_device_or_changed_binding_is_rejected(self):
        for field, value in (("usb_pair", "054c:0994"), ("service", "WinUSB"),
                             ("camera_count", 2), ("config_error", 28)):
            metadata = self.metadata()
            metadata[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                select_target(metadata)

    def test_unverified_or_ambiguous_lun_is_rejected(self):
        metadata = self.metadata()
        metadata["disks"][0]["parent_identity_verified"] = False
        with self.assertRaisesRegex(ValueError, "parent-verified"):
            select_target(metadata)
        metadata = self.metadata()
        metadata["disks"].append(metadata["disks"][0].copy())
        with self.assertRaisesRegex(ValueError, "parent-verified"):
            select_target(metadata)


class CommandAdmissionTests(unittest.TestCase):
    def test_storage_and_unreviewed_debugger_commands_are_refused(self):
        for command in (b"\x28" + bytes(9), b"\x2a" + bytes(9),
                        b"\x7a\x23" + bytes(10), bytes(6)):
            with self.subTest(command=command), self.assertRaises(ValueError):
                admit_request(command, None, 36)

    def test_only_exact_model_query_payload_is_admitted(self):
        self.assertEqual(admit_request(MODEL_CDB, MODEL_REQUEST, 0),
                         "sony_get_model_info_request")
        modified = bytearray(MODEL_REQUEST)
        modified[4] = 2
        with self.assertRaises(ValueError):
            admit_request(MODEL_CDB, bytes(modified), 0)
        with self.assertRaises(ValueError):
            admit_request(MODEL_CDB, None, 4096)

    def test_wrong_inquiry_identity_prevents_any_sony_vendor_command(self):
        class Fake:
            calls = []

            def exchange(self, command, **kwargs):
                self.calls.append(command)
                return inquiry(b"Other")

        fake = Fake()
        with self.assertRaises(ValueError):
            probe_identity(fake, model_info=True)
        self.assertEqual(fake.calls, [INQUIRY_CDB])

    def test_identity_probe_sends_only_three_reviewed_commands_and_omits_serial(self):
        class Fake:
            def __init__(self):
                self.calls = []

            def exchange(self, command, *, data_out=None, read_size=0):
                admit_request(command, data_out, read_size)
                self.calls.append(command)
                return inquiry() if command == INQUIRY_CDB else b"" if data_out else model_info()

        fake = Fake()
        result = probe_identity(fake, model_info=True)
        self.assertEqual(fake.calls, [INQUIRY_CDB, MODEL_CDB, MODEL_CDB])
        self.assertEqual(result["model_info"]["model"], "ILCE-6400A")
        self.assertNotIn("SECR", repr(result))
        self.assertFalse(result["model_info"]["serial_retained"])


class DebuggerPresenceTests(unittest.TestCase):
    def test_default_transport_refuses_channel_before_any_device_ioctl(self):
        transport = object.__new__(NativeTransport)
        transport.handle = 1
        transport._ioctl = mock.Mock()
        with self.assertRaises(ValueError):
            transport.exchange(DEBUGGER_CDB, read_size=4096)
        transport._ioctl.assert_not_called()

    def test_presence_read_uses_one_native_in_transfer_with_the_exact_frame_bound(self):
        transport = object.__new__(PresenceTransport)
        transport.handle = 1
        transport.operations = []
        calls = []

        def missing_channel(code, input_buffer, input_size, output_buffer, output_size):
            packet = ctypes.cast(output_buffer, ctypes.POINTER(ScsiPacket)).contents
            calls.append((packet.sptd.DataIn, packet.sptd.DataTransferLength,
                          bytes(packet.sptd.Cdb)[:packet.sptd.CdbLength]))
            packet.sptd.ScsiStatus = 2
            packet.sptd.DataTransferLength = 0
            packet.sense[2], packet.sense[12], packet.sense[13] = 5, 0x20, 0

        transport._ioctl = missing_channel
        result = probe_debugger_channel(transport)
        self.assertEqual(calls, [(1, 4096, DEBUGGER_CDB)])
        self.assertEqual(len(transport.operations), 1)
        self.assertEqual(result["status"], "illegal_command_no_usable_channel")
        self.assertFalse(result["debugger_verified"])

    def test_presence_transport_refuses_out_other_sizes_and_storage_commands(self):
        transport = object.__new__(PresenceTransport)
        transport.handle = 1
        transport._ioctl = mock.Mock()
        for command, data_out, read_size in (
                (DEBUGGER_CDB, bytes(4096), 0), (DEBUGGER_CDB, None, 512),
                (b"\x7a\x24" + bytes(10), None, 4096),
                (b"\x28" + bytes(9), None, 4096), (b"\x2a" + bytes(9), bytes(4096), 0)):
            with self.subTest(command=command, read_size=read_size), self.assertRaises(ValueError):
                transport.exchange(command, data_out=data_out, read_size=read_size)
        transport._ioctl.assert_not_called()

    def test_missing_or_busy_channel_is_one_observation_not_a_debugger(self):
        for error, expected in (
                (ScsiCommandError(2, [5, 0x20, 0], 0), "illegal_command_no_usable_channel"),
                (SonyBusy("busy"), "sony_busy_no_in_response")):
            with self.subTest(expected=expected):
                transport = mock.Mock()
                transport.exchange.side_effect = error
                result = probe_debugger_channel(transport)
                self.assertEqual(result["status"], expected)
                self.assertFalse(result["debugger_verified"])
                transport.exchange.assert_called_once_with(DEBUGGER_CDB, read_size=4096)

    def test_other_sense_or_partial_illegal_command_is_not_reinterpreted(self):
        for error in (ScsiCommandError(2, [6, 0x29, 0], 0),
                      ScsiCommandError(2, [5, 0x20, 0], 4), OSError("transport failed")):
            with self.subTest(error=error):
                transport = mock.Mock()
                transport.exchange.side_effect = error
                with self.assertRaises(type(error)):
                    probe_debugger_channel(transport)
                self.assertEqual(transport.exchange.call_count, 1)

    def test_unsolicited_response_is_not_a_handshake_and_short_data_is_rejected(self):
        transport = mock.Mock()
        transport.exchange.return_value = bytes(4096)
        result = probe_debugger_channel(transport)
        self.assertEqual(result["status"], "response_available_not_a_handshake")
        self.assertFalse(result["debugger_verified"])
        self.assertEqual(result["response_bytes"], 4096)
        transport.exchange.return_value = bytes(32)
        with self.assertRaisesRegex(ValueError, "Short debugger-channel"):
            probe_debugger_channel(transport)


class ModelResponseTests(unittest.TestCase):
    def test_truncated_or_overstated_identity_is_rejected(self):
        for raw in (bytes(8), struct.pack("<IHH8x", 8192, 1, 1),
                    model_info()[:24]):
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                parse_model_info(raw)

    def test_regular_a6400_is_not_silently_accepted_as_a6400a(self):
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            parse_model_info(model_info(b"ILCE-6400"))

    def test_another_subcommand_or_request_direction_is_not_a_model_reply(self):
        for command, direction in ((2, 1), (1, 0)):
            raw = bytearray(model_info())
            struct.pack_into("<HH", raw, 4, command, direction)
            with self.subTest(command=command, direction=direction), self.assertRaisesRegex(
                    ValueError, "response command/direction"):
                parse_model_info(bytes(raw))


class BusyReadTests(unittest.TestCase):
    @mock.patch("tools.research.a6400a_native_readonly.time.sleep")
    def test_busy_retries_only_the_read_and_never_resubmits_the_request(self, sleep):
        class Fake:
            def __init__(self):
                self.commands = []
                self.reads = 0

            def exchange(self, command, *, data_out=None, read_size=0):
                admit_request(command, data_out, read_size)
                self.commands.append((command, data_out is not None))
                if command == INQUIRY_CDB:
                    return inquiry()
                if data_out is not None:
                    return b""
                self.reads += 1
                if self.reads == 1:
                    raise SonyBusy("busy")
                return model_info()

        fake = Fake()
        result = probe_identity(fake, model_info=True)
        self.assertEqual(result["model_info"]["model"], "ILCE-6400A")
        self.assertEqual(sum(write for _, write in fake.commands), 1)
        self.assertEqual(fake.reads, 2)

    @mock.patch("tools.research.a6400a_native_readonly.time.sleep")
    def test_permanent_busy_is_bounded_to_forty_read_attempts(self, sleep):
        class Fake:
            calls = 0

            def exchange(self, command, *, read_size):
                self.calls += 1
                raise SonyBusy("busy")

        fake = Fake()
        with self.assertRaises(TimeoutError):
            read_model_info(fake)
        self.assertEqual(fake.calls, 40)

    def test_nonbusy_error_is_not_retried_and_preserves_partial_inquiry(self):
        class Fake:
            calls = 0

            def exchange(self, command, **kwargs):
                self.calls += 1
                if command == INQUIRY_CDB:
                    return inquiry()
                raise RuntimeError("other failure")

        fake, result = Fake(), {}
        with self.assertRaisesRegex(RuntimeError, "other failure"):
            probe_identity(fake, model_info=True, result=result)
        self.assertEqual(fake.calls, 2)
        self.assertEqual(result["inquiry"]["vendor"], "Sony")

    @mock.patch("tools.research.a6400a_native_readonly.discover")
    def test_delayed_continuation_is_refused_before_any_discovery(self, discover):
        error = io.StringIO()
        with redirect_stderr(error), self.assertRaises(SystemExit) as caught:
            main(["--finish-model-info", "old-receipt.json", "--output-dir", "unused"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("replies expire", error.getvalue())
        discover.assert_not_called()

    @mock.patch("tools.research.a6400a_native_readonly.time.monotonic",
                side_effect=[0, 9, 9, 9])
    def test_request_time_counts_against_the_same_response_budget(self, clock):
        class Fake:
            def __init__(self):
                self.commands = []

            def exchange(self, command, **kwargs):
                self.commands.append(command)
                return inquiry() if command == INQUIRY_CDB else b""

        fake, result = Fake(), {}
        with self.assertRaises(TimeoutError):
            probe_identity(fake, model_info=True, result=result)
        self.assertEqual(fake.commands, [INQUIRY_CDB, MODEL_CDB])
        self.assertEqual(result["model_transaction"]["status"], "not_completed")
        self.assertEqual(result["model_transaction"]["elapsed_seconds"], 9)

    @mock.patch("tools.research.a6400a_native_readonly.time.sleep")
    @mock.patch("tools.research.a6400a_native_readonly.time.monotonic",
                side_effect=[0, 0, 9, 9])
    def test_deadline_prevents_another_read_even_below_the_attempt_cap(self, clock, sleep):
        transport = mock.Mock()
        transport.exchange.side_effect = SonyBusy("busy")
        with self.assertRaises(TimeoutError):
            read_model_info(transport)
        self.assertEqual(transport.exchange.call_count, 1)

    def test_native_busy_with_partial_data_is_not_retryable(self):
        transport = object.__new__(NativeTransport)
        transport.handle = 1
        transport.operations = []

        def partial_busy(code, input_buffer, input_size, output_buffer, output_size):
            packet = ctypes.cast(output_buffer, ctypes.POINTER(ScsiPacket)).contents
            packet.sptd.ScsiStatus = 2
            packet.sptd.DataTransferLength = 4
            packet.sense[2], packet.sense[12], packet.sense[13] = 9, 129, 129

        transport._ioctl = partial_busy
        with self.assertRaisesRegex(ValueError, "partial data; no retry"):
            transport.exchange(MODEL_CDB, read_size=8192)


class MultiBodyIdentityTests(unittest.TestCase):
    """The probe generalizes to other bodies without inventing an identity (E0645, E0646)."""

    #: A registered body whose Mass Storage PID has not been observed on a host.
    UNOBSERVED = CameraIdentity("unobserved_fixture", None, "Sony DSC USB Device", "ILCE-0000")

    def a7r4a(self):
        return IDENTITIES["ilce_7rm4a"]

    def metadata(self, pair, model="Sony DSC USB Device"):
        return {"usb_pair": pair, "service": "USBSTOR", "status": "OK",
                "config_error": 0, "camera_count": 1,
                "disks": [{"disk_index": 3, "lun": 0, "model": model,
                           "parent_identity_verified": True}]}

    def test_a7r4a_mass_storage_pid_is_the_host_observed_value(self):
        # E0646: read-only Windows PnP on the owner's body (firmware 1.13) in Mass Storage
        # showed USB\VID_054C&PID_0D9D&REV_0200 bound to USBSTOR.
        identity = self.a7r4a()
        self.assertEqual(identity.usb_pid, 0x0D9D)
        self.assertEqual(identity.usb_pair, "054c:0d9d")
        self.assertEqual(identity.expected_model, "ILCE-7RM4A")
        script = build_discover(identity)
        self.assertIn("PID_0D9D", script)
        self.assertIn("usb_pair='054c:0d9d'", script)
        self.assertIn("Expected exactly one healthy ilce_7rm4a USBSTOR camera.", script)
        # PC Remote (0x0D9F, libusbK) is a different interface and must never be admitted.
        self.assertNotIn("PID_0D9F", script)

    def test_unobserved_pid_refuses_pair_and_discovery(self):
        with self.assertRaisesRegex(ValueError, "not observed yet"):
            _ = self.UNOBSERVED.usb_pair
        with self.assertRaisesRegex(ValueError, "not observed yet"):
            build_discover(self.UNOBSERVED)

    def test_default_target_is_unchanged_a6400a(self):
        self.assertEqual(A6400A.target_id, "ilce_6400a")
        self.assertEqual(A6400A.usb_pair, "054c:0f0f")
        self.assertEqual(A6400A.expected_model, "ILCE-6400A")
        self.assertIn("PID_0F0F", build_discover())
        self.assertIn("usb_pair='054c:0f0f'", build_discover())
        self.assertIn("Expected exactly one healthy a6400A USBSTOR camera.", build_discover())

    def test_select_target_admits_only_the_bodys_own_pid(self):
        identity = self.a7r4a()
        self.assertEqual(select_target(self.metadata("054c:0d9d"), identity), 3)
        # The a6400A pair, the a6400 pair and the body's own PC Remote pair are not admitted.
        for pair in ("054c:0f0f", "054c:0994", "054c:0d9f"):
            with self.subTest(pair=pair), self.assertRaisesRegex(ValueError, "admission failed"):
                select_target(self.metadata(pair), identity)

    def test_model_info_expects_this_bodys_model_string(self):
        reply = model_info(b"ILCE-7RM4A")
        self.assertEqual(parse_model_info(reply, "ILCE-7RM4A")["model"], "ILCE-7RM4A")
        # The default (a6400A) parser must reject the A7R IVA reply, and vice versa.
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            parse_model_info(reply)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            parse_model_info(model_info(b"ILCE-6400A"), "ILCE-7RM4A")

    def test_one_busy_then_reply_matches_the_recorded_a7r4a_transaction(self):
        # Constructed fixture shaped like the E0646 receipt: request ack, one Sony BUSY on the
        # first response read, then a reply. No live bytes are embedded; the product code here
        # is the fixture's, not the camera's.
        class Fake:
            def __init__(self):
                self.commands = []
                self.reads = 0

            def exchange(self, command, *, data_out=None, read_size=0):
                admit_request(command, data_out, read_size)
                self.commands.append((command, data_out is not None))
                if command == INQUIRY_CDB:
                    return inquiry()
                if data_out is not None:
                    return b""
                self.reads += 1
                if self.reads == 1:
                    raise SonyBusy("busy")
                return model_info(b"ILCE-7RM4A")

        fake = Fake()
        with mock.patch("tools.research.a6400a_native_readonly.time.sleep"):
            result = probe_identity(fake, model_info=True, expected_model="ILCE-7RM4A")
        self.assertEqual(result["model_info"]["model"], "ILCE-7RM4A")
        self.assertEqual(result["model_transaction"]["status"], "model_reply_received")
        self.assertEqual([command for command, _ in fake.commands],
                         [INQUIRY_CDB, MODEL_CDB, MODEL_CDB, MODEL_CDB])
        self.assertEqual(sum(write for _, write in fake.commands), 1)
        self.assertEqual(fake.reads, 2)

    @mock.patch("tools.research.a6400a_native_readonly.discover")
    def test_main_refuses_a_body_without_an_observed_pid(self, discover):
        error = io.StringIO()
        with mock.patch.dict("tools.research.a6400a_native_readonly.IDENTITIES",
                             {"unobserved_fixture": self.UNOBSERVED}),                 redirect_stderr(error), self.assertRaises(SystemExit) as caught:
            main(["--target", "unobserved_fixture", "--inquiry", "--output-dir", "unused"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("not observed yet", error.getvalue())
        discover.assert_not_called()
