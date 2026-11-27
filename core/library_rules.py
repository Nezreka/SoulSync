"""secondary library rules (#2332).

some music belongs in its own folder: holiday music that listener accounts
only see in december, kids' music, a partner's collection. the media server
gets that folder as a second library, and SoulSync files matching albums
there instead of the main library.

- one folder and its match rules live in settings, library section (off by
  default): ``secondary_enabled``, ``secondary_path``, ``secondary_name``,
  ``secondary_match_holiday`` (core/holiday_music.py's album guess and the
  album and artist holiday flags), ``secondary_genres`` (genre contains any
  of these) and ``secondary_album_titles`` (album title matches any of
  these, whole words). terms are split on | or commas. any rule matching
  is enough.
- the decision is made for the ALBUM, never the track: only what is true of
  every track on it counts (the album title, the album's genres, the album
  artist's main genre, an album or artist flag). an album that is already
  in the library stays where its files are, so a late track never splits it.
- downloads: ``secondary_root_for_context`` is asked by
  core.imports.paths.transfer_root_for_context, the one place a download's
  library folder is decided. the naming template is unchanged; only the root
  differs. own-library profiles, lidarr and audiobook routes keep their own
  folders.
- albums already owned: ``plan_move`` / ``move_owned`` preview and move the
  matching albums from the main folder to the secondary one, at the same
  relative path, the same way an audiobook route moves an artist's albums.
- visibility: ``visibility_check`` compares the files in the folder with what
  the media server returned on its last scan.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("library_rules")

PREFIX = "library.secondary_"
DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "path": "",
    "name": "Holiday",
    "match_holiday": True,
    "genres": "",
    "album_titles": "",
}


def _terms(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        raw = [str(v) for v in value]
    else:
        raw = re.split(r"[|,;\n]", str(value or ""))
    out: List[str] = []
    for term in raw:
        term = re.sub(r"\s+", " ", term).strip().casefold()
        if term and term not in out:
            out.append(term)
    return out


@dataclass
class Rules:
    enabled: bool = False
    path: str = ""
    name: str = "Holiday"
    match_holiday: bool = True
    genres: List[str] = field(default_factory=list)
    album_titles: List[str] = field(default_factory=list)

    @property
    def has_rule(self) -> bool:
        return self.match_holiday or bool(self.genres) or bool(self.album_titles)

    @property
    def problem(self) -> str:
        """why the rules can't work as saved, or ''"""
        if not self.path:
            return "no folder set"
        if not self.has_rule:
            return "no rule set"
        try:
            from core.imports.paths import shared_transfer_root
            main = os.path.normpath(shared_transfer_root())
        except Exception:  # noqa: BLE001
            main = ""
        # a folder inside the main library (or around it) would show up in the
        # main media server library too, and its albums could never be told apart
        if main and (is_under(self.path, main) or is_under(main, self.path)):
            return "the folder is inside the main library folder (or holds it)"
        return ""

    @property
    def active(self) -> bool:
        """on, with a folder of its own outside the main library, and a rule"""
        return bool(self.enabled and not self.problem)

    def to_json(self) -> Dict[str, Any]:
        return {"enabled": self.enabled, "path": self.path, "name": self.name,
                "match_holiday": self.match_holiday, "genres": list(self.genres),
                "album_titles": list(self.album_titles), "active": self.active,
                "problem": self.problem if self.enabled else ""}


