"""Video libraries (#2029): named movie and TV libraries, each with its own
folder, the way Radarr / Sonarr users run a Movies, a Movies 4K and a
Stand-Up library side by side.

The main and 4K folders that existed before are libraries in this model
(``builtin`` 'main' / '4k', seeded by VideoDatabase on every start). Their
folders still live in the settings keys they always used (movies_path, tv_path,
movies_4k_path + movies_4k_enabled, ...), so every reader of those keys and
Settings > Folders keep working unchanged. A custom library keeps its own
folder in its row.

Which library a title files into (``choose``), first match wins:

  1. a library picked for this grab / wishlist row
  2. a library picked for the title by hand (video_title_libraries)
  3. the 4K library when the title's quality profile says save to 4K (the
     pre-#2029 routing, kept exactly)
  4. the first library whose rules match the title (genres, keywords)
  5. the kind's default library
  6. the main library

A library that is switched off, or has no folder, is skipped at every step,
so turning one off is a clean undo. The anime folder is not a library: an
anime show keeps going there unless a library was picked for it by hand.

Imports nothing from the music side.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

KINDS = ("movie", "show")
MAX_NAME = 80
MAX_RULE_TERMS = 30

# the settings keys a builtin library's folder lives in
_MAIN_KEYS = {"movie": "movies_path", "show": "tv_path"}
_FOUR_K_KEYS = {"movie": ("movies_4k_enabled", "movies_4k_path"),
                "show": ("tv_4k_enabled", "tv_4k_path")}


def kind_of(value: Any) -> Optional[str]:
    """'movie' or 'show' for anything naming one (episode/season/tv are a
    show's), else None."""
    k = str(value or "").strip().lower()
    if k == "movie":
        return "movie"
    if k in ("show", "tv", "episode", "season", "series"):
        return "show"
    return None


def _on(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _terms(value: Any) -> List[str]:
    """a clean, de-duplicated list of rule terms (a list, or one comma string)"""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return []
    out, seen = [], set()
    for v in value:
        t = " ".join(str(v or "").split())[:60]
        if t and t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    return out[:MAX_RULE_TERMS]


def normalize_options(raw: Any) -> Dict[str, Any]:
    """A library's config, cleaned. Unknown keys dropped. Never raises."""
    raw = raw if isinstance(raw, dict) else {}
    rules = raw.get("rules") if isinstance(raw.get("rules"), dict) else {}
    return {"rules": {"genres": _terms(rules.get("genres")),
                      "keywords": _terms(rules.get("keywords"))}}


def _load_options(text: Any) -> Dict[str, Any]:
    try:
        return normalize_options(json.loads(text or "{}"))
    except (TypeError, ValueError):
        return normalize_options({})


def _same(a: Any, b: Any) -> bool:
    na = os.path.normcase(os.path.normpath(str(a or "").strip())) if str(a or "").strip() else ""
    nb = os.path.normcase(os.path.normpath(str(b or "").strip())) if str(b or "").strip() else ""
    return bool(na) and na == nb


def _builtin_state(db, kind: str, builtin: str) -> tuple:
    """(folder as set, switched on) for a builtin library. The main movie
    library falls back to the legacy single transfer folder, as routing
    always did."""
    if builtin == "main":
        path = str(db.get_setting(_MAIN_KEYS[kind]) or "").strip()
        if not path and kind == "movie":
            path = str(db.get_setting("transfer_path") or "").strip()
        return path, True
    switch, folder = _FOUR_K_KEYS[kind]
    return str(db.get_setting(folder) or "").strip(), _on(db.get_setting(switch))


def _row_to_lib(db, row) -> Dict[str, Any]:
    kind = row["kind"]
    builtin = row["builtin"]
    if builtin:
        path, enabled = _builtin_state(db, kind, builtin)
    else:
        path, enabled = str(row["path"] or "").strip(), bool(row["enabled"])
    options = _load_options(row["options"])
    return {"id": int(row["id"]), "kind": kind, "name": row["name"], "path": path,
            "builtin": builtin, "enabled": enabled, "usable": bool(enabled and path),
            "is_default": bool(row["is_default"]), "position": int(row["position"] or 0),
            "options": options, "rules": options["rules"]}


def _rows(db, where: str = "", args: tuple = ()) -> list:
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT id, kind, name, path, builtin, enabled, is_default, position, options "
            "FROM video_libraries " + where +
            " ORDER BY kind, CASE builtin WHEN 'main' THEN 0 WHEN '4k' THEN 1 ELSE 2 END, "
            "position, id", args).fetchall()
    finally:
        conn.close()


def list_libraries(db, kind: Any = None) -> List[Dict[str, Any]]:
    """Every library (of one kind), main first, then 4K, then custom ones in
    their order."""
    k = kind_of(kind) if kind is not None else None
    if kind is not None and not k:
        return []
    rows = _rows(db, "WHERE kind=?", (k,)) if k else _rows(db)
    return [_row_to_lib(db, r) for r in rows]


def get_library(db, library_id: Any) -> Optional[Dict[str, Any]]:
    try:
        lid = int(library_id)
    except (TypeError, ValueError):
        return None
    rows = _rows(db, "WHERE id=?", (lid,))
    return _row_to_lib(db, rows[0]) if rows else None


def _check_name(name: Any) -> str:
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("Give the library a name")
    if len(name) > MAX_NAME:
        raise ValueError("Keep the name under %d characters" % MAX_NAME)
    return name


def _check_path(db, path: Any, *, skip_id: Optional[int] = None) -> str:
    path = str(path or "").strip()
    if not path:
        raise ValueError("Pick the library's folder")
    if "\x00" in path:
        raise ValueError("Library folders cannot contain null characters")
    for lib in list_libraries(db):
        if lib["id"] == skip_id:
            continue
        if _same(lib["path"], path):
            raise ValueError("%s already uses that folder" % lib["name"])
    for key, label in (("anime_path", "The anime folder"), ("youtube_path", "The YouTube folder")):
        if _same(db.get_setting(key), path):
            raise ValueError("%s already uses that folder" % label)
    return path


def create_library(db, body: Any) -> Dict[str, Any]:
    """A new custom library. Raises ValueError with a message for the user."""
    body = body if isinstance(body, dict) else {}
    kind = kind_of(body.get("kind"))
    if not kind:
        raise ValueError("A library holds movies or TV")
    name = _check_name(body.get("name"))
    path = _check_path(db, body.get("path"))
    options = normalize_options(body.get("options") or {"rules": body.get("rules")})
    conn = db.connect()
    try:
        pos = conn.execute("SELECT COALESCE(MAX(position), 1) + 1 FROM video_libraries WHERE kind=?",
                           (kind,)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO video_libraries (kind, name, path, enabled, position, options) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, name, path, 0 if body.get("enabled") is False else 1, int(pos),
             json.dumps(options)))
        conn.commit()
        new_id = cur.lastrowid
    finally:
        conn.close()
    if body.get("is_default"):
        set_default(db, new_id)
    return get_library(db, new_id)


