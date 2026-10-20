import pytest
from database.music_database import MusicDatabase


def test_request_list_db_operations(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))

    # Create a regular mirrored playlist
    pid = db.mirror_playlist(
        source="spotify",
        source_playlist_id="sp_playlist_123",
        name="Friend's Mix",
        tracks=[
            {
                "track_name": "Song A",
                "artist_name": "Artist 1",
                "source_track_id": "sp_track_1",
            },
            {
                "track_name": "Song B",
                "artist_name": "Artist 2",
                "source_track_id": "sp_track_2",
            },
        ],
        profile_id=1,
    )
    assert pid is not None

    pl = db.get_mirrored_playlist(pid)
    assert pl["is_request_list"] is False
    assert pl["auto_remove_fulfilled"] is False

    # Copy to request list creates a new distinct request list
    copy_id = db.copy_to_request_list(pid, name="Copied Request List")
    assert copy_id is not None
    assert copy_id != pid

    copy_pl = db.get_mirrored_playlist(copy_id)
    assert copy_pl["name"] == "Copied Request List"
    assert copy_pl["is_request_list"] is True
    # a copy never deletes from the user's spotify playlist until they opt in
    assert copy_pl["auto_remove_fulfilled"] is False
    assert copy_pl["upstream_playlist_id"] == "sp_playlist_123"

    # Test filtering in get_mirrored_playlists
    all_pls = db.get_mirrored_playlists(profile_id=1)
    req_pls = db.get_mirrored_playlists(profile_id=1, is_request_list=True)
    non_req_pls = db.get_mirrored_playlists(profile_id=1, is_request_list=False)

    assert len(all_pls) == 2
    assert len(req_pls) == 1
    assert req_pls[0]["id"] == copy_id
    assert len(non_req_pls) == 1
    assert non_req_pls[0]["id"] == pid

    copy_tracks = db.get_mirrored_playlist_tracks(copy_id)
    assert len(copy_tracks) == 2
    assert copy_tracks[0]["track_name"] == "Song A"
    assert copy_tracks[1]["track_name"] == "Song B"

    # Toggle auto_remove_fulfilled
    db.set_mirrored_playlist_auto_remove(copy_id, auto_remove_fulfilled=True)
    copy_pl = db.get_mirrored_playlist(copy_id)
    assert copy_pl["auto_remove_fulfilled"] is True
    db.set_mirrored_playlist_auto_remove(copy_id, auto_remove_fulfilled=False)
    copy_pl = db.get_mirrored_playlist(copy_id)
    assert copy_pl["auto_remove_fulfilled"] is False
