"""the library as music apps see it: Library v2 rows in subsonic shapes, for
one profile.

what a profile sees: tracks with a live file in that profile's library
(core.library2.sql_util, read through the ambient library scope the
blueprint sets), less explicit ones when the profile hides them. albums
are the ones with at least one such track, artists the album artists of
those albums.

speed: one pass over the albums (the snapshot) answers the artist index,
genres, every album list sorted by name, year, artist or date added, the
random picks and the whole-library paging apps use to sync. it is cached
per library scope and explicit rule, and rebuilt when a file changes
(song_search's files_version counter) or after SNAPSHOT_TTL. single rows
(an album, a song, a page of songs) are primary key and index lookups.

ids are typed so one id field can name anything: ``ar-<artist>``,
``al-<album>``, ``tr-<track>``, ``pl-<playlist>``, ``mx-<n>`` (a daily
mix). a bare number is a track, for apps that strip prefixes.
"""

from __future__ import annotations

import bisect
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from core.subsonic import response as R

ARTIST, ALBUM, TRACK, PLAYLIST, MIX = "ar", "al", "tr", "pl", "mx"
IGNORED_ARTICLES = ("The", "El", "La", "Los", "Las", "Le", "Les", "A", "An")
SNAPSHOT_TTL = 15 * 60
_SNAPSHOT_KEEP = 6

AUDIO_MIME = {
    "mp3": "audio/mpeg", "flac": "audio/flac", "ogg": "audio/ogg", "oga": "audio/ogg",
    "opus": "audio/ogg", "aac": "audio/aac", "m4a": "audio/mp4", "mp4": "audio/mp4",
    "alac": "audio/mp4", "wav": "audio/wav", "aif": "audio/aiff", "aiff": "audio/aiff",
    "wma": "audio/x-ms-wma", "ape": "audio/x-ape", "wv": "audio/x-wavpack", "dsf": "audio/dsf",
    "webm": "audio/webm",
}


# ── ids ──────────────────────────────────────────────────────────────────

def sid(kind: str, n: Any) -> str:
    return "%s-%s" % (kind, n)


def parse_id(value: Any, *kinds: str) -> Tuple[str, int]:
    """("ar"|"al"|"tr"|..., number) for one of ``kinds``; a bare number is a
    track when tracks are allowed. raises not found for anything else."""
    text = str(value or "").strip()
    kind, _, rest = text.partition("-")
    if not rest and text.isdigit() and TRACK in kinds:
        kind, rest = TRACK, text
    if kind in kinds and rest.isdigit():
        return kind, int(rest)
    raise R.not_found()


# ── small helpers ────────────────────────────────────────────────────────

def _iso(value: Any) -> Optional[str]:
    """a stored timestamp as ISO 8601 UTC, or None."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:26].rstrip("Z"), fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            continue
    return None


def _genres(raw: Any) -> List[str]:
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            items = [raw] if isinstance(raw, str) and raw.strip() else []
    out = []
    for g in items if isinstance(items, list) else []:
        name = g.get("name") if isinstance(g, dict) else g
        name = str(name or "").strip()
        if name and name.lower() not in {x.lower() for x in out}:
            out.append(name)
    return out


def _kbps(bitrate: Any) -> Optional[int]:
    try:
        n = int(bitrate or 0)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n // 1000 if n > 10000 else n


def _secs(ms: Any) -> int:
    try:
        return int(round(int(ms or 0) / 1000.0))
    except (TypeError, ValueError):
        return 0


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def sort_key(name: str) -> str:
    """a name without a leading article, folded, for indexes and sorting."""
    text = (name or "").strip()
    low = text.lower()
    for art in IGNORED_ARTICLES:
        if low.startswith(art.lower() + " ") and len(text) > len(art) + 1:
            return low[len(art) + 1:].strip()
    return low


def index_letter(name: str) -> str:
    key = sort_key(name)
    ch = key[:1].upper()
    return ch if ch.isalpha() and ch.isascii() else "#"


def explicit_sql(expr: str) -> str:
    """``expr`` (a flag column or override value) counts as explicit, the same
    reading as core.content_filter.is_explicit."""
    return (f"(CASE WHEN {expr} IS NULL THEN 0 WHEN typeof({expr}) = 'text' "
            f"THEN LOWER(TRIM({expr})) IN ('1','true','yes','explicit') ELSE {expr} <> 0 END)")


def _override(entity: str, alias_id: str) -> str:
    return (f"(SELECT o.value_json FROM lib2_metadata_overrides o WHERE o.entity_type = '{entity}' "
            f"AND o.entity_id = {alias_id} AND o.field_name = 'explicit')")


def track_explicit_sql(t: str = "t", al: str = "al", overrides: bool = True) -> str:
    """the track (or its release) is explicit, after the user's overrides."""
    if not overrides:
        return f"({explicit_sql(f'{t}.explicit')} OR {explicit_sql(f'{al}.explicit')})"
    tv = f"COALESCE({_override('track', f'{t}.id')}, {t}.explicit)"
    av = f"COALESCE({_override('release_group', f'{al}.id')}, {al}.explicit)"
    return f"({explicit_sql(tv)} OR {explicit_sql(av)})"


