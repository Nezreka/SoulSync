"""Re-route a library album's existing files through the same
post-processing pipeline that handles fresh downloads.

The old reorganize endpoint reinvented several wheels — its own template
engine, its own disc-number resolution from file tags, its own sidecar
sweep, its own collision detection. Each of those drifted from the
canonical post-processing path over time, producing reorganize-only
bugs (multi-disc deluxe collapsing to single-disc when even one file's
tag was missing; tracks silently skipped when their file paths didn't
resolve on disk; etc.).

The new design follows the import page's pattern: copy each file to a
staging folder, build the same context dict the download workers
build, then call ``_post_process_matched_download`` for each one.
Post-processing already knows how to pick the right destination, write
the right tags, handle multi-disc subfolders, recreate sidecars (cover
art, lyrics), and run AcoustID verification — there's nothing for
reorganize to add on top.

Hard requirement: the album must have at least one stored
metadata-source ID (spotify_album_id / itunes_album_id / deezer_id /
discogs_id / soul_id). With no source ID we have nothing authoritative
to ask for the canonical tracklist, and silently degrading to file
tags is exactly the failure mode the old code path produced. Albums
without a source ID are reported back to the caller and skipped
entirely.
"""

import os
import shutil
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

# Per-album track concurrency. Matches the download workers' per-batch
# concurrency (3) so reorganize feels comparable to a fresh download.
#
# Operational note: post-processing can spawn an ffmpeg subprocess per
# track if `lossy_copy.downsample_hires` is enabled. With 3 workers
# that's up to 3 concurrent ffmpeg processes. Acceptable for typical
# album sizes (10-20 tracks); on a giant single-album reorganize
# (50+ tracks) ffmpeg's transient memory could be noticeable but each
# subprocess is short-lived so total RAM doesn't pile up. If we ever
# see resource issues from this, drop to 2 here rather than disabling
# concurrency entirely.
_REORGANIZE_MAX_WORKERS = 3

# Watchdog interval — how often the orchestrator checks the worker
# pool while waiting for tasks to finish. Setting this to 30s means
# we log a warning naming any track that's been in flight longer than
# `_HUNG_WORKER_THRESHOLD` (so an operator can investigate) without
# burning CPU on a tight poll. Doesn't kill stuck threads (Python
# can't), just surfaces them.
_WATCHDOG_INTERVAL_SECONDS = 30
_HUNG_WORKER_THRESHOLD_SECONDS = 300  # 5 min — generous; real worst-case
                                       # is ffmpeg downsampling a long
                                       # hi-res FLAC, ~30-60s typically.

from core.metadata_service import (
    get_album_for_source,
    get_album_tracks_for_source,
    get_client_for_source,
    get_primary_source,
    get_source_priority,
)
from utils.logging_config import get_logger

logger = get_logger("library_reorganize")


def _safe_filename(name: str) -> str:
    """Strip path-illegal characters so we can use the value as a
    filename component on the staging path."""
    return ''.join(c for c in (name or 'unknown') if c not in '<>:"/\\|?*').strip() or 'unknown'


def _normalize_album_tracks(api_tracks):
    """Normalize the various provider tracklist shapes (dict-with-`items`,
    bare list, ``None``) to a single list of item dicts."""
    if not api_tracks:
        return []
    if isinstance(api_tracks, dict):
        items = api_tracks.get('items') or []
        return items if items else []
    if isinstance(api_tracks, list):
        return api_tracks
    return []


SUPPORTED_SOURCES = ('spotify', 'itunes', 'deezer', 'discogs', 'hydrabase')

# Per-source album-ID column mapping on the `albums` table row.
_ALBUM_ID_COLUMNS = {
    'spotify': 'spotify_album_id',
    'itunes': 'itunes_album_id',
    'deezer': 'deezer_id',
    'discogs': 'discogs_id',
    'hydrabase': 'soul_id',
}

# Human-facing label for each source.
SOURCE_LABELS = {
    'spotify': 'Spotify',
    'itunes': 'Apple Music (iTunes)',
    'deezer': 'Deezer',
    'discogs': 'Discogs',
    'hydrabase': 'Hydrabase',
}


def _extract_source_ids(album_data: dict) -> Dict[str, str]:
    """Pull the per-source album-ID strings off an album row."""
    return {
        source: (album_data.get(column) or '')
        for source, column in _ALBUM_ID_COLUMNS.items()
    }


def available_sources_for_album(album_data: dict) -> List[dict]:
    """Return the list of metadata sources the user can pick for this
    album's reorganize. Every entry has both (a) a stored album ID on
    the local row AND (b) an authenticated / configured client on this
    SoulSync instance.

    Returns entries in source-priority order (preferred source first).
    Each entry is ``{'source': str, 'label': str}``. No API calls —
    purely local inspection.
    """
    source_ids = _extract_source_ids(album_data)
    try:
        primary = get_primary_source()
    except Exception:
        primary = 'deezer'

    out = []
    for source in get_source_priority(primary):
        if source not in SUPPORTED_SOURCES:
            continue
        if not source_ids.get(source):
            continue
        if get_client_for_source(source) is None:
            continue
        out.append({
            'source': source,
            'label': SOURCE_LABELS.get(source, source),
        })
    return out


def authed_sources() -> List[dict]:
    """Return all metadata sources the user has authed/configured on
    this SoulSync instance. Doesn't require any album-specific stored
    ID — used by the bulk "Reorganize All" picker where each album
    has its own ID coverage and we just want to know which sources
    are reachable. Returned in priority order."""
    try:
        primary = get_primary_source()
    except Exception:
        primary = 'deezer'

    out = []
    for source in get_source_priority(primary):
        if source not in SUPPORTED_SOURCES:
            continue
        if get_client_for_source(source) is None:
            continue
        out.append({
            'source': source,
            'label': SOURCE_LABELS.get(source, source),
        })
    return out


def _resolve_source(album_data: dict, primary_source: str, strict_source: bool = False):
    """Walk the configured source priority looking for the first source
    we have an ID for AND that returns a usable tracklist.

    When ``strict_source`` is True, only the caller-provided
    ``primary_source`` is tried — no fallback. Used when the user has
    explicitly picked a source in the reorganize modal: picking Spotify
    means "use Spotify or fail", not "use Spotify and silently fall
    back to Deezer".

    Returns ``(source_name, album_meta, tracks_list)`` or ``(None, None, None)``.
    """
    source_ids = _extract_source_ids(album_data)

    if strict_source:
        sources_to_try = [primary_source] if primary_source else []
    else:
        sources_to_try = get_source_priority(primary_source)

    for source in sources_to_try:
        sid = source_ids.get(source) or ''
        if not sid:
            continue
        try:
            api_album = get_album_for_source(source, sid)
            api_tracks = get_album_tracks_for_source(source, sid)
        except Exception as e:
            logger.warning(f"[Reorganize] {source} lookup raised: {e}")
            continue
        items = _normalize_album_tracks(api_tracks)
        if not items or not api_album:
            continue
        return source, api_album, items

    return None, None, None


