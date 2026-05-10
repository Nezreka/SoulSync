"""Album-track matching helpers — lifted out of
``AutoImportWorker._match_tracks`` so the matching logic is testable in
isolation without instantiating the worker, mocking the metadata
client, or monkey-patching ``_read_file_tags``.

Diagnostic logging:
- Every match decision (matched, rejected by duration, rejected by
  threshold) emits a debug-level log when ``ALBUM_MATCHING_DEBUG`` is
  truthy. Defaults to off so production logs stay clean. Flip via the
  ``SOULSYNC_ALBUM_MATCHING_DEBUG`` env var when investigating
  "nothing matched" reports.

The worker still owns:
- File-system traversal + tag reads
- Metadata client lookup + album_data fetch
- Album-vs-single routing

This module owns:
- Quality-aware deduplication keyed on the ``(disc_number, track_number)``
  position tuple
- Weighted match scoring against the album's tracklist
- Returning the list of (track, file, confidence) matches + leftover
  unmatched files

Both behaviors are pure functions over already-fetched data, so the
test surface is just dicts in / dicts out.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Set, Tuple

# Use the project's namespaced logger so diagnostic lines actually
# land in app.log. `logging.getLogger(__name__)` would resolve to
# `core.imports.album_matching` which sits OUTSIDE the `soulsync.*`
# tree the file handler watches, making every "no matches" diagnostic
# silently invisible to anyone debugging an import problem.
from utils.logging_config import get_logger

logger = get_logger("imports.album_matching")


# ---------------------------------------------------------------------------
# Match-scoring weights
# ---------------------------------------------------------------------------
# Each weight is a fraction of the 0..1 confidence score the matcher
# accumulates per (file, track) pair. Sum of all maximum-bonus paths
# equals 1.0 in the happy case (perfect title + artist + position +
# album tag agreement).
#
# History note: the position bonus (30%) used to fire on track_number
# alone, which broke multi-disc albums where every disc has tracks 1..N.
# Disc-aware split (POSITION + CROSS_DISC) shipped 2026-05-09 after
# user reported Mr. Morale & The Big Steppers losing half its tracks
# during auto-import.

TITLE_WEIGHT = 0.45                 # case-folded fuzzy title similarity
ARTIST_WEIGHT = 0.15                # albumartist (or artist) similarity
POSITION_WEIGHT = 0.30              # exact (disc_number, track_number) match
NEAR_POSITION_WEIGHT = 0.12         # off-by-one track number, same disc
CROSS_DISC_POSITION_WEIGHT = 0.05   # same track_number, different disc
ALBUM_WEIGHT = 0.10                 # album tag similarity to target album

# A file scoring below this threshold against every track is treated
# as unmatched. Threshold sits below the per-component partial-match
# floor (~0.5 × 0.45 = 0.22) plus a small position consolation, so
# files with weak title agreement still need at least one strong signal.
MATCH_THRESHOLD = 0.4


SimilarityFn = Callable[[str, str], float]
QualityRankFn = Callable[[str], int]


def dedupe_files_by_position(
    audio_files: List[str],
    file_tags: Dict[str, Dict[str, Any]],
    *,
    quality_rank: QualityRankFn,
) -> List[str]:
    """Drop quality-duplicate files at the same ``(disc, track)``
    position, keeping the higher-quality one.

    The position key is ``(disc_number, track_number)`` — NOT
    ``track_number`` alone. Multi-disc albums where every disc has
    tracks 1..N would otherwise collapse to one disc's worth of files
    here, before the matcher even sees the rest.

    Files with ``track_number == 0`` (no tag) all pass through —
    can't dedupe positions we don't know.
    """
    seen_positions: Dict[Tuple[int, int], str] = {}
    deduped: List[str] = []

    for f in audio_files:
        tags = file_tags.get(f, {})
        track_num = tags.get('track_number', 0) or 0
        disc_num = tags.get('disc_number', 1) or 1
        ext = os.path.splitext(f)[1].lower()
        position_key = (disc_num, track_num)

        if track_num > 0 and position_key in seen_positions:
            prev_f = seen_positions[position_key]
            prev_ext = os.path.splitext(prev_f)[1].lower()
            if quality_rank(ext) > quality_rank(prev_ext):
                deduped.remove(prev_f)
                deduped.append(f)
                seen_positions[position_key] = f
        else:
            deduped.append(f)
            if track_num > 0:
                seen_positions[position_key] = f

    return deduped


def _extract_track_disc(track: Dict[str, Any]) -> int:
    """Pull disc number off an API track dict.

    Different metadata sources spell the field differently:
    Spotify ``disc_number``, Deezer ``disk_number``, iTunes
    ``discNumber``. Default to 1 when missing so single-disc albums
    still match.
    """
    return (
        track.get('disc_number')
        or track.get('disk_number')
        or track.get('discNumber')
        or 1
    )


def _extract_track_artist(track: Dict[str, Any]) -> str:
    artists = track.get('artists') or []
    if not artists:
        return ''
    a = artists[0]
    return a.get('name', str(a)) if isinstance(a, dict) else str(a)


def score_file_against_track(
    file_path: str,
    file_tags: Dict[str, Any],
    track: Dict[str, Any],
    *,
    target_album: str,
    similarity: SimilarityFn,
) -> float:
    """Compute the 0..1 confidence score for matching ``file_path``
    (with its tags) to ``track`` (an API track dict).

    Pure scoring — caller decides what to do with the score (compare
    against ``MATCH_THRESHOLD``, pick best-per-track, etc).
    """
    score = 0.0

    # Title similarity (TITLE_WEIGHT). Falls back to filename stem when
    # the file has no title tag.
    title = file_tags.get('title') or os.path.splitext(os.path.basename(file_path))[0]
    track_name = track.get('name', '')
    score += similarity(title, track_name) * TITLE_WEIGHT

    # Artist similarity (ARTIST_WEIGHT). Skipped if either side missing.
    file_artist = file_tags.get('artist', '')
    track_artist = _extract_track_artist(track)
    if file_artist and track_artist:
        score += similarity(file_artist, track_artist) * ARTIST_WEIGHT

    # Position match (POSITION_WEIGHT / NEAR_POSITION_WEIGHT /
    # CROSS_DISC_POSITION_WEIGHT). Gates on the (disc, track) tuple
    # rather than track_number alone — see the module docstring's
    # multi-disc history note.
    file_track_num = file_tags.get('track_number', 0) or 0
    track_num = track.get('track_number', 0) or 0
    if file_track_num > 0 and track_num > 0:
        file_disc = file_tags.get('disc_number', 1) or 1
        track_disc = _extract_track_disc(track)
        if file_track_num == track_num and file_disc == track_disc:
            score += POSITION_WEIGHT
        elif file_track_num == track_num and file_disc != track_disc:
            # Same track number, different disc — small consolation so
            # title/artist similarity has to carry the match. Common
            # collision in deluxe / multi-disc releases where every
            # disc has tracks numbered 1..N.
            score += CROSS_DISC_POSITION_WEIGHT
        elif abs(file_track_num - track_num) <= 1 and file_disc == track_disc:
            score += NEAR_POSITION_WEIGHT

    # Album tag bonus (ALBUM_WEIGHT). Helps disambiguate when the
    # target_album name is a strong signal.
    file_album = file_tags.get('album', '')
    if file_album:
        score += similarity(file_album, target_album) * ALBUM_WEIGHT

    return score


# ---------------------------------------------------------------------------
# Exact-identifier fast paths
# ---------------------------------------------------------------------------
# Tagged libraries (especially Picard / Beets) carry per-recording IDs
# that uniquely identify the track regardless of title spelling, album
# context, or duration drift. When both the file tag AND the metadata
# source's track entry carry the same identifier, no fuzzy matching is
# needed — exact match wins, full confidence, no further scoring.
#
# Order: MBID first (MusicBrainz Recording ID — primary Picard tag),
# then ISRC (International Standard Recording Code — many sources).
# An ISRC can be shared across remasters / region releases of the same
# recording, so MBID is preferred when both are present.

EXACT_MATCH_CONFIDENCE = 1.0


def _track_identifier(track: Dict[str, Any], key: str) -> str:
    """Pull a normalized identifier off a metadata-source track dict.

    Different sources spell ISRC differently — Spotify exposes it on
    ``external_ids.isrc``; iTunes uses ``isrc`` directly when present.
    MBID lives at ``external_ids.mbid`` for some sources, top-level
    ``musicbrainz_id`` / ``mbid`` for others.
    """
    if key == 'isrc':
        # ISRC normalization: uppercase, strip dashes/spaces. Picard writes
        # tags as "USRC1234567" but some sources return "US-RC-12-34567".
        for candidate in (
            track.get('isrc'),
            (track.get('external_ids') or {}).get('isrc'),
        ):
            if candidate:
                return str(candidate).upper().replace('-', '').replace(' ', '').strip()
        return ''
    if key == 'mbid':
        for candidate in (
            track.get('musicbrainz_id'),
            track.get('mbid'),
            (track.get('external_ids') or {}).get('mbid'),
            (track.get('external_ids') or {}).get('musicbrainz'),
        ):
            if candidate:
                return str(candidate).lower().strip()
        return ''
    return ''


def _file_identifier(file_tags: Dict[str, Any], key: str) -> str:
    """Pull a normalized identifier off the file's tag dict."""
    if key == 'isrc':
        raw = file_tags.get('isrc') or ''
        return str(raw).upper().replace('-', '').replace(' ', '').strip()
    if key == 'mbid':
        return str(file_tags.get('mbid') or '').lower().strip()
    return ''