def update_library(db, library_id: Any, body: Any) -> Dict[str, Any]:
    """Rename, move, switch, re-rule a library. A builtin's folder and switch
    stay in Settings > Folders (they are the same settings as always), so a
    builtin only takes a name and options here."""
    lib = get_library(db, library_id)
    if not lib:
        raise LookupError("No such library")
    body = body if isinstance(body, dict) else {}
    sets, args = [], []
    if "name" in body:
        sets.append("name=?")
        args.append(_check_name(body.get("name")))
    if not lib["builtin"]:
        if "path" in body:
            sets.append("path=?")
            args.append(_check_path(db, body.get("path"), skip_id=lib["id"]))
        if "enabled" in body:
            sets.append("enabled=?")
            args.append(1 if body.get("enabled") else 0)
    elif "path" in body and not _same(body.get("path"), lib["path"]):
        raise ValueError("Change this library's folder above, in its own setting")
    if "options" in body or "rules" in body:
        merged = dict(lib["options"])
        merged.update(body.get("options") if isinstance(body.get("options"), dict) else {})
        if "rules" in body:
            merged["rules"] = body.get("rules")
        sets.append("options=?")
        args.append(json.dumps(normalize_options(merged)))
    if sets:
        conn = db.connect()
        try:
            conn.execute("UPDATE video_libraries SET " + ", ".join(sets) + " WHERE id=?",
                         (*args, lib["id"]))
            conn.commit()
        finally:
            conn.close()
    if body.get("is_default"):
        set_default(db, lib["id"])
    out = get_library(db, lib["id"])
    if out and out["is_default"] and not out["usable"]:
        # a default switched off hands the kind back to the main library
        _reset_default(db, out["kind"])
        out = get_library(db, lib["id"])
    return out


def _reset_default(db, kind: str) -> None:
    conn = db.connect()
    try:
        conn.execute("UPDATE video_libraries SET is_default = (builtin IS 'main') WHERE kind=?",
                     (kind,))
        conn.commit()
    finally:
        conn.close()