# Tokens that indicate a *different recording* of a track — when one
# side of a comparison has these and the other doesn't, the two are NOT
# the same track (e.g. "Bitch Don't Kill My Vibe" vs "Bitch Don't Kill
# My Vibe (Remix)" are different recordings; the tier 4 substring match
# would silently merge them otherwise). "Bonus track" is intentionally
# NOT here — it's a marketing annotation, not a recording difference.
_VERSION_DIFFERENTIATORS = frozenset({
    'remix', 'remixed',
    'live', 'unplugged', 'concert',
    'acoustic',
    'demo',
    'extended', 'edit',
    'instrumental', 'karaoke',
    'remaster', 'remastered', 'remastering',
    'mono', 'stereo',
    'acapella', 'cappella',
    'cover',
    'reprise',
    'alternate', 'alt',
    'rehearsal',
})


def _differentiators_in(norm_title: str) -> frozenset:
    """Return the set of version-differentiator tokens present in a
    normalized title. Used by the tier-4 matcher to reject substring
    matches across different recordings of the same song."""
    if not norm_title:
        return frozenset()
    return frozenset(t for t in norm_title.split() if t in _VERSION_DIFFERENTIATORS)


def _normalize_title(value) -> str:
    """Lowercase + strip cosmetic punctuation and treat brackets / dashes
    / slashes as word separators so the same track named slightly
    differently across providers and user libraries still matches.

    Examples that should normalize equal:

    - ``Bitch, Don't Kill My Vibe - Remix``  ↔  ``Bitch, Don't Kill My Vibe (Remix)``
    - ``Don't Stop Believin'``               ↔  ``Don’t Stop Believin’``
    - ``Swimming Pools (Drank) - Extended Version``
                                              ↔  ``Swimming Pools (Drank) (Extended Version)``
    """
    if value is None:
        return ''
    out = str(value).strip().lower()
    # Strip characters that don't carry meaning across providers.
    for ch in ('"', "'", '‘', '’', '“', '”', '.', ',', '!', '?',
               '(', ')', '[', ']', '{', '}'):
        out = out.replace(ch, '')
    # Treat separators as whitespace so "foo - bar" and "foo (bar)" align.
    for ch in ('-', '–', '—', ':', '/', '\\'):
        out = out.replace(ch, ' ')
    return ' '.join(out.split())


# Title-match scoring grid. Each component's weight was picked to
# satisfy these design rules:
#
#   1. EXACT title alone is enough to win.
#   2. SUBSTRING at the high-confidence floor (≥0.6) is enough to win.
#   3. SUBSTRING at the lower with-tn-match floor (≥0.3) needs the
#      track_number bonus to win — track_number provides the missing
#      confidence.
#   4. TRACK-NUMBER alone is NOT enough — never falls through to a
#      blind track-number lookup on multi-disc albums (that's the
#      bug that mis-routed winecountrygames's bonus tracks).
#   5. Different version-differentiator tokens (Remix vs no-remix)
#      hard-reject before scoring (see `_score_candidate`).
#
# Worked examples (with threshold = 50):
#
#   exact title + tn match               100 + 20 = 120  → match
#   exact title alone                    100      = 100  → match
#   substring ratio 1.0  (no tn match)   50 + 40  = 90   → match
#   substring ratio 0.6  (no tn match)   50 + 0   = 50   → match
#   substring ratio 0.5  (no tn match)   0        = 0    → no match
#   substring ratio 0.45 + tn match      40 + 20  = 60   → match
#   substring ratio 0.28 + tn match      0  + 20  = 20   → no match
#                                          (Real vs "Real Real Real")
#   track_number alone (no title signal) 0  + 20  = 20   → no match
#   different version diffs (any inputs) hard-reject     → 0
#
# Weights are deliberately spaced so each gate is well-clear of the
# threshold; small ratio adjustments don't flip a borderline case
# unexpectedly.

_MATCH_SCORE_THRESHOLD = 50

_W_EXACT_TITLE = 100
_W_TRACK_NUMBER = 20

# Standalone substring (no tn match required): floor + scaled bonus.
# At ratio = floor: contribute base only. At ratio = 1.0: contribute
# base + range. Linear in between.
_W_SUBSTRING_BASE_STANDALONE = 50
_W_SUBSTRING_RATIO_RANGE = 40
_SUBSTRING_RATIO_FLOOR_STANDALONE = 0.6

# With-tn-match substring: lower floor (0.3) but slightly reduced
# base (40) so this path never beats a standalone high-ratio match
# on equal-tn ties.
_W_SUBSTRING_BASE_WITH_TN = 40
_SUBSTRING_RATIO_FLOOR_WITH_TN = 0.3


def _score_candidate(
    norm_local: str,
    local_tn: Optional[int],
    local_diffs: frozenset,
    api_norm: str,
    api_tn: Optional[int],
) -> int:
    """Score a single API candidate against the local track. Higher
    means more confident match; 0 means no usable signal. The orchestrator
    picks the highest-scoring candidate above
    :data:`_MATCH_SCORE_THRESHOLD` and treats sub-threshold tracks as
    unmatched (the "trust the source — if it doesn't have the track,
    skip it" design policy).

    Components:

    - **Exact normalized-title match** is the strongest signal — usually
      enough on its own, especially because local titles SoulSync wrote
      should already match the source's text after normalization.
    - **Substring containment** with a length-ratio guard handles
      annotation drift like ``"The Recipe - Bonus Track"`` (local)
      matching ``"The Recipe"`` (API). The ratio bonus rewards more
      specific matches, so longer common prefixes win over shorter ones.
    - **Track-number agreement** is a tiebreaker, never enough alone
      (track_number-only would mis-route on multi-disc).
    - **Version-differentiator mismatch** is a hard reject — if local
      has ``Remix`` and API doesn't (or vice versa), they're different
      recordings, not annotation drift. Returns 0 unconditionally.
    """
    if not norm_local or not api_norm:
        return 0

    # Hard reject: version differentiators must agree exactly. ``Remix``
    # vs no-remix means different recordings, regardless of how
    # otherwise-similar the titles are.
    if _differentiators_in(api_norm) != local_diffs:
        return 0

    score = 0
    tn_match = local_tn is not None and api_tn == local_tn

    if api_norm == norm_local:
        score += _W_EXACT_TITLE
    else:
        if api_norm in norm_local:
            ratio = len(api_norm) / max(len(norm_local), 1)
        elif norm_local in api_norm:
            ratio = len(norm_local) / max(len(api_norm), 1)
        else:
            ratio = 0.0
        if ratio >= _SUBSTRING_RATIO_FLOOR_STANDALONE:
            # Strong substring — credit regardless of tn agreement.
            normalized = (
                (ratio - _SUBSTRING_RATIO_FLOOR_STANDALONE)
                / (1.0 - _SUBSTRING_RATIO_FLOOR_STANDALONE)
            )
            score += _W_SUBSTRING_BASE_STANDALONE + int(normalized * _W_SUBSTRING_RATIO_RANGE)
        elif tn_match and ratio >= _SUBSTRING_RATIO_FLOOR_WITH_TN:
            # Weaker substring (e.g., "the recipe" in "the recipe bonus
            # track" at ratio 0.45) — accept ONLY because track_number
            # also matches, and at slightly reduced base score.
            score += _W_SUBSTRING_BASE_WITH_TN

    if tn_match:
        score += _W_TRACK_NUMBER

    return score


