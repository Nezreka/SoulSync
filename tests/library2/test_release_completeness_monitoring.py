"""A release may only be called complete on evidence that it IS complete.

`reconcile_import_monitoring` derives `lib2_albums.monitored` as "the full
expected tracklist is represented and every represented track is covered". It
computed the expectation as

    max(expected_track_count, track_count, known_tracks)

and the third term makes the test vacuous: `known_tracks >= known_tracks` is
always true. A single whose provider tracklist had never been fetched — no
expected_track_count, no track_count, one row because one file came off disk —
therefore passed as complete and was monitored, without anything ever having
asked how many tracks that single really has. On the production library that
was 368 releases (314 singles, 50 albums, 4 compilations).

Despite living in `importer.py` this is not an import-time function: it runs on
every tracklist materialization (`completeness._persist_tracklist_tracks`), so
it reaches a library that was never imported from anywhere.
"""

from __future__ import annotations

from core.library2.completeness import _persist_tracklist_tracks
from core.library2.importer import reconcile_import_monitoring


def _album(conn, title, *, expected=None, track_count=None):
    artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    return int(conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, origin, "
        "expected_track_count, track_count, monitored) "
        "VALUES(?,?,'single','library',?,?,0)",
        (artist_id, title, expected, track_count),
    ).lastrowid)


def _owned_track(conn, album_id, title, number):
    track_id = int(conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number, monitored) "
        "VALUES(?,?,?,1,1)", (album_id, title, number),
    ).lastrowid)
    conn.execute(
        "INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
        (track_id, f"/m/{title}.flac"),
    )
    return track_id


def _monitored(conn, album_id):
    return conn.execute(
        "SELECT monitored FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()["monitored"]


def test_a_release_of_unknown_size_is_not_called_complete(imported_conn):
    album_id = _album(imported_conn, "One File Single")
    _owned_track(imported_conn, album_id, "The A Side", 1)

    reconcile_import_monitoring(imported_conn.cursor(), album_ids=[album_id])

    assert _monitored(imported_conn, album_id) == 0


def test_a_release_whose_size_is_known_and_met_is_still_complete(imported_conn):
    album_id = _album(imported_conn, "Known Single", expected=1)
    _owned_track(imported_conn, album_id, "The Only Side", 1)

    reconcile_import_monitoring(imported_conn.cursor(), album_ids=[album_id])

    assert _monitored(imported_conn, album_id) == 1


def test_a_media_server_track_count_alone_still_counts_as_a_size(imported_conn):
    album_id = _album(imported_conn, "Server Counted", track_count=2)
    _owned_track(imported_conn, album_id, "Side One", 1)
    _owned_track(imported_conn, album_id, "Side Two", 2)

    reconcile_import_monitoring(imported_conn.cursor(), album_ids=[album_id])

    assert _monitored(imported_conn, album_id) == 1


def test_the_single_that_turns_out_to_have_two_tracks(imported_conn):
    """The reported case, end to end: one file, single flagged complete, then
    the tracklist arrives and it has two."""
    album_id = _album(imported_conn, "Grows On Click")
    owned = _owned_track(imported_conn, album_id, "The A Side", 1)
    reconcile_import_monitoring(imported_conn.cursor(), album_ids=[album_id])
    assert _monitored(imported_conn, album_id) == 0, "never proved, never claimed"

    _persist_tracklist_tracks(imported_conn, album_id, [
        {"track_number": 1, "title": "The A Side"},
        {"track_number": 2, "title": "The B Side"},
    ], complete=True)

    # The release is genuinely incomplete now, and says so.
    assert _monitored(imported_conn, album_id) == 0
    # The track you own keeps its own monitoring…
    assert imported_conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE id=?", (owned,)
    ).fetchone()["monitored"] == 1
    # …and the newly discovered sibling is in the wanted projection, so no
    # consumer has to guess what it is.
    assert imported_conn.execute(
        """SELECT COUNT(*) FROM lib2_wanted_tracks w JOIN lib2_tracks t ON t.id=w.track_id
            WHERE t.album_id=? AND t.title='The B Side'""",
        (album_id,),
    ).fetchone()[0] == 1


def test_a_release_of_unknown_size_is_queued_for_verification(imported_conn):
    """Withdrawing an unproven claim is only half the job — something has to go
    and establish the size.

    `_partial_album_rows` selected on `expected_track_count > known_tracks`, and
    `NULL > n` is NULL, so a release whose size was never established could
    never be picked up: 314 singles sat at `tracklist_status='idle'` forever and
    only revealed their second track when somebody clicked them. That is the
    "the catalogue only loads when I click on it" half of the report.
    """
    from core.library2.completeness import _partial_album_rows

    unknown = _album(imported_conn, "Never Verified")
    _owned_track(imported_conn, unknown, "The A Side", 1)
    satisfied = _album(imported_conn, "Fully Known", expected=1)
    _owned_track(imported_conn, satisfied, "Only Side", 1)

    candidates = {int(r[0]) for r in _partial_album_rows(imported_conn, cached=False)}

    assert unknown in candidates
    assert satisfied not in candidates


def test_browse_only_releases_are_not_dragged_into_verification(imported_conn):
    """A discography row exists to be browsed. Its size being unknown is not a
    gap in the user's library, and resolving all of them is the provider-call
    storm this stays out of."""
    from core.library2.completeness import _partial_album_rows

    browse = _album(imported_conn, "Somebody Elses Record")
    imported_conn.execute(
        "UPDATE lib2_albums SET origin='discography' WHERE id=?", (browse,))

    candidates = {int(r[0]) for r in _partial_album_rows(imported_conn, cached=False)}
    assert browse not in candidates
