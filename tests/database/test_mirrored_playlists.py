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
