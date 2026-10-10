"""The journal-never-lies rules: grounding checks, verbatim quotes, GPX stats."""

from __future__ import annotations

import unittest

from tests import TMP  # noqa: F401

from walkworth import config, gpx, steps, stt

FACTS = {
    "note-01": "[chapter start] noticed still dark; chai stall just opening",
    "note-04": "[chapter climb] noticed langurs on the wall near the gate",
    "stats.distance": "walked 8.6 km",
    "trek.place": "the walk was at Sinhagad",
}
VOCAB = {"sinhagad", "atkarwadi"}


class GroundingTest(unittest.TestCase):
    def check(self, text, ids):
        return steps._check(text, ids, FACTS, VOCAB)

    def test_sourced_line_passes(self):
        self.assertIsNone(self.check("Walked 8.6 km at Sinhagad.", ["stats.distance", "trek.place"]))
        self.assertIsNone(self.check("Still dark as the chai stall opened.", ["note-01"]))

    def test_unsourced_lines_fail(self):
        self.assertEqual(self.check("A quiet start.", []), "cites no facts")
        self.assertIn("don't exist", self.check("Langurs.", ["note-99"]))
        self.assertIn("number 12", self.check("Walked 12 km.", ["stats.distance"]))
        self.assertIn("Tanaji", self.check("Langurs near Tanaji Kada.", ["note-04"]))
        self.assertIn("words its facts don't support",
                      self.check("Peacocks danced in golden monsoon rain.", ["note-04"]) or "")

    def test_chapter_tags_are_not_evidence(self):
        # "[chapter climb]" must not let a line claim "climb".
        self.assertIsNotNone(self.check("The climb.", ["note-04"]))

    def test_copying_the_walker_is_detected(self):
        self.assertTrue(steps._copies("we saw langurs on the wall near the gate today", "Langurs on the wall near the gate, three of them"))
        self.assertFalse(steps._copies("langurs watched from the wall", "Langurs on the wall near the gate"))

    def test_quotes_must_be_verbatim(self):
        t = "Steep bit now, loose stones, had to use my hands on a couple of rocks."
        self.assertEqual(steps._verbatim("had to use my hands", t), "had to use my hands")
        self.assertIsNone(steps._verbatim("had to climb with my hands", t))

    def test_glossary_fixes_misheard_places(self):
        self.assertEqual(stt._glossary_fix("Started from Atkarvadi at dawn", ["Atkarwadi"]), "Started from Atkarwadi at dawn")
        self.assertEqual(stt._glossary_fix("the kalyan darwaza", ["Kalyan Darwaja"]), "the Kalyan Darwaja")

    def test_sample_gpx_stats(self):
        track = gpx.read_track(config.REPO_ROOT / "fixtures" / "sinhagad-sample" / "track.gpx", "t")
        s = gpx.stats(track)
        self.assertAlmostEqual(s.distance_m / 1000, 8.6, delta=0.1)
        self.assertAlmostEqual(s.gain_m, 555, delta=10)
        self.assertAlmostEqual(s.high_point_m, 1276, delta=2)


if __name__ == "__main__":
    unittest.main()
