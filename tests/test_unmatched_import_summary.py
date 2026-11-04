"""#1202 — the library's "these never got matched" count.

A file that imports with unreadable tags and no acoustid hit falls back to
filename-only identification, which parks it under a made-up 'Unknown Artist'
as its own one-track album. Re-identify has always been able to re-file it, but
it lives on an artist page, so nothing ever pointed you at the problem.

These pin the count that drives the banner. The interesting cases are the ones
that would make it lie: an empty Unknown Artist row, the same name existing
once per server source, and real artists sitting next to it.

Ported to Library v2. Upstream counts the legacy `artists`/`albums`/`tracks`
tables, which are empty here, so the summary would have reported a clean
library on every install. The v2 catalogue also keeps discography and wishlist
rows beside the owned ones, which adds a case upstream cannot have: a
browse-only "Unknown Artist" release is not a failed import.
"""

import pytest


@pytest.fixture
def db(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(database_path=str(tmp_path / "m.db"))


def _artist(conn, name, source='plex'):
    return conn.execute(
        "INSERT INTO lib2_artists (name, name_key, server_source) VALUES (?, ?, ?)",
        (name, name.strip().lower(), source),
    ).lastrowid


def _album_with_tracks(conn, artist_id, title, track_count, source='plex',
                       *, with_files=True):
    album_id = conn.execute(
        "INSERT INTO lib2_albums (primary_artist_id, title, server_source) "
        "VALUES (?, ?, ?)", (artist_id, title, source),
    ).lastrowid
    for n in range(track_count):
        track_id = conn.execute(
            "INSERT INTO lib2_tracks (album_id, title) VALUES (?, ?)",
            (album_id, f"{title} {n}"),
        ).lastrowid
        if with_files:
            conn.execute(
                "INSERT INTO lib2_track_files (track_id, path, is_primary, file_state) "
                "VALUES (?, ?, 1, 'active')", (track_id, f"/music/{album_id}-{n}.flac"),
            )
    return album_id


def test_a_clean_library_reports_nothing(db):
    conn = db._get_connection()
    a1 = _artist(conn, 'Aphex Twin')
    _album_with_tracks(conn, a1, 'Selected Ambient Works', 3)
    conn.commit()
    conn.close()

    assert db.get_unmatched_import_summary() == {'count': 0, 'artist_id': None}


def test_counts_the_unknown_artist_tracks_and_points_at_the_row(db):
    conn = db._get_connection()
    a1 = _artist(conn, 'Aphex Twin')
    _album_with_tracks(conn, a1, 'Selected Ambient Works', 3)
    u1 = _artist(conn, 'Unknown Artist')
    _album_with_tracks(conn, u1, 'track_04', 1)
    _album_with_tracks(conn, u1, 'some_rip', 1)
    conn.commit()
    conn.close()

    summary = db.get_unmatched_import_summary()
    # the real artist's 3 tracks are not anybody's problem
    assert summary == {'count': 2, 'artist_id': u1}


def test_an_empty_unknown_artist_row_raises_no_banner(db):
    """The row can exist with nothing under it once tracks are re-filed.
    Saying "0 tracks imported without a match" would be worse than silence."""
    conn = db._get_connection()
    _artist(conn, 'Unknown Artist')
    conn.commit()
    conn.close()

    assert db.get_unmatched_import_summary() == {'count': 0, 'artist_id': None}


def test_a_browse_only_release_is_not_a_failed_import(db):
    """v2-only case: the catalogue carries releases you do NOT own. Telling
    someone to go re-identify a file that was never imported is a false alarm,
    and it would fire on any discography row a provider files under the name."""
    conn = db._get_connection()
    u1 = _artist(conn, 'Unknown Artist')
    _album_with_tracks(conn, u1, 'never downloaded', 3, with_files=False)
    conn.commit()
    conn.close()

    assert db.get_unmatched_import_summary() == {'count': 0, 'artist_id': None}


def test_sums_across_server_sources_and_links_to_the_biggest(db):
    """The same name exists once per server source, so taking the first row
    found would under-report and could link at the near-empty one."""
    # the SMALLER row is inserted first, so picking rows[0] instead of the
    # biggest has to be able to fail here rather than pass by luck.
    conn = db._get_connection()
    small = _artist(conn, 'Unknown Artist', 'plex')
    _album_with_tracks(conn, small, 'a', 1, 'plex')
    big = _artist(conn, 'Unknown Artist', 'navidrome')
    _album_with_tracks(conn, big, 'b', 4, 'navidrome')
    conn.commit()
    conn.close()

    summary = db.get_unmatched_import_summary()
    assert summary['count'] == 5
    assert summary['artist_id'] == big


def test_matches_the_name_regardless_of_case_and_padding(db):
    conn = db._get_connection()
    u1 = _artist(conn, '  unknown artist ')
    _album_with_tracks(conn, u1, 'x', 2)
    conn.commit()
    conn.close()

    assert db.get_unmatched_import_summary()['count'] == 2


def test_a_real_artist_whose_name_merely_contains_the_words_is_left_alone(db):
    """Substring matching here would swallow real bands."""
    conn = db._get_connection()
    a1 = _artist(conn, 'Unknown Artist Collective')
    _album_with_tracks(conn, a1, 'Debut', 4)
    conn.commit()
    conn.close()

    assert db.get_unmatched_import_summary() == {'count': 0, 'artist_id': None}


def test_the_endpoint_still_exists_for_the_banner_to_call():
    """Upstream pins this route against the legacy library page's api module,
    which this branch deleted for Library v2 — there is no v2 banner wired to
    it yet (see the sync notes). Until there is, pin the server half so a
    rename cannot quietly remove the endpoint a future banner will call.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    server = (root / 'web_server.py').read_text(encoding='utf-8')

    assert "@app.route('/api/library/unmatched-summary')" in server
