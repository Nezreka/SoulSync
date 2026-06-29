"""Wire the real cheapest-first sources for the export MBID waterfall (#903).

``mbid_resolver`` is the pure waterfall; this module supplies the real I/O behind each
source and assembles the ``resolve_fn`` the export job uses:

1. **cache** — ``recording_mbid_cache`` (persistent (artist,title)->mbid).
2. **DB** — a text-matched library track's ``tracks.musicbrainz_recording_id``.
3. **file** — ``MUSICBRAINZ_RECORDING_ID`` tag of that track's file (when the DB row had
   no recording id but the file was tagged on import).
4. **MusicBrainz** — live ``match_recording(track, artist)`` (rate-limited tail).

Every source is wrapped so any failure (missing table, unreadable file, MB timeout) returns
None — the waterfall just falls through, the export never breaks. ``build_resolve_fn`` also
writes a fresh non-cache hit back to the cache so the next export of the same song is free.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional, Tuple

from utils.logging_config import get_logger

from core.exports.mbid_resolver import (
    SRC_CACHE,
    SRC_DB,
    SRC_FILE,
    SRC_MUSICBRAINZ,
    normalize_key,
    resolve_recording_mbid,
)

logger = get_logger("exports.export_sources")


def _db_match(artist: str, title: str) -> Tuple[Optional[str], Optional[str]]:
    """Text-match a library track by (artist, title); return (recording_mbid, file_path).
    Either may be None. Fail-safe — any DB error returns (None, None)."""
    if not title:
        return (None, None)
    try:
        from database.music_database import get_database
        db = get_database()
        conn = db._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT t.musicbrainz_recording_id, t.file_path "
                "FROM tracks t JOIN artists a ON t.artist_id = a.id "
                "WHERE LOWER(t.title) = LOWER(?) AND LOWER(a.name) = LOWER(?) "
                "LIMIT 1",
                (title, artist),
            )
            row = cur.fetchone()
            if not row:
                return (None, None)
            mbid = row[0] if not hasattr(row, "keys") else row["musicbrainz_recording_id"]
            fpath = row[1] if not hasattr(row, "keys") else row["file_path"]
            return ((mbid or None), (fpath or None))
        finally:
            try:
                conn.close()
            except Exception:  # noqa: S110
                pass
    except Exception as exc:
        logger.debug(f"export db_match failed for '{artist} - {title}': {exc}")
        return (None, None)


def db_recording_mbid(artist: str, title: str) -> Optional[str]:
    """Recording MBID stored on a matched library track (``musicbrainz_recording_id``)."""
    return _db_match(artist, title)[0]


# Service → the tracks-table column carrying that service's track ID (set by enrichment).
# Trusted constants — never user input — so safe to interpolate into the SELECT below.
_SERVICE_ID_COLUMNS = {"spotify": "spotify_track_id", "deezer": "deezer_id"}


def db_service_track_id(artist: str, title: str, service: str) -> Optional[str]:
    """The service track ID (``spotify_track_id`` / ``deezer_id``) stored on a matched
    library track — what lets a mirrored playlist be exported BACK to Spotify/Deezer
    without re-searching, since enrichment already pinned it (#945). Text-matches by
    (artist, title), same as the MBID resolver. Fail-safe: any miss/error returns None."""
    column = _SERVICE_ID_COLUMNS.get((service or "").lower())
    if not column or not title:
        return None
    try:
        from database.music_database import get_database
        db = get_database()
        conn = db._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT t.{column} FROM tracks t JOIN artists a ON t.artist_id = a.id "
                "WHERE LOWER(t.title) = LOWER(?) AND LOWER(a.name) = LOWER(?) LIMIT 1",
                (title, artist),
            )
            row = cur.fetchone()
            if not row:
                return None
            val = row[0] if not hasattr(row, "keys") else row[column]
            return val or None
        finally:
            try:
                conn.close()
            except Exception:  # noqa: S110
                pass
    except Exception as exc:
        logger.debug(f"export service-id lookup failed for '{artist} - {title}' ({service}): {exc}")
        return None


def build_service_resolve_fn(service: str) -> Callable[[str, str], Tuple[Optional[str], Optional[str]]]:
    """resolve_fn for service-playlist export: ``(artist, title) -> (service_track_id, 'library')``.
    Plugs into ``resolve_playlist_tracks(..., id_key='service_track_id')`` exactly like the
    MBID resolver plugs in for ListenBrainz."""
    def resolve_fn(artist: str, title: str) -> Tuple[Optional[str], Optional[str]]:
        tid = db_service_track_id(artist, title, service)
        return (tid, "library" if tid else None)
    return resolve_fn


def file_recording_mbid(artist: str, title: str) -> Optional[str]:
    """Recording MBID read from the matched track's file tag (set on import post-processing)."""
    _mbid, fpath = _db_match(artist, title)
    if not fpath:
        return None
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(fpath)
        if audio is None or not getattr(audio, "tags", None):
            return None
        tags = audio.tags
        # ID3 UFID (MusicBrainz), Vorbis/MP4 musicbrainz_trackid, etc.
        for key in ("UFID:http://musicbrainz.org", "musicbrainz_trackid",
                    "MUSICBRAINZ_TRACKID", "----:com.apple.iTunes:MusicBrainz Track Id"):
            try:
                val = tags.get(key)
            except Exception:
                val = None
            if not val:
                continue
            if hasattr(val, "data"):       # ID3 UFID frame
                val = val.data.decode("utf-8", "ignore")
            if isinstance(val, (list, tuple)):
                val = val[0] if val else ""
            if isinstance(val, bytes):
                val = val.decode("utf-8", "ignore")
            val = str(val).strip()
            if val:
                return val
    except Exception as exc:
        logger.debug(f"export file_recording_mbid failed for {fpath}: {exc}")
    return None


_mb_service = None
_mb_service_lock = threading.Lock()


def _get_mb_service():
    """Shared MusicBrainzService (client + cache + DB), created lazily so importing this
    module never triggers a DB/network connection on paths that don't export."""
    global _mb_service
    if _mb_service is None:
        with _mb_service_lock:
            if _mb_service is None:
                from core.musicbrainz_service import MusicBrainzService
                from database.music_database import get_database
                _mb_service = MusicBrainzService(get_database())
    return _mb_service


def musicbrainz_recording_mbid(artist: str, title: str) -> Optional[str]:
    """Live MusicBrainz ``match_recording`` — the rate-limited tail."""
    if not title:
        return None
    try:
        svc = _get_mb_service()
        if not svc:
            return None
        result = svc.match_recording(title, artist)
        if result and result.get("mbid"):
            return result["mbid"]
    except Exception as exc:
        logger.debug(f"export musicbrainz_recording_mbid failed for '{artist} - {title}': {exc}")
    return None


def build_resolve_fn(
    *,
    db_fn: Callable[[str, str], Optional[str]] = db_recording_mbid,
    file_fn: Callable[[str, str], Optional[str]] = file_recording_mbid,
    mb_fn: Callable[[str, str], Optional[str]] = musicbrainz_recording_mbid,
    cache_lookup: Optional[Callable[[str], Optional[str]]] = None,
    cache_record: Optional[Callable[[str, str], bool]] = None,
) -> Callable[[str, str], Tuple[Optional[str], Optional[str]]]:
    """Assemble the export ``resolve_fn(artist, title) -> (mbid, source_label)``.

    Runs cache -> DB -> file -> MusicBrainz, and writes a fresh (non-cache) hit back to the
    persistent cache. All sources are injectable so the wiring is unit-testable; defaults
    use the real cache module.
    """
    if cache_lookup is None or cache_record is None:
        from core.exports import recording_mbid_cache as _cache
        cache_lookup = cache_lookup or _cache.lookup
        cache_record = cache_record or _cache.record

    def resolve_fn(artist: str, title: str) -> Tuple[Optional[str], Optional[str]]:
        sources = [
            (SRC_CACHE, lambda a, t: cache_lookup(normalize_key(a, t))),
            (SRC_DB, db_fn),
            (SRC_FILE, file_fn),
            (SRC_MUSICBRAINZ, mb_fn),
        ]
        mbid, label = resolve_recording_mbid(artist, title, sources)
        if mbid and label and label != SRC_CACHE:
            try:
                cache_record(normalize_key(artist, title), mbid)
            except Exception:  # noqa: S110 — cache write is best-effort
                pass
        return (mbid, label)

    return resolve_fn


__all__ = [
    "build_resolve_fn",
    "db_recording_mbid",
    "file_recording_mbid",
    "musicbrainz_recording_mbid",
]
