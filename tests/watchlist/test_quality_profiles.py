from database.music_database import MusicDatabase


def _new_profile(db: MusicDatabase, name: str) -> int:
    profile_id = db.create_quality_profile(
        name,
        {"ranked_targets": [{"format": "flac", "bit_depth": 24}]},
    )
    assert profile_id is not None
    return int(profile_id)


def test_new_watchlist_artist_inherits_current_global_quality_profile(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    default_id = next(p["id"] for p in db.list_quality_profiles() if p["is_default"])

    assert db.add_artist_to_watchlist(
        "artist-1", "Artist", source="spotify", profile_id=1
    )

    artist = db.get_watchlist_artists(profile_id=1)[0]
    assert artist.quality_profile_id == default_id


def test_existing_watchlist_assignment_is_overwritten_when_explicit(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    first = _new_profile(db, "Watchlist First")
    second = _new_profile(db, "Watchlist Second")

    assert db.add_artist_to_watchlist(
        "artist-1",
        "Artist",
        source="spotify",
        profile_id=1,
        quality_profile_id=first,
    )
    assert db.add_artist_to_watchlist(
        "artist-1",
        "Artist",
        source="spotify",
        profile_id=1,
        quality_profile_id=second,
    )

    artist = db.get_watchlist_artists(profile_id=1)[0]
    assert artist.quality_profile_id == second


def test_deleting_assigned_profile_repoints_watchlist_to_default(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    assigned = _new_profile(db, "Watchlist Delete")
    default_id = next(p["id"] for p in db.list_quality_profiles() if p["is_default"])
    db.add_artist_to_watchlist(
        "artist-1",
        "Artist",
        source="spotify",
        quality_profile_id=assigned,
    )

    assert db.delete_quality_profile(assigned)[0] is True
    assert db.get_watchlist_artists(profile_id=1)[0].quality_profile_id == default_id


def test_authoritative_watchlist_add_updates_existing_wishlist_profile(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    first = _new_profile(db, "Wishlist First")
    second = _new_profile(db, "Wishlist Second")
    track = {
        "id": "track-1",
        "name": "Song",
        "artists": [{"name": "Artist"}],
        "album": {"id": "album-1", "name": "Album"},
    }

    # Exercise the default duplicate-enabled path.
    assert db.add_to_wishlist(track, quality_profile_id=first) is True
    assert db.add_to_wishlist(track, quality_profile_id=second) is False

    row = db.get_wishlist_tracks(profile_id=1)[0]
    assert row["quality_profile_id"] == second
