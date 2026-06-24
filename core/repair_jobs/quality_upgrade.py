"""Quality Upgrade Finder maintenance job.

Replaces the old auto-acting "Quality Scanner" tool. That tool decided quality
purely by file EXTENSION (so a 128 kbps MP3 and a 320 kbps MP3 looked identical),
ignored the bitrate-based quality profile, and silently dumped every match
straight into the wishlist with no review — which, on the default profile, meant
flagging an entire non-lossless library at once.

This job does it the way the rest of the app works: it SCANS (watchlist artists
or the whole library), judges each track against the user's quality profile using
BOTH format and bitrate, and for anything below the preferred quality it searches
the configured metadata source for a better version and emits a FINDING. Nothing
is queued until you review and Apply the finding — at which point the matched
track (carrying its album context) is added to the wishlist, exactly like every
other acquisition path.

The quality decision (``meets_preferred_quality``) is a pure function so it can be
unit-tested without a database or network. Transcode/"fake lossless" detection is
intentionally NOT done here — that's the separate Fake Lossless Detector job.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

from core.metadata.registry import get_client_for_source, get_primary_source, get_source_priority
from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
# Reuse the (tested) provider search + result-normalization helpers from the old
# scanner module so matching stays a single source of truth.
from core.discovery.quality_scanner import (
    _extract_lookup_value,
    _normalize_track_match,
    _search_tracks_for_source,
    _track_artist_names,
    _track_name,
)
from core.library.file_tags import read_embedded_tags
from core.library.path_resolver import resolve_library_file_path
# v3 quality: probe the real file + check it against the ranked profile targets,
# the SAME definition the download import guard uses. Module-level so they're
# monkeypatchable in tests.
from core.imports.file_ops import probe_audio_quality
from core.quality.model import rank_candidate
from core.quality.selection import targets_from_profile, quality_meets_profile
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.quality_upgrade")


def _to_bool(val) -> bool:
    """Coerce a setting value to bool. Handles Python bool, string 'true'/'false', and int."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() == 'true'
    return bool(val) if val is not None else False


# Quality ranks — higher is better. Lossless tops everything; lossy tiers fall out
# of bitrate. 0 means "below the lowest tracked tier / unknown".
RANK_LOSSLESS = 4
RANK_320 = 3
RANK_256 = 2
RANK_192 = 1
RANK_BELOW = 0

LOSSLESS_EXTENSIONS = {'.flac', '.alac', '.ape', '.wav', '.aiff', '.aif', '.dsf', '.dff', '.m4a'}
# NB: .m4a is ambiguous (ALAC vs AAC); we treat the *format* as lossy-capable and
# rely on bitrate below — a true ALAC .m4a reports a lossless-scale bitrate.

# Quality-profile bucket key -> rank.
_PROFILE_KEY_RANK = {
    'flac': RANK_LOSSLESS,
    'mp3_320': RANK_320,
    'mp3_256': RANK_256,
    'mp3_192': RANK_192,
}

# Per-source file-tag key holding that source's own track ID (written by enrichment).
_SOURCE_TRACK_ID_TAG = {
    'spotify': 'spotify_track_id',
    'deezer': 'deezer_track_id',
    'itunes': 'itunes_track_id',
    'audiodb': 'audiodb_track_id',
    'musicbrainz': 'musicbrainz_releasetrackid',
    'tidal': 'tidal_track_id',
}

# Reject a fuzzy candidate whose length differs from ours by more than this (ms) —
# catches wrong versions (live/edit/remix) that share a title. Exact tiers skip it.
_DURATION_TOLERANCE_MS = 5000


def _normalize_kbps(bitrate: Optional[int]) -> Optional[int]:
    """Library bitrate may be stored in bps (e.g. 320000) or kbps (320).
    Normalize to kbps. Returns None when unknown/zero."""
    if not bitrate:
        return None
    try:
        b = int(bitrate)
    except (TypeError, ValueError):
        return None
    if b <= 0:
        return None
    return b // 1000 if b > 4000 else b


