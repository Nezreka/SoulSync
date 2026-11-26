"""libraries in the player: the folders you keep your video in, grouped and
named the way you want to browse them.

the manager already knows every folder a kind of video lives in (the main
one, the extra drives, the server's folders it was told how to reach:
core/video/library_roots.py). a player library is a NAME and a set of those
folders:

  * every folder starts in its kind's default library ("Movies", "TV Shows"),
    so a server nobody has set up looks exactly as it always did;
  * an admin can make more libraries ("Anime", "Kids", "4K"), move folders
    into them and rename any of them (player setup). a library that isn't
    the default one is a place of its own in the player: its own entry in
    the sidebar, its own page, its own rows;
  * an admin can hide a library from a profile. its titles are then gone
    for that profile wherever the video side answers (the catalog, search, a
    title's page, its poster, its stream), through the same gate the rating
    cap uses (api/video/kids.py). an admin sees every library.

a title is in the library of the folder its file sits in. a title whose file
is in none of the folders (or has no file path) is in the default library.

home videos (core/video/home_videos.py) are a third kind, ``home``. their
"folders" are the home video roots: each home videos folder on disk and each
server home video library picked to scan. the "Home videos" library is made
by the first scan that finds a root (ensure_home), so a server with no home
videos never shows one; there is no fixed id for it.

every library has ``in_guide``: whether the guide schedules its titles. home
libraries start off, the rest on.

nothing here touches where downloads are filed: that is the manager's own
"video libraries" (core/video/libraries.py), a different thing.

kept in player.db. the tables are made by the first WRITE: every video
request asks what is hidden, and a read must never create a table.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("player.libraries")

LIBS = "player_libraries"
FOLDERS = "player_library_folders"
HIDDEN = "player_library_hidden"
KINDS = ("movie", "show", "home")
# the kinds whose titles live in the movie / show tables (the resolver's)
TITLE_KINDS = ("movie", "show")
# the default libraries exist before anything is saved, by these ids. the
# home one is made when there are home videos (ensure_home)
DEFAULTS = {"movie": (1, "Movies"), "show": (2, "TV Shows")}
HOME_NAME = "Home videos"
MAX_NAME = 60
MAX_LIBRARIES = 40

_ready: set = set()
_lock = threading.Lock()
_version = 0                        # bumped by every write: caches key on it
_hidden_cache: Dict[str, Dict[int, FrozenSet[int]]] = {}
_resolver_cache: Dict[str, Tuple[float, int, "Resolver"]] = {}
RESOLVER_SECONDS = 60


class LibraryError(ValueError):
    pass


def _key(db: Any) -> str:
    return str(getattr(db, "database_path", id(db)))


def version() -> int:
    return _version


def _changed(db: Any) -> None:
    global _version
    with _lock:
        _version += 1
        _hidden_cache.pop(_key(db), None)
        _resolver_cache.clear()


def norm(path: Any) -> str:
    """one spelling per folder (separators, case on windows, a trailing slash)"""
    from core.video.owned_ids import _norm
    return _norm(path)


def ensure_schema(db: Any) -> None:
    key = _key(db)
    if key in _ready:
        return
    with db._get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {LIBS} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                is_default INTEGER NOT NULL DEFAULT 0
            )""")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {FOLDERS} (
                kind TEXT NOT NULL,
                folder TEXT NOT NULL,
                library_id INTEGER NOT NULL,
                PRIMARY KEY (kind, folder)
            )""")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {HIDDEN} (
                library_id INTEGER NOT NULL,
                profile_id INTEGER NOT NULL,
                PRIMARY KEY (library_id, profile_id)
            )""")
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({LIBS})")}
        if "in_guide" not in cols:
            # NULL: the kind's default (home off, the rest on)
            conn.execute(f"ALTER TABLE {LIBS} ADD COLUMN in_guide INTEGER")
        for kind, (lib_id, name) in DEFAULTS.items():
            conn.execute(
                f"INSERT OR IGNORE INTO {LIBS} (id, kind, name, position, is_default) VALUES (?, ?, ?, 0, 1)",
                (lib_id, kind, name))
        conn.commit()
    _ready.add(key)