def _prenormalize_api_tracks(api_tracks: List[dict]) -> List[tuple]:
    """Compute ``(item, normalized_title, parsed_track_number)`` once
    per API track so the matcher doesn't redo this work on every local
    track. Callers that match many local tracks against the same API
    list (the orchestrator's per-album loop) should hold this list and
    pass it to :func:`_find_api_track`.

    For a 17-track local library matched against a 22-track API list,
    avoiding re-normalization saves 17×22 = 374 normalize calls per
    album reorganize."""
    out = []
    for item in api_tracks:
        api_norm = _normalize_title(item.get('name') or item.get('title'))
        try:
            api_tn = int(item.get('track_number')) if item.get('track_number') is not None else None
        except (TypeError, ValueError):
            api_tn = None
        out.append((item, api_norm, api_tn))
    return out


def _find_api_track(api_tracks, db_title: str, db_track_number) -> Optional[dict]:
    """Find the API track that corresponds to a given local track row.

    ``api_tracks`` may be either a raw list of API dicts (will be
    normalized internally) OR a list of pre-normalized 3-tuples from
    :func:`_prenormalize_api_tracks`. The orchestrator uses the
    pre-normalized form to avoid O(n*m) normalization calls; tests
    use the raw list for convenience.

    Local rows carry (title, track_number) but NOT disc_number.
    Multi-disc albums repeat track_numbers across discs, so a
    track_number-only join would collapse the mapping. Title is the
    natural disambiguator (each disc's track 1 has a different title),
    but local titles drift from API titles in predictable ways:
    trailing ``- Bonus Track`` annotations, ``- Remix`` vs ``(Remix)``,
    etc.

    Implementation: each candidate is scored by :func:`_score_candidate`;
    the highest-scoring one above :data:`_MATCH_SCORE_THRESHOLD` wins.
    If nothing clears the threshold the source genuinely doesn't have a
    plausible match and we return ``None`` — the orchestrator surfaces
    that as ``"not in tracklist, left in place"`` rather than silently
    mis-routing.
    """
    norm_local = _normalize_title(db_title)
    if not norm_local:
        return None
    try:
        tn = int(db_track_number) if db_track_number is not None else None
    except (TypeError, ValueError):
        tn = None
    local_diffs = _differentiators_in(norm_local)

    # Accept either pre-normalized candidates or raw API dicts.
    if api_tracks and isinstance(api_tracks[0], tuple):
        candidates = api_tracks  # type: ignore[assignment]
    else:
        candidates = _prenormalize_api_tracks(api_tracks)  # type: ignore[arg-type]

    best_item: Optional[dict] = None
    best_score = 0
    best_tn_match = False

    for item, api_norm, api_tn in candidates:
        score = _score_candidate(norm_local, tn, local_diffs, api_norm, api_tn)
        if score < _MATCH_SCORE_THRESHOLD:
            continue
        tn_match = tn is not None and api_tn == tn
        if score > best_score or (score == best_score and tn_match and not best_tn_match):
            best_item = item
            best_score = score
            best_tn_match = tn_match

    return best_item