def rules(config: Any = None) -> Rules:
    """the saved rules, every key with its default"""
    if config is None:
        try:
            from core.settings import config_manager as config
        except Exception:  # noqa: BLE001
            config = None
    get = getattr(config, "get", None)

    def read(key: str) -> Any:
        default = DEFAULTS[key]
        if get is None:
            return default
        try:
            value = get(PREFIX + key, default)
        except Exception:  # noqa: BLE001
            return default
        return default if value is None else value

    raw_path = str(read("path") or "").strip()
    path = ""
    if raw_path:
        try:
            from core.imports.paths import config_root_path
            path = config_root_path(raw_path)
        except Exception:  # noqa: BLE001
            path = raw_path
    return Rules(
        enabled=read("enabled") is True,
        path=path,
        name=str(read("name") or DEFAULTS["name"]).strip() or DEFAULTS["name"],
        match_holiday=read("match_holiday") is not False,
        genres=_terms(read("genres")),
        album_titles=_terms(read("album_titles")),
    )


def active_rules() -> Optional[Rules]:
    current = rules()
    return current if current.active else None


def secondary_root() -> Optional[str]:
    """the secondary folder when the rules are on, else None"""
    current = active_rules()
    return current.path if current else None


def is_under(path: Any, root: Any) -> bool:
    if not path or not root:
        return False
    p, r = os.path.normpath(str(path)), os.path.normpath(str(root))
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


# ── the album decision (pure) ───────────────────────────────────────────────

def _word_match(term: str, text: str) -> bool:
    return bool(re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, re.I))


def album_matches(current: Rules, *, artist: Any = "", album: Any = "",
                  album_genres: Iterable[Any] = (), artist_genres: Iterable[Any] = (),
                  overrides: Optional[Dict[Tuple[str, str], bool]] = None) -> Tuple[bool, str]:
    """(goes to the secondary folder, why) for one album. why is 'holiday',
    'genre', 'album title' or ''"""
    from core import holiday_music as H
    album_text = str(album or "")
    genres = H._genre_list(list(album_genres or []))
    # the artist's main genre only: what the artist usually makes is not what
    # this album is
    main = H._genre_list(list(artist_genres or []))[:1]
    if current.match_holiday:
        flagged = None
        if overrides:
            for scope in ("album", "artist"):
                key = H.match_key(scope, artist, album_text)
                if key.strip("|") and (scope, key) in overrides:
                    flagged = overrides[(scope, key)]
                    break
        if flagged is True:
            return True, "holiday"
        if flagged is None and H.album_says_holiday(album_text, genres, artist=artist,
                                                    artist_genres=main):
            return True, "holiday"
    if current.genres:
        for g in [*genres, *main]:
            text = str(g or "").casefold()
            if any(term in text for term in current.genres):
                return True, "genre"
    if current.album_titles and album_text:
        if any(_word_match(term, album_text) for term in current.album_titles):
            return True, "album title"
    return False, ""


# ── downloads ───────────────────────────────────────────────────────────────

# a batch's tracks are decided once per album: the first track's answer is the
# album's, even if a later track's context carries other genres
_decided: Dict[Tuple[str, str, str], Tuple[float, bool]] = {}
_decided_lock = threading.Lock()
_DECIDED_SECONDS = 6 * 3600.0


def forget_decisions() -> None:
    with _decided_lock:
        _decided.clear()


def _album_of_context(context: Dict[str, Any]) -> Tuple[str, str, List[Any], List[Any]]:
    """(album artist, album title, album genres, artist genres) of an import"""
    from core.imports.context import (
        extract_artist_name,
        get_import_clean_album,
        get_import_context_album,
        get_import_context_artist,
        get_import_track_info,
        has_real_album_name,
    )
    album_ctx = get_import_context_album(context)
    artist_ctx = get_import_context_artist(context)
    track_info = get_import_track_info(context)
    album = str(album_ctx.get("name") or album_ctx.get("title") or "")
    if not has_real_album_name(album):
        album = get_import_clean_album(context, default="") or ""
    artist = ""
    album_artists = album_ctx.get("artists")
    if isinstance(album_artists, (list, tuple)) and album_artists:
        artist = extract_artist_name(album_artists[0])
    elif album_artists:
        artist = extract_artist_name(album_artists)
    artist = artist or extract_artist_name(artist_ctx)
    if not artist:
        artists = track_info.get("artists") or []
        if isinstance(artists, (list, tuple)) and artists:
            artist = extract_artist_name(artists[0])
    album_genres = list(album_ctx.get("genres") or [])
    track_album = track_info.get("album")
    if isinstance(track_album, dict):
        album_genres.extend(track_album.get("genres") or [])
    artist_genres = list(artist_ctx.get("genres") or [])
    return artist, album, album_genres, artist_genres


