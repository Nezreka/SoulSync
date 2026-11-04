"""jellyfin names the folders behind its music library; the delete roots use them."""

from core.jellyfin_client import JellyfinClient


def _client(folders, library_id=None):
    client = JellyfinClient.__new__(JellyfinClient)
    client.music_library_id = library_id
    client.ensure_connection = lambda: True
    client._make_request = lambda endpoint, params=None: folders if endpoint == "/Library/VirtualFolders" else None
    return client


_FOLDERS = [
    {"Name": "Music", "ItemId": "m1", "CollectionType": "music", "Locations": ["/media/music"]},
    {"Name": "Lossless", "ItemId": "m2", "CollectionType": "music",
     "Locations": ["/media/flac", "/media/music"]},
    {"Name": "Movies", "ItemId": "v1", "CollectionType": "movies", "Locations": ["/media/movies"]},
]


def test_the_selected_music_library_only():
    assert _client(_FOLDERS, "m2").get_music_library_locations() == ["/media/flac", "/media/music"]


def test_every_music_library_without_a_selection_and_never_video():
    assert _client(_FOLDERS).get_music_library_locations() == ["/media/music", "/media/flac"]


def test_no_answer_is_no_folders():
    assert _client(None).get_music_library_locations() == []
    client = _client(_FOLDERS)
    client.ensure_connection = lambda: False
    assert client.get_music_library_locations() == []
