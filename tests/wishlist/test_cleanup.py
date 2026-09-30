from types import SimpleNamespace

import pytest

from core.wishlist import processing


class _FakeLogger:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []

    def info(self, msg):
        self.info_messages.append(msg)

    def warning(self, msg):
        self.warning_messages.append(msg)

    def error(self, msg):
        self.error_messages.append(msg)


class _FakeWishlistService:
    def __init__(self, tracks):
        self._tracks = list(tracks)
        self.removed_ids = set()

    def get_wishlist_tracks_for_download(self, profile_id=1):
        return [
            track
            for track in self._tracks
            if (track.get("spotify_track_id") or track.get("id")) not in self.removed_ids
        ]

    def mark_track_download_result(self, spotify_track_id, success, error_message=None, profile_id=1, **kwargs):
        self.removed_ids.add(spotify_track_id)
        return True


class _FakeMusicDatabase:
    def __init__(self, owned_matches=None):
        self.owned_matches = set(owned_matches or [])
        self.track_checks = []

    def check_track_exists(self, track_name, artist_name, confidence_threshold=0.7, server_source=None, album=None):
        self.track_checks.append((track_name, artist_name, server_source, album))
        if (track_name, artist_name) in self.owned_matches:
            return SimpleNamespace(id='db-track', title=track_name,
                                   artist_name=artist_name, album_title=album,
                                   file_path='/music/owned.flac'), 0.9
        return None, 0.0


def test_cleanup_wishlist_against_library_removes_owned_tracks():
    service = _FakeWishlistService(
        [
            {
                "id": "track-1",
                "name": "Song A",
                "artists": [{"name": "Artist A"}],
                "album": {"name": "Album A", "album_type": "album"},
            },
            {
                "id": "track-2",
                "name": "Song B",
                "artists": [{"name": "Artist B"}],
                "album": {"name": "Album B", "album_type": "album"},
            },
        ]
    )
    db = _FakeMusicDatabase(owned_matches={("Song A", "Artist A")})
    logger = _FakeLogger()

    payload, status = processing.cleanup_wishlist_against_library(
        service,
        db,
        1,
        "navidrome",
        logger=logger,
    )

    assert status == 200
    assert payload["success"] is True
    assert payload["removed_count"] == 1
    assert payload["processed_count"] == 2
    assert service.removed_ids == {"track-1"}
    assert any("Completed cleanup: 1 tracks removed" in msg for msg in logger.info_messages)


def test_cleanup_wishlist_against_library_handles_empty_wishlist():
    service = _FakeWishlistService([])
    db = _FakeMusicDatabase()
    logger = _FakeLogger()

    payload, status = processing.cleanup_wishlist_against_library(
        service,
        db,
        1,
        "navidrome",
        logger=logger,
    )

    assert status == 200
    assert payload == {"success": True, "message": "No tracks in wishlist to clean up", "removed_count": 0}


@pytest.mark.parametrize(('owned_title', 'owned_album', 'removed'), [
    ('The Sun Maid', 'Grave Dancers Union', False),
    ('Runaway Train', 'Another Release', False),
    ('Runaway Train', 'Grave Dancers Union', True),
])
def test_processing_cleanup_checks_identity_and_accepts_edition_noise(owned_title, owned_album, removed):
    service = _FakeWishlistService([{
        'id': 'wish', 'name': 'Runaway Train (2022 Remaster)',
        'artists': ['Soul Asylum'], 'album': 'Grave Dancers Union (2022 Remaster)',
        'source_type': 'album',
    }])
    owned = SimpleNamespace(title=owned_title, album_title=owned_album, artist_name='Soul Asylum',
                            file_path='/music/owned.flac')
    db = _FakeMusicDatabase()
    db.check_track_exists = lambda *a, **kw: (owned, 0.99)

    payload, status = processing.cleanup_wishlist_against_library(
        service, db, 1, 'navidrome', logger=_FakeLogger())

    assert status == 200
    assert payload['removed_count'] == int(removed)
    assert service.removed_ids == ({'wish'} if removed else set())
