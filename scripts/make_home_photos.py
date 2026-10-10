"""Web-sized copies of the credited Sinhagad sample photos for the home page.

    uv run --project backend python scripts/make_home_photos.py

Reads fixtures/sinhagad-sample/photos/ (credits in credits.json there) and
writes web/public/home/<name>.jpg, long side 1400 px. The home page shows the
same credits under every photo.
"""

from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "fixtures" / "sinhagad-sample" / "photos"
OUT = ROOT / "web" / "public" / "home"
LONG_SIDE = 1400

OUT.mkdir(parents=True, exist_ok=True)
for src in sorted(SRC.glob("*.jpg")):
    img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    img.thumbnail((LONG_SIDE, LONG_SIDE))
    dest = OUT / src.name
    img.save(dest, "JPEG", quality=78, optimize=True, progressive=True)
    print(f"{dest.relative_to(ROOT)}  {img.size[0]}x{img.size[1]}  {dest.stat().st_size // 1024} KB")
