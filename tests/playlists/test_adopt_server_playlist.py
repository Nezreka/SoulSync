"""adopting a server playlist soulsync didn't make: it becomes a My Playlist
under the same name, and every track is pinned to its server track, so the
first sync can't take a track the matcher misses off the server playlist."""

import pytest

from core.playlists import user_playlists as up
from core.playlists.adopt_server_playlist import AdoptError, adopt_server_playlist
from database.music_database import MusicDatabase


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _track(sid, title, artist="Yes", album="Fragile", duration=300000):
    return {"id": sid, "title": title, "artist": artist, "album": album, "duration": duration}


BANDS = [
    _track("nd-1", "Roundabout"),
    _track("nd-2", "Cans and Brahms"),
    # a classical title the matcher could easily miss
    _track("nd-3", "12 Concerti grossi, Op. 8: VII. Allegro", artist="Giuseppe Torelli",
           album="Torelli: 12 Concerti Grossi"),
    _track("nd-1", "Roundabout"),   # the same song twice on the server
]


def test_it_becomes_a_my_playlist_in_server_order(mdb):
    result = adopt_server_playlist(mdb, 1, "navidrome", "Bands", BANDS)
    playlist = mdb.get_mirrored_playlist(result["id"], profile_id=1)
    assert up.is_user_playlist(playlist)
    assert result["name"] == "Bands"   # the same name, so a sync updates this playlist
    titles = [t["track_name"] for t in mdb.get_mirrored_playlist_tracks(result["id"])]
    assert titles == ["Roundabout", "Cans and Brahms",
                      "12 Concerti grossi, Op. 8: VII. Allegro", "Roundabout"]
    assert result["track_count"] == 4


def test_every_track_is_pinned_to_its_server_track(mdb):
    result = adopt_server_playlist(mdb, 1, "navidrome", "Bands", BANDS)
    rows = mdb.get_mirrored_playlist_tracks(result["id"])
    pinned = {r["track_name"]: mdb.read_sync_match_cache(r["source_track_id"], "navidrome")
              for r in rows}
    assert pinned["Roundabout"]["server_track_id"] == "nd-1"
    assert pinned["12 Concerti grossi, Op. 8: VII. Allegro"]["server_track_id"] == "nd-3"
    # confidence 1.0 is the manual-match marker the sync trusts before any matching
    assert all(p["confidence"] == 1.0 for p in pinned.values())
    assert result["pinned"] == 4


def test_a_read_only_playlist_is_the_servers(mdb):
    # a navidrome smart playlist (opensubsonic readonly) or a plex smart one
    with pytest.raises(AdoptError, match="read-only"):
        adopt_server_playlist(mdb, 1, "navidrome", "Missing genre", BANDS, readonly=True)
    assert not mdb.get_mirrored_playlists(profile_id=1)


def test_a_name_already_in_soulsync_is_refused(mdb):
    up.create_playlist(mdb, 1, "Bands")
    with pytest.raises(AdoptError, match="already have a playlist"):
        adopt_server_playlist(mdb, 1, "navidrome", "bands", BANDS)


def test_tracks_without_an_artist_are_skipped_and_counted(mdb):
    result = adopt_server_playlist(mdb, 1, "navidrome", "Bands",
                                   [BANDS[0], _track("nd-9", "Untitled", artist="")])
    assert (result["track_count"], result["skipped"]) == (1, 1)


def test_an_empty_playlist_is_refused(mdb):
    with pytest.raises(AdoptError, match="no tracks"):
        adopt_server_playlist(mdb, 1, "navidrome", "Empty", [])
    assert not mdb.get_mirrored_playlists(profile_id=1)


def test_navidrome_says_which_playlists_are_read_only():
    # opensubsonic `readonly`: a smart playlist, or one this user can't edit
    from core.navidrome_client import NavidromeClient

    client = NavidromeClient.__new__(NavidromeClient)
    client.ensure_connection = lambda: True
    client._make_request = lambda *a, **k: {"playlists": {"playlist": [
        {"id": "1", "name": "Bands", "songCount": 4, "duration": 900},
        {"id": "2", "name": "Missing genre", "songCount": 80, "duration": 9000, "readonly": True},
    ]}}
    flags = {p.title: p.readonly for p in client.get_all_playlists()}
    assert flags == {"Bands": False, "Missing genre": True}
