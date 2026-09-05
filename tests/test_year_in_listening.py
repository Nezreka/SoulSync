"""Year in Listening (stats P5).

The rest of the stats page is a FILTER — the user picks a range and gets
totals. This is a fixed PERIOD, decided by the code and printed on the page,
which is the whole reason it reads as a story instead of a dashboard.

``now`` is injected everywhere below so the twelve-month window is a fact of
the test rather than a fact of the day it runs on. Without that these tests
would quietly change meaning at every month boundary.

TIMEZONE: played_at is LOCAL naive wall-clock (see
test_listening_clock_and_rhythm.py). This query compares it against a window
built from the LOCAL clock via date(played_at) — so, unlike the range filters,
the year carries no UTC skew. The tests insert local-looking timestamps for
that reason, and one of them pins the ISO 'T' separator the web player writes.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from database.music_database import MusicDatabase


NOW = datetime(2026, 8, 14, 12, 0, 0)   # a Friday, mid-month


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'year.db'))


def _play(db, when: datetime, *, title='t', artist='A', album='Al',
          duration_ms=180_000, sep=' '):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO listening_history (track_id, title, artist, album, played_at, duration_ms) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (f'{title}-{artist}-{when.isoformat()}', title, artist, album,
         when.isoformat(sep=sep), duration_ms))
    conn.commit()
    conn.close()


def _year(db, now=NOW):
    return db.get_year_in_listening(now=now)


# ── the period ───────────────────────────────────────────────────────────────

def test_the_window_is_twelve_calendar_months_ending_this_month(db):
    year = _year(db)

    assert year['period']['start'] == '2025-09-01'
    assert year['period']['end'] == '2026-08-14'
    assert year['period']['label'] == 'Sep 2025 — Aug 2026'


def test_the_month_strip_is_dense_even_where_nothing_was_played(db):
    """A month you listened to nothing in is a fact about the year. Letting it
    fall out of the GROUP BY would silently close the gap and draw a lie."""
    _play(db, datetime(2026, 8, 1, 10, 0))

    months = _year(db)['months']

    assert len(months) == 12
    assert [m['month'] for m in months][0] == '2025-09'
    assert [m['month'] for m in months][-1] == '2026-08'
    assert months[0]['plays'] == 0            # Sep 2025, nothing played
    assert months[-1]['plays'] == 1


def test_a_play_before_the_window_is_outside_the_year(db):
    _play(db, datetime(2025, 8, 31, 23, 0))   # one day before the window opens
    _play(db, datetime(2025, 9, 1, 0, 30))    # first day inside it

    year = _year(db)

    assert year['totals']['plays'] == 1
    assert year['months'][0]['plays'] == 1


def test_a_future_dated_play_does_not_inflate_the_current_month(db):
    """A clock artefact is not listening. The window end is today, not the end
    of the month, so a row stamped next week stays out."""
    _play(db, datetime(2026, 8, 14, 9, 0))
    _play(db, datetime(2026, 8, 20, 9, 0), title='future')

    assert _year(db)['totals']['plays'] == 1


def test_both_stored_timestamp_shapes_land_in_the_window(db):
    """The web player writes an ISO 'T' separator and plex_client writes a
    space. A lexicographic compare orders those differently at a boundary —
    date() parses both, which is why the filter uses it."""
    _play(db, datetime(2025, 9, 1, 8, 0), title='iso', sep='T')
    _play(db, datetime(2025, 9, 1, 9, 0), title='spaced', sep=' ')

    assert _year(db)['totals']['plays'] == 2


# ── the totals ───────────────────────────────────────────────────────────────

def test_minutes_come_from_duration_not_from_play_count(db):
    _play(db, datetime(2026, 1, 5, 10, 0), duration_ms=240_000)   # 4 min
    _play(db, datetime(2026, 1, 5, 11, 0), title='b', duration_ms=360_000)  # 6

    assert _year(db)['totals']['minutes'] == 10


def test_a_null_duration_does_not_poison_the_minute_total(db):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO listening_history (track_id, title, artist, played_at, duration_ms) "
        "VALUES ('x', 'x', 'A', '2026-01-05 10:00:00', NULL)")
    conn.commit()
    conn.close()
    _play(db, datetime(2026, 1, 5, 11, 0), duration_ms=120_000)

    year = _year(db)

    assert year['totals']['plays'] == 2
    assert year['totals']['minutes'] == 2


def test_active_days_counts_days_not_plays(db):
    for hour in range(5):
        _play(db, datetime(2026, 3, 2, 10 + hour, 0), title=f't{hour}')
    _play(db, datetime(2026, 3, 3, 10, 0), title='next-day')

    assert _year(db)['totals']['active_days'] == 2


def test_an_empty_history_is_a_complete_empty_story(db):
    """Every key the page reads must still be present, or the surface breaks
    on exactly the install least able to report it."""
    year = _year(db)

    assert year['has_data'] is False
    assert year['totals']['plays'] == 0
    assert len(year['months']) == 12
    assert year['top_artists'] == []
    assert year['discoveries'] == []
    assert year['peak_day'] == {'date': None, 'plays': 0}
    assert year['top_hour'] == {'hour': None, 'plays': 0}
    assert year['period']['label'] == 'Sep 2025 — Aug 2026'


# ── the leaders ──────────────────────────────────────────────────────────────

def test_the_top_artist_is_the_most_played_in_the_window(db):
    for i in range(6):
        _play(db, datetime(2026, 2, 1, 10, i), artist='Favourite', title=f'f{i}')
    for i in range(2):
        _play(db, datetime(2026, 2, 2, 10, i), artist='Other', title=f'o{i}')

    top = _year(db)['top_artists']

    assert top[0]['name'] == 'Favourite'
    assert top[0]['plays'] == 6


def test_months_on_top_counts_the_months_an_artist_actually_led(db):
    """The fact that separates "your year" from "one heavy weekend"."""
    # Leads Oct and Nov; a rival out-plays them overall in a single month.
    for month in (10, 11):
        for i in range(3):
            _play(db, datetime(2025, month, 5, 10, i), artist='Steady', title=f's{month}{i}')
    for i in range(10):
        _play(db, datetime(2026, 1, 9, 10, i), artist='Binge', title=f'b{i}')

    by_name = {a['name']: a for a in _year(db)['top_artists']}

    assert by_name['Binge']['plays'] == 10
    assert by_name['Binge']['months_on_top'] == 1
    assert by_name['Steady']['months_on_top'] == 2


def test_each_month_carries_its_own_leader(db):
    for i in range(3):
        _play(db, datetime(2025, 10, 4, 10, i), artist='October Band', title=f'a{i}')
    for i in range(4):
        _play(db, datetime(2025, 11, 4, 10, i), artist='November Band', title=f'b{i}')

    months = {m['month']: m for m in _year(db)['months']}

    assert months['2025-10']['top_artist'] == 'October Band'
    assert months['2025-11']['top_artist'] == 'November Band'
    assert months['2025-12']['top_artist'] is None


def test_a_month_leader_tie_is_broken_the_same_way_every_time(db):
    """Two artists on equal plays must not make the strip flicker between
    renders. Asserted against the fold DIRECTLY and in both row orders,
    because going through the query would only prove which order SQLite
    happened to hand back today — which is not a promise it makes."""
    forwards = [('2026-04', 'Zebra', 3), ('2026-04', 'Aardvark', 3)]
    backwards = list(reversed(forwards))

    assert MusicDatabase._pick_month_leaders(forwards) == {'2026-04': 'Aardvark'}
    assert MusicDatabase._pick_month_leaders(backwards) == {'2026-04': 'Aardvark'}


def test_a_clear_month_winner_beats_an_alphabetically_earlier_rival(db):
    """The tie-break must only apply to actual ties — it is a stabiliser, not
    a sort key."""
    rows = [('2026-04', 'Aardvark', 1), ('2026-04', 'Zebra', 9)]

    assert MusicDatabase._pick_month_leaders(rows) == {'2026-04': 'Zebra'}


def test_a_month_that_would_not_parse_is_dropped_not_keyed_as_none(db):
    """strftime returns NULL for junk; a None month key would collide with
    every other unparseable row and render as a real month."""
    rows = [(None, 'Ghost', 5), ('2026-04', 'Real', 1)]

    assert MusicDatabase._pick_month_leaders(rows) == {'2026-04': 'Real'}


def test_the_peak_day_is_the_single_biggest_listening_day(db):
    for i in range(7):
        _play(db, datetime(2026, 5, 20, 9 + i, 0), title=f'p{i}')
    _play(db, datetime(2026, 5, 21, 9, 0), title='quiet')

    peak = _year(db)['peak_day']

    assert peak['date'] == '2026-05-20'
    assert peak['plays'] == 7


def test_the_top_hour_is_local_wall_clock(db):
    for i in range(4):
        _play(db, datetime(2026, 6, 10 + i, 22, 0), title=f'n{i}')
    _play(db, datetime(2026, 6, 15, 8, 0), title='morning')

    assert _year(db)['top_hour']['hour'] == 22


# ── discoveries ──────────────────────────────────────────────────────────────

def test_a_discovery_is_an_artist_first_ever_played_inside_the_window(db):
    _play(db, datetime(2026, 3, 1, 10, 0), artist='New Find')

    discoveries = [d['name'] for d in _year(db)['discoveries']]

    assert discoveries == ['New Find']


def test_an_old_favourite_replayed_this_year_is_not_a_discovery(db):
    """The whole point. Comparing first-play against the WINDOW instead of
    against all of history would call every returning artist a discovery —
    the most obviously wrong number this surface could print."""
    _play(db, datetime(2019, 4, 2, 10, 0), artist='Old Flame', title='then')
    _play(db, datetime(2026, 3, 2, 10, 0), artist='Old Flame', title='now')
    _play(db, datetime(2026, 3, 3, 10, 0), artist='Genuinely New', title='x')

    discoveries = [d['name'] for d in _year(db)['discoveries']]

    assert discoveries == ['Genuinely New']


def test_discoveries_are_ranked_by_how_much_they_stuck(db):
    """An artist you found and played forty times is the story; one you tried
    once is a footnote."""
    for i in range(9):
        _play(db, datetime(2026, 2, 3, 10, i), artist='Stuck', title=f's{i}')
    _play(db, datetime(2026, 2, 4, 10, 0), artist='Tried Once', title='t')

    assert [d['name'] for d in _year(db)['discoveries']] == ['Stuck', 'Tried Once']


# ── grouping parity with the rest of the page ────────────────────────────────

def test_artist_casing_does_not_split_one_artist_into_two(db):
    _play(db, datetime(2026, 7, 1, 10, 0), artist='Radiohead', title='a')
    _play(db, datetime(2026, 7, 1, 11, 0), artist='radiohead', title='b')

    year = _year(db)

    assert year['totals']['artists'] == 1
    assert len(year['top_artists']) == 1
    assert year['top_artists'][0]['plays'] == 2


def test_the_top_track_carries_when_you_first_and_last_played_it(db):
    _play(db, datetime(2026, 1, 2, 10, 0), title='On Repeat')
    _play(db, datetime(2026, 6, 9, 10, 0), title='On Repeat')

    track = _year(db)['top_tracks'][0]

    assert track['name'] == 'On Repeat'
    assert track['plays'] == 2
    assert track['first_played'].startswith('2026-01-02')
    assert track['last_played'].startswith('2026-06-09')


# ── the API layer ────────────────────────────────────────────────────────────

def test_the_endpoint_computes_the_year_when_the_cache_is_cold(db):
    """The path that matters. The worker rebuilds every 30 minutes, so a fresh
    or just-restarted install has no cached year. Serving an empty one there
    would look exactly like "you have never listened to anything" — and the
    user has no way to tell the difference or fix it."""
    from core.stats import queries

    _play(db, datetime(2026, 3, 1, 10, 0), artist='Cold Start')

    out = queries.get_year_in_listening(db, lambda url: url)

    assert out['cached'] is False
    assert out['totals']['plays'] == 1
    assert out['top_artists'][0]['name'] == 'Cold Start'


def test_the_endpoint_prefers_the_cache_when_the_worker_has_run(db):
    import json

    from core.stats import queries

    conn = db._get_connection()
    conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('stats_cache_year', ?)",
                 (json.dumps({'totals': {'plays': 999}, 'top_artists': []}),))
    conn.commit(); conn.close()

    out = queries.get_year_in_listening(db, lambda url: url)

    assert out['cached'] is True
    assert out['totals']['plays'] == 999


def test_a_corrupt_cache_falls_back_instead_of_raising(db):
    """Half-written JSON in the KV must degrade to a live read, not take the
    whole surface down."""
    from core.stats import queries

    conn = db._get_connection()
    conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('stats_cache_year', '{not json')")
    conn.commit(); conn.close()
    _play(db, datetime(2026, 3, 1, 10, 0), artist='Survivor')

    out = queries.get_year_in_listening(db, lambda url: url)

    assert out['cached'] is False
    assert out['top_artists'][0]['name'] == 'Survivor'


# ── artwork ──────────────────────────────────────────────────────────────────

def _artist_row(db, artist_id, name, thumb='http://img/a.jpg', soul_id=None):
    from core.library2.importer import normalize_name

    conn = db._get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO lib2_artists "
        "(id, name, name_key, image_url, soul_id) VALUES (?, ?, ?, ?, ?)",
        (artist_id, name, normalize_name(name), thumb, soul_id),
    )
    conn.commit()
    conn.close()


def test_discoveries_get_images_and_ids_like_top_artists(db):
    """The surface makes a discovery clickable through to artist detail, which
    needs the id — and it is the artwork that makes the slide worth looking
    at. Enriching only top_artists left discoveries as bare names."""
    from core.stats.enrich import enrich_stats_items

    _artist_row(db, 7, 'Brand New Act')
    _play(db, datetime(2026, 3, 1, 10, 0), artist='Brand New Act')

    year = _year(db)
    enrich_stats_items(db, year)

    discovery = year['discoveries'][0]
    assert str(discovery['id']) == '7'
    assert discovery['image_url']


def test_the_live_endpoint_enriches_too(db):
    """A cache miss must not serve a name-only story — it would look like the
    artwork is missing from the library rather than from the response."""
    from core.stats import queries

    _artist_row(db, 3, 'Cover Star')
    _play(db, datetime(2026, 3, 1, 10, 0), artist='Cover Star')

    out = queries.get_year_in_listening(db, lambda url: url)

    assert out['cached'] is False
    assert out['top_artists'][0]['image_url']
    assert str(out['top_artists'][0]['id']) == '3'


def test_enrichment_leaves_an_unknown_artist_alone(db):
    """A play whose artist is not in the library is still a real play. It must
    render without art rather than disappear or carry someone else's face."""
    from core.stats.enrich import enrich_stats_items

    _play(db, datetime(2026, 3, 1, 10, 0), artist='Never Imported')

    year = _year(db)
    enrich_stats_items(db, year)

    assert year['top_artists'][0]['name'] == 'Never Imported'
    assert year['top_artists'][0].get('image_url') is None


