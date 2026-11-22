"""Contract tests for the port map.

The port map answers a question with money and time behind it: "port the whole app" is 2,664
source files, and the honest answer depends on what this body actually serves. These tests
protect the two ways that answer could go wrong:

* counting a feature as portable when the camera does not advertise some method behind it —
  which would promise a control that answers an error;
* treating a blocked feature as missing work, when it is the camera declining to serve it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "research"))

from port_map import (  # noqa: E402
    FEATURE_GROUPS,
    load_live,
    load_methods,
    port_map,
    summarise,
)

CLIENT = ROOT / "docs" / "evidence" / "cross_model" / "imaging-edge-mobile-control.json"
SESSION = ROOT / "docs" / "evidence" / "cross_model" / "a6400-scalarwebapi-session.json"


class InputTests(unittest.TestCase):
    def test_the_client_record_carries_a_method_list(self) -> None:
        methods = load_methods(CLIENT)
        self.assertEqual(len(methods), 311)
        self.assertIn("actTakePicture", methods)

    def test_the_body_advertises_forty_seven(self) -> None:
        self.assertEqual(len(load_live(SESSION)), 47)


class GradingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = port_map(load_live(SESSION), load_methods(CLIENT))

    def test_a_feature_with_every_method_advertised_is_portable(self) -> None:
        iso = next(f for f in self.result["features"] if f["feature"] == "ISO")
        self.assertEqual(iso["state"], "portable now")
        self.assertEqual(iso["missing"], [])

    def test_a_feature_with_no_method_advertised_is_blocked(self) -> None:
        shot = next(f for f in self.result["features"] if f["feature"] == "single shot")
        self.assertEqual(shot["state"], "blocked by the body")
        self.assertEqual(shot["advertised"], [])

    def test_a_feature_missing_only_its_setter_is_only_partly_portable(self) -> None:
        # Measured: this body serves getShutterSpeed but not setShutterSpeed.
        speed = next(f for f in self.result["features"] if f["feature"] == "shutter speed")
        self.assertEqual(speed["state"], "partly portable")
        self.assertIn("setShutterSpeed", speed["missing"])
        self.assertIn("getShutterSpeed", speed["advertised"])

    def test_every_feature_state_is_one_of_the_three_words(self) -> None:
        states = {f["state"] for f in self.result["features"]}
        self.assertTrue(states <= {"portable now", "partly portable", "blocked by the body"})

    def test_no_feature_is_both_advertised_and_missing(self) -> None:
        for feature in self.result["features"]:
            overlap = set(feature["advertised"]) & set(feature["missing"])
            self.assertEqual(overlap, set(), f"{feature['feature']} lists one method twice")


class CountingTests(unittest.TestCase):
    def test_the_callable_count_matches_the_two_inputs(self) -> None:
        result = port_map(load_live(SESSION), load_methods(CLIENT))
        self.assertEqual(result["callable_of_client"], 46)
        self.assertEqual(result["body_advertises"], 47)

    def test_the_summary_states_that_blocked_is_not_missing_work(self) -> None:
        text = summarise(port_map(load_live(SESSION), load_methods(CLIENT)))
        self.assertIn("not a missing piece of this port", text)
        self.assertIn("46 of the 311", text)


class FeatureGroupTests(unittest.TestCase):
    def test_every_group_names_at_least_one_method(self) -> None:
        for label, methods in FEATURE_GROUPS.items():
            self.assertTrue(methods, f"{label} has no methods")

    def test_the_groups_cover_the_features_the_owner_asked_about(self) -> None:
        # These are the ones a photographer would notice if a port silently lacked them.
        for label in ("single shot", "ISO", "aperture", "white balance", "live view",
                      "self timer", "movie recording"):
            self.assertIn(label, FEATURE_GROUPS)


if __name__ == "__main__":
    unittest.main()