def _in_guide(kind: str, value: Any) -> bool:
    return (kind != "home") if value is None else bool(value)


def _read(db: Any, sql: str, args: tuple = ()) -> list:
    """rows for a read; no table yet is nothing saved yet"""
    try:
        with db._get_connection() as conn:
            return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            return []
        raise


# ── what is saved ─────────────────────────────────────────────────────────

def libraries(db: Any) -> List[Dict[str, Any]]:
    """every library, defaults first then by position: {id, kind, name,
    is_default, folders}. before anything is saved: the two defaults."""
    order = "ORDER BY kind, is_default DESC, position, id"
    try:
        rows = _read(db, f"SELECT id, kind, name, position, is_default, in_guide FROM {LIBS} {order}")
    except sqlite3.OperationalError as e:
        if "no such column" not in str(e).lower():
            raise
        # a table from before in_guide that nothing has written to since
        rows = [(*r, None) for r in _read(db, f"SELECT id, kind, name, position, is_default FROM {LIBS} {order}")]
    if not rows:
        rows = [(lib_id, kind, name, 0, 1, None) for kind, (lib_id, name) in DEFAULTS.items()]
    by_lib: Dict[int, List[str]] = {}
    for _kind, folder, lib_id in _read(db, f"SELECT kind, folder, library_id FROM {FOLDERS}"):
        by_lib.setdefault(int(lib_id), []).append(str(folder))
    return [{"id": int(r[0]), "kind": r[1], "name": r[2], "is_default": bool(r[4]),
             "in_guide": _in_guide(r[1], r[5]),
             "folders": sorted(by_lib.get(int(r[0]), []))} for r in rows]


def default_id(libs: List[Dict[str, Any]], kind: str) -> Optional[int]:
    for lib in libs:
        if lib["kind"] == kind and lib["is_default"]:
            return lib["id"]
    return DEFAULTS[kind][0] if kind in DEFAULTS else None


def hidden_for(db: Any, profile_id: Optional[int]) -> FrozenSet[int]:
    """the library ids hidden from this profile (empty for no profile)"""
    if profile_id is None:
        return frozenset()
    key = _key(db)
    with _lock:
        hit = _hidden_cache.get(key, {}).get(int(profile_id))
    if hit is not None:
        return hit
    rows = _read(db, f"SELECT library_id FROM {HIDDEN} WHERE profile_id = ?", (int(profile_id),))
    out = frozenset(int(r[0]) for r in rows)
    with _lock:
        _hidden_cache.setdefault(key, {})[int(profile_id)] = out
    return out


def hidden_map(db: Any) -> Dict[int, List[int]]:
    """{library id: the profiles it is hidden from}"""
    out: Dict[int, List[int]] = {}
    for lib_id, pid in _read(db, f"SELECT library_id, profile_id FROM {HIDDEN}"):
        out.setdefault(int(lib_id), []).append(int(pid))
    return {k: sorted(v) for k, v in out.items()}