def _library_album(database: Any, artist: str, album: str) -> Dict[str, Any]:
    """what the library already knows about this album: its genres, its
    artist's genres and where its files are"""
    out: Dict[str, Any] = {"album_genres": [], "artist_genres": [], "paths": []}
    if database is None or not artist:
        return out
    try:
        with database._get_connection() as conn:
            row = conn.execute(
                "SELECT genres FROM lib2_artists WHERE LOWER(name) = LOWER(?) LIMIT 1",
                (artist,)).fetchone()
            if row and row[0]:
                out["artist_genres"] = _json_list(row[0])
            if not album:
                return out
            albums = conn.execute(
                "SELECT al.id, al.genres FROM lib2_albums al"
                " JOIN lib2_artists ar ON ar.id = al.primary_artist_id"
                " WHERE LOWER(al.title) = LOWER(?) AND LOWER(ar.name) = LOWER(?) LIMIT 5",
                (album, artist)).fetchall()
            for album_id, genres in albums:
                out["album_genres"].extend(_json_list(genres))
                out["paths"].extend(r[0] for r in conn.execute(
                    "SELECT f.path FROM lib2_track_files f JOIN lib2_tracks t ON t.id = f.track_id"
                    " WHERE t.album_id = ? AND COALESCE(f.file_state, 'active') = 'active'"
                    " AND f.path IS NOT NULL AND TRIM(f.path) <> '' LIMIT 50",
                    (album_id,)).fetchall())
    except Exception as exc:  # noqa: BLE001 - no library tables, nothing known
        logger.debug("library lookup for %s / %s failed: %s", artist, album, exc)
    return out


def _json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    text = str(value or "").strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return [text] if text else []


def decide_album(artist: str, album: str, album_genres: Iterable[Any] = (),
                 artist_genres: Iterable[Any] = (), *, database: Any = None,
                 current: Optional[Rules] = None) -> bool:
    """does this album go to the secondary folder. an album the library
    already has stays where its files are"""
    current = current or active_rules()
    if current is None or not (artist or album):
        return False
    from core import holiday_music as H
    key = (H.norm(artist), H.norm(album), current.path)
    now = time.monotonic()
    with _decided_lock:
        hit = _decided.get(key)
        if hit and now - hit[0] < _DECIDED_SECONDS:
            return hit[1]
    if database is None:
        try:
            from database.music_database import get_database
            database = get_database()
        except Exception:  # noqa: BLE001
            database = None
    known = _library_album(database, artist, album)
    paths = known["paths"]
    if paths:
        # the album is here already: it stays together where it is
        answer = any(is_under(p, current.path) for p in paths)
        why = "already there" if answer else "already in the main library"
    else:
        answer, why = album_matches(
            current, artist=artist, album=album,
            album_genres=[*album_genres, *known["album_genres"]],
            artist_genres=list(artist_genres) or known["artist_genres"],
            overrides=H.load_overrides(database) if current.match_holiday else None)
    with _decided_lock:
        _decided[key] = (now, answer)
    if answer:
        logger.info("[Secondary library] %s / %s goes to %s (%s)", artist, album,
                    current.name, why)
    return answer


def secondary_root_for_context(context: Any, database: Any = None) -> Optional[str]:
    """the secondary folder when this download's album matches, else None.
    never raises: a broken rule files the download in the main library"""
    if not isinstance(context, dict):
        return None
    try:
        current = active_rules()
        if current is None:
            return None
        artist, album, album_genres, artist_genres = _album_of_context(context)
        if decide_album(artist, album, album_genres, artist_genres, database=database,
                        current=current):
            return current.path
    except Exception as exc:  # noqa: BLE001
        logger.debug("secondary library rule skipped: %s", exc)
    return None


