"""libraries in the player: which ones get a page of their own, and which a
profile can't see.

the libraries themselves are the video side's (core/video/libraries.py,
#2029): a name, a kind (movie or show) and a folder, and every movie and
show is already stamped with the one its files sit in
(movies.video_library_id / shows.video_library_id, by
libraries.attribute_files). nothing here changes any of that. this is only
how the player shows them:

  own page   an admin can give a library its own entry in the player's
             sidebar and its own page, with its own rows ("Anime", "4K",
             "Kids"). off for every library until switched on, so the player
             looks exactly as it did: one Movies, one TV Shows. a library
             without its own page stays part of Movies / TV Shows.
  hidden     an admin can hide a library from a profile. its titles are then
             gone for that profile everywhere the video side answers: the
             catalog, search, a title's page, its stream (the same gate the
             rating cap uses, api/video/kids.py). an admin always sees every
             library.

a title that sits in no library folder counts as its kind's main library, so
"hide the main library" means what it says.

kept in player.db; made on first use like the other player tables.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, FrozenSet, Iterable, List, Optional

HIDDEN = "player_library_hidden"
PAGES = "player_library_pages"

_ready: set = set()
_lock = threading.Lock()
# profile id -> its hidden library ids, per db; cleared on every write.
# video_cap() asks on every video request, so this is not read from disk each time
_hidden_cache: Dict[str, Dict[int, FrozenSet[int]]] = {}


class LibraryError(ValueError):
    pass


def _key(db: Any) -> str:
    return str(getattr(db, "database_path", id(db)))


def ensure_schema(db: Any) -> None:
    key = _key(db)
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {HIDDEN} (
                library_id INTEGER NOT NULL,
                profile_id INTEGER NOT NULL,
                PRIMARY KEY (library_id, profile_id)
            )""")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {PAGES} (library_id INTEGER PRIMARY KEY)")
        conn.commit()
    _ready.add(key)


def hidden_for(db: Any, profile_id: Optional[int]) -> FrozenSet[int]:
    """the library ids hidden from this profile (empty for no profile)"""
    if profile_id is None:
        return frozenset()
    key = _key(db)
    with _lock:
        hit = _hidden_cache.get(key, {}).get(int(profile_id))
    if hit is not None:
        return hit
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(f"SELECT library_id FROM {HIDDEN} WHERE profile_id = ?",
                            (int(profile_id),)).fetchall()
    out = frozenset(int(r[0]) for r in rows)
    with _lock:
        _hidden_cache.setdefault(key, {})[int(profile_id)] = out
    return out


def _forget(db: Any) -> None:
    with _lock:
        _hidden_cache.pop(_key(db), None)


def own_page_ids(db: Any) -> FrozenSet[int]:
    ensure_schema(db)
    with db._get_connection() as conn:
        return frozenset(int(r[0]) for r in conn.execute(f"SELECT library_id FROM {PAGES}").fetchall())


def set_own_page(db: Any, library_id: int, on: bool) -> None:
    ensure_schema(db)
    with db._get_connection() as conn:
        if on:
            conn.execute(f"INSERT OR IGNORE INTO {PAGES} (library_id) VALUES (?)", (int(library_id),))
        else:
            conn.execute(f"DELETE FROM {PAGES} WHERE library_id = ?", (int(library_id),))
        conn.commit()


def set_hidden_from(db: Any, library_id: int, profile_ids: Iterable[Any]) -> List[int]:
    """the profiles this library is hidden from, replaced whole"""
    try:
        wanted = sorted({int(p) for p in profile_ids})
    except (TypeError, ValueError) as exc:
        raise LibraryError("hidden_from is a list of profile ids") from exc
    ensure_schema(db)
    with db._get_connection() as conn:
        conn.execute(f"DELETE FROM {HIDDEN} WHERE library_id = ?", (int(library_id),))
        conn.executemany(f"INSERT INTO {HIDDEN} (library_id, profile_id) VALUES (?, ?)",
                         [(int(library_id), p) for p in wanted])
        conn.commit()
    _forget(db)
    return wanted


def forget_library(db: Any, library_id: int) -> None:
    """a library that was deleted: drop what the player kept about it"""
    ensure_schema(db)
    with db._get_connection() as conn:
        conn.execute(f"DELETE FROM {HIDDEN} WHERE library_id = ?", (int(library_id),))
        conn.execute(f"DELETE FROM {PAGES} WHERE library_id = ?", (int(library_id),))
        conn.commit()
    _forget(db)


def _shown(video_libs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """the libraries worth showing: switched on, in the admin's order"""
    return [lib for lib in video_libs if lib.get("enabled")]


def visible(db: Any, video_libs: List[Dict[str, Any]], profile_id: Optional[int], *,
            is_admin: bool = False, kinds: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """the libraries this profile can see, as the player lists them:
    {id, kind, name, own_page, main}. ``kinds``: only these ('movie', 'show')."""
    hidden = frozenset() if is_admin else hidden_for(db, profile_id)
    pages = own_page_ids(db)
    allowed = set(kinds) if kinds is not None else None
    out = []
    for lib in _shown(video_libs):
        if lib["id"] in hidden or (allowed is not None and lib["kind"] not in allowed):
            continue
        main = lib.get("builtin") == "main"
        out.append({"id": lib["id"], "kind": lib["kind"], "name": lib["name"],
                    # the main library is Movies / TV Shows itself: it has no second page
                    "own_page": (lib["id"] in pages) and not main, "main": main})
    return out


def admin_view(db: Any, video_libs: List[Dict[str, Any]], counts: Dict[int, int]) -> List[Dict[str, Any]]:
    """every library with what the player does with it, for the admin page"""
    ensure_schema(db)
    pages = own_page_ids(db)
    with db._get_connection() as conn:
        rows = conn.execute(f"SELECT library_id, profile_id FROM {HIDDEN}").fetchall()
    hidden: Dict[int, List[int]] = {}
    for lib_id, pid in rows:
        hidden.setdefault(int(lib_id), []).append(int(pid))
    out = []
    for lib in _shown(video_libs):
        main = lib.get("builtin") == "main"
        out.append({"id": lib["id"], "kind": lib["kind"], "name": lib["name"], "main": main,
                    "own_page": (lib["id"] in pages) and not main,
                    "titles": int(counts.get(lib["id"], 0)),
                    "hidden_from": sorted(hidden.get(lib["id"], []))})
    return out


__all__ = ["LibraryError", "hidden_for", "own_page_ids", "set_own_page", "set_hidden_from",
           "forget_library", "visible", "admin_view", "ensure_schema"]
