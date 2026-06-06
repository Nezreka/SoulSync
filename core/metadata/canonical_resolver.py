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

from core.metadata.canonical_version import (
    score_release_against_files,
    score_release_detail,
)

# Source-selection modes (a per-job setting). See resolve_canonical_for_album.
MODE_ACTIVE_PREFERRED = "active_preferred"  # default: use the active source if it fits, else best-fit
MODE_ACTIVE_ONLY = "active_only"            # only ever the active source
MODE_BEST_FIT = "best_fit"                  # whichever source fits the files best
VALID_MODES = (MODE_ACTIVE_PREFERRED, MODE_ACTIVE_ONLY, MODE_BEST_FIT)


def resolve_canonical_for_album(
    *,
    album_source_ids: Dict[str, str],
    file_tracks: List[Dict[str, Any]],
    fetch_tracklist: Callable[[str, str], Optional[List[Dict[str, Any]]]],
    source_priority: List[str],
    min_score: float = 0.5,
    mode: str = MODE_ACTIVE_PREFERRED,
    primary_source: Optional[str] = None,
    fetch_alternates: Optional[
        Callable[[str, str], Optional[List[Dict[str, Any]]]]
    ] = None,
) -> Optional[Dict[str, Any]]:
    """Pick the canonical release for one album, honoring the source-selection mode.

    ``album_source_ids``: ``{source: album_id}`` the album is linked to.
    ``file_tracks``: on-disk track metadata (``{duration_ms, title}``).
    ``fetch_tracklist(source, album_id)``: returns that release's tracklist (or
    None/[] on miss); injected so callers supply ``get_album_tracks_for_source``
    while tests supply a fake.
    ``source_priority``: source order; ties break toward the earlier source.
    ``primary_source``: the user's active metadata source (defaults to the first
    of ``source_priority``).

    Modes:
      - ``active_preferred`` (default): use the active source's release when the
        album has an ID for it AND it clears ``min_score``; otherwise fall back
        to the best-fit among the remaining sources. So it normally respects the
        user's configured source but self-heals when that link is clearly wrong.
      - ``active_only``: only ever the active source (pinned if it clears the
        floor; never considers other sources).
      - ``best_fit``: whichever source's release best matches the files.

    Returns an enriched dict for the chosen release — ``source``, ``album_id``,
    ``score``, the per-signal breakdown (``count_fit``/``duration_fit``/
    ``title_fit``), ``file_track_count`` vs ``release_track_count``, and a
    ``candidates`` list of everything it scored (so a finding can show WHY the
    pick won and what it beat). ``None`` when there are no files, no resolvable
    candidates, or nothing clears ``min_score``."""
    if not file_tracks:
        return None
    primary = primary_source or (source_priority[0] if source_priority else None)
    scored: List[Dict[str, Any]] = []  # every edition we actually scored
    seen: set = set()  # (source, album_id) already scored — dedup linked + alternates

    def _score_edition(
        source: Optional[str], album_id: Any,
        tracks: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Score one concrete (source, album_id) edition, deduped by that pair.
        Fetches the tracklist when not pre-supplied. Returns the entry (existing
        on a repeat) or None when it has no resolvable tracklist."""
        if not source or not album_id:
            return None
        key = (source, str(album_id))
        if key in seen:
            return next((e for e in scored if (e['source'], e['album_id']) == key), None)
        if tracks is None:
            try:
                tracks = fetch_tracklist(source, str(album_id))
            except Exception:
                tracks = None
        if not tracks:
            return None
        entry = {
            'source': source, 'album_id': str(album_id),
            'track_count': len(tracks),
            'score': round(score_release_against_files(file_tracks, tracks), 4),
            '_tracks': tracks,
        }
        scored.append(entry)
        seen.add(key)
        return entry

    def _score_linked(source: Optional[str]) -> Optional[Dict[str, Any]]:
        return _score_edition(source, album_source_ids.get(source)) if source else None

    def _best_clearing_floor(
        entries: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        best = None
        for e in entries:  # priority-ordered -> strictly-greater = priority tiebreak
            if best is None or e['score'] > best['score'] + 1e-9:
                best = e
        return best if (best and best['score'] >= min_score) else None

    winner: Optional[Dict[str, Any]] = None

    # Active-source modes: try the primary's linked edition first.
    if mode in (MODE_ACTIVE_ONLY, MODE_ACTIVE_PREFERRED):
        p = _score_linked(primary)
        if p and p['score'] >= min_score:
            winner = p

    # best_fit, or active_preferred fallback: score the rest of the linked editions.
    if winner is None and mode != MODE_ACTIVE_ONLY:
        for source in source_priority:
            _score_linked(source)
        winner = _best_clearing_floor(scored)

    # #767-2 expansion: no LINKED edition cleared the floor — e.g. a 1-track single
    # linked only to the 10-track deluxe, whose count_fit tanks its score to 0.1.
    # Fetch the source's OTHER editions of the same release and score those too,
    # then re-pick. Gated on winner-is-None so a well-fitting library never
    # triggers a fetch (zero behaviour change + no API cost for the common case).
    if winner is None and fetch_alternates is not None:
        if mode == MODE_ACTIVE_ONLY:
            expand_sources = [primary] if primary else []
        else:
            expand_sources = [s for s in source_priority if album_source_ids.get(s)]
            for s in album_source_ids:  # any linked source not in the priority list
                if s not in expand_sources:
                    expand_sources.append(s)
        for source in expand_sources:
            linked_id = album_source_ids.get(source)
            if not linked_id:
                continue
            try:
                alts = fetch_alternates(source, str(linked_id)) or []
            except Exception:
                alts = []
            for alt in alts:
                _score_edition(source, alt.get('album_id'), alt.get('tracks'))
        # active_only stays on-source; other modes re-pick across everything scored.
        pool = [e for e in scored if e['source'] == primary] if mode == MODE_ACTIVE_ONLY else scored
        winner = _best_clearing_floor(pool)

    if winner is None:
        return None

    detail = score_release_detail(file_tracks, winner['_tracks'])
    # Pinned-release track titles — already fetched, so free. Capped so a giant
    # box set can't bloat the finding's details_json.
    release_titles = [
        (t.get('title') or t.get('name') or '') for t in winner['_tracks']
    ][:60]
    return {
        'source': winner['source'],
        'album_id': winner['album_id'],
        'score': winner['score'],
        'file_track_count': detail['file_track_count'],
        'release_track_count': detail['release_track_count'],
        'count_fit': detail['count_fit'],
        'duration_fit': detail['duration_fit'],
        'title_fit': detail['title_fit'],
        'release_track_titles': release_titles,
        'candidates': [
            {'source': e['source'], 'album_id': e['album_id'],
             'track_count': e['track_count'], 'score': e['score']}
            for e in scored
        ],
    }


def _item_get(item: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a track item that may be a dict or an object."""
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


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
        dur = _item_get(it, 'duration_ms')
        if dur is None:
            secs = _item_get(it, 'duration')  # some sources give seconds
            dur = int(secs * 1000) if isinstance(secs, (int, float)) and secs else None
        out.append({
            'title': _item_get(it, 'name') or _item_get(it, 'title') or '',
            'track_number': _item_get(it, 'track_number'),
            'duration_ms': dur,
        })
    return out or None


# Edition/format qualifiers stripped when deciding whether two album titles name
# the SAME underlying release (so "Scatterbrain", "Scatterbrain (Deluxe)" and
# "Scatterbrain - Single" all collapse to one key). Generous on purpose: the
# scorer is the real precision gate, so over-including an edition is harmless —
# it just won't win. Under-including is what hides the right single.
_EDITION_TOKENS = frozenset({
    "deluxe", "expanded", "edition", "remaster", "remastered", "single", "ep",
    "anniversary", "special", "bonus", "explicit", "clean", "version", "extended",
    "complete", "collectors", "reissue", "original", "standard",
})


def _release_name_key(name: str) -> str:
    """Normalise an album title to a comparison key for 'same release': lowercase,
    drop bracketed qualifiers, strip punctuation, and remove edition/format words.
    Pure — unit-tested directly."""
    import re
    if not name:
        return ""
    t = str(name).lower()
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t)       # (Deluxe Edition), [Remastered]
    t = re.sub(r"[^a-z0-9 ]", " ", t)            # punctuation -> space ("- Single")
    toks = [w for w in t.split() if w not in _EDITION_TOKENS]
    return " ".join(toks)