def load_album_and_tracks(db, album_id):
    """Load the album row + all its track rows from the local DB.

    Returns ``(album_dict | None, tracks_list)``. ``album_dict`` is None
    when the album doesn't exist; tracks_list is empty when the album
    has no tracks. The caller decides what status to surface for each
    state.
    """
    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT al.*, ar.name as artist_name
            FROM albums al
            JOIN artists ar ON al.artist_id = ar.id
            WHERE al.id = ?
            """,
            (str(album_id),),
        )
        album_row = cursor.fetchone()
        if not album_row:
            return None, []
        album_data = dict(album_row)

        cursor.execute(
            """
            SELECT t.*, ar.name as artist_name
            FROM tracks t
            JOIN artists ar ON t.artist_id = ar.id
            WHERE t.album_id = ?
            ORDER BY t.track_number
            """,
            (str(album_id),),
        )
        tracks = [dict(r) for r in cursor.fetchall()]
        return album_data, tracks
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                pass


def plan_album_reorganize(
    album_data: dict,
    tracks: List[dict],
    primary_source: Optional[str] = None,
    strict_source: bool = False,
) -> dict:
    """Compute the per-track plan for an album reorganize without doing
    any file IO. Both the actual reorganize orchestrator and the preview
    endpoint share this so the preview is guaranteed to match what would
    happen on apply.

    Returns:
        ``{'status': 'planned' | 'no_source_id' | 'no_tracks',
           'source': str | None,
           'api_album': dict | None,
           'total_discs': int,
           'items': [{'track': dict, 'api_track': dict | None,
                      'matched': bool, 'reason': str | None}, ...]}``

    Per-track behavior matches the orchestrator exactly:
    - Match by `(normalized_title, track_number)`, then title alone, then
      track_number alone.
    - Tracks with no match are reported with `matched=False` and a reason.
    - `disc_number` for each track comes from its matched API entry; if
      unmatched, `api_track is None` and the caller decides what to do.
    """
    if not tracks:
        return {
            'status': 'no_tracks', 'source': None, 'api_album': None,
            'total_discs': 1, 'items': [],
        }

    if primary_source is None:
        try:
            primary_source = get_primary_source()
        except Exception:
            primary_source = 'deezer'

    source, api_album, api_tracks = _resolve_source(
        album_data, primary_source, strict_source=strict_source
    )
    if not source:
        reason = (
            f"Source '{primary_source}' has no usable tracklist for this album"
            if strict_source else
            "No metadata source ID for this album"
        )
        return {
            'status': 'no_source_id', 'source': None, 'api_album': None,
            'total_discs': 1,
            'items': [{
                'track': t, 'api_track': None, 'matched': False,
                'reason': reason,
            } for t in tracks],
        }

    total_discs = max(
        (int(item.get('disc_number') or 1) for item in api_tracks),
        default=1,
    )

    # Pre-normalize once so the matcher doesn't redo the work per track.
    prenormalized = _prenormalize_api_tracks(api_tracks)
    items = []
    for track in tracks:
        api_track = _find_api_track(prenormalized, track.get('title', ''), track.get('track_number'))
        if api_track is None:
            items.append({
                'track': track, 'api_track': None, 'matched': False,
                'reason': f"No matching track in {source} tracklist (likely a bonus / non-canonical track)",
            })
        else:
            items.append({
                'track': track, 'api_track': api_track, 'matched': True,
                'reason': None,
            })

    return {
        'status': 'planned',
        'source': source,
        'api_album': api_album,
        'total_discs': total_discs,
        'items': items,
    }


def _build_post_process_context(
    api_album: dict,
    api_track: dict,
    artist_name: str,
    album_title: str,
    total_discs: int,
) -> dict:
    """Build the same shape `import_album_process` builds so post-process
    treats this exactly like a fresh download with full Spotify-style
    metadata in hand."""
    track_number = int(api_track.get('track_number') or 1)
    disc_number = int(api_track.get('disc_number') or 1)
    track_artists = api_track.get('artists') or [artist_name]
    normalized_artists = [
        ({'name': a} if isinstance(a, str) else a) for a in track_artists
    ]

    api_album_id = api_album.get('id') or api_album.get('album_id') or ''
    api_album_name = api_album.get('name') or api_album.get('title') or album_title
    api_album_release = (
        api_album.get('release_date')
        or api_album.get('releaseDate')
        or ''
    )
    api_album_total_tracks = (
        api_album.get('total_tracks')
        or api_album.get('totalTracks')
        or 0
    )
    # Spotify shape: {'images': [{'url': ...}, ...]}.
    # Deezer shape: {'image_url': '...'}.
    api_album_image = api_album.get('image_url') or ''
    if not api_album_image:
        images = api_album.get('images')
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                api_album_image = first.get('url') or ''

    track_name = api_track.get('name') or api_track.get('title') or ''

    return {
        'spotify_artist': {
            'name': artist_name,
            'id': '',
            'genres': [],
        },
        'spotify_album': {
            'id': api_album_id,
            'name': api_album_name,
            'release_date': api_album_release,
            'total_tracks': api_album_total_tracks,
            'total_discs': total_discs,
            'image_url': api_album_image,
        },
        'track_info': {
            'name': track_name,
            'id': api_track.get('id', ''),
            'track_number': track_number,
            'disc_number': disc_number,
            'duration_ms': api_track.get('duration_ms', 0),
            'artists': normalized_artists,
            'uri': api_track.get('uri', ''),
        },
        'original_search_result': {
            'title': track_name,
            'artist': artist_name,
            'album': api_album_name,
            'track_number': track_number,
            'disc_number': disc_number,
            'spotify_clean_title': track_name,
            'spotify_clean_album': api_album_name,
            'artists': normalized_artists,
        },
        'is_album_download': True,
        'has_clean_spotify_data': True,
        'has_full_spotify_metadata': True,
    }


def preview_album_reorganize(
    *,
    album_id: str,
    db,
    transfer_dir: str,
    resolve_file_path_fn: Callable[[Optional[str]], Optional[str]],
    build_final_path_fn: Callable,
    primary_source: Optional[str] = None,
    strict_source: bool = False,
) -> dict:
    """Compute the planned destination paths for a reorganize WITHOUT
    moving any files. The preview UI uses this to show users what the
    "Apply" run would do.

    Critically: the destination per track comes from
    ``build_final_path_fn(context, spotify_artist, None, file_ext)`` —
    the same shared helper post-processing uses. So the preview is
    guaranteed to match what the orchestrator would actually produce.

    Args:
        album_id: Library album ID.
        db: Database object exposing ``_get_connection()``.
        transfer_dir: Configured transfer directory (for trimming the
            display-relative current-path string).
        resolve_file_path_fn: Resolves a DB-stored file path to the
            actual on-disk path (or ``None`` if missing).
        build_final_path_fn: ``_build_final_path_for_track`` from
            web_server. Signature is
            ``(context, spotify_artist, album_info_or_none, file_ext) -> (path, ok)``.
            Injected so this module stays Flask-free.
        primary_source: Optional override for the configured primary
            source.

    Returns:
        ``{
            'success': bool,
            'status': str,  # 'planned' | 'no_album' | 'no_tracks' | 'no_source_id'
            'source': str | None,
            'album': str,
            'artist': str,
            'transfer_dir': str,
            'tracks': [
                {'track_id', 'title', 'track_number', 'current_path',
                 'new_path', 'file_exists', 'unchanged', 'collision',
                 'matched', 'reason', 'disc_number'},
                ...
            ],
        }``
    """
    album_data, tracks = load_album_and_tracks(db, album_id)
    if album_data is None:
        return {'success': False, 'status': 'no_album', 'tracks': []}

    if not tracks:
        return {
            'success': False, 'status': 'no_tracks',
            'album': album_data.get('title', ''),
            'artist': album_data.get('artist_name', ''),
            'tracks': [],
        }

    plan = plan_album_reorganize(
        album_data, tracks,
        primary_source=primary_source, strict_source=strict_source,
    )
    artist_name = album_data.get('artist_name') or 'Unknown Artist'
    album_title = album_data.get('title') or 'Unknown Album'

    common = {
        'album': album_title,
        'artist': artist_name,
        'transfer_dir': transfer_dir,
        'source': plan['source'],
    }

    if plan['status'] == 'no_source_id':
        return {
            'success': False, 'status': 'no_source_id',
            **common,
            'tracks': [{
                'track_id': t.get('id'),
                'title': t.get('title', ''),
                'track_number': t.get('track_number', 0),
                'current_path': t.get('file_path', ''),
                'new_path': '',
                'file_exists': False, 'unchanged': False, 'collision': False,
                'matched': False,
                'reason': 'No metadata source ID — run enrichment first',
                'disc_number': None,
            } for t in tracks],
        }

    total_discs = plan['total_discs']
    api_album = plan['api_album'] or {}
    preview_tracks = []

    for plan_item in plan['items']:
        track = plan_item['track']
        title = track.get('title', '')
        db_path = track.get('file_path')
        resolved = resolve_file_path_fn(db_path) if db_path else None
        file_ext = os.path.splitext(resolved or db_path or '.flac')[1] or '.flac'

        item = {
            'track_id': track.get('id'),
            'title': title,
            'track_number': track.get('track_number', 0),
            'current_path': _trim_to_transfer(db_path, resolved, transfer_dir),
            'new_path': '',
            'file_exists': resolved is not None,
            'unchanged': False,
            'collision': False,
            'matched': plan_item['matched'],
            'reason': plan_item.get('reason'),
            'disc_number': None,
        }

        if not plan_item['matched']:
            preview_tracks.append(item)
            continue

        api_track = plan_item['api_track']
        item['disc_number'] = int(api_track.get('disc_number') or 1)
        # Build the same context the orchestrator builds so the path
        # builder produces the same destination it would on apply.
        context = _build_post_process_context(
            api_album, api_track, artist_name, album_title, total_discs
        )
        # `_build_final_path_for_track` switches between ALBUM and SINGLE
        # modes based on `album_info.get('is_album')` — must be passed,
        # not None, otherwise multi-disc deluxes degrade to single-track
        # folders (the exact bug winecountrygames hit).
        album_info = _build_album_info(context)
        try:
            spotify_artist = context['spotify_artist']
            new_full, _ok = build_final_path_fn(context, spotify_artist, album_info, file_ext)
            item['new_path'] = (
                os.path.relpath(new_full, transfer_dir)
                if transfer_dir and new_full and new_full.startswith(transfer_dir)
                else new_full or ''
            )
            if resolved and new_full and os.path.normpath(resolved) == os.path.normpath(new_full):
                item['unchanged'] = True
        except Exception as e:
            item['reason'] = f"Couldn't compute destination path: {e}"

        preview_tracks.append(item)

    # Collision detection: multiple matched tracks mapping to the same
    # destination would overwrite each other on apply.
    seen = {}
    for it in preview_tracks:
        if not it['matched'] or it['unchanged'] or not it['new_path']:
            continue
        norm = os.path.normpath(it['new_path'])
        if norm in seen:
            it['collision'] = True
            seen[norm]['collision'] = True
        else:
            seen[norm] = it

    return {
        'success': True, 'status': 'planned',
        **common,
        'tracks': preview_tracks,
    }


def _trim_to_transfer(db_path, resolved, transfer_dir):
    """Compose the user-facing 'current path' string — relative to the
    transfer dir if the file lives there, else the raw DB value."""
    if resolved and transfer_dir and resolved.startswith(transfer_dir):
        return resolved[len(transfer_dir):].lstrip(os.sep).lstrip('/')
    return db_path or 'No file'


def _build_album_info(context: dict) -> dict:
    """Build the ``album_info`` dict that ``_build_final_path_for_track``
    consumes to enter ALBUM MODE. Without this (passing None) the path
    builder falls through to SINGLE MODE and produces per-track folders
    named after each track title — the exact bug we're fixing.

    Mirrors the shape the download path produces at write time.
    """
    spotify_album = context.get('spotify_album', {}) or {}
    track_info = context.get('track_info', {}) or {}
    return {
        'is_album': True,
        'album_name': spotify_album.get('name') or 'Unknown Album',
        'clean_track_name': track_info.get('name') or 'Unknown Track',
        'track_number': track_info.get('track_number') or 1,
        'disc_number': track_info.get('disc_number') or 1,
        'album_image_url': spotify_album.get('image_url') or '',
        'spotify_album_id': spotify_album.get('id') or '',
    }


@dataclass
class _RunContext:
    """Bundles all state + injected dependencies a single
    ``_process_one_track`` call needs.

    Hoisted out of orchestrator-local closures so the per-track
    helpers can be unit-tested directly with a fake ctx, and so a
    stack trace into a failing helper is intelligible (closures
    captured 16+ values, none of which were visible in tracebacks).

    Thread-safety contract — read this before adding new fields:

    - ``state_lock`` MUST be held when mutating any of the
      lock-protected fields below. The provided ``record_error``
      method already takes the lock; direct mutation outside that
      method is the only place where future contributors might
      forget. Add new mutable shared state with the same discipline.

    Lock-protected fields (mutate only inside ``state_lock``):

        summary              dict — counts and errors list
        src_dirs_touched     set — populated by `_finalize_track`
        dst_dirs_touched     set — populated by `_finalize_track`

    Read-only after construction (safe to read without locking):

        album_id, api_album, artist_name, album_title, total_discs,
        staging_album_dir, resolve_file_path_fn, post_process_fn,
        update_track_path_fn, on_progress, stop_check, state_lock

    Side-effecting methods that take the lock internally:

        record_error()       — records a per-track failure
        emit()               — fires on_progress callback (no lock;
                               assumes caller holds it when also
                               passing summary fields, which the
                               record_error and orchestrator-success
                               paths both do)
    """
    album_id: str
    api_album: dict
    artist_name: str
    album_title: str
    total_discs: int
    staging_album_dir: str
    state_lock: threading.Lock              # required to mutate lock-protected fields
    summary: dict                           # LOCK-PROTECTED
    src_dirs_touched: Set[str]              # LOCK-PROTECTED
    dst_dirs_touched: Set[str]              # LOCK-PROTECTED
    resolve_file_path_fn: Callable[[Optional[str]], Optional[str]]
    post_process_fn: Callable[[str, dict, str], None]
    update_track_path_fn: Optional[Callable[[Any, str], None]] = None
    on_progress: Optional[Callable[[dict], None]] = None
    stop_check: Optional[Callable[[], bool]] = None

    def emit(self, **updates) -> None:
        """Fire the progress callback. Caller is responsible for
        holding ``state_lock`` when the updates payload includes
        snapshots of lock-protected fields (so the snapshot is
        coherent). Currently always called from inside the lock by
        ``record_error`` and the orchestrator's success path."""
        if self.on_progress is None:
            return
        try:
            self.on_progress(updates)
        except Exception as e:
            logger.debug("progress emit failed: %s", e)

    def record_error(self, track_id, title, message, kind: str = 'skipped') -> None:
        with self.state_lock:
            self.summary['errors'].append({
                'track_id': track_id,
                'title': title,
                'error': message,
            })
            self.summary[kind] += 1
            self.emit(**{
                kind: self.summary[kind],
                'errors': list(self.summary['errors']),
                'processed': (
                    self.summary['moved']
                    + self.summary['skipped']
                    + self.summary['failed']
                ),
            })


