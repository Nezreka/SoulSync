"""THE HOME LAYOUT CONTRACT. every player (web, phone, tv) reads this, so
it is plain data with one meaning:

  layout  = {"version": 1, "billboard": bool, "rows": [row, ...]}
  row     = {"id": str, "style": style, "on": bool}
  style   = "standard" | "large" | "wide" | "ranked"
  ids     = the fixed ones in ROWS, "genre:<movie|show>:<genre name>",
            "lib-recent:<library id>", "lib-continue:<library id>" (a movie
            or TV library the profile can see) or "home-memories" (when it
            can see a home video library)

the server hands out the catalog (catalog()) with each row's label, hint,
the styles it takes and its default, and cleans every layout it stores or
returns (normalize()), so a client never has to guess: draw the rows that
are on, in order, in their style; skip a row you don't know or have no
cards for.

a profile's home page layout (which rows, in what order, in what style),
kept in player.db, per screen: a tv and a phone of one profile can each
have their own. a screen is a device id the player makes once and keeps;
ALL ('*') is the profile's shared layout, which any screen without its own
uses.

the server checks the shape and size, not the row names: the player knows
its rows and drops ones it doesn't (an old layout after an update, a row
from a newer build). a layout is small: 60 rows at most, plus the rows
about libraries (two a library, and on this day).

library rows (scrap/HOME_LIBRARIES_CONTRACT.md) follow the libraries the
profile can see right now, handed in as ``libs`` ([{id, kind, name}], from
core/player/libraries.visible): a row for a library that is gone or hidden
is dropped, a library's rows are added at the end, off, and "on this day"
comes in on, just after continue, the first time a home library shows up.
no ``libs`` is no libraries.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from core.player.libraries import MAX_LIBRARIES

TABLE = "player_home_layout"
MAX_ROWS = 60
VERSION = 1

# what each style means, for any client:
#   standard: the row's own cards (posters, squares or stills)
#   large:    the same cards, bigger
#   wide:     16:9 stills (a title's backdrop)
#   ranked:   the first ten, numbered, posters
STYLE_LABELS = {"standard": "Cards", "large": "Large cards", "wide": "Wide stills", "ranked": "Top 10"}
STYLES = set(STYLE_LABELS)

_POSTER = ["standard", "large", "wide", "ranked"]
_WIDE = ["wide", "large"]
_SQUARE = ["standard", "large"]

# the rows, in their default order: (id, label, hint, styles, default style, on by default)
ROWS = [
    ("continue", "Continue watching", "What you started, here and on your media server", _WIDE, "wide", True),
    ("live-guide", "On Live TV right now", "Tune straight into what's airing across your channels", _WIDE, "wide", True),
    ("premieres", "Premieres tonight", "What's new, and when it airs on the guide", _WIDE, "wide", True),
    ("new-episodes", "New episodes", "The latest episodes to land", _WIDE, "wide", True),
    ("collections", "Collections", "Curated movie & TV collections built in SoulSync", _POSTER, "standard", True),
    ("top-movies", "Top 10 movies today", "Today's most watched films that you have", _POSTER, "ranked", True),
    ("recent-movies", "Recently added movies", "Newest films first", _POSTER, "standard", True),
    ("discovery-playlists", "SoulSync Discovery Playlists", "Made for you, daily mixes, moods & curated playlists", _SQUARE, "standard", True),
    ("seasonal", "Seasonal spotlight", "Timely picks and seasonal collections for this time of year", _POSTER, "standard", True),
    ("because", "Because you have…", "Titles you own like ones you own", _POSTER, "standard", True),
    ("recent-music", "Recently added music", "Newest albums", _SQUARE, "standard", True),
    ("top-shows", "Top 10 shows today", "Today's most watched shows that you have", _POSTER, "ranked", False),
    ("recent-shows", "Recently added shows", "Shows that just arrived", _POSTER, "standard", False),
    ("youtube", "New on YouTube", "Fresh from the channels you follow", _WIDE, "wide", False),
    ("genre-picks", "Today's picks", "A new handful from your top genre every day", _POSTER, "standard", False),
    ("top-rated", "Top rated in your library", "The highest rated films you have", _POSTER, "standard", False),
    ("rediscover", "Rediscover", "Films you've had a while", _POSTER, "standard", False),
    ("audiobooks", "Recently added audiobooks", "Newest books", _SQUARE, "standard", False),
    ("podcasts", "Your podcasts", "The shows you follow", _SQUARE, "standard", False),
]
_ROW_BY_ID = {r[0]: r for r in ROWS}
GENRE_ROW = re.compile(r"^genre:(movie|show):([^\x00-\x1f]{1,60})$")
# rows about one library, and on this day (home videos)
LIB_ROW = re.compile(r"^lib-(recent|continue):(\d{1,9})$")
MEMORIES = "home-memories"
LIB_KINDS = ("movie", "show")
# two rows a library, and on this day
MAX_LIB_ROWS = 2 * MAX_LIBRARIES + 1
ALL = "*"
DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_ready: set = set()


class LayoutError(ValueError):
    pass


def ensure_schema(db: Any) -> None:
    key = str(getattr(db, "database_path", id(db)))
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                profile_id INTEGER NOT NULL,
                device_id TEXT NOT NULL,
                layout_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (profile_id, device_id)
            )""")
        conn.commit()
    _ready.add(key)