def _same_release(a: str, b: str) -> bool:
    """True when two album titles name the same underlying release (edition-blind)."""
    ka, kb = _release_name_key(a), _release_name_key(b)
    return bool(ka) and ka == kb


def default_fetch_alternates(
    source: str, album_id: str, *,
    artist_id: str = "", artist_name: str = "", album_title: str = "",
    max_editions: int = 6,
) -> Optional[List[Dict[str, Any]]]:
    """Production ``fetch_alternates``: list a release's OTHER editions on a source
    and return ``[{album_id, tracks}, ...]`` for the canonical resolver to score.

    Strategy: discover the album's artist + title (from supplied context, else one
    ``get_album_for_source`` call), list the artist's albums+singles, keep the ones
    whose title is the same release (edition-blind), and fetch each one's tracklist.
    Best-effort throughout — returns ``[]`` on any miss so the resolver simply
    finds no alternates rather than erroring. Only ever called on the misfit path,
    so the artist-albums + per-edition fetches don't run for a well-fitting library."""
    try:
        from core.metadata.album_tracks import (
            get_album_for_source,
            get_artist_albums_for_source,
        )
    except Exception:
        return []

    title = album_title
    a_id, a_name = str(artist_id or ""), str(artist_name or "")
    if not (title and (a_id or a_name)):
        try:
            meta = get_album_for_source(source, str(album_id)) or {}
        except Exception:
            meta = {}
        title = title or (_item_get(meta, "name") or _item_get(meta, "title") or "")
        a_id = a_id or str(_item_get(meta, "artist_id") or _item_get(meta, "artistId") or "")
        a_name = a_name or str(_item_get(meta, "artist") or _item_get(meta, "artist_name") or "")
    if not title or not (a_id or a_name):
        return []

    try:
        albums = get_artist_albums_for_source(
            source, a_id, a_name, album_type="album,single", limit=50,
        ) or []
    except Exception:
        albums = []

    out: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for alb in albums:
        if len(out) >= max_editions:
            break
        alb_title = _item_get(alb, "name") or _item_get(alb, "title") or ""
        if not _same_release(title, alb_title):
            continue
        alb_id = _item_get(alb, "id") or _item_get(alb, "album_id")
        if not alb_id or str(alb_id) in seen_ids:
            continue
        seen_ids.add(str(alb_id))
        tracks = default_fetch_tracklist(source, str(alb_id))
        if tracks:
            out.append({"album_id": str(alb_id), "tracks": tracks})
    return out


