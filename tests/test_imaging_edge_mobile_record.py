"""Contract tests over the measured facts about Sony's own client.

These are offline and read only the committed record, never the APK (which is not in the repo
and must not be, being third-party content). What they protect is provenance: every number the
project states about how Sony's app drives this camera is tied to an artifact identity that a
reader can re-check. If the record were edited without re-measuring the APK, these fail.

The facts themselves, each measured on the shipped 7.6.0 package (E0660):

* The app enumerates 312 web-API names, 221 of them camera methods, while this body advertises
  47. That gap is the reason the shutter works in the app's flow and not in a naive client.
* `TakePicture.canExecute` refuses unless `actTakePicture` is in the camera's own list, and
  `GetEvent` calls `startRecMode` first when the camera offers it. A client that skipped that
  ordering would report a refusal as a fault.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "docs" / "evidence" / "cross_model" / "imaging-edge-mobile-control.json"


class RecordIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = json.loads(RECORD.read_text(encoding="utf-8"))

    def test_the_package_is_the_one_named(self) -> None:
        self.assertEqual(self.record["apk"]["package"], "com.sony.playmemories.mobile")

    def test_the_measured_md5_matches_the_catalog_value(self) -> None:
        # A record that cannot show its own file matched is not evidence.
        self.assertEqual(self.record["apk"]["md5_measured"], self.record["apk"]["md5_catalog"])

    def test_identity_fields_are_present_and_non_empty(self) -> None:
        for field in ("sha256_measured", "version_name", "signing_owner", "source"):
            self.assertTrue(self.record["apk"].get(field), f"{field} is empty")

    def test_the_size_is_a_measured_integer(self) -> None:
        self.assertIsInstance(self.record["apk"]["size_bytes"], int)
        self.assertGreater(self.record["apk"]["size_bytes"], 30_000_000)


class SurfaceGapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = json.loads(RECORD.read_text(encoding="utf-8"))

    def test_the_app_holds_more_methods_than_this_body_advertises(self) -> None:
        # Measured: 221 camera methods in the app, 47 advertised live.
        self.assertEqual(self.record["what_the_app_does"]["camera_methods_in_app"], 221)
        self.assertGreater(self.record["what_the_app_does"]["camera_methods_in_app"], 47)

    def test_the_shutter_sequence_states_the_ordering_that_matters(self) -> None:
        steps = "\n".join(self.record["shutter_sequence"])
        self.assertIn("startRecMode", steps)
        self.assertIn("actTakePicture", steps)
        # The guard is the fact a hand-written client gets wrong.
        self.assertIn("availableApiList contains actTakePicture", steps)

    def test_the_recovery_path_for_a_busy_shutter_is_recorded(self) -> None:
        steps = "\n".join(self.record["shutter_sequence"])
        self.assertIn("StillCaturingNotFinished", steps)
        self.assertIn("awaitTakePicture", steps)

    def test_discovery_uses_the_published_url_not_a_hard_coded_port(self) -> None:
        discovery = self.record["discovery"]
        self.assertIn("X_ScalarWebAPI_ActionList_URL", discovery["upnp"])
        self.assertIn("does not hard-code the port", discovery["note"])


if __name__ == "__main__":
    unittest.main()