# ── play an album from the story ─────────────────────────────────────────────

def _album_with_tracks(db, album_id, title, artist_id=1, artist='A', files=(1, 2, 3)):
    from core.library2.importer import normalize_name

    conn = db._get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO lib2_artists (id, name, name_key) VALUES (?, ?, ?)",
        (artist_id, artist, normalize_name(artist)),
    )
    conn.execute(
        "INSERT OR REPLACE INTO lib2_albums "
        "(id, primary_artist_id, title, image_url) VALUES (?, ?, ?, ?)",
        (album_id, artist_id, title, 'http://img/cover.jpg'),
    )
    for n in files:
        track_id = album_id * 100 + n
        conn.execute(
            "INSERT OR REPLACE INTO lib2_tracks "
            "(id, album_id, title, track_number) VALUES (?, ?, ?, ?)",
            (track_id, album_id, f'Track {n}', n),
        )
        conn.execute(
            "INSERT INTO lib2_track_files (track_id, path, bitrate, is_primary) "
            "VALUES (?, ?, 320, 1)",
            (track_id, f'/music/{album_id}/{n}.flac'),
        )
    conn.commit()
    conn.close()


def test_an_album_plays_in_track_order(db):
    """It has to play as an ALBUM. Insertion order is not track order."""
    from core.stats import queries

    _album_with_tracks(db, 5, 'Cold', files=(3, 1, 2))

    tracks = queries.get_album_play_tracks(db, 5, lambda url: url)

    assert [t['title'] for t in tracks] == ['Track 1', 'Track 2', 'Track 3']