# ── albums already owned ────────────────────────────────────────────────────

def _main_root() -> str:
    from core.imports.paths import shared_transfer_root
    return os.path.normpath(shared_transfer_root())


def _db(database: Any = None) -> Any:
    if database is not None:
        return database
    from database.music_database import get_database
    return get_database()


def matching_owned_albums(database: Any = None, current: Optional[Rules] = None) -> List[Dict[str, Any]]:
    """the albums in the main library folder that a rule matches, with why.
    an owned album has had its genres filled in by now, and a holiday album is
    also one where half or more of its songs are holiday music"""
    current = current or active_rules()
    if current is None:
        return []
    database = _db(database)
    from core import holiday_music as H
    from core.library2.library_roots import normalize_prefix
    main = normalize_prefix(_main_root())
    overrides = H.load_overrides(database) if current.match_holiday else None
    mostly_holiday = set(H.holiday_album_ids(database)) if current.match_holiday else set()
    out: List[Dict[str, Any]] = []
    with database._get_connection() as conn:
        rows = conn.execute(
            "SELECT al.id, al.title, ar.name, al.genres, ar.genres FROM lib2_albums al"
            " JOIN lib2_artists ar ON ar.id = al.primary_artist_id"
            " WHERE EXISTS (SELECT 1 FROM lib2_tracks t JOIN lib2_track_files f ON f.track_id = t.id"
            " WHERE t.album_id = al.id AND COALESCE(f.file_state, 'active') = 'active'"
            " AND f.owner_profile_id IS NULL AND substr(f.path, 1, ?) = ?)",
            (len(main), main)).fetchall()
    for album_id, title, artist, album_genres, artist_genres in rows:
        hit, why = album_matches(current, artist=artist, album=title,
                                 album_genres=_json_list(album_genres),
                                 artist_genres=_json_list(artist_genres), overrides=overrides)
        if not hit and int(album_id) in mostly_holiday:
            flagged = (overrides or {}).get(("album", H.match_key("album", artist, title)))
            if flagged is not False:
                hit, why = True, "holiday"
        if hit:
            out.append({"album_id": int(album_id), "title": title, "artist": artist, "why": why})
    return out


def plan_move(database: Any = None) -> Dict[str, Any]:
    """what moving the matching owned albums would do, nothing touched.
    each album's folder is the folder holding all its files; it lands at the
    same place under the secondary folder, so the naming template still
    holds. items say 'move', 'exists' (something is already there, never
    overwritten) or 'shared folder' (the folder holds other albums too, as
    with files loose in an artist folder: moving it would take them along)"""
    current = active_rules()
    if current is None:
        return {"active": False, "items": [], "problem": rules().problem}
    database = _db(database)
    main = _main_root()
    items: List[Dict[str, Any]] = []
    with database._get_connection() as conn:
        for album in matching_owned_albums(database, current):
            paths = [r[0] for r in conn.execute(
                "SELECT f.path FROM lib2_track_files f JOIN lib2_tracks t ON t.id = f.track_id"
                " WHERE t.album_id = ? AND COALESCE(f.file_state, 'active') = 'active'"
                " AND f.path IS NOT NULL AND TRIM(f.path) <> ''", (album["album_id"],)).fetchall()]
            inside = [os.path.normpath(p) for p in paths if is_under(p, main)]
            if not inside:
                continue
            folder = os.path.commonpath([os.path.dirname(p) for p in inside])
            item = {**album, "src": folder, "tracks": len(inside)}
            if not is_under(folder, main) or os.path.normpath(folder) == main:
                items.append({**item, "dst": "", "action": "shared folder"})
                continue
            prefix = folder.rstrip(os.sep) + os.sep
            others = conn.execute(
                "SELECT COUNT(*) FROM lib2_track_files f JOIN lib2_tracks t ON t.id = f.track_id"
                " WHERE COALESCE(f.file_state, 'active') = 'active' AND t.album_id <> ?"
                " AND substr(f.path, 1, ?) = ?", (album["album_id"], len(prefix), prefix)).fetchone()[0]
            dst = os.path.join(current.path, os.path.relpath(folder, main))
            if others:
                action = "shared folder"
            elif os.path.lexists(dst):
                action = "exists"
            else:
                action = "move"
            items.append({**item, "dst": dst, "action": action})
    items.sort(key=lambda i: (str(i["artist"]).casefold(), str(i["title"]).casefold()))
    return {"active": True, "name": current.name, "folder": current.path, "items": items,
            "to_move": sum(1 for i in items if i["action"] == "move")}


