"""Recent Unheard generator — recently added songs you haven't heard yet.

Queries the local ``tracks`` table for tracks where ``play_count`` is
zero (or NULL), sorted newest-first, and returns the top N as a
personalized playlist.  Supports filtering by age
(``max_days_since_added`` in ``config.extra``).

Custom naming: set ``config.extra['name']`` to override the default
playlist name on first creation (e.g. ``{'name': 'Fresh & Silent'}``).
After creation the name can also be edited via the UI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'recent_unheard'


def _get_db(deps: Any):
    """Extract the database handle from the deps object."""
    db = getattr(deps, 'database', None)
    if db is not None:
        return db
    if isinstance(deps, dict):
        return deps.get('database')
    return None


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    """Return recently added library tracks never played, newest-first,
    trimmed to ``config.limit``.

    Config extras:
      - ``max_days_since_added`` (int | None): only include tracks added
        within this many days.  ``None`` or ``0`` means no age filter.
    """
    db = _get_db(deps)
    if db is None:
        raise RuntimeError('Recent Unheard generator deps missing database')

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
        LIMIT ?
    """

    with db._get_connection() as conn:
        cursor = conn.execute(query, params + [config.limit])
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

    from core.personalized.generators.unplayed_tracks import _diversity_limit
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
    return 'Recent Unheard'


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Recent Unheard',
    description="Recently added songs you haven't played yet.",
    default_config=PlaylistConfig(
        limit=500,
        max_per_album=5,
        max_per_artist=3,
        extra={'max_days_since_added': None, 'name': 'Recent Unheard'},
    ),
    generator=generate,
    requires_variant=False,
    tags=['library'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
