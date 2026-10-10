"""compose() with a scripted model: opener and chapter rules, no network."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from tests import TMP  # noqa: F401

from walkworth import db, fixtures, steps
from walkworth.schema import GeneratedLine

TREK = "sinhagad-sample"


def line(text, ids):
    return {"text": text, "source_ids": ids}


class ComposeTest(unittest.TestCase):
    def run_compose(self, answers):
        """answers: a function (system, user) -> JSON for each model call."""
        calls = []

        def fake_chat(model, system, user, **kw):
            calls.append((system, user))
            return json.dumps(answers(system, user))

        fixtures.load()
        with mock.patch.object(steps.llm, "chat", fake_chat):
            result = steps.compose(TREK)
        with db.connect() as conn:
            lines = db.fetch(conn, GeneratedLine, "trek_id = ?", (TREK,), "id")
        return result, lines, calls

    def test_opener_gets_only_walk_level_facts_and_may_not_cite_notes(self):
        def answers(system, user):
            if "opening" in system:
                # First tries to cite a note (rejected), then fixes itself.
                if "You already wrote this" in user:
                    return {"opener": [line("Walked 8.6 km at Sinhagad.", ["stats.distance", "trek.place"])]}
                return {"opener": [line("Chai stall opening in the dark.", ["note-01"])]}
            return {"title": line("", []), "line": line("", [])}

        result, lines, calls = self.run_compose(answers)
        opener_prompt = next(u for s, u in calls if "opening" in s)
        self.assertIn("stats.distance", opener_prompt)
        self.assertNotIn("note-01", opener_prompt, "chapter facts must not reach the opener")
        openers = [ln for ln in lines if ln.role == "opener"]
        self.assertEqual([ln.text for ln in openers], ["Walked 8.6 km at Sinhagad."])

    def test_unsourced_and_invented_lines_never_reach_the_journal(self):
        def answers(system, user):
            if "opening" in system:
                return {"opener": [line("A golden dawn over the Sahyadri.", [])]}
            return {"title": line("Peacocks at dawn", ["note-01"]), "line": line("Eleven peacocks danced.", ["note-01"])}

        result, lines, _ = self.run_compose(answers)
        self.assertEqual(lines, [])
        self.assertGreater(result["dropped_unsourced"], 0)

    def test_chapter_line_that_only_restates_the_note_is_dropped(self):
        def answers(system, user):
            if "opening" in system:
                return {"opener": []}
            if "start of the walk" not in user:
                return {"title": line("", []), "line": line("", [])}
            # The start chapter has only note-01, so a paraphrase is all the model can offer.
            return {"title": line("Still dark", ["note-01"]),
                    "line": line("It is still dark and the chai stall is just opening.", ["note-01"])}

        _, lines, calls = self.run_compose(answers)
        retry = next(u for s, u in calls if "You already wrote this" in u)
        self.assertIn("only says again what the walker said", retry)
        self.assertEqual([ln.role for ln in lines], ["chapter_title"], "the title stays, the paraphrase goes")

    def test_restates_spots_paraphrase_not_new_facts(self):
        quote = "Started from Atkarwadi, still dark, chai stall just opening"
        self.assertTrue(steps._restates("It is still dark and the chai stall is just opening.", quote))
        self.assertFalse(steps._restates("The climb from 736 m began in the dark.", quote))

    def test_every_kept_line_cites_something(self):
        def answers(system, user):
            if "opening" in system:
                return {"opener": [line("Walked 8.6 km at Sinhagad.", ["stats.distance", "trek.place"])]}
            return {"title": line("Still dark", ["note-01"]), "line": line("It is still dark and the chai stall is opening.", ["note-01"])}

        _, lines, _ = self.run_compose(answers)
        self.assertTrue(lines)
        self.assertTrue(all(ln.source_ids for ln in lines))


if __name__ == "__main__":
    unittest.main()
