"""Wishlist add idempotency and context upsert (audit P1-09 / P1-10).

P1-09: adding the same track+album twice used to create BOTH a bare-id row
and a `track::album` composite row (the second add saw the bare row and
assumed "different album"). The composite key is now canonical from the
first insert, so a repeat add updates the existing row instead.

P1-10: a repeat add refreshes the waiting row's pipeline context (quality
profile, source info, payload) — a later profile change in Library v2 must
reach the entry that is actually queued — without downgrading manual
provenance or resetting retry state.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _track(track_id="track-1", album_id="album-1", name="Song", artist="Artist"):
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": artist}],
        "album": {"id": album_id, "name": "Album", "images": []},
    }


def _rows(db):
    with db._get_connection() as conn:
        return conn.execute(
            "SELECT spotify_track_id, source_type, quality_profile_id, retry_count "
            "FROM wishlist_tracks ORDER BY id").fetchall()


def _seed_profiles(db):
    """Two real quality profiles so profile updates can be observed."""
    with db._get_connection() as conn:
        cur = conn.cursor()
        ids = []
        for name, default in (("Standard", 1), ("Lossless", 0)):
            cur.execute(
                "INSERT INTO quality_profiles(name, upgrade_policy, ranked_targets, is_default) "
                "VALUES(?, 'acceptable', '[]', ?)", (name, default))
            ids.append(cur.lastrowid)
        conn.commit()
        return ids


def test_same_track_same_album_is_idempotent(db):
    """The audit reproduction: used to return True, True and leave two rows
    ('track-1' and 'track-1::album-1'). Now: one row, keyed canonically."""
    assert db.add_to_wishlist(_track(), source_type="album") is True
    assert db.add_to_wishlist(_track(), source_type="album") is False
    assert db.add_to_wishlist(_track(), source_type="album") is False
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["spotify_track_id"] == "track-1::album-1"


def test_same_track_different_album_coexists(db):
    assert db.add_to_wishlist(_track(album_id="album-1"), source_type="album") is True
    assert db.add_to_wishlist(_track(album_id="album-2"), source_type="album") is True
    keys = {r["spotify_track_id"] for r in _rows(db)}
    assert keys == {"track-1::album-1", "track-1::album-2"}


def test_legacy_bare_row_is_adopted_not_duplicated(db):
    """Pre-fix installs keyed the first album under the bare track id. A
    repeat add of the SAME album must update that row, not add a composite."""
    import json
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO wishlist_tracks(spotify_track_id, spotify_data, source_type, profile_id) "
            "VALUES('track-1', ?, 'album', 1)", (json.dumps(_track()),))
        conn.commit()
    assert db.add_to_wishlist(_track(), source_type="album") is False
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["spotify_track_id"] == "track-1"


def test_repeat_add_updates_quality_profile(db):
    """P1-10: a later add with a different quality profile reaches the row."""
    standard, lossless = _seed_profiles(db)
    assert db.add_to_wishlist(_track(), source_type="album",
                              quality_profile_id=standard) is True
    assert db.add_to_wishlist(_track(), source_type="album",
                              quality_profile_id=lossless) is False
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["quality_profile_id"] == lossless


def test_repeat_add_without_profile_keeps_existing(db):
    standard, _lossless = _seed_profiles(db)
    assert db.add_to_wishlist(_track(), source_type="album",
                              quality_profile_id=standard) is True
    assert db.add_to_wishlist(_track(), source_type="album") is False
    assert _rows(db)[0]["quality_profile_id"] == standard


def test_auto_readd_does_not_downgrade_manual_provenance(db):
    assert db.add_to_wishlist(_track(), source_type="manual") is True
    assert db.add_to_wishlist(_track(), source_type="playlist") is False
    assert _rows(db)[0]["source_type"] == "manual"


def test_repeat_add_preserves_retry_state(db):
    """The old INSERT OR REPLACE path silently reset retry_count/date_added."""
    assert db.add_to_wishlist(_track(), source_type="album") is True
    with db._get_connection() as conn:
        conn.execute("UPDATE wishlist_tracks SET retry_count = 3")
        conn.commit()
    assert db.add_to_wishlist(_track(), source_type="album") is False
    assert _rows(db)[0]["retry_count"] == 3


def test_bare_id_removal_clears_composite_rows(db):
    """Success-cleanup paths only know the source track id — it must clear
    however the entry was keyed."""
    assert db.add_to_wishlist(_track(), source_type="album") is True
    assert db.remove_from_wishlist("track-1") is True
    assert _rows(db) == []


def test_update_wishlist_retry_success_clears_composite_rows(db):
    assert db.add_to_wishlist(_track(), source_type="album") is True
    assert db.update_wishlist_retry("track-1", success=True) is True
    assert _rows(db) == []
