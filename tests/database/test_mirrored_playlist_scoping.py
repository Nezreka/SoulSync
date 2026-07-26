"""Mirrored-playlist DB access must be scopeable to one SoulSync profile.

Audit finding P0-01: several mirror read/write helpers keyed on the primary key
alone, so any logged-in profile could read, rename, re-point, run, clear or
delete another profile's mirror — and read its persisted ``quality_profile_id``
— just by guessing a numeric id (or the synthetic ``auto_mirror_<pk>`` form).

Every helper below now takes an optional ``profile_id``. When supplied the id is
part of the SAME statement, so a foreign row behaves exactly like a missing one.
"""

from __future__ import annotations

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


def _mirror(db, *, profile_id: int, source: str = "spotify", source_playlist_id: str = "p1",
            name: str = "Mix", tracks=None):
    return db.mirror_playlist(
        source=source,
        source_playlist_id=source_playlist_id,
        name=name,
        tracks=tracks if tracks is not None else [{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=profile_id,
    )


def test_get_mirrored_playlist_is_scopeable(db):
    pk = _mirror(db, profile_id=2)

    assert db.get_mirrored_playlist(pk, profile_id=2)["id"] == pk
    assert db.get_mirrored_playlist(pk, profile_id=1) is None
    # Unscoped stays available for trusted internal callers that already know
    # which mirror (and therefore which profile) they are acting for.
    assert db.get_mirrored_playlist(pk)["id"] == pk


def test_get_mirrored_playlist_tracks_is_scopeable(db):
    pk = _mirror(db, profile_id=2)

    assert len(db.get_mirrored_playlist_tracks(pk, profile_id=2)) == 1
    assert db.get_mirrored_playlist_tracks(pk, profile_id=1) == []


def test_resolver_pk_fallback_cannot_cross_profiles(db):
    """The reported repro: profile 1 asks for ``auto_mirror_<pk-of-profile-2>``."""
    pk = _mirror(db, profile_id=2, source="deezer", source_playlist_id="abc")

    assert db.resolve_mirrored_playlist(f"auto_mirror_{pk}", profile_id=1,
                                        default_source="mirrored") is None
    assert db.resolve_mirrored_playlist(str(pk), profile_id=1, default_source="spotify") is None
    assert db.resolve_mirrored_playlist(f"auto_mirror_{pk}", profile_id=2,
                                        default_source="mirrored")["id"] == pk


def test_resolver_pk_fallback_does_not_leak_quality_profile(db):
    profile_id = db.create_quality_profile("Foreign Hi-Res", {})
    assert profile_id
    pk = _mirror(db, profile_id=2)
    assert db.set_mirrored_playlist_quality_profile(pk, profile_id, profile_id=2)

    assert db.resolve_mirrored_playlist_assignment(f"auto_mirror_{pk}", None, 1) is None
    assert db.resolve_mirrored_playlist_assignment(f"auto_mirror_{pk}", None, 2)[
        "quality_profile_id"] == profile_id


def test_set_custom_name_is_scopeable(db):
    pk = _mirror(db, profile_id=2)

    assert db.set_mirrored_playlist_custom_name(pk, "Stolen", profile_id=1) is False
    assert db.get_mirrored_playlist(pk).get("custom_name") in (None, "")

    assert db.set_mirrored_playlist_custom_name(pk, "Mine", profile_id=2) is True
    assert db.get_mirrored_playlist(pk)["custom_name"] == "Mine"


def test_set_organize_by_playlist_is_scopeable(db):
    pk = _mirror(db, profile_id=2)

    assert db.set_mirrored_playlist_organize_by_playlist(pk, True, profile_id=1) is False
    assert db.get_mirrored_playlist(pk)["organize_by_playlist"] is False

    assert db.set_mirrored_playlist_organize_by_playlist(pk, True, profile_id=2) is True
    assert db.get_mirrored_playlist(pk)["organize_by_playlist"] is True


def test_update_source_ref_is_scopeable(db):
    pk = _mirror(db, profile_id=2, source="youtube", source_playlist_id="old")

    assert db.update_mirrored_playlist_source_ref(pk, "new", None, profile_id=1) is False
    assert db.get_mirrored_playlist(pk)["source_playlist_id"] == "old"

    assert db.update_mirrored_playlist_source_ref(pk, "new", None, profile_id=2) is True
    assert db.get_mirrored_playlist(pk)["source_playlist_id"] == "new"


def test_clear_discovery_is_scopeable(db):
    pk = _mirror(
        db,
        profile_id=2,
        source="youtube",
        tracks=[{"track_name": "Song", "artist_name": "Artist",
                 "source_track_id": "yt1", "extra_data": {"discovered": True}}],
    )

    assert db.clear_mirrored_playlist_discovery(pk, profile_id=1) == 0
    assert db.get_mirrored_playlist_tracks(pk)[0]["extra_data"] is not None

    assert db.clear_mirrored_playlist_discovery(pk, profile_id=2) == 1
    assert db.get_mirrored_playlist_tracks(pk)[0]["extra_data"] is None


def test_mark_explored_is_scopeable(db):
    pk = _mirror(db, profile_id=2)

    assert db.mark_mirrored_playlist_explored(pk, profile_id=1) is False
    assert db.mark_mirrored_playlist_explored(pk, profile_id=2) is True


def test_delete_is_scopeable(db):
    pk = _mirror(db, profile_id=2)

    assert db.delete_mirrored_playlist(pk, profile_id=1) is False
    assert db.get_mirrored_playlist(pk) is not None

    assert db.delete_mirrored_playlist(pk, profile_id=2) is True
    assert db.get_mirrored_playlist(pk) is None