def delete_library(db, library_id: Any) -> bool:
    """Remove a custom library (the builtin ones can't go: switch the 4K one
    off instead). Its files stay on disk; titles picked for it go back to the
    rules and the default; a default removed hands the kind to the main one."""
    lib = get_library(db, library_id)
    if not lib:
        return False
    if lib["builtin"]:
        raise ValueError("The %s library can't be removed" % lib["name"])
    conn = db.connect()
    try:
        conn.execute("DELETE FROM video_title_libraries WHERE library_id=?", (lib["id"],))
        conn.execute("DELETE FROM video_libraries WHERE id=?", (lib["id"],))
        conn.commit()
    finally:
        conn.close()
    if lib["is_default"]:
        _reset_default(db, lib["kind"])
    return True


def set_default(db, library_id: Any) -> Dict[str, Any]:
    """Make a library the default for its kind (new titles that no rule or
    pick sends elsewhere)."""
    lib = get_library(db, library_id)
    if not lib:
        raise LookupError("No such library")
    if not lib["usable"]:
        raise ValueError("%s needs a folder and to be switched on first" % lib["name"])
    conn = db.connect()
    try:
        conn.execute("UPDATE video_libraries SET is_default = (id = ?) WHERE kind=?",
                     (lib["id"], lib["kind"]))
        conn.commit()
    finally:
        conn.close()
    return get_library(db, lib["id"])


# ── a title's own pick ────────────────────────────────────────────────────────

