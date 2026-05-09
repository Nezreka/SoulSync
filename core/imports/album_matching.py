"""Album-track matching helpers — lifted out of
``AutoImportWorker._match_tracks`` so the matching logic is testable in
isolation without instantiating the worker, mocking the metadata
client, or monkey-patching ``_read_file_tags``.

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

    Returns a dict with:
    - ``matches``: list of ``{'track': dict, 'file': str, 'confidence': float}``,
      one per track that found a file scoring at or above
      ``MATCH_THRESHOLD``
    - ``unmatched_files``: files left over after every track found its
      best (or none)

    Each file matches at most one track (best-scoring track that
    accepted it wins). Each track matches at most one file (the highest-
    scoring still-unused file).

    Pure function — no side effects, no I/O, no metadata client. Easy
    to unit-test by feeding tag dicts and track dicts directly.
    """
    deduped = dedupe_files_by_position(audio_files, file_tags, quality_rank=quality_rank)

    matches: List[Dict[str, Any]] = []
    used_files: Set[str] = set()

    for track in tracks:
        best_file = None
        best_score = 0.0

        for f in deduped:
            if f in used_files:
                continue
            tags = file_tags.get(f, {})
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

    return {
        'matches': matches,
        'unmatched_files': [f for f in deduped if f not in used_files],
    }


__all__ = [
    'TITLE_WEIGHT',
    'ARTIST_WEIGHT',
    'POSITION_WEIGHT',
    'NEAR_POSITION_WEIGHT',
    'CROSS_DISC_POSITION_WEIGHT',
    'ALBUM_WEIGHT',
    'MATCH_THRESHOLD',
    'dedupe_files_by_position',
    'score_file_against_track',
    'match_files_to_tracks',
]
