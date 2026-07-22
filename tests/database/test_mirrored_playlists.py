import pytest

from database.music_database import MusicDatabase


def test_update_mirrored_playlist_source_ref_preserves_tracks(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = db.mirror_playlist(
        source="youtube",
        source_playlist_id="oldhash",
        name="Mirror",
        tracks=[
            {
                "track_name": "Song",
                "artist_name": "Artist",
                "source_track_id": "yt1",
                "extra_data": {"discovered": True},
            }
        ],
        profile_id=1,
        description="https://youtube.com/playlist?list=old",
    )

    assert playlist_id is not None

    updated = db.update_mirrored_playlist_source_ref(
        playlist_id,
        "newhash",
        "https://youtube.com/playlist?list=new",
    )

    assert updated is True
    playlist = db.get_mirrored_playlist(playlist_id)
    assert playlist["source_playlist_id"] == "newhash"
    assert playlist["description"] == "https://youtube.com/playlist?list=new"

    tracks = db.get_mirrored_playlist_tracks(playlist_id)
    assert len(tracks) == 1
    assert tracks[0]["track_name"] == "Song"
    assert tracks[0]["extra_data"] is not None


def test_playlist_quality_profile_can_be_set_and_cleared(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = db.mirror_playlist(
        source="spotify",
        source_playlist_id="quality",
        name="Quality",
        tracks=[{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=1,
    )

    assert db.set_mirrored_playlist_quality_profile(playlist_id, 2) is True
    assert db.get_mirrored_playlist(playlist_id)["quality_profile_id"] == 2
    assert db.set_mirrored_playlist_quality_profile(playlist_id, None) is True
    assert db.get_mirrored_playlist(playlist_id)["quality_profile_id"] is None


def test_mirror_playlist_refresh_preserves_existing_description(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = db.mirror_playlist(
        source="spotify_public",
        source_playlist_id="hash",
        name="Release Radar",
        tracks=[{"track_name": "Song", "artist_name": "Artist"}],
        profile_id=1,
        description="https://open.spotify.com/playlist/abc",
    )

    refreshed_id = db.mirror_playlist(
        source="spotify_public",
        source_playlist_id="hash",
        name="Release Radar",
        tracks=[{"track_name": "New Song", "artist_name": "Artist"}],
        profile_id=1,
    )

    assert refreshed_id == playlist_id
    playlist = db.get_mirrored_playlist(playlist_id)
    assert playlist["description"] == "https://open.spotify.com/playlist/abc"


def test_mirror_playlist_refresh_preserves_library_v2_track_link(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    playlist_id = db.mirror_playlist(
        source="spotify",
        source_playlist_id="linked",
        name="Linked",
        tracks=[{
            "track_name": "Song",
            "artist_name": "Artist",
            "source_track_id": "track-1",
        }],
        profile_id=1,
    )
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE mirrored_playlist_tracks SET lib2_track_id=42 WHERE playlist_id=?",
            (playlist_id,),
        )
        conn.commit()

    db.mirror_playlist(
        source="spotify",
        source_playlist_id="linked",
        name="Linked",
        tracks=[{
            "track_name": "Song (refreshed)",
            "artist_name": "Artist",
            "source_track_id": "track-1",
        }],
        profile_id=1,
    )

    rows = db.get_mirrored_playlist_tracks(playlist_id)
    assert rows[0]["lib2_track_id"] == 42


def test_new_playlist_profile_conflict_removes_stale_wishlist_row(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as conn:
        from core.library2.schema import ensure_library_v2_schema

        ensure_library_v2_schema(conn)
        artist_id = conn.execute(
            "INSERT INTO lib2_artists(name, sort_name) VALUES('Artist', 'Artist')"
        ).lastrowid
        album_id = conn.execute(
            "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')",
            (artist_id,),
        ).lastrowid
        track_id = conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, spotify_id) "
            "VALUES(?, 'Song', 'sp-track')",
            (album_id,),
        ).lastrowid
        conn.commit()
    playlist_ids = [
        db.mirror_playlist(
            source="spotify",
            source_playlist_id=f"quality-{index}",
            name=f"Quality {index}",
            tracks=[{
                "track_name": "Song",
                "artist_name": "Artist",
                "source_track_id": "sp-track",
            }],
            profile_id=1,
        )
        for index in (1, 2)
    ]
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE mirrored_playlist_tracks SET lib2_track_id=?",
            (track_id,),
        )
        conn.commit()
    assert db.set_mirrored_playlist_quality_profile(playlist_ids[0], 1) is True
    assert db.add_to_wishlist({
        "id": "sp-track",
        "name": "Song",
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album"},
    }) is True

    assert db.set_mirrored_playlist_quality_profile(playlist_ids[1], 2) is True

    with db._get_connection() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM wishlist_tracks WHERE spotify_track_id='sp-track'"
        ).fetchone()[0]
    assert remaining == 0


def test_file_import_tracks_get_a_stable_source_track_id(tmp_path):
    # #901: file-import tracks arrive with no source_track_id; mirror_playlist must
    # assign a deterministic one so a Find & Add manual match can key on it (and so
    # discovery extra_data survives a re-import).
    db = MusicDatabase(str(tmp_path / "music.db"))
    file_tracks = [
        {"track_name": "Slow Ride", "artist_name": "Foghat", "album_name": "Fool for the City"},
        {"track_name": "I Gotta Feeling", "artist_name": "The Black Eyed Peas"},
    ]
    pid = db.mirror_playlist(source="file", source_playlist_id="myfile", name="From File",
                             tracks=file_tracks, profile_id=1)
    rows = db.get_mirrored_playlist_tracks(pid)
    ids = [r["source_track_id"] for r in rows]
    assert all(i and i.startswith("file:") for i in ids)      # no empty ids
    assert len(set(ids)) == 2                                  # distinct per song

    # Re-import the SAME file → SAME ids (stable), so a recorded match still keys.
    db.mirror_playlist(source="file", source_playlist_id="myfile", name="From File",
                       tracks=list(file_tracks), profile_id=1)
    rows2 = db.get_mirrored_playlist_tracks(pid)
    assert [r["source_track_id"] for r in rows2] == ids


def test_native_ids_still_used_verbatim(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    pid = db.mirror_playlist(source="spotify", source_playlist_id="sp", name="Sp",
                             tracks=[{"track_name": "S", "artist_name": "A", "source_track_id": "spotify123"}],
                             profile_id=1)
    rows = db.get_mirrored_playlist_tracks(pid)
    assert rows[0]["source_track_id"] == "spotify123"         # native id untouched


def test_backfill_fills_existing_empty_ids_idempotently(tmp_path):
    # #901 backfill: a file-import playlist mirrored BEFORE the fix has empty-id rows.
    # The backfill assigns the SAME stable ids a fresh import would, so existing
    # Find & Add matches start working without a re-import.
    db = MusicDatabase(str(tmp_path / "music.db"))
    pid = db.mirror_playlist(source="file", source_playlist_id="old", name="Old",
                             tracks=[{"track_name": "Slow Ride", "artist_name": "Foghat"}], profile_id=1)
    # simulate a pre-fix row: blank out the id
    with db._get_connection() as conn:
        conn.execute("UPDATE mirrored_playlist_tracks SET source_track_id = '' WHERE playlist_id = ?", (pid,))
        conn.commit()

    n = db._backfill_mirrored_track_source_ids()
    assert n == 1
    rows = db.get_mirrored_playlist_tracks(pid)
    from core.playlists.source_refs import stable_source_track_id
    assert rows[0]["source_track_id"] == stable_source_track_id(
        {"track_name": "Slow Ride", "artist_name": "Foghat"})   # same id a fresh import gives

    # idempotent — second run touches nothing
    assert db._backfill_mirrored_track_source_ids() == 0


def test_backfill_leaves_native_ids_untouched(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    pid = db.mirror_playlist(source="spotify", source_playlist_id="sp", name="Sp",
                             tracks=[{"track_name": "S", "artist_name": "A", "source_track_id": "spotify123"}],
                             profile_id=1)
    db._backfill_mirrored_track_source_ids()
    rows = db.get_mirrored_playlist_tracks(pid)
    assert rows[0]["source_track_id"] == "spotify123"


# ── #990: accept the Spotify shape + reject all-empty (silent 21k-empty-rows bug) ──
def test_mirror_accepts_spotify_shaped_tracks(tmp_path):
    """The GET playlist endpoints return Spotify-shaped tracks; feeding them straight
    back must map cleanly instead of storing empty rows."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    spotify_tracks = [{
        "name": "Because of You", "artists": [{"name": "Ne-Yo"}],
        "album": {"name": "Because of You"}, "id": "sp_track_1", "duration_ms": 217000,
    }]
    pid = db.mirror_playlist(source="spotify", source_playlist_id="liked", name="Liked",
                             tracks=spotify_tracks, profile_id=1)
    rows = db.get_mirrored_playlist_tracks(pid)
    assert rows[0]["track_name"] == "Because of You"
    assert rows[0]["artist_name"] == "Ne-Yo"
    assert rows[0]["album_name"] == "Because of You"
    assert rows[0]["source_track_id"] == "sp_track_1"
    assert rows[0]["duration_ms"] == 217000


def test_mirror_rejects_all_empty_payload_and_preserves_existing(tmp_path):
    """A wrong-shaped payload where every track maps to empty must be rejected —
    and must NOT wipe the existing mirror (the reported 21k-row disaster)."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    pid = db.mirror_playlist(source="spotify", source_playlist_id="liked", name="Liked",
                             tracks=[{"track_name": "Real Song", "artist_name": "A", "source_track_id": "x1"}],
                             profile_id=1)
    with pytest.raises(ValueError):
        db.mirror_playlist(source="spotify", source_playlist_id="liked", name="Liked",
                           tracks=[{"duration_ms": 1000}, {"duration_ms": 2000}], profile_id=1)
    rows = db.get_mirrored_playlist_tracks(pid)          # existing mirror untouched
    assert len(rows) == 1 and rows[0]["track_name"] == "Real Song"


def test_coalesce_mirror_track_shapes():
    from core.playlists.source_refs import coalesce_mirror_track
    sp = coalesce_mirror_track({"name": "T", "artists": [{"name": "A"}],
                                "album": {"name": "Al"}, "id": 7, "duration_ms": 5})
    assert (sp["track_name"], sp["artist_name"], sp["album_name"], sp["source_track_id"]) == ("T", "A", "Al", "7")
    assert sp["duration_ms"] == 5                          # non-mapped keys preserved
    m = {"track_name": "X", "artist_name": "Y", "album_name": "Z", "source_track_id": "id1"}
    assert coalesce_mirror_track(m) == m                   # mirror shape untouched
    s = coalesce_mirror_track({"name": "N", "artist": "Solo", "album": "AlbumStr"})
    assert s["artist_name"] == "Solo" and s["album_name"] == "AlbumStr"