def classify_track_quality(file_path: str, bitrate: Optional[int]) -> Optional[int]:
    """Rank a file by format + bitrate. Returns a RANK_* value, or None when it
    can't be judged (a lossy file with no known bitrate)."""
    ext = os.path.splitext(file_path or '')[1].lower()
    kbps = _normalize_kbps(bitrate)

    # Lossless containers: a real lossless file has a high bitrate; a low one is a
    # lossy stream in a lossless container — but flagging that is the Fake Lossless
    # Detector's job, so here we treat the lossless *format* as top rank.
    if ext in {'.flac', '.alac', '.ape', '.wav', '.aiff', '.aif', '.dsf', '.dff'}:
        return RANK_LOSSLESS
    # .m4a / lossy: judge purely by bitrate. A lossless-scale bitrate (ALAC in m4a,
    # or a mislabeled lossless) ranks as lossless.
    if kbps is None:
        return None
    if kbps >= 800:
        return RANK_LOSSLESS
    if kbps >= 280:
        return RANK_320
    if kbps >= 200:
        return RANK_256
    if kbps >= 150:
        return RANK_192
    return RANK_BELOW


def preferred_quality_floor(quality_profile: Dict[str, Any]) -> Optional[int]:
    """The lowest acceptable quality rank from the profile's ENABLED buckets — the
    floor a track must meet. Returns None when nothing is enabled (caller should
    then flag nothing, rather than flagging everything)."""
    qualities = (quality_profile or {}).get('qualities', {}) or {}
    enabled_ranks = [
        _PROFILE_KEY_RANK[key]
        for key, cfg in qualities.items()
        if isinstance(cfg, dict) and cfg.get('enabled') and key in _PROFILE_KEY_RANK
    ]
    if not enabled_ranks:
        return None
    return min(enabled_ranks)


def meets_preferred_quality(file_path: str, bitrate: Optional[int],
                            quality_profile: Dict[str, Any]) -> bool:
    """Pure decision: does this track already meet the user's preferred quality?

    A track meets quality when its format+bitrate rank is at least the profile's
    floor (the worst quality the user still accepts). This honors a profile that
    enables, say, FLAC *and* MP3-320: a 320 kbps MP3 passes, a 128 kbps MP3 does
    not. With nothing enabled, everything passes (we never flag the whole library
    on an empty profile)."""
    floor = preferred_quality_floor(quality_profile)
    if floor is None:
        return True

    file_rank = classify_track_quality(file_path, bitrate)
    if file_rank is None:
        # Lossy file with unknown bitrate: only judgeable when the floor is
        # lossless (then any lossy file is below it). Otherwise don't flag.
        ext = os.path.splitext(file_path or '')[1].lower()
        if floor == RANK_LOSSLESS and ext not in LOSSLESS_EXTENSIONS:
            return False
        return True

    return file_rank >= floor


def _rank_label(rank: Optional[int]) -> str:
    return {
        RANK_LOSSLESS: 'Lossless', RANK_320: 'MP3 320', RANK_256: 'MP3 256',
        RANK_192: 'MP3 192', RANK_BELOW: 'low bitrate',
    }.get(rank, 'unknown')


def _norm_isrc(value: Any) -> str:
    """Canonicalize an ISRC for comparison: uppercase, strip dashes/spaces."""
    if not value:
        return ''
    return str(value).upper().replace('-', '').replace(' ', '').strip()


def _read_file_ids(file_path: str, resolved_path: Optional[str] = None) -> Dict[str, str]:
    """Read the identifiers enrichment embedded in the file's tags.

    Enrichment matches every track to the metadata sources and writes the IDs
    (ISRC + per-source track IDs) into the file — so an already-enriched track
    carries its exact identity. Returns a dict with a normalized ``isrc`` plus any
    ``<source>_track_id`` tags present; empty dict when unreadable / not enriched."""
    resolved = resolved_path or (resolve_library_file_path(file_path) if file_path else None)
    if not resolved and file_path and os.path.isfile(file_path):
        resolved = file_path
    if not resolved:
        return {}
    try:
        info = read_embedded_tags(resolved)
    except Exception:
        return {}
    if not info or not info.get('available'):
        return {}
    tags = info.get('tags') or {}
    out: Dict[str, str] = {}
    isrc = _norm_isrc(tags.get('isrc'))
    if isrc:
        out['isrc'] = isrc
    for tag_key in set(_SOURCE_TRACK_ID_TAG.values()):
        val = tags.get(tag_key)
        if val:
            out[tag_key] = str(val)
    return out


