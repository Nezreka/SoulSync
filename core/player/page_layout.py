"""THE PAGE LAYOUT CONTRACT: the rows of every browse page but home.

home had a contract (core/player/home_layout.py) and nothing else did: the
movies, tv, music and youtube pages were each written out by hand, once in
the web player and again in the app, and the two drifted until the app's
movies page had none of the web's recommendation rows and the web's music
page none of the app's listening rows. this is the same contract for those
pages, so a row exists once, here, and every client draws from it:

  layout = {"version": 1, "rows": [{"id", "style", "on"}, ...]}
  page   = "movies" | "tv" | "music" | "youtube", or "<page>:lib:<id>" for
           one library's page (it takes its page's rows, and keeps its own
           arrangement)

each page has its rows in their default order; the ones that are off by
default are the extra rows a person can switch on. styles mean what they
mean on home. genre rows ("genre:<movie|show>:<name>") can be added to the
movies and tv pages.

kept per profile and per screen exactly like home: a screen's own layout,
else the profile's shared one ('*'), else the default.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from core.player.home_layout import (ALL, GENRE_ROW, MAX_ROWS, STYLE_LABELS, STYLES, VERSION,
                                     LayoutError, device)

TABLE = "player_page_layout"

_POSTER = ["standard", "large", "wide", "ranked"]
_WIDE = ["wide", "large"]
_SQUARE = ["standard", "large"]
_TILE = ["standard"]

# (id, label, hint, styles, default style, on by default)
_MOVIES = [
    ("continue", "Continue watching", "Movies you started", _WIDE, "wide", True),
    ("recommended", "Recommended for you", "From your library, picked from what you watch", _POSTER, "standard", True),
    ("recent", "Recently added", "Newest films first", _POSTER, "standard", True),
    ("recommended-add", "Recommended movies to add", "Films you don't have that fit what you watch", _POSTER, "standard", True),
    ("more-like", "More like…", "Rows built from titles you own", _POSTER, "standard", True),
    ("top-rated", "Top rated in your library", "The highest rated films you have", _POSTER, "standard", True),
    ("genres", "Your top genres", "A row for each of your three biggest genres", _POSTER, "standard", True),
    ("popular", "Popular right now", "What everyone is watching", _POSTER, "standard", True),
    ("in-theaters", "In theaters", "Playing in cinemas now", _POSTER, "standard", True),
    ("coming-soon", "Coming soon", "Releases on the way", _POSTER, "standard", True),
    ("top-all-time", "Top rated of all time", "The all-time list", _POSTER, "standard", True),
    ("rediscover", "Rediscover", "Films you've had a year or more", _POSTER, "standard", True),
    # the extra rows: off until someone switches them on
    ("new-releases", "New releases", "Films from this year and last that you have", _POSTER, "standard", False),
    ("collections", "Collections", "Your movie collections", _POSTER, "standard", False),
    ("acclaimed", "Critically acclaimed", "Rated 8 and up", _POSTER, "standard", False),
    ("top-10", "Top 10 in your library", "Your ten highest rated, numbered", _POSTER, "ranked", False),
    ("thrills", "Edge of your seat", "Thrillers, horror and action", _POSTER, "standard", False),
    ("feel-good", "Feel-good", "Comedy, romance and family films", _POSTER, "standard", False),
    ("family", "Family night", "Rated G and PG", _POSTER, "standard", False),
    ("quick-watches", "Quick watches", "Under 100 minutes", _POSTER, "standard", False),
    ("epics", "Epic cinema", "Two and a half hours and up", _POSTER, "standard", False),
    ("classics", "Timeless classics", "Thirty years old or more", _POSTER, "standard", False),
    ("decades", "By decade", "A row for each of your three biggest decades", _POSTER, "standard", False),
    ("studios", "Studios", "Browse by the studios you have most of", _TILE, "standard", False),
]

_TV = [
    ("continue", "Continue watching", "Shows you're in the middle of", _WIDE, "wide", True),
    ("new-episodes", "New episodes", "The latest episodes to land", _WIDE, "wide", True),
    ("recommended", "Recommended for you", "From your library, picked from what you watch", _POSTER, "standard", True),
    ("recent", "Recently added", "Shows that just arrived", _POSTER, "standard", True),
    ("recommended-add", "Recommended shows to add", "Shows you don't have that fit what you watch", _POSTER, "standard", True),
    ("more-like", "More like…", "Rows built from titles you own", _POSTER, "standard", True),
    ("top-rated", "Top rated in your library", "The highest rated shows you have", _POSTER, "standard", True),
    ("genres", "Your top genres", "A row for each of your three biggest genres", _POSTER, "standard", True),
    ("popular", "Popular right now", "What everyone is watching", _POSTER, "standard", True),
    ("airing-today", "Airing today", "New episodes out today", _POSTER, "standard", True),
    ("on-the-air", "On the air this week", "Shows with episodes this week", _POSTER, "standard", True),
    ("top-all-time", "Top rated of all time", "The all-time list", _POSTER, "standard", True),
    ("rediscover", "Rediscover", "Shows you've had a year or more", _POSTER, "standard", True),
    ("new-series", "New series", "Shows from this year and last that you have", _POSTER, "standard", False),
    ("collections", "Collections", "Your TV collections", _POSTER, "standard", False),
    ("acclaimed", "Critically acclaimed", "Rated 8 and up", _POSTER, "standard", False),
    ("top-10", "Top 10 in your library", "Your ten highest rated, numbered", _POSTER, "ranked", False),
    ("comfort", "Comfort and comedy", "Comedies and family shows", _POSTER, "standard", False),
    ("drama-scifi", "Drama and sci-fi", "Dramas, sci-fi and fantasy", _POSTER, "standard", False),
    ("family", "Family night", "Rated TV-Y to TV-PG", _POSTER, "standard", False),
    ("classics", "Timeless classics", "Twenty years old or more", _POSTER, "standard", False),
    ("top-network", "Best of your top network", "From the network you have most of", _POSTER, "standard", False),
    ("networks", "Networks", "Browse by the networks you have most of", _TILE, "standard", False),
]

_MUSIC = [
    ("recently-played", "Recently played", "What you listened to last", _SQUARE, "standard", True),
    ("stations", "Stations & Artist Mixes", "Endless radio from your library", _SQUARE, "standard", True),
    ("discovery", "Made for you · SoulSync Discovery", "Daily mixes, moods and curated playlists", _SQUARE, "standard", True),
    ("heavy-rotation", "Heavy rotation", "Your most played artists these 30 days", _SQUARE, "standard", True),
    ("recent-albums", "Recently added albums", "Newest albums", _SQUARE, "standard", True),
    ("artists", "Artists in your library", "A daily handful of your artists", _SQUARE, "standard", True),
    ("playlists", "Your playlists", "The playlists you made and follow", _SQUARE, "standard", True),
    ("top-albums", "Most played albums", "Your top albums these 30 days", _SQUARE, "standard", False),
    ("top-tracks", "Most played tracks", "Your top tracks these 30 days", _SQUARE, "standard", False),
    ("all-time-artists", "Most played of all time", "The artists you've played most, ever", _SQUARE, "standard", False),
]

_YOUTUBE = [
    ("continue", "Continue watching", "Videos you started", _WIDE, "wide", True),
    ("latest", "Latest from your channels", "Newest uploads first", _WIDE, "wide", True),
    ("channels", "Your channels", "The channels you follow", _TILE, "standard", True),
    ("downloaded", "Downloaded", "Everything on disk", _WIDE, "wide", True),
    ("by-channel", "A row per channel", "The latest from each of your biggest channels", _WIDE, "wide", True),
    ("this-week", "This week", "Uploaded in the last seven days", _WIDE, "wide", False),
    ("quick", "Quick watches", "Under ten minutes", _WIDE, "wide", False),
    ("sit-down", "Sit-down watches", "Ten to thirty minutes", _WIDE, "wide", False),
    ("long", "Long watches", "Thirty minutes and up", _WIDE, "wide", False),
    ("most-viewed", "Most viewed", "The biggest videos you have", _WIDE, "wide", False),
]

PAGES: Dict[str, list] = {"movies": _MOVIES, "tv": _TV, "music": _MUSIC, "youtube": _YOUTUBE}
# which genre rows a page takes
_GENRE_KIND = {"movies": "movie", "tv": "show"}
PAGE_KEY = re.compile(r"^(movies|tv|music|youtube)(?::lib:(\d{1,9}))?$")

_ready: set = set()


def page(value: Any) -> str:
    """a page key, checked. junk is refused, not guessed at."""
    text = str(value or "").strip().lower()
    if not PAGE_KEY.match(text):
        raise LayoutError("not a page")
    return text


def base(page_key: str) -> str:
    return page_key.split(":", 1)[0]


def ensure_schema(db: Any) -> None:
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                profile_id INTEGER NOT NULL,
                page TEXT NOT NULL,
                device_id TEXT NOT NULL,
                layout_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (profile_id, page, device_id)
            )""")
        conn.commit()
    _ready.add(key)


