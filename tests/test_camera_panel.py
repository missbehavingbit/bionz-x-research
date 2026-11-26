"""Contract tests for the browser control panel.

Offline: the panel is driven through its own HTTP surface against a recorded session, so no
camera is contacted. What these protect is the rule the panel exists to obey — a control the
camera does not advertise must be refused with the camera's own reason, before anything is
sent. On the owner's body the single-shot shutter is exactly that case (E0659), and a panel that
greyed it out without saying why, or that sent it and showed a transport failure, would both be
wrong in a way a user reads as "the tool is broken".

Second protected rule: a setting whose values arrive as objects must show readable names.
Measured shape for white balance is a list of ``{"whiteBalanceMode": "Auto WB", ...}``; rendering
those with ``str`` produces a Python repr, which is what the first draft did and what the test
below now forbids.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "research"))

from camera_panel import SETTINGS, Camera, build_handler  # noqa: E402
from camera_remote import setter_params, value_from_setter_params  # noqa: E402
from scalarwebapi import ApiError  # noqa: E402

RECORD = ROOT / "docs" / "evidence" / "cross_model" / "a6400-scalarwebapi-session.json"


class PanelHarness:
    """A panel bound to a recorded session on an ephemeral port."""

    def __init__(self) -> None:
        self.camera = Camera("offline", 10000, RECORD)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(self.camera))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return json.loads(r.read())

    def post(self, path: str, *, token: str | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, dict]:
        sent = {"X-Panel-Token": self.camera.token if token is None else token}
        sent.update(headers or {})
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=b"", method="POST", headers=sent)
        try:
            with urllib.request.urlopen(request, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read())


class SceneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = PanelHarness()

    def tearDown(self) -> None:
        self.panel.close()

    def test_the_scene_reports_what_the_camera_advertises(self) -> None:
        scene = self.panel.get("/api/scene")
        self.assertEqual(len(scene["api"]), 47)
        self.assertEqual(scene["host"], "offline")

    def test_the_scene_ships_the_controls_and_settings_definitions(self) -> None:
        scene = self.panel.get("/api/scene")
        self.assertIn("shutter", scene["controls"])
        self.assertIn("iso", scene["settings"])
        # The page needs the method name to decide what to grey out.
        self.assertEqual(scene["controls"]["shutter"]["method"], "actTakePicture")


class RefusalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = PanelHarness()

    def tearDown(self) -> None:
        self.panel.close()

    def test_the_shutter_is_refused_and_the_reason_names_the_method(self) -> None:
        status, body = self.panel.post("/api/act?key=shutter")
        self.assertEqual(status, 409)
        self.assertFalse(body["ok"])
        self.assertIn("actTakePicture", body["detail"])
        self.assertIn("not advertised", body["detail"])

    def test_an_advertised_control_is_performed(self) -> None:
        status, body = self.panel.post("/api/act?key=half_press")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_an_unknown_control_is_rejected_as_a_client_error(self) -> None:
        status, body = self.panel.post("/api/act?key=nonsense")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])


class SettingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = PanelHarness()

    def tearDown(self) -> None:
        self.panel.close()

    def test_every_setting_answers_a_current_value_and_a_value_list(self) -> None:
        for key in SETTINGS:
            with self.subTest(setting=key):
                answer = self.panel.get(f"/api/setting?key={key}")
                self.assertIsNotNone(answer["current"], f"{key} has no current value")
                self.assertTrue(answer["values"], f"{key} has no value list")

    def test_object_shaped_values_become_readable_names(self) -> None:
        answer = self.panel.get("/api/setting?key=white_balance")
        self.assertEqual(answer["current"], "Auto WB")
        self.assertIn("Daylight", answer["values"])
        # A Python repr would contain a brace or a quote; a name never does.
        for value in answer["values"]:
            self.assertNotIn("{", value)
            self.assertNotIn("'", value)

    def test_a_setting_reports_the_value_after_a_set(self) -> None:
        status, body = self.panel.post("/api/set?key=iso&value=400")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])


class OriginTests(unittest.TestCase):
    """A page on any other site can POST to 127.0.0.1; only the panel's own page may act."""

    def setUp(self) -> None:
        self.panel = PanelHarness()

    def tearDown(self) -> None:
        self.panel.close()

    def test_a_post_without_the_page_token_is_refused(self) -> None:
        status, body = self.panel.post("/api/act?key=half_press", token="")
        self.assertEqual(status, 403)
        self.assertFalse(body["ok"])

    def test_a_post_from_another_origin_is_refused_even_with_the_token(self) -> None:
        status, _ = self.panel.post("/api/act?key=half_press",
                                    headers={"Origin": "https://example.invalid"})
        self.assertEqual(status, 403)

    def test_a_rebound_host_name_is_refused(self) -> None:
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.panel.port, timeout=5)
        try:
            connection.request("GET", "/api/scene", headers={"Host": "attacker.invalid"})
            self.assertEqual(connection.getresponse().status, 403)
        finally:
            connection.close()

    def test_the_served_page_carries_the_token(self) -> None:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.panel.port}/", timeout=5) as r:
            self.assertIn(self.panel.camera.token, r.read().decode())


