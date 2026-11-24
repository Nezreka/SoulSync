"""SoulSync Standalone for video: soulsync scans its own folders.

a media source like PlexVideoSource / JellyfinVideoSource (the scanner's duck
type: core/video/scanner.py), read off the disk instead of a server. it walks
the movie and TV folders soulsync files into (core/video/library_roots.py),
works out what each title is, and yields the same dicts the server sources
do, so the scanner, the upserts, the library pages, the wishlist and the
calendar all work unchanged. its rows are filed under 'soulsync' with ``ss:``
ids from their place on disk (core/video/owned_ids.py), the same ids a
landed download gets.

what a title is, in order, the first that answers:
1. the id soulsync already has for that folder (a scan never asks twice,
   and a fix-match the person made stays theirs);
2. an id in a .nfo file in the folder (movie.nfo, tvshow.nfo, <name>.nfo),
   which also gives the title as it is written;
3. an id tag in the folder name: ``{tmdb-603}``, ``[imdbid-tt0133093]``,
   ``{tvdb-121361}``;
4. the folder's name and year searched on TMDB, taken only when the match is
   sure (title and year agree: core/video/import_folder.match).
anything less waits for the person: the row is added under its folder name
with ``review`` as its match status, which enrichment never guesses at, and
the Tools page lists it as unmatched.

two folders that are the same show ('9-1-1 Nashville' and '9-1-1 - Nashville')
are one show in the library, as a media server lists them.

episodes are placed by SxxEyy, by air date (daily shows) and by absolute
number (anime: 'Show - 127'), the last two against the show's episode list
once enrichment has it. a file's edition ('{edition-Director's Cut}',
Extended, Unrated ...) is kept, and a poster.jpg in the folder is the
title's poster until a better one is chosen.

safe by design: a folder that can't be read stops the scan before anything
is pruned (scan_problem), and a scan never deletes a title whose file is
still on disk.
"""

from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video_disk_source")

SERVER_NAME = "soulsync"

# folders that are never a title or an episode's home
_SKIP_DIRS = re.compile(
    r"^(?:\.|@eadir$|#recycle$|\$recycle\.bin$|lost\+found$|extras?$|featurettes?$|"
    r"behind the scenes$|deleted scenes$|interviews?$|scenes?$|shorts?$|trailers?$|"
    r"samples?$|other$|bonus$|subs?$|subtitles?$)", re.I)

_TAG_TMDB = re.compile(r"[\[{(](?:tmdb|tmdbid)[-=: ](\d+)[\]})]", re.I)
_TAG_IMDB = re.compile(r"[\[{(](?:imdb|imdbid)[-=: ](tt\d+)[\]})]", re.I)
_TAG_TVDB = re.compile(r"[\[{(](?:tvdb|tvdbid)[-=: ](\d+)[\]})]", re.I)
_TAG_ANY = re.compile(r"\s*[\[{(](?:tmdb|tmdbid|imdb|imdbid|tvdb|tvdbid)[-=: ][^\]})]*[\]})]", re.I)

_NFO_PATTERNS = {
    "tmdb_id": (re.compile(r"<tmdbid>\s*(\d+)\s*</tmdbid>", re.I),
                re.compile(r"<uniqueid[^>]*type=\"tmdb\"[^>]*>\s*(\d+)\s*<", re.I),
                re.compile(r"themoviedb\.org/(?:movie|tv)/(\d+)", re.I)),
    "imdb_id": (re.compile(r"<imdbid>\s*(tt\d+)\s*</imdbid>", re.I),
                re.compile(r"<uniqueid[^>]*type=\"imdb\"[^>]*>\s*(tt\d+)\s*<", re.I),
                re.compile(r"imdb\.com/title/(tt\d+)", re.I),
                re.compile(r"<id>\s*(tt\d+)\s*</id>", re.I)),
    "tvdb_id": (re.compile(r"<tvdbid>\s*(\d+)\s*</tvdbid>", re.I),
                re.compile(r"<uniqueid[^>]*type=\"tvdb\"[^>]*>\s*(\d+)\s*<", re.I),
                re.compile(r"thetvdb\.com/\?tab=series&id=(\d+)", re.I)),
}