def _stage_track(ctx: _RunContext, track_id, title, resolved_src) -> Optional[str]:
    """Stage a copy of ``resolved_src`` into a per-track UUID
    subdirectory under ``ctx.staging_album_dir``.

    Per-track subdirs are required for concurrent safety: post-process
    calls ``_cleanup_empty_directories`` after each move, which walks
    UP from the source file removing empty dirs. With a shared
    ``staging_album_dir`` that walk would race with other workers'
    in-flight ``makedirs``/``copy2`` calls — worker A finishing could
    nuke the dir between worker B's ``makedirs`` and ``copy2``,
    causing intermittent ``[WinError 3]`` / ``ENOENT`` failures.

    With per-track subdirs:

    - Worker A's cleanup walks: per-track subdir (empty after move →
      removed) → ``staging_album_dir`` (still has other workers'
      subdirs → not empty → walk stops). ✓
    - Worker B's stage-in: makedirs its OWN subdir, copies into
      it. No interference from worker A. ✓
    """
    worker_dir = os.path.join(ctx.staging_album_dir, uuid.uuid4().hex[:8])
    try:
        os.makedirs(worker_dir, exist_ok=True)
    except OSError as mk_err:
        ctx.record_error(track_id, title,
                         f"Couldn't create staging subdirectory: {mk_err}",
                         kind='failed')
        return None
    staging_file = os.path.join(worker_dir, os.path.basename(resolved_src))
    try:
        shutil.copy2(resolved_src, staging_file)
    except OSError as copy_err:
        ctx.record_error(track_id, title,
                         f"Couldn't copy to staging: {copy_err}",
                         kind='failed')
        return None
    return staging_file


