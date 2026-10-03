"""S4: clearing the wishlist must write ignore entries for the cleared tracks.

The ignore list (#874) is the "stop auto-grabbing this" signal — the add
path checks it before any automatic re-add. ``clear_wishlist`` deleted the
rows but wrote no ignore entries, so the next watchlist scan / failed-track
capture re-added exactly the tracks the user just cleared.
"""
import json

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _seed_wishlist(db, profile_id=1):
    tracks = [
        ("sp-1", "Song One", "Artist One"),
        ("sp-2", "Song Two", "Artist Two"),
    ]
    with db._get_connection() as conn:
        cur = conn.cursor()
        for track_id, name, artist in tracks:
            cur.execute(
                """INSERT INTO wishlist_tracks
                   (spotify_track_id, spotify_data, profile_id, date_added)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
                (track_id,
                 json.dumps({"id": track_id, "name": name,
                             "artists": [{"name": artist}]}),
                 profile_id),
            )
        conn.commit()
    return [t[0] for t in tracks]


def test_s4_clear_wishlist_writes_ignore_entries(db):
    ids = _seed_wishlist(db)
    assert db.clear_wishlist(profile_id=1) is True
    for track_id in ids:
        assert db.is_track_ignored(track_id, profile_id=1) is True, (
            f"cleared track {track_id} has no ignore entry — it will be auto re-added")


def test_s4_clear_wishlist_still_clears_rows(db):
    _seed_wishlist(db)
    assert db.clear_wishlist(profile_id=1) is True
    assert db.get_wishlist_tracks(profile_id=1) == []


def test_s4_clear_wishlist_is_profile_scoped(db):
    _seed_wishlist(db, profile_id=1)
    _seed_wishlist(db, profile_id=2)
    assert db.clear_wishlist(profile_id=1) is True
    # profile 1's tracks ignored; profile 2 untouched
    assert db.is_track_ignored("sp-1", profile_id=1) is True
    assert db.is_track_ignored("sp-1", profile_id=2) is False
    assert len(db.get_wishlist_tracks(profile_id=2)) == 2


def test_s4_clear_is_atomic_when_ignore_write_fails(db, monkeypatch):
    """Delete + ignore writes are one transaction: if an ignore write
    blows up mid-clear, the delete rolls back instead of leaving rows
    deleted with no ignore entries (the pre-fix shape committed the
    delete first, so a later failure silently un-stuck the clear)."""
    import core.wishlist.ignore as ignore_mod

    _seed_wishlist(db)
    real_extract = ignore_mod.extract_display
    calls = []

    def _boom(spotify_data):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("simulated ignore-write failure")
        return real_extract(spotify_data)

    monkeypatch.setattr(ignore_mod, "extract_display", _boom)
    assert db.clear_wishlist(profile_id=1) is False
    # rolled back: rows still there, no half-written ignores
    assert len(db.get_wishlist_tracks(profile_id=1)) == 2
    with db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM wishlist_ignore").fetchone()[0] == 0
