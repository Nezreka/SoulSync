"""§52.2 quality-profile precedence and provenance contracts."""

from core.library2.profile_lookup import (
    assign_quality_profile,
    effective_quality_profile,
    lib2_quality_profile_for_artist,
)
from core.library2.queries import get_album
from core.library2.wishlist_mirror import track_wishlist_payload


def _ids(conn):
    artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]
    album_id = conn.execute(
        "SELECT id FROM lib2_albums WHERE primary_artist_id=? AND title='Views'",
        (artist_id,),
    ).fetchone()[0]
    track_id = conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=? AND title='One Dance'",
        (album_id,),
    ).fetchone()[0]
    return artist_id, album_id, track_id


def test_specific_profile_overrides_survive_parent_changes(imported_conn):
    artist_id, album_id, track_id = _ids(imported_conn)

    artist = assign_quality_profile(imported_conn, "artists", artist_id, 2)
    assert artist["source"] == "artist"
    assert effective_quality_profile(imported_conn, "albums", album_id) == {
        "id": 2,
        "source": "artist",
        "source_id": artist_id,
        "explicit": True,
    }
    assert effective_quality_profile(imported_conn, "tracks", track_id)["source"] == "artist"

    assign_quality_profile(imported_conn, "albums", album_id, 1)
    assert effective_quality_profile(imported_conn, "tracks", track_id)["source"] == "album"

    assign_quality_profile(imported_conn, "tracks", track_id, 2)
    assign_quality_profile(imported_conn, "albums", album_id, 1)
    track = effective_quality_profile(imported_conn, "tracks", track_id)
    assert track == {
        "id": 2,
        "source": "track",
        "source_id": track_id,
        "explicit": True,
    }
    stored = imported_conn.execute(
        "SELECT quality_profile_id, quality_profile_explicit FROM lib2_tracks WHERE id=?",
        (track_id,),
    ).fetchone()
    assert tuple(stored) == (2, 1)


def test_clearing_overrides_walks_back_to_album_artist_then_global(imported_conn):
    artist_id, album_id, track_id = _ids(imported_conn)
    assign_quality_profile(imported_conn, "artists", artist_id, 2)
    assign_quality_profile(imported_conn, "albums", album_id, 1)
    assign_quality_profile(imported_conn, "tracks", track_id, 2)

    assert assign_quality_profile(imported_conn, "tracks", track_id, None)["source"] == "album"
    assert assign_quality_profile(imported_conn, "albums", album_id, None)["source"] == "artist"
    artist = assign_quality_profile(imported_conn, "artists", artist_id, None)
    assert artist["source"] == "global"
    assert effective_quality_profile(imported_conn, "tracks", track_id)["source"] == "global"


def test_wishlist_payload_uses_central_effective_profile_and_source(imported_conn):
    artist_id, album_id, track_id = _ids(imported_conn)
    assign_quality_profile(imported_conn, "artists", artist_id, 2)
    assign_quality_profile(imported_conn, "albums", album_id, 1)

    payload = track_wishlist_payload(imported_conn, track_id)

    assert payload is not None
    assert payload["quality_profile_id"] == 1
    assert payload["quality_profile"]["source"] == "album"
    assert payload["_source_info"]["quality_profile_source"] == "album"
    assert payload["_source_info"]["quality_profile_source_id"] == album_id


def test_album_payload_distinguishes_inherited_source_from_own_override(imported_conn):
    artist_id, album_id, _track_id = _ids(imported_conn)
    assign_quality_profile(imported_conn, "artists", artist_id, 2)

    inherited = get_album(imported_conn, album_id)

    assert inherited is not None
    assert inherited["quality_profile_source"] == "artist"
    assert inherited["quality_profile_explicit"] is False
    assert all(track["quality_profile_source"] == "artist" for track in inherited["tracks"])

    assign_quality_profile(imported_conn, "albums", album_id, 1)
    explicit = get_album(imported_conn, album_id)
    assert explicit is not None
    assert explicit["quality_profile_source"] == "album"
    assert explicit["quality_profile_explicit"] is True


def test_legacy_artist_lookup_follows_a_changed_global_default(
    imported_conn, legacy_db, monkeypatch,
):
    artist_id, _album_id, _track_id = _ids(imported_conn)
    assign_quality_profile(imported_conn, "artists", artist_id, None)
    imported_conn.execute("UPDATE quality_profiles SET is_default=0")
    imported_conn.execute("UPDATE quality_profiles SET is_default=1 WHERE id=2")
    imported_conn.commit()

    from config.settings import config_manager

    original_get = config_manager.get
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: (
            True if key == "features.library_v2" else original_get(key, default)
        ),
    )

    assert lib2_quality_profile_for_artist(legacy_db, "Drake") == 2