def _duration_ok(want_ms: Any, got_ms: Any, tolerance_ms: int = _DURATION_TOLERANCE_MS) -> bool:
    """Wrong-version guard: True when the candidate's length is within tolerance of
    ours — or when either length is unknown (never reject on missing data)."""
    try:
        w, g = int(want_ms or 0), int(got_ms or 0)
    except (TypeError, ValueError):
        return True
    if w <= 0 or g <= 0:
        return True
    return abs(w - g) <= tolerance_ms


def _match_via_track_id(file_ids: Dict[str, str],
                        source_priority: List[str]) -> Tuple[Optional[Any], Optional[str]]:
    """Most-direct path: enrichment already wrote this track's per-source IDs into
    the file. If we have the active source's own track ID, fetch that exact track by
    ID — no search at all. Returns (track, source) or (None, None)."""
    for source in source_priority:
        tag_key = _SOURCE_TRACK_ID_TAG.get(source)
        track_id = file_ids.get(tag_key) if tag_key else None
        if not track_id:
            continue
        client = get_client_for_source(source)
        if not client or not hasattr(client, 'get_track_details'):
            continue
        try:
            track = client.get_track_details(str(track_id))
        except Exception:
            track = None
        if track:
            return track, source
    return None, None


def _candidate_isrc(cand: Any) -> str:
    """Pull an ISRC off a provider search result (Track / dict), checking the
    common shapes: a flat ``isrc`` or a nested ``external_ids.isrc``."""
    direct = _extract_lookup_value(cand, 'isrc')
    if direct:
        return _norm_isrc(direct)
    ext = _extract_lookup_value(cand, 'external_ids')
    if isinstance(ext, dict):
        return _norm_isrc(ext.get('isrc'))
    return ''


def _match_via_isrc(isrc: str, source_priority: List[str]) -> Tuple[Optional[Any], Optional[str]]:
    """Exact-match a track by its ISRC via each source's ``isrc:`` search.

    ISRC is the universal cross-source recording key, so this resolves the EXACT
    track (with its real album) instead of fuzzy-matching by name. Guarded: only
    a candidate whose own ISRC equals ours is accepted, so a source that ignores
    the ``isrc:`` syntax and returns unrelated hits can't produce a false match.
    Returns (track, source) or (None, None)."""
    if not isrc:
        return None, None
    for source in source_priority:
        client = get_client_for_source(source)
        if not client or not hasattr(client, 'search_tracks'):
            continue
        try:
            results = _search_tracks_for_source(source, f'isrc:{isrc}', limit=5, client=client)
        except Exception:
            results = []
        for cand in results or []:
            if _candidate_isrc(cand) == isrc:
                return cand, source
    return None, None


# Column order for the _load_tracks SELECT — rows come back as dicts keyed by these.
_TRACK_COLS = (
    'id', 'title', 'file_path', 'bitrate', 'duration', 'artist_name', 'album_title',
    'album_id', 'track_number', 'spotify_album_id', 'itunes_album_id', 'deezer_id',
    'musicbrainz_release_id', 'audiodb_id',
)

# Human-readable note per match tier (search uses a confidence % instead).
_MATCH_NOTE = {
    'track_id': 'exact track ID', 'isrc': 'exact ISRC match',
    'album': 'matched within album',
}

# Per-source column holding that source's album ID on the albums table.
_SOURCE_ALBUM_ID_COL = {
    'spotify': 'spotify_album_id',
    'itunes': 'itunes_album_id',
    'deezer': 'deezer_id',
    'musicbrainz': 'musicbrainz_release_id',
    'audiodb': 'audiodb_id',
}


