"""The HTTP API end to end, without model calls."""

from __future__ import annotations

import asyncio
import io
import os
import unittest
from types import SimpleNamespace

from tests import TMP  # noqa: F401  (sets up the throwaway data dir first)

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from walkworth import api, config, importer

FIX = config.REPO_ROOT / "fixtures" / "sinhagad-sample"
AUDIO = (FIX / "audio" / "note-01.ogg").read_bytes()
PHOTO = (FIX / "photos" / "gate.jpg").read_bytes()
GPX = (FIX / "track.gpx").read_bytes()


def jpeg_without_exif(color=(120, 140, 90)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(out, "JPEG")
    return out.getvalue()


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = TestClient(api.app)
        cls.c = cls.ctx.__enter__()  # runs the lifespan: loads the sample into the empty data dir

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def new_trek(self, title="Test walk", pack="sinhagad") -> str:
        r = self.c.post("/api/treks", json={"title": title, "fort_pack_id": pack})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def capture(self, tid, kind, data, name, t="2026-10-10T01:00:00Z", **extra):
        form = {"kind": kind, "t": t, **{k: str(v) for k, v in extra.items()}}
        return self.c.post(f"/api/treks/{tid}/capture", data=form, files={"file": (name, data)})

    # ---- health and sample ------------------------------------------------------

    def test_health_reports_models_and_keys_without_values(self):
        h = self.c.get("/api/health").json()
        self.assertTrue(h["ok"])
        self.assertEqual(set(h["models"]), {"llm", "vision", "stt"})
        self.assertFalse(h["keys"]["llm_key"])
        self.assertNotIn("test", str(h))

    def test_sample_loaded_on_empty_disk(self):
        treks = {t["trek"]["id"]: t for t in self.c.get("/api/treks").json()}
        s = treks["sinhagad-sample"]
        self.assertTrue(s["trek"]["is_sample"])
        self.assertEqual((s["note_count"], s["photo_count"]), (8, 6))
        self.assertAlmostEqual(s["stats"]["distance_m"] / 1000, 8.6, delta=0.1)

    def test_fortpacks(self):
        packs = self.c.get("/api/fortpacks").json()
        self.assertIn("sinhagad", [p["id"] for p in packs])
        self.assertEqual(self.c.get("/api/fortpacks/nope").status_code, 404)

    # ---- live capture (Trail mode) --------------------------------------------------

    def test_capture_note_and_photo_then_finish(self):
        tid = self.new_trek()
        r = self.capture(tid, "note", AUDIO, "n1.ogg", lat=18.3755, lon=73.7672, duration_s=5.7)
        self.assertEqual(r.json()["status"], "added", r.text)
        r = self.capture(tid, "photo", jpeg_without_exif(), "p1.jpg", t="2026-10-10T01:05:00Z", lat=18.37, lon=73.76)
        self.assertEqual(r.json()["status"], "added", r.text)
        notes = self.c.get(f"/api/treks/{tid}/notes").json()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["location_source"], "fix")
        self.assertEqual(notes[0]["duration_s"], 5.7)
        self.assertEqual(notes[0]["t"], "2026-10-10T01:00:00Z")
        photos = self.c.get(f"/api/treks/{tid}/photos").json()
        self.assertEqual(photos[0]["t"], "2026-10-10T01:05:00Z")  # no EXIF: the phone's time
        # The stored media path is served.
        self.assertEqual(self.c.get(f"/files/{notes[0]['audio_path']}").status_code, 200)
        trek = self.c.post(f"/api/treks/{tid}/finish", json={"screen_seconds": 95}).json()
        self.assertEqual(trek["screen_seconds"], 95)
        self.assertIsNotNone(trek["ended_at"])

    def test_capture_retry_is_a_noop(self):
        tid = self.new_trek()
        self.assertEqual(self.capture(tid, "note", AUDIO, "n.ogg").json()["status"], "added")
        self.assertEqual(self.capture(tid, "note", AUDIO, "n.ogg").json()["status"], "duplicate")
        self.assertEqual(len(self.c.get(f"/api/treks/{tid}/notes").json()), 1)

    def test_capture_note_without_gps(self):
        tid = self.new_trek()
        self.capture(tid, "note", AUDIO, "n.ogg")
        n = self.c.get(f"/api/treks/{tid}/notes").json()[0]
        self.assertIsNone(n["lat"])
        self.assertEqual(n["location_source"], "none")

    def test_capture_rejects_bad_input(self):
        tid = self.new_trek()
        self.assertEqual(self.capture(tid, "video", AUDIO, "x.ogg").status_code, 400)
        self.assertEqual(self.capture(tid, "note", AUDIO, "x.ogg", t="yesterday").status_code, 400)
        self.assertEqual(self.capture(tid, "note", AUDIO, "x.ogg", lat=99, lon=0).status_code, 400)
        self.assertEqual(self.capture(tid, "note", b"", "x.ogg").status_code, 400)
        self.assertEqual(self.capture("no-such-trek", "note", AUDIO, "x.ogg").status_code, 404)
        r = self.capture(tid, "note", b"not audio at all", "x.ogg")
        self.assertEqual(r.json()["status"], "skipped")
        r = self.capture(tid, "photo", b"not a photo", "x.jpg")
        self.assertEqual(r.json()["status"], "skipped")

    # ---- import ------------------------------------------------------------------

    def test_import_gpx_sets_bounds_and_stats(self):
        tid = self.new_trek()
        r = self.c.post(f"/api/treks/{tid}/files", files=[("files", ("track.gpx", GPX))], data={"last_modified": "[]"})
        self.assertEqual(r.json()[0]["status"], "added", r.text)
        s = self.c.get(f"/api/treks/{tid}").json()
        self.assertGreater(s["stats"]["distance_m"], 8000)
        self.assertEqual(s["trek"]["started_at"], "2026-10-10T00:35:00Z")
        again = self.c.post(f"/api/treks/{tid}/files", files=[("files", ("track.gpx", GPX))]).json()
        self.assertNotEqual(again[0]["status"], "added")

    def test_import_photo_with_exif_and_unknown_type(self):
        tid = self.new_trek()
        r = self.c.post(f"/api/treks/{tid}/files",
                        files=[("files", ("gate.jpg", PHOTO)), ("files", ("notes.txt", b"hello"))],
                        data={"last_modified": "[1791277000000, 1791277000000]"}).json()
        self.assertEqual([x["status"] for x in r], ["added", "skipped"])

    def test_import_rejects_bad_last_modified(self):
        tid = self.new_trek()
        r = self.c.post(f"/api/treks/{tid}/files", files=[("files", ("gate.jpg", PHOTO))], data={"last_modified": "{oops"})
        self.assertEqual(r.status_code, 400)

    def test_time_offset_is_clamped(self):
        tid = self.new_trek()
        self.assertEqual(self.c.put(f"/api/treks/{tid}/time-offset", json={"seconds": 99999}).json()["time_offset_s"], 6 * 3600)

    # ---- review edits ----------------------------------------------------------------

    def test_edit_and_delete_note(self):
        tid = self.new_trek()
        self.capture(tid, "note", AUDIO, "n.ogg")
        nid = self.c.get(f"/api/treks/{tid}/notes").json()[0]["id"]
        n = self.c.patch(f"/api/notes/{nid}", json={"transcript": "Fixed words."}).json()
        self.assertTrue(n["user_edited"])
        self.assertEqual(n["transcript"], "Fixed words.")
        self.assertEqual(self.c.delete(f"/api/notes/{nid}").status_code, 200)
        self.assertEqual(self.c.get(f"/api/treks/{tid}/notes").json(), [])

    # ---- journal and exports -----------------------------------------------------------

    def test_journal_renders_with_credits_and_sample_ribbon(self):
        r = self.c.get("/journal/sinhagad-sample")
        self.assertEqual(r.status_code, 200)
        html = r.text
        self.assertIn("Sinhagad", html)
        self.assertIn("Suraj Digrase", html)
        self.assertIn("Sample", html)
        self.assertEqual(self.c.get("/journal/nope").status_code, 404)

    def test_exports(self):
        for kind in ("html", "gpx", "markdown"):
            r = self.c.post(f"/api/treks/sinhagad-sample/exports/{kind}")
            self.assertEqual(r.status_code, 200, f"{kind}: {r.text}")
            out = r.json()
            self.assertGreater(out["bytes"], 0)
            self.assertEqual(self.c.get(out["url"]).status_code, 200)
        self.assertEqual(self.c.post("/api/treks/sinhagad-sample/exports/docx").status_code, 404)
        md = self.c.get("/api/treks/sinhagad-sample/dev-markdown")
        self.assertEqual(md.status_code, 200)

    def test_gpx_export_trims_ends(self):
        full = self.c.post("/api/treks/sinhagad-sample/exports/gpx?trim=false").json()["bytes"]
        trimmed = self.c.post("/api/treks/sinhagad-sample/exports/gpx?trim=true").json()["bytes"]
        self.assertLess(trimmed, full)

    # ---- safety ---------------------------------------------------------------------

    def test_files_route_refuses_traversal_and_private_files(self):
        for path in ("../.env", "data/walkworth.sqlite3", "fixtures/../.env", "data/exports/x", "backend/walkworth/api.py",
                     "data/llm_calls.json"):
            self.assertEqual(self.c.get(f"/files/{path}").status_code, 404, path)

    def test_export_download_refuses_traversal(self):
        self.assertEqual(self.c.get("/api/exports/sinhagad-sample/..%2F..%2Fwalkworth.sqlite3").status_code, 404)

    def test_delete_trek_removes_its_files(self):
        tid = self.new_trek()
        self.capture(tid, "note", AUDIO, "n.ogg")
        folder = importer.trek_dir(tid)
        self.assertTrue(folder.exists())
        self.assertEqual(self.c.delete(f"/api/treks/{tid}").status_code, 200)
        self.assertFalse(folder.exists())
        self.assertEqual(self.c.get(f"/api/treks/{tid}").status_code, 404)

    def test_submit_limit_per_visitor(self):
        api._submits.clear()

        async def run():
            req = lambda ip: SimpleNamespace(client=SimpleNamespace(host=ip))
            for _ in range(3):
                api._spend_guard(req("10.0.0.1"))
            with self.assertRaises(HTTPException) as e:
                api._spend_guard(req("10.0.0.1"))
            self.assertEqual(e.exception.status_code, 429)
            api._spend_guard(req("10.0.0.2"))

        asyncio.run(run())

    # ---- ownership, sample and access code --------------------------------------------

    def test_other_browsers_cannot_see_or_change_my_walk(self):
        tid = self.new_trek("Mine")
        self.capture(tid, "note", AUDIO, "n.ogg")
        audio = self.c.get(f"/api/treks/{tid}/notes").json()[0]["audio_path"]
        self.assertTrue(self.c.get(f"/api/treks/{tid}").json()["can_edit"])
        self.assertEqual(self.c.get(f"/files/{audio}").status_code, 200)

        other = TestClient(api.app)  # no cookie: someone else
        shelf = [t["trek"]["id"] for t in other.get("/api/treks").json()]
        self.assertNotIn(tid, shelf)
        self.assertIn("sinhagad-sample", shelf)
        self.assertIn(tid, [t["trek"]["id"] for t in self.c.get("/api/treks").json()])
        # The link still opens the journal, read-only, and never the raw audio.
        self.assertEqual(other.get(f"/journal/{tid}").status_code, 200)
        self.assertFalse(other.get(f"/api/treks/{tid}").json()["can_edit"])
        self.assertEqual(other.get(f"/files/{audio}").status_code, 404)
        r = other.post(f"/api/treks/{tid}/capture", data={"kind": "note", "t": "2026-10-10T01:00:00Z"},
                       files={"file": ("n2.ogg", AUDIO)})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(other.delete(f"/api/treks/{tid}").status_code, 403)
        self.assertEqual(other.post(f"/api/treks/{tid}/process").status_code, 403)
        nid = self.c.get(f"/api/treks/{tid}/notes").json()[0]["id"]
        self.assertEqual(other.patch(f"/api/notes/{nid}", json={"transcript": "vandal"}).status_code, 403)

    def test_sample_is_read_only(self):
        self.assertFalse(self.c.get("/api/treks/sinhagad-sample").json()["can_edit"])
        nid = self.c.get("/api/treks/sinhagad-sample/notes").json()[0]["id"]
        self.assertEqual(self.c.patch(f"/api/notes/{nid}", json={"transcript": "x"}).status_code, 403)
        self.assertEqual(self.c.delete(f"/api/notes/{nid}").status_code, 403)
        self.assertEqual(self.c.delete("/api/treks/sinhagad-sample").status_code, 403)
        r = self.c.put("/api/treks/sinhagad-sample/lines", json={"role": "opener", "text": "x"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.post("/api/treks/sinhagad-sample/recompose").status_code, 403)
        # Reading and exporting still work.
        self.assertEqual(self.c.post("/api/treks/sinhagad-sample/exports/markdown").status_code, 200)

    def test_access_code_guards_new_walks(self):
        os.environ["ACCESS_CODE"] = "monsoon"
        config.load.cache_clear()
        try:
            self.assertTrue(self.c.get("/api/health").json()["access_code"])
            self.assertEqual(self.c.post("/api/treks", json={"title": "x"}).status_code, 401)
            bad = self.c.post("/api/treks", json={"title": "x"}, headers={"X-Access-Code": "nope"})
            self.assertEqual(bad.status_code, 401)
            ok = self.c.post("/api/treks", json={"title": "x"}, headers={"X-Access-Code": "monsoon"})
            self.assertEqual(ok.status_code, 200)
        finally:
            os.environ["ACCESS_CODE"] = ""
            config.load.cache_clear()
        self.assertFalse(self.c.get("/api/health").json()["access_code"])

    def test_oversized_uploads_are_refused(self):
        tid = self.new_trek()
        old = api.MAX_UPLOAD_BYTES
        api.MAX_UPLOAD_BYTES = 1000
        try:
            self.assertEqual(self.capture(tid, "note", AUDIO, "n.ogg").status_code, 413)
            r = self.c.post(f"/api/treks/{tid}/files", files=[("files", ("gate.jpg", PHOTO))]).json()
            self.assertEqual(r[0]["status"], "skipped")
        finally:
            api.MAX_UPLOAD_BYTES = old

    def test_security_headers(self):
        h = self.c.get("/api/health").headers
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertIn("microphone=(self)", h["permissions-policy"])

    def test_media_paths_round_trip_outside_repo(self):
        p = config.load().data_dir / "treks" / "x" / "audio" / "a.ogg"
        stored = config.store_path(p)
        self.assertEqual(stored, "data/treks/x/audio/a.ogg")
        self.assertEqual(config.media_path(stored).resolve(), p.resolve())
        self.assertEqual(config.media_path("fixtures/sinhagad-sample/track.gpx"), FIX / "track.gpx")


if __name__ == "__main__":
    unittest.main()
