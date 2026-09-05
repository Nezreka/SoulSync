"""What "missing" counts, once you are allowed to not want a track.

Reported: it is fine for an album to be monitored while a few of its tracks are
not — but then the missing count has to mean "monitored and absent", not
"absent". Otherwise unmonitoring the two interludes you never want leaves the
album reading "2 missing" forever, and there is no way to reach zero except by
downloading music you deliberately said no to.

Unmaterialized slots still count while the ALBUM is monitored: a release whose
provider tracklist promises twelve tracks and has three rows is genuinely nine
short, and those nine have no row yet to carry a monitored flag of their own.
"""

from __future__ import annotations

from core.library2.queries import get_album, get_artist


def _album(conn, *, expected=None, monitored=1):
    artist_id = conn.execute("SELECT id FROM lib2_artists ORDER BY id LIMIT 1").fetchone()[0]
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, monitored, "
        "expected_track_count, origin) VALUES(?,'Counting','album',?,?, 'library')",
        (artist_id, monitored, expected),
    ).lastrowid
    # The artist view lists releases through the credit table, not the
    # primary-artist column.
    conn.execute(
        "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
        "VALUES(?,?, 'primary')",
        (album_id, artist_id),
    )
    return int(artist_id), int(album_id)


def _track(conn, album_id, number, *, monitored, file=True, file_state="active"):
    track_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
        "VALUES(?,?,?,1,?)",
        (album_id, f"Track {number}", number, monitored),
    ).lastrowid
    if file:
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, is_primary, file_state) "
            "VALUES(?,?,1,?)",
            (track_id, f"/music/Counting/{number:02d}.flac", file_state),
        )
    return int(track_id)


def _summary(conn, artist_id, album_id):
    grouped = get_artist(conn, artist_id) or {}
    for bucket in ("albums", "eps", "singles"):
        for entry in grouped.get(bucket, []) or []:
            if entry["id"] == album_id:
                return entry
    raise AssertionError("album not in the artist's list")


def test_an_unmonitored_track_without_a_file_is_not_missing(imported_conn):
    """The reported case: you decided you do not want it, so it is not a gap."""
    artist_id, album_id = _album(imported_conn)
    _track(imported_conn, album_id, 1, monitored=1)
    _track(imported_conn, album_id, 2, monitored=0, file=False)
    imported_conn.commit()

    summary = _summary(imported_conn, artist_id, album_id)

    assert summary["tracks_present"] == 1
    assert summary["tracks_missing"] == 0
    assert get_album(imported_conn, album_id)["tracks_missing"] == 0


def test_a_monitored_track_without_a_file_still_is(imported_conn):
    artist_id, album_id = _album(imported_conn)
    _track(imported_conn, album_id, 1, monitored=1)
    _track(imported_conn, album_id, 2, monitored=1, file=False)
    imported_conn.commit()

    assert _summary(imported_conn, artist_id, album_id)["tracks_missing"] == 1
    assert get_album(imported_conn, album_id)["tracks_missing"] == 1


def test_a_deleted_file_leaves_a_monitored_track_missing(imported_conn):
    """A file removed from disk is a gap again — as long as the track is still
    wanted. (Deleting through the library unmonitors it; deleting behind
    SoulSync's back does not.)"""
    artist_id, album_id = _album(imported_conn)
    _track(imported_conn, album_id, 1, monitored=1, file_state="deleted")
    imported_conn.commit()

    assert _summary(imported_conn, artist_id, album_id)["tracks_missing"] == 1


def test_slots_the_provider_promised_still_count_while_the_album_is_monitored(
        imported_conn):
    """Nine rows that do not exist yet cannot carry a monitored flag, and a
    slot nobody has seen is not a slot anyone declined."""
    artist_id, album_id = _album(imported_conn, expected=12)
    for number in (1, 2, 3):
        _track(imported_conn, album_id, number, monitored=1)
    imported_conn.commit()

    summary = _summary(imported_conn, artist_id, album_id)

    assert summary["track_count"] == 12
    assert summary["tracks_present"] == 3
    assert summary["tracks_missing"] == 9


def test_promised_slots_count_even_when_the_album_row_is_unmonitored(
        imported_conn):
    """`lib2_albums.monitored` is not "do you want this": the importer clears
    it precisely BECAUSE a release is incomplete (a wishlist-seeded album with
    one of three tracks is unmonitored and three tracks short). Gating slots on
    it would hide the gaps on exactly the albums that have them. What the user
    unmonitored are TRACKS, and those are rows — a slot with no row is nobody's
    "no"."""
    artist_id, album_id = _album(imported_conn, expected=12, monitored=0)
    for number in (1, 2, 3):
        _track(imported_conn, album_id, number, monitored=0)
    imported_conn.commit()

    summary = _summary(imported_conn, artist_id, album_id)

    assert summary["tracks_missing"] == 9, "the nine unmaterialized slots"


def test_the_denominator_still_shows_the_whole_release(imported_conn):
    """"3 of 12" stays honest even when nothing is wanted — the count of what
    the release HAS is not a statement about what you want."""
    artist_id, album_id = _album(imported_conn, expected=12, monitored=0)
    for number in (1, 2, 3):
        _track(imported_conn, album_id, number, monitored=0)
    imported_conn.commit()

    summary = _summary(imported_conn, artist_id, album_id)

    assert summary["track_count"] == 12
    assert summary["tracks_present"] == 3
