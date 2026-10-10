"""Photo and audio helpers: EXIF time and GPS, HEIC conversion, durations."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

MAX_PHOTO_PX = 2000
EXIF_IFD = 0x8769
GPS_IFD = 0x8825
DATETIME_ORIGINAL = 36867
OFFSET_TIME_ORIGINAL = 36881


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class PhotoInfo:
    jpeg: bytes
    t: datetime | None
    lat: float | None
    lon: float | None
    ele_m: float | None


def _ratio(v) -> float:
    return float(v[0]) / float(v[1]) if isinstance(v, tuple) else float(v)


def _dms(values, ref: str) -> float:
    d, m, s = (_ratio(v) for v in values)
    deg = d + m / 60 + s / 3600
    return -deg if ref in ("S", "W") else deg


def read_photo(data: bytes, local_tz: str) -> PhotoInfo:
    """Decode JPEG/HEIC/PNG, read EXIF, and re-encode as an upright JPEG."""
    img = Image.open(io.BytesIO(data))
    exif = img.getexif()
    t = lat = lon = ele = None

    sub = exif.get_ifd(EXIF_IFD)
    raw_time = sub.get(DATETIME_ORIGINAL) or exif.get(306)
    if raw_time:
        try:
            naive = datetime.strptime(str(raw_time).strip("\x00"), "%Y:%m:%d %H:%M:%S")
            offset = sub.get(OFFSET_TIME_ORIGINAL)
            if offset and len(str(offset)) >= 6:
                sign = -1 if str(offset)[0] == "-" else 1
                hh, mm = str(offset)[1:].split(":")[:2]
                tz = timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
            else:
                # Phones without an offset tag write local time.
                tz = ZoneInfo(local_tz)
            t = naive.replace(tzinfo=tz).astimezone(timezone.utc)
        except (ValueError, TypeError):
            t = None

    gps = exif.get_ifd(GPS_IFD)
    try:
        if 2 in gps and 4 in gps:
            lat = _dms(gps[2], gps.get(1, "N"))
            lon = _dms(gps[4], gps.get(3, "E"))
        if 6 in gps:
            ele = _ratio(gps[6]) * (-1 if gps.get(5) == 1 else 1)
    except (TypeError, ValueError, ZeroDivisionError):
        lat = lon = ele = None

    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_PHOTO_PX, MAX_PHOTO_PX))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=86, optimize=True, progressive=True)
    return PhotoInfo(out.getvalue(), t, lat, lon, ele)


def audio_duration(path) -> float:
    """Seconds of audio. MediaRecorder WebM often has no duration header, so
    fall back to decoding."""
    import av

    with av.open(str(path)) as container:
        if container.duration:
            return container.duration / 1_000_000
        stream = container.streams.audio[0]
        samples = 0
        rate = stream.rate or 48000
        for frame in container.decode(stream):
            samples += frame.samples
        return samples / rate