def title_assignment(db, kind: Any, tmdb_id: Any) -> Optional[int]:
    k = kind_of(kind)
    try:
        tmdb = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    if not k:
        return None
    conn = db.connect()
    try:
        row = conn.execute("SELECT library_id FROM video_title_libraries WHERE kind=? AND tmdb_id=?",
                           (k, tmdb)).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def assign_title(db, kind: Any, tmdb_id: Any, library_id: Any) -> Optional[int]:
    """Pick a library for a title by hand; None / 0 clears the pick (back to
    the rules and the default). Returns the picked id."""
    k = kind_of(kind)
    if not k:
        raise ValueError("kind must be movie or show")
    try:
        tmdb = int(tmdb_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("The title has no TMDB id to keep the pick on") from exc
    lid = None
    if library_id not in (None, "", 0, "0"):
        lib = get_library(db, library_id)
        if not lib or lib["kind"] != k:
            raise LookupError("No such %s library" % ("movie" if k == "movie" else "TV"))
        lid = lib["id"]
    conn = db.connect()
    try:
        if lid is None:
            conn.execute("DELETE FROM video_title_libraries WHERE kind=? AND tmdb_id=?", (k, tmdb))
        else:
            conn.execute(
                "INSERT INTO video_title_libraries (kind, tmdb_id, library_id) VALUES (?, ?, ?) "
                "ON CONFLICT(kind, tmdb_id) DO UPDATE SET library_id=excluded.library_id, "
                "updated_at=CURRENT_TIMESTAMP", (k, tmdb, lid))
        conn.commit()
    finally:
        conn.close()
    return lid


# ── rules ─────────────────────────────────────────────────────────────────────

def has_rules(lib: Dict[str, Any]) -> bool:
    r = (lib or {}).get("rules") or {}
    return bool(r.get("genres") or r.get("keywords"))


def rule_match(rules: Dict[str, Any], tags: Optional[Dict[str, Any]], title: Any = None) -> Optional[str]:
    """What made a title match a library's rules ('genre Documentary',
    'keyword stand-up'), or None. A genre matches a genre exactly (any case);
    a keyword matches inside one of the title's TMDB keywords, or its name."""
    rules = rules or {}
    tags = tags or {}
    genres = {str(g).casefold() for g in tags.get("genres") or []}
    for g in rules.get("genres") or []:
        if g.casefold() in genres:
            return "genre " + g
    words = [str(k).casefold() for k in tags.get("keywords") or []]
    name = str(title or "").casefold()
    for kw in rules.get("keywords") or []:
        needle = kw.casefold()
        if any(needle in w for w in words) or (name and needle in name):
            return "keyword " + kw
    return None


def title_tags(db, kind: Any, tmdb_id: Any) -> Dict[str, List[str]]:
    """A title's genres and TMDB keywords for the rules: the library's own
    genres when it's owned, plus TMDB's (cached a day by the enrichment
    engine, the same call that names the title for searches). Best-effort,
    never raises."""
    k = kind_of(kind)
    out: Dict[str, List[str]] = {"genres": [], "keywords": []}
    try:
        tmdb = int(tmdb_id)
    except (TypeError, ValueError):
        return out
    try:
        conn = db.connect()
        try:
            if k == "movie":
                sql = ("SELECT DISTINCT g.name FROM movies m JOIN movie_genres mg ON mg.movie_id=m.id "
                       "JOIN genres g ON g.id=mg.genre_id WHERE m.tmdb_id=?")
            else:
                sql = ("SELECT DISTINCT g.name FROM shows s JOIN show_genres sg ON sg.show_id=s.id "
                       "JOIN genres g ON g.id=sg.genre_id WHERE s.tmdb_id=?")
            out["genres"] = [r[0] for r in conn.execute(sql, (tmdb,)).fetchall() if r[0]]
        finally:
            conn.close()
    except Exception:   # noqa: BLE001 - rules read what they can
        pass
    try:
        from core.video.enrichment.engine import get_video_enrichment_engine
        ident = get_video_enrichment_engine().title_identity(k, tmdb) or {}
        for g in ident.get("genres") or []:
            if g not in out["genres"]:
                out["genres"].append(g)
        out["keywords"] = list(ident.get("keywords") or [])
    except Exception:   # noqa: BLE001 - no TMDB: the library's own genres only
        pass
    return out


# ── the decision ──────────────────────────────────────────────────────────────

def _resolve(value: Any) -> Any:
    return value() if callable(value) else value


def choose(db, kind: Any, *, tmdb_id: Any = None, explicit_id: Any = None,
           profile_library: Any = None, title: Any = None,
           tags: Any = None) -> Optional[Dict[str, Any]]:
    """The library a title of this kind files into, with ``why`` set to
    'picked' | 'assigned' | 'profile' | 'rule: ...' | 'default'. See the
    module doc for the order. ``profile_library`` ('main' / '4k', or a
    callable giving it) is only asked while the 4K library is usable;
    ``tags`` (a dict or a callable; defaults to :func:`title_tags`) only while
    a usable library has rules. None when the kind is unknown."""
    k = kind_of(kind)
    if not k:
        return None
    libs = list_libraries(db, k)
    usable = [lib for lib in libs if lib["usable"]]
    by_id = {lib["id"]: lib for lib in usable}

    def pick(lib, why):
        return dict(lib, why=why)

    try:
        if explicit_id not in (None, "", 0, "0") and int(explicit_id) in by_id:
            return pick(by_id[int(explicit_id)], "picked")
    except (TypeError, ValueError):
        pass
    if tmdb_id not in (None, ""):
        assigned = title_assignment(db, k, tmdb_id)
        if assigned in by_id:
            return pick(by_id[assigned], "assigned")
    four_k = next((lib for lib in usable if lib["builtin"] == "4k"), None)
    if four_k and str(_resolve(profile_library) or "").strip().lower() == "4k":
        return pick(four_k, "profile")
    ruled = [lib for lib in usable if has_rules(lib)]
    if ruled:
        t = tags if tags is not None else (lambda: title_tags(db, k, tmdb_id))
        got = _resolve(t) or {}
        for lib in ruled:
            hit = rule_match(lib["rules"], got, title)
            if hit:
                return pick(lib, "rule: " + hit)
    default = next((lib for lib in usable if lib["is_default"]), None)
    if default:
        return pick(default, "default")
    main = next((lib for lib in libs if lib["builtin"] == "main"), None)
    return pick(main, "default") if main else None


def custom_folders(db, kind: Any = None) -> List[str]:
    """The folders of the switched-on custom libraries (of one kind), for the
    places that list every library folder (scans, health, the monitor's
    protected roots). Best-effort: [] on any error."""
    try:
        return [lib["path"] for lib in list_libraries(db, kind)
                if not lib["builtin"] and lib["usable"]]
    except Exception:   # noqa: BLE001 - a bad row never hides the configured folders
        return []


def library_for_path(db, path: Any, kind: Any = None,
                     libs: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """The library whose folder holds ``path`` (the deepest one), or None."""
    p = str(path or "").strip()
    if not p:
        return None
    libs = libs if libs is not None else list_libraries(db, kind)
    best, best_len = None, -1
    absolute = os.path.normcase(os.path.abspath(p))
    for lib in libs:
        if not lib.get("path") or not lib.get("enabled"):
            continue
        root = os.path.normcase(os.path.abspath(lib["path"]))
        try:
            if os.path.commonpath([absolute, root]) == root and len(root) > best_len:
                best, best_len = lib, len(root)
        except ValueError:
            continue
    return best


def folder_for(lib: Optional[Dict[str, Any]], fallback: str = "") -> str:
    """The folder a chosen library files into ('' falls back)."""
    return str((lib or {}).get("path") or "") or fallback


__all__ = ["KINDS", "kind_of", "normalize_options", "list_libraries", "get_library",
           "create_library", "update_library", "delete_library", "set_default",
           "title_assignment", "assign_title", "has_rules", "rule_match", "title_tags",
           "choose", "custom_folders", "library_for_path", "folder_for"]