def _rows(page_key: str) -> list:
    return PAGES[base(page_key)]


def row_def(page_key: str, rid: str) -> Optional[Dict[str, Any]]:
    for r in _rows(page_key):
        if r[0] == rid:
            return {"id": r[0], "label": r[1], "hint": r[2], "styles": list(r[3]), "style": r[4], "on": r[5]}
    m = GENRE_ROW.match(rid or "")
    kind = _GENRE_KIND.get(base(page_key))
    if not m or not kind or m.group(1) != kind:
        return None
    genre = m.group(2)
    noun = "movies" if kind == "movie" else "shows"
    return {"id": rid, "label": f"{genre} {noun}", "hint": f"{noun.capitalize()} you have in {genre}",
            "styles": list(_POSTER), "style": "standard", "on": True}


def catalog(page_key: str) -> Dict[str, Any]:
    """everything a client needs to draw and edit the page"""
    kind = _GENRE_KIND.get(base(page_key))
    return {
        "version": VERSION,
        "page": page_key,
        "styles": [{"id": k, "label": v} for k, v in STYLE_LABELS.items()],
        "rows": [row_def(page_key, r[0]) for r in _rows(page_key)],
        "genre_row": f"genre:{kind}:<genre name>" if kind else None,
        "max_rows": MAX_ROWS,
    }


