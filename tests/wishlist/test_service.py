from types import SimpleNamespace

from core.wishlist.service import WishlistService


class _FakeWishlistDatabase:
    def __init__(self, tracks=None, count=None):
        self.tracks = list(tracks or [])
        self.count = len(self.tracks) if count is None else count
        self.add_calls = []
        self.track_queries = []

    def add_to_wishlist(self, **kwargs):
        self.add_calls.append(kwargs)
        return True

    def get_wishlist_tracks(self, limit=None, profile_id=1):
        self.track_queries.append(("get_wishlist_tracks", limit, profile_id))
        tracks = list(self.tracks)
        return tracks if limit is None else tracks[:limit]

    def get_wishlist_count(self, profile_id=1):
        self.track_queries.append(("get_wishlist_count", profile_id))
        return self.count


def _build_service(fake_db):
    service = WishlistService(database_path="test.db")
    service._database = fake_db
    return service


def test_add_failed_track_from_modal_normalizes_candidates_and_forwards_payload():
    fake_db = _FakeWishlistDatabase()
    service = _build_service(fake_db)

    track_info = {
        "failure_reason": "Download cancelled",
        "download_index": 4,
        "table_index": 4,
        "candidates": [
            SimpleNamespace(title="Candidate Song", artist="Candidate Artist", filename="track.flac"),
            {"title": "kept"},
        ],
        "spotify_track": {
            "id": "sp-1",
            "name": "Song One",
            "artists": [{"name": "Artist One"}],
            "album": {"name": "Album One"},
        },
    }
    source_context = {"playlist_name": "Playlist One"}

    result = service.add_failed_track_from_modal(
        track_info,
        source_type="playlist",
        source_context=source_context,
        profile_id=3,
    )

    assert result is True
    assert fake_db.add_calls[0]["spotify_track_data"]["id"] == "sp-1"
    assert fake_db.add_calls[0]["failure_reason"] == "Download cancelled"
    assert fake_db.add_calls[0]["source_type"] == "playlist"
    assert fake_db.add_calls[0]["profile_id"] == 3
    assert fake_db.add_calls[0]["source_info"]["playlist_name"] == "Playlist One"
    assert fake_db.add_calls[0]["source_info"]["original_modal_data"] == {
        "download_index": 4,
        "table_index": 4,
        "candidates": [
            {
                "title": "Candidate Song",
                "artist": "Candidate Artist",
                "filename": "track.flac",
            },
            {"title": "kept"},
        ],
    }


def test_add_failed_track_from_modal_returns_false_when_no_spotify_track_found():
    fake_db = _FakeWishlistDatabase()
    service = _build_service(fake_db)

    result = service.add_failed_track_from_modal({"track_info": {}}, profile_id=1)

    assert result is False
    assert fake_db.add_calls == []


def test_add_spotify_track_to_wishlist_accepts_track_data_alias():
    fake_db = _FakeWishlistDatabase()
    service = _build_service(fake_db)

    result = service.add_spotify_track_to_wishlist(
        track_data={
            "id": "sp-1",
            "name": "Song One",
            "artists": [{"name": "Artist One"}],
            "album": {"name": "Album One"},
        },
        failure_reason="Download failed",
        source_type="manual",
        profile_id=2,
    )

    assert result is True
    assert fake_db.add_calls[0]["track_data"]["id"] == "sp-1"
    assert fake_db.add_calls[0]["failure_reason"] == "Download failed"
    assert fake_db.add_calls[0]["source_type"] == "manual"
    assert fake_db.add_calls[0]["profile_id"] == 2


def test_get_wishlist_tracks_for_download_formats_modal_shape():
    fake_db = _FakeWishlistDatabase(
        tracks=[
            {
                "id": "wl-1",
                "spotify_track_id": "sp-1",
                "spotify_data": {
                    "id": "sp-1",
                    "name": "Song One",
                    "artists": [{"name": "Artist One"}],
                    "album": {"name": "Album One"},
                    "duration_ms": 321,
                    "preview_url": "https://example.test/preview",
                    "external_urls": {"spotify": "https://open.spotify.com/track/sp-1"},
                    "popularity": 88,
                    "track_number": 7,
                    "disc_number": 2,
                },
                "failure_reason": "Download failed",
                "retry_count": 2,
                "date_added": "2024-01-01",
                "last_attempted": "2024-01-02",
                "source_type": "playlist",
                "source_info": {"playlist_name": "Playlist One"},
            }
        ]
    )
    service = _build_service(fake_db)

    formatted_tracks = service.get_wishlist_tracks_for_download(limit=1, profile_id=7)

    assert fake_db.track_queries == [("get_wishlist_tracks", 1, 7)]
    assert formatted_tracks == [
        {
            "wishlist_id": "wl-1",
            "track_id": "sp-1",
            "spotify_track_id": "sp-1",
            "track_data": {
                "id": "sp-1",
                "name": "Song One",
                "artists": [{"name": "Artist One"}],
                "album": {"name": "Album One"},
                "duration_ms": 321,
                "preview_url": "https://example.test/preview",
                "external_urls": {"spotify": "https://open.spotify.com/track/sp-1"},
                "popularity": 88,
                "track_number": 7,
                "disc_number": 2,
            },
            "track_name": "Song One",
            "artist_name": "Artist One",
            "album_name": "Album One",
            "provider": None,
            "spotify_data": {
                "id": "sp-1",
                "name": "Song One",
                "artists": [{"name": "Artist One"}],
                "album": {"name": "Album One"},
                "duration_ms": 321,
                "preview_url": "https://example.test/preview",
                "external_urls": {"spotify": "https://open.spotify.com/track/sp-1"},
                "popularity": 88,
                "track_number": 7,
                "disc_number": 2,
            },
            "failure_reason": "Download failed",
            "retry_count": 2,
            "date_added": "2024-01-01",
            "last_attempted": "2024-01-02",
            "source_type": "playlist",
            "source_info": {"playlist_name": "Playlist One"},
            "id": "sp-1",
            "name": "Song One",
            "artists": [{"name": "Artist One"}],
            "album": {"name": "Album One"},
            "duration_ms": 321,
            "preview_url": "https://example.test/preview",
            "external_urls": {"spotify": "https://open.spotify.com/track/sp-1"},
            "popularity": 88,
            "track_number": 7,
            "disc_number": 2,
        }
    ]