def _norm_title(value: Any) -> str:
    """Collapse a title to alphanumerics for tolerant comparison."""
    return ''.join(ch for ch in str(value or '').lower() if ch.isalnum())


def _find_track_in_album(items: Any, title: str, track_number: Any, engine: Any,
                         want_duration_ms: Any = None) -> Optional[Any]:
    """Pick the track in an album's tracklist that matches ours — exact normalized
    title first (track_number then duration break ties), then a high-similarity
    fuzzy fallback that respects the duration guard."""
    want = _norm_title(title)
    exact = []
    best, best_score = None, 0.0
    for it in items or []:
        it_name = _extract_lookup_value(it, 'name', 'title', default='')
        if want and _norm_title(it_name) == want:
            exact.append(it)
            continue
        if engine and it_name:
            if not _duration_ok(want_duration_ms, _extract_lookup_value(it, 'duration_ms', 'duration')):
                continue
            score = engine.similarity_score(
                engine.normalize_string(title), engine.normalize_string(it_name))
            if score > best_score and score >= 0.85:
                best, best_score = it, score
    if exact:
        if track_number:
            for it in exact:
                if _extract_lookup_value(it, 'track_number') == track_number:
                    return it
        # Multiple same-title cuts (e.g. album + live): prefer the closest length.
        if want_duration_ms and len(exact) > 1:
            exact.sort(key=lambda it: abs(int(want_duration_ms) - int(
                _extract_lookup_value(it, 'duration_ms', 'duration', default=0) or 0)))
        return exact[0]
    return best


def _match_via_album(engine: Any, source_priority: List[str], artist: str, album_title: str,
                     title: str, track_number: Any, stored_album_ids: Dict[str, str],
                     want_duration_ms: Any = None) -> Tuple[Optional[Any], Optional[str]]:
    """Structured artist → album → track match. For each source: use the album's
    stored source ID if we already have it (enriched album), else find the album
    by searching ``artist album``; then pull that album's tracklist and locate our
    track in it. This pins the right album (exact context) without needing the
    track itself to be enriched. Returns (track, source) or (None, None)."""
    if not album_title:
        return None, None
    for source in source_priority:
        client = get_client_for_source(source)
        if not client or not hasattr(client, 'get_album_tracks'):
            continue

        album_id = stored_album_ids.get(source)
        album_name = album_title
        if not album_id and hasattr(client, 'search_albums'):
            try:
                albums = client.search_albums(f'{artist} {album_title}'.strip(), limit=5)
            except Exception:
                albums = []
            best_alb, best_s = None, 0.0
            for alb in albums or []:
                aname = _extract_lookup_value(alb, 'name', 'title', default='')
                s = engine.similarity_score(
                    engine.normalize_string(album_title), engine.normalize_string(aname))
                if s > best_s and s >= 0.80:
                    best_alb, best_s = alb, s
            if best_alb is not None:
                album_id = _extract_lookup_value(best_alb, 'id')
                album_name = _extract_lookup_value(best_alb, 'name', 'title', default=album_title)
        if not album_id:
            continue

        try:
            resp = client.get_album_tracks(str(album_id))
        except Exception:
            resp = None
        items = resp.get('items') if isinstance(resp, dict) else None
        match = _find_track_in_album(items, title, track_number, engine, want_duration_ms)
        if match is None:
            continue
        # The album tracklist's tracks usually omit the album object — attach it so
        # the wishlist add carries the correct album context.
        if isinstance(match, dict):
            alb = match.get('album')
            if not isinstance(alb, dict) or not alb.get('name'):
                match['album'] = {'name': album_name, 'images': []}
        return match, source
    return None, None