class SetterShapeTests(unittest.TestCase):
    """Sony's client sends three setters in shapes other than [value] (CameraClient, E0660)."""

    def test_white_balance_is_mode_flag_and_temperature(self) -> None:
        self.assertEqual(setter_params("setWhiteBalance", "Daylight"), ["Daylight", False, -1])

    def test_drive_mode_is_an_object(self) -> None:
        self.assertEqual(setter_params("setContShootingMode", "Continuous"),
                         [{"contShootingMode": "Continuous"}])

    def test_exposure_is_an_index_at_a_third_of_a_stop(self) -> None:
        self.assertEqual(setter_params("setExposureCompensation", "0.7"), [2])
        self.assertEqual(setter_params("setExposureCompensation", "-4.7"), [-14])

    def test_the_replay_refuses_the_shape_the_first_draft_sent(self) -> None:
        camera = Camera("offline", 10000, RECORD)
        with self.assertRaises(ApiError) as caught:
            camera.call("setWhiteBalance", ["Daylight"])
        self.assertEqual(caught.exception.code, 3)

    def test_the_replay_refuses_a_value_the_camera_did_not_list(self) -> None:
        camera = Camera("offline", 10000, RECORD)
        with self.assertRaises(ApiError):
            camera.call("setIsoSpeedRate", ["123"])

    def test_every_shape_round_trips(self) -> None:
        for setter, value in (("setWhiteBalance", "Daylight"), ("setIsoSpeedRate", "400"),
                              ("setContShootingMode", "Single"), ("setFNumber", "4.0")):
            with self.subTest(setter=setter):
                self.assertEqual(value_from_setter_params(setter, setter_params(setter, value)),
                                 value)


class SettableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = PanelHarness()

    def tearDown(self) -> None:
        self.panel.close()

    def test_a_setter_the_camera_does_not_advertise_is_refused_before_sending(self) -> None:
        # setShutterSpeed is not among the 47 methods (E0659), although its getter is.
        status, body = self.panel.post('/api/set?key=shutter_speed&value=1/200')
        self.assertEqual(status, 409)
        self.assertIn("setShutterSpeed", body["detail"])

    def test_the_setting_says_it_is_read_only(self) -> None:
        answer = self.panel.get("/api/setting?key=shutter_speed")
        self.assertFalse(answer["settable"])
        self.assertIn("read-only", answer["error"])

    def test_white_balance_set_reaches_the_getter(self) -> None:
        status, body = self.panel.post("/api/set?key=white_balance&value=Daylight")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.panel.get("/api/setting?key=white_balance")["current"], "Daylight")

    def test_exposure_set_reads_back_in_the_cameras_own_spelling(self) -> None:
        status, body = self.panel.post("/api/set?key=ev&value=0.7")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.panel.get("/api/setting?key=ev")["current"], "0.7")

    def test_the_documented_exposure_shape_expands_to_indices(self) -> None:
        camera = Camera("offline", 10000, RECORD)
        camera.recorded["answers"]["getSupportedExposureCompensation"] = {
            "result": [[3], [-3], [1]]}
        self.assertEqual(camera.supported_values("getSupportedExposureCompensation"),
                         ["-3", "-2", "-1", "0", "1", "2", "3"])


class EventTests(unittest.TestCase):
    """The panel must stay current without a busy loop, and must survive a silent camera."""

    def setUp(self) -> None:
        self.panel = PanelHarness()

    def tearDown(self) -> None:
        self.panel.close()

    def test_an_event_is_reduced_to_fields_a_person_can_use(self) -> None:
        event = self.panel.get("/api/events")
        self.assertEqual(event["type"], "availableApiList")
        self.assertEqual(event["api_count"], 47)

    def test_a_recording_answers_once_and_then_says_it_is_finished(self) -> None:
        # An instant replay would otherwise make the page re-poll in a tight loop.
        self.panel.get("/api/events")
        second = self.panel.get("/api/events")
        self.assertTrue(second["final"])

    def test_the_raw_name_list_is_not_forwarded(self) -> None:
        # getEvent's "names" array is 47 strings and would be re-sent on every poll.
        event = self.panel.get("/api/events")
        self.assertNotIn("names", event)


class StreamTests(unittest.TestCase):
    def test_a_stream_request_without_a_camera_is_a_clean_refusal(self) -> None:
        # Offline there is no stream to relay; the panel must say so rather than hang.
        panel = PanelHarness()
        try:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{panel.port}/stream", timeout=5) as r:
                    payload = json.loads(r.read())
                    status = 200
            except urllib.error.HTTPError as exc:
                with exc:
                    payload = json.loads(exc.read())
                    status = exc.code
            self.assertEqual(status, 503)
            self.assertIn("no camera", payload["error"])
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()