def _run_post_process_for_track(ctx: _RunContext, track_id, title, api_track, staging_file) -> Optional[str]:
    """Build the per-track context, hand it to post-processing, and
    return the final on-disk path it produced. Returns None on any
    failure (exception, AcoustID rejection, internal skip); the caller
    leaves the original file alone."""
    context = _build_post_process_context(
        ctx.api_album, api_track, ctx.artist_name, ctx.album_title, ctx.total_discs
    )
    context_key = f"reorganize_{ctx.album_id}_{track_id}_{uuid.uuid4().hex[:8]}"
    try:
        ctx.post_process_fn(context_key, context, staging_file)
    except Exception as pp_err:
        ctx.record_error(track_id, title,
                         f"Post-processing failed: {pp_err}",
                         kind='failed')
        return None
    new_path = context.get('_final_processed_path')
    if not new_path or not os.path.exists(new_path):
        ctx.record_error(track_id, title,
                         'Post-processing did not produce a final file '
                         '(AcoustID rejection, quarantine, or skip).',
                         kind='failed')
        return None
    return new_path


def _finalize_track(ctx: _RunContext, track_id, resolved_src, new_path) -> bool:
    """Update the DB row, then remove the original (in that order — DB
    failure leaves the file at both locations, recoverable by library
    scan; the reverse would orphan the row). Records src/dst dirs for
    end-of-run cleanup, deletes per-track sidecars.

    Returns ``True`` if the track is fully landed (DB row points to
    ``new_path`` AND the original is dealt with), ``False`` if DB
    update failed. Caller MUST treat False as a failure for counting
    purposes — the file is at both locations, the DB still points to
    the old path, and counting it as "moved" overstates how many
    tracks the user can actually find via the UI."""
    if ctx.update_track_path_fn:
        try:
            ctx.update_track_path_fn(track_id, new_path)
        except Exception as db_err:
            logger.warning(
                f"[Reorganize] DB path update failed for {track_id}: {db_err} "
                f"— leaving original at {resolved_src} so the library scan can recover."
            )
            return False
    if os.path.normpath(resolved_src) == os.path.normpath(new_path):
        return True  # in-place edit; DB already correct, nothing to remove
    with ctx.state_lock:
        ctx.src_dirs_touched.add(os.path.dirname(resolved_src))
        ctx.dst_dirs_touched.add(os.path.dirname(new_path))
    try:
        os.remove(resolved_src)
    except OSError as rm_err:
        logger.warning(f"[Reorganize] Couldn't remove original {resolved_src}: {rm_err}")
    _delete_track_sidecars(resolved_src)
    return True


def _process_one_track(ctx: _RunContext, plan_item: dict) -> None:
    """Process a single plan item end-to-end. Safe to call concurrently
    from multiple workers — all shared-state mutations go through
    ``ctx.state_lock`` (via ``record_error`` and ``_finalize_track``)."""
    if ctx.stop_check and ctx.stop_check():
        return
    track = plan_item['track']
    title = track.get('title', 'Unknown')
    track_id = track.get('id')
    ctx.emit(current_track=title)

    if not plan_item['matched']:
        ctx.record_error(track_id, title,
                         plan_item.get('reason') or 'No matching API track')
        return

    db_path = track.get('file_path')
    resolved_src = ctx.resolve_file_path_fn(db_path) if db_path else None
    if not resolved_src:
        ctx.record_error(track_id, title,
                         f"File not found on disk — DB path: {db_path or '(empty)'}")
        return

    staging_file = _stage_track(ctx, track_id, title, resolved_src)
    if staging_file is None:
        return

    new_path = _run_post_process_for_track(ctx, track_id, title, plan_item['api_track'], staging_file)
    if new_path is None:
        return

    finalized = _finalize_track(ctx, track_id, resolved_src, new_path)
    if not finalized:
        # File landed at new_path but DB row + original-removal didn't.
        # User can still find the track (library scan will re-index from
        # new_path), but we can't honestly count it as "moved" — that
        # would overstate how many tracks the UI knows are at their new
        # locations. Surfacing as failed lets the user see something
        # needs attention (per kettui's PR #377 review).
        ctx.record_error(
            track_id, title,
            'Track landed at new location but DB update failed — '
            'file is at both old and new paths until library scan re-indexes.',
            kind='failed',
        )
        return

    with ctx.state_lock:
        ctx.summary['moved'] += 1
        ctx.emit(
            moved=ctx.summary['moved'],
            processed=ctx.summary['moved'] + ctx.summary['skipped'] + ctx.summary['failed'],
        )


