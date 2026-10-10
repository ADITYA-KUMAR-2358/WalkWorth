"""Who may change a trek. There are no accounts.

Creating a trek sets a random owner token in an HTTP-only cookie and stores
its hash next to the trek. Only that browser sees the trek on its shelf and
may change it. Anyone with a trek's link can read its journal (ids carry
48 random bits), but raw audio is served to the owner only. The sample is
read-only for everyone.

ACCESS_CODE, when set, is needed to create a trek. Everything that spends
model quota happens on a trek you created, so the code guards the quota too.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3

from fastapi import HTTPException, Request, Response

from . import config, db
from .schema import Trek

COOKIE = "ww_owner"
COOKIE_MAX_AGE_S = 365 * 24 * 3600
CODE_HEADER = "x-access-code"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token(request: Request) -> str | None:
    value = request.cookies.get(COOKIE, "")
    return value if 16 <= len(value) <= 128 else None


def ensure_token(request: Request, response: Response) -> str:
    """This browser's owner token, set as a cookie the first time."""
    if t := token(request):
        return t
    t = secrets.token_urlsafe(32)
    response.set_cookie(COOKIE, t, max_age=COOKIE_MAX_AGE_S, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")
    return t


def claim(conn: sqlite3.Connection, trek_id: str, owner_token: str) -> None:
    conn.execute("INSERT INTO trek_owner (trek_id, owner) VALUES (?, ?) ON CONFLICT DO NOTHING",
                 (trek_id, _hash(owner_token)))


def _owner(conn: sqlite3.Connection, trek_id: str) -> str | None:
    row = conn.execute("SELECT owner FROM trek_owner WHERE trek_id = ?", (trek_id,)).fetchone()
    return row[0] if row else None


def is_mine(conn: sqlite3.Connection, trek: Trek, request: Request) -> bool:
    """Treks from before ownership existed have no owner and stay open."""
    if trek.is_sample:
        return False
    owner = _owner(conn, trek.id)
    if owner is None:
        return True
    t = token(request)
    return t is not None and hmac.compare_digest(owner, _hash(t))


def on_shelf(conn: sqlite3.Connection, trek: Trek, request: Request) -> bool:
    return trek.is_sample or is_mine(conn, trek, request)


def writable(conn: sqlite3.Connection, trek_id: str, request: Request) -> Trek:
    """The trek, if this browser may change it; 404 or 403 otherwise."""
    trek = db.get(conn, Trek, trek_id)
    if trek is None:
        raise HTTPException(404, f"No trek with id '{trek_id}'.")
    if trek.is_sample:
        raise HTTPException(403, "The sample is read-only. Start a walk or import your own to edit one.")
    if not is_mine(conn, trek, request):
        raise HTTPException(403, "Only the browser that made this walk can change it.")
    return trek


def can_read_audio(conn: sqlite3.Connection, trek_id: str, request: Request) -> bool:
    trek = db.get(conn, Trek, trek_id)
    return trek is not None and is_mine(conn, trek, request)


def code_required() -> bool:
    return bool(config.load().access_code)


def check_code(request: Request) -> None:
    code = config.load().access_code
    if not code:
        return
    given = request.headers.get(CODE_HEADER, "").strip()
    if not given:
        raise HTTPException(401, "This Walkworth needs an access code to start a walk. Ask whoever shared the link.")
    if not hmac.compare_digest(given.encode(), code.encode()):
        raise HTTPException(401, "That access code isn't right. Check it and try again.")
