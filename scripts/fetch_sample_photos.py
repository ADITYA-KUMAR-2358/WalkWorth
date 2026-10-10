"""Downloads the openly licensed Sinhagad photos used by the SAMPLE journal.

These are other people's photos from Wikimedia Commons. The sample journal
credits each one under its print and never presents them as the user's own.
Real imports replace them.

    python scripts/fetch_sample_photos.py
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "sinhagad-sample" / "photos"
UA = {"User-Agent": "walkworth-setup/0.1 (sample fixture)"}
API = "https://commons.wikimedia.org/w/api.php"
WIDTH = 1600

# (local name, Commons file, what it shows)
PHOTOS = [
    ("ridge-trail.jpg", "Sinhagad Fort (75834).jpg", "the trail along the ridge"),
    ("sunrise.jpg", "Sinhagad 2.jpg", "sunrise over the ridges"),
    ("gate.jpg", "Sinhagad Fort (49146).jpg", "a gate in the fort wall"),
    ("fog-edge.jpg", "Sinhagad Fort (25034).jpg", "fog over the cliff edge"),
    ("valley.jpg", "Sinhagad Fort (64325).jpg", "the valley from the top"),
    ("cannon.jpg", "Sinhagad Fort (67008).jpg", "a cannon on the wall"),
]


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
        return r.read()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    titles = "|".join(f"File:{c}" for _, c, _ in PHOTOS)
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "titles": titles,
        "prop": "imageinfo", "iiprop": "url|extmetadata", "iiurlwidth": WIDTH,
    })
    pages = json.loads(get(f"{API}?{q}"))["query"]["pages"].values()
    info = {p["title"][5:]: p["imageinfo"][0] for p in pages}

    credits = []
    for name, commons, label in PHOTOS:
        ii = info[commons]
        meta = ii["extmetadata"]
        artist = re.sub(r"<[^>]+>", "", meta["Artist"]["value"]).strip()
        licence = meta["LicenseShortName"]["value"]
        (OUT / name).write_bytes(get(ii["thumburl"]))
        credits.append({
            "file": name,
            "label": label,
            "artist": artist,
            "licence": licence,
            "licence_url": meta.get("LicenseUrl", {}).get("value"),
            "source": ii["descriptionurl"],
        })
        print(f"{name}: {artist}, {licence}")

    (OUT / "credits.json").write_text(json.dumps(credits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