def find_exact_id_matches(
    audio_files: List[str],
    file_tags: Dict[str, Dict[str, Any]],
    tracks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pair files to tracks via exact-identifier match (MBID, then ISRC).

    Returns a dict with ``matches`` (one entry per file/track pair that
    matched on a shared identifier) + ``used_files`` (set) +
    ``used_track_indices`` (set). Caller is responsible for feeding the
    leftovers into the fuzzy-scoring path.

    No similarity computation, no I/O. Pure dict-in/dict-out.
    """
    matches: List[Dict[str, Any]] = []
    used_files: Set[str] = set()
    used_track_indices: Set[int] = set()

    for id_key in ('mbid', 'isrc'):
        # Build {identifier_value: track_index} for this key — single pass
        # over tracks, lookup is O(1) per file afterwards.
        track_index_by_id: Dict[str, int] = {}
        for i, track in enumerate(tracks):
            if i in used_track_indices:
                continue
            tid = _track_identifier(track, id_key)
            if tid:
                track_index_by_id[tid] = i

        if not track_index_by_id:
            continue

        for f in audio_files:
            if f in used_files:
                continue
            fid = _file_identifier(file_tags.get(f, {}), id_key)
            if not fid:
                continue
            track_idx = track_index_by_id.get(fid)
            if track_idx is None or track_idx in used_track_indices:
                continue
            matches.append({
                'track': tracks[track_idx],
                'file': f,
                'confidence': EXACT_MATCH_CONFIDENCE,
                'match_type': id_key,
            })
            used_files.add(f)
            used_track_indices.add(track_idx)

    return {
        'matches': matches,
        'used_files': used_files,
        'used_track_indices': used_track_indices,
    }


# ---------------------------------------------------------------------------
# Duration sanity gate
# ---------------------------------------------------------------------------
# A file whose audio length differs from the candidate track's duration
# by more than this tolerance can't possibly be the right track —
# rejecting cross-disc / cross-release / wrong-edit mismatches before
# they hit the post-download integrity check (which catches the same
# problem AFTER the file has been moved). The integrity check stays as
# a defense-in-depth backstop.
#
# Tolerance picked to match standard library-importer behavior:
# Picard ~7s, Beets ~10-15s, Plex ~10s. The post-download integrity
# check uses a stricter ±3s because it's catching truncated downloads
# (same recording, partial bytes — should be byte-exact match) — a
# different problem from "is this the same recording across remasters
# / encodings / streaming services."
#
# Real-world drift between matched-recording sources:
# - FLAC vs MP3 transcode of same master: typically <0.5s
# - Different mastering eras (2009 remaster vs original): 1-3s
# - Different streaming service encodings: 2-7s (varying fade-out)
# - Album version vs "remixed/expanded edition": often >10s — these
#   genuinely should NOT match the original tracklist anyway
#
# 10s tolerance lands in the sweet spot: catches real recording
# mismatches (gross differences = wrong track) while accepting normal
# encoding / mastering drift.

DURATION_TOLERANCE_MS = 10000   # ±10 seconds


def duration_sanity_ok(file_duration_ms: int, track_duration_ms: int) -> bool:
    """True when the file's audio duration is plausibly the track's
    duration, OR when either side has no usable duration info.

    "Either side missing" returns True (don't reject when we can't
    confirm) — gates only on cases where BOTH sides have a number we
    can compare. Files with no length info (rare — corrupt headers,
    streamed-only formats) are deferred to the fuzzy scorer.
    """
    if not file_duration_ms or not track_duration_ms:
        return True
    return abs(int(file_duration_ms) - int(track_duration_ms)) <= DURATION_TOLERANCE_MS


# Per-source duration field conventions for what the matcher RECEIVES
# (after each client's internal normalisation), NOT what each provider's
# raw API returns. Deezer's API returns `duration` in seconds, but
# `DeezerClient.get_album_tracks` converts to `duration_ms` in actual
# ms before returning — so the matcher sees ms, and double-converting
# here turns 255000 into 255000000 (the user-reported "no matches"
# bug from 2026-05-09 — every Deezer-primary user's auto-import broke).
#
# Track entries built by `_build_album_track_entry` carry the source
# name on `source` / `_source` / `provider` so we can dispatch
# deterministically instead of guessing from value magnitude.
_SECONDS_DURATION_SOURCES = frozenset((
    'discogs',        # release tracks expose duration as MM:SS strings
                      # (handled in metadata layer, but defensive here)
    'musicbrainz',    # recording length is sometimes seconds vs ms
                      # depending on which endpoint
))
_MS_DURATION_SOURCES = frozenset((
    'spotify',        # duration_ms (canonical Spotify naming)
    'itunes',         # trackTimeMillis → normalised to duration_ms upstream
    'deezer',         # CLIENT converts seconds → ms before returning
                      # (see core/deezer_client.py:get_album_tracks)
    'qobuz',          # duration_ms
    'tidal',          # duration in seconds OR duration_ms — see below
    'hydrabase',      # duration_ms
    'hifi',           # duration_ms
))


def _track_duration_ms(track: Dict[str, Any]) -> int:
    """Pull track duration in milliseconds — source-aware.

    Different metadata providers spell + scale duration differently:

    - Spotify / iTunes / Qobuz / HiFi / Hydrabase: ``duration_ms`` (ms)
    - Deezer / Discogs: ``duration`` (seconds, int)
    - Tidal: depends on endpoint — usually seconds for browse, ms for
      album tracks; defensive heuristic kicks in if source missing

    Decision order:
    1. If the track carries a source name + that source is in the
       seconds-only list, treat raw value as seconds and × 1000.
    2. If source is ms-only, take the value as-is.
    3. If source unknown / missing (e.g. mocked test data), fall back
       to a magnitude heuristic — values < 30000 treated as seconds.
       This is the legacy behavior, kept as the safety net.
    """
    raw = track.get('duration_ms') or track.get('duration') or 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0

    source = (track.get('source') or track.get('_source') or track.get('provider') or '').strip().lower()

    if source in _SECONDS_DURATION_SOURCES:
        return value * 1000
    if source in _MS_DURATION_SOURCES:
        return value

    # Unknown / missing source — fall back to the magnitude heuristic.
    # Anything below 30000 (30 seconds in ms) is implausibly short for
    # a real track and is almost certainly seconds being passed where
    # ms was expected.
    if value < 30000:
        return value * 1000
    return value


def match_files_to_tracks(
    audio_files: List[str],
    file_tags: Dict[str, Dict[str, Any]],
    tracks: List[Dict[str, Any]],
    *,
    target_album: str,
    similarity: SimilarityFn,
    quality_rank: QualityRankFn,
) -> Dict[str, Any]:
    """Match staging files to album tracks.

    Algorithm (in order):

    1. **Exact-identifier fast paths** (``find_exact_id_matches``) —
       pair files to tracks via shared MBID, then ISRC. Picard-tagged
       libraries land here on the first pass with full confidence,
       skipping the fuzzy scorer entirely. Each match carries a
       ``'match_type': 'mbid' | 'isrc'`` field for downstream
       provenance / debug logging.

    2. **Quality dedup** on remaining files — keep the highest-quality
       file per ``(disc, track)`` position.

    3. **Fuzzy scoring** on remaining files vs remaining tracks — title
       + artist + position + album-tag weighted scoring with a duration
       sanity gate (files whose audio length is more than
       ``DURATION_TOLERANCE_MS`` from the candidate track are rejected
       before scoring, regardless of how good the title agreement
       looks).

    Returns a dict with:
    - ``matches``: list of ``{'track': dict, 'file': str, 'confidence': float}``;
      exact-id matches additionally carry ``'match_type'``.
    - ``unmatched_files``: files left over after every track found its
      best (or none).

    Each file matches at most one track. Each track matches at most one
    file. Pure function — no side effects, no I/O, no metadata client.
    """
    matches: List[Dict[str, Any]] = []
    used_files: Set[str] = set()
    used_track_indices: Set[int] = set()

    # Phase 1 — exact identifiers (MBID, then ISRC).
    exact = find_exact_id_matches(audio_files, file_tags, tracks)
    matches.extend(exact['matches'])
    used_files.update(exact['used_files'])
    used_track_indices.update(exact['used_track_indices'])

    # Phase 2 — quality dedup on remaining files.
    remaining_files = [f for f in audio_files if f not in used_files]
    deduped = dedupe_files_by_position(remaining_files, file_tags, quality_rank=quality_rank)

    # Phase 3 — fuzzy scoring on remaining tracks.
    duration_rejected = 0     # diagnostics for the "no matches" case
    below_threshold = 0
    sample_rejection_logged = False
    for i, track in enumerate(tracks):
        if i in used_track_indices:
            continue

        track_duration = _track_duration_ms(track)

        best_file = None
        best_score = 0.0

        for f in deduped:
            if f in used_files:
                continue

            tags = file_tags.get(f, {})

            # Duration sanity gate — reject implausible matches before
            # title/artist scoring even runs. Defends against the
            # cross-disc / cross-release wrong-edit problem the post-
            # download integrity check used to catch only AFTER the
            # file had already been moved + tagged + DB-inserted.
            file_duration = tags.get('duration_ms', 0) or 0
            if not duration_sanity_ok(file_duration, track_duration):
                duration_rejected += 1
                # On the FIRST rejection per matcher run, log the actual
                # values so users / reviewers can see whether it's a
                # unit mismatch (seconds vs ms), genuine drift, or some
                # third thing. Logging every rejection would spam the
                # log on a 21-file × 19-track album (399 lines).
                if not sample_rejection_logged:
                    sample_rejection_logged = True
                    raw_dur_ms = track.get('duration_ms')
                    raw_dur = track.get('duration')
                    raw_src = track.get('source') or track.get('_source') or track.get('provider')
                    logger.info(
                        "[Album Matching] First duration rejection in '%s': "
                        "file %r duration_ms=%d, track %r resolved=%d "
                        "(raw duration_ms=%r, raw duration=%r, source=%r)",
                        target_album,
                        os.path.basename(f), file_duration,
                        track.get('name', '?'), track_duration,
                        raw_dur_ms, raw_dur, raw_src,
                    )
                continue

            score = score_file_against_track(
                f, tags, track,
                target_album=target_album,
                similarity=similarity,
            )
            if score > best_score and score >= MATCH_THRESHOLD:
                best_score = score
                best_file = f

        if best_file:
            used_files.add(best_file)
            matches.append({
                'track': track,
                'file': best_file,
                'confidence': round(best_score, 3),
            })
        elif deduped:
            below_threshold += 1

    # Diagnostic surface — when the matcher returns 0 matches against
    # a non-trivial input, it's nearly always one of: duration gate too
    # strict, title agreement too low, or wrong tracks list passed in.
    # Log a one-line summary at INFO so users grep'ing app.log for
    # "no matches" cases see WHY without needing to bump log level.
    if not matches and (audio_files or tracks):
        logger.info(
            "[Album Matching] No matches: %d files, %d tracks, "
            "%d duration-rejected pairs, %d tracks below threshold. "
            "Album: %r",
            len(audio_files), len(tracks),
            duration_rejected, below_threshold, target_album,
        )

    # Final unmatched list: every file that didn't get used in any
    # phase. Includes quality-dedup losers (lower-quality copies of
    # files we already matched) so the caller can see the full picture.
    return {
        'matches': matches,
        'unmatched_files': [f for f in audio_files if f not in used_files],
    }


__all__ = [
    'TITLE_WEIGHT',
    'ARTIST_WEIGHT',
    'POSITION_WEIGHT',
    'NEAR_POSITION_WEIGHT',
    'CROSS_DISC_POSITION_WEIGHT',
    'ALBUM_WEIGHT',
    'MATCH_THRESHOLD',
    'EXACT_MATCH_CONFIDENCE',
    'DURATION_TOLERANCE_MS',
    'dedupe_files_by_position',
    'score_file_against_track',
    'find_exact_id_matches',
    'duration_sanity_ok',
    'match_files_to_tracks',
]
