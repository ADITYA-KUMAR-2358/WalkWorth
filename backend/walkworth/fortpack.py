"""Fort packs: offline landmark and glossary data (fortpacks/*.json)."""

from __future__ import annotations

import json
from functools import lru_cache

from . import config
from .gpx import haversine_m
from .schema import FortPack, Landmark

SNAP_RADIUS_M = 120.0


@lru_cache
def load(pack_id: str) -> FortPack | None:
    if not pack_id.replace("-", "").isalnum():
        return None
    path = config.REPO_ROOT / "fortpacks" / f"{pack_id}.json"
    if not path.is_file():
        return None
    return FortPack(**json.loads(path.read_text(encoding="utf-8")))


def available() -> list[FortPack]:
    packs = []
    for path in sorted((config.REPO_ROOT / "fortpacks").glob("*.json")):
        pack = load(path.stem)
        if pack:
            packs.append(pack)
    return packs


def nearest_landmark(pack: FortPack | None, lat: float | None, lon: float | None) -> Landmark | None:
    """The closest landmark within SNAP_RADIUS_M, if any."""
    if pack is None or lat is None or lon is None:
        return None
    best, best_d = None, SNAP_RADIUS_M
    for lm in pack.landmarks:
        if lm.lat is None or lm.lon is None:
            continue
        d = haversine_m(lat, lon, lm.lat, lm.lon)
        if d <= best_d:
            best, best_d = lm, d
    return best
