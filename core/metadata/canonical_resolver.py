"""Resolve (and persist) the canonical release for an album — Stage 2 of #765.

Stage 1 gave us the pure scorer (``core.metadata.canonical_version``). This
module turns it into an end-to-end resolver: gather the album's candidate
releases (one per metadata-source ID it has), score each against the on-disk
files, and return the best fit. Wiring (backfill job / enrichment hook) and the
DB store live alongside; the decision logic here is kept dependency-injected
(``fetch_tracklist`` is passed in) so it's fully unit-testable without live APIs
or real files.

Still NO consumer reads the result in Stage 2 — populating the columns is
behavior-neutral. Stages 3-4 wire the Reorganizer and Track Number Repair to
read it.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.metadata.canonical_version import pick_canonical_release


def resolve_canonical_for_album(
    *,
    album_source_ids: Dict[str, str],
    file_tracks: List[Dict[str, Any]],
    fetch_tracklist: Callable[[str, str], Optional[List[Dict[str, Any]]]],
    source_priority: List[str],
    min_score: float = 0.5,
) -> Optional[Dict[str, Any]]:
    """Pick the canonical release for one album.

    ``album_source_ids``: ``{source: album_id}`` the album is linked to.
    ``file_tracks``: on-disk track metadata (``{duration_ms, title}``).
    ``fetch_tracklist(source, album_id)``: returns that release's tracklist (or
    None/[] on miss); injected so callers supply ``get_album_tracks_for_source``
    while tests supply a fake.
    ``source_priority``: order to build candidates in — ties break toward the
    earlier (higher-priority) source, keeping the choice deterministic.

    Returns ``{'source', 'album_id', 'score'}`` for the best fit, or ``None``
    when there are no files, no resolvable candidates, or nothing clears
    ``min_score`` (caller leaves the album unresolved → tools fall back)."""
    if not file_tracks:
        return None

    candidates: List[Dict[str, Any]] = []
    for source in source_priority:
        album_id = album_source_ids.get(source)
        if not album_id:
            continue
        try:
            tracks = fetch_tracklist(source, str(album_id))
        except Exception:
            tracks = None
        if tracks:
            candidates.append({
                'source': source,
                'album_id': str(album_id),
                'tracks': tracks,
            })

    if not candidates:
        return None

    best, score = pick_canonical_release(file_tracks, candidates, min_score=min_score)
    if not best:
        return None
    return {
        'source': best['source'],
        'album_id': best['album_id'],
        'score': round(score, 4),
    }


__all__ = ["resolve_canonical_for_album"]