def test_the_rows_carry_what_the_player_needs(db):
    """Shaped like /api/library/radio — npMapRadioTrack (media-player.js) reads
    these exact keys, and anything missing drops out of the queue."""
    from core.stats import queries

    _album_with_tracks(db, 5, 'Cold', files=(1,))

    row = queries.get_album_play_tracks(db, 5, lambda url: url)[0]

    assert row['file_path'] == '/music/5/1.flac'
    assert row['album'] == 'Cold'
    assert row['artist'] == 'A'
    assert str(row['album_id']) == '5'
    assert row['image_url'] == 'http://img/cover.jpg'


def test_a_track_with_no_file_is_not_offered(db):
    """A row the player would skip is not a track you own. Counting it makes
    "play album" look like it lost songs."""
    from core.stats import queries

    _album_with_tracks(db, 5, 'Cold', files=(1,))
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO lib2_tracks (id, album_id, title, track_number) "
        "VALUES (999, 5, 'Wishlisted', 2)"
    )
    conn.commit()
    conn.close()

    tracks = queries.get_album_play_tracks(db, 5, lambda url: url)

    assert [t['title'] for t in tracks] == ['Track 1']


def test_an_unknown_album_is_empty_not_an_error(db):
    from core.stats import queries

    assert queries.get_album_play_tracks(db, 4242, lambda url: url) == []
