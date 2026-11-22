"""Contract tests for the ScalarWebAPI transport.

These are offline: no camera is contacted. What they protect is the decoding rules the live
camera forced, each of which was measured on the owner's ILCE-6400 (E0659) and each of which
would silently corrupt a caller if it regressed:

* The camera answers HTTP ``200`` with an ``error`` body. A decoder that trusted the status
  code would report success for a refused method.
* ``startLiveview`` succeeds with no parameters and is rejected when given a version string.
  A client that "helpfully" passed one would never start a stream.
* ``getAvailableApiList`` nests its list one level. A caller reading the outer list would
  get one element and conclude the camera exposes a single method.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "research"))

from scalarwebapi import (  # noqa: E402
    ApiError,
    ScalarWebApi,
    TransportError,
    build_request,
    parse_response,
)


def http(body: bytes, status: str = "HTTP/1.1 200 OK") -> bytes:
    """One HTTP answer as the camera writes it, header block then JSON body."""
    return b"%s\r\nContent-Type: application/json\r\n\r\n%s" % (status.encode(), body)


class BuildRequestTests(unittest.TestCase):
    def test_request_carries_the_four_documented_fields(self) -> None:
        body = json.loads(build_request("getShootMode"))
        self.assertEqual(body, {"method": "getShootMode", "params": [], "id": 1, "version": "1.0"})

    def test_parameters_are_passed_through_unchanged(self) -> None:
        body = json.loads(build_request("setIsoSpeedRate", ["400"]))
        self.assertEqual(body["params"], ["400"])

    def test_a_method_name_is_never_rewritten(self) -> None:
        # A caller must be able to ask for a method this camera does not expose and learn
        # that from the answer; a client that "corrected" the name would hide the fact.
        body = json.loads(build_request("actTakePicture"))
        self.assertEqual(body["method"], "actTakePicture")


class ParseResponseTests(unittest.TestCase):
    def test_result_is_returned(self) -> None:
        answer = http(b'{"id":1,"result":["still"]}')
        self.assertEqual(parse_response(answer)["result"], ["still"])

    def test_error_body_raises_even_though_the_status_is_200(self) -> None:
        # Measured: the camera returns HTTP 200 for a refused method.
        answer = http(b'{"error":[12,"getCreativeStyle"],"id":1}')
        with self.assertRaises(ApiError) as caught:
            parse_response(answer, "getCreativeStyle")
        self.assertEqual(caught.exception.code, 12)
        self.assertEqual(caught.exception.message, "getCreativeStyle")
        self.assertIn("No Such Method", str(caught.exception))

    def test_error_code_one_uses_sonys_own_name(self) -> None:
        # Measured on the body: the shutter answers 1. Sony's client names 1 "Any", an
        # unclassified refusal, and calling it "no such method" (12) would be a different claim.
        answer = http(b'{"error":[1,""],"id":1}')
        with self.assertRaises(ApiError) as caught:
            parse_response(answer, "actTakePicture")
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("Any", str(caught.exception))
        self.assertNotIn("No Such Method", str(caught.exception))

    def test_results_shape_is_accepted(self) -> None:
        # guide/getMethodTypes answers with "results", not "result".
        answer = http(b'{"id":1,"results":[["getVersions",[],["string*"],"1.0"]]}')
        self.assertIn("results", parse_response(answer))

    def test_body_without_separator_is_a_transport_error(self) -> None:
        with self.assertRaises(TransportError):
            parse_response(b'{"result":[]}')

    def test_non_json_body_is_a_transport_error(self) -> None:
        with self.assertRaises(TransportError):
            parse_response(http(b"<html>404</html>"))

    def test_json_without_result_or_error_is_a_transport_error(self) -> None:
        with self.assertRaises(TransportError):
            parse_response(http(b'{"id":1}'))


class ChunkedAndResultsTests(unittest.TestCase):
    def test_a_chunked_body_is_reassembled(self) -> None:
        body = b'{"result":[["still"]],"id":1}'
        chunked = b"%x\r\n%s\r\n%x\r\n%s\r\n0\r\n\r\n" % (10, body[:10], len(body) - 10, body[10:])
        raw = (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
               b"Content-Type: application/json\r\n\r\n" + chunked)
        self.assertEqual(parse_response(raw)["result"], [["still"]])

    def test_a_truncated_chunked_body_is_a_transport_fault(self) -> None:
        raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nff\r\n{}"
        with self.assertRaises(TransportError):
            parse_response(raw)

    def test_call_returns_results_when_the_camera_answers_results(self) -> None:
        api = _StubApi(http(b'{"results":[[1],[2]],"id":1}'))
        self.assertEqual(api.call("getMethodTypes"), [[1], [2]])


class _StubApi(ScalarWebApi):
    """Replays recorded answers so the list and stream shapes are tested without a camera."""

    def __init__(self, raw: bytes) -> None:
        super().__init__()
        self._raw = raw
        self.calls: list[tuple[str, list | None, str]] = []

    def _post(self, service, method, params, version):  # type: ignore[override]
        self.calls.append((method, params, version))
        return self._raw


class AvailableApiListTests(unittest.TestCase):
    # The real shape, copied from the owner's camera: one nested list.
    NESTED = http(b'{"id":1,"result":[["getShootMode","startLiveview","actHalfPressShutter"]]}')

    def test_the_nested_list_is_unwrapped(self) -> None:
        api = _StubApi(self.NESTED)
        self.assertEqual(
            api.available_api_list(),
            ["getShootMode", "startLiveview", "actHalfPressShutter"],
        )

    def test_a_flat_list_is_also_accepted(self) -> None:
        api = _StubApi(http(b'{"id":1,"result":["getShootMode"]}'))
        self.assertEqual(api.available_api_list(), ["getShootMode"])


class LiveviewUrlTests(unittest.TestCase):
    def test_startliveview_is_called_with_no_parameters(self) -> None:
        # Measured: passing a version string makes the camera answer error 3.
        api = _StubApi(http(b'{"id":1,"result":["http://192.168.122.1:60152/liveviewstream?x"]}'))
        url = api.liveview_url()
        self.assertEqual(url, "http://192.168.122.1:60152/liveviewstream?x")
        self.assertEqual(api.calls[0][0], "startLiveview")
        self.assertIsNone(api.calls[0][1])

    def test_a_non_url_answer_is_a_transport_error(self) -> None:
        api = _StubApi(http(b'{"id":1,"result":[]}'))
        with self.assertRaises(TransportError):
            api.liveview_url()


class TranscriptTests(unittest.TestCase):
    """A live session keeps the camera's bytes, so a recorded answer can be checked later.

    E0659's session survived only as a per-method summary, and four of its answers then
    disagreed with the types orb-server declares for them, with nothing left to settle which
    was right. These tests protect the record that would have settled it.
    """

    def setUp(self) -> None:
        import tempfile
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "session.jsonl"

    def _api(self, raw: bytes) -> "_StubApi":
        api = _StubApi(raw)
        api.transcript = self.path
        return api

    def _lines(self) -> list[dict]:
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def test_the_answer_is_kept_byte_for_byte(self) -> None:
        import base64
        raw = http(b'{"result":[[-15],[15],[1]],"id":1}')
        self._api(raw).call("getSupportedExposureCompensation", [], version="1.0")
        (entry,) = self._lines()
        self.assertEqual(base64.b64decode(entry["response_b64"]), raw)
        self.assertEqual((entry["service"], entry["method"], entry["params"], entry["version"]),
                         ("camera", "getSupportedExposureCompensation", [], "1.0"))

    def test_a_refused_call_is_recorded_before_it_raises(self) -> None:
        with self.assertRaises(ApiError):
            self._api(http(b'{"error":[1,"Any"],"id":1}')).call("actTakePicture")
        self.assertEqual(self._lines()[0]["method"], "actTakePicture")

    def test_an_undecodable_answer_is_still_recorded(self) -> None:
        with self.assertRaises(TransportError):
            self._api(http(b"not json")).call("getEvent", [False])
        self.assertEqual(len(self._lines()), 1)

    def test_a_transport_failure_is_recorded_without_a_response(self) -> None:
        api = ScalarWebApi(host="127.0.0.1", port=9, timeout=0.5, transcript=self.path)
        with self.assertRaises(TransportError):
            api.call("getEvent", [False])
        (entry,) = self._lines()
        self.assertIn("transport_error", entry)
        self.assertNotIn("response_b64", entry)

    def test_without_a_transcript_nothing_is_written(self) -> None:
        _StubApi(http(b'{"result":[0],"id":1}')).call("stopLiveview")
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()