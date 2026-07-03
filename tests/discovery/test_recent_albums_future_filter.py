"""Fresh Tape / Release Radar candidate fetch must not be starved by future albums.

Regression: get_discovery_recent_albums orders release_date DESC, so announced-but-
unreleased albums dated to a LATER YEAR sort to the very top and consumed the album
budget before the scanner's in-loop is_future_release skip ran — leaving only a handful
of released albums to draw tracks from (the reported "Fresh Tape only has 5-10 tracks").
exclude_future_years drops next-year albums at the query so released ones fill the budget.
"""

from __future__ import annotations

from datetime import datetime

from database.music_database import MusicDatabase


def _insert(db, **kw):
    with db._get_connection() as conn:
        conn.execute(
            """INSERT INTO discovery_recent_albums
               (album_spotify_id, album_name, artist_name, artist_spotify_id,
                album_cover_url, release_date, album_type, source, profile_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (kw['id'], kw['name'], kw['artist'], kw['id'] + '_a', '',
             kw['release_date'], kw.get('album_type', 'album'), kw.get('source', 'spotify'), 1))
        conn.commit()


def test_future_year_albums_excluded_released_kept(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "t.db"))
    this_year = datetime.now().year
    next_year = this_year + 1
    _insert(db, id='past1', name='Released A', artist='X', release_date=f'{this_year - 1}-03-01')
    _insert(db, id='past2', name='Released B', artist='Y', release_date=f'{this_year}-01-15')
    _insert(db, id='fut1', name='Announced', artist='Z', release_date=f'{next_year}-02-01')
    _insert(db, id='fut2', name='Year Only Future', artist='W', release_date=str(next_year))
    _insert(db, id='blank', name='Unknown Date', artist='Q', release_date='')

    names_all = {a['album_name'] for a in db.get_discovery_recent_albums(limit=50, source='spotify')}
    assert 'Announced' in names_all          # without the flag, futures are present (and sort first)

    filtered = db.get_discovery_recent_albums(limit=50, source='spotify', exclude_future_years=True)
    names = {a['album_name'] for a in filtered}
    assert 'Announced' not in names          # next-year album dropped
    assert 'Year Only Future' not in names   # YYYY-only future dropped
    assert {'Released A', 'Released B'} <= names   # released kept
    assert 'Unknown Date' in names           # blank date kept (treated as released)


def test_future_filter_does_not_over_trim_when_all_released(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "t2.db"))
    this_year = datetime.now().year
    for i in range(8):
        _insert(db, id=f'r{i}', name=f'Album {i}', artist=f'A{i}',
                release_date=f'{this_year}-0{(i % 9) + 1}-01')
    filtered = db.get_discovery_recent_albums(limit=300, source='spotify', exclude_future_years=True)
    assert len(filtered) == 8                # every released album survives, budget honored