def test_check_track_in_wishlist_and_find_matching_wishlist_track_handle_id_variants():
    fake_db = _FakeWishlistDatabase(
        tracks=[
            {
                "id": "wl-1",
                "spotify_track_id": "sp-1",
                "name": "Song One",
                "artists": [{"name": "Artist One"}],
                "spotify_data": {
                    "id": "sp-1",
                    "name": "Song One",
                    "artists": [{"name": "Artist One"}],
                    "album": {"name": "Album One"},
                },
                "failure_reason": "Download failed",
                "retry_count": 1,
                "date_added": "2024-01-01",
                "last_attempted": None,
                "source_type": "playlist",
                "source_info": {},
            },
            {
                "id": "sp-2",
                "spotify_track_id": "sp-2",
                "name": "Song Two",
                "artists": ["Artist Two"],
                "spotify_data": {
                    "id": "sp-2",
                    "name": "Song Two",
                    "artists": [{"name": "Artist Two"}],
                    "album": {"name": "Album Two"},
                },
                "failure_reason": "Download failed",
                "retry_count": 1,
                "date_added": "2024-01-02",
                "last_attempted": None,
                "source_type": "manual",
                "source_info": {},
            },
        ]
    )
    service = _build_service(fake_db)
    formatted_tracks = service.get_wishlist_tracks_for_download()

    assert service.check_track_in_wishlist("sp-1") is True
    assert service.check_track_in_wishlist("sp-2") is True
    assert service.check_track_in_wishlist("missing") is False
    assert service.find_matching_wishlist_track("Song One", "Artist One") == formatted_tracks[0]
    assert service.find_matching_wishlist_track("Song Two", "Artist Two") == formatted_tracks[1]
    assert service.find_matching_wishlist_track("Missing", "Nobody") is None


def test_get_wishlist_summary_returns_empty_summary_when_count_is_zero():
    fake_db = _FakeWishlistDatabase(tracks=[], count=0)
    service = _build_service(fake_db)

    assert service.get_wishlist_summary(profile_id=2) == {
        "total_tracks": 0,
        "by_source_type": {},
        "recent_failures": [],
    }


def test_get_wishlist_summary_groups_by_source_type_and_limits_recent_failures():
    fake_db = _FakeWishlistDatabase(
        tracks=[
            {
                "source_type": "playlist",
                "failure_reason": "f1",
                "retry_count": 1,
                "date_added": "2024-01-01",
                "spotify_data": {"name": "Song A", "artists": [{"name": "Artist A"}]},
            },
            {
                "source_type": "playlist",
                "failure_reason": "f2",
                "retry_count": 2,
                "date_added": "2024-01-02",
                "spotify_data": {"name": "Song B", "artists": ["Artist B"]},
            },
            {
                "source_type": "album",
                "failure_reason": "f3",
                "retry_count": 3,
                "date_added": "2024-01-03",
                "spotify_data": {"name": "Song C", "artists": [{"name": "Artist C"}]},
            },
            {
                "source_type": "manual",
                "failure_reason": "f4",
                "retry_count": 4,
                "date_added": "2024-01-04",
                "spotify_data": {"name": "Song D", "artists": [{"name": "Artist D"}]},
            },
            {
                "source_type": "manual",
                "failure_reason": "f5",
                "retry_count": 5,
                "date_added": "2024-01-05",
                "spotify_data": {"name": "Song E", "artists": [{"name": "Artist E"}]},
            },
            {
                "source_type": "manual",
                "failure_reason": "f6",
                "retry_count": 6,
                "date_added": "2024-01-06",
                "spotify_data": {"name": "Song F", "artists": [{"name": "Artist F"}]},
            },
        ]
    )
    service = _build_service(fake_db)

    summary = service.get_wishlist_summary(profile_id=4)

    assert summary["total_tracks"] == 6
    assert summary["by_source_type"] == {"playlist": 2, "album": 1, "manual": 3}
    assert len(summary["recent_failures"]) == 5
    assert summary["recent_failures"][0] == {
        "name": "Song A",
        "artist": "Artist A",
        "failure_reason": "f1",
        "retry_count": 1,
        "date_added": "2024-01-01",
    }
    assert summary["recent_failures"][1]["artist"] == "Artist B"
    assert summary["recent_failures"][-1]["name"] == "Song E"
