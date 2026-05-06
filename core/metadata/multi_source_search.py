"""Multi-source parallel metadata search.

Both the Track Redownload modal and the Artist Enhance Quality flow
need to find the best metadata match for a known track (we have the
title + artist + duration from the user's library; we want to find
the matching entry in Spotify / iTunes / Deezer / Discogs / Hydrabase
to drive the wishlist re-download).

Pre-extraction, redownload had a fully-fledged multi-source parallel
search (parallel ThreadPoolExecutor, per-source query optimization,
"current match" flagging via stored source IDs, per-result scoring)
while enhance had a hardcoded Spotify-direct → Spotify-search →
iTunes-fallback chain that only searched ONE source. That's why
redownload "worked" for users without Spotify (it'd find matches via
iTunes / Deezer in parallel) and enhance silently failed (single
fallback returned junk).

This module owns the search logic. Both endpoints call
``search_all_sources`` and get back the same shape — same scoring,
same source-optimized queries, same "current match" semantics. UI
behavior diverges per-endpoint (redownload renders a picker, enhance
auto-picks the best across all sources) but the metadata-search
contract is shared.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class TrackQuery:
    """Inputs needed to run a multi-source metadata search for one track."""
    title: str
    artist: str
    album: str = ''
    # Library-side duration in milliseconds. Used for the duration
    # similarity component of scoring; pass 0 when unknown (scoring
    # falls back to a neutral 0.5 weight).
    duration_ms: int = 0
    # Source-native track IDs already stored on the library track,
    # used for the "is_current_match" flag in the per-result rendering
    # so the UI can highlight the entry that produced the existing file.
    spotify_track_id: Optional[str] = None
    deezer_id: Optional[str] = None


@dataclass
class MultiSourceResult:
    """Aggregated output from ``search_all_sources``."""
    # source_name → list of result dicts (already per-source sorted:
    # is_current_match first, then descending match_score). Dict shape
    # is JSON-serializable for direct return to the frontend (the
    # redownload picker uses these as-is).
    metadata_results: Dict[str, List[dict]] = field(default_factory=dict)
    # source_name → list of source-native Track objects, parallel-indexed
    # to ``metadata_results[source_name]``. Used by callers (Enhance
    # Quality) that need to build a wishlist payload from the chosen
    # match — the dict shape lacks per-source fields like full album
    # data / external_urls / popularity that the wishlist needs.
    raw_tracks: Dict[str, List[Any]] = field(default_factory=dict)
    # Best match across all sources, or None if every source returned
    # nothing. Shape: ``{'source': str, 'index': int, 'score': float}``.
    best_match: Optional[dict] = None

    def best_track(self) -> Optional[Any]:
        """Convenience: return the source-native Track object for the
        cross-source best match, or None if no match was found."""
        if not self.best_match:
            return None
        source = self.best_match['source']
        index = self.best_match['index']
        tracks = self.raw_tracks.get(source) or []
        return tracks[index] if index < len(tracks) else None


def _score_match(query: TrackQuery, result: dict) -> float:
    """Score one result dict against the query.

    Weights: title 0.5, artist 0.35, duration 0.15. Duration component
    is neutral (0.5) when the library track has no duration on file.

    These weights match the redownload pre-extraction implementation
    so existing callers keep their scoring behavior identical.
    """
    title_sim = SequenceMatcher(
        None, query.title.lower(), (result.get('name') or '').lower()
    ).ratio()
    artist_sim = SequenceMatcher(
        None, query.artist.lower(), (result.get('artist') or '').lower()
    ).ratio()
    if query.duration_ms:
        dur_diff = abs(query.duration_ms - (result.get('duration_ms') or 0))
        dur_score = max(0.0, 1.0 - dur_diff / 30000.0)
    else:
        dur_score = 0.5
    return round((title_sim * 0.5 + artist_sim * 0.35 + dur_score * 0.15), 3)


def _build_source_query(source_name: str, query: TrackQuery, clean_title: str) -> str:
    """Build the source-optimized search query string.

    Deezer's API responds best to its native field-prefixed syntax
    (``artist:"X" track:"Y"``) — empirically returns better matches
    than a plain query for ambiguous track names. Other sources use
    the artist + clean-title concatenation.
    """
    if source_name == 'deezer':
        return f'artist:"{query.artist}" track:"{clean_title}"'
    return f"{query.artist} {clean_title}"


def _search_one_source(source_name: str, client: Any,
                       query: TrackQuery, clean_title: str
                       ) -> Tuple[str, List[dict], List[Any]]:
    """Run one source's search with three-tier query fallback.

    Tier 1: source-optimized query (Deezer's structured form, others' plain).
    Tier 2: plain ``artist + title`` if tier 1 returned nothing.
    Tier 3: title-only as last resort.

    Returns ``(source_name, results, raw_tracks)``:
    - ``results`` are the JSON-serializable dicts (id / name / artist /
      etc.), sorted by is_current_match first, then descending match_score
    - ``raw_tracks`` are the source-native Track objects, parallel-indexed
      to ``results``, for callers that need richer per-source fields
      than the dict surface (album_type, external_urls, etc).
    """
    try:
        primary_q = _build_source_query(source_name, query, clean_title)
        plain_q = f"{query.artist} {clean_title}"
        title_q = clean_title

        logger.info(f"[MultiSourceSearch] Searching {source_name} for: {primary_q}")
        track_objs = client.search_tracks(primary_q, limit=10)
        if not track_objs and primary_q != plain_q:
            track_objs = client.search_tracks(plain_q, limit=10)
        if not track_objs and clean_title != plain_q:
            track_objs = client.search_tracks(title_q, limit=10)
        logger.info(f"[MultiSourceSearch] {source_name} returned {len(track_objs)} results")

        scored: List[Tuple[dict, Any]] = []
        for t in track_objs:
            r = {
                'id': str(getattr(t, 'id', '')),
                'name': getattr(t, 'name', '') or '',
                'artist': ', '.join(t.artists) if getattr(t, 'artists', None) else '',
                'album': getattr(t, 'album', '') or '',
                'duration_ms': getattr(t, 'duration_ms', 0) or 0,
                'image_url': getattr(t, 'image_url', '') or '',
                'is_current_match': False,
            }
            # Flag the result that backs the user's existing library
            # track so the UI can highlight it.
            if source_name == 'spotify' and query.spotify_track_id and r['id'] == str(query.spotify_track_id):
                r['is_current_match'] = True
            elif source_name == 'deezer' and query.deezer_id and r['id'] == str(query.deezer_id):
                r['is_current_match'] = True
            r['match_score'] = _score_match(query, r)
            scored.append((r, t))

        # Sort dict + raw track in lockstep so raw_tracks[i] is the
        # source-native object behind metadata_results[source][i].
        scored.sort(key=lambda pair: (-int(pair[0]['is_current_match']), -pair[0]['match_score']))
        results = [pair[0] for pair in scored]
        raw_tracks = [pair[1] for pair in scored]
        return source_name, results, raw_tracks
    except Exception as exc:
        logger.error(
            f"[MultiSourceSearch] Search failed for {source_name}: {exc}",
            exc_info=True,
        )
        return source_name, [], []


def search_all_sources(query: TrackQuery,
                       sources: List[Tuple[str, Any]],
                       clean_title: Optional[str] = None,
                       max_workers: int = 3) -> MultiSourceResult:
    """Run a parallel metadata search across every source in ``sources``.

    Args:
        query: TrackQuery describing the library track we want to match.
        sources: List of ``(name, client)`` pairs. Each client must
            implement ``search_tracks(query: str, limit: int) -> List[Track]``
            where each Track has ``.id``, ``.name``, ``.artists`` (list),
            ``.album``, ``.duration_ms``, ``.image_url`` attributes.
            All five primary metadata clients (Spotify / iTunes /
            Deezer / Discogs / Hydrabase) satisfy this contract.
        clean_title: Optional pre-cleaned track title (e.g. with
            "(Remastered)" / "(Single Version)" suffixes stripped).
            Defaults to ``query.title`` if not supplied.
        max_workers: ThreadPoolExecutor pool size. Default 3 matches
            the redownload endpoint's pre-extraction default — bumping
            higher rate-limits on slower sources without speeding up
            the slowest source's response.

    Returns:
        MultiSourceResult with per-source results + cross-source best match.
    """
    if clean_title is None:
        clean_title = query.title

    if not sources:
        return MultiSourceResult()

    metadata_results: Dict[str, List[dict]] = {}
    raw_tracks: Dict[str, List[Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_search_one_source, name, client, query, clean_title): name
            for name, client in sources
        }
        for future in as_completed(futures):
            source_name, results, raws = future.result()
            metadata_results[source_name] = results
            raw_tracks[source_name] = raws

    best_match: Optional[dict] = None
    for source, results in metadata_results.items():
        if results:
            top = results[0]
            if best_match is None or top['match_score'] > best_match['score']:
                best_match = {
                    'source': source,
                    'index': 0,
                    'score': top['match_score'],
                }

    return MultiSourceResult(
        metadata_results=metadata_results,
        raw_tracks=raw_tracks,
        best_match=best_match,
    )