def visible(db: Any, profile_id: Optional[int], *, is_admin: bool = False,
            kinds: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """the libraries this profile can see, as the player lists them:
    {id, kind, name, own_page, main, in_guide}. a library that isn't its
    kind's default has a page of its own; every home library does."""
    hidden = frozenset() if is_admin else hidden_for(db, profile_id)
    allowed = set(kinds) if kinds is not None else None
    return [{"id": lib["id"], "kind": lib["kind"], "name": lib["name"],
             "own_page": lib["kind"] == "home" or not lib["is_default"], "main": lib["is_default"],
             "in_guide": lib["in_guide"]}
            for lib in libraries(db)
            if lib["id"] not in hidden and (allowed is None or lib["kind"] in allowed)]


# ── which library a title is in ───────────────────────────────────────────

def roots(video_db: Any, kind: str) -> List[str]:
    """the folders the manager keeps this kind of video in (for home: the
    home video roots, core/video/home_videos.roots)"""
    if kind == "home":
        try:
            from core.video.home_videos import roots as home_roots
            return [r["id"] for r in home_roots(video_db)]
        except Exception:  # noqa: BLE001
            logger.debug("home video roots unreadable", exc_info=True)
            return []
    from core.video.library_roots import library_roots
    try:
        return list(library_roots(video_db, "movie" if kind == "movie" else "episode"))
    except Exception:  # noqa: BLE001 - no folders is every title in the default library
        logger.debug("library roots unreadable", exc_info=True)
        return []


class Resolver:
    """a file path -> the library it is in. built once from the folders and
    their assignments, then asked thousands of times (once per title)."""

    def __init__(self, libs: List[Dict[str, Any]], roots_by_kind: Dict[str, List[str]],
                 localize: Callable[[str], str] = lambda p: p):
        self._default = {k: default_id(libs, k) for k in TITLE_KINDS}
        self._localize = localize
        assigned = {(lib["kind"], f): lib["id"] for lib in libs for f in lib["folders"]}
        # deepest folder first: a folder inside another is its own place
        self._roots = {
            k: sorted(((norm(r), assigned.get((k, norm(r)), self._default[k])) for r in roots_by_kind.get(k, [])),
                      key=lambda x: -len(x[0]))
            for k in TITLE_KINDS}

    def folder_of(self, kind: str, path: Any) -> Optional[str]:
        p = norm(self._localize(str(path))) if path else ""
        if not p:
            return None
        for root, _lib in self._roots.get(kind, ()):
            if root and (p == root or p.startswith(root + "/")):
                return root
        return None

    def library_of(self, kind: str, path: Any) -> int:
        folder = self.folder_of(kind, path)
        if folder is not None:
            for root, lib in self._roots[kind]:
                if root == folder:
                    return lib
        return self._default.get(kind, DEFAULTS["movie"][0])


def resolver(player_db: Any, video_db: Any) -> Resolver:
    """the current resolver, rebuilt when the libraries change (and every
    minute, for a folder added in the manager)"""
    key = f"{_key(player_db)}|{getattr(video_db, 'database_path', id(video_db))}"
    now = time.monotonic()
    with _lock:
        hit = _resolver_cache.get(key)
        if hit and hit[1] == _version and now - hit[0] < RESOLVER_SECONDS:
            return hit[2]
    built = Resolver(libraries(player_db), {k: roots(video_db, k) for k in TITLE_KINDS}, _localizer(video_db))
    with _lock:
        _resolver_cache[key] = (now, _version, built)
    return built


def _localizer(video_db: Any) -> Callable[[str], str]:
    """a media server's path for a file -> the path of that file from here
    (Settings > Folders > Video > Your library's folders), as the manager's
    own attribution does it"""
    try:
        from core.video import drive_map
        entries = drive_map.load(video_db)
    except Exception:  # noqa: BLE001
        entries = []
    if not entries:
        return lambda p: p

    def localize(path: str) -> str:
        try:
            return drive_map.map_path(entries, path) or path
        except Exception:  # noqa: BLE001 - the map is a shortcut, never a failure
            return path
    return localize


def title_paths(conn: Any, kind: str, ids: Optional[Iterable[Any]] = None) -> Dict[int, str]:
    """{title id: the path of one of its files}; ``ids`` narrows it"""
    wanted = None if ids is None else [int(i) for i in ids if i is not None]
    if wanted is not None and not wanted:
        return {}
    out: Dict[int, str] = {}
    if kind == "movie":
        base = "SELECT movie_id, MAX(relative_path) FROM media_files WHERE movie_id IS NOT NULL"
        col = "movie_id"
        tail = " GROUP BY movie_id"
    else:
        base = ("SELECT e.show_id, MAX(f.relative_path) FROM episodes e "
                "JOIN media_files f ON f.episode_id = e.id WHERE 1=1")
        col = "e.show_id"
        tail = " GROUP BY e.show_id"
    if wanted is None:
        for tid, path in conn.execute(base + tail):
            if path:
                out[int(tid)] = path
        return out
    for i in range(0, len(wanted), 500):
        chunk = wanted[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for tid, path in conn.execute(f"{base} AND {col} IN ({marks}){tail}", chunk):
            if path:
                out[int(tid)] = path
    return out


def library_ids_for(video_db: Any, player_db: Any, kind: str, ids: Iterable[Any]) -> Dict[int, int]:
    """{title id: the library it is in} for these movies or shows"""
    k = "movie" if str(kind).lower() in ("movie", "movies") else "show"
    wanted = [int(i) for i in ids if i is not None]
    if not wanted:
        return {}
    res = resolver(player_db, video_db)
    conn = video_db._get_connection()
    try:
        paths = title_paths(conn, k, wanted)
    finally:
        conn.close()
    return {tid: res.library_of(k, paths.get(tid)) for tid in wanted}


def folder_overview(video_db: Any, player_db: Any) -> Dict[str, List[Dict[str, Any]]]:
    """for player setup: every folder of each kind with the library it is in,
    how many titles sit in it and a few of them to recognise it by; plus,
    under "", the titles that are in none of the folders."""
    res = resolver(player_db, video_db)
    out: Dict[str, List[Dict[str, Any]]] = {}
    conn = video_db._get_connection()
    try:
        for kind in TITLE_KINDS:
            counts: Dict[Optional[str], int] = {}
            samples: Dict[Optional[str], List[int]] = {}
            for tid, path in title_paths(conn, kind).items():
                folder = res.folder_of(kind, path)
                counts[folder] = counts.get(folder, 0) + 1
                bucket = samples.setdefault(folder, [])
                if len(bucket) < 6:
                    bucket.append(tid)
            rows = []
            for root in roots(video_db, kind):
                key = norm(root)
                rows.append({"folder": key, "path": root, "titles": counts.get(key, 0),
                             "sample": samples.get(key, []), "library_id": res.library_of(kind, root)})
            if counts.get(None):
                rows.append({"folder": "", "path": "", "titles": counts[None], "sample": samples.get(None, []),
                             "library_id": res.library_of(kind, None)})
            out[kind] = rows
        out["home"] = _home_overview(conn, video_db, libraries(player_db))
    finally:
        conn.close()
    return out


def _home_overview(conn: Any, video_db: Any, libs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """setup's rows for the home video roots: {folder, path, label, source,
    titles, sample (clip ids with a picture), library_id}"""
    try:
        from core.video import home_videos
        found = home_videos.roots(video_db)
        counts = home_videos.root_counts(conn, [r["id"] for r in found])
        places = home_videos.root_places(conn, [r["id"] for r in found])
    except Exception:  # noqa: BLE001 - no home table yet is no home videos
        logger.debug("home overview unreadable", exc_info=True)
        return []
    return [{"folder": r["id"], "path": r["path"], "label": r["label"], "source": r["source"],
             "titles": counts.get(r["id"], (0, []))[0], "sample": counts.get(r["id"], (0, []))[1],
             "library_id": home_library_of(libs, r["id"]),
             "where": places.get(r["id"]) or (r["path"] if r["source"] == "disk" else None)} for r in found]


# ── home ──────────────────────────────────────────────────────────────────

def home_library_of(libs: List[Dict[str, Any]], root: str) -> Optional[int]:
    """the home library a root is in: the one it was moved to, else the default"""
    key = norm(root)
    for lib in libs:
        if lib["kind"] == "home" and key in lib["folders"]:
            return lib["id"]
    return default_id(libs, "home")


def home_library_roots(libs: List[Dict[str, Any]], all_roots: Iterable[str], library_id: int) -> List[str]:
    """the roots one home library holds"""
    return [r for r in all_roots if home_library_of(libs, r) == int(library_id)]


def ensure_home(db: Any, video_db: Any) -> Optional[int]:
    """the "Home videos" library, made when there are home video roots and
    there is none yet. its id, or None with no roots."""
    libs = libraries(db)
    have = default_id(libs, "home")
    if have is not None:
        return have
    if not roots(video_db, "home"):
        return None
    ensure_schema(db)
    with db._get_connection() as conn:
        row = conn.execute(f"SELECT id FROM {LIBS} WHERE kind = 'home' AND is_default = 1").fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(f"INSERT INTO {LIBS} (kind, name, position, is_default, in_guide) "
                           "VALUES ('home', ?, 0, 1, 0)", (HOME_NAME,))
        conn.commit()
        made = int(cur.lastrowid)
    _changed(db)
    logger.info("player: made the %s library (%d)", HOME_NAME, made)
    return made


def guide_off(db: Any) -> FrozenSet[int]:
    """the movie and TV libraries the guide leaves out (a home library is
    never in the guide's catalog to begin with)"""
    return frozenset(lib["id"] for lib in libraries(db) if not lib["in_guide"] and lib["kind"] in TITLE_KINDS)


# ── saving ────────────────────────────────────────────────────────────────

_KIND_WORDS = {"movie": "movie", "show": "TV", "home": "home video"}


def _name(value: Any) -> str:
    name = " ".join(str(value or "").split())
    if not name or len(name) > MAX_NAME:
        raise LibraryError(f"a library's name is 1 to {MAX_NAME} characters")
    return name


def save(db: Any, video_db: Any, wanted: Any, hidden: Any = None) -> List[Dict[str, Any]]:
    """the whole set of libraries, replaced: ``[{id?, kind, name, folders}]``
    (a library with no id is new), and optionally who each is hidden from,
    ``{library id or its place in the list as "new:<n>": [profile id]}``.

    all or nothing. each kind keeps its default library (it can be renamed,
    not removed); a folder belongs to one library and must be one of the
    manager's folders for that kind; a folder nobody names goes to the
    default; a library left out is removed and what was hidden with it.

    home libraries are left as they are when the list has none at all (an
    app that knows only movies and shows saves without touching them)."""
    if not isinstance(wanted, list) or len(wanted) > MAX_LIBRARIES:
        raise LibraryError(f"libraries is a list of at most {MAX_LIBRARIES}")
    ensure_schema(db)
    current = {lib["id"]: lib for lib in libraries(db)}
    wanted_kinds = {str(i.get("kind") or "") for i in wanted if isinstance(i, dict)}
    known_folders = {k: ({norm(r) for r in roots(video_db, k)} if k in wanted_kinds else set()) for k in KINDS}
    seen_folders: Dict[Tuple[str, str], str] = {}
    seen_names: Dict[Tuple[str, str], bool] = {}
    kept_defaults = set()
    plan = []
    for n, item in enumerate(wanted):
        if not isinstance(item, dict):
            raise LibraryError("each library is an object")
        kind = str(item.get("kind") or "")
        if kind not in KINDS:
            raise LibraryError("a library is of movies, shows or home videos")
        name = _name(item.get("name"))
        if (kind, name.lower()) in seen_names:
            raise LibraryError(f"two libraries are called {name}")
        seen_names[(kind, name.lower())] = True
        lib_id = item.get("id")
        existing = None
        if lib_id is not None:
            try:
                existing = current.get(int(lib_id))
            except (TypeError, ValueError):
                existing = None
            if existing is None or existing["kind"] != kind:
                raise LibraryError("no such library")
            if existing["is_default"]:
                kept_defaults.add(kind)
        in_guide = item.get("in_guide")
        if in_guide is not None and not isinstance(in_guide, bool):
            raise LibraryError("in_guide is true or false")
        folders = item.get("folders") or []
        if not isinstance(folders, list):
            raise LibraryError("folders is a list")
        clean = []
        for f in folders:
            key = norm(f)
            if key not in known_folders[kind]:
                raise LibraryError(f"{f} is not one of the {_KIND_WORDS[kind]} folders")
            if (kind, key) in seen_folders:
                raise LibraryError(f"{f} is in two libraries")
            seen_folders[(kind, key)] = name
            clean.append(key)
        plan.append({"id": existing["id"] if existing else None, "kind": kind, "name": name,
                     "folders": clean, "is_default": bool(existing and existing["is_default"]), "place": n,
                     # not said: as it was (a new one: its kind's default)
                     "in_guide": in_guide if in_guide is not None else (
                         existing["in_guide"] if existing else kind != "home")})
    # home libraries the list doesn't mention: kept whole
    untouched = [lib for lib in current.values()
                 if lib["kind"] == "home" and "home" not in wanted_kinds]
    for kind in KINDS:
        if kind == "home" and untouched:
            continue
        main = next((lib for lib in current.values() if lib["kind"] == kind and lib["is_default"]), None)
        if kind not in kept_defaults and (kind in DEFAULTS or main is not None):
            raise LibraryError(f"the {main['name'] if main else DEFAULTS[kind][1]} library can be renamed, not removed")
        if kind == "home" and main is None and any(p["kind"] == "home" for p in plan):
            raise LibraryError("home video libraries come once there are home videos")

    hide: Dict[Any, List[int]] = {}
    if hidden is not None:
        if not isinstance(hidden, dict):
            raise LibraryError("hidden is {library: [profile id]}")
        for k, pids in hidden.items():
            try:
                hide[str(k)] = sorted({int(p) for p in (pids or [])})
            except (TypeError, ValueError) as exc:
                raise LibraryError("hidden is {library: [profile id]}") from exc

    with db._get_connection() as conn:
        keep_ids = []
        new_ids: Dict[int, int] = {}
        for position, lib in enumerate(plan):
            if lib["id"] is None:
                cur = conn.execute(
                    f"INSERT INTO {LIBS} (kind, name, position, is_default, in_guide) VALUES (?, ?, ?, 0, ?)",
                    (lib["kind"], lib["name"], position, int(lib["in_guide"])))
                lib["id"] = int(cur.lastrowid)
                new_ids[lib["place"]] = lib["id"]
            else:
                conn.execute(f"UPDATE {LIBS} SET name = ?, position = ?, in_guide = ? WHERE id = ?",
                             (lib["name"], position, int(lib["in_guide"]), lib["id"]))
            keep_ids.append(lib["id"])
        keep_ids.extend(lib["id"] for lib in untouched)
        marks = ",".join("?" * len(keep_ids))
        conn.execute(f"DELETE FROM {LIBS} WHERE id NOT IN ({marks})", keep_ids)
        conn.execute(f"DELETE FROM {HIDDEN} WHERE library_id NOT IN ({marks})", keep_ids)
        conn.execute(f"DELETE FROM {FOLDERS}")
        conn.executemany(
            f"INSERT INTO {FOLDERS} (kind, folder, library_id) VALUES (?, ?, ?)",
            [(lib["kind"], f, lib["id"]) for lib in plan + untouched if not lib["is_default"] for f in lib["folders"]])
        if hidden is not None:
            kept_hidden = hidden_map(db)
            conn.execute(f"DELETE FROM {HIDDEN}")
            rows = [(lib["id"], p) for lib in untouched if str(lib["id"]) not in hide
                    for p in kept_hidden.get(lib["id"], [])]
            for k, pids in hide.items():
                lib_id = new_ids.get(int(k[4:])) if k.startswith("new:") and k[4:].isdigit() else (
                    int(k) if k.lstrip("-").isdigit() else None)
                if lib_id in keep_ids:
                    rows.extend((lib_id, p) for p in pids)
            conn.executemany(f"INSERT OR IGNORE INTO {HIDDEN} (library_id, profile_id) VALUES (?, ?)", rows)
        conn.commit()
    _changed(db)
    return libraries(db)


__all__ = ["LibraryError", "KINDS", "TITLE_KINDS", "DEFAULTS", "libraries", "visible", "hidden_for",
           "hidden_map", "roots", "Resolver", "resolver", "title_paths", "library_ids_for", "folder_overview",
           "save", "norm", "version", "ensure_schema", "default_id", "ensure_home", "home_library_of",
           "home_library_roots", "guide_off"]