def album_explicit_sql(al: str = "al", overrides: bool = True) -> str:
    if not overrides:
        return explicit_sql(f"{al}.explicit")
    return explicit_sql(f"COALESCE({_override('release_group', f'{al}.id')}, {al}.explicit)")


def _has_overrides(conn) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='lib2_metadata_overrides'").fetchone() is not None


def owned_track_sql(t: str = "t") -> str:
    """``t`` has a live file in the caller's library (ambient scope)."""
    from core.library2.sql_util import owned_sql
    return owned_sql("track", t)


# ── the snapshot ─────────────────────────────────────────────────────────

@dataclass
class Album:
    id: int
    title: str
    artist_id: Optional[int]
    artist: str
    year: Optional[int]
    genres: List[str]
    added: str
    songs: int
    duration_ms: int
    ft: str = ""        # the title and artist folded for search
    fa: str = ""


@dataclass
class Snapshot:
    built_at: float
    version: Any
    albums: List[Album]
    by_id: Dict[int, Album]
    artists: Dict[int, Dict[str, Any]]           # id -> {name, sort_name, albums}
    orders: Dict[str, List[Album]] = field(default_factory=dict)
    by_id_order: List[Album] = field(default_factory=list)
    cumulative: List[int] = field(default_factory=list)   # songs before each album in by_id_order
    lock: threading.Lock = field(default_factory=threading.Lock)

    def ordered(self, how: str) -> List[Album]:
        with self.lock:
            hit = self.orders.get(how)
            if hit is not None:
                return hit
            if how == "alphabeticalByName":
                out = sorted(self.albums, key=lambda a: (sort_key(a.title), a.id))
            elif how == "alphabeticalByArtist":
                out = sorted(self.albums, key=lambda a: (sort_key(a.artist), a.year or 0, sort_key(a.title), a.id))
            elif how == "newest":
                out = sorted(self.albums, key=lambda a: (a.added or "", a.id), reverse=True)
            elif how == "year":
                out = sorted(self.albums, key=lambda a: (a.year or 0, sort_key(a.title), a.id))
            else:
                out = list(self.by_id_order)
            self.orders[how] = out
            return out

    def genre_index(self) -> Dict[str, Dict[str, Any]]:
        """lowercased genre -> {name, albums: [Album], songs}"""
        with self.lock:
            hit = self.orders.get("__genres__")
            if hit is not None:
                return hit  # type: ignore[return-value]
        out: Dict[str, Dict[str, Any]] = {}
        for a in self.ordered("alphabeticalByName"):
            for g in a.genres:
                row = out.setdefault(g.lower(), {"name": g, "albums": [], "songs": 0})
                row["albums"].append(a)
                row["songs"] += a.songs
        with self.lock:
            self.orders["__genres__"] = out  # type: ignore[assignment]
        return out


_snapshots: Dict[Tuple, Snapshot] = {}
_snap_lock = threading.Lock()
_build_locks: Dict[Tuple, threading.Lock] = {}


def _files_version(conn) -> Any:
    from core.library2.song_search import song_count_version
    return song_count_version(conn)


def snapshot_sql(hide_explicit: bool, overrides: bool) -> str:
    """one row per visible album: the album, its artist and how many of its
    tracks the caller may play (and their length)."""
    hidden = f" AND NOT {track_explicit_sql('t', 'al', overrides)}" if hide_explicit else ""
    return (
        "SELECT * FROM (SELECT al.id, al.title, COALESCE(ar.canonical_artist_id, ar.id) AS artist_id, "
        "COALESCE(ca.name, ar.name) AS artist_name, COALESCE(ca.sort_name, ar.sort_name) AS artist_sort, "
        "al.year, al.genres, al.added_at, "
        "(SELECT json_array(COUNT(*), COALESCE(SUM(t.duration), 0)) FROM lib2_tracks t "
        f" WHERE t.album_id = al.id AND {owned_track_sql('t')}{hidden}) AS stats "
        "FROM lib2_albums al LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
        "LEFT JOIN lib2_artists ca ON ca.id = ar.canonical_artist_id"
        + (f" WHERE NOT {album_explicit_sql('al', overrides)}" if hide_explicit else "")
        + ") WHERE json_extract(stats, '$[0]') > 0")


