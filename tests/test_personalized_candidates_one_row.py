"""_candidates reads the pool's (id, near) pairs as one row, and draws exactly
what the row-per-entry read drew.

sqlite3 takes the GIL back for every row it hands to Python; next to busy
threads each take waits up to the switch interval. 15.5k rows took 185-370 s
live; the same pairs as one JSON array, 25 ms."""

from __future__ import annotations

import random

import pytest

import core.personalized_playlists as pp
from core.personalized.taste_draw import nearest
from tests.test_discover_owned_pool_cache import FileDatabase


@pytest.fixture
def db(tmp_path):
    pp.clear_owned_pool_cache()
    database = FileDatabase(tmp_path / "music.db")
    for i in range(300):
        database.pool(f"t{i}", spotify=f"sp-{i}")
    with database._get_connection() as conn:
        conn.execute("UPDATE discovery_pool SET artist_name = 'artist ' || (id % 40), "
                     "popularity = id % 100")
    yield database
    pp.clear_owned_pool_cache()


def _row_per_entry_draw(db, service, fetch_limit, near_names, extra_where="", extra_params=()):
    """the algorithm as it was: one row per pool entry, then the same draw"""
    near_sql = f"LOWER(artist_name) IN ({','.join('?' * len(near_names))})"
    with db._get_connection() as conn:
        pp._ensure_owned_ids(conn)
        ids = conn.execute(
            f"SELECT id, {near_sql} FROM discovery_pool {service._POOL_WHERE} "
            f"{service._OWNED_CLAUSE} {extra_where}",
            tuple(near_names) + ("spotify", 1, 1) + tuple(extra_params)).fetchall()
    near = [r[0] for r in ids if r[1]]
    far = [r[0] for r in ids if not r[1]]
    take_near = min(len(near), fetch_limit * 2 // 3 if far else fetch_limit)
    chosen = service.rng.sample(near, take_near)
    chosen += service.rng.sample(far, min(len(far), fetch_limit - take_near))
    return chosen


@pytest.mark.parametrize("extra_where,extra_params", [("", ()), ("AND popularity >= ?", (50,))])
def test_the_one_row_read_draws_the_same_tracks(db, monkeypatch, extra_where, extra_params):
    near_artists = {f"artist {i}": 1.0 - i / 100 for i in range(0, 40, 3)}
    service = pp.PersonalizedPlaylistsService(db)
    monkeypatch.setattr(service, "_taste_signals", lambda: ({}, near_artists))
    names = nearest(near_artists, service.NEAR_ARTISTS)

    service.rng = random.Random(7)
    expected = _row_per_entry_draw(db, service, 60, names, extra_where, extra_params)
    service.rng = random.Random(7)
    drawn = service._candidates(source="spotify", fetch_limit=60,
                                extra_where=extra_where, extra_params=extra_params)
    with db._get_connection() as conn:
        name_of = dict(conn.execute("SELECT id, track_name FROM discovery_pool").fetchall())
    assert [row["track_name"] for row in drawn] == [name_of[i] for i in expected]
    assert len(expected) == 60


def test_an_empty_pool_draws_nothing(tmp_path, monkeypatch):
    pp.clear_owned_pool_cache()
    service = pp.PersonalizedPlaylistsService(FileDatabase(tmp_path / "empty.db"))
    monkeypatch.setattr(service, "_taste_signals", lambda: ({}, {"x": 1.0}))
    assert service._candidates(source="spotify", fetch_limit=10) == []
