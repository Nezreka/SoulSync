from types import SimpleNamespace

from core.wishlist import resolution


class _FakeWishlistService:
    def __init__(self, tracks):
        self.tracks = tracks
        self.removed = []

    def get_wishlist_tracks_for_download(self, profile_id=1):
        return list(self.tracks)

    def mark_track_download_result(self, spotify_track_id, success, error_message=None, profile_id=1):
        self.removed.append((spotify_track_id, success, error_message, profile_id))
        return True


def test_check_and_remove_from_wishlist_uses_search_result_fallback():
    fake_db = SimpleNamespace(get_all_profiles=lambda: [{"id": 1}])
    wishlist_service = _FakeWishlistService(
        [
            {
                "wishlist_id": 11,
                "spotify_track_id": "sp-track-1",
                "id": "sp-track-1",
                "name": "Song One",
                "artists": [{"name": "Artist One"}],
            }
        ]
    )

    context = {
        "search_result": {
            "title": "Song One",
            "artist": "Artist One",
            "album": "Album One",
        },
        "track_info": {},
        "original_search_result": {},
    }

    resolution.check_and_remove_from_wishlist(
        context,
        wishlist_service=wishlist_service,
        database=fake_db,
    )

    assert wishlist_service.removed == [("sp-track-1", True, None, 1)]


def test_check_and_remove_from_wishlist_uses_spotify_source_id():
    fake_db = SimpleNamespace(get_all_profiles=lambda: [{"id": 1}])
    wishlist_service = _FakeWishlistService(
        [
            {
                "wishlist_id": 11,
                "spotify_track_id": "sp-track-1",
                "id": "sp-track-1",
                "name": "Song One",
                "artists": [{"name": "Artist One"}],
            }
        ]
    )

    context = {
        "source": "spotify",
        "track_info": {
            "id": "sp-track-1",
            "name": "Song One",
            "artists": [{"name": "Artist One"}],
        },
        "search_result": {},
        "original_search_result": {},
    }

    resolution.check_and_remove_from_wishlist(
        context,
        wishlist_service=wishlist_service,
        database=fake_db,
    )

    assert wishlist_service.removed == [("sp-track-1", True, None, 1)]


def test_check_and_remove_from_wishlist_uses_wishlist_id_lookup():
    fake_db = SimpleNamespace(get_all_profiles=lambda: [{"id": 1}])
    wishlist_service = _FakeWishlistService(
        [
            {
                "wishlist_id": 22,
                "spotify_track_id": "sp-track-2",
                "id": "sp-track-2",
                "name": "Song Two",
                "artists": [{"name": "Artist Two"}],
            }
        ]
    )

    context = {
        "source": "manual",
        "track_info": {"wishlist_id": 22},
        "search_result": {},
        "original_search_result": {},
    }

    resolution.check_and_remove_from_wishlist(
        context,
        wishlist_service=wishlist_service,
        database=fake_db,
    )

    assert wishlist_service.removed == [("sp-track-2", True, None, 1)]


def test_check_and_remove_track_from_wishlist_by_metadata_uses_fuzzy_match():
    fake_db = SimpleNamespace(get_all_profiles=lambda: [{"id": 1}])
    wishlist_service = _FakeWishlistService(
        [
            {
                "wishlist_id": 22,
                "spotify_track_id": "sp-track-2",
                "id": "sp-track-2",
                "name": "Song Two",
                "artists": [{"name": "Artist Two"}],
            }
        ]
    )

    track_data = {
        "name": "Song Two",
        "id": "",
        "artists": [{"name": "Artist Two"}],
    }

    removed = resolution.check_and_remove_track_from_wishlist_by_metadata(
        track_data,
        wishlist_service=wishlist_service,
        database=fake_db,
    )

    assert removed is True
    assert wishlist_service.removed == [("sp-track-2", True, None, 1)]


def test_check_and_remove_track_from_wishlist_by_metadata_uses_direct_id_match():
    fake_db = SimpleNamespace(get_all_profiles=lambda: [{"id": 1}])
    wishlist_service = _FakeWishlistService([])

    track_data = {
        "name": "Song Three",
        "id": "sp-track-3",
        "artists": [{"name": "Artist Three"}],
    }

    removed = resolution.check_and_remove_track_from_wishlist_by_metadata(
        track_data,
        wishlist_service=wishlist_service,
        database=fake_db,
    )

    assert removed is True
    assert wishlist_service.removed == [("sp-track-3", True, None, 1)]


def test_check_and_remove_track_from_wishlist_by_metadata_returns_false_when_no_match():
    fake_db = SimpleNamespace(get_all_profiles=lambda: [{"id": 1}])
    wishlist_service = _FakeWishlistService([])

    removed = resolution.check_and_remove_track_from_wishlist_by_metadata(
        {
            "name": "Missing Song",
            "id": "",
            "artists": [{"name": "Missing Artist"}],
        },
        wishlist_service=wishlist_service,
        database=fake_db,
    )

    assert removed is False
    assert wishlist_service.removed == []