def _lookup_artist_thumb(db, artist_id) -> Optional[str]:
    """Best-effort artist thumb URL by id. Returns None on missing column / any
    error (the artists table doesn't have thumb_url in every schema)."""
    if not artist_id:
        return None
    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(artists)")
        if 'thumb_url' not in {r[1] for r in cursor.fetchall()}:
            return None
        cursor.execute("SELECT thumb_url FROM artists WHERE id = ?", (str(artist_id),))
        row = cursor.fetchone()
        return (row[0] or None) if row else None
    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def resolve_and_store_canonical_for_album(
    db,
    album_id,
    *,
    fetch_tracklist: Optional[Callable[[str, str], Any]] = None,
    fetch_alternates: Optional[Callable[[str, str], Any]] = None,
    source_priority: Optional[List[str]] = None,
    min_score: float = 0.5,
    store: bool = True,
    mode: str = MODE_ACTIVE_PREFERRED,
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
    if fetch_alternates is None:
        # Default alternates fetcher, primed with the artist/title we already
        # loaded (no extra get_album call). Only fires on the misfit path.
        _art_id = str(album_data.get('artist_id') or '')
        _art_name = album_data.get('artist_name') or ''
        _title = album_data.get('title') or ''

        def fetch_alternates(source, aid):  # noqa: ANN001
            return default_fetch_alternates(
                source, aid,
                artist_id=_art_id, artist_name=_art_name, album_title=_title,
            )
    primary_source = None
    if source_priority is None:
        try:
            from core.metadata_service import get_primary_source, get_source_priority
            primary_source = get_primary_source()
            source_priority = get_source_priority(primary_source)
        except Exception:
            source_priority = list(source_ids.keys())

    result = resolve_canonical_for_album(
        album_source_ids=source_ids,
        file_tracks=file_tracks,
        fetch_tracklist=fetch_tracklist,
        fetch_alternates=fetch_alternates,
        source_priority=source_priority,
        min_score=min_score,
        mode=mode,
        primary_source=primary_source,
    )
    if result:
        # Album/artist/art context for richer findings (read from the row we
        # already loaded — no extra query). Storage only uses source/id/score.
        result['album_title'] = album_data.get('title') or ''
        result['artist_name'] = album_data.get('artist_name') or ''
        # Free context off the album row + the data we already gathered.
        if album_data.get('year'):
            result['year'] = album_data['year']
        result['db_track_count'] = album_data.get('track_count') or len(file_tracks)
        if album_data.get('duration'):
            result['db_duration_ms'] = album_data['duration']
        result['linked_sources'] = source_ids  # {source: album_id} the album points at now
        result['file_track_titles'] = [ft.get('title') or '' for ft in file_tracks][:60]
        if album_data.get('thumb_url'):
            result['album_thumb_url'] = album_data['thumb_url']
        # Artist thumb via a guarded lookup (not the shared album loader — some
        # schemas have no artists.thumb_url column). Only runs for resolved
        # albums, so no cost on the no-source-id short-circuit majority.
        artist_thumb = _lookup_artist_thumb(db, album_data.get('artist_id'))
        if artist_thumb:
            result['artist_thumb_url'] = artist_thumb
        if store:
            db.set_album_canonical(album_id, result['source'], result['album_id'], result['score'])
    return result


__all__ = [
    "resolve_canonical_for_album",
    "resolve_and_store_canonical_for_album",
    "default_fetch_tracklist",
    "default_fetch_alternates",
]
