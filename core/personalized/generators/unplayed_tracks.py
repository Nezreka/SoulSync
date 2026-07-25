"""Unplayed Tracks generator — library songs you haven't heard yet.

Queries the local ``tracks`` table for tracks where ``play_count`` is
zero (or NULL) and returns them as a personalized playlist.  Supports
filtering by age (``max_days_since_added`` in ``config.extra``) and
diversity caps (``max_per_album`` / ``max_per_artist``).

Tracks are sorted newest-first so the freshest additions surface
first.  Since these live in the user's library, the sync pipeline
matches them against the media-server library and creates a server-
side playlist — no downloads required.

Custom naming: set ``config.extra['name']`` to override the default
playlist name on first creation (e.g. ``{'name': 'New & Unheard'}``).
After creation the name can also be edited via the UI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'unplayed_tracks'


def _diversity_limit(
    tracks: List[Track],
    limit: int,
    max_per_artist: int,
    max_per_album: int,
) -> List[Track]:
    """Enforce per-artist and per-album diversity caps.

    Greedy round-robin: picks one track per artist (up to
    ``max_per_artist``) and one per album (up to ``max_per_album``),
    then fills remaining slots from what's left, until ``limit``
    tracks are selected or the pool is exhausted."""

    def _key(t: Track, field: str) -> str:
        val = getattr(t, field, None) or ''
        return val.strip().lower() or '_unknown'

    by_artist: dict[str, List[Track]] = {}
    by_album: dict[str, List[Track]] = {}
    for t in tracks:
        by_artist.setdefault(_key(t, 'artist_name'), []).append(t)
        by_album.setdefault(_key(t, 'album_name'), []).append(t)

    picked: List[Track] = []
    seen_ids: set = set()

    artist_budget: dict[str, int] = {k: max_per_artist for k in by_artist}
    album_budget: dict[str, int] = {k: max_per_album for k in by_album}

    for t in tracks:
        if len(picked) >= limit:
            break
        aid = id(t)
        if aid in seen_ids:
            continue
        ak = _key(t, 'artist_name')
        bk = _key(t, 'album_name')
        if artist_budget.get(ak, 0) <= 0 and album_budget.get(bk, 0) <= 0:
            continue
        picked.append(t)
        seen_ids.add(aid)
        artist_budget[ak] = artist_budget.get(ak, 1) - 1
        album_budget[bk] = album_budget.get(bk, 1) - 1

    return picked


def _get_db(deps: Any):
    """Extract the database handle from the deps object."""
    db = getattr(deps, 'database', None)
    if db is not None:
        return db
    if isinstance(deps, dict):
        return deps.get('database')
    return None


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    """Return unplayed library tracks, newest-first, trimmed to ``config.limit``.

    Config extras:
      - ``max_days_since_added`` (int | None): only include tracks added
        within this many days.  ``None`` or ``0`` means no age filter.
    """
    db = _get_db(deps)
    if db is None:
        raise RuntimeError('Unplayed Tracks generator deps missing database')

    max_days: Optional[int] = config.extra.get('max_days_since_added')
    cutoff: Optional[str] = None
    if max_days and max_days > 0:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max_days)
        ).strftime('%Y-%m-%d %H:%M:%S')

    where = ['(t.play_count IS NULL OR t.play_count = 0)']
    params: list = []
    if cutoff:
        where.append('t.created_at >= ?')
        params.append(cutoff)

    query = f"""
        SELECT t.id, t.title, t.duration,
               t.play_count, t.created_at, t.spotify_track_id,
               t.deezer_id, t.itunes_track_id,
               COALESCE(t.track_artist, ar.name, 'Unknown') AS artist_name,
               a.title AS album_name
        FROM tracks t
        LEFT JOIN artists ar ON t.artist_id = ar.id
        LEFT JOIN albums a ON t.album_id = a.id
        WHERE {' AND '.join(where)}
        ORDER BY t.created_at DESC
    """

    with db._get_connection() as conn:
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

    tracks: List[Track] = []
    for row in rows:
        tracks.append(Track(
            track_name=row['title'] or 'Unknown',
            artist_name=(row['artist_name'] or '').strip() or 'Unknown',
            album_name=(row['album_name'] or '').strip() or '',
            duration_ms=int(row['duration'] or 0),
            spotify_track_id=row['spotify_track_id'],
            deezer_track_id=row['deezer_id'],
            itunes_track_id=row['itunes_track_id'],
            source='library',
        ))

    tracks = _diversity_limit(
        tracks,
        limit=config.limit,
        max_per_artist=config.max_per_artist,
        max_per_album=config.max_per_album,
    )
    return tracks


def display_name_with_config(variant: str, config: PlaylistConfig) -> str:
    """Resolve the playlist name, honouring ``config.extra['name']``."""
    custom = (config.extra or {}).get('name')
    if custom:
        return str(custom).strip()
    return 'Unplayed Tracks'


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Unplayed Tracks',
    description="Library songs you haven't played yet, newest additions first.",
    default_config=PlaylistConfig(
        limit=500,
        max_per_album=5,
        max_per_artist=3,
        extra={'max_days_since_added': None, 'name': 'Unplayed Tracks'},
    ),
    generator=generate,
    requires_variant=False,
    tags=['library'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
