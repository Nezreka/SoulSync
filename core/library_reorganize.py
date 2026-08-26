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

import errno
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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


def _canonical_file_path(path: Any) -> str:
    """Return one comparison form for relative, absolute and symlinked paths.

    The library resolver normally returns an absolute container path while the
    path builder may preserve a configured spelling such as ``./Transfer``.
    ``normpath`` alone leaves those different even when they name the same
    location, which made the old finalizer unlink the user's only copy as the
    supposed source.
    """
    if path in (None, ""):
        return ""
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))
    except (TypeError, ValueError, OSError):
        return ""


def _same_physical_file(left: Any, right: Any) -> bool:
    """Whether two path spellings designate the same physical file/location."""
    left_canonical = _canonical_file_path(left)
    right_canonical = _canonical_file_path(right)
    if not left_canonical or not right_canonical:
        return False
    if left_canonical == right_canonical:
        return True
    try:
        return bool(os.path.exists(left) and os.path.exists(right)
                    and os.path.samefile(left, right))
    except (OSError, TypeError, ValueError):
        return False


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

# Compatibility aliases projected by :func:`load_album_and_tracks`.
_ALBUM_ID_COLUMNS = {
    'spotify': 'spotify_album_id',
    'itunes': 'itunes_album_id',
    'deezer': 'deezer_id',
    'discogs': 'discogs_id',
    'hydrabase': 'soul_id',
    'musicbrainz': 'musicbrainz_release_id',
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


_UNKNOWN_ARTIST_NAMES = {'unknown artist', 'unknown', ''}


def _is_unknown_artist(artist_name: Optional[str]) -> bool:
    if not artist_name:
        return True
    return str(artist_name).strip().lower() in _UNKNOWN_ARTIST_NAMES


def _looks_like_album_id_title(album_title: Optional[str]) -> bool:
    """Pre-#524 manual-import bug left some albums with a numeric
    album_id stored as `albums.title`. Detect that shape so reorganize
    can point the user at Unknown Artist Fixer instead of the generic
    'run enrichment' hint."""
    if not album_title:
        return False
    stripped = str(album_title).strip()
    return len(stripped) >= 6 and stripped.isdigit()


def _unresolvable_reason(album_data: dict, primary_source: str, strict_source: bool) -> str:
    """Reason text for albums reorganize can't place. Surfaces the
    Unknown Artist Fixer hint when the row matches the bad-metadata
    shape (Unknown Artist OR album-id-as-title) — that fixer reads
    file tags + re-resolves metadata, which reorganize itself doesn't
    do."""
    artist = album_data.get('artist_name')
    title = album_data.get('title')
    if _is_unknown_artist(artist) or _looks_like_album_id_title(title):
        return (
            "Album has placeholder metadata (Unknown Artist or numeric "
            "title) — run the 'Fix Unknown Artists' repair job to "
            "recover real artist/album from file tags before reorganize"
        )
    if strict_source:
        return f"Source '{primary_source}' has no usable tracklist for this album"
    return "No metadata source ID for this album"


# #767-2: a walked edition scoring below this against the on-disk files is treated
# as the WRONG edition (e.g. a 1-track single vs the 10-track deluxe scores 0.1),
# triggering the alternate-edition search. Matches the resolver's min_score.
_CANONICAL_FIT_FLOOR = 0.5


def _score_edition_items(file_tracks: List[dict], items: List[dict]) -> float:
    """Score a fetched provider tracklist (raw ``items``) against the on-disk
    ``file_tracks`` using the canonical scorer. Normalises the provider's varied
    shapes (``name``/``title``, ``duration_ms``/``duration`` seconds) first."""
    from core.metadata.canonical_version import score_release_against_files
    rel = []
    for it in items or []:
        dur = it.get('duration_ms')
        if dur is None:
            secs = it.get('duration')
            dur = int(secs * 1000) if isinstance(secs, (int, float)) and secs else None
        rel.append({'title': it.get('name') or it.get('title') or '', 'duration_ms': dur})
    return score_release_against_files(file_tracks, rel) if rel else 0.0


def _resolve_better_edition(album_data, source_ids, file_tracks, primary_source):
    """Misfit path: run the canonical resolver WITH alternate-edition expansion and,
    if it lands on a genuinely different edition than the linked ones, fetch it for
    organizing. Returns ``(source, album_id, api_album, items, score)`` or ``None``."""
    from core.metadata.canonical_resolver import (
        default_fetch_alternates,
        default_fetch_tracklist,
        resolve_canonical_for_album,
    )
    art_id = str(album_data.get('artist_id') or '')
    art_name = album_data.get('artist_name') or ''
    title = album_data.get('title') or ''

    def _alts(source, aid):
        return default_fetch_alternates(
            source, aid, artist_id=art_id, artist_name=art_name, album_title=title,
        )

    try:
        result = resolve_canonical_for_album(
            album_source_ids=source_ids,
            file_tracks=file_tracks,
            fetch_tracklist=default_fetch_tracklist,
            fetch_alternates=_alts,
            source_priority=get_source_priority(primary_source),
            primary_source=primary_source,
        )
    except Exception as e:
        logger.warning(f"[Reorganize] canonical resolve raised: {e}")
        return None
    if not result:
        return None
    linked = source_ids.get(result['source'])
    if str(result['album_id']) == str(linked or ''):
        return None  # resolver chose a linked edition the walk already considered
    try:
        b_album = get_album_for_source(result['source'], result['album_id'])
        b_items = _normalize_album_tracks(
            get_album_tracks_for_source(result['source'], result['album_id'])
        )
    except Exception as e:
        logger.warning(f"[Reorganize] alternate edition fetch raised: {e}")
        return None
    if not b_album or not b_items:
        return None
    return result['source'], result['album_id'], b_album, b_items, result.get('score') or 0.0


def _resolve_source(
    album_data: dict, primary_source: str, strict_source: bool = False,
    *, file_tracks: Optional[List[dict]] = None, on_better_edition=None,
):
    """Walk the configured source priority looking for the first source
    we have an ID for AND that returns a usable tracklist.

    When ``strict_source`` is True, only the caller-provided
    ``primary_source`` is tried — no fallback. Used when the user has
    explicitly picked a source in the reorganize modal: picking Spotify
    means "use Spotify or fail", not "use Spotify and silently fall
    back to Deezer".

    When ``file_tracks`` is supplied (and not ``strict_source``), the walked
    edition is fit-scored against the on-disk files; a clear misfit triggers an
    alternate-edition search (#767-2). ``on_better_edition(source, album_id,
    score)`` is invoked to persist the pin when a better edition is chosen.

    Returns ``(source_name, album_meta, tracks_list)`` or ``(None, None, None)``.
    """
    source_ids = _extract_source_ids(album_data)

    # #765: if a canonical release was pinned for this album (best-fit to the
    # user's actual files), prefer it — so reorganize agrees with Track Number
    # Repair and stops mislabelling standard albums as deluxe (#767-Bug2). Gated
    # on the album row carrying a canonical, and skipped when the user explicitly
    # picked a source in the modal (strict_source) — their choice wins. Falls
    # through to the priority walk if the canonical fetch fails.
    if not strict_source:
        c_source = album_data.get('canonical_source')
        c_id = album_data.get('canonical_album_id')
        if c_source and c_id:
            try:
                api_album = get_album_for_source(c_source, c_id)
                api_tracks = get_album_tracks_for_source(c_source, c_id)
                items = _normalize_album_tracks(api_tracks)
                if items and api_album:
                    return c_source, api_album, items
            except Exception as e:
                logger.warning(f"[Reorganize] canonical {c_source} lookup raised: {e}")

    if strict_source:
        sources_to_try = [primary_source] if primary_source else []
    else:
        sources_to_try = get_source_priority(primary_source)

    walk_source = walk_album = walk_items = None
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
        walk_source, walk_album, walk_items = source, api_album, items
        break

    # #767-2: the walk takes the first source we have an ID for, but that ID can
    # point at the WRONG edition (a single enriched against the deluxe → it'd file
    # the track as #2 of a 10-track album). With the on-disk tracklist in hand,
    # fit-score the walked edition; only a clear misfit looks for a better-fitting
    # edition. Well-fitting albums keep today's exact behavior + make no extra calls.
    if not strict_source and file_tracks:
        walk_fit = _score_edition_items(file_tracks, walk_items) if walk_items else 0.0
        if walk_fit < _CANONICAL_FIT_FLOOR:
            better = _resolve_better_edition(
                album_data, source_ids, file_tracks, primary_source,
            )
            if better is not None:
                b_source, b_id, b_album, b_items, b_score = better
                if on_better_edition:
                    try:
                        on_better_edition(b_source, b_id, b_score)
                    except Exception as e:
                        logger.warning(f"[Reorganize] canonical pin persist failed: {e}")
                logger.info(
                    "[Reorganize] %s: walked edition fit %.2f below floor — using "
                    "better-fit %s edition %s (fit %.2f)",
                    album_data.get('title', '?'), walk_fit, b_source, b_id, b_score,
                )
                return b_source, b_album, b_items

    if walk_source:
        return walk_source, walk_album, walk_items
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


# Featured-artist credit: "(feat. X)" / "[ft X]" / a trailing "feat. X". The
# parenthesised form is stripped wherever it appears; the bare form only when
# something follows it (so a song literally named "The Feat" is left alone, and
# "Defeat"/"Lift" never trip the word-boundary). Case-insensitive.
_FEAT_RE = re.compile(
    r"""\s*[\(\[]\s*(?:feat|ft|featuring)\b\.?[^)\]]*[\)\]]   # (feat. X) / [ft. X]
        | \s+(?:feat|ft|featuring)\b\.?\s+\S.*$               # trailing  feat. X ...
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Detection only (does a title carry ANY feat credit?) — word-boundary so
# "Defeat"/"Lift" never trip it. Used to avoid double-crediting.
_FEAT_DETECT_RE = re.compile(r"\b(?:feat|ft|featuring)\b", re.IGNORECASE)


def _feat_in_title_enabled() -> bool:
    """Whether the user asked featured artists to live in the track title
    (Settings → Metadata). Read live so the reorganize honors the same
    switch the download path does. Isolated in a helper so tests can
    monkeypatch it without a full config manager."""
    try:
        from core.settings import config_manager
        return bool(config_manager.get("metadata_enhancement.tags.feat_in_title", False))
    except Exception:
        return False


# A folder the organizer itself writes for one disc of a multi-disc release:
# "Disc 1" / "CD 1" (the `file_organization.disc_label` setting) and "CD01"
# (the `$cdnum` template variable). Anchored and numeric so a real album called
# "Discovery" or an artist called "CD" is never mistaken for one.
_DISC_FOLDER_RE = re.compile(r'^(disc|disk|cd|volume|vol)\s*\.?\s*\d+$', re.IGNORECASE)


def _already_organized_by_disc(tracks) -> bool:
    """Do the user's files already sit in per-disc folders?

    The single-disc cap below reads the user's layout from their track NUMBERS,
    which cannot distinguish "a single-disc edition mis-matched against a deluxe"
    (#1080) from "a multi-disc album that is still downloading". A part-downloaded
    box set has only disc 1 on disk, uniquely numbered and inside disc 1 — exactly
    what the cap keys on — so Reorganize proposed moving the album straight back
    out of the disc folders the download pipeline had just created, and flipped it
    back again once disc 2 arrived.

    The files settle it. SoulSync only writes a disc folder when the release IS
    multi-disc, so an album already living in one is organized, not mis-matched,
    and the setting gating this cap is "preserve my organization".
    """
    for track in tracks or []:
        path = str(track.get('file_path') or '').replace('\\', '/')
        if not path:
            continue                      # a missing file carries no evidence
        parent = os.path.basename(os.path.dirname(path))
        if parent and _DISC_FOLDER_RE.match(parent):
            return True
    return False


def _preserve_casing_enabled() -> bool:
    """Whether the reorganize leaves a title/album alone when the metadata
    source differs from the user's file only by letter-case (#1078 QT3496:
    already-organized files were flagged for cosmetic re-casing). Default on.
    Isolated so tests can monkeypatch without a config manager."""
    try:
        from core.settings import config_manager
        return bool(config_manager.get("library.reorganize_preserve_casing", True))
    except Exception:
        return True


def _keep_user_year(api_release_date, user_year):
    """Prefer the user's own album year over the source's original-release
    year when preserving is on (#1080 QT3496: a file imported as [2023] — a
    reissue/edition year the user chose — was being 'corrected' to the
    source's 2020 original). Returns a release_date string the path builder
    reads for $year; falls back to the source value."""
    if not _preserve_casing_enabled():
        return api_release_date
    uy = str(user_year or "").strip()
    if len(uy) == 4 and uy.isdigit():
        src_year = str(api_release_date or "")[:4]
        if uy != src_year:
            return uy
    return api_release_date


def _keep_user_casing(source_value, user_value):
    """Return the USER's string when it matches the source only by case, else
    the source string. Case-only means identical after casefold — so genuine
    edits (punctuation, words, feat additions) still adopt the source; only
    cosmetic capitalization churn is suppressed."""
    if not _preserve_casing_enabled():
        return source_value
    s = str(source_value or "")
    u = str(user_value or "")
    if u and s and s != u and s.strip().casefold() == u.strip().casefold():
        return u
    return source_value


def _extract_feat_credit(title: str) -> str:
    """The '(feat. X)' credit substring from a title (leading space trimmed),
    or '' when there's none. Lets us carry a user's own credit forward when
    the API only knows the primary artist."""
    if not title:
        return ''
    m = _FEAT_RE.search(str(title))
    return m.group(0).strip() if m else ''


def _apply_feat_credit(track_name: str, normalized_artists: list, local_title: str) -> str:
    """#1078: when feat_in_title is on, make sure the clean title the
    reorganize builds carries the featured-artist credit — so the FILENAME
    keeps it too (the tag writer re-adds it for the tag, but the filename is
    built straight from this clean title and was dropping "(feat. X)").

    Precedence when the API's own track name has no credit:
      1. featured artists from the API track's artist list (canonical names),
      2. else the credit already present in the user's file title (the API
         only knows the primary — don't strip what the user curated).
    A track name that already carries a credit is left untouched."""
    name = str(track_name or '')
    if _FEAT_DETECT_RE.search(name):
        return name
    featured = [
        (a.get('name') if isinstance(a, dict) else str(a))
        for a in (normalized_artists[1:] if normalized_artists else [])
    ]
    featured = [f for f in featured if f]
    if featured:
        return f"{name} (feat. {', '.join(featured)})".strip()
    credit = _extract_feat_credit(local_title)
    if credit:
        return f"{name} {credit}".strip()
    return name


def _normalize_title(value) -> str:
    """Lowercase + strip cosmetic punctuation and treat brackets / dashes
    / slashes as word separators so the same track named slightly
    differently across providers and user libraries still matches.

    Examples that should normalize equal:

    - ``Bitch, Don't Kill My Vibe - Remix``  ↔  ``Bitch, Don't Kill My Vibe (Remix)``
    - ``Don't Stop Believin'``               ↔  ``Don’t Stop Believin’``
    - ``Swimming Pools (Drank) - Extended Version``
                                              ↔  ``Swimming Pools (Drank) (Extended Version)``
    - ``The Chase (feat. Big Artist)``       ↔  ``The Chase``  (#914)
    """
    if value is None:
        return ''
    out = str(value).strip()
    # #914: drop featured-artist credits FIRST (while the parens are still here to
    # bound the group). iTunes appends "(feat. X)" to track titles while a user's
    # file is often just "The Chase" — the credit is metadata, not the song's
    # identity, and leaving it in dropped the match ratio below the threshold so
    # correctly-identified tracks reported as "not in the tracklist".
    out = _FEAT_RE.sub('', out).lower()
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
            SELECT al.*, al.primary_artist_id AS artist_id,
                   al.spotify_id AS spotify_album_id,
                   al.musicbrainz_id AS musicbrainz_release_id,
                   json_extract(al.external_ids, '$.itunes') AS itunes_album_id,
                   json_extract(al.external_ids, '$.deezer') AS deezer_id,
                   json_extract(al.external_ids, '$.discogs') AS discogs_id,
                   ar.name AS artist_name
            FROM lib2_albums al
            JOIN lib2_artists ar ON al.primary_artist_id = ar.id
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
            SELECT t.*, ar.name AS artist_name,
                   (SELECT f.path FROM lib2_track_files f
                     WHERE f.track_id=t.id AND f.file_state='active'
                     ORDER BY f.is_primary DESC, f.id LIMIT 1) AS file_path
            FROM lib2_tracks t
            JOIN lib2_albums al ON t.album_id = al.id
            JOIN lib2_artists ar ON al.primary_artist_id = ar.id
            WHERE t.album_id = ?
              AND EXISTS (SELECT 1 FROM lib2_track_files f
                           WHERE f.track_id=t.id AND f.file_state='active')
            ORDER BY COALESCE(t.disc_number, 1), t.track_number, t.id
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


def _plan_from_tags(
    album_data: dict,
    tracks: List[dict],
    resolve_file_path_fn: Optional[Callable[[Optional[str]], Optional[str]]],
) -> dict:
    """Tag-mode planner: build per-track ``api_track`` shapes from each
    file's own embedded metadata instead of a live source API call.

    Per-track behavior:
    - File missing on disk → unmatched with reason.
    - Tags missing essentials (title / artist / album) → unmatched
      with reason.
    - Otherwise matched with the per-file extracted ``api_track`` and
      a per-file ``api_album``. The plan stores the FIRST matched
      track's album dict on the top-level ``api_album`` field for
      backward compatibility with downstream callers; downstream
      consumers that need the per-track album shape read it off
      ``items[i]['api_album']``.

    Returns the same status / source / api_album / total_discs / items
    shape as :func:`plan_album_reorganize`. ``source`` is the literal
    string ``'tags'`` so callers can distinguish from API sources."""
    if resolve_file_path_fn is None:
        # Without the file-path resolver we can't read anything off
        # disk. Return an unmatched plan so callers surface a clear
        # error instead of silently returning empty.
        reason = 'Tag-mode reorganize requires the file path resolver.'
        return {
            'status': 'no_source_id', 'source': None, 'api_album': None,
            'total_discs': 1,
            'items': [{
                'track': t, 'api_track': None, 'matched': False,
                'reason': reason,
            } for t in tracks],
        }

    from core.library.reorganize_tag_source import read_album_track_from_file

    items: List[dict] = []
    first_album_meta: Optional[dict] = None
    max_disc = 1

    for track in tracks:
        db_path = track.get('file_path')
        resolved = resolve_file_path_fn(db_path) if db_path else None
        if not resolved:
            items.append({
                'track': track, 'api_track': None, 'api_album': None,
                'matched': False,
                'reason': 'File no longer exists on disk for this track.',
            })
            continue

        album_meta, track_meta, err = read_album_track_from_file(resolved)
        if err is not None or track_meta is None or album_meta is None:
            items.append({
                'track': track, 'api_track': None, 'api_album': None,
                'matched': False,
                'reason': err or 'Could not extract metadata from embedded tags.',
            })
            continue

        if first_album_meta is None:
            first_album_meta = album_meta
        try:
            disc = int(track_meta.get('disc_number') or 1)
        except (TypeError, ValueError):
            disc = 1
        if disc > max_disc:
            max_disc = disc
        # Respect an explicit `totaldiscs` tag (or "1/2" disc-number
        # form) so a partial-album reorganize (only disc 1 present
        # locally) still routes into `Disc 1/` when the file's tags
        # know there are 2 discs total.
        try:
            tagged_total = int(album_meta.get('total_discs') or 0)
        except (TypeError, ValueError):
            tagged_total = 0
        if tagged_total > max_disc:
            max_disc = tagged_total

        items.append({
            'track': track,
            'api_track': track_meta,
            'api_album': album_meta,
            'matched': True,
            'reason': None,
        })

    if not any(it['matched'] for it in items):
        return {
            'status': 'no_source_id',
            'source': 'tags',
            'api_album': None,
            'total_discs': 1,
            'items': items,
        }

    return {
        'status': 'planned',
        'source': 'tags',
        'api_album': first_album_meta or {},
        'total_discs': max_disc,
        'items': items,
    }


def plan_album_reorganize(
    album_data: dict,
    tracks: List[dict],
    primary_source: Optional[str] = None,
    strict_source: bool = False,
    metadata_source: str = 'api',
    resolve_file_path_fn: Optional[Callable[[Optional[str]], Optional[str]]] = None,
    on_better_edition: Optional[Callable[[str, str, float], None]] = None,
) -> dict:
    """Compute the per-track plan for an album reorganize without doing
    any file IO. Both the actual reorganize orchestrator and the preview
    endpoint share this so the preview is guaranteed to match what would
    happen on apply.

    ``metadata_source``:
        - ``'api'`` (default): query the configured metadata source(s)
          for the canonical tracklist (existing behavior). Issues an
          API call.
        - ``'tags'``: read each file's embedded tags as the source of
          truth (issue #592). Zero API calls; trusts the user's
          enriched library.

    When ``metadata_source='tags'``, ``resolve_file_path_fn`` MUST be
    provided (the planner needs to read the actual files). The
    ``primary_source`` and ``strict_source`` params are ignored in
    tag mode.

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

    if metadata_source == 'tags':
        return _plan_from_tags(album_data, tracks, resolve_file_path_fn)

    if primary_source is None:
        try:
            primary_source = get_primary_source()
        except Exception:
            primary_source = 'deezer'

    # On-disk track shape for the #767-2 fit check (duration stored in ms).
    file_tracks = [
        {'duration_ms': t.get('duration') or 0, 'title': t.get('title') or ''}
        for t in tracks
    ]
    source, api_album, api_tracks = _resolve_source(
        album_data, primary_source, strict_source=strict_source,
        file_tracks=file_tracks, on_better_edition=on_better_edition,
    )
    if not source:
        reason = _unresolvable_reason(album_data, primary_source, strict_source)
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

    # #1080 (QT3496): a SINGLE-disc user album re-matched against a MULTI-disc
    # source edition (deluxe / 2-disc) picks up disc-2 track numbers and stamps
    # a bogus disc prefix ("11" -> "0211"). Read the user's REAL layout from
    # their own track numbers and, when it's unambiguously single-disc, organize
    # by that instead of the source's disc structure. Conservative on purpose —
    # only caps when BOTH hold, so genuine multi-disc is never flattened:
    #   * the user's track numbers don't repeat  → single disc (a box set
    #     numbers per-disc, so 1..13 / 1..14 REPEAT → left multi-disc, #1009);
    #   * every user track fits within the source's disc 1  → a continuously-
    #     numbered 2-disc set (1..25) spills past disc 1 → left multi-disc.
    # Gated on the same preserve-my-organization setting as casing/year.
    if (total_discs > 1 and _preserve_casing_enabled()
            and not _already_organized_by_disc(tracks)):
        try:
            user_nums = [int(t.get('track_number')) for t in tracks
                         if str(t.get('track_number') or '').strip().isdigit()]
            api_disc1 = sum(1 for t in api_tracks if int(t.get('disc_number') or 1) == 1)
            if (user_nums and len(user_nums) == len(set(user_nums))
                    and api_disc1 and max(user_nums) <= api_disc1):
                total_discs = 1
                for item in items:
                    if item.get('matched') and item.get('api_track'):
                        # shallow copy — override only the disc, keep name/track/artists
                        item['api_track'] = {**item['api_track'], 'disc_number': 1}
        except Exception:
            # never let the single-disc heuristic break the reorganize — on any
            # odd data just fall back to the source's disc structure
            logger.debug("single-disc cap skipped (unexpected track data)", exc_info=True)

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
    local_title: Optional[str] = None,
    local_year: Optional[str] = None,
) -> dict:
    """Build the same shape `import_album_process` builds so post-process
    treats this exactly like a fresh download with full Spotify-style
    metadata in hand.

    ``local_title`` is the user's own current track title — used only to
    carry a featured-artist credit forward when feat_in_title is on and the
    API doesn't supply one (#1078)."""
    track_number = int(api_track.get('track_number') or 1)
    disc_number = int(api_track.get('disc_number') or 1)
    track_artists = api_track.get('artists') or [artist_name]
    normalized_artists = [
        ({'name': a} if isinstance(a, str) else a) for a in track_artists
    ]

    api_album_id = api_album.get('id') or api_album.get('album_id') or ''
    api_album_name = api_album.get('name') or api_album.get('title') or album_title
    # #1078: keep the user's album-folder casing when the source differs only
    # by case (album_title is the user's own library album name).
    api_album_name = _keep_user_casing(api_album_name, album_title)
    api_album_release = (
        api_album.get('release_date')
        or api_album.get('releaseDate')
        or ''
    )
    # #1080: keep the user's own album year ($year) — a reissue/edition year
    # they imported with, not the source's original-release year.
    api_album_release = _keep_user_year(api_album_release, local_year)
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
    # #1078: keep the featured-artist credit on the CLEAN title when the user
    # asked for feat-in-title. The tag writer re-adds it to the tag, but the
    # filename is built straight from this clean title and was silently
    # dropping "(feat. X)" — flagging already-correct files for "correction".
    if _feat_in_title_enabled():
        track_name = _apply_feat_credit(track_name, normalized_artists, local_title or '')
    # #1078: keep the user's own title casing when the source title differs
    # ONLY by case — no cosmetic rename/re-tag on already-organized files.
    # Runs AFTER feat so "Song (feat. X)" vs a bare source title stays a real
    # change; this only collapses pure capitalization differences. Both the
    # filename and the title tag are built from this string, so they agree.
    track_name = _keep_user_casing(track_name, local_title or '')

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
            # Reorganize is the caller that really knows: it counted the discs
            # off the source tracklist it just resolved, so the path builder must
            # not go and ask a provider again (that lookup's success or failure
            # would decide the destination).
            'total_discs_declared': True,
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
        # A reorganize processes the user's OWN library files, not slskd
        # transfers — same as the Import page (#804). Skips the integrity
        # check's duration-agreement leg: the re-resolved API tracklist can
        # legitimately disagree with the user's copy (a different master /
        # long version), and that mismatch was QUARANTINING a copy of a file
        # that stayed happily in the library (TheHomeGuy: 'Through Glass'
        # 283s vs Discogs' 241s). Size + parse corruption legs still run.
        'is_local_import': True,
        # This pipeline run mutates an EXISTING library file. The reorganize
        # runner owns its one catalogue path update after the destination is
        # proven present; ordinary download registration would insert a second
        # lib2_track_files row for the same track and destination.
        '_library_reorganize': True,
        # ...and for the same reason, the AcoustID identity leg does not get to
        # quarantine this file. A reorganize stages a COPY of a track the user
        # ALREADY OWNS and runs it through the download post-process; when the
        # fingerprint disagreed, that leg moved the copy into ss_quarantine and
        # the whole run reported `failed`, so a rename that should have been a
        # no-op only worked on a second attempt with "Rename only" ticked. The
        # disagreement is routinely legitimate — a different master, a regional
        # release, or an artist credited in another script (Sawano Hiroyuki
        # fingerprints as 澤野弘之). Identity of files already in the library is
        # the AcoustID Scanner's job, and that one raises a finding instead of
        # moving anyone's audio. The size and parse-corruption legs still run:
        # skipping identity is not skipping safety.
        '_skip_quarantine_check': 'acoustid',
        # Reorganize destinations must come from the CURRENT template alone.
        # The #829 existing-folder reuse would resolve to the folder the album
        # already lives in — the very folder reorganize is trying to move it
        # out of — so preview computed "unchanged" for every already-together
        # album and both the Tools job and Reorganize All silently no-opped
        # after a template change.
        '_no_album_folder_reuse': True,
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
    metadata_source: str = 'api',
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
        metadata_source=metadata_source,
        resolve_file_path_fn=resolve_file_path_fn,
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
            # Absolute on-disk paths (additive). `current_path`/`new_path` above are
            # display-trimmed; these carry the real paths so the rename-only executor
            # acts on EXACTLY what the preview computed — no separate path logic that
            # could drift from what the user saw (#875).
            'current_path_abs': resolved or '',
            'new_path_abs': '',
            'file_exists': resolved is not None,
            'unchanged': False,
            'collision': False,
            'matched': plan_item['matched'],
            'reason': plan_item.get('reason'),
            'disc_number': None,
        }

        # #746: never reorganize files sitting in the duplicate-cleaner
        # quarantine (<transfer>/deleted). Surface as a non-matched skip so
        # the preview shows WHY and apply leaves them put. Checked before the
        # matched branch so a quarantined track that happens to match the API
        # tracklist is still skipped.
        if _is_in_deleted_quarantine(resolved, transfer_dir):
            item['matched'] = False
            item['reason'] = 'In deleted/quarantine folder — skipped'
            preview_tracks.append(item)
            continue

        if not plan_item['matched']:
            preview_tracks.append(item)
            continue

        api_track = plan_item['api_track']
        item['disc_number'] = int(api_track.get('disc_number') or 1)
        # Build the same context the orchestrator builds so the path
        # builder produces the same destination it would on apply.
        # Tag-mode plan items carry per-item album metadata; fall back
        # to the shared api_album in API mode (where every plan item
        # shares the same one).
        per_item_album = plan_item.get('api_album') or api_album
        context = _build_post_process_context(
            per_item_album, api_track, artist_name, album_title, total_discs,
            local_title=title,
            local_year=(str(album_data.get('year')) if album_data.get('year') else None),
        )
        # `_build_final_path_for_track` switches between ALBUM and SINGLE
        # modes based on `album_info.get('is_album')` — must be passed,
        # not None, otherwise multi-disc deluxes degrade to single-track
        # folders (the exact bug winecountrygames hit).
        album_info = _build_album_info(context)
        try:
            spotify_artist = context['spotify_artist']
            # Dry run: compute the destination path WITHOUT creating the folder.
            # Previously this physically created the album dir during preview,
            # leaving empty folders all over the library (#767).
            new_full, _ok = build_final_path_fn(
                context, spotify_artist, album_info, file_ext, create_dirs=False
            )
            item['new_path_abs'] = new_full or ''
            item['new_path'] = _display_relative_to_root(new_full, transfer_dir)
            if resolved and new_full and _same_physical_file(resolved, new_full):
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
        norm = _canonical_file_path(it.get('new_path_abs') or it['new_path'])
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


def _is_in_deleted_quarantine(resolved_path, transfer_dir) -> bool:
    """True when ``resolved_path`` lives inside the duplicate-cleaner
    quarantine folder (``<transfer_dir>/.deleted/...``, or the legacy bare
    ``deleted`` spelling older installs still carry).

    The Duplicate Cleaner (``core/library/duplicate_cleaner.py``) moves
    de-duplicated files into the quarantine. If the user's
    media server scans the transfer folder (e.g. a ``/music`` root that
    contains both the library and the transfer dir), those quarantined
    files get real rows in SoulSync's DB — and Reorganize, being purely
    DB-driven, would otherwise dutifully move them back OUT of /deleted
    to the template location. This guard makes Reorganize skip them so
    the quarantine stays quarantined (#746).

    Anchored to the transfer dir specifically so a legitimately
    named artist/album like "Deleted" elsewhere in the library is NOT
    skipped. When ``transfer_dir`` is unavailable we fall back to an exact
    ``deleted`` path-SEGMENT match (mirrors the cleaner's own
    ``if 'deleted' in dirs`` skip) — never a substring, so "Undeleted"
    or "deleted scenes" stay safe.
    """
    if not resolved_path:
        return False

    def _norm(p):
        # normpath collapses redundant separators / '..'; normcase applies
        # the platform's case rule (lowercases on Windows, no-op on posix);
        # fold to '/' so the segment/prefix checks are separator-agnostic.
        return os.path.normcase(os.path.normpath(p)).replace('\\', '/')

    norm = _norm(resolved_path)
    if transfer_dir:
        for name in ('.deleted', 'deleted'):     # hidden spelling + legacy installs
            quarantine = _norm(os.path.join(transfer_dir, name))
            if norm == quarantine or norm.startswith(quarantine + '/'):
                return True
        return False
    return any(seg in ('.deleted', 'deleted') for seg in norm.split('/'))


def _display_relative_to_root(path, root):
    """``path`` shown relative to ``root`` when it lives inside it, else whole.

    A raw ``startswith`` was wrong twice over. It matched a SIBLING root
    (``/music/Transfer2`` starts with ``/music/Transfer``), and it compared two
    strings that different code paths had spelled differently — the proposed
    path came from the path builder rooted at the config value, the current
    path from the resolver (absolute, symlinks resolved). With a relative root
    configured the two never shared a prefix, so the preview trimmed one column
    and printed the raw stored value in the other, for the very same file.
    """
    if not path or not root:
        return path or ''
    p = os.path.normpath(str(path))
    r = os.path.normpath(str(root))
    if p == r:
        return ''
    if p.startswith(r + os.sep) or (os.altsep and p.startswith(r + os.altsep)):
        return p[len(r):].lstrip(os.sep).lstrip('/')
    return path


def _trim_to_transfer(db_path, resolved, transfer_dir):
    """Compose the user-facing 'current path' string — relative to the
    transfer dir if the file lives there, else the raw DB value."""
    if resolved and transfer_dir:
        trimmed = _display_relative_to_root(resolved, transfer_dir)
        if trimmed != resolved:
            return trimmed
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
    local_year: Optional[str]               # the user's stored album year (#1080)
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
    transfer_dir: Optional[str] = None      # anchors the #746 /deleted-quarantine skip

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


def _run_post_process_for_track(ctx: _RunContext, track_id, title, api_track, staging_file, *, per_item_api_album=None) -> Optional[str]:
    """Build the per-track context, hand it to post-processing, and
    return the final on-disk path it produced. Returns None on any
    failure (exception, AcoustID rejection, internal skip); the caller
    leaves the original file alone.

    ``per_item_api_album`` overrides ``ctx.api_album`` for this track —
    used in tag-mode reorganize where each file may carry its own
    embedded album metadata."""
    api_album = per_item_api_album if per_item_api_album else ctx.api_album
    context = _build_post_process_context(
        api_album, api_track, ctx.artist_name, ctx.album_title, ctx.total_discs,
        local_title=title, local_year=ctx.local_year,
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
    if _same_physical_file(resolved_src, new_path):
        return True  # in-place edit; DB already correct, nothing to remove
    with ctx.state_lock:
        ctx.src_dirs_touched.add(os.path.dirname(resolved_src))
        ctx.dst_dirs_touched.add(os.path.dirname(new_path))

    # Discord report (Foxxify): users with lossy-copy enabled have
    # `track.flac` AND `track.opus` side-by-side. The DB tracks ONE
    # (the lossy copy). Reorganize used to move only the canonical
    # and leave the orphan behind, blocking empty-folder cleanup.
    # Move sibling-format audio to the same destination dir BEFORE
    # removing the canonical source, preserving both formats with
    # the canonical's renamed stem.
    siblings = _find_sibling_audio_files(resolved_src)
    for sibling_src in siblings:
        moved_to = _move_sibling_to_destination(sibling_src, new_path)
        if moved_to:
            logger.debug(
                "[Reorganize] Moved sibling-format file alongside canonical: %s",
                moved_to,
            )

    try:
        os.remove(resolved_src)
    except OSError as rm_err:
        logger.warning(f"[Reorganize] Couldn't remove original {resolved_src}: {rm_err}")
    _carry_track_sidecars(resolved_src, new_path)
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

    # #746: leave duplicate-cleaner quarantine files (<transfer>/deleted)
    # where they are. Matches the preview's skip so apply never yanks a file
    # back out of /deleted. (Mirrors the preview guard in
    # preview_album_reorganize.)
    if _is_in_deleted_quarantine(resolved_src, ctx.transfer_dir):
        ctx.record_error(track_id, title,
                         'In deleted/quarantine folder — skipped')
        return

    staging_file = _stage_track(ctx, track_id, title, resolved_src)
    if staging_file is None:
        return

    new_path = _run_post_process_for_track(
        ctx, track_id, title, plan_item['api_track'], staging_file,
        per_item_api_album=plan_item.get('api_album'),
    )
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
    metadata_source: str = 'api',
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

    # #767-2: persist the canonical pin when the resolver lands on a better-fit
    # edition than the linked one, so Track Number Repair + future runs agree and
    # we don't re-resolve every time. Never overrides a manually-locked pin (the
    # set_album_canonical SQL guard from #758 enforces that).
    def _persist_canonical(source, alt_album_id, score):
        try:
            db.set_album_canonical(album_id, source, alt_album_id, score)
        except Exception as e:
            logger.warning("[Reorganize] set_album_canonical failed: %s", e)

    # Build the per-track plan (same logic the preview uses).
    plan = plan_album_reorganize(
        album_data, tracks,
        primary_source=primary_source, strict_source=strict_source,
        metadata_source=metadata_source,
        resolve_file_path_fn=resolve_file_path_fn,
        on_better_edition=_persist_canonical,
    )
    if plan['status'] == 'no_source_id':
        summary['status'] = 'no_source_id'
        summary['source'] = plan.get('source')  # 'tags' or None
        if plan.get('source') == 'tags':
            err_text = (
                f"No tracks of '{album_data.get('title', '?')}' have readable "
                "embedded tags (missing title / artist / album, or file unreadable). "
                "Switch back to API mode or fix the embedded tags first."
            )
        elif _is_unknown_artist(album_data.get('artist_name')) or _looks_like_album_id_title(album_data.get('title')):
            err_text = (
                f"Album '{album_data.get('title', '?')}' has placeholder metadata "
                "(Unknown Artist or numeric title) — run the 'Fix Unknown Artists' "
                "repair job to recover real artist/album from file tags first."
            )
        else:
            err_text = (
                f"No reachable metadata source ID for '{album_data.get('title', '?')}' — "
                "run enrichment first to populate at least one of "
                "spotify_album_id / itunes_album_id / deezer_id / discogs_id / soul_id."
            )
        summary['errors'].append({'error': err_text})
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
        local_year=(str(album_data.get('year')) if album_data.get('year') else None),
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
        transfer_dir=transfer_dir,
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

        for src_dir in src_dirs_touched:
            # Injected pruner (transfer-dir-bounded — works when the library lives
            # under the transfer folder) PLUS the library-safe upward prune (#985)
            # for libraries that don't.
            if cleanup_empty_dir_fn:
                try:
                    cleanup_empty_dir_fn(src_dir)
                except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                    pass
            try:
                _prune_empty_source_dirs(src_dir)
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


def _rename_track_in_place(current_abs: str, new_abs: str) -> Tuple[bool, Optional[str]]:
    """Move ONE file from ``current_abs`` to ``new_abs`` in place — no copy, no re-tag,
    no post-processing. Creates the destination folder, carries sibling-format files
    (e.g. a lossy ``.opus`` alongside the ``.flac``) along with the renamed stem, and
    falls back to a cross-device move when the rename crosses a filesystem boundary.

    Refuses to overwrite a DIFFERENT existing file at the destination (returns an error
    instead) — never silent data loss. Returns ``(ok, error_message)``.
    """
    try:
        if current_abs and not os.path.exists(current_abs):
            return False, 'source file no longer on disk'
        same = _same_physical_file(current_abs, new_abs)
        if same:
            return True, None
        if os.path.exists(new_abs) and not same:
            return False, 'destination already exists'
        os.makedirs(os.path.dirname(new_abs), exist_ok=True)
        # Siblings are collected BEFORE the rename (the source stem is gone
        # afterwards) but carried AFTER it succeeds (iss29-E02): moving them
        # first meant a failed canonical rename left the album split across two
        # directories, with no error path that could put it back.
        siblings = _find_sibling_audio_files(current_abs)
        try:
            os.rename(current_abs, new_abs)
        except OSError as e:
            if getattr(e, 'errno', None) == errno.EXDEV:
                shutil.move(current_abs, new_abs)  # crosses a filesystem boundary
            else:
                raise
        # Carry sibling-format audio to the same destination with the renamed stem —
        # mirrors _finalize_track so lossy-copy pairs don't get orphaned.
        for sibling_src in siblings:
            _move_sibling_to_destination(sibling_src, new_abs)
        # And the track's own sidecars. A .lrc is part of the track — SoulSync
        # writes it itself — and the import path has carried it since
        # lilbob5769's report. Reorganize looked for sibling AUDIO only and
        # stranded the lyrics in the old folder. Same helper as the import, so
        # a reorganized album and a freshly downloaded one end up with the same
        # files beside each other.
        #
        # Best-effort by design: the audio is already at its destination here.
        # Failing the move over a sidecar would report a track that did not
        # move, and leave the catalogue pointing at a path nothing is at.
        try:
            from core.imports.file_ops import move_companion_sidecars
            move_companion_sidecars(current_abs, new_abs)
        except Exception as sidecar_err:  # noqa: BLE001
            logger.warning("[Reorganize/rename] sidecar move failed for %s: %s",
                           os.path.basename(new_abs), sidecar_err)
        return True, None
    except Exception as e:
        return False, str(e)


def reorganize_album_rename_only(
    *,
    album_id: str,
    db,
    transfer_dir: str,
    resolve_file_path_fn: Callable[[Optional[str]], Optional[str]],
    build_final_path_fn: Callable,
    update_track_path_fn: Optional[Callable[[object, str], None]] = None,
    cleanup_empty_dir_fn: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    primary_source: Optional[str] = None,
    strict_source: bool = False,
    metadata_source: str = 'api',
    stop_check: Optional[Callable[[], bool]] = None,
    preview_fn: Optional[Callable] = None,
) -> dict:
    """RENAME-ONLY reorganize (#875): move each track's file to the path the current
    naming scheme dictates, and nothing else — no copy-to-staging, no re-tag, no
    quality/AcoustID checks.

    It acts on EXACTLY what :func:`preview_album_reorganize` computed (injected via
    ``preview_fn`` for testability), so the apply can never disagree with what the user
    saw, and ONLY files whose path actually changes are touched — files marked
    ``unchanged`` are skipped, which is what keeps a rename from rewriting the whole
    album (the #875 complaint). Tags and audio are left byte-for-byte alone.

    Returns the same summary shape as :func:`reorganize_album`.
    """
    preview_fn = preview_fn or preview_album_reorganize
    summary = {
        'status': 'completed', 'source': None, 'total': 0,
        'moved': 0, 'skipped': 0, 'failed': 0, 'errors': [],
    }

    def _emit(**updates):
        if on_progress is None:
            return
        try:
            on_progress(updates)
        except Exception as e:
            logger.debug("[Reorganize/rename] progress emit failed: %s", e)

    preview = preview_fn(
        album_id=album_id, db=db, transfer_dir=transfer_dir,
        resolve_file_path_fn=resolve_file_path_fn,
        build_final_path_fn=build_final_path_fn,
        primary_source=primary_source, strict_source=strict_source,
        metadata_source=metadata_source,
    )
    summary['source'] = preview.get('source')
    if not preview.get('success'):
        summary['status'] = preview.get('status', 'error')
        return summary

    tracks = preview.get('tracks', [])
    summary['total'] = len(tracks)
    src_dirs_touched: Set[str] = set()

    for t in tracks:
        if stop_check and stop_check():
            break
        title = t.get('title', 'Unknown')
        _emit(current_track=title)

        # Skip anything that isn't a real, changing move. `unchanged` is the key one —
        # it's why a rename no longer rewrites files whose name didn't change.
        # `current_path_abs` is the other one: the preview leaves it empty when the
        # stored path resolves to nothing on disk (`file_exists` False). Passing that
        # to the mover fails on os.rename — but only AFTER os.makedirs has built the
        # destination tree, so an unresolvable track used to litter the library with
        # empty folders, which is exactly what the preview avoids with create_dirs=False.
        if (not t.get('matched') or t.get('unchanged')
                or t.get('collision') or not t.get('new_path_abs')
                or not t.get('current_path_abs')):
            summary['skipped'] += 1
            _emit(skipped=summary['skipped'])
            continue

        current_abs = t.get('current_path_abs')
        new_abs = t.get('new_path_abs')
        ok, err = _rename_track_in_place(current_abs, new_abs)
        if not ok:
            summary['failed'] += 1
            summary['errors'].append({
                'track_id': t.get('track_id'), 'title': title,
                'error': err or 'rename failed',
            })
            _emit(failed=summary['failed'], errors=list(summary['errors']))
            continue

        # File is at its new home — update the DB directly (authoritative; no need to
        # round-trip through a server scan to learn what we just did).
        #
        # A rename MOVES the only copy, so a failed catalogue update is NOT
        # recoverable by "a scan will reconcile": the file sits at a path nothing
        # points at, the track reads as MISSING, and the wishlist re-downloads
        # something the user already owns. Reported against a fresh library — songs
        # downloaded, Reorganize run while the import still held the write lock.
        # Put the file back and fail the track loudly instead.
        if update_track_path_fn:
            try:
                update_track_path_fn(t.get('track_id'), new_abs)
            except Exception as db_err:
                undone, undo_err = _rename_track_in_place(new_abs, current_abs)
                if undone:
                    detail = f'{db_err} (move undone)'
                    logger.warning(
                        "[Reorganize/rename] catalogue update failed for %s: %s "
                        "— moved %s back to %s",
                        t.get('track_id'), db_err, new_abs, current_abs,
                    )
                else:
                    detail = (f'{db_err} — and the file could not be moved back '
                              f'to {current_abs}: {undo_err}')
                    logger.error(
                        "[Reorganize/rename] catalogue update failed for %s (%s) AND "
                        "the rollback failed (%s): the file is at %s while the "
                        "catalogue still names %s",
                        t.get('track_id'), db_err, undo_err, new_abs, current_abs,
                    )
                summary['failed'] += 1
                summary['errors'].append({
                    'track_id': t.get('track_id'), 'title': title, 'error': detail,
                })
                _emit(failed=summary['failed'], errors=list(summary['errors']))
                continue
        if current_abs:
            src_dirs_touched.add(os.path.dirname(current_abs))
        summary['moved'] += 1
        _emit(moved=summary['moved'],
              processed=summary['moved'] + summary['skipped'] + summary['failed'])

    for src_dir in src_dirs_touched:
        if cleanup_empty_dir_fn:
            try:
                cleanup_empty_dir_fn(src_dir)
            except Exception as e:
                logger.debug("[Reorganize/rename] cleanup of %s failed: %s", src_dir, e)
        try:
            _prune_empty_source_dirs(src_dir)   # #985: library-safe prune (transfer-dir-independent)
        except Exception as e:
            logger.debug("[Reorganize/rename] source prune of %s failed: %s", src_dir, e)

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


# A disc subfolder inside an album: "Disc 1", "CD 2", "Disk 01", "Vol. 3", etc.
_DISC_DIR_RE = re.compile(r'^(disc|disk|cd|vol|volume)\s*\.?\s*\d+$', re.IGNORECASE)


def _rmdir_if_empty(dir_path: str) -> bool:
    """rmdir ``dir_path`` if it holds no non-hidden entries (clearing hidden junk like
    .DS_Store first) and isn't a protected/configured root. Returns True if removed."""
    try:
        from core.imports.file_ops import protected_root_dirs
        if os.path.normpath(dir_path) in protected_root_dirs():
            return False
    except Exception as e:
        logger.debug("[Reorganize] protected-root check failed for %s: %s", dir_path, e)
    try:
        entries = os.listdir(dir_path)
    except OSError:
        return False
    if any(not e.startswith('.') for e in entries):
        return False  # real content remains
    try:
        for hidden in entries:
            try:
                os.remove(os.path.join(dir_path, hidden))
            except OSError:
                pass
        os.rmdir(dir_path)
        logger.info(f"[Reorganize] Pruned empty source dir: {dir_path}")
        return True
    except OSError:
        return False


def _prune_empty_source_dirs(start_dir: str) -> None:
    """Prune the emptied SOURCE folders after a reorganize move.

    #985: the injected empty-dir cleanup is bounded by the transfer/download folder,
    but a Library Reorganize moves files that live in the MEDIA library — usually a
    different path — so that pruner's ``startswith(transfer_dir)`` guard never matches
    and the emptied source folders (``Album/Disc 1`` and the old ``Album`` dir) are
    left behind.

    Removes the moved file's own dir, AND the album dir directly above it IF that dir
    was a disc subfolder (``Disc N``/``CD N``). It deliberately NEVER climbs to the
    artist dir or the library root — bounded to the album level — so it can only ever
    delete the album/disc folders the reorganize actually emptied, never a root
    (which isn't in the protected set when the library lives outside the transfer
    folder — exactly this bug's setup).
    """
    d = os.path.normpath(start_dir)
    was_disc = bool(_DISC_DIR_RE.match(os.path.basename(d)))
    if not _rmdir_if_empty(d):
        return
    if was_disc:                          # the parent is the album dir — prune if now empty
        _rmdir_if_empty(os.path.dirname(d))


# Sidecar / cleanup helpers --------------------------------------------------

# Sidecars that live alongside ONE audio file (same filename stem).
_TRACK_SIDECAR_EXTS = ('.lrc', '.nfo', '.txt', '.cue', '.json')

# Album-level leftovers (cover images, .lrc, etc.) are classified by the shared
# `core.library.residual_files.is_disposable` predicate — see `_delete_album_sidecars`.

# Audio extensions used to decide whether a source directory still has
# tracks the user might care about (i.e. a per-track failure left audio
# behind that we shouldn't strip the cover art from).
_AUDIO_EXTS = frozenset(
    {'.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.aac', '.wma', '.mp4'}
)


def _find_sibling_audio_files(audio_path: str) -> list:
    """Find OTHER audio files at the same source directory that share
    the canonical file's stem.

    Discord report (Foxxify): users with the lossy-copy feature
    enabled end up with `track.flac` AND `track.opus` side-by-side.
    Reorganize is DB-driven and only knows about ONE file per track
    (the lossy copy in library), so the other format gets left behind
    in the old location while the canonical moves to the new
    destination. Cleanup never fires because the source dir still has
    audio.

    This helper returns the orphan-format paths so the caller can
    move them alongside the canonical to the new destination dir.
    Same stem + audio extension + NOT the canonical itself.

    Returns empty list when source dir doesn't exist or read fails
    (defensive — never raises).
    """
    src_dir = os.path.dirname(audio_path)
    if not os.path.isdir(src_dir):
        return []
    stem = os.path.splitext(os.path.basename(audio_path))[0]
    canonical_basename = os.path.basename(audio_path)
    siblings = []
    try:
        entries = os.listdir(src_dir)
    except OSError:
        return []
    for name in entries:
        if name == canonical_basename:
            continue
        sibling_stem, ext = os.path.splitext(name)
        if sibling_stem != stem:
            continue
        if ext.lower() not in _AUDIO_EXTS:
            continue
        full = os.path.join(src_dir, name)
        if os.path.isfile(full):
            siblings.append(full)
    return siblings


def _move_sibling_to_destination(sibling_src: str, canonical_dst: str) -> Optional[str]:
    """Move a sibling-format audio file to the same destination
    directory as the canonical, preserving its extension.

    Example: canonical at ``/library/Artist/Album/01 Track.opus`` +
    sibling source ``/old/01 Track.flac`` → destination ``/library/
    Artist/Album/01 Track.flac``. The destination filename uses the
    canonical's stem (post-template-rename) + the sibling's original
    extension — so a renamed canonical gets matching siblings.

    Returns the destination path on success, None on failure (logged
    at warning, doesn't raise — sibling moves are best-effort).

    iss29-E02: refuses to overwrite a DIFFERENT file already at the
    destination, exactly like ``_rename_track_in_place``. ``shutil.move``
    resolves to ``os.rename`` for a regular file on POSIX and overwrites
    silently — no error, no log, no counter. With lossy-copy enabled and a
    destination already holding a file under the canonical's post-rename stem
    (an earlier partial run, a second edition), that destroyed a file nothing
    in the catalogue was tracking.
    """
    dst_dir = os.path.dirname(canonical_dst)
    canonical_stem = os.path.splitext(os.path.basename(canonical_dst))[0]
    _, sibling_ext = os.path.splitext(sibling_src)
    sibling_dst = os.path.join(dst_dir, canonical_stem + sibling_ext)
    if _same_physical_file(sibling_src, sibling_dst):
        return sibling_dst  # already at the right place
    if os.path.exists(sibling_dst):
        logger.warning(
            "[Reorganize] Not moving sibling-format file %s → %s: destination "
            "already exists; leaving the source in place",
            sibling_src, sibling_dst,
        )
        return None
    try:
        os.makedirs(dst_dir, exist_ok=True)
        shutil.move(sibling_src, sibling_dst)
        return sibling_dst
    except OSError as e:
        logger.warning(
            "[Reorganize] Couldn't move sibling-format file %s → %s: %s",
            sibling_src, sibling_dst, e,
        )
        return None


def _carry_track_sidecars(src_audio: str, dst_audio: str) -> None:
    """Move per-track sidecars (.lrc / .nfo / .txt / .cue / .json) to sit
    alongside the moved audio, under the destination's stem.

    iss29-E05: these used to be DELETED at the source. The full reorganize run
    stages only the audio file (``_stage_track``), so the sidecar was never
    carried across — meaning "Fetch Lyrics" followed by "Reorganize" on the same
    page silently emptied the lyrics column. Library V2 writes exactly these
    files (``core/library2/lyrics.py``), so reorganize was deleting its own
    application's data. Every other mover in the project already carries them:
    ``_fix_path_mismatch`` and ``_rename_to_basename`` both move the sidecar —
    reorganize was the outlier.

    A sidecar whose destination name is already taken is removed rather than
    moved: the destination copy belongs to the same track at the same path, so
    the source one is a leftover and keeping it would block the empty-folder
    cleanup this function exists to enable. Best-effort throughout — individual
    failures are logged at debug and never raised.
    """
    src_dir = os.path.dirname(src_audio)
    src_stem = os.path.splitext(os.path.basename(src_audio))[0]
    dst_dir = os.path.dirname(dst_audio)
    dst_stem = os.path.splitext(os.path.basename(dst_audio))[0]
    for ext in _TRACK_SIDECAR_EXTS:
        sidecar_src = os.path.join(src_dir, src_stem + ext)
        if not os.path.isfile(sidecar_src):
            continue
        sidecar_dst = os.path.join(dst_dir, dst_stem + ext)
        if _same_physical_file(sidecar_src, sidecar_dst):
            continue
        if os.path.exists(sidecar_dst):
            try:
                os.remove(sidecar_src)
            except OSError as e:
                logger.debug(f"[Reorganize] Couldn't remove sidecar {sidecar_src}: {e}")
            continue
        try:
            os.makedirs(dst_dir, exist_ok=True)
            shutil.move(sidecar_src, sidecar_dst)
        except OSError as e:
            logger.debug(
                f"[Reorganize] Couldn't carry sidecar {sidecar_src} → {sidecar_dst}: {e}"
            )


def _delete_album_sidecars(src_dir: str) -> None:
    """Delete album-level *residual* files from ``src_dir`` — any cover/scan image,
    lyric/metadata sidecar (.lrc/.nfo/.cue/.m3u), or OS junk. Called during
    end-of-run cleanup ONLY when no audio remains in the directory, so everything
    here is leftover from the album that just moved (#891 — previously this only
    removed a fixed list of cover names, so ``back.jpg`` / ``disc.jpg`` / ``.webp``
    survived and kept the folder un-prunable).

    Uses the shared ``is_disposable`` predicate so it agrees with the Empty Folder
    Cleaner on what's a dead leftover; anything unrecognized (a booklet ``.pdf``, a
    video) is deliberately LEFT. Best-effort — individual failures are debug-logged."""
    from core.library.residual_files import is_disposable
    try:
        entries = os.listdir(src_dir)
    except OSError:
        return
    for name in entries:
        if not is_disposable(name):
            continue
        full = os.path.join(src_dir, name)
        if os.path.isfile(full):
            try:
                os.remove(full)
            except OSError as e:
                logger.debug(f"[Reorganize] Couldn't remove residual file {full}: {e}")


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