def move_owned(apply: bool = False, database: Any = None,
               album_ids: Optional[Iterable[int]] = None) -> Dict[str, Any]:
    """preview (default) or move the matching owned albums. a move is the
    folder on disk and every catalogue path under it in one go, the same as
    an audiobook route's move (core/audiobook_routing.py); a failed repoint
    puts the folder back. nothing is overwritten or deleted, except artist
    folders the move left empty. a media server scan is asked for after"""
    import shutil
    from core.audiobook_routing import _repoint_moved_album
    plan = plan_move(database)
    result: Dict[str, Any] = {**plan, "applied": bool(apply), "moved": 0, "skipped": 0,
                              "failed": 0}
    wanted = {int(a) for a in album_ids} if album_ids is not None else None
    for item in plan.get("items", []):
        if item["action"] != "move" or (wanted is not None and item["album_id"] not in wanted):
            result["skipped"] += 1
            continue
        if not apply:
            continue
        moved_on_disk = False
        try:
            os.makedirs(os.path.dirname(item["dst"]), exist_ok=True)
            shutil.move(item["src"], item["dst"])
            moved_on_disk = True
            _repoint_moved_album(item["src"], item["dst"])
            item["action"] = "moved"
            result["moved"] += 1
        except Exception as exc:  # noqa: BLE001 - one album never stops the rest
            if moved_on_disk:
                try:
                    if os.path.lexists(item["src"]):
                        raise OSError("source reappeared during move rollback")
                    shutil.move(item["dst"], item["src"])
                except OSError as rollback_error:
                    item["rollback_error"] = str(rollback_error)
            item["action"] = "failed"
            item["error"] = str(exc)
            result["failed"] += 1
            logger.warning("[Secondary library] could not move %s: %s", item["src"], exc)
    if apply and result["moved"]:
        main = _main_root()
        for item in plan.get("items", []):
            if item["action"] != "moved":
                continue
            parent = os.path.dirname(item["src"])
            while is_under(parent, main) and os.path.normpath(parent) != main:
                try:
                    os.rmdir(parent)  # only succeeds when empty
                except OSError:
                    break
                parent = os.path.dirname(parent)
        forget_decisions()
        result["scan"] = request_media_scan(f"{result['moved']} album(s) moved to {plan.get('name')}")
    return result


def request_media_scan(reason: str) -> bool:
    """ask the media server to rescan, as finished downloads do. False when
    no server or scan manager is around"""
    import sys
    for name in ("web_server", "__main__"):
        manager = getattr(sys.modules.get(name), "web_scan_manager", None)
        if manager is not None and hasattr(manager, "request_scan"):
            try:
                manager.request_scan(reason)
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("scan request failed: %s", exc)
                return False
    return False


# ── can the media server see it ─────────────────────────────────────────────

_AUDIO = (".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".aif",
          ".wma", ".alac", ".ape", ".wv", ".dsf", ".dff")
_last_check: Dict[str, Any] = {}


