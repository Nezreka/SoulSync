"""`add_to_wishlist()` resolves a `quality_profile_id` pointer onto the
wishlist row at insert time, so the download/import pipeline knows which
profile this item belongs to. It is only ever a pointer — every pipeline
stage resolves the profile's actual settings LIVE via
`core/quality/selection.py::load_profile_by_id` when it needs them, so
editing a profile later takes effect immediately for every item assigned to
it (see `core/downloads/master.py`, `core/imports/guards.py`).
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _track(track_id="sp1", name="Song", album_id="al1"):
    return {
        "id": track_id,
        "name": name,
        "artists": [{"id": "ar1", "name": "Artist"}],
        "album": {"id": album_id, "name": "Album", "artists": [{"id": "ar1", "name": "Artist"}]},
    }


def _wishlist_profile_id(db, spotify_track_id):
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT quality_profile_id FROM wishlist_tracks WHERE spotify_track_id=?",
            (spotify_track_id,),
        ).fetchone()
        return row["quality_profile_id"] if row else None
    finally:
        conn.close()


def test_add_to_wishlist_defaults_to_default_profile_when_omitted(db):
    """The 14 existing call sites across the repo never pass
    quality_profile_id — this must resolve to the app-wide default profile
    without the caller changing anything."""
    assert db.add_to_wishlist(_track("sp1"), source_type="manual", user_initiated=True) is True

    assert _wishlist_profile_id(db, "sp1") == 1  # the default profile's id


def test_add_to_wishlist_resolves_explicit_profile(db):
    pid = db.create_quality_profile("Strict", {
        "ranked_targets": [{"label": "FLAC", "format": "flac"}],
        "fallback_enabled": False,
    })

    assert db.add_to_wishlist(
        _track("sp2"), source_type="manual", user_initiated=True, quality_profile_id=pid,
    ) is True

    assert _wishlist_profile_id(db, "sp2") == pid


def test_add_to_wishlist_falls_back_to_default_for_unknown_profile_id(db):
    assert db.add_to_wishlist(
        _track("sp3"), source_type="manual", user_initiated=True, quality_profile_id=99999,
    ) is True

    assert _wishlist_profile_id(db, "sp3") == 1


def test_add_to_wishlist_does_not_hardcode_deleted_profile_one(db):
    """Users may delete the factory id=1 row. Unknown/omitted assignments must
    resolve to the promoted default, not leave a dangling quality_profile_id=1."""
    pid = db.create_quality_profile("Remaining Default", {"ranked_targets": []})
    assert pid is not None
    ok, reason = db.delete_quality_profile(1)
    assert ok is True and reason == ""

    default = next(p for p in db.list_quality_profiles() if p["is_default"])
    assert default["id"] != 1

    assert db.add_to_wishlist(
        _track("sp3b"), source_type="manual", user_initiated=True, quality_profile_id=99999,
    ) is True
    assert _wishlist_profile_id(db, "sp3b") == default["id"]


def test_get_wishlist_tracks_surfaces_quality_profile_id(db):
    pid = db.create_quality_profile("For Read", {
        "ranked_targets": [{"label": "MP3", "format": "mp3", "min_bitrate": 320}],
        "fallback_enabled": True,
    })
    db.add_to_wishlist(_track("sp4"), source_type="manual", user_initiated=True, quality_profile_id=pid)

    tracks = db.get_wishlist_tracks(profile_id=1)
    match = next(t for t in tracks if t["spotify_track_id"] == "sp4")
    assert match["quality_profile_id"] == pid


def test_ensure_wishlist_quality_columns_drops_leftover_frozen_columns(db):
    """An intermediate version of this work denormalized acoustid_required/
    fallback_allowed/downsample_enabled onto wishlist_tracks too, before that
    was simplified down to just the quality_profile_id pointer. Anyone who
    ran that version has those 3 dead columns sitting in their real DB;
    `_ensure_wishlist_quality_columns` must clean them up on the next boot,
    not just skip adding them for fresh installs."""
    conn = db._get_connection()
    try:
        for dead_col in ("acoustid_required", "fallback_allowed", "downsample_enabled"):
            conn.execute(f"ALTER TABLE wishlist_tracks ADD COLUMN {dead_col} INTEGER DEFAULT NULL")
        conn.commit()
        cols_before = {r[1] for r in conn.execute("PRAGMA table_info(wishlist_tracks)").fetchall()}
        assert {"acoustid_required", "fallback_allowed", "downsample_enabled"} <= cols_before

        cursor = conn.cursor()
        db._ensure_wishlist_quality_columns(cursor)
        conn.commit()

        cols_after = {r[1] for r in conn.execute("PRAGMA table_info(wishlist_tracks)").fetchall()}
        assert "acoustid_required" not in cols_after
        assert "fallback_allowed" not in cols_after
        assert "downsample_enabled" not in cols_after
        assert "quality_profile_id" in cols_after
    finally:
        conn.close()