_SEASON_DIR = re.compile(r"^(?:season|series|staffel|saison|temporada)[ ._-]*(\d+)$|^s(\d{1,2})$", re.I)
_SPECIALS_DIR = re.compile(r"^specials?$", re.I)
# plex's extras naming: 'Movie-trailer.mkv', '-behindthescenes', ... are not the feature
_EXTRA_SUFFIX = re.compile(r"-(?:trailer|behindthescenes|deleted|featurette|interview|scene|short|other)$", re.I)
# 'Show - 05', '[Group] Show - 127 [1080p]', 'Show - 12v2': a bare episode number
_BARE_EP = re.compile(r"\s-\s(\d{1,4})(?:v\d)?(?=\s|$|\.|\[|\()")


def _stamp(ts: float) -> Optional[str]:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def ids_from_name(name: str) -> Dict[str, Any]:
    """the ids a folder name carries in tags, e.g. 'The Matrix (1999) {tmdb-603}'"""
    out: Dict[str, Any] = {}
    m = _TAG_TMDB.search(name or "")
    if m:
        out["tmdb_id"] = int(m.group(1))
    m = _TAG_IMDB.search(name or "")
    if m:
        out["imdb_id"] = m.group(1)
    m = _TAG_TVDB.search(name or "")
    if m:
        out["tvdb_id"] = int(m.group(1))
    return out


def ids_from_nfo(text: str) -> Dict[str, Any]:
    """the ids a kodi/jellyfin style .nfo names"""
    out: Dict[str, Any] = {}
    for key, pats in _NFO_PATTERNS.items():
        for p in pats:
            m = p.search(text or "")
            if m:
                out[key] = m.group(1) if key == "imdb_id" else int(m.group(1))
                break
    return out


_NFO_TITLE = re.compile(r"<title>\s*([^<]+?)\s*</title>", re.I)
_NFO_YEAR = re.compile(r"<year>\s*((?:19|20)\d{2})\s*</year>", re.I)


def meta_from_nfo(text: str) -> Dict[str, Any]:
    """the title and year a .nfo gives, as written"""
    out: Dict[str, Any] = {}
    m = _NFO_TITLE.search(text or "")
    if m:
        out["title"] = html.unescape(m.group(1)).strip()
    m = _NFO_YEAR.search(text or "")
    if m:
        out["year"] = int(m.group(1))
    return out


_NAMED_YEAR = re.compile(r"^(?P<t>.+?)\s*[\(\[]\s*(?P<y>(?:19|20)\d{2})\s*[\)\]]")


def title_from_folder(name: str) -> tuple:
    """(title, year) from a folder name. a name a person wrote ('9-1-1',
    'The Matrix (1999) [1080p]') is taken as written; only a scene-style name
    ('The.Matrix.1999.1080p.BluRay') goes through the release parser, which
    would turn '9-1-1' into '9 1 1'."""
    from core.video.import_folder import _title_of, _year_of
    base = clean_name(name)
    m = _NAMED_YEAR.match(base)
    if m and m.group("t").strip():
        return m.group("t").strip(), int(m.group("y"))
    scene = "." in base or "_" in base or re.search(r"\b(?:19|20)\d{2}\b", base)
    if not scene and base:
        return base, None
    return (_title_of(base) or base), _year_of(base)


_EDITION_TAG = re.compile(r"\{edition-([^}]+)\}", re.I)
_EDITIONS = (
    (re.compile(r"\bdirector'?s?[ ._-]?cut\b", re.I), "Director's Cut"),
    (re.compile(r"\bextended(?:[ ._-](?:cut|edition))?\b", re.I), "Extended"),
    (re.compile(r"\bunrated\b", re.I), "Unrated"),
    (re.compile(r"\btheatrical(?:[ ._-]cut)?\b", re.I), "Theatrical"),
    (re.compile(r"\bfinal[ ._-]cut\b", re.I), "Final Cut"),
    (re.compile(r"\bimax\b", re.I), "IMAX"),
    (re.compile(r"\bremastered\b", re.I), "Remastered"),
    (re.compile(r"\bcriterion\b", re.I), "Criterion"),
    (re.compile(r"\bspecial[ ._-]edition\b", re.I), "Special Edition"),
)


