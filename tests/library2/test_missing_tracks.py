"""Materializing a missing album slot into a real, monitorable lib2 track row.

Backs the legacy "Manage → Add to Library" action: a missing slot shown as an
id-less placeholder must become a real track row before it can be monitored /
mirrored into the wishlist.
"""

from __future__ import annotations

import pytest

from core.library2 import missing_tracks as MT


def _views_id(conn) -> int:
    return conn.execute("SELECT id FROM lib2_albums WHERE title='Views'").fetchone()[0]


def test_existing_slot_returns_its_row_without_creating(imported_conn):
    album_id = _views_id(imported_conn)
    # Legacy seed: 'One Dance' is track 1 on Views (already a real row).
    existing = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=? AND track_number=1", (album_id,)
    ).fetchone()[0]

    result = MT.materialize_missing_track(
        imported_conn, album_id, track_number=1, disc_number=1, title="One Dance"
    )

    assert result == {"track_id": existing, "created": False}


def test_missing_slot_is_created_as_a_real_fileless_row(imported_conn):
    album_id = _views_id(imported_conn)

    result = MT.materialize_missing_track(
        imported_conn, album_id, track_number=5, disc_number=1, title="Brand New Slot"
    )

    assert result["created"] is True
    row = imported_conn.execute(
        "SELECT title, track_number, disc_number FROM lib2_tracks WHERE id=?",
        (result["track_id"],),
    ).fetchone()
    assert row["title"] == "Brand New Slot"
    assert row["track_number"] == 5
    assert row["disc_number"] == 1


def test_created_slot_links_primary_artist_and_enters_wanted_projection(imported_conn):
    album_id = _views_id(imported_conn)
    drake_id = imported_conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()[0]

    result = MT.materialize_missing_track(
        imported_conn, album_id, track_number=6, disc_number=1, title="Another Slot"
    )
    track_id = result["track_id"]

    artist_link = imported_conn.execute(
        "SELECT artist_id, role FROM lib2_track_artists WHERE track_id=?", (track_id,)
    ).fetchone()
    assert artist_link["artist_id"] == drake_id
    assert artist_link["role"] == "primary"
    projected = imported_conn.execute(
        "SELECT 1 FROM lib2_wanted_tracks WHERE track_id=?", (track_id,)
    ).fetchone()
    assert projected is not None


def test_created_slot_starts_unmonitored(imported_conn):
    album_id = _views_id(imported_conn)
    result = MT.materialize_missing_track(
        imported_conn, album_id, track_number=7, disc_number=1, title="Yet Another"
    )
    monitored = imported_conn.execute(
        "SELECT monitored FROM lib2_tracks WHERE id=?", (result["track_id"],)
    ).fetchone()[0]
    assert monitored == 0


def test_unknown_album_raises(imported_conn):
    with pytest.raises(MT.MissingTrackError):
        MT.materialize_missing_track(
            imported_conn, 999999, track_number=1, disc_number=1, title="Nope"
        )


def test_multidisc_slot_is_distinct_from_disc_one(imported_conn):
    album_id = _views_id(imported_conn)
    first = MT.materialize_missing_track(
        imported_conn, album_id, track_number=1, disc_number=2, title="Disc 2 Opener"
    )
    assert first["created"] is True
    # Disc 1 track 1 already exists ('One Dance') — the disc-2 slot must NOT
    # collide with it.
    disc1 = imported_conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=? AND track_number=1 AND disc_number=1",
        (album_id,),
    ).fetchone()[0]
    assert first["track_id"] != disc1