def build_snapshot(conn, hide_explicit: bool) -> Snapshot:
    from core.library2.song_search import fold
    rows = conn.execute(snapshot_sql(hide_explicit, _has_overrides(conn))).fetchall()
    albums: List[Album] = []
    artists: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        try:
            n, dur = json.loads(r["stats"])
        except (TypeError, ValueError):
            continue
        a = Album(id=int(r["id"]), title=r["title"] or "", artist_id=_int(r["artist_id"]),
                  artist=r["artist_name"] or "", year=_int(r["year"]), genres=_genres(r["genres"]),
                  added=str(r["added_at"] or ""), songs=int(n or 0), duration_ms=int(dur or 0))
        a.ft, a.fa = fold(a.title), fold(a.artist)
        albums.append(a)
        if a.artist_id is not None:
            row = artists.setdefault(a.artist_id, {"name": a.artist, "sort_name": r["artist_sort"], "albums": 0,
                                                   "folded": a.fa})
            row["albums"] += 1
    by_id_order = sorted(albums, key=lambda a: a.id)
    cumulative, total = [], 0
    for a in by_id_order:
        cumulative.append(total)
        total += a.songs
    return Snapshot(built_at=time.monotonic(), version=None, albums=albums, by_id={a.id: a for a in albums},
                    artists=artists, by_id_order=by_id_order, cumulative=cumulative)


def snapshot(conn, *, db_key: str, scope: Any, hide_explicit: bool, now: Optional[float] = None) -> Snapshot:
    """the cached snapshot for this library scope and explicit rule, built
    once per change (single flight: a second caller waits for the first)."""
    key = (db_key, str(scope), bool(hide_explicit))
    version = _files_version(conn)
    mono = time.monotonic() if now is None else now
    with _snap_lock:
        snap = _snapshots.get(key)
        lock = _build_locks.setdefault(key, threading.Lock())
    if snap and snap.version == version and mono - snap.built_at < SNAPSHOT_TTL:
        return snap
    with lock:
        with _snap_lock:
            snap = _snapshots.get(key)
        if snap and snap.version == version and mono - snap.built_at < SNAPSHOT_TTL:
            return snap
        snap = build_snapshot(conn, hide_explicit)
        snap.version = version
        snap.built_at = mono
        with _snap_lock:
            _snapshots[key] = snap
            while len(_snapshots) > _SNAPSHOT_KEEP:
                _snapshots.pop(next(iter(_snapshots)))
        return snap


def forget_snapshots() -> None:
    with _snap_lock:
        _snapshots.clear()


# ── rows ─────────────────────────────────────────────────────────────────

