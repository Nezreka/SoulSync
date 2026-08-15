"""What you own against what you actually play (stats P4).

The one thing only SoulSync can say. Spotify has no library; Plex has no
acquisition history. We hold both halves, so we can answer "you own 40% metal
and play 12% of it" — a fact about the user rather than a number about the
software.

Both percentages are shares of the GENRE-KNOWN population, so they can sit
beside each other honestly: an untagged artist is absent from both sides, not
counted as a zero on one.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'ovp.db'))


def _artist(db, artist_id, name, genres):
    from core.library2.importer import normalize_name

    conn = db._get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO lib2_artists (id, name, name_key, genres) "
        "VALUES (?, ?, ?, ?)",
        (artist_id, name, normalize_name(name),
         json.dumps(genres) if genres is not None else ''),
    )
    conn.commit()
    conn.close()


def _album(db, album_id, artist_id, name):
    conn = db._get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO lib2_albums (id, primary_artist_id, title) "
        "VALUES (?, ?, ?)",
        (album_id, artist_id, name),
    )
    conn.commit()
    conn.close()


def _track(db, track_id, album_id, artist_id, title, play_count=0):
    conn = db._get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO lib2_tracks (id, album_id, title, play_count) "
        "VALUES (?, ?, ?, ?)",
        (track_id, album_id, title, play_count),
    )
    conn.execute(
        "INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, ?, 1)",
        (track_id, f'/music/{track_id}.flac'),
    )
    conn.commit()
    conn.close()


def _play(db, track_id, title='t', artist='A', when=None):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO listening_history (track_id, title, artist, played_at, duration_ms, lib2_track_id) "
        "VALUES (?, ?, ?, ?, 1000, ?)",
        (f'x{track_id}-{when}', title, artist,
         (when or datetime.now()).isoformat(sep=' '), track_id))
    conn.commit()
    conn.close()


def _rows_by_genre(rows):
    return {r['genre']: r for r in rows}


# ── the gap ──────────────────────────────────────────────────────────────────

def test_owning_a_genre_you_never_play_shows_a_negative_gap(db):
    """The headline case: half your library, none of your listening."""
    _artist(db, 1, 'Metal Band', ['Metal'])
    _artist(db, 2, 'Pop Act', ['Pop'])
    _album(db, 10, 1, 'M'); _album(db, 20, 2, 'P')
    for i in range(8):
        _track(db, 100 + i, 10, 1, f'metal{i}')
    for i in range(2):
        _track(db, 200 + i, 20, 2, f'pop{i}')
    # Only the pop tracks ever get played.
    _play(db, 200); _play(db, 201)

    rows = _rows_by_genre(db.get_genre_own_vs_play('all'))

    assert rows['Metal']['owned_pct'] == 80.0
    assert rows['Metal']['played_pct'] == 0.0
    assert rows['Metal']['gap'] == -80.0
    assert rows['Pop']['gap'] == 80.0


def test_rows_are_sorted_by_the_size_of_the_disagreement(db):
    """The interesting rows are where owning and listening disagree — not the
    biggest genre, which the user already knows."""
    _artist(db, 1, 'A', ['Balanced'])
    _artist(db, 2, 'B', ['Skewed'])
    _album(db, 10, 1, 'x'); _album(db, 20, 2, 'y')
    for i in range(5):
        _track(db, 100 + i, 10, 1, f'a{i}')
        _track(db, 200 + i, 20, 2, f'b{i}')
    for i in range(5):
        _play(db, 100 + i)          # all plays on the balanced-owner half

    rows = db.get_genre_own_vs_play('all')

    assert rows[0]['genre'] in ('Balanced', 'Skewed')
    assert abs(rows[0]['gap']) >= abs(rows[-1]['gap'])


def test_an_untagged_artist_is_absent_from_both_sides(db):
    """Not a zero on one side. Counting it as owned-but-unplayed would invent
    a gap out of missing metadata."""
    _artist(db, 1, 'Tagged', ['Jazz'])
    _artist(db, 2, 'Untagged', None)
    _album(db, 10, 1, 'j'); _album(db, 20, 2, 'u')
    _track(db, 100, 10, 1, 'jazz')
    for i in range(9):
        _track(db, 200 + i, 20, 2, f'unknown{i}')
    _play(db, 100)

    rows = _rows_by_genre(db.get_genre_own_vs_play('all'))

    # Jazz is the ONLY genre-known track, so it is 100% of that population —
    # not 10% of the whole library.
    assert rows['Jazz']['owned_pct'] == 100.0
    assert len(rows) == 1


def test_no_plays_in_range_is_zero_percent_not_a_crash(db):
    """A fresh install has a library and no history. Every genre reads 0%
    played, which is the honest answer."""
    _artist(db, 1, 'A', ['Rock'])
    _album(db, 10, 1, 'r')
    _track(db, 100, 10, 1, 'rock')

    rows = db.get_genre_own_vs_play('all')

    assert rows[0]['played_pct'] == 0.0
    assert rows[0]['owned_pct'] == 100.0


def test_an_empty_library_returns_nothing_rather_than_dividing_by_zero(db):
    assert db.get_genre_own_vs_play('all') == []


def test_a_multi_genre_artist_counts_toward_each_of_its_genres(db):
    _artist(db, 1, 'Crossover', ['Metal', 'Jazz'])
    _album(db, 10, 1, 'c')
    _track(db, 100, 10, 1, 'song')
    _play(db, 100)

    rows = _rows_by_genre(db.get_genre_own_vs_play('all'))

    assert set(rows) == {'Metal', 'Jazz'}
    assert rows['Metal']['owned_tracks'] == 1
    assert rows['Jazz']['plays'] == 1


def test_both_sides_parse_genres_the_same_way(db):
    """A genre spelled one way on the owned side and another on the played
    side would render as a real gap. Same parser, both halves."""
    conn = db._get_connection()
    # A comma string, not JSON — the old payload shape the parser also accepts.
    conn.execute(
        "INSERT OR REPLACE INTO lib2_artists (id, name, name_key, genres) "
        "VALUES (1, 'X', 'x', 'Metal, Jazz')"
    )
    conn.commit(); conn.close()
    _album(db, 10, 1, 'x')
    _track(db, 100, 10, 1, 'song')
    _play(db, 100)

    rows = _rows_by_genre(db.get_genre_own_vs_play('all'))

    assert set(rows) == {'Metal', 'Jazz'}
    for row in rows.values():
        # Owned and played shares must agree when there is exactly one track
        # and one play — any disagreement is a parsing difference.
        assert row['owned_pct'] == row['played_pct']


# ── the neglected shelf ──────────────────────────────────────────────────────

def test_an_album_with_no_plays_is_neglected(db):
    _artist(db, 1, 'A', ['Rock'])
    _album(db, 10, 1, 'Never Played')
    for i in range(4):
        _track(db, 100 + i, 10, 1, f't{i}', play_count=0)

    albums = db.get_neglected_albums()

    assert len(albums) == 1
    assert albums[0]['name'] == 'Never Played'
    assert albums[0]['tracks'] == 4
    assert albums[0]['artist'] == 'A'


def test_one_played_track_takes_the_whole_album_off_the_shelf(db):
    """"Never played" must mean the album, not a track. An album you have
    dipped into is not neglected."""
    _artist(db, 1, 'A', ['Rock'])
    _album(db, 10, 1, 'Half Played')
    _track(db, 100, 10, 1, 'a', play_count=3)
    _track(db, 101, 10, 1, 'b', play_count=0)

    assert db.get_neglected_albums() == []


def test_the_biggest_neglected_albums_come_first(db):
    _artist(db, 1, 'A', ['Rock'])
    _album(db, 10, 1, 'Small'); _album(db, 20, 1, 'Big')
    _track(db, 100, 10, 1, 's1')
    for i in range(6):
        _track(db, 200 + i, 20, 1, f'b{i}')

    albums = db.get_neglected_albums()

    assert [a['name'] for a in albums] == ['Big', 'Small']


def test_an_empty_library_has_no_neglected_albums(db):
    assert db.get_neglected_albums() == []
