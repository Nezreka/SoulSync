"""#1289 — a manual library match must immediately refresh mirrored flags.

The mirrored playlist card's "missing / in library" counts are a stored
per-track cache (extra_data.in_library) written only during sync
(_record_library_membership). Saving a manual match left the card showing
the track as missing until the next sync; deleting the match stranded the
flag as in-library. Both directions are covered here.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.library import manual_library_match as mlm
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _mirror(db, name, source_track_ids, profile_id=1):
    """Create a mirrored playlist; return its id."""
    return db.mirror_playlist(
        source="youtube",
        source_playlist_id=name,
        name=name,
        tracks=[
            {"track_name": f"Song {t}", "artist_name": "Artist", "source_track_id": t}
            for t in source_track_ids
        ],
        profile_id=profile_id,
    )


def _extra(db, playlist_id, source_track_id):
    for t in db.get_mirrored_playlist_tracks(playlist_id):
        if t["source_track_id"] == source_track_id:
            raw = t.get("extra_data")
            return json.loads(raw) if raw else {}
    raise AssertionError(source_track_id)


def _live(db, *live_ids):
    """Patch liveness: only the given library ids resolve."""
    live = set(live_ids)
    return patch.object(
        db,
        "api_get_tracks_by_ids",
        side_effect=lambda ids: [{"id": i} for i in ids if i in live],
    )


def _match_id(db, source, tid, server_source=""):
    row = db.get_manual_library_match(1, source, tid, server_source)
    assert row is not None
    return row["id"]


def test_save_marks_matching_mirrored_tracks_in_library(db):
    p1 = _mirror(db, "pl1", ["t1", "t2"])
    p2 = _mirror(db, "pl2", ["t1"])
    with _live(db, "lib1"):
        assert mlm.save_match(db, 1, "spotify", "t1", "lib1") is True
    assert _extra(db, p1, "t1")["in_library"] is True
    assert _extra(db, p1, "t1")["library_track_id"] == "lib1"
    assert _extra(db, p2, "t1")["in_library"] is True  # other playlist, same id


def test_save_leaves_unrelated_tracks_alone(db):
    p1 = _mirror(db, "pl1", ["t1", "t2"])
    with _live(db, "lib1"):
        mlm.save_match(db, 1, "spotify", "t1", "lib1")
    assert _extra(db, p1, "t2").get("in_library") is not True


def test_save_with_dead_library_track_does_not_stamp(db):
    p1 = _mirror(db, "pl1", ["t1"])
    with _live(db):  # libDead resolves to nothing
        assert mlm.save_match(db, 1, "spotify", "t1", "libDead") is True
    assert _extra(db, p1, "t1").get("in_library") is not True


def test_delete_resets_the_flag(db):
    p1 = _mirror(db, "pl1", ["t1"])
    with _live(db, "lib1"):
        mlm.save_match(db, 1, "spotify", "t1", "lib1")
    mid = _match_id(db, "spotify", "t1")
    with _live(db, "lib1"):
        assert mlm.delete_match(db, mid, 1) is True
    flags = _extra(db, p1, "t1")
    assert flags["in_library"] is False
    assert flags["library_track_id"] is None


def test_delete_keeps_flag_when_another_live_match_covers_the_id(db):
    p1 = _mirror(db, "pl1", ["t1"])
    with _live(db, "lib1", "lib2"):
        mlm.save_match(db, 1, "spotify", "t1", "lib1")
        mlm.save_match(db, 1, "youtube", "t1", "lib2")
        mid = _match_id(db, "spotify", "t1")
        assert mlm.delete_match(db, mid, 1) is True
    assert _extra(db, p1, "t1")["in_library"] is True


def test_failed_save_touches_nothing(db):
    p1 = _mirror(db, "pl1", ["t1"])
    with _live(db, "lib1"), patch.object(
        db, "save_manual_library_match", return_value=False
    ):
        assert mlm.save_match(db, 1, "spotify", "t1", "lib1") is False
    assert _extra(db, p1, "t1").get("in_library") is not True


def test_delete_uses_server_source_from_row(db):
    """A surviving match with non-empty server_source must be seen."""
    p1 = _mirror(db, "pl1", ["t1"])
    with _live(db, "lib1", "lib2"):
        # First match: server_source='' (Manual Library Match UI)
        mlm.save_match(db, 1, "spotify", "t1", "lib1", server_source="")
        # Second match: server_source='plex' (Find & Add) — same source_track_id
        mlm.save_match(db, 1, "youtube", "t1", "lib2", server_source="plex")
        # Delete the first one; the plex survivor is live, so the flag stays
        mid = _match_id(db, "spotify", "t1", "")
        assert mlm.delete_match(db, mid, 1) is True
    assert _extra(db, p1, "t1")["in_library"] is True


def test_delete_beyond_100_row_cap_still_resets(db):
    """get_by_id must work for matches beyond the list cap."""
    p1 = _mirror(db, "pl1", ["t0"])
    with _live(db, "lib1"):
        for i in range(105):
            mlm.save_match(db, 1, "spotify", f"t{i}", "lib1")
    # Age t0's row so it deterministically falls outside the 100
    # most-recently-updated (updated_at is second-precision; a fast test run
    # gives every row the same timestamp and the order is arbitrary).
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE manual_library_track_matches SET updated_at = '2000-01-01 00:00:00'"
            " WHERE profile_id = 1 AND source_track_id = 't0'"
        )
        conn.commit()
    assert len(db.list_manual_library_matches(1, 100)) == 100
    assert all(
        m["source_track_id"] != "t0" for m in db.list_manual_library_matches(1, 100)
    )
    # But get_by_id finds it, and deleting it resets the flag
    mid = _match_id(db, "spotify", "t0")
    with _live(db, "lib1"):
        assert mlm.delete_match(db, mid, 1) is True
    assert _extra(db, p1, "t0")["in_library"] is False


def test_delete_mixed_liveness_live_survivor_keeps_flag(db):
    """A dead survivor must not mask a live one."""
    p1 = _mirror(db, "pl1", ["t1"])
    with _live(db, "libLive"):  # libDead is NOT live
        mlm.save_match(db, 1, "spotify", "t1", "libDead", server_source="a")
        mlm.save_match(db, 1, "youtube", "t1", "libLive", server_source="plex")
        mlm.save_match(db, 1, "tidal", "t1", "libLive", server_source="")
        doomed = _match_id(db, "tidal", "t1", "")
        assert mlm.delete_match(db, doomed, 1) is True
    # A live survivor (libLive) still covers t1 → flag stays True
    assert _extra(db, p1, "t1")["in_library"] is True


def test_delete_all_survivors_dead_clears_flag(db):
    """When every survivor is dead, the flag IS cleared."""
    p1 = _mirror(db, "pl1", ["t1"])
    with _live(db):  # nothing is live
        mlm.save_match(db, 1, "spotify", "t1", "libDead1", server_source="a")
        mlm.save_match(db, 1, "youtube", "t1", "libDead2", server_source="plex")
        mlm.save_match(db, 1, "tidal", "t1", "libDead3", server_source="")
        # Saves didn't stamp (nothing live); seed the flag to prove the
        # delete actively clears it rather than leaving it stranded.
        track_id = db.get_mirrored_playlist_tracks(p1)[0]["id"]
        db.update_mirrored_track_extra_data(
            track_id, {"in_library": True, "library_track_id": "libDead1"}
        )
        doomed = _match_id(db, "tidal", "t1", "")
        assert mlm.delete_match(db, doomed, 1) is True
    assert _extra(db, p1, "t1")["in_library"] is False