def _find_best_match(engine: Any, source_priority: List[str], title: str, artist: str,
                     album: str, min_confidence: float,
                     want_duration_ms: Any = None) -> Tuple[Optional[Any], float, Optional[str], bool]:
    """Search the configured metadata sources for the best replacement match.
    Returns (best_track, confidence, source, attempted_any_provider)."""
    temp_track = type('TempTrack', (), {'name': title, 'artists': [artist], 'album': album})()
    queries = engine.generate_download_queries(temp_track)

    best, best_conf, best_src = None, 0.0, None
    attempted = False
    for query in queries:
        for source in source_priority:
            client = get_client_for_source(source)
            if not client or not hasattr(client, 'search_tracks'):
                continue
            attempted = True
            matches = _search_tracks_for_source(source, query, limit=5, client=client)
            time.sleep(0.5)  # be gentle on metadata APIs
            for cand in matches or []:
                # Wrong-version guard: a candidate whose length is way off is a
                # different cut (live/edit/remix) — reject before it can win.
                if not _duration_ok(want_duration_ms, _extract_lookup_value(cand, 'duration_ms', 'duration')):
                    continue
                cand_artists = _track_artist_names(cand)
                artist_conf = max(
                    (engine.similarity_score(engine.normalize_string(artist),
                                             engine.normalize_string(n)) for n in cand_artists),
                    default=0.0,
                )
                title_conf = engine.similarity_score(
                    engine.normalize_string(title), engine.normalize_string(_track_name(cand)))
                conf = artist_conf * 0.5 + title_conf * 0.5
                album_type = _extract_lookup_value(cand, 'album_type', default='') or ''
                if album_type == 'album':
                    conf += 0.02
                elif album_type == 'ep':
                    conf += 0.01
                if conf > best_conf and conf >= min_confidence:
                    best, best_conf, best_src = cand, conf, source
            if best_conf >= 0.9:
                break
        if best_conf >= 0.9:
            break
    return best, best_conf, best_src, attempted


