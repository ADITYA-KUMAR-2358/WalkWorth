"""Renders the journal (Jinja2) and writes the exports: a static HTML folder,
a PDF (WeasyPrint), a GPX and Markdown."""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, db, gpx, journal
from .schema import Photo, TrackPoint, Trek

HERE = Path(__file__).parent
TEMPLATES = HERE / "templates"
FONTS = HERE / "assets" / "fonts"

FONT_FACES = [
    ("Familjen Grotesk Variable", "normal", "400 700", "familjen-grotesk-latin-wght-normal.woff2"),
    ("Source Serif 4 Variable", "normal", "200 900", "source-serif-4-latin-opsz-normal.woff2"),
    ("Source Serif 4 Variable", "italic", "200 900", "source-serif-4-latin-opsz-italic.woff2"),
    ("Tiro Devanagari Marathi", "normal", "400", "tiro-devanagari-marathi-devanagari-400-normal.woff2"),
    ("Tiro Devanagari Marathi", "normal", "400", "tiro-devanagari-marathi-latin-400-normal.woff2"),
]

# WeasyPrint doesn't resolve var() inside SVG attributes, so print gets the
# light palette written in.
PRINT_COLOURS = {
    "--paper": "#FFFFFF", "--ink": "#17201C", "--monsoon": "#2E5B48", "--laterite": "#7A5810",
    "--accent": "#D9A62A", "--mist": "#E3E9E5", "--pencil": "#4F5E57", "--rule": "#CBD3CE",
    "--contour": "#D3DDD7", "--strip-hl": "#BFCBC4",
}

env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["j2", "html"]))


def fonts_css(url_for) -> str:
    return "\n".join(
        f'@font-face{{font-family:"{fam}";font-style:{style};font-weight:{weight};font-display:swap;'
        f'src:url("{url_for(file)}") format("woff2");}}'
        for fam, style, weight, file in FONT_FACES
    )


def render(trek_id: str, mode: str, *, photo_src, font_url, trim_m: float = 0) -> str:
    view = journal.build(trek_id, photo_src=photo_src, trim_m=trim_m)
    html = env.get_template("journal.html.j2").render(
        j=view, mode=mode,
        fonts_css=fonts_css(font_url),
        css=(TEMPLATES / "journal.css").read_text(encoding="utf-8"),
        print_css=(TEMPLATES / "journal-print.css").read_text(encoding="utf-8"),
        js=(TEMPLATES / "journal.js").read_text(encoding="utf-8"),
    )
    if mode == "print":
        for name, hexv in PRINT_COLOURS.items():
            html = html.replace(f'"var({name})"', f'"{hexv}"')
    return html


def render_app(trek_id: str) -> str:
    return render(trek_id, "app", photo_src=lambda p: f"/files/{p.path}", font_url=lambda f: f"/journal-assets/fonts/{f}")


# ---- Exports ----------------------------------------------------------------


@dataclass
class ExportFile:
    kind: str
    path: Path
    bytes: int


def export_dir(trek_id: str) -> Path:
    d = config.load().data_dir / "exports" / trek_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trek(trek_id: str) -> Trek:
    with db.connect() as conn:
        trek = db.get(conn, Trek, trek_id)
    if trek is None:
        raise KeyError(trek_id)
    return trek


def _photos(trek_id: str) -> list[Photo]:
    with db.connect() as conn:
        return db.fetch(conn, Photo, "trek_id = ?", (trek_id,), "t")


def _trim(trim: bool) -> float:
    return config.load().trim_ends_m if trim else 0


def write_static_site(trek_id: str, out: Path, *, trim: bool = True) -> Path:
    """index.html + fonts/ + photos/: opens from a USB stick, deploys to Render."""
    if out.exists():
        shutil.rmtree(out)
    (out / "fonts").mkdir(parents=True)
    (out / "photos").mkdir()
    for _, _, _, file in FONT_FACES:
        shutil.copy2(FONTS / file, out / "fonts" / file)
    for p in _photos(trek_id):
        shutil.copy2(config.media_path(p.path), out / "photos" / Path(p.path).name)
    html = render(trek_id, "export", photo_src=lambda p: f"photos/{Path(p.path).name}",
                  font_url=lambda f: f"fonts/{f}", trim_m=_trim(trim))
    (out / "index.html").write_text(html, encoding="utf-8")
    return out


