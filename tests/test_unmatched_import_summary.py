"""#1202 — the library's "these never got matched" count.

A file that imports with unreadable tags and no acoustid hit falls back to
filename-only identification, which parks it under a made-up 'Unknown Artist'
as its own one-track album. Re-identify has always been able to re-file it, but
it lives on an artist page, so nothing ever pointed you at the problem.

These pin the count that drives the banner. The interesting cases are the ones
that would make it lie: an empty Unknown Artist row, the same name existing
once per server source, and real artists sitting next to it.
"""

import pytest


@pytest.fixture
def db(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(database_path=str(tmp_path / "m.db"))


def _artist(conn, artist_id, name, source='plex'):
    conn.execute(
        "INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
        (artist_id, name, source),
    )


def _album_with_tracks(conn, album_id, artist_id, title, track_count, source='plex'):
    conn.execute(
        "INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, ?)",
        (album_id, artist_id, title, source),
    )
    for n in range(track_count):
        conn.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title) VALUES (?, ?, ?, ?)",
            (f"{album_id}-t{n}", album_id, artist_id, f"{title} {n}"),
        )


def test_a_clean_library_reports_nothing(db):
    conn = db._get_connection()
    _artist(conn, 'a1', 'Aphex Twin')
    _album_with_tracks(conn, 'al1', 'a1', 'Selected Ambient Works', 3)
    conn.commit()
    conn.close()

    assert db.get_unmatched_import_summary() == {'count': 0, 'artist_id': None}


def test_counts_the_unknown_artist_tracks_and_points_at_the_row(db):
    conn = db._get_connection()
    _artist(conn, 'a1', 'Aphex Twin')
    _album_with_tracks(conn, 'al1', 'a1', 'Selected Ambient Works', 3)
    _artist(conn, 'u1', 'Unknown Artist')
    _album_with_tracks(conn, 'al2', 'u1', 'track_04', 1)
    _album_with_tracks(conn, 'al3', 'u1', 'some_rip', 1)
    conn.commit()
    conn.close()

    summary = db.get_unmatched_import_summary()
    # the real artist's 3 tracks are not anybody's problem
    assert summary == {'count': 2, 'artist_id': 'u1'}


def test_an_empty_unknown_artist_row_raises_no_banner(db):
    """The row can exist with nothing under it once tracks are re-filed.
    Saying "0 tracks imported without a match" would be worse than silence."""
    conn = db._get_connection()
    _artist(conn, 'u1', 'Unknown Artist')
    conn.commit()
    conn.close()

    assert db.get_unmatched_import_summary() == {'count': 0, 'artist_id': None}


def test_sums_across_server_sources_and_links_to_the_biggest(db):
    """The same name exists once per server source, so taking the first row
    found would under-report and could link at the near-empty one."""
    # ids are deliberately ordered so the SMALLER row sorts and inserts first:
    # picking rows[0] instead of the biggest has to be able to fail here, and
    # with the ids the other way round it passes by luck.
    conn = db._get_connection()
    _artist(conn, 'u-a-plex', 'Unknown Artist', 'plex')
    _album_with_tracks(conn, 'al1', 'u-a-plex', 'a', 1, 'plex')
    _artist(conn, 'u-b-navi', 'Unknown Artist', 'navidrome')
    _album_with_tracks(conn, 'al2', 'u-b-navi', 'b', 4, 'navidrome')
    conn.commit()
    conn.close()

    summary = db.get_unmatched_import_summary()
    assert summary['count'] == 5
    assert summary['artist_id'] == 'u-b-navi'


def test_matches_the_name_regardless_of_case_and_padding(db):
    conn = db._get_connection()
    _artist(conn, 'u1', '  unknown artist ')
    _album_with_tracks(conn, 'al1', 'u1', 'x', 2)
    conn.commit()
    conn.close()

    assert db.get_unmatched_import_summary()['count'] == 2


def test_a_real_artist_whose_name_merely_contains_the_words_is_left_alone(db):
    """Substring matching here would swallow real bands."""
    conn = db._get_connection()
    _artist(conn, 'a1', 'Unknown Artist Collective')
    _album_with_tracks(conn, 'al1', 'a1', 'Debut', 4)
    conn.commit()
    conn.close()

    assert db.get_unmatched_import_summary() == {'count': 0, 'artist_id': None}


def test_the_page_and_the_server_agree_on_the_url():
    """The React test stubs fetch by URL, so it cannot catch the two sides
    drifting apart. A rename on either end would ship a banner whose request
    404s in silence — the query fails, the banner hides, and it looks exactly
    like a library with nothing wrong. Pin both spellings against each other.

    apiClient prefixes '/api/' (webui/src/app/api-client.ts), which is why the
    two strings below differ by that much and no more.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    api_ts = (root / 'webui' / 'src' / 'routes' / 'library' / '-library.api.ts').read_text(encoding='utf-8')
    server = (root / 'web_server.py').read_text(encoding='utf-8')

    assert "apiClient.get('library/unmatched-summary')" in api_ts
    assert "@app.route('/api/library/unmatched-summary')" in server
