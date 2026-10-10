"""Builds fortpacks/sinhagad.json from OpenStreetMap data.

Run once during setup and commit the result:

    python scripts/build_fortpack.py            # from the cached OSM files
    python scripts/build_fortpack.py --refresh  # re-download from Overpass first

Coordinates and Marathi names come only from OSM. A landmark OSM doesn't have
keeps lat/lon null, and every `one_line` stays "TODO" until a person writes it.
Data from OpenStreetMap, available under the ODbL.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OSM_DIR = ROOT / "fortpacks" / "osm"
OUT = ROOT / "fortpacks" / "sinhagad.json"
OVERPASS = "https://overpass-api.de/api/interpreter"

QUERIES = {
    "sinhagad-features.json": (
        "[out:json][timeout:40];("
        'nwr["historic"](18.355,73.745,18.372,73.765);'
        'nwr["name"~"gate|darwa|kada|wadi",i](18.34,73.74,18.40,73.79);'
        ");out center tags;"
    ),
    "sinhagad-trail.json": "[out:json][timeout:40];way(id:856781948,856781949);out geom tags;",
}

# (our id, display name, OSM names that match it)
WANTED = [
    ("atkarwadi", "Atkarwadi", ["Atkarwadi", "Aatkarwadi"]),
    ("pune-darwaja", "Pune Darwaja", ["Pune Gate", "Pune Darwaja"]),
    ("kalyan-darwaja", "Kalyan Darwaja", ["Kalyan Gate", "Kalyan Darwaja"]),
    ("tanaji-kada", "Tanaji Kada", ["Tanaji Kada"]),
    ("sinhagad-fort", "Sinhagad Fort", ["Sinhagad Fort"]),
]

GLOSSARY = [
    "Sinhagad", "Kondhana", "Tanaji Kada", "Kalyan Darwaja", "Pune Darwaja",
    "Atkarwadi", "buruj", "darwaja",
]


def refresh() -> None:
    for name, query in QUERIES.items():
        body = urllib.parse.urlencode({"data": query}).encode()
        req = urllib.request.Request(
            OVERPASS,
            data=body,
            headers={"User-Agent": "walkworth-setup/0.1 (fort pack build)", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            (OSM_DIR / name).write_bytes(resp.read())
        print(f"downloaded {name}")


def main() -> None:
    if "--refresh" in sys.argv:
        refresh()

    elements = json.loads((OSM_DIR / "sinhagad-features.json").read_text(encoding="utf-8"))["elements"]
    by_name: dict[str, dict] = {}
    for e in elements:
        name = e.get("tags", {}).get("name")
        # Prefer nodes over ways for a point location.
        if name and (name not in by_name or e["type"] == "node"):
            by_name[name] = e

    # The fort's Marathi name is on the castle way, not the node.
    mr_by_name = {
        e["tags"]["name"]: e["tags"]["name:mr"]
        for e in elements
        if "name:mr" in e.get("tags", {})
    }

    landmarks = []
    for lid, display, aliases in WANTED:
        hit = next((by_name[a] for a in aliases if a in by_name), None)
        lm = {"id": lid, "name": display, "name_mr": None, "lat": None, "lon": None, "one_line": "TODO", "osm_id": None}
        if hit:
            lat = hit.get("lat", hit.get("center", {}).get("lat"))
            lon = hit.get("lon", hit.get("center", {}).get("lon"))
            lm.update(
                lat=round(lat, 7),
                lon=round(lon, 7),
                name_mr=hit["tags"].get("name:mr") or mr_by_name.get(hit["tags"]["name"]),
                osm_id=f"{hit['type']}/{hit['id']}",
            )
        landmarks.append(lm)

    pack = {
        "id": "sinhagad",
        "name": "Sinhagad",
        "name_mr": mr_by_name.get("Sinhagad Fort", "").removesuffix(" किल्ला") or None,
        "landmarks": landmarks,
        "glossary": GLOSSARY,
    }
    OUT.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    missing = [lm["name"] for lm in landmarks if lm["lat"] is None]
    print(f"wrote {OUT.relative_to(ROOT)}; not in OSM yet: {', '.join(missing) or 'none'}")


if __name__ == "__main__":
    main()
