"""An album that has everything must not keep claiming one track is missing.

Reported for Justin Bieber's single "DAISIES": the library shows "1 missing"
while the single has its one track, with a file. The numbers behind it:

    expected_track_count = 2      (an old provider count, source long gone)
    tracklist_json       = 1 entry (Deezer, fetched complete, status 'ready')
    lib2_tracks          = 1 row   (with an active file)

`list_*` computes ``total = max(expected_track_count, known rows, present)``,
so the stale 2 wins over a provider-confirmed 1 and the album is permanently
one track short — of a track that exists nowhere: no row, no tracklist entry,
just a numeric placeholder the detail view synthesizes for the empty slot.

``_persist_tracklist_tracks`` already treats a provider-confirmed list as
authoritative — but only upwards ("never slice real entries to a stale
expected_track_count", P1-26). The same authority has to work downwards, or a
count that was once too high can never be corrected by the provider that
disagrees with it.

Two things keep it honest: only a COMPLETE fetch may lower the expectation (a
truncated page is not evidence of a shorter album), and it never drops below
the rows that actually exist.
"""

from __future__ import annotations

from core.library2.completeness import (
    _from_a_known_release_id,
    _persist_tracklist_tracks,
)


def _album(conn, *, expected, tracks):
    """One album with ``expected`` stored and ``tracks`` real rows (with files)."""
    artist_id = conn.execute("SELECT id FROM lib2_artists ORDER BY id LIMIT 1").fetchone()[0]
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, "
        "expected_track_count, tracklist_status) VALUES(?,'DAISIES','single',?,'ready')",
        (artist_id, expected),
    ).lastrowid
    for number in range(1, tracks + 1):
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number) "
            "VALUES(?,?,?,1)",
            (album_id, "DAISIES" if number == 1 else f"Track {number}", number),
        ).lastrowid
        conn.execute(
            "INSERT INTO lib2_track_files(track_id, path, is_primary) VALUES(?,?,1)",
            (track_id, f"/music/Justin Bieber/DAISIES/{number:02d}.flac"),
        )
    conn.commit()
    return int(album_id)


def _expected(conn, album_id):
    return conn.execute(
        "SELECT expected_track_count FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()[0]


def _entry(number, title="DAISIES"):
    return {"track_number": number, "disc_number": 1, "title": title}


def test_a_complete_provider_list_corrects_an_inflated_expectation(imported_conn):
    """The reported case, exactly: expectation 2, provider says 1, library has 1."""
    album_id = _album(imported_conn, expected=2, tracks=1)

    _persist_tracklist_tracks(imported_conn, album_id, [_entry(1)], complete=True)

    assert _expected(imported_conn, album_id) == 1
    rows = imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?", (album_id,)
    ).fetchone()[0]
    assert rows == 1, "the one real track must survive the correction"


def test_a_partial_fetch_may_not_shrink_the_album(imported_conn):
    """A truncated page says nothing about how long the album is. Lowering the
    expectation from one would hide genuinely missing tracks."""
    album_id = _album(imported_conn, expected=12, tracks=1)

    _persist_tracklist_tracks(imported_conn, album_id, [_entry(1)], complete=False)

    assert _expected(imported_conn, album_id) == 12


def test_a_longer_list_still_raises_the_expectation(imported_conn):
    """P1-26 in the other direction, unchanged."""
    album_id = _album(imported_conn, expected=1, tracks=1)

    _persist_tracklist_tracks(
        imported_conn, album_id,
        [_entry(1), _entry(2, "DAISIES (Instrumental)")], complete=True,
    )

    assert _expected(imported_conn, album_id) == 2


def test_it_never_drops_below_the_tracks_that_really_exist(imported_conn):
    """Local rows the provider does not know about are not deleted, so the
    expectation must not claim fewer tracks than the album demonstrably has."""
    album_id = _album(imported_conn, expected=3, tracks=3)

    _persist_tracklist_tracks(imported_conn, album_id, [_entry(1)], complete=True)

    assert _expected(imported_conn, album_id) >= 3


def test_an_empty_list_is_not_a_shorter_album(imported_conn):
    """"The provider returned nothing" is a failed fetch, not a zero-track
    release."""
    album_id = _album(imported_conn, expected=2, tracks=1)

    _persist_tracklist_tracks(imported_conn, album_id, [], complete=True)

    assert _expected(imported_conn, album_id) == 2


# ── which lists are allowed to shrink an album ───────────────────────────────


def test_a_direct_release_id_may_shrink_the_album():
    assert _from_a_known_release_id({"deezer": "956053021"}, "deezer", "956053021") is True


def test_a_name_search_result_may_not():
    """The provider walk falls back to a Deezer title search for rows without
    release ids. A suite EP matching a one-track single would otherwise
    "correct" a 31-track expectation down to 1 and hide thirty real gaps."""
    assert _from_a_known_release_id({}, "deezer", "12345") is False
    assert _from_a_known_release_id({"deezer": "999"}, "deezer", "12345") is False


def test_an_id_from_a_provider_the_album_does_not_claim_may_not():
    assert _from_a_known_release_id({"spotify": "abc"}, "deezer", "12345") is False
    assert _from_a_known_release_id({"deezer": "1"}, None, None) is False
