"""``track_id_for_path`` — the catalogue track that owns a file on disk.

Three callers used to hand-roll this against the legacy ``tracks.file_path``
column, each with its own fallback: the manual-match re-resolver, the download
recorder and the expired-download cleaner. In v2 the path lives on the file
row, a track can own several, and a deleted row stays as history — so the
lookup is worth having exactly once.
"""

from __future__ import annotations

from core.library2.track_files import track_id_for_path


def _seed_track(conn, title="Song"):
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('A')")
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')",
        (artist_id,))
    cur.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?,?)",
                (cur.lastrowid, title))
    return cur.lastrowid


def _add_file(conn, track_id, path, state='active'):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO lib2_track_files(track_id, path, file_state) VALUES(?,?,?)",
        (track_id, path, state))
    return cur.lastrowid


def test_exact_path_wins(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    _add_file(conn, track, '/library/A/01 - Song.flac')

    assert track_id_for_path(conn, '/library/A/01 - Song.flac') == track


def test_basename_resolves_a_differently_prefixed_path(imported_conn):
    """The media server stores its own mount prefix; the filename survives."""
    conn = imported_conn
    track = _seed_track(conn)
    _add_file(conn, track, '/library/Artist/Album/01 - Song.flac')

    assert track_id_for_path(conn, '/mnt/media/Artist/Album/01 - Song.flac') == track


def test_ambiguous_basename_resolves_to_nothing(imported_conn):
    """Two albums both have an '01 - Intro.flac'. Legacy took whichever row
    came first, which is a coin flip on which song gets the download record."""
    conn = imported_conn
    first = _seed_track(conn, 'Intro A')
    second = _seed_track(conn, 'Intro B')
    _add_file(conn, first, '/library/AlbumA/01 - Intro.flac')
    _add_file(conn, second, '/library/AlbumB/01 - Intro.flac')

    assert track_id_for_path(conn, '/transfer/x/01 - Intro.flac') is None


def test_basename_match_respects_the_separator(imported_conn):
    """A plain suffix match made "song.flac" match "my-song.flac"."""
    conn = imported_conn
    track = _seed_track(conn)
    _add_file(conn, track, '/library/A/my-song.flac')

    assert track_id_for_path(conn, '/transfer/song.flac') is None


def test_a_windows_stored_path_matches_a_posix_query(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    _add_file(conn, track, 'C:\\Music\\Artist\\01 - Song.flac')

    assert track_id_for_path(conn, '/library/Artist/01 - Song.flac') == track


def test_a_live_file_outvotes_a_deleted_one_at_the_same_path(imported_conn):
    """The old file was deleted and a new one imported to the same path — the
    answer is the track that has it now, not the one that had it."""
    conn = imported_conn
    gone = _seed_track(conn, 'Old')
    current = _seed_track(conn, 'New')
    _add_file(conn, gone, '/library/A/01 - Song.flac', state='deleted')
    _add_file(conn, current, '/library/A/01 - Song.flac')

    assert track_id_for_path(conn, '/library/A/01 - Song.flac') == current


def test_a_deleted_file_alone_is_not_owned(imported_conn):
    conn = imported_conn
    track = _seed_track(conn)
    _add_file(conn, track, '/library/A/gone.flac', state='deleted')

    assert track_id_for_path(conn, '/library/A/gone.flac') is None


def test_an_unlinked_staging_file_is_not_an_answer(imported_conn):
    """A file row may exist before it is linked to a track (manual import)."""
    conn = imported_conn
    conn.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(NULL, ?)",
                 ('/staging/01 - Song.flac',))

    assert track_id_for_path(conn, '/staging/01 - Song.flac') is None


def test_unknown_and_empty_paths_return_none(imported_conn):
    conn = imported_conn
    _add_file(conn, _seed_track(conn), '/library/A/01 - Song.flac')

    assert track_id_for_path(conn, '/library/A/nope.flac') is None
    assert track_id_for_path(conn, '') is None
    assert track_id_for_path(conn, None) is None
