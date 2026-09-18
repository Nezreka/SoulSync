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

The DB and file rungs are gated (#903 follow-up): ``tracks.musicbrainz_recording_id`` is
populated verbatim from file tags at import (``core/imports/side_effects.py``), so a
mis-tagged file poisons both rungs identically, and neither used to be cross-checked
against the track's actual title. Before accepting either rung's MBID, ``build_resolve_fn``
now (1) rejects it if the ``mbid_mismatch_detector`` repair job already has a pending
finding against that track/file, and (2) as a cheap second line of defence, verifies its
MusicBrainz title against the track's title (same check the repair job uses). Either
failing makes the rung a miss, falling through the waterfall instead of exporting the
wrong recording.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from utils.logging_config import get_logger

from core.exports.mbid_resolver import (
    SRC_CACHE,
    SRC_DB,
    SRC_FILE,
    SRC_MUSICBRAINZ,
    normalize_key,
    resolve_recording_mbid,
)
from core.metadata.title_match import title_matches

logger = get_logger("exports.export_sources")


def _db_match(artist: str, title: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Text-match a library track by (artist, title); return (recording_mbid, file_path,
    track_id). Any may be None. Fail-safe — any DB error returns (None, None, None).
    ``track_id`` is the ``tracks.id`` of the matched row, used to cross-check the finding
    the mbid_mismatch repair job (#903 follow-up) may already have on file for it."""
    if not title:
        return (None, None, None)
    try:
        from database.music_database import get_database
        db = get_database()
        conn = db._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT t.musicbrainz_recording_id, t.file_path, t.id "
                "FROM tracks t JOIN artists a ON t.artist_id = a.id "
                "WHERE LOWER(t.title) = LOWER(?) AND LOWER(a.name) = LOWER(?) "
                "LIMIT 1",
                (title, artist),
            )
            row = cur.fetchone()
            if not row:
                return (None, None, None)
            mbid = row[0] if not hasattr(row, "keys") else row["musicbrainz_recording_id"]
            fpath = row[1] if not hasattr(row, "keys") else row["file_path"]
            track_id = row[2] if not hasattr(row, "keys") else row["id"]
            return ((mbid or None), (fpath or None), (track_id if track_id is not None else None))
        finally:
            try:
                conn.close()
            except Exception:  # noqa: S110
                pass
    except Exception as exc:
        logger.debug(f"export db_match failed for '{artist} - {title}': {exc}")
        return (None, None, None)


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


def service_id_from_extra_data(track: Any, service: str) -> Optional[str]:
    """The target-service track ID the DISCOVERY step already resolved for this mirrored
    track, read from its ``extra_data`` blob (#945 — Boulder: "all 50 are discovered to
    Deezer already, it's not using any of that"). This is free (no API call) and reliable
    (it's the same id used to mirror the track).

    Only trusted when the track was discovered ON the export's target service — a
    Deezer-discovered track carries a Deezer id under ``matched_data['id']``, and its
    ``provider`` is the service name. A ``wing_it_fallback`` provider (the low-confidence
    guess path) deliberately does NOT match here, so those fall through to the library/
    none path rather than risk a wrong track in the exported playlist."""
    raw = track.get("extra_data") if isinstance(track, dict) else None
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("discovered"):
        return None
    if str(data.get("provider") or "").lower() != str(service or "").lower():
        return None
    matched = data.get("matched_data")
    tid = matched.get("id") if isinstance(matched, dict) else None
    return str(tid) if tid else None


def _track_field(track: Dict[str, Any], *names: str) -> str:
    for n in names:
        v = track.get(n)
        if v:
            return str(v)
    return ""


def resolve_service_track_ids(
    tracks: List[Dict[str, Any]],
    service: str,
    *,
    db_fn: Optional[Callable[[str, str, str], Optional[str]]] = None,
    search_id_fn: Optional[Callable[[str, str], Optional[str]]] = None,
    on_progress: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Resolve a mirrored playlist's tracks to target-service track IDs for export.

    Waterfall per track: the discovery cache (``extra_data`` — free + already confidently
    matched) → the library track's stored service id → (only when ``search_id_fn`` is
    given, i.e. the opt-in backfill toggle) a confident live-search match. A track that
    clears none of these is reported unmatched (caller skips it — never a guessed/wrong
    id). Returns ``{"resolved": [{artist, title, album, service_track_id}], "stats":
    {...}}`` with ``from_cache`` / ``from_library`` / ``from_search`` / ``unmatched``
    tallies for the status display.
    """
    db_fn = db_fn or db_service_track_id
    total = len(tracks or [])
    resolved: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {
        "total": total, "resolved": 0, "unmatched": 0,
        "from_cache": 0, "from_library": 0, "from_search": 0,
    }
    for i, t in enumerate(tracks or []):
        if not isinstance(t, dict):
            t = {}
        artist = _track_field(t, "artist", "artist_name", "creator")
        title = _track_field(t, "title", "track_name", "name")
        album = _track_field(t, "album", "album_name", "release_name")

        tid = service_id_from_extra_data(t, service)
        if tid:
            stats["from_cache"] += 1
        else:
            tid = db_fn(artist, title, service)
            if tid:
                stats["from_library"] += 1
            elif search_id_fn is not None:
                tid = search_id_fn(artist, title)
                if tid:
                    stats["from_search"] += 1

        resolved.append({"artist": artist, "title": title, "album": album,
                         "service_track_id": tid or None})
        stats["resolved" if tid else "unmatched"] += 1
        if on_progress is not None:
            try:
                on_progress(i + 1, total, stats)
            except Exception:  # noqa: S110 — a progress error must never fail the export
                pass

    return {"resolved": resolved, "stats": stats}


# Confidence floor for backfill, on the score_track scale (~1.5 = exact title + exact
# artist, thanks to the 1.5x artist boost). A cover/karaoke (x0.05) or a wrong-artist hit
# (no boost, caps ~1.0) can't clear this — so backfill never adds a guessed/wrong version.
BACKFILL_MIN_SCORE = 1.2


def search_service_track_id(
    artist: str,
    title: str,
    *,
    search_fn: Callable[[str], List[Any]],
    min_score: float = BACKFILL_MIN_SCORE,
) -> Optional[str]:
    """Confident live-search match for export backfill (#945): search the target service
    for (artist, title), rerank by relevance, and return the top match's id ONLY if it
    clears the confidence floor. Below the floor → None: the track is left out of the
    export rather than risk a wrong/cover/karaoke version (the whole point of backfill is
    coverage WITHOUT the wrong-track risk). ``search_fn(query) -> List[Track]`` is injected
    so this is unit-testable without a live service."""
    if not title:
        return None
    from core.metadata.relevance import build_combined_search_query, filter_and_rerank
    query = build_combined_search_query(title, artist)
    try:
        candidates = list(search_fn(query) or [])
    except Exception as exc:
        logger.debug(f"export backfill search failed for '{artist} - {title}': {exc}")
        return None
    if not candidates:
        return None
    ranked = filter_and_rerank(
        candidates, expected_title=title, expected_artist=artist, min_score=min_score,
    )
    if not ranked:
        return None
    tid = getattr(ranked[0], "id", None)
    return str(tid) if tid else None


def file_recording_mbid(artist: str, title: str) -> Optional[str]:
    """Recording MBID read from the matched track's file tag (set on import post-processing)."""
    _mbid, fpath, _track_id = _db_match(artist, title)
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


def mbid_flagged_by_repair_finding(artist: str, title: str) -> bool:
    """Default ``mbid_flagged_fn`` for ``build_resolve_fn``: True when the
    ``mbid_mismatch_detector`` repair job (``core/repair_jobs/mbid_mismatch_detector.py``)
    already has a PENDING ``mbid_mismatch`` finding against the (artist, title)'s matched
    library track — by its DB row id, or by its file path. That job already proved (via a
    live MusicBrainz lookup + title comparison) that the file's/DB's embedded MBID points
    at the wrong recording, so the DB and file rungs here must not trust it either.
    Fail-safe: any DB error is treated as "not flagged" — this is a defensive extra check,
    never the reason an export breaks."""
    _mbid, file_path, track_id = _db_match(artist, title)
    if track_id is None and not file_path:
        return False
    try:
        from database.music_database import get_database
        db = get_database()
        conn = db._get_connection()
        try:
            cur = conn.cursor()
            if file_path:
                cur.execute(
                    "SELECT 1 FROM repair_findings WHERE finding_type = 'mbid_mismatch' "
                    "AND status = 'pending' AND "
                    "((entity_type = 'track' AND entity_id = ?) OR file_path = ?) LIMIT 1",
                    (str(track_id) if track_id is not None else None, file_path),
                )
            else:
                cur.execute(
                    "SELECT 1 FROM repair_findings WHERE finding_type = 'mbid_mismatch' "
                    "AND status = 'pending' AND entity_type = 'track' AND entity_id = ? LIMIT 1",
                    (str(track_id),),
                )
            return cur.fetchone() is not None
        finally:
            try:
                conn.close()
            except Exception:  # noqa: S110
                pass
    except Exception as exc:
        logger.debug(f"export mbid_flagged lookup failed for '{artist} - {title}': {exc}")
        return False


def verify_recording_title(mbid: str, title: str) -> bool:
    """Default ``verify_fn`` for ``build_resolve_fn``: a cheap live cross-check that a
    DB/file MBID an export is about to accept actually points at a recording named (close
    enough to) this track's title — using the SAME normaliser/threshold as the
    mbid_mismatch repair job (``core/metadata/title_match.title_matches``) so the two
    can't drift apart. No MusicBrainz client available -> nothing to verify against ->
    accept. Any exception (network, service init, ...) is left to the caller
    (``build_resolve_fn`` memoizes and fail-opens) rather than swallowed here."""
    svc = _get_mb_service()
    client = getattr(svc, "mb_client", None) if svc else None
    if not client:
        return True
    recording = client.get_recording(mbid, includes=["artist-credits"])
    if not recording:
        return False
    return title_matches(title, recording.get("title", ""))


def build_resolve_fn(
    *,
    db_fn: Callable[[str, str], Optional[str]] = db_recording_mbid,
    file_fn: Callable[[str, str], Optional[str]] = file_recording_mbid,
    mb_fn: Callable[[str, str], Optional[str]] = musicbrainz_recording_mbid,
    cache_lookup: Optional[Callable[[str], Optional[str]]] = None,
    cache_record: Optional[Callable[[str, str], bool]] = None,
    mbid_flagged_fn: Optional[Callable[[str, str], bool]] = None,
    verify_fn: Optional[Callable[[str, str], bool]] = None,
) -> Callable[[str, str], Tuple[Optional[str], Optional[str]]]:
    """Assemble the export ``resolve_fn(artist, title) -> (mbid, source_label)``.

    Runs cache -> DB -> file -> MusicBrainz, and writes a fresh (non-cache) hit back to the
    persistent cache. All sources are injectable so the wiring is unit-testable; defaults
    use the real cache module.

    Before a DB or file MBID is accepted, two extra gates run (#903 follow-up — a
    mis-tagged file poisons both the DB column and the file tag it was copied from at
    import, and the export waterfall used to trust either blindly):

    1. ``mbid_flagged_fn(artist, title) -> bool`` — True when the repair job already
       flagged this track's embedded MBID as wrong (a pending ``mbid_mismatch`` finding).
       Defaults to ``mbid_flagged_by_repair_finding`` (a DB read).
    2. ``verify_fn(mbid, title) -> bool`` — a live MusicBrainz title cross-check. Defaults
       to ``verify_recording_title``. On a verifier exception the MBID is ACCEPTED
       (fail-open — a network hiccup must not degrade an export), logged at debug.

    Either gate failing turns the rung into a miss, so the waterfall falls through (to
    file, then live MusicBrainz search) exactly as if that rung had returned nothing.
    Both gates are memoized per ``resolve_fn`` (i.e. per export run) so the same
    (artist, title) flag check and the same (mbid, title) verification are each done at
    most once, however many rungs/tracks would otherwise repeat them.
    """
    if cache_lookup is None or cache_record is None:
        from core.exports import recording_mbid_cache as _cache
        cache_lookup = cache_lookup or _cache.lookup
        cache_record = cache_record or _cache.record
    mbid_flagged_fn = mbid_flagged_fn or mbid_flagged_by_repair_finding
    verify_fn = verify_fn or verify_recording_title

    _flagged_memo: Dict[Tuple[str, str], bool] = {}
    _verify_memo: Dict[Tuple[str, str], bool] = {}

    def _flagged(artist: str, title: str) -> bool:
        key = (artist, title)
        if key not in _flagged_memo:
            try:
                _flagged_memo[key] = bool(mbid_flagged_fn(artist, title))
            except Exception as exc:  # a flagged-check error must never block the rung
                logger.debug(f"export mbid_flagged_fn raised for '{artist} - {title}': {exc}")
                _flagged_memo[key] = False
        return _flagged_memo[key]

    def _verified(mbid: str, title: str) -> bool:
        key = (mbid, title)
        if key not in _verify_memo:
            try:
                _verify_memo[key] = bool(verify_fn(mbid, title))
            except Exception as exc:
                logger.debug(f"export verify_fn raised for {mbid} / '{title}': {exc}")
                _verify_memo[key] = True  # fail-open — network trouble must not degrade exports
        return _verify_memo[key]

    def _gated(fn: Callable[[str, str], Optional[str]]) -> Callable[[str, str], Optional[str]]:
        """Wrap a DB/file rung so a flagged or MB-title-mismatched MBID is a miss."""
        def gated(a: str, t: str) -> Optional[str]:
            mbid = fn(a, t)
            if not mbid or _flagged(a, t) or not _verified(mbid, t):
                return None
            return mbid
        return gated

    db_fn_gated = _gated(db_fn)
    file_fn_gated = _gated(file_fn)

    def resolve_fn(artist: str, title: str) -> Tuple[Optional[str], Optional[str]]:
        sources = [
            (SRC_CACHE, lambda a, t: cache_lookup(normalize_key(a, t))),
            (SRC_DB, db_fn_gated),
            (SRC_FILE, file_fn_gated),
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
    "mbid_flagged_by_repair_finding",
    "verify_recording_title",
]