def edition_of(file_name: str, title: str = "") -> Optional[str]:
    """the edition a file name states: a plex '{edition-...}' tag, or a known
    word. the title itself is left out first ('Extended Family' is no edition)."""
    name = os.path.splitext(file_name or "")[0]
    m = _EDITION_TAG.search(name)
    if m:
        return m.group(1).strip() or None
    rest = name
    if title:
        rest = re.sub(re.escape(title), " ", rest, flags=re.I)
        rest = re.sub(re.escape(title.replace(" ", ".")), " ", rest, flags=re.I)
    for pat, label in _EDITIONS:
        if pat.search(rest):
            return label
    return None


_POSTER_NAMES = ("poster.jpg", "poster.png", "folder.jpg", "folder.png", "cover.jpg", "movie.jpg", "show.jpg")
LOCAL_ART = "local:"


def clean_name(name: str) -> str:
    """a folder name without its id tags, for the title search"""
    return _TAG_ANY.sub("", name or "").strip()


class _Walker:
    """the filesystem, injectable for tests"""

    def __init__(self, scandir=os.scandir, isdir=os.path.isdir, read_text=None):
        self.scandir = scandir
        self.isdir = isdir
        self.read_text = read_text or self._read_text

    @staticmethod
    def _read_text(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read(200_000)
        except OSError:
            return ""

    def entries(self, path: str) -> List[Any]:
        try:
            with self.scandir(path) as it:
                return sorted(it, key=lambda e: e.name.lower())
        except OSError:
            return []


def _is_video(name: str) -> bool:
    from core.video.importer import is_video
    return is_video(name)


def _junk(path: str) -> bool:
    try:
        from core.video.season_pack import _is_junk
        return bool(_is_junk(path))
    except Exception:   # noqa: BLE001
        return False


class DiskVideoSource:
    """soulsync's own folders as a video library source (see module doc)"""

    server_name = SERVER_NAME

    def __init__(self, db, *, movie_roots: Optional[List[str]] = None,
                 tv_roots: Optional[List[str]] = None, search: Optional[Callable] = None,
                 walker: Optional[_Walker] = None, scanner_status: Optional[Callable] = None,
                 hash_lookup: Optional[Callable] = None):
        self.db = db
        if movie_roots is None or tv_roots is None:
            from core.video.library_roots import library_roots
            movie_roots = library_roots(db, "movie") if movie_roots is None else movie_roots
            tv_roots = library_roots(db, "episode") if tv_roots is None else tv_roots
        self.movie_roots = [r for r in dict.fromkeys(movie_roots or []) if r]
        self.tv_roots = [r for r in dict.fromkeys(tv_roots or []) if r]
        self._search = search
        self._hash_lookup = hash_lookup
        self.fs = walker or _Walker()
        self._scanner_status = scanner_status
        self._known: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._aliases: Optional[Dict[str, str]] = None
        self._maps: Dict[int, tuple] = {}

    # ── what the scanner asks first ──────────────────────────────────────────
    def scan_problem(self, media_type="all"):
        """a reason not to scan, or None. a folder that can't be reached stops
        the scan: reading it as empty would demote everything in it."""
        wants = []
        if media_type in ("all", "movie"):
            wants.append(("Movies", self.movie_roots))
        if media_type in ("all", "show"):
            wants.append(("TV", self.tv_roots))
        if not any(roots for _label, roots in wants):
            return "Set your Movies and TV folders in Settings > Video > Folders first."
        for label, roots in wants:
            for r in roots:
                if not self.fs.isdir(r):
                    return ("The %s folder %s can't be reached, so the scan stopped "
                            "and nothing was removed." % (label, r))
        return None

    def available_libraries(self) -> dict:
        return {"movies": [], "tv": []}

    def library_folders(self) -> list:
        return list(self.movie_roots) + list(self.tv_roots)

    def counts(self, incremental=False) -> dict:
        return {"movies": sum(1 for _ in self._movie_entries()),
                "shows": sum(1 for _ in self._show_dirs())}

    def is_scanning(self, media_type="all") -> bool:
        if self._scanner_status is not None:
            return bool(self._scanner_status())
        from core.video.scanner import get_video_scanner
        return (get_video_scanner(self.db).get_status() or {}).get("state") == "scanning"

    def refresh_sections(self, media_type="all") -> dict:
        """'scan the server' is a scan of soulsync's own folders"""
        from core.video.scanner import get_video_scanner
        r = get_video_scanner(self.db).request_scan(
            lambda: self, mode="incremental", media_type=media_type)
        return {"ok": True, "sections": [SERVER_NAME], **r}

    def has_item(self, media_type, item) -> bool:
        """a landed download is already in the library (library_ingest wrote
        it), so the post-download scan can skip the crawl"""
        tmdb = _int((item or {}).get("tmdb_id"))
        if not tmdb:
            return False
        sql = ("SELECT 1 FROM movies WHERE server_source=? AND tmdb_id=? AND has_file=1 LIMIT 1"
               if media_type == "movie" else
               "SELECT 1 FROM shows WHERE server_source=? AND tmdb_id=? LIMIT 1")
        conn = self.db._get_connection()
        try:
            return conn.execute(sql, (SERVER_NAME, tmdb)).fetchone() is not None
        finally:
            conn.close()

    # ── what soulsync already knows ──────────────────────────────────────────
    def _known_rows(self, table: str) -> Dict[str, Dict[str, Any]]:
        """this library's rows by their ss: id, read once per scan"""
        if table not in self._known:
            cols = ("id, server_id, title, year, tmdb_id, imdb_id, tmdb_match_status, poster_url"
                    + (", tvdb_id" if table == "shows" else ""))
            conn = self.db._get_connection()
            try:
                self._known[table] = {r["server_id"]: dict(r) for r in conn.execute(
                    f"SELECT {cols} FROM {table} WHERE server_source=?", (SERVER_NAME,))}
            finally:
                conn.close()
        return self._known[table]

    def _show_aliases(self) -> Dict[str, str]:
        """a show folder's id -> the id of the show row its files are already
        on: a second folder of a show soulsync merged is known by this, so it
        isn't looked up again every scan"""
        if self._aliases is None:
            from core.video import owned_ids
            out: Dict[str, str] = {}
            conn = self.db._get_connection()
            try:
                rows = conn.execute(
                    "SELECT s.server_id, f.relative_path FROM media_files f "
                    "JOIN episodes e ON e.id=f.episode_id JOIN shows s ON s.id=e.show_id "
                    "WHERE s.server_source=?", (SERVER_NAME,)).fetchall()
            finally:
                conn.close()
            for sid, path in rows:
                out.setdefault(owned_ids.show_id(path, self.tv_roots), sid)
            self._aliases = out
        return self._aliases

    def _known_for(self, kind: str, folder: str) -> Optional[Dict[str, Any]]:
        from core.video import owned_ids
        sid = owned_ids.of_path(folder)
        if kind == "movie":
            return self._known_rows("movies").get(sid)
        rows = self._known_rows("shows")
        return rows.get(sid) or rows.get(self._show_aliases().get(sid))

    def _identify(self, kind: str, folder: str, name: str, files: List[str],
                  nfo_names: Iterable[str], *, search: bool = True) -> Dict[str, Any]:
        """{title, year, tmdb_id, imdb_id, tvdb_id, review, row} for a title
        folder. ``search=False`` stops before the TMDB search (``unsearched``
        is set when one would have run), for a caller with a cheaper way
        to try first."""
        known = self._known_for(kind, folder)
        if known and (known.get("tmdb_id") or known.get("tvdb_id") or known.get("imdb_id")):
            return {"title": known["title"], "year": known["year"], "tmdb_id": known.get("tmdb_id"),
                    "imdb_id": known.get("imdb_id"), "tvdb_id": known.get("tvdb_id"), "review": False,
                    "row": known}
        ids: Dict[str, Any] = {}
        meta: Dict[str, Any] = {}
        for nfo in nfo_names:
            text = self.fs.read_text(nfo)
            if not text:
                continue
            meta = meta or meta_from_nfo(text)
            ids = ids_from_nfo(text)
            if ids:
                break
        for k, v in ids_from_name(name).items():
            ids.setdefault(k, v)
        title, year = title_from_folder(name)
        title = meta.get("title") or title
        year = meta.get("year") or year
        poster = None
        if not (ids.get("tmdb_id") or ids.get("tvdb_id") or ids.get("imdb_id")):
            if known:
                # already waiting for the person: not searched again each scan
                return {"title": known["title"], "year": known["year"], "review": True, "row": known}
            if not search:
                return {"title": title, "year": year, "review": True, "row": None, "unsearched": True}
            m = self._match(kind, title, year, files)
            if m.get("tmdb_id"):
                ids["tmdb_id"] = int(m["tmdb_id"])
                title = m.get("title") or title
                year = m.get("year") or year
                poster = m.get("poster_url")
            if m.get("imdb_id"):
                ids["imdb_id"] = m["imdb_id"]
        return {"title": title, "year": year, "tmdb_id": ids.get("tmdb_id"),
                "imdb_id": ids.get("imdb_id"), "tvdb_id": ids.get("tvdb_id"), "poster_url": poster,
                "review": not (ids.get("tmdb_id") or ids.get("tvdb_id") or ids.get("imdb_id")),
                "row": known}

    def _match(self, kind: str, title: str, year, files: List[str]) -> Dict[str, Any]:
        """a sure match only: the folder's title and year on TMDB, then the
        main file's own name when the folder's says nothing, then (movies) the
        file's fingerprint. {} when none is sure."""
        from core.video.import_folder import _default_search, _title_of, _year_of, match
        search = self._search or _default_search
        tries = [(title, year)]
        main = self._biggest(files)
        if main:
            stem = os.path.splitext(os.path.basename(main))[0]
            t2, y2 = _title_of(stem), _year_of(stem)
            if t2 and (t2, y2) != (title, year):
                tries.append((t2, y2))
        for t, y in tries:
            if not t:
                continue
            try:
                m = match({"kind": kind, "title": t, "year": y}, search)
            except Exception:   # noqa: BLE001 - no search: it waits for the person
                logger.debug("disk scan: search failed for %r", t, exc_info=True)
                continue
            if m.get("confidence") == "high" and m.get("tmdb_id"):
                return m
        if kind == "movie" and main and self._hash_lookup is not None:
            try:
                got = self._hash_lookup(main) or {}
            except Exception:   # noqa: BLE001 - the fingerprint is an assist
                logger.debug("disk scan: fingerprint lookup failed for %s", main, exc_info=True)
                got = {}
            if got.get("tmdb_id") or got.get("imdb_id"):
                return {"tmdb_id": got.get("tmdb_id"), "imdb_id": got.get("imdb_id")}
        return {}

    @staticmethod
    def _biggest(files: List[str]) -> Optional[str]:
        best, size = None, -1
        for p in files or []:
            try:
                n = os.path.getsize(p)
            except OSError:
                n = 0
            if n > size:
                best, size = p, n
        return best

    def _local_poster(self, folder: str, known: Optional[Dict[str, Any]]) -> Optional[str]:
        """a poster.jpg in the title's folder, for a title with no poster yet
        (or still on a local one): the person's own artwork shows without
        waiting for enrichment"""
        if known and known.get("poster_url") and not str(known["poster_url"]).startswith(LOCAL_ART):
            return None
        if not self.fs.isdir(folder):
            return None
        names = {e.name.lower(): e.path for e in self.fs.entries(folder) if not e.is_dir()}
        for n in _POSTER_NAMES:
            if n in names:
                return LOCAL_ART + names[n]
        return None

    # ── movies ───────────────────────────────────────────────────────────────
    def _movie_entries(self):
        """(folder, name, video paths, nfo paths) for every movie: a folder
        under a movie root, or a video file loose in the root"""
        for root in self.movie_roots:
            for e in self.fs.entries(root):
                if e.name.startswith(".") or _SKIP_DIRS.match(e.name):
                    continue
                if e.is_dir():
                    videos, nfos = self._movie_files(e.path)
                    if videos:
                        yield e.path, e.name, videos, nfos
                elif _is_video(e.name) and not _junk(e.path):
                    stem = os.path.splitext(e.name)[0]
                    nfo = os.path.join(root, stem + ".nfo")
                    yield e.path, stem, [e.path], [nfo]

    def _movie_files(self, folder: str, depth: int = 0):
        videos, nfos = [], []
        for e in self.fs.entries(folder):
            if e.is_dir():
                if depth < 2 and not _SKIP_DIRS.match(e.name):
                    v, n = self._movie_files(e.path, depth + 1)
                    videos += v
                    nfos += n
            elif e.name.lower().endswith(".nfo"):
                nfos.append(e.path)
            elif _is_video(e.name) and not _junk(e.path) and not _EXTRA_SUFFIX.search(
                    os.path.splitext(e.name)[0]):
                videos.append(e.path)
        # movie.nfo first, then the rest
        nfos.sort(key=lambda p: (os.path.basename(p).lower() != "movie.nfo", p))
        return videos, nfos

    def _changed_since(self, paths: Iterable[str], since) -> bool:
        if since is None:
            return True
        cutoff = since.timestamp() if hasattr(since, "timestamp") else float(since)
        for p in paths:
            try:
                if os.stat(p).st_mtime >= cutoff:
                    return True
            except OSError:
                return True
        return False

    def _movie(self, folder: str, name: str, videos: List[str], nfos: List[str]) -> Dict[str, Any]:
        from core.video import owned_ids
        who = self._identify("movie", folder, name, videos, nfos)
        files = [self._facts(p, who["title"]) for p in videos]
        added = min((f.pop("_mtime") for f in files), default=None)
        item = {"server_id": owned_ids.of_path(folder), "title": who["title"], "year": who["year"],
                "tmdb_id": who.get("tmdb_id"), "imdb_id": who.get("imdb_id"),
                "files": files, "file": files[0] if files else None,
                "server_added_at": _stamp(added) if added else None,
                "_review": who["review"]}
        poster = self._local_poster(folder, who.get("row")) or who.get("poster_url")
        if poster:
            item["poster_url"] = poster
        return item

    def iter_movies(self, incremental=False, since=None):
        for folder, name, videos, nfos in self._movie_entries():
            if incremental and not self._changed_since([folder, *videos], since):
                continue
            try:
                yield self._movie(folder, name, videos, nfos)
            except Exception:   # noqa: BLE001 - one unreadable title never stops the scan
                logger.exception("disk scan: skipping movie %s", folder)

    def movie_item(self, server_id, title=None, tmdb_id=None):
        """one movie again, for its page's Sync. None only when its folder is
        gone while the library folders are reachable."""
        self._reachable(self.movie_roots)
        from core.video import owned_ids
        want = {server_id} | {owned_ids.movie_id(p, self.movie_roots)
                              for p in self._row_paths("movies", server_id)}
        for folder, name, videos, nfos in self._movie_entries():
            if owned_ids.of_path(folder) in want:
                return self._movie(folder, name, videos, nfos)
        return None

    def _reachable(self, roots: List[str]) -> None:
        for r in roots:
            if not self.fs.isdir(r):
                raise RuntimeError("The library folder %s can't be reached" % r)

    def _row_paths(self, table: str, server_id: str) -> List[str]:
        owner = ("movie_id IN (SELECT id FROM movies WHERE server_source=? AND server_id=?)"
                 if table == "movies" else
                 "episode_id IN (SELECT e.id FROM episodes e JOIN shows s ON s.id=e.show_id "
                 "WHERE s.server_source=? AND s.server_id=?)")
        conn = self.db._get_connection()
        try:
            return [r[0] for r in conn.execute(
                f"SELECT relative_path FROM media_files WHERE {owner}", (SERVER_NAME, server_id))]
        finally:
            conn.close()

    # ── shows ────────────────────────────────────────────────────────────────
    def _show_dirs(self):
        for root in self.tv_roots:
            for e in self.fs.entries(root):
                if e.is_dir() and not e.name.startswith(".") and not _SKIP_DIRS.match(e.name):
                    yield e.path, e.name

    def _show_files(self, folder: str, depth: int = 0, season_hint=None):
        """(path, season from its folder) for every video under a show"""
        out = []
        for e in self.fs.entries(folder):
            if e.is_dir():
                if depth >= 3 or _SKIP_DIRS.match(e.name):
                    continue
                m = _SEASON_DIR.match(e.name)
                hint = (int(m.group(1) or m.group(2)) if m else
                        0 if _SPECIALS_DIR.match(e.name) else season_hint)
                out += self._show_files(e.path, depth + 1, hint)
            elif _is_video(e.name) and not _junk(e.path) and not _EXTRA_SUFFIX.search(
                    os.path.splitext(e.name)[0]):
                out.append((e.path, season_hint))
        return out

    def _episode_maps(self, show_row_id) -> tuple:
        """(air date -> (s, e), absolute number -> (s, e), season -> size) from
        the show's episode list, once enrichment has it. a daily show's files
        are named by date and an anime's by absolute number."""
        if not show_row_id:
            return {}, {}, {}
        if show_row_id not in self._maps:
            conn = self.db._get_connection()
            try:
                rows = conn.execute(
                    "SELECT season_number, episode_number, air_date FROM episodes WHERE show_id=? "
                    "ORDER BY season_number, episode_number", (show_row_id,)).fetchall()
            finally:
                conn.close()
            dates, absolute, sizes, n = {}, {}, {}, 0
            for s, e, air in rows:
                sizes[s] = sizes.get(s, 0) + 1
                if air:
                    dates.setdefault(str(air)[:10], (s, e))
                if s and s > 0:
                    n += 1
                    absolute[n] = (s, e)
            self._maps[show_row_id] = (dates, absolute, sizes)
        return self._maps[show_row_id]

    def _episode_keys(self, path: str, season_hint, show_row_id) -> List[tuple]:
        """the (season, episode) pairs one file is"""
        from core.video.season_pack import episode_keys_for
        name = os.path.basename(path)
        dates, absolute, sizes = self._episode_maps(show_row_id)
        keys = episode_keys_for(name, air_dates=dates)
        if keys:
            return [(int(s), int(e)) for s, e in keys][:50]
        m = _BARE_EP.search(os.path.splitext(name)[0])
        if not m:
            return []
        n = int(m.group(1))
        if season_hint is not None and season_hint > 0:
            size = sizes.get(season_hint)
            if size is None or n <= size:
                return [(int(season_hint), n)]     # 'Show - 05' in 'Season 2': its fifth
        if n in absolute:
            return [absolute[n]]                   # anime: 'Show - 127'
        if season_hint == 0:
            return [(0, n)]                        # a numbered special
        return []

    def _show_groups(self):
        """[{who, folders, files}]: every show, its folders together. two
        folders that are the same show are one show.

        a folder nothing identifies ('60.Minutes', no .nfo) whose title is
        exactly that of one show this library already has identified
        ('60 Minutes') is that show: the person's own library says which of
        TMDB's several it is. only then is TMDB asked."""
        from core.video.release_parse import normalize_title
        found = []
        for folder, name in self._show_dirs():
            try:
                files = self._show_files(folder)
                if not files:
                    continue
                who = self._identify("show", folder, name, [p for p, _ in files],
                                     [os.path.join(folder, "tvshow.nfo")], search=False)
            except Exception:   # noqa: BLE001 - one unreadable show never stops the scan
                logger.exception("disk scan: skipping show %s", folder)
                continue
            found.append((folder, name, files, who))

        def key_of(who, folder):
            row = who.get("row")
            return (("row", row["id"]) if row else
                    ("tmdb", who["tmdb_id"]) if who.get("tmdb_id") else
                    ("tvdb", who["tvdb_id"]) if who.get("tvdb_id") else
                    ("imdb", who["imdb_id"]) if who.get("imdb_id") else ("path", folder))

        by_title: Dict[str, set] = {}
        firsts: Dict[Any, Dict[str, Any]] = {}
        for folder, _name, _files, who in found:
            if not who.get("unsearched") and not who["review"]:
                k = key_of(who, folder)
                by_title.setdefault(normalize_title(who["title"]), set()).add(k)
                firsts.setdefault(k, who)
        groups: Dict[Any, Dict[str, Any]] = {}
        for folder, name, files, who in found:
            if who.get("unsearched"):
                same = by_title.get(normalize_title(who["title"])) or set()
                if len(same) == 1:
                    who = firsts[next(iter(same))]
                else:
                    try:
                        who = self._identify("show", folder, name, [p for p, _ in files],
                                             [os.path.join(folder, "tvshow.nfo")])
                    except Exception:   # noqa: BLE001
                        logger.exception("disk scan: skipping show %s", folder)
                        continue
            g = groups.setdefault(key_of(who, folder), {"who": who, "folders": [], "files": []})
            g["folders"].append(folder)
            g["files"] += files
        return list(groups.values())

    def _show(self, group: Dict[str, Any]) -> Dict[str, Any]:
        from core.video import owned_ids
        who, folders = group["who"], group["folders"]
        row = who.get("row")
        sid = row["server_id"] if row else owned_ids.of_path(folders[0])
        seasons: Dict[int, Dict[tuple, Dict[str, Any]]] = {}
        unplaced = 0
        for path, hint in group["files"]:
            keys = self._episode_keys(path, hint, row["id"] if row else None)
            if not keys:
                unplaced += 1
                continue
            facts = self._facts(path)
            mtime = facts.pop("_mtime")
            for i, (s, e) in enumerate(keys):
                eps = seasons.setdefault(s, {})
                cur = eps.get((s, e))
                if cur is None:
                    eps[(s, e)] = {"server_id": owned_ids.of_path(path) + ("" if i == 0 else "-e%d" % e),
                                   "episode_number": e, "files": [dict(facts)],
                                   "added_at": _stamp(mtime) if mtime else None}
                else:
                    cur["files"].append(dict(facts))    # a second version of the episode
        if unplaced:
            logger.info("disk scan: %d file(s) of %s have no episode soulsync can place yet",
                        unplaced, who["title"])
        item = {"server_id": sid, "title": who["title"], "year": who["year"],
                "tmdb_id": who.get("tmdb_id"), "imdb_id": who.get("imdb_id"),
                "tvdb_id": who.get("tvdb_id"),
                "seasons": [{"season_number": s, "episodes": [eps[k] for k in sorted(eps)]}
                            for s, eps in sorted(seasons.items())],
                "_review": who["review"], "_unplaced": unplaced}
        poster = self._local_poster(folders[0], row) or who.get("poster_url")
        if poster:
            item["poster_url"] = poster
        return item

    def iter_shows(self, incremental=False, since=None):
        for group in self._show_groups():
            if incremental and since is not None and not any(
                    self._show_changed(f, since) for f in group["folders"]):
                continue
            try:
                yield self._show(group)
            except Exception:   # noqa: BLE001 - one unreadable show never stops the scan
                logger.exception("disk scan: skipping show %s", group["folders"][0])

    def _show_changed(self, folder: str, since) -> bool:
        """a file added or removed changes its folder's mtime: the show folder
        or one of its season folders"""
        dirs = [folder] + [e.path for e in self.fs.entries(folder) if e.is_dir()]
        return self._changed_since(dirs, since)

    def show_tree(self, server_id, title=None, tmdb_id=None):
        self._reachable(self.tv_roots)
        for group in self._show_groups():
            row = group["who"].get("row")
            from core.video import owned_ids
            if (row and row["server_id"] == server_id) or \
                    server_id in {owned_ids.of_path(f) for f in group["folders"]}:
                return self._show(group)
        return None

    # ── a file's facts ───────────────────────────────────────────────────────
    @staticmethod
    def _facts(path: str, title: str = "") -> Dict[str, Any]:
        from core.video.release_parse import parse_release
        from core.video.sources import _format_from_name
        name = os.path.basename(path)
        p = parse_release(os.path.splitext(name)[0])
        try:
            st = os.stat(path)
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            size, mtime = None, time.time()
        out = {"relative_path": path, "size_bytes": size, "resolution": p.get("resolution"),
               "release_source": p.get("source"), "video_codec": p.get("codec"), "_mtime": mtime,
               "edition": edition_of(name, title)}
        out.update(_format_from_name(name))
        return out
