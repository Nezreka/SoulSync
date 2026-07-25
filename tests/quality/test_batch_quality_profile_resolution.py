"""Batch-level Quality Profile resolution must not trample per-item intent.

Review-round-2 finding R2-13: ``_tracks_with_mirrored_quality_profile``
overwrote EVERY track's ``quality_profile_id`` as soon as a mirror resolved —
and the resolver's last fallback matches on playlist name alone, with no ref or
source constraint. A wishlist batch whose rows carry their own per-item
assignment (P1-02) therefore lost it to whatever mirror happened to share the
modal's label.
"""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    database = MusicDatabase(str(tmp_path / "m.db"))
    monkeypatch.setattr(web_server, "get_database", lambda *a, **k: database)
    return database


def _mirror(db, name, quality_profile_id):
    return db.mirror_playlist(
        source="spotify",
        source_playlist_id="up-1",
        name=name,
        tracks=[{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=1,
        quality_profile_id=quality_profile_id,
    )


def test_a_per_row_assignment_survives_a_name_only_mirror_match(db):
    mirror_profile = db.create_quality_profile("Mirror", {})
    own_profile = db.create_quality_profile("Own", {})
    _mirror(db, "Wishlist", mirror_profile)

    tracks, batch_profile = web_server._tracks_with_mirrored_quality_profile(
        "wishlist", "Wishlist",
        [
            {"id": "t1", "quality_profile_id": own_profile},
            {"id": "t2"},
        ],
        profile_id=1,
    )

    assert batch_profile == mirror_profile
    assert tracks[0]["quality_profile_id"] == own_profile, "per-item intent must win"
    assert tracks[1]["quality_profile_id"] == mirror_profile, "unstamped rows still get filled"


def test_an_explicit_dialog_choice_overrides_every_row(db):
    own_profile = db.create_quality_profile("Own", {})
    chosen = db.create_quality_profile("Chosen", {})

    tracks, batch_profile = web_server._tracks_with_mirrored_quality_profile(
        "wishlist", "Wishlist",
        [{"id": "t1", "quality_profile_id": own_profile}],
        profile_id=1,
        explicit_quality_profile_id=chosen,
    )

    assert batch_profile == chosen
    assert tracks[0]["quality_profile_id"] == chosen


def test_resolution_is_available_before_any_track_is_stamped(db):
    """R2-07: the sync-history row is written before the tracks are stamped."""
    mirror_profile = db.create_quality_profile("Mirror", {})
    _mirror(db, "My Mix", mirror_profile)

    resolved = web_server._resolve_batch_quality_profile_id(
        "up-1", "My Mix", profile_id=1, source="spotify",
    )

    assert resolved == mirror_profile


def test_resolution_returns_none_when_nothing_matches(db):
    assert web_server._resolve_batch_quality_profile_id(
        "unknown-ref", "No Such Playlist", profile_id=1,
    ) is None
