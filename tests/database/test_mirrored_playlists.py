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