def default_layout(page_key: str) -> Dict[str, Any]:
    return {"version": VERSION, "rows": [{"id": r[0], "style": r[4], "on": r[5]} for r in _rows(page_key)]}


def normalize(page_key: str, layout: Any) -> Dict[str, Any]:
    """a stored layout made safe to draw: rows the page doesn't have are
    out, a style a row can't take goes back to its own, duplicates are gone,
    and rows the page gained since it was saved are added at the end, off."""
    if not isinstance(layout, dict):
        return default_layout(page_key)
    rows: List[Dict[str, Any]] = []
    seen = set()
    for r in layout.get("rows") if isinstance(layout.get("rows"), list) else []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "")
        d = row_def(page_key, rid)
        if not d or rid in seen:
            continue
        seen.add(rid)
        style = r.get("style") if r.get("style") in d["styles"] else d["style"]
        rows.append({"id": rid, "style": style, "on": r.get("on") is not False})
    for r in _rows(page_key):
        if r[0] not in seen:
            rows.append({"id": r[0], "style": r[4], "on": False})
    return {"version": VERSION, "rows": rows[:MAX_ROWS]}


def clean(page_key: str, layout: Any) -> Dict[str, Any]:
    """a layout a client sent: refused when it isn't the contract's shape"""
    if not isinstance(layout, dict):
        raise LayoutError("a layout is an object")
    rows = layout.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise LayoutError(f"rows must be a list of at most {MAX_ROWS}")
    for r in rows:
        if not isinstance(r, dict):
            raise LayoutError("each row is an object")
        rid = str(r.get("id") or "")
        if not row_def(page_key, rid):
            raise LayoutError(f"not a row of this page: {rid[:40]!r}")
        if str(r.get("style") or "standard") not in STYLES:
            raise LayoutError(f"not a style: {str(r.get('style'))[:20]!r}")
    return normalize(page_key, layout)


def get(db: Any, profile_id: int, page_key: str, device_id: str = ALL) -> Dict[str, Any]:
    """{layout, scope}: this screen's own ('device'), else the profile's
    shared one ('all'), else the default (None). a library's page with no
    layout of its own follows its page's ("movies:lib:3" follows "movies"),
    so arranging Movies once arranges every movie library until one is
    arranged on its own."""
    ensure_schema(db)
    pages = [page_key] + ([base(page_key)] if base(page_key) != page_key else [])
    with db._get_connection() as conn:
        found = {(r[0], r[1]): r[2] for r in conn.execute(
            f"SELECT page, device_id, layout_json FROM {TABLE} WHERE profile_id = ? "
            f"AND page IN ({','.join('?' * len(pages))}) AND device_id IN (?, ?)",
            (int(profile_id), *pages, device_id, ALL)).fetchall()}
    for p in pages:
        order = ([(device_id, "device")] if device_id != ALL else []) + [(ALL, "all")]
        for key, scope in order:
            if (p, key) in found:
                try:
                    return {"layout": normalize(page_key, json.loads(found[(p, key)])),
                            "scope": scope, "inherited": p != page_key}
                except (TypeError, ValueError):
                    continue
    return {"layout": default_layout(page_key), "scope": None, "inherited": False}


def put(db: Any, profile_id: int, page_key: str, layout: Any, device_id: str = ALL) -> Dict[str, Any]:
    cleaned = clean(page_key, layout)
    ensure_schema(db)
    with db._get_connection() as conn:
        conn.execute(
            f"INSERT INTO {TABLE} (profile_id, page, device_id, layout_json, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(profile_id, page, device_id) DO UPDATE SET layout_json = excluded.layout_json, "
            "updated_at = excluded.updated_at",
            (int(profile_id), page_key, device_id, json.dumps(cleaned), time.time()))
        conn.commit()
    return cleaned


def reset(db: Any, profile_id: int, page_key: str, device_id: str = ALL) -> None:
    """this screen back to the shared layout (or, for ALL, the page back to
    its default on every screen)"""
    ensure_schema(db)
    with db._get_connection() as conn:
        if device_id == ALL:
            conn.execute(f"DELETE FROM {TABLE} WHERE profile_id = ? AND page = ?", (int(profile_id), page_key))
        else:
            conn.execute(f"DELETE FROM {TABLE} WHERE profile_id = ? AND page = ? AND device_id = ?",
                         (int(profile_id), page_key, device_id))
        conn.commit()


__all__ = ["PAGES", "LayoutError", "ALL", "page", "base", "device", "catalog", "default_layout",
           "normalize", "clean", "get", "put", "reset", "row_def"]