def reorganize_album(
    *,
    album_id: str,
    db,
    staging_root: str,
    resolve_file_path_fn: Callable[[Optional[str]], Optional[str]],
    post_process_fn: Callable[[str, dict, str], None],
    update_track_path_fn: Optional[Callable[[object, str], None]] = None,
    cleanup_empty_dir_fn: Optional[Callable[[str], None]] = None,
    transfer_dir: Optional[str] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    primary_source: Optional[str] = None,
    strict_source: bool = False,
    stop_check: Optional[Callable[[], bool]] = None,
) -> dict:
    """Run a single album through the post-processing pipeline.

    See module docstring for the rationale. Dependencies (file
    resolution, post-processing, DB-path update, empty-dir cleanup)
    are injected so the orchestrator stays in ``core/`` and is unit
    testable without spinning up the Flask app.

    Args:
        album_id: Library album ID.
        db: Database object exposing ``_get_connection()``.
        staging_root: Root staging directory under the user's download
            path. A per-album subfolder is created beneath it; the
            whole subfolder is removed at the end of the run.
        resolve_file_path_fn: Resolves a DB-stored file path to the
            actual on-disk path (or ``None`` if missing). Injected
            because the resolution logic lives in ``web_server``.
        post_process_fn: ``_post_process_matched_download``. Must set
            ``context['_final_processed_path']`` on success.
        update_track_path_fn: Called as
            ``update_track_path_fn(track_id, new_path)`` after each
            successful post-process to update the DB row. ``None`` to
            skip (e.g. in tests).
        cleanup_empty_dir_fn: Called with each source directory we
            emptied so the caller can prune empty parents. ``None`` to
            skip.
        on_progress: Optional callback for live status updates.
            Receives a dict with any subset of the standard reorganize
            state keys (``current_track``, ``processed``, ``moved``,
            ``skipped``, ``failed``, ``errors``).
        primary_source: Override for the configured primary source.
            Defaults to ``get_primary_source()``.
        stop_check: Returns True when the caller wants the reorganize
            to abort early (e.g. server shutdown).

    Returns:
        Status summary dict with ``status`` ∈ ``{'completed',
        'no_album', 'no_tracks', 'no_source_id'}`` plus per-track
        counters.
    """
    summary = {
        'status': 'completed',
        'source': None,
        'total': 0,
        'moved': 0,
        'skipped': 0,
        'failed': 0,
        'errors': [],
    }

    state_lock = threading.Lock()

    def _emit(**updates):
        if on_progress is None:
            return
        try:
            on_progress(updates)
        except Exception as e:
            logger.debug("reorganize progress callback failed: %s", e)

    # Load album + tracks
    album_data, tracks = load_album_and_tracks(db, album_id)
    if album_data is None:
        summary['status'] = 'no_album'
        return summary

    if not tracks:
        summary['status'] = 'no_tracks'
        return summary

    summary['total'] = len(tracks)
    _emit(total=len(tracks))

    # Build the per-track plan (same logic the preview uses).
    plan = plan_album_reorganize(
        album_data, tracks,
        primary_source=primary_source, strict_source=strict_source,
    )
    if plan['status'] == 'no_source_id':
        summary['status'] = 'no_source_id'
        summary['errors'].append({
            'error': (
                f"No reachable metadata source ID for '{album_data.get('title', '?')}' — "
                "run enrichment first to populate at least one of "
                "spotify_album_id / itunes_album_id / deezer_id / discogs_id / soul_id."
            ),
        })
        return summary

    source = plan['source']
    api_album = plan['api_album']
    total_discs = plan['total_discs']
    summary['source'] = source
    logger.info(
        f"[Reorganize] Album '{album_data.get('title')}' resolved via {source}: "
        f"{len(plan['items'])} item(s) planned"
    )

    # Per-album staging dir under the configured download path. Cleaned
    # up (best-effort) at the end of the run regardless of outcome.
    artist_name = album_data.get('artist_name') or 'Unknown Artist'
    album_title = album_data.get('title') or 'Unknown Album'
    staging_album_dir = os.path.join(
        staging_root,
        f"{_safe_filename(artist_name)} - {_safe_filename(album_title)}_{uuid.uuid4().hex[:8]}",
    )
    try:
        os.makedirs(staging_album_dir, exist_ok=True)
    except OSError as e:
        summary['status'] = 'setup_failed'
        summary['errors'].append({
            'error': f"Couldn't create staging directory '{staging_album_dir}': {e}",
        })
        return summary

    src_dirs_touched: Set[str] = set()
    dst_dirs_touched: Set[str] = set()

    ctx = _RunContext(
        album_id=str(album_id),
        api_album=api_album or {},
        artist_name=artist_name,
        album_title=album_title,
        total_discs=total_discs,
        staging_album_dir=staging_album_dir,
        state_lock=state_lock,
        summary=summary,
        src_dirs_touched=src_dirs_touched,
        dst_dirs_touched=dst_dirs_touched,
        resolve_file_path_fn=resolve_file_path_fn,
        post_process_fn=post_process_fn,
        update_track_path_fn=update_track_path_fn,
        on_progress=on_progress,
        stop_check=stop_check,
    )

    try:
        # 3 concurrent workers per album — matches the download-side
        # batch worker count. Post-process has its own per-context-key
        # lock so concurrent calls don't race on the same file, and
        # all shared-state mutations here are inside `state_lock`.
        #
        # Wait loop with a periodic watchdog: instead of blocking
        # indefinitely on `as_completed`, we wake every
        # `_WATCHDOG_INTERVAL_SECONDS` so we can react to stop_check
        # promptly AND log a warning if any track has been processing
        # for longer than `_HUNG_WORKER_THRESHOLD_SECONDS`. We can't
        # kill the thread (Python doesn't allow that cleanly), but
        # surfacing it lets operators investigate.
        with ThreadPoolExecutor(
            max_workers=_REORGANIZE_MAX_WORKERS,
            thread_name_prefix='Reorganize',
        ) as executor:
            future_to_item = {
                executor.submit(_process_one_track, ctx, item): item
                for item in plan['items']
            }
            future_started_at = {f: time.monotonic() for f in future_to_item}
            pending = set(future_to_item.keys())
            warned_about: Set[Any] = set()

            while pending:
                if stop_check and stop_check():
                    for f in pending:
                        f.cancel()
                    break

                done, pending = wait(
                    pending,
                    timeout=_WATCHDOG_INTERVAL_SECONDS,
                    return_when=FIRST_COMPLETED,
                )
                for finished in done:
                    try:
                        finished.result()
                    except Exception as worker_err:
                        logger.error(
                            f"[Reorganize] Worker raised: {worker_err}",
                            exc_info=True,
                        )

                # Watchdog pass — log once per stuck future.
                now = time.monotonic()
                for f in pending:
                    if f in warned_about:
                        continue
                    elapsed = now - future_started_at[f]
                    if elapsed >= _HUNG_WORKER_THRESHOLD_SECONDS:
                        item = future_to_item.get(f, {})
                        track_title = (item.get('track') or {}).get('title', 'Unknown')
                        logger.warning(
                            f"[Reorganize] Worker stuck for {elapsed:.0f}s on track "
                            f"'{track_title}' — leaving it running, other workers continuing."
                        )
                        warned_about.add(f)

    finally:
        # Best-effort cleanup of the staging dir.
        try:
            if os.path.isdir(staging_album_dir):
                shutil.rmtree(staging_album_dir, ignore_errors=True)
        except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
            pass

        # Best-effort cleanup of source directories. For each touched dir
        # that has no audio files left (i.e. every track in this dir was
        # successfully moved), delete album-level sidecars (cover.jpg,
        # folder.jpg, etc.) so the dir is empty enough for the empty-dir
        # pruner to take it. If audio remains (a track failed to move),
        # leave everything alone so the user can see what's still there.
        for src_dir in src_dirs_touched:
            try:
                if _has_remaining_audio(src_dir):
                    continue
                _delete_album_sidecars(src_dir)
            except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                pass

        if cleanup_empty_dir_fn:
            for src_dir in src_dirs_touched:
                try:
                    cleanup_empty_dir_fn(src_dir)
                except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                    pass

        # Prune empty *destination* siblings — e.g. when a previous
        # failed reorganize attempt left ``Artist/Album-Sibling/`` dirs
        # behind that we never end up using, OR when a current-run
        # post-process created a destination dir then failed AcoustID
        # before landing the file. Walk up from any successful
        # destination to the artist folder, then prune one level of
        # empty children. Bounded depth = safer than recursive sweep.
        if transfer_dir and dst_dirs_touched:
            artist_dirs = set()
            for dst in dst_dirs_touched:
                artist = _find_artist_dir(dst, transfer_dir)
                if artist:
                    artist_dirs.add(artist)
            for artist_dir in artist_dirs:
                _prune_empty_album_dirs(artist_dir)

    return summary