def is_library_row(rid: str) -> bool:
    return rid == MEMORIES or bool(LIB_ROW.match(rid or ""))


def _lib_index(libs: Optional[List[Dict[str, Any]]]) -> Dict[int, Dict[str, Any]]:
    return {int(lib["id"]): lib for lib in libs or [] if lib.get("kind") in LIB_KINDS}


def _has_home(libs: Optional[List[Dict[str, Any]]]) -> bool:
    return any(lib.get("kind") == "home" for lib in libs or [])


def _library_row_def(rid: str, libs: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """a library row's definition, named after the library as it is now;
    None when the profile can't see that library (or a home one)"""
    if rid == MEMORIES:
        if not _has_home(libs):
            return None
        return {"id": rid, "label": "On this day", "hint": "Home videos filmed on this day in years past",
                "styles": list(_WIDE), "style": "wide", "on": True}
    m = LIB_ROW.match(rid or "")
    lib = _lib_index(libs).get(int(m.group(2))) if m else None
    if lib is None:
        return None
    name = lib["name"]
    if m.group(1) == "recent":
        return {"id": rid, "label": f"Recently added in {name}", "hint": f"The newest in {name} first",
                "styles": list(_POSTER), "style": "standard", "on": False}
    return {"id": rid, "label": f"Continue in {name}", "hint": f"What you started in {name}",
            "styles": list(_WIDE), "style": "wide", "on": False}


def library_rows(libs: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """the rows these libraries give home: on this day (with a home library),
    then each movie or TV library's recently added and continue"""
    out = [_library_row_def(MEMORIES, libs)] if _has_home(libs) else []
    for lib_id in _lib_index(libs):
        out.append(_library_row_def(f"lib-recent:{lib_id}", libs))
        out.append(_library_row_def(f"lib-continue:{lib_id}", libs))
    return out


def row_def(rid: str, libs: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """a row's definition; genre and library rows are made from their id"""
    if is_library_row(rid):
        return _library_row_def(rid, libs)
    known = _ROW_BY_ID.get(rid)
    if known:
        rid, label, hint, styles, style, on = known
        return {"id": rid, "label": label, "hint": hint, "styles": list(styles), "style": style, "on": on}
    m = GENRE_ROW.match(rid or "")
    if not m:
        return None
    kind, genre = m.group(1), m.group(2)
    noun = "movies" if kind == "movie" else "shows"
    return {"id": rid, "label": f"{genre} {noun}", "hint": f"{noun.capitalize()} you have in {genre}",
            "styles": list(_POSTER), "style": "standard", "on": True}


def catalog(libs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """everything a client needs to draw and edit a home, for a profile that
    can see these libraries"""
    return {
        "version": VERSION,
        "styles": [{"id": k, "label": v} for k, v in STYLE_LABELS.items()],
        "rows": [row_def(r[0]) for r in ROWS] + library_rows(libs),
        "genre_row": "genre:<movie|show>:<genre name>",
        "library_rows": ["lib-recent:<library id>", "lib-continue:<library id>", MEMORIES],
        "max_rows": MAX_ROWS,
    }


def default_layout(libs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return normalize({"billboard": True, "rows": [{"id": r[0], "style": r[4], "on": r[5]} for r in ROWS]}, libs)


def _with_library_rows(rows: List[Dict[str, Any]], seen: set,
                       libs: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """the library rows a layout doesn't have yet: on this day on, after
    continue; a library's own rows at the end, off (offered, not forced)"""
    for d in library_rows(libs):
        if d["id"] in seen:
            continue
        row = {"id": d["id"], "style": d["style"], "on": d["id"] == MEMORIES}
        if d["id"] == MEMORIES:
            at = next((i + 1 for i, r in enumerate(rows) if r["id"] == "continue"), 0)
            rows.insert(at, row)
        else:
            rows.append(row)
    return rows


def normalize(layout: Any, libs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """any stored layout made safe to draw: unknown rows out, a style a row
    can't take back to its own, duplicates gone, and rows home gained since
    it was saved added at the end, off (nothing new appears on a home
    someone arranged). rows for a library the profile can't see (gone,
    hidden) are dropped; see the module's note for the rest."""
    if not isinstance(layout, dict):
        return default_layout(libs)
    rows, seen = [], set()
    own = lib_rows = 0
    for r in layout.get("rows") if isinstance(layout.get("rows"), list) else []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "")
        d = row_def(rid, libs)
        if not d or rid in seen:
            continue
        about_lib = is_library_row(rid)
        if (lib_rows if about_lib else own) >= (MAX_LIB_ROWS if about_lib else MAX_ROWS):
            continue
        if about_lib:
            lib_rows += 1
        else:
            own += 1
        seen.add(rid)
        style = r.get("style") if r.get("style") in d["styles"] else d["style"]
        rows.append({"id": rid, "style": style, "on": r.get("on") is not False})
    for r in ROWS:
        if r[0] not in seen and own < MAX_ROWS:
            own += 1
            rows.append({"id": r[0], "style": r[4], "on": False})
    rows = _with_library_rows(rows, seen, libs)
    return {"version": VERSION, "billboard": layout.get("billboard") is not False, "rows": rows}


def clean(layout: Any, libs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """a layout a client sent: refused when it isn't the contract's shape,
    then normalized. a library row is the contract's shape whichever
    library it names (one gone or hidden is dropped, not refused)."""
    if not isinstance(layout, dict):
        raise LayoutError("a layout is an object")
    rows = layout.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS + MAX_LIB_ROWS:
        raise LayoutError(f"rows must be a list of at most {MAX_ROWS} (and the library rows)")
    own = 0
    for r in rows:
        if not isinstance(r, dict):
            raise LayoutError("each row is an object")
        rid = str(r.get("id") or "")
        if not is_library_row(rid):
            own += 1
            if not row_def(rid):
                raise LayoutError(f"not a row: {rid[:40]!r}")
        if str(r.get("style") or "standard") not in STYLES:
            raise LayoutError(f"not a style: {str(r.get('style'))[:20]!r}")
    if own > MAX_ROWS:
        raise LayoutError(f"rows must be a list of at most {MAX_ROWS} (and the library rows)")
    return normalize(layout, libs)


def device(value: Any) -> str:
    """a screen's id, or ALL. junk is refused, not guessed at."""
    text = str(value or "").strip()
    if not text or text == ALL:
        return ALL
    if not DEVICE_ID.match(text):
        raise LayoutError("not a device id")
    return text


def get(db: Any, profile_id: int, device_id: str = ALL,
        libs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """{layout, scope}: this screen's own (scope 'device'), else the
    profile's shared one ('all'), else the default (scope None). always a
    clean layout, its library rows those of ``libs``."""
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(
            f"SELECT device_id, layout_json FROM {TABLE} WHERE profile_id = ? AND device_id IN (?, ?)",
            (int(profile_id), device_id, ALL)).fetchall()
    found = {r[0]: r[1] for r in rows}
    order = [(device_id, "device")] if device_id != ALL else []
    for key, scope in order + [(ALL, "all")]:
        if key in found:
            try:
                return {"layout": normalize(json.loads(found[key]), libs), "scope": scope}
            except (TypeError, ValueError):
                continue
    return {"layout": default_layout(libs), "scope": None}


def put(db: Any, profile_id: int, layout: Any, device_id: str = ALL,
        libs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """save for one screen, or for all of them (which also clears this
    screen's own, so it follows the shared one from now on)"""
    cleaned = clean(layout, libs)
    ensure_schema(db)
    with db._get_connection() as conn:
        conn.execute(
            f"INSERT INTO {TABLE} (profile_id, device_id, layout_json, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(profile_id, device_id) DO UPDATE SET layout_json = excluded.layout_json, "
            "updated_at = excluded.updated_at",
            (int(profile_id), device_id, json.dumps(cleaned), time.time()))
        conn.commit()
    return cleaned


def reset(db: Any, profile_id: int, device_id: str = ALL) -> None:
    """this screen back to the shared layout (or, for ALL, every screen to the default)"""
    ensure_schema(db)
    with db._get_connection() as conn:
        if device_id == ALL:
            conn.execute(f"DELETE FROM {TABLE} WHERE profile_id = ?", (int(profile_id),))
        else:
            conn.execute(f"DELETE FROM {TABLE} WHERE profile_id = ? AND device_id = ?",
                         (int(profile_id), device_id))
        conn.commit()