class Library:
    """one request's view: a connection, whose library, what it hides, and
    what this profile starred and rated."""

    def __init__(self, conn, *, db_key: str, scope: Any, hide_explicit: bool,
                 starred: Optional[Dict[Tuple[str, int], str]] = None,
                 ratings: Optional[Dict[Tuple[str, int], int]] = None,
                 bridge: Optional[Any] = None):
        self.conn = conn
        self.db_key = db_key
        self.scope = scope
        self.hide_explicit = bool(hide_explicit)
        self.starred = starred or {}
        self.ratings = ratings or {}
        # bridge(source, path) -> marker or None, for songs on a media server
        self.bridge = bridge
        self._overrides = _has_overrides(conn)

    # the snapshot
    def snap(self) -> Snapshot:
        return snapshot(self.conn, db_key=self.db_key, scope=self.scope, hide_explicit=self.hide_explicit)

    def _hidden(self, t="t", al="al") -> str:
        return f" AND NOT {track_explicit_sql(t, al, self._overrides)}" if self.hide_explicit else ""

    # ── songs ──
    def song_rows(self, track_ids: Sequence[int]) -> List[Dict[str, Any]]:
        """raw rows for these tracks, in this order, only the ones this
        profile may play."""
        ids = [int(i) for i in track_ids if i is not None]
        if not ids:
            return []
        from core.library2.sql_util import scoped_primary_file_join
        rows = self.conn.execute(
            f"""SELECT t.id, t.title, t.track_number, t.disc_number, t.duration, t.play_count, t.last_played,
                       t.track_artist, t.bpm, t.isrc, t.musicbrainz_id, t.added_at, t.album_id,
                       al.title AS album_title, al.year, al.genres, al.album_type,
                       COALESCE(ar.canonical_artist_id, ar.id) AS artist_id, ar.name AS artist_name,
                       f.id AS file_id, f.path, f.bitrate, f.sample_rate, f.bit_depth, f.size,
                       f.server_source AS file_source,
                       {track_explicit_sql('t', 'al', self._overrides)} AS is_explicit
                  FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
                  LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                  LEFT JOIN lib2_track_files f ON {scoped_primary_file_join('t', 'f')}
                 WHERE t.id IN (SELECT value FROM json_each(:ids)) AND {owned_track_sql('t')}{self._hidden()}""",
            {"ids": json.dumps(ids)}).fetchall()
        by = {int(r["id"]): dict(r) for r in rows}
        seen, out = set(), []
        for i in ids:
            if i in by and i not in seen:
                seen.add(i)
                out.append(by[i])
        return out

    def songs(self, track_ids: Sequence[int]) -> List[Dict[str, Any]]:
        return [self.child(r) for r in self.song_rows(track_ids)]

    def song(self, track_id: int) -> Dict[str, Any]:
        rows = self.song_rows([track_id])
        if not rows:
            raise R.not_found("Song")
        return self.child(rows[0])

    def child(self, r: Dict[str, Any]) -> Dict[str, Any]:
        path = r.get("path") or ""
        suffix = os.path.splitext(path)[1].lstrip(".").lower()
        genres = _genres(r.get("genres"))
        artist = r.get("track_artist") or r.get("artist_name") or ""
        tid = int(r["id"])
        out = {
            "id": sid(TRACK, tid), "parent": sid(ALBUM, r["album_id"]), "isDir": False,
            "title": r.get("title") or "", "album": r.get("album_title") or "", "artist": artist,
            "track": _int(r.get("track_number")), "discNumber": _int(r.get("disc_number")),
            "year": _int(r.get("year")), "genre": genres[0] if genres else None,
            "coverArt": sid(ALBUM, r["album_id"]), "size": _int(r.get("size")),
            "contentType": AUDIO_MIME.get(suffix, "audio/mpeg"), "suffix": suffix or None,
            "duration": _secs(r.get("duration")), "bitRate": _kbps(r.get("bitrate")),
            "samplingRate": _int(r.get("sample_rate")), "bitDepth": _int(r.get("bit_depth")),
            # a path inside the library, never where the file really lives
            "path": "%s/%s/%s" % (_safe(r.get("artist_name")), _safe(r.get("album_title")),
                                  os.path.basename(path) if path else _safe(r.get("title"))),
            "playCount": int(r.get("play_count") or 0), "played": _iso(r.get("last_played")),
            "created": _iso(r.get("added_at")), "albumId": sid(ALBUM, r["album_id"]),
            "artistId": sid(ARTIST, r["artist_id"]) if r.get("artist_id") is not None else None,
            "type": "music", "mediaType": "song", "isVideo": False,
            "bpm": _int(r.get("bpm")), "musicBrainzId": r.get("musicbrainz_id") or None,
            "isrc": [r["isrc"]] if r.get("isrc") else None,
            "genres": [{"name": g} for g in genres],
            "displayArtist": artist,
            "artists": ([{"id": sid(ARTIST, r["artist_id"]), "name": r.get("artist_name") or ""}]
                        if r.get("artist_id") is not None and not r.get("track_artist") else None),
            "explicitStatus": "explicit" if r.get("is_explicit") else "",
            "starred": self.starred.get((TRACK, tid)),
            "userRating": self.ratings.get((TRACK, tid)),
        }
        if self.bridge is not None:
            marker = self.bridge(r.get("file_source"), path)
            if marker:
                out["soulsyncStreamVia"] = marker
        return out

    # ── albums ──
    def album_rows(self, album_ids: Sequence[int]) -> List[Dict[str, Any]]:
        ids = [int(i) for i in album_ids]
        if not ids:
            return []
        rows = self.conn.execute(
            f"""SELECT al.id, al.title, al.year, al.genres, al.album_type, al.label, al.musicbrainz_id,
                       al.added_at, COALESCE(ar.canonical_artist_id, ar.id) AS artist_id,
                       COALESCE(ca.name, ar.name) AS artist_name,
                       {album_explicit_sql('al', self._overrides)} AS is_explicit,
                       (SELECT json_array(COUNT(*), COALESCE(SUM(t.duration), 0), COALESCE(SUM(t.play_count), 0),
                                          MAX(t.last_played))
                          FROM lib2_tracks t WHERE t.album_id = al.id AND {owned_track_sql('t')}{self._hidden()}) AS stats
                  FROM lib2_albums al LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                  LEFT JOIN lib2_artists ca ON ca.id = ar.canonical_artist_id
                 WHERE al.id IN (SELECT value FROM json_each(:ids))""",
            {"ids": json.dumps(ids)}).fetchall()
        by = {}
        for r in rows:
            d = dict(r)
            try:
                d["songs"], d["duration_ms"], d["plays"], d["last_played"] = json.loads(d.pop("stats"))
            except (TypeError, ValueError):
                continue
            if not d["songs"] or (self.hide_explicit and d["is_explicit"]):
                continue
            by[int(d["id"])] = d
        return [by[i] for i in ids if i in by]

    def album_id3(self, d: Dict[str, Any]) -> Dict[str, Any]:
        aid = int(d["id"])
        genres = _genres(d.get("genres"))
        kind = (d.get("album_type") or "album").lower()
        return {
            "id": sid(ALBUM, aid), "name": d.get("title") or "", "title": d.get("title") or "",
            "album": d.get("title") or "",
            "artist": d.get("artist_name") or "", "displayArtist": d.get("artist_name") or "",
            "artistId": sid(ARTIST, d["artist_id"]) if d.get("artist_id") is not None else None,
            "artists": ([{"id": sid(ARTIST, d["artist_id"]), "name": d.get("artist_name") or ""}]
                        if d.get("artist_id") is not None else None),
            "coverArt": sid(ALBUM, aid), "songCount": int(d.get("songs") or 0),
            "duration": _secs(d.get("duration_ms")), "playCount": int(d.get("plays") or 0),
            "played": _iso(d.get("last_played")), "created": _iso(d.get("added_at")) or "1970-01-01T00:00:00.000Z",
            "year": _int(d.get("year")), "genre": genres[0] if genres else None,
            "genres": [{"name": g} for g in genres], "musicBrainzId": d.get("musicbrainz_id") or None,
            "releaseTypes": [kind.capitalize()], "isCompilation": kind == "compilation",
            "recordLabels": [{"name": d["label"]}] if d.get("label") else None,
            "explicitStatus": "explicit" if d.get("is_explicit") else "",
            "starred": self.starred.get((ALBUM, aid)), "userRating": self.ratings.get((ALBUM, aid)),
            "sortName": sort_key(d.get("title") or ""),
        }

    def album_dir(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """an album as a directory entry (getAlbumList, search2, an artist's
        directory)."""
        a = self.album_id3(d)
        return {"id": a["id"], "parent": a["artistId"], "isDir": True, "title": a["name"],
                "name": a["name"], "album": a["name"], "artist": a["artist"], "year": a["year"],
                "genre": a["genre"], "coverArt": a["coverArt"], "duration": a["duration"],
                "playCount": a["playCount"], "created": a["created"], "starred": a["starred"],
                "userRating": a["userRating"], "albumId": a["id"], "artistId": a["artistId"],
                "songCount": a["songCount"], "isVideo": False, "mediaType": "album"}

    def albums(self, album_ids: Sequence[int], *, as_dir: bool = False) -> List[Dict[str, Any]]:
        rows = self.album_rows(album_ids)
        return [self.album_dir(r) if as_dir else self.album_id3(r) for r in rows]

    def album(self, album_id: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        rows = self.album_rows([album_id])
        if not rows:
            raise R.not_found("Album")
        ids = [r[0] for r in self.conn.execute(
            f"SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
            f"WHERE t.album_id = ? AND {owned_track_sql('t')}{self._hidden()} "
            "ORDER BY COALESCE(t.disc_number, 1), COALESCE(t.track_number, 0), t.id", (int(album_id),))]
        return rows[0], self.songs(ids)

    # ── artists ──
    def artist_alias_ids(self, artist_id: int) -> List[int]:
        return [int(r[0]) for r in self.conn.execute(
            "SELECT id FROM lib2_artists WHERE id = ? OR canonical_artist_id = ?",
            (int(artist_id), int(artist_id)))]

    def artist_row(self, artist_id: int) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT id, name, sort_name, musicbrainz_id, image_url, genres, enrichment, spotify_id, "
            "external_ids FROM lib2_artists WHERE id = ?", (int(artist_id),)).fetchone()
        if not row:
            raise R.not_found("Artist")
        return dict(row)

    def artist_album_ids(self, artist_id: int) -> List[int]:
        snap = self.snap()
        if int(artist_id) not in snap.artists:
            raise R.not_found("Artist")
        return [a.id for a in sorted((a for a in snap.albums if a.artist_id == int(artist_id)),
                                     key=lambda a: (a.year or 0, sort_key(a.title)))]

    def artist_id3(self, artist_id: int, info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        snap = self.snap()
        meta = snap.artists.get(int(artist_id)) or {}
        name = (info or {}).get("name") or meta.get("name") or ""
        return {"id": sid(ARTIST, artist_id), "name": name, "coverArt": sid(ARTIST, artist_id),
                "albumCount": int(meta.get("albums") or 0), "starred": self.starred.get((ARTIST, int(artist_id))),
                "userRating": self.ratings.get((ARTIST, int(artist_id))),
                "musicBrainzId": (info or {}).get("musicbrainz_id") or None,
                "sortName": sort_key(meta.get("sort_name") or name), "roles": ["albumartist"]}

    def artist_index(self) -> List[Dict[str, Any]]:
        """[{name: letter, artist: [ArtistID3]}] for getArtists."""
        snap = self.snap()
        groups: Dict[str, List[Tuple[str, int, Dict[str, Any]]]] = {}
        for aid, meta in snap.artists.items():
            name = meta.get("name") or ""
            key = sort_key(meta.get("sort_name") or name)
            groups.setdefault(index_letter(meta.get("sort_name") or name), []).append((key, aid, meta))
        out = []
        for letter in sorted(groups, key=lambda x: (x == "#", x)):
            items = sorted(groups[letter], key=lambda x: (x[0], x[1]))
            out.append({"name": letter, "artist": [{
                "id": sid(ARTIST, aid), "name": meta.get("name") or "", "coverArt": sid(ARTIST, aid),
                "albumCount": int(meta.get("albums") or 0), "starred": self.starred.get((ARTIST, aid)),
                "userRating": self.ratings.get((ARTIST, aid)), "sortName": key} for key, aid, meta in items]})
        return out

    # ── lists ──
    def album_list(self, kind: str, *, size: int, offset: int, from_year=None, to_year=None,
                   genre: str = "", ranked: Optional[List[int]] = None) -> List[int]:
        """album ids for getAlbumList(2). ``ranked`` is an outside order
        (frequent, recent, starred, highest) to filter to visible albums."""
        snap = self.snap()
        if ranked is not None:
            return [i for i in ranked if i in snap.by_id][offset:offset + size]
        if kind == "random":
            pool = snap.albums
            return [a.id for a in random.sample(pool, min(size, len(pool)))]
        if kind == "byGenre":
            row = snap.genre_index().get((genre or "").strip().lower())
            return [a.id for a in (row["albums"] if row else [])][offset:offset + size]
        if kind == "byYear":
            lo, hi = _int(from_year) or 0, _int(to_year) or 9999
            asc = lo <= hi
            a, b = (lo, hi) if asc else (hi, lo)
            items = [x for x in snap.ordered("year") if x.year is not None and a <= x.year <= b]
            if not asc:
                items = list(reversed(items))
            return [x.id for x in items][offset:offset + size]
        order = {"newest": "newest", "alphabeticalByName": "alphabeticalByName",
                 "alphabeticalByArtist": "alphabeticalByArtist"}.get(kind)
        if order is None:
            raise R.SubsonicError(R.GENERIC, "Unknown list type: %s" % kind)
        return [a.id for a in snap.ordered(order)[offset:offset + size]]

    def songs_in_albums(self, album_ids: Sequence[int], *, limit: int, skip: int = 0) -> List[int]:
        """track ids of these albums in album order, after skipping ``skip``."""
        if not album_ids:
            return []
        rows = self.conn.execute(
            f"SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
            f"WHERE t.album_id IN (SELECT value FROM json_each(:ids)) AND {owned_track_sql('t')}{self._hidden()} "
            "ORDER BY t.album_id, COALESCE(t.disc_number, 1), COALESCE(t.track_number, 0), t.id "
            "LIMIT :limit OFFSET :skip",
            {"ids": json.dumps([int(i) for i in album_ids]), "limit": int(limit), "skip": int(skip)}).fetchall()
        return [int(r[0]) for r in rows]

    def page_of_songs(self, albums: List[Album], cumulative: List[int], offset: int, count: int) -> List[int]:
        """``count`` songs from ``offset`` through these albums (in this
        order), walking only the albums that page touches."""
        if count <= 0 or not albums:
            return []
        start = max(0, bisect.bisect_right(cumulative, offset) - 1)
        skip = offset - cumulative[start]
        picked, need, i = [], count + skip, start
        while i < len(albums) and need > 0:
            picked.append(albums[i].id)
            need -= albums[i].songs
            i += 1
        return self.songs_in_albums(sorted(picked), limit=count, skip=skip) if picked == sorted(picked) else \
            self._ordered_album_songs(picked, count, skip)

    def _ordered_album_songs(self, album_ids: List[int], count: int, skip: int) -> List[int]:
        out: List[int] = []
        for aid in album_ids:
            out += self.songs_in_albums([aid], limit=100000)
        return out[skip:skip + count]

    def all_songs_page(self, offset: int, count: int) -> List[int]:
        """the whole library in a stable order, for apps that sync it all
        through an empty search."""
        snap = self.snap()
        return self.page_of_songs(snap.by_id_order, snap.cumulative, offset, count)

    def genre_songs(self, genre: str, offset: int, count: int) -> List[int]:
        row = self.snap().genre_index().get((genre or "").strip().lower())
        if not row:
            return []
        albums = row["albums"]
        cum, total = [], 0
        for a in albums:
            cum.append(total)
            total += a.songs
        return self.page_of_songs(albums, cum, offset, count)

    def random_songs(self, size: int, *, genre: str = "", from_year=None, to_year=None) -> List[int]:
        snap = self.snap()
        pool: Iterable[Album] = snap.albums
        if genre:
            row = snap.genre_index().get(genre.strip().lower())
            pool = row["albums"] if row else []
        lo, hi = _int(from_year), _int(to_year)
        if lo is not None or hi is not None:
            pool = [a for a in pool if a.year is not None and (lo is None or a.year >= lo) and (hi is None or a.year <= hi)]
        pool = list(pool)
        if not pool:
            return []
        weights = [max(1, a.songs) for a in pool]
        chosen = random.choices(pool, weights=weights, k=min(size * 2, 1000))
        album_ids = sorted({a.id for a in chosen})
        rows = self.conn.execute(
            f"SELECT t.id, t.album_id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
            f"WHERE t.album_id IN (SELECT value FROM json_each(:ids)) AND {owned_track_sql('t')}{self._hidden()}",
            {"ids": json.dumps(album_ids)}).fetchall()
        by_album: Dict[int, List[int]] = {}
        for tid, aid in rows:
            by_album.setdefault(int(aid), []).append(int(tid))
        out: List[int] = []
        seen = set()
        for a in chosen:
            tracks = by_album.get(a.id) or []
            if not tracks:
                continue
            t = random.choice(tracks)
            if t not in seen:
                seen.add(t)
                out.append(t)
            if len(out) >= size:
                break
        return out

    def genres(self) -> List[Dict[str, Any]]:
        idx = self.snap().genre_index()
        return [{"value": row["name"], "songCount": row["songs"], "albumCount": len(row["albums"])}
                for _, row in sorted(idx.items())]

    # ── search ──
    def search(self, query: str, *, artist_count: int, artist_offset: int, album_count: int,
               album_offset: int, song_count: int, song_offset: int) -> Tuple[List[int], List[int], List[int]]:
        """(artist ids, album ids, track ids). an empty query is the whole
        library, paged, the way apps sync it."""
        snap = self.snap()
        q = (query or "").strip().strip('"').strip()
        if q in ("", "*"):
            artists = sorted(snap.artists, key=lambda a: (sort_key(snap.artists[a].get("sort_name")
                                                                    or snap.artists[a]["name"]), a))
            return (artists[artist_offset:artist_offset + artist_count],
                    [a.id for a in snap.by_id_order[album_offset:album_offset + album_count]],
                    self.all_songs_page(song_offset, song_count))
        from core.library2.song_search import fold
        fq = fold(q.rstrip("*"))
        artists = [aid for aid, meta in snap.artists.items() if fq in meta["folded"]]
        artists.sort(key=lambda a: (not snap.artists[a]["folded"].startswith(fq),
                                    sort_key(snap.artists[a]["name"]), a))
        albums = [a for a in snap.albums if fq in a.ft or fq in a.fa]
        albums.sort(key=lambda a: (fq not in a.ft, not a.ft.startswith(fq), sort_key(a.title), a.id))
        songs = self.search_songs(fq, song_count, song_offset) if song_count > 0 else []
        return (artists[artist_offset:artist_offset + artist_count],
                [a.id for a in albums[album_offset:album_offset + album_count]], songs)

    def search_songs(self, fq: str, count: int, offset: int) -> List[int]:
        from core.library2.song_search import matching_ids
        hits = matching_ids(self.conn, fq)
        params: Dict[str, Any] = {"limit": int(count), "offset": int(offset)}
        if hits is not None:
            params["hits"] = json.dumps(hits)
            where = "t.id IN (SELECT value FROM json_each(:hits))"
        else:
            from core.library2.queries import _fold_sql
            from core.text.normalize import normalize_for_comparison
            self.conn.create_function("norm_text", 1, lambda x: normalize_for_comparison(x) if x else "",
                                      deterministic=True)
            params["like"] = "%" + fq.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where = (f"({_fold_sql('t.title')} LIKE :like ESCAPE '\\' OR {_fold_sql('t.track_artist')} "
                     f"LIKE :like ESCAPE '\\' OR {_fold_sql('ar.name')} LIKE :like ESCAPE '\\')")
        rows = self.conn.execute(
            f"SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
            f"LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
            f"WHERE {where} AND {owned_track_sql('t')}{self._hidden()} "
            "ORDER BY LOWER(t.title), t.id LIMIT :limit OFFSET :offset", params).fetchall()
        return [int(r[0]) for r in rows]

    # ── finding a library track for a name ──
    def find_track(self, title: str, artist: str, album: str = "") -> Optional[int]:
        """the owned track with this title by this artist (a playlist row, a
        mix track, a top song), or None. one indexed lookup per title."""
        if not title:
            return None
        return self.find_tracks([(title, artist, album)])[0]

    def find_tracks(self, wanted: Sequence[Tuple[str, str, str]]) -> List[Optional[int]]:
        """find_track for many (title, artist, album) at once: a few batched
        lookups instead of one per song (a 1,500-song playlist took seconds)"""
        titles = sorted({_sql_lower((t or "").strip()) for t, _, _ in wanted if (t or "").strip()})
        by_title: Dict[str, List[Any]] = {}
        for i in range(0, len(titles), 400):
            chunk = titles[i:i + 400]
            marks = ",".join("?" * len(chunk))
            for r in self.conn.execute(
                    f"SELECT t.id, t.title, COALESCE(t.track_artist, ar.name) AS artist, ar.name AS album_artist, "
                    f"al.title AS album FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
                    f"LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id "
                    f"WHERE LOWER(t.title) IN ({marks}) AND {owned_track_sql('t')}{self._hidden()}",
                    chunk).fetchall():
                by_title.setdefault(_sql_lower(r["title"] or ""), []).append(r)
        return [self._pick(by_title.get(_sql_lower((t or "").strip()), [])[:50], t, a, al) if (t or "").strip()
                else None for t, a, al in wanted]

    @staticmethod
    def _pick(rows: Sequence[Any], title: str, artist: str, album: str) -> Optional[int]:
        from core.playlists.user_playlists import track_key
        want = track_key({"track_name": title, "artist_name": artist})
        best = None
        for r in rows:
            keys = {track_key({"track_name": r["title"], "artist_name": r["artist"] or ""}),
                    track_key({"track_name": r["title"], "artist_name": r["album_artist"] or ""})}
            if want in keys or not artist:
                if album and (r["album"] or "").strip().lower() == album.strip().lower():
                    return int(r["id"])
                best = best or int(r["id"])
        return best


def _stamp(row: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
    extra = row.get("extra_data")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (TypeError, ValueError):
            extra = {}
    extra = extra if isinstance(extra, dict) else {}
    try:
        tid = int(extra.get("library_track_id") or 0) or None
    except (TypeError, ValueError):
        tid = None
    return tid, extra.get("library_track_id_kind")


def stamped_tracks(conn, rows: Sequence[Dict[str, Any]]) -> List[Optional[int]]:
    """the library track a playlist row was stamped with, when it can be
    trusted. marked lib2 (a manual match): yes. marked anything else (a
    media server's id): no. unmarked: only when that lib2 track has the
    row's title, since old stamps carry other id spaces with no mark (a
    playlist played the wrong song, or nothing, off them)."""
    out: List[Optional[int]] = []
    check: Dict[int, List[int]] = {}
    for n, r in enumerate(rows):
        tid, kind = _stamp(r)
        if tid and kind == "lib2":
            out.append(tid)
            continue
        out.append(None)
        if tid and not kind:
            check.setdefault(tid, []).append(n)
    ids = list(check)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for tid, title in conn.execute(f"SELECT id, title FROM lib2_tracks WHERE id IN ({marks})", chunk).fetchall():
            for n in check.get(int(tid), []):
                want = _sql_lower((rows[n].get("track_name") or "").strip())
                if want and _sql_lower((title or "").strip()) == want:
                    out[n] = int(tid)
    return out


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _sql_lower(text: str) -> str:
    """sqlite's LOWER(): a to z only. a batch matches what one-by-one did,
    accented titles included"""
    return text.translate(_ASCII_LOWER)


def _safe(value: Any) -> str:
    return str(value or "Unknown").replace("/", "_").strip() or "Unknown"


def _reset_for_tests() -> None:
    forget_snapshots()


__all__ = ["stamped_tracks", "ARTIST", "ALBUM", "TRACK", "PLAYLIST", "MIX", "IGNORED_ARTICLES", "AUDIO_MIME", "sid", "parse_id",
           "sort_key", "index_letter", "explicit_sql", "track_explicit_sql", "album_explicit_sql",
           "snapshot_sql", "build_snapshot", "snapshot", "forget_snapshots", "Library", "Album", "Snapshot"]
