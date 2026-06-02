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


def default_fetch_tracklist(source: str, album_id: str) -> Optional[List[Dict[str, Any]]]:
    """Production ``fetch_tracklist``: pull a release's tracklist from a metadata
    source and normalise to ``{title, track_number, duration_ms}``. Duration is
    best-effort (not every source exposes it); when absent the scorer just leans
    on track-count + title. Returns None on any failure."""
    try:
        from core.metadata_service import get_album_tracks_for_source
        data = get_album_tracks_for_source(source, album_id)
    except Exception:
        return None
    items = data if isinstance(data, list) else (
        (data.get('items') or data.get('tracks') or []) if isinstance(data, dict) else []
    )
    if isinstance(items, dict):  # {'tracks': {'items': [...]}}
        items = items.get('items') or []
    out: List[Dict[str, Any]] = []
    for it in items:
        get = it.get if isinstance(it, dict) else (lambda k, d=None: getattr(it, k, d))
        dur = get('duration_ms')
        if dur is None:
            secs = get('duration')  # some sources give seconds
            dur = int(secs * 1000) if isinstance(secs, (int, float)) and secs else None
        out.append({
            'title': get('name') or get('title') or '',
            'track_number': get('track_number'),
            'duration_ms': dur,
        })
    return out or None


def resolve_and_store_canonical_for_album(
    db,
    album_id,
    *,
    fetch_tracklist: Optional[Callable[[str, str], Any]] = None,
    source_priority: Optional[List[str]] = None,
    min_score: float = 0.5,
    store: bool = True,
) -> Optional[Dict[str, Any]]:
    """Gather an album's source IDs + its tracks' (duration, title) from the DB,
    resolve the best-fit canonical release, and (when ``store``) persist it.
    Returns the resolved ``{source, album_id, score}`` or None when unresolved.
    ``store=False`` resolves without writing — used by the backfill job's dry run.

    Uses the SAME album/source-id loader the Reorganizer uses
    (``load_album_and_tracks`` + ``_extract_source_ids``) so the canonical is
    chosen over exactly the source IDs the reorganizer sees. Scores off the DB
    track rows' ``duration`` (stored in ms) + ``title`` — the library's view of
    the files — so no per-file disk reads are needed."""
    from core.library_reorganize import _extract_source_ids, load_album_and_tracks

    album_data, tracks = load_album_and_tracks(db, album_id)
    if not album_data or not tracks:
        return None
    source_ids = {s: v for s, v in _extract_source_ids(album_data).items() if v}
    if not source_ids:
        return None

    file_tracks = [
        {'duration_ms': t.get('duration') or 0, 'title': t.get('title') or ''}
        for t in tracks
    ]

    if fetch_tracklist is None:
        fetch_tracklist = default_fetch_tracklist
    if source_priority is None:
        try:
            from core.metadata_service import get_primary_source, get_source_priority
            source_priority = get_source_priority(get_primary_source())
        except Exception:
            source_priority = list(source_ids.keys())

    result = resolve_canonical_for_album(
        album_source_ids=source_ids,
        file_tracks=file_tracks,
        fetch_tracklist=fetch_tracklist,
        source_priority=source_priority,
        min_score=min_score,
    )
    if result and store:
        db.set_album_canonical(album_id, result['source'], result['album_id'], result['score'])
    return result


__all__ = [
    "resolve_canonical_for_album",
    "resolve_and_store_canonical_for_album",
    "default_fetch_tracklist",
]