@register_job
class QualityUpgradeJob(RepairJob):
    job_id = 'quality_upgrade'
    display_name = 'Quality Upgrade Finder (active — proposes a replacement)'
    description = 'Finds library tracks below your quality profile and actively searches a better version to add to the wishlist'
    help_text = (
        'ACTIVE quality job. For every library track below your quality profile it '
        'goes one step further than the Quality Check (flag-only) scanner: it '
        'actively SEARCHES your metadata source for the exact better version and '
        'attaches it to the finding, so Apply adds that track straight to your '
        'wishlist.\n\n'
        'Quality is judged the SAME way as the download/import pipeline — it reads '
        'the REAL file with mutagen (measured bit depth / sample rate / bitrate) and '
        'checks it against your v3 quality profile targets (strict: fallback is '
        'ignored, that\'s a download-time concession, not "good enough" for an '
        'upgrade). So a 128 kbps MP3, a 16-bit FLAC where you want 24-bit, etc. are '
        'all caught accurately.\n\n'
        'For every below-profile track it resolves the better version by the most '
        'precise identity available, in order: the source track ID enrichment wrote '
        "into the file → the file's ISRC → the album's tracklist (by stored album ID "
        'or album search) → a name/artist search (with a duration guard against wrong '
        'live/edit cuts). It skips tracks it already proposed, so re-runs are cheap. '
        'Nothing is queued automatically — applying a finding adds the matched track '
        '(with album context) to the wishlist, like any other download.\n\n'
        'Settings:\n'
        '- Scope: "watchlist" (watchlisted artists only) or "all" (whole library)\n'
        '- Min confidence: minimum match confidence (0-1) to surface a finding\n'
        '- Deep audio verify (default OFF): also run the ffmpeg decode guard '
        '(truncation + silence) per track — catches broken/incomplete files the '
        'header hides, but decodes every file (seconds per track, CPU-heavy).\n\n'
        'Sibling job: "Quality Check (flag only)" finds the same below-profile tracks '
        'but only flags them for you to decide per finding (re-download / delete / '
        'ignore) instead of searching a replacement. Fake/transcoded lossless '
        'detection is the separate Fake Lossless Detector job.'
    )
    icon = 'repair-icon-lossy'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {'scope': 'all', 'min_confidence': 0.7, 'deep_audio_verify': False, 'require_top_target': False}
    setting_options = {'scope': ['all', 'watchlist'], 'deep_audio_verify': [True, False], 'require_top_target': [True, False]}
    auto_fix = False

    def _get_settings(self, context: JobContext) -> Dict[str, Any]:
        merged = dict(self.default_settings)
        if context.config_manager:
            try:
                cfg = context.config_manager.get(f'repair.jobs.{self.job_id}.settings', {})
                if isinstance(cfg, dict):
                    merged.update(cfg)
            except Exception as e:
                logger.debug("settings read failed: %s", e)
        try:
            merged['min_confidence'] = float(merged.get('min_confidence', 0.7))
        except (TypeError, ValueError):
            merged['min_confidence'] = 0.7
        merged['deep_audio_verify'] = _to_bool(merged.get('deep_audio_verify'))
        merged['require_top_target'] = _to_bool(merged.get('require_top_target'))
        return merged

    def _load_tracks(self, db: Any, scope: str) -> List[dict]:
        conn = db._get_connection()
        try:
            base = (
                "SELECT t.id, t.title, t.file_path, t.bitrate, t.duration, "
                "a.name AS artist_name, al.title AS album_title, t.album_id, t.track_number, "
                "al.spotify_album_id, al.itunes_album_id, al.deezer_id, "
                "al.musicbrainz_release_id, al.audiodb_id "
                "FROM tracks t "
                "JOIN artists a ON t.artist_id = a.id "
                "JOIN albums al ON t.album_id = al.id "
                "WHERE t.file_path IS NOT NULL AND t.file_path != ''"
            )
            if scope == 'watchlist':
                artists = db.get_watchlist_artists(profile_id=1)
                names = [getattr(ar, 'artist_name', None) for ar in artists]
                names = [n for n in names if n]
                if not names:
                    return []
                placeholders = ','.join('?' for _ in names)
                rows = conn.execute(
                    base + f" AND a.name IN ({placeholders})", names).fetchall()
            else:
                rows = conn.execute(base).fetchall()
            return [dict(zip(_TRACK_COLS, r, strict=False)) for r in rows]
        finally:
            conn.close()

    def _load_existing_finding_ids(self, db: Any) -> set:
        """Track IDs that already have a finding for this job (any status). Lets a
        re-run skip tracks we've already proposed/dismissed without re-hitting the
        metadata API — pending stays deduped, and a dismissed track stays dismissed."""
        conn = db._get_connection()
        try:
            rows = conn.execute(
                "SELECT entity_id FROM repair_findings WHERE job_id = ? AND entity_type = 'track'",
                (self.job_id,)).fetchall()
            return {str(r[0]) for r in rows if r and r[0] is not None}
        except Exception:
            return set()
        finally:
            conn.close()

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(self._load_tracks(context.db, self._get_settings(context)['scope']))
        except Exception:
            return 0

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = self._get_settings(context)
        scope = settings['scope']
        min_conf = settings['min_confidence']
        deep_verify = settings['deep_audio_verify']
        require_top = settings['require_top_target']

        # v3 quality: judge the REAL file (mutagen-measured bit depth / sample rate
        # / bitrate) against the profile's ranked targets — the SAME definition the
        # download import guard uses. Strict: fallback is ignored (a download-time
        # concession, not "good enough" for an upgrade). targets_from_profile /
        # quality_meets_profile / probe_audio_quality are imported at module level.
        db = context.db
        quality_profile = db.get_quality_profile()
        targets, _fallback = targets_from_profile(quality_profile)
        if not targets:
            logger.info("[Quality Upgrade] No quality targets in profile — nothing to flag")
            return result

        logger.info(
            "[Quality Upgrade] scope=%s require_top=%s · all targets: %s",
            scope, require_top,
            [t.label for t in targets] if targets else '(none — all pass)',
        )

        try:
            tracks = self._load_tracks(db, scope)
        except Exception as e:
            logger.error("[Quality Upgrade] Error loading tracks: %s", e, exc_info=True)
            result.errors += 1
            return result

        total = len(tracks)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(phase=f'Checking quality on {total} tracks...', total=total)

        # Tracks we've already proposed/dismissed — skip them so a re-run doesn't
        # re-resolve the same tracks against the metadata API.
        already_found = self._load_existing_finding_ids(db)

        # Metadata source for matching — resolved lazily so we only fail if we
        # actually find a low-quality track that needs a match.
        engine = None
        source_priority: List[str] = []

        for i, row in enumerate(tracks):
            if context.check_stop():
                return result
            if i % 10 == 0 and context.wait_if_paused():
                return result

            track_id = row['id']
            title = row['title']
            file_path = row['file_path']
            bitrate = row['bitrate']
            duration_ms = row.get('duration')
            artist_name = row['artist_name']
            album_title = row['album_title']
            album_id = row['album_id']
            track_number = row.get('track_number')
            stored_album_ids = {
                src: row[col] for src, col in _SOURCE_ALBUM_ID_COL.items() if row.get(col)
            }
            result.scanned += 1

            if str(track_id) in already_found:
                result.findings_skipped_dedup += 1
                continue

            # v3 quality decision — probe the REAL file. Resolve the library path
            # first (the DB stores a possibly-relative path). Pass config_manager so
            # the resolver can find the transfer/music folders and expand relative paths.
            resolved_path = resolve_library_file_path(
                file_path,
                transfer_folder=context.transfer_folder,
                config_manager=context.config_manager,
            ) if file_path else None
            if not resolved_path and file_path and os.path.isfile(file_path):
                resolved_path = file_path

            measured_aq = probe_audio_quality(resolved_path) if resolved_path else None

            # Optional ffmpeg deep verify (default off): a truncated/silent file is
            # treated as "needs a replacement" just like a below-profile one.
            broken_reason = None
            if deep_verify and resolved_path:
                try:
                    from core.imports.silence import detect_broken_audio
                    broken_reason = detect_broken_audio(resolved_path)
                except Exception as e:
                    logger.debug("[Quality Upgrade] deep verify failed for %s: %s", file_path, e)

            if measured_aq is None and not broken_reason:
                # Can't read the file → can't judge it; leave it alone.
                result.skipped += 1
                if context.update_progress and (i + 1) % 25 == 0:
                    context.update_progress(i + 1, total)
                continue

            if not broken_reason and measured_aq is not None:
                if require_top:
                    # ranking-based: skip only if the file already sits at rank 0
                    # (the top configured target). Any lower rank → flag for upgrade.
                    idx, _ = rank_candidate(measured_aq, targets)
                    already_best = (idx == 0)
                else:
                    # default: skip if the file meets ANY configured target (i.e.
                    # it's not below the acceptable floor).
                    already_best = quality_meets_profile(measured_aq, targets)
                if already_best:
                    result.skipped += 1
                    if context.update_progress and (i + 1) % 25 == 0:
                        context.update_progress(i + 1, total)
                    continue

            # Below profile (or broken) — find a better version to propose.
            if engine is None:
                from core.matching_engine import MusicMatchingEngine
                engine = MusicMatchingEngine()
                source_priority = get_source_priority(get_primary_source()) or []
                if not source_priority:
                    logger.warning("[Quality Upgrade] No metadata provider available — cannot propose upgrades")
                    return result

            if context.is_spotify_rate_limited():
                logger.info("[Quality Upgrade] Spotify rate-limited — stopping scan early")
                return result

            current_label = measured_aq.label() if measured_aq is not None else 'broken/unreadable'
            if broken_reason:
                current_label = f'{current_label} (broken: {broken_reason})' if measured_aq is not None else f'broken ({broken_reason})'
            if context.report_progress:
                _why = 'broken audio' if broken_reason else 'low quality'
                context.report_progress(
                    scanned=i + 1, total=total,
                    log_line=f'{_why} ({current_label}): {artist_name} - {title}',
                    log_type='info')

            # Read the identifiers enrichment embedded in the file once (ISRC +
            # per-source track IDs), used by the two most-exact tiers below.
            # Pass resolved_path so the inner resolver doesn't redo the lookup.
            file_ids = _read_file_ids(file_path, resolved_path=resolved_path)

            # Tiered match, best identity first, loosest last:
            #   0. The active source's OWN track ID, embedded in the file by
            #      enrichment → fetch that exact track by ID. No search at all.
            #   1. ISRC (also in the tags) → exact track on any source.
            #   2. Album → track: stored album source ID if we have it (enriched
            #      album), else find the album by search, then locate our track in
            #      its tracklist. Pins the right album even when the track itself
            #      isn't enriched. (artist → album → track)
            #   3. Plain artist+title search with similarity scoring. (artist → track)
            # The fuzzy tiers (2-3) also apply a duration guard to reject wrong cuts.
            best, source, conf, attempted = None, None, 0.0, False

            matched_via = 'track_id'
            best, source = _match_via_track_id(file_ids, source_priority)
            if best:
                conf, attempted = 1.0, True

            if not best:
                matched_via = 'isrc'
                best, source = _match_via_isrc(file_ids.get('isrc', ''), source_priority)
                if best:
                    conf, attempted = 1.0, True

            if not best:
                matched_via = 'album'
                try:
                    best, source = _match_via_album(
                        engine, source_priority, artist_name or '', album_title or '',
                        title, track_number, stored_album_ids, duration_ms)
                except Exception as e:
                    logger.debug("[Quality Upgrade] Album match error for %s - %s: %s", artist_name, title, e)
                    best = None
                if best:
                    conf, attempted = 1.0, True

            if not best:
                matched_via = 'search'
                try:
                    best, conf, source, attempted = _find_best_match(
                        engine, source_priority, title, artist_name or '', album_title or '',
                        min_conf, duration_ms)
                except Exception as e:
                    logger.debug("[Quality Upgrade] Match error for %s - %s: %s", artist_name, title, e)
                    result.errors += 1
                    continue

            if not best:
                if matched_via == 'search' and not attempted:
                    logger.warning("[Quality Upgrade] No metadata provider responded — stopping")
                    return result
                result.skipped += 1
                continue

            matched = _normalize_track_match(best, source or 'metadata')
            # Carry album context: prefer the matched album, fall back to the
            # library album the low-quality track came from.
            alb = matched.get('album')
            if (not isinstance(alb, dict) or not alb.get('name')) and album_title:
                matched['album'] = {'name': album_title, 'images': (alb or {}).get('images', []) if isinstance(alb, dict) else []}

            if context.create_finding:
                try:
                    inserted = context.create_finding(
                        job_id=self.job_id,
                        finding_type='quality_upgrade',
                        severity='info',
                        entity_type='track',
                        entity_id=str(track_id),
                        file_path=file_path,
                        title=f'Upgrade: {artist_name} - {title} ({current_label})',
                        description=(
                            f'"{title}" by {artist_name} is {current_label}'
                            + (f', below your preferred quality ({targets[0].label})' if require_top and targets else ', below your preferred quality')
                            + f'. Best match: "{_track_name(best)}" via {source} '
                            f'({_MATCH_NOTE.get(matched_via, "matched") if matched_via != "search" else f"confidence {conf:.0%}"}). '
                            'Apply to add it to the wishlist.'),
                        details={
                            'track_id': track_id,
                            'track_title': title,
                            'artist': artist_name,
                            'album_id': album_id,
                            'album_title': album_title,
                            'current_format': current_label,
                            'current_bitrate': bitrate,
                            'match_confidence': conf,
                            'matched_via': matched_via,
                            'provider': source,
                            'matched_track_data': matched,
                        })
                    if inserted:
                        result.findings_created += 1
                    else:
                        result.findings_skipped_dedup += 1
                except Exception as e:
                    logger.debug("[Quality Upgrade] create finding failed for track %s: %s", track_id, e)
                    result.errors += 1

            if context.update_progress and (i + 1) % 10 == 0:
                context.update_progress(i + 1, total)

        if context.update_progress:
            context.update_progress(total, total)
        logger.info("[Quality Upgrade] %d scanned, %d upgrades found, %d already met profile / skipped",
                    result.scanned, result.findings_created, result.skipped)
        if result.scanned > 0 and result.findings_created == 0 and result.errors == 0:
            logger.info(
                "[Quality Upgrade] All tracks already satisfy the configured targets. "
                "If you expected upgrades, check your quality profile — the current "
                "top target is: %s",
                targets[0].label if targets else '(none)',
            )
        return result