def export_html(trek_id: str, trim: bool = True) -> ExportFile:
    base = export_dir(trek_id)
    site = write_static_site(trek_id, base / "html", trim=trim)
    zpath = base / f"{trek_id}-journal.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(site.rglob("*")):
            if f.is_file():
                zf.write(f, Path(f"{trek_id}-journal") / f.relative_to(site))
    return ExportFile("html", zpath, zpath.stat().st_size)


def _weasyprint():
    """WeasyPrint needs Pango. On Windows, find the MSYS2 copy before import."""
    if sys.platform == "win32" and not os.environ.get("WEASYPRINT_DLL_DIRECTORIES"):
        for cand in (Path(os.environ.get("LOCALAPPDATA", "")) / "msys64" / "mingw64" / "bin",
                     Path("C:/msys64/mingw64/bin")):
            if (cand / "libpango-1.0-0.dll").is_file():
                os.environ["WEASYPRINT_DLL_DIRECTORIES"] = str(cand)
                break
    try:
        import weasyprint
    except OSError as e:
        raise RuntimeError(
            "PDF export needs Pango. On Windows, install MSYS2 and run: pacman -S mingw-w64-x86_64-pango."
        ) from e
    return weasyprint


def export_pdf(trek_id: str, trim: bool = True) -> ExportFile:
    weasyprint = _weasyprint()
    html = render(trek_id, "print",
                  photo_src=lambda p: config.media_path(p.path).resolve().as_uri(),
                  font_url=lambda f: (FONTS / f).resolve().as_uri(), trim_m=_trim(trim))
    path = export_dir(trek_id) / f"{trek_id}-journal.pdf"
    weasyprint.HTML(string=html, base_url=str(config.REPO_ROOT)).write_pdf(path)
    return ExportFile("pdf", path, path.stat().st_size)


def export_gpx(trek_id: str, trim: bool = True) -> ExportFile:
    trek = _trek(trek_id)
    with db.connect() as conn:
        track = db.fetch(conn, TrackPoint, "trek_id = ?", (trek_id,), "t")
    path = export_dir(trek_id) / f"{trek_id}.gpx"
    path.write_text(gpx.write_gpx(gpx.trim_ends(track, _trim(trim)), trek.title), encoding="utf-8")
    return ExportFile("gpx", path, path.stat().st_size)


def markdown(trek_id: str, image_base: str = "photos/", trim: bool = True) -> str:
    view = journal.build(trek_id, photo_src=lambda p: f"{image_base}{Path(p.path).name}", trim_m=_trim(trim))
    out = [f"# {view.title}" + (f" ({view.title_mr})" if view.title_mr else ""), "", f"*{view.date_label}*", ""]
    if view.is_sample:
        out += ["> **Sample data.** The voice notes are invented and the photos are other people's, credited below each one.", ""]
    if view.opener:
        out += ["*" + "*  \n*".join(view.opener) + "*", ""]
    out += [" · ".join(f"**{v}** {k}" for v, k in view.stats), ""]

    def entry(e: journal.EntryView, closing: bool = False) -> None:
        stamp = e.time + (f" · {e.alt}" if e.alt else "") + (f" · {e.chip}" if e.chip else "")
        out.extend([f"`{stamp}`", ""])
        if e.quote:
            out.extend([f"> “{e.quote}”", ""])
        if e.translation:
            out.extend([f"*In {e.lang_name}. Gemma's translation: {e.translation}*", ""])
        if e.after_line and not closing:
            out.extend([e.after_line, ""])
        for p in e.photos:
            out.append(f"![{p.caption or 'A photo from the walk'}]({p.src})")
            cap = p.stamp + (f" · {p.caption}" if p.caption else "") + (f". Photo: {p.credit}" if p.credit else "")
            out.extend([f"*{cap}*", ""])

    for ch in view.chapters:
        out.extend([f"## {ch.label}: {ch.title}", ""])
        for e in ch.entries:
            entry(e)
    if view.closing:
        out.extend(["---", ""])
        entry(view.closing, closing=True)
    out.extend(["---", "", *[f"- {c}" for c in view.colophon]])
    if view.credits:
        out.extend(["", *[f"- {c}" for c in view.credits]])
    out.append("")
    return "\n".join(out)


def export_markdown(trek_id: str, trim: bool = True) -> ExportFile:
    path = export_dir(trek_id) / f"{trek_id}.md"
    path.write_text(markdown(trek_id, "photos/", trim), encoding="utf-8")
    return ExportFile("markdown", path, path.stat().st_size)


def dev_markdown(trek_id: str) -> str:
    """For pasting into a DEV post: images point at the published journal."""
    base = config.load().publish_base_url.rstrip("/")
    return markdown(trek_id, f"{base}/photos/" if base else "photos/")
