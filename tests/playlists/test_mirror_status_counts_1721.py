"""#1721 (kevin2xk): on a real database, what the card and the detail modal say
about where a mirrored playlist's tracks stand.

- the card's wishlisted count asked only for the playlist's own id, but sync
  files a deezer-identified track under the deezer id, so it read 0.
- the card's "need a match" count is what the wing it pool lists for it.
- the detail endpoint stamps each track with its status, plus the totals.
"""

from __future__ import annotations

import json

from api.mirrored_playlists import _stamp_track_statuses
from database.music_database import MusicDatabase

STUB = {"discovered": True, "provider": "wing_it_fallback", "wing_it_fallback": True,
        "matched_data": {"id": "wing_it_ab12cd34", "name": "x"}}


def _db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "m.db"))


def _playlist(db, profile_id=1, source_id="pl1"):
    with db._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO mirrored_playlists (source, source_playlist_id, name, profile_id) VALUES (?,?,?,?)",
            ("spotify_public", source_id, "Reading Soundtrack", profile_id))
        conn.commit()
        return cur.lastrowid


def _track(db, pid, pos, sid, extra):
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO mirrored_playlist_tracks (playlist_id, position, track_name, artist_name, "
            "source_track_id, extra_data) VALUES (?,?,?,?,?,?)",
            (pid, pos, f"Track {pos}", "Artist", sid, json.dumps(extra) if extra is not None else None))
        conn.commit()


def _wish(db, track_id, profile_id=1):
    with db._get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(wishlist_tracks)")}
        if "profile_id" in cols:
            conn.execute("INSERT INTO wishlist_tracks (spotify_track_id, spotify_data, profile_id) VALUES (?,?,?)",
                         (track_id, "{}", profile_id))
        else:
            conn.execute("INSERT INTO wishlist_tracks (spotify_track_id, spotify_data) VALUES (?,?)",
                         (track_id, "{}"))
        conn.commit()


def _seed(db):
    pid = _playlist(db)
    _track(db, pid, 1, "sp1", {"discovered": True, "matched_data": {"id": "dz1"},
                               "in_library": True, "library_checked_at": 1})
    # wishlisted under the deezer id sync filed it under, not the spotify id
    _track(db, pid, 2, "sp2", {"discovered": True, "matched_data": {"id": "dz2"},
                               "in_library": False, "library_checked_at": 1})
    _track(db, pid, 3, "sp3", {**STUB, "in_library": False, "library_checked_at": 1})
    _track(db, pid, 4, "sp4", {"discovered": True, "matched_data": {"id": "dz4"},
                               "in_library": False, "library_checked_at": 1})
    _track(db, pid, 5, "sp5", None)
    _wish(db, "dz2")
    return pid


def test_card_counts_find_the_deezer_filed_wishlist_row_and_the_guesses(tmp_path):
    db = _db(tmp_path)
    pid = _seed(db)
    counts = db.get_all_mirrored_playlist_status_counts(profile_id=1)[pid]
    assert counts["wishlisted"] == 1
    assert counts["needs_match"] == 1
    assert counts["needs_match"] == db.get_wing_it_pool_stats(playlist_id=pid)["wing_it"]
    assert db.get_mirrored_playlist_status_counts(pid)["wishlisted"] == 1


def test_a_playlist_with_nothing_to_fix_counts_zero(tmp_path):
    db = _db(tmp_path)
    pid = _playlist(db)
    _track(db, pid, 1, "sp1", {"discovered": True, "matched_data": {"id": "dz1"}})
    counts = db.get_all_mirrored_playlist_status_counts(profile_id=1)[pid]
    assert counts["needs_match"] == 0 and counts["wishlisted"] == 0


def test_detail_stamps_every_track_and_the_totals(tmp_path):
    db = _db(tmp_path)
    pid = _seed(db)
    playlist = db.get_mirrored_playlist(pid)
    playlist["tracks"] = db.get_mirrored_playlist_tracks(pid)
    _stamp_track_statuses(db, playlist)

    assert [t["status"] for t in playlist["tracks"]] == [
        "in_library", "wishlisted", "needs_match", "missing", "unchecked"]
    assert playlist["status_counts"] == {"in_library": 1, "wishlisted": 1, "needs_match": 1,
                                         "missing": 1, "unchecked": 1}


def test_another_profiles_wishlist_does_not_count(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        has_profile = "profile_id" in {r[1] for r in conn.execute("PRAGMA table_info(wishlist_tracks)")}
    assert has_profile
    _wish(db, "dzX", profile_id=2)
    assert db.get_wishlisted_ids(["dzX"], profile_id=1) == set()
    assert db.get_wishlisted_ids(["dzX"], profile_id=2) == {"dzX"}
    assert db.get_wishlisted_ids([]) == set()
