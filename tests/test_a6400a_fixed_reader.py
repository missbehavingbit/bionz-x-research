import io
from threading import Event
import unittest

from tools.research.a6400a_fixed_reader import (
    CAPACITY, FRAME_BYTES, HEADER, TRANSFER_BYTES, FixedPartitionReader, FixedReadError,
)
from tools.research.a6400a_service_contracts import HASHES, PACKAGE, SYSTEM, Library
from tools.research.a6400a_transfer_controls import (
    FramedTransport, HashSink, completion_release, fixture_bytes, response_frames, serialize_payload,
)


def headers(length):
    return [HEADER.pack(left, 0x40, 1, 3, 0, 0, 0)
            for left in range(length, 0, -FRAME_BYTES)]


class FixedReaderTests(unittest.TestCase):
    def test_fragmented_multiframe_read_preserves_bytes_and_checks_every_terminator(self):
        size = FRAME_BYTES + 512
        transport = FramedTransport(headers(size), max_fragment=997)
        sink = io.BytesIO()
        receipt = FixedPartitionReader(transport, expected_bytes=size).read(sink)
        self.assertEqual(sink.getvalue(), fixture_bytes(0, size))
        self.assertEqual((receipt.payload_bytes, receipt.output_bytes, receipt.frames),
                         (size, size, 2))
        self.assertEqual(receipt.zero_length_packets, 33)
        self.assertTrue(receipt.wire_complete)
        self.assertFalse(receipt.cleanup_verified)
        self.assertEqual(receipt.payload_sha256, receipt.output_sha256)
        self.assertEqual(len(transport.requests), 1)

    def test_invalid_capacity_sequence_and_deadline_send_nothing(self):
        for kwargs in (
            {"expected_bytes": 0}, {"expected_bytes": 511}, {"expected_bytes": CAPACITY + 512},
            {"expected_bytes": True}, {"sequence": 0}, {"sequence": 65536},
            {"seconds": 0}, {"seconds": float("inf")}, {"seconds": float("nan")},
        ):
            with self.subTest(kwargs=kwargs):
                transport = FramedTransport(headers(512))
                with self.assertRaises(ValueError):
                    FixedPartitionReader(transport, **kwargs)
                self.assertEqual(transport.requests, [])

    def test_malformed_headers_fail_before_writing_any_payload(self):
        valid = [512, 0x40, 1, 3, 0, 0, 0]
        for field, value in ((0, 0), (0, CAPACITY + 512), (1, 0xFF01),
                             (2, 2), (3, 0), (4, 1), (5, 1), (6, 0x80)):
            with self.subTest(field=field, value=value):
                changed = valid.copy()
                changed[field] = value
                transport = FramedTransport([HEADER.pack(*changed)])
                sink = HashSink()
                with self.assertRaises(FixedReadError) as caught:
                    FixedPartitionReader(transport, expected_bytes=512).read(sink)
                self.assertEqual(caught.exception.receipt["status"], "invalid_header")
                self.assertEqual(sink.size, 0)
                self.assertFalse(caught.exception.receipt["wire_complete"])
                self.assertFalse(caught.exception.receipt["cleanup_verified"])

    def test_remaining_count_must_decrease_by_the_actual_frame_not_repeat(self):
        size = FRAME_BYTES + 512
        transport = FramedTransport([headers(size)[0]] * 2)
        sink = HashSink()
        with self.assertRaises(FixedReadError) as caught:
            FixedPartitionReader(transport, expected_bytes=size).read(sink)
        self.assertEqual(caught.exception.receipt["status"], "invalid_header")
        self.assertEqual(sink.size, FRAME_BYTES)
        self.assertFalse(caught.exception.receipt["wire_complete"])

    def test_nonempty_terminator_is_not_silently_discarded(self):
        class BadTerminator(FramedTransport):
            def read(self, length, *, timeout_ms):
                raw = super().read(length, timeout_ms=timeout_ms)
                return raw if raw else b"\xA5"

        with self.assertRaises(FixedReadError) as caught:
            FixedPartitionReader(BadTerminator(headers(512)), expected_bytes=512).read(HashSink())
        self.assertEqual(caught.exception.receipt["status"], "invalid_terminator")
        self.assertFalse(caught.exception.receipt["wire_complete"])

    def test_zero_packet_inside_payload_cannot_be_a_successful_empty_read(self):
        class EmptyPayload(FramedTransport):
            def read(self, length, *, timeout_ms):
                raw = super().read(length, timeout_ms=timeout_ms)
                return b"" if length == 512 else raw

        with self.assertRaises(FixedReadError) as caught:
            FixedPartitionReader(EmptyPayload(headers(512)), expected_bytes=512).read(HashSink())
        self.assertEqual(caught.exception.receipt["status"], "short_transfer")
        self.assertEqual(caught.exception.receipt["payload_bytes"], 0)

    def test_cancel_before_request_is_distinct_from_cancel_then_drain(self):
        cancel = Event()
        cancel.set()
        transport = FramedTransport(headers(65536))
        with self.assertRaises(FixedReadError) as caught:
            FixedPartitionReader(transport, expected_bytes=65536).read(HashSink(), cancel=cancel)
        self.assertEqual(caught.exception.receipt["status"], "cancelled_before_request")
        self.assertEqual(transport.requests, [])
        cancel.clear()

        class CancelSink(HashSink):
            def write(self, data):
                written = super().write(data)
                cancel.set()
                return written

        sink = CancelSink()
        with self.assertRaises(FixedReadError) as caught:
            FixedPartitionReader(transport, expected_bytes=65536).read(sink, cancel=cancel)
        receipt = caught.exception.receipt
        self.assertEqual(receipt["status"], "cancelled_drained")
        self.assertEqual((receipt["payload_bytes"], receipt["output_bytes"]), (65536, TRANSFER_BYTES))
        self.assertTrue(receipt["wire_complete"])
        self.assertFalse(receipt["cleanup_verified"])
        self.assertEqual(len(transport.requests), 1)

    def test_output_errors_and_short_writes_drain_without_a_success_claim(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                class BrokenSink(HashSink):
                    def write(self, data):
                        if raises:
                            raise OSError("Constructed disk failure")
                        return super().write(data[:7])

                with self.assertRaises(FixedReadError) as caught:
                    FixedPartitionReader(FramedTransport(headers(65536)),
                                         expected_bytes=65536).read(BrokenSink())
                receipt = caught.exception.receipt
                self.assertEqual(receipt["status"], "output_failed_drained")
                self.assertTrue(receipt["wire_complete"])
                self.assertEqual(receipt["payload_bytes"], 65536)
                self.assertEqual(receipt["output_bytes"], 0 if raises else 7)
                self.assertEqual(receipt["output_integrity_unknown"], raises)

    def test_partial_transport_timeout_preserves_progress_and_never_retries_or_sends_stop(self):
        class Timeout(FramedTransport):
            calls = 0

            def read(self, length, *, timeout_ms):
                self.calls += 1
                if self.calls == 4:
                    raise TimeoutError("Constructed timeout")
                return super().read(length, timeout_ms=timeout_ms)

        transport = Timeout(headers(65536))
        reader = FixedPartitionReader(transport, expected_bytes=65536)
        with self.assertRaises(FixedReadError) as caught:
            reader.read(HashSink())
        receipt = caught.exception.receipt
        self.assertEqual(receipt["status"], "transport_failed")
        self.assertEqual(receipt["payload_bytes"], TRANSFER_BYTES)
        self.assertFalse(receipt["wire_complete"])
        self.assertFalse(receipt["cleanup_verified"])
        with self.assertRaisesRegex(ValueError, "reused"):
            reader.read(HashSink())
        self.assertEqual(len(transport.requests), 1)

    def test_mid_fragment_failure_retains_all_received_payload_not_only_complete_chunks(self):
        class FragmentTimeout(FramedTransport):
            calls = 0

            def read(self, length, *, timeout_ms):
                self.calls += 1
                if self.calls == 3:
                    raise TimeoutError("Constructed failure after 100 payload bytes")
                return super().read(length, timeout_ms=timeout_ms)

        sink = io.BytesIO()
        reader = FixedPartitionReader(
            FragmentTimeout(headers(512), max_fragment=100), expected_bytes=512)
        with self.assertRaises(FixedReadError) as caught:
            reader.read(sink)
        self.assertEqual(sink.getvalue(), fixture_bytes(0, 100))
        self.assertEqual(caught.exception.receipt["payload_bytes"], 100)
        self.assertEqual(caught.exception.receipt["output_bytes"], 100)
        self.assertEqual(caught.exception.receipt["payload_sha256"],
                         caught.exception.receipt["output_sha256"])
        self.assertFalse(caught.exception.receipt["wire_complete"])

    def test_short_request_remains_unacknowledged_and_is_not_repeated(self):
        class ShortRequest(FramedTransport):
            def write(self, data, *, timeout_ms):
                return super().write(data, timeout_ms=timeout_ms) - 1

        transport = ShortRequest(headers(512))
        with self.assertRaises(FixedReadError) as caught:
            FixedPartitionReader(transport, expected_bytes=512).read(HashSink())
        self.assertEqual(caught.exception.receipt["status"], "short_request")
        self.assertTrue(caught.exception.receipt["request_attempted"])
        self.assertFalse(caught.exception.receipt["request_sent"])
        self.assertEqual(len(transport.requests), 1)

    def test_one_deadline_also_bounds_cancellation_drain(self):
        now = [0.0]
        cancel = Event()

        class ExpiringSink(HashSink):
            def write(self, data):
                written = super().write(data)
                cancel.set()
                now[0] = 2.0
                return written

        with self.assertRaises(FixedReadError) as caught:
            FixedPartitionReader(FramedTransport(headers(65536)), expected_bytes=65536,
                                 seconds=1, clock=lambda: now[0]).read(ExpiringSink(), cancel=cancel)
        self.assertEqual(caught.exception.receipt["status"], "deadline")
        self.assertEqual(caught.exception.receipt["payload_bytes"], TRANSFER_BYTES)
        self.assertFalse(caught.exception.receipt["wire_complete"])

    def test_fragment_budget_is_finite_even_with_a_stationary_clock(self):
        reader = FixedPartitionReader(FramedTransport(headers(512), max_fragment=1),
                                      expected_bytes=512, clock=lambda: 0.0)
        with self.assertRaises(FixedReadError) as caught:
            reader.read(HashSink())
        self.assertEqual(caught.exception.receipt["status"], "io_limit")
        self.assertEqual(caught.exception.receipt["io_calls"], reader.max_calls)
        self.assertFalse(caught.exception.receipt["wire_complete"])

    def test_late_final_terminator_does_not_turn_an_expired_deadline_into_success(self):
        now = [0.0]

        class LateTerminator(FramedTransport):
            def read(self, length, *, timeout_ms):
                raw = super().read(length, timeout_ms=timeout_ms)
                if not raw:
                    now[0] = 2.0
                return raw

        with self.assertRaises(FixedReadError) as caught:
            FixedPartitionReader(LateTerminator(headers(512)), expected_bytes=512,
                                 seconds=1, clock=lambda: now[0]).read(HashSink())
        self.assertEqual(caught.exception.receipt["status"], "deadline")
        self.assertFalse(caught.exception.receipt["wire_complete"])
        self.assertFalse(caught.exception.receipt["cleanup_verified"])


@unittest.skipUnless((PACKAGE / SYSTEM / "libsencore.so").is_file(),
                     "Private a6400A original transfer input unavailable")
class OriginalTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = Library((PACKAGE / SYSTEM / "libsencore.so").read_bytes(),
                           HASHES["libsencore.so"])

    def test_original_headers_cover_exact_capacity_without_mapping_padding(self):
        report = response_frames(self.core, CAPACITY)
        decoded = [HEADER.unpack(bytes.fromhex(value)) for value in report["header_hex"]]
        self.assertEqual(len(decoded), 60)
        self.assertEqual((decoded[0][0], decoded[-1][0]), (CAPACITY, FRAME_BYTES))
        self.assertEqual(sum(report["frame_bytes"]), CAPACITY)
        self.assertTrue(all(row[1:] == (0x40, 1, 3, 0, 0, 0) for row in decoded))

    def test_original_serializer_retries_short_writes_without_skipping_source_bytes(self):
        report = serialize_payload(self.core)
        self.assertEqual(report["bytes_sent"], 65536)
        self.assertEqual(report["bytes_advanced"], 65536)
        self.assertEqual([row["offset"] for row in report["writes"][:3]], [0, 127, 512])
        self.assertTrue(all(row["requested"] <= TRANSFER_BYTES for row in report["writes"]))

    def test_original_io_failures_reach_cancellation_without_advancing(self):
        for value in (0, -1, -4, -6, 65537):
            with self.subTest(value=value):
                report = serialize_payload(self.core, failure=value)
                self.assertTrue(report["cancellation_boundary"])
                self.assertEqual(report["original_execution"]["stop"], "declared_boundary")
                self.assertEqual((report["bytes_sent"], report["bytes_advanced"]), (0, 0))

    def test_original_mapped_response_arms_and_delivers_owned_release(self):
        report = completion_release(self.core)
        self.assertEqual(report["extracted_state"], 16)
        self.assertTrue(report["mapping_released"])
        self.assertEqual(report["resource_fields_after"], [0xFFFFFFFF, 0, 0])
        self.assertEqual([row["mode"] for row in report["deliveries"]], [16])
        self.assertEqual([row["operation"] for row in report["operations"]][-3:],
                         ["munmap_fixture", "flock_fixture", "close_fixture"])

    def test_original_delivery_failure_and_unarmed_state_are_not_cleanup(self):
        for kwargs in ({"state": 8}, {"delivery_result": -1}):
            with self.subTest(kwargs=kwargs):
                report = completion_release(self.core, **kwargs)
                self.assertFalse(report["mapping_released"])
                self.assertEqual(report["resource_fields_after"][0], 7)
                self.assertNotEqual(report["resource_fields_after"][2], 0)
                self.assertNotIn("munmap_fixture", [row["operation"] for row in report["operations"]])


if __name__ == "__main__":
    unittest.main()
