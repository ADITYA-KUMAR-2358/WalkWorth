"""The sample trek on a fresh disk: processed snapshot, and real recordings."""

from __future__ import annotations

import unittest
from unittest import mock

from tests import TMP  # noqa: F401

from walkworth import db, fixtures
from walkworth.schema import Entry, GeneratedLine, VoiceNote


def state():
    with db.connect() as conn:
        notes = db.fetch(conn, VoiceNote, "trek_id = ?", ("sinhagad-sample",), "id")
        lines = db.fetch(conn, GeneratedLine, "trek_id = ?", ("sinhagad-sample",))
        entries = db.fetch(conn, Entry, "trek_id = ?", ("sinhagad-sample",))
    return notes, lines, entries


class FixturesTest(unittest.TestCase):
    def tearDown(self):
        fixtures.load()  # leave the shared test database as a fresh deploy would

    def test_fresh_load_has_a_journal_without_the_pipeline(self):
        fixtures.load()
        notes, lines, entries = state()
        self.assertEqual(len(notes), 8)
        self.assertTrue(all(n.transcript for n in notes))
        self.assertGreaterEqual(len(lines), 6)
        self.assertTrue(all(ln.source_ids for ln in lines), "every snapshot line cites a source")
        self.assertEqual(len(entries), 8)
        self.assertIn("Atkarwadi", notes[0].transcript)

    def test_changed_audio_invalidates_the_snapshot(self):
        # The user drops in a real recording: its audio hash no longer matches,
        # so nothing from the snapshot is reused and the pipeline starts clean.
        with mock.patch.object(fixtures, "_sha", return_value="0" * 64):
            fixtures.load()
        notes, lines, entries = state()
        self.assertTrue(all(n.transcript is None for n in notes))
        self.assertEqual((lines, entries), ([], []))

    def test_recording_in_any_format_replaces_the_synthetic_one(self):
        audio = fixtures.FIXTURE_DIR / "audio"
        spare = audio / "note-01.m4a"
        spare.write_bytes((audio / "note-01.ogg").read_bytes())
        try:
            fixtures.load()
            notes, *_ = state()
            self.assertTrue(notes[0].audio_path.endswith("note-01.m4a"))
        finally:
            spare.unlink()


if __name__ == "__main__":
    unittest.main()