def _audio_files_on_disk(folder: str, limit: int = 200) -> int:
    count = 0
    for _root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        count += sum(1 for f in files if f.lower().endswith(_AUDIO))
        if count >= limit:
            break
    return count


def visibility_check(database: Any = None, server: Optional[str] = None) -> Dict[str, Any]:
    """does the media server return anything from the secondary folder.
    hidden is True when the folder has music but the server's last scan
    returned none of it: SoulSync's server account has lost access, and
    those albums are known only from SoulSync's own files"""
    out: Dict[str, Any] = {"checked": False, "hidden": False}
    current = active_rules()
    if current is None:
        return out
    if server is None:
        try:
            from core.settings import config_manager
            server = config_manager.get_active_media_server()
        except Exception:  # noqa: BLE001
            server = None
    out.update(name=current.name, folder=current.path, server=server or "")
    if not server or server == "soulsync" or not os.path.isdir(current.path):
        return out
    on_disk = _audio_files_on_disk(current.path)
    out.update(checked=True, files_on_disk=on_disk)
    if not on_disk:
        return out
    from core.library2.library_roots import normalize_prefix
    prefix = normalize_prefix(current.path)
    seen = 0
    try:
        with _db(database)._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM lib2_track_files f"
                " JOIN lib2_tracks t ON t.id = f.track_id"
                " WHERE COALESCE(f.file_state, 'active') = 'active' AND substr(f.path, 1, ?) = ?"
                " AND (t.server_source = ? OR EXISTS (SELECT 1 FROM lib2_media_server_mappings m"
                "  WHERE m.entity_type = 'track' AND m.entity_id = t.id AND m.server_source = ?))"
                " LIMIT 1)", (len(prefix), prefix, server, server)).fetchone()
            seen = int(row[0] or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("secondary library visibility check failed: %s", exc)
        return out
    out.update(server_sees=bool(seen), hidden=not seen)
    return out


def after_scan(database: Any = None) -> Dict[str, Any]:
    """run after a media server scan: check again, and tell the admin once
    when the server stopped returning the secondary folder"""
    result = visibility_check(database)
    was_hidden = bool(_last_check.get("hidden"))
    _last_check.clear()
    _last_check.update(result, at=time.time())
    if result.get("hidden") and not was_hidden:
        try:
            from core.profile_notify import notify_profile
            notify_profile(1, hidden_message(result), "warning", "/settings/library")
        except Exception as exc:  # noqa: BLE001
            logger.debug("secondary library note not sent: %s", exc)
    return result


def last_check() -> Dict[str, Any]:
    return dict(_last_check)


def hidden_message(result: Dict[str, Any]) -> str:
    server = str(result.get("server") or "your media server").title()
    return (f"{server} returned none of the music in your {result.get('name') or 'secondary'} "
            f"library ({result.get('folder')}). Give SoulSync's {server} account access to that "
            "library again. Until then SoulSync still counts those albums as owned from its own "
            "files, so nothing is downloaded twice.")


def ensure_library_folder(config: Any = None) -> bool:
    """the secondary folder is one of SoulSync's library folders
    (library.music_paths), so every reader that resolves, watches, repoints or
    deletes library files knows it. added when the rules are on, never taken
    away. True when it was added"""
    if config is None:
        from core.settings import config_manager as config
    current = rules(config)
    if not current.active:
        return False
    raw = str(config.get(PREFIX + "path", "") or "").strip()
    listed = list(config.get("library.music_paths", []) or [])
    try:
        from core.imports.paths import config_root_path
        resolve = config_root_path
    except Exception:  # noqa: BLE001
        def resolve(value: Any) -> str:
            return str(value)
    want = os.path.normpath(current.path)
    if any(os.path.normpath(resolve(str(p))) == want for p in listed if str(p).strip()):
        return False
    config.set("library.music_paths", [*listed, raw])
    return True
