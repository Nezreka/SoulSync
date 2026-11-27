"""Holiday mix generator (#2332): the holiday music you own, as one playlist.

core/holiday_music.py finds it (holiday genres and moods, holiday titles,
and the flags you set on an artist, album or track). the mix is built only
in season (the dates in Settings), so a synced Holiday playlist empties
itself in January instead of playing carols all year. holiday music set to
never makes it empty too.

owned tracks carry no source id; sync matches them by title and artist."""

from __future__ import annotations

from typing import Any, List

from core.personalized.specs import PlaylistKindSpec, get_registry
from core.personalized.types import PlaylistConfig, Track


KIND = 'holiday_mix'


def _database(deps: Any):
    db = getattr(deps, 'database', None) or (deps.get('database') if isinstance(deps, dict) else None)
    if db is None:
        raise RuntimeError("Holiday mix generator deps missing `database`")
    return db


def generate(deps: Any, variant: str, config: PlaylistConfig) -> List[Track]:
    from core.holiday_music import holiday_mix, policy
    db = _database(deps)
    if not policy(db).shows_holiday_mix:
        return []
    rows = holiday_mix(db, size=config.limit, seed=config.seed)
    return [Track(track_name=r['title'] or 'Unknown', artist_name=r['artist'] or 'Unknown',
                  album_name=r['album'] or '', duration_ms=int(r.get('duration') or 0),
                  track_data_json={'name': r['title'], 'artists': [{'name': r['artist']}],
                                   'album': {'name': r['album'] or ''},
                                   'lib2_track_id': r['id'], 'owned': True})
            for r in rows]


SPEC = PlaylistKindSpec(
    kind=KIND,
    name_template='Holiday',
    description='The holiday music in your library. Built only in season (Settings, Discovery).',
    default_config=PlaylistConfig(limit=50, max_per_album=3, max_per_artist=3),
    generator=generate,
    requires_variant=False,
    tags=['personalized'],
)


if get_registry().get(KIND) is None:
    get_registry().register(SPEC)
