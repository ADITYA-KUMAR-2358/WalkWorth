"""Draws the app icons: Trail mode's record ring around a Familjen Grotesk W.

    python scripts/make_icons.py
"""

from __future__ import annotations

import io
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT / "backend" / "walkworth" / "assets" / "fonts" / "familjen-grotesk-latin-wght-normal.woff2"
OUT = ROOT / "web" / "public"
BASALT, PAPER, LATERITE = "#17201C", "#ECF0ED", "#D9A62A"


def grotesk_bold(px: int) -> ImageFont.FreeTypeFont:
    font = TTFont(FONT)
    font.flavor = None
    buf = io.BytesIO()
    font.save(buf)
    buf.seek(0)
    f = ImageFont.truetype(buf, px)
    try:
        f.set_variation_by_axes([700])  # wght
    except Exception:  # noqa: BLE001 - axis order differs between builds
        pass
    return f


def icon(size: int, safe: float) -> Image.Image:
    scale = 4
    s = size * scale
    img = Image.new("RGB", (s, s), BASALT)
    d = ImageDraw.Draw(img)
    r = s * safe / 2
    c = s / 2
    d.ellipse([c - r, c - r, c + r, c + r], outline=LATERITE, width=max(2, int(s * 0.035)))
    font = grotesk_bold(int(r * 1.15))
    box = d.textbbox((0, 0), "W", font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text((c - w / 2 - box[0], c - h / 2 - box[1]), "W", font=font, fill=PAPER)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    icon(192, 0.84).save(OUT / "icon-192.png")
    icon(512, 0.84).save(OUT / "icon-512.png")
    icon(512, 0.62).save(OUT / "icon-maskable-512.png")
    icon(180, 0.84).save(OUT / "apple-touch-icon.png")
    print("icons written to web/public")


if __name__ == "__main__":
    main()