def _find_artist_dir(dest_path: str, transfer_dir: str) -> Optional[str]:
    """Walk up from ``dest_path`` until the parent equals ``transfer_dir``;
    the directory at that point is the artist folder. Returns None if
    ``dest_path`` isn't inside ``transfer_dir`` at all."""
    if not transfer_dir:
        return None
    transfer_norm = os.path.normpath(transfer_dir)
    cur = os.path.normpath(dest_path)
    while True:
        parent = os.path.dirname(cur)
        if parent == cur:
            return None  # filesystem root
        if os.path.normpath(parent) == transfer_norm:
            return cur
        cur = parent


def _prune_empty_album_dirs(artist_dir: str) -> None:
    """Remove direct subdirectories of ``artist_dir`` that are empty.
    Single-level prune: deliberately doesn't recurse — we want to
    catch leftover album-sibling folders without aggressively touching
    the user's nested directory tree.

    Also walks one level deeper into each album dir to remove empty
    Disc-N subfolders that previous runs may have created."""
    if not os.path.isdir(artist_dir):
        return
    try:
        children = list(os.listdir(artist_dir))
    except OSError:
        return
    for entry in children:
        album_path = os.path.join(artist_dir, entry)
        if not os.path.isdir(album_path):
            continue
        # First pass: prune empty Disc-N subfolders inside this album.
        try:
            for sub in list(os.listdir(album_path)):
                disc_path = os.path.join(album_path, sub)
                if os.path.isdir(disc_path):
                    try:
                        if not os.listdir(disc_path):
                            os.rmdir(disc_path)
                    except OSError:
                        pass
        except OSError:
            pass
        # Then: if the whole album dir is now empty, prune it.
        try:
            if not os.listdir(album_path):
                os.rmdir(album_path)
                logger.info(f"[Reorganize] Pruned empty album dir: {album_path}")
        except OSError:
            pass


# Sidecar / cleanup helpers --------------------------------------------------

# Sidecars that live alongside ONE audio file (same filename stem).
_TRACK_SIDECAR_EXTS = ('.lrc', '.nfo', '.txt', '.cue', '.json')

# Sidecars that live at the ALBUM level (one per directory).
_ALBUM_SIDECARS = (
    'cover.jpg', 'cover.jpeg', 'cover.png',
    'folder.jpg', 'folder.png',
    'front.jpg', 'front.png',
    'album.jpg', 'album.png',
    'artwork.jpg', 'artwork.png',
)

# Audio extensions used to decide whether a source directory still has
# tracks the user might care about (i.e. a per-track failure left audio
# behind that we shouldn't strip the cover art from).
_AUDIO_EXTS = frozenset(
    {'.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.aac', '.wma', '.mp4'}
)


def _delete_track_sidecars(audio_path: str) -> None:
    """Delete per-track sidecars (.lrc / .nfo / .txt / .cue / .json) that
    sit alongside `audio_path` and share its filename stem. Best-effort —
    individual failures are logged at debug and never raised."""
    src_dir = os.path.dirname(audio_path)
    stem = os.path.splitext(os.path.basename(audio_path))[0]
    for ext in _TRACK_SIDECAR_EXTS:
        sidecar = os.path.join(src_dir, stem + ext)
        if os.path.isfile(sidecar):
            try:
                os.remove(sidecar)
            except OSError as e:
                logger.debug(f"[Reorganize] Couldn't remove sidecar {sidecar}: {e}")


def _delete_album_sidecars(src_dir: str) -> None:
    """Delete album-level sidecars (cover.jpg, folder.jpg, etc.) from
    `src_dir`. Used during end-of-run cleanup when no audio files remain
    in the directory. Best-effort — individual failures are debug-logged."""
    for name in _ALBUM_SIDECARS:
        sidecar = os.path.join(src_dir, name)
        if os.path.isfile(sidecar):
            try:
                os.remove(sidecar)
            except OSError as e:
                logger.debug(f"[Reorganize] Couldn't remove album sidecar {sidecar}: {e}")


def _has_remaining_audio(directory: str) -> bool:
    """Return True if `directory` contains any audio files. Used as the
    safety check before stripping album-level sidecars: if a track
    failed to move, leave its cover art and friends in place."""
    if not os.path.isdir(directory):
        return False
    try:
        for name in os.listdir(directory):
            full = os.path.join(directory, name)
            if not os.path.isfile(full):
                continue
            if os.path.splitext(name)[1].lower() in _AUDIO_EXTS:
                return True
    except OSError:
        return True  # Safer to assume "yes, leave it" if we can't check
    return False
