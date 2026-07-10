import sys
import types


if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")

    class _DummySpotify:
        def __init__(self, *args, **kwargs):
            pass

    oauth2 = types.ModuleType("spotipy.oauth2")

    class _DummyOAuth:
        def __init__(self, *args, **kwargs):
            pass

    spotipy.Spotify = _DummySpotify
    oauth2.SpotifyOAuth = _DummyOAuth
    oauth2.SpotifyClientCredentials = _DummyOAuth
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "config.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("config.settings")

    class _DummyConfigManager:
        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.metadata import completion as metadata_completion


class _CanonicalCompletionDB:
    def __init__(self, canonical, local_expected=28):
        self.canonical = canonical
        self.local_album = types.SimpleNamespace(id="local-album")
        self.local_expected = local_expected
        self.match_calls = []
        self.completeness_calls = []

    def check_album_exists_with_completeness(self, **kwargs):
        self.match_calls.append(dict(kwargs))
        return self.local_album, 0.95, 28, kwargs["expected_track_count"], False, ["FLAC"]

    def get_album_canonical(self, album_id):
        assert album_id == "local-album"
        return self.canonical

    def check_album_completeness(self, album_id, expected_track_count=None):
        self.completeness_calls.append((album_id, expected_track_count))
        expected = expected_track_count or self.local_expected
        return 28, expected, 28 >= expected, ["FLAC"]


def test_album_completion_uses_exact_canonical_release_count(monkeypatch):
    db = _CanonicalCompletionDB({
        "source": "musicbrainz",
        "album_id": "canonical-28",
    })
    source_calls = []

    def get_tracks(source, album_id):
        source_calls.append((source, album_id))
        return {
            "items": [
                {"id": f"track-{number}"}
                for number in range(1, 29)
            ],
        }

    monkeypatch.setattr(
        metadata_completion,
        "get_album_tracks_for_source",
        get_tracks,
    )

    result = metadata_completion.check_album_completion(
        db,
        {
            "id": "remote-deluxe-93",
            "name": "Mellon Collie and the Infinite Sadness",
            "total_tracks": 93,
        },
        "The Smashing Pumpkins",
    )

    assert db.match_calls[0]["expected_track_count"] == 93
    assert source_calls == [("musicbrainz", "canonical-28")]
    assert db.completeness_calls == [("local-album", 28)]
    assert result["owned_tracks"] == 28
    assert result["expected_tracks"] == 28
    assert result["completion_percentage"] == 100.0
    assert result["status"] == "completed"


def test_album_completion_never_falls_back_to_remote_edition_when_canonical_unavailable(monkeypatch):
    db = _CanonicalCompletionDB({
        "source": "musicbrainz",
        "album_id": "canonical-unavailable",
    })
    source_calls = []

    def get_tracks(source, album_id):
        source_calls.append((source, album_id))
        return None

    monkeypatch.setattr(
        metadata_completion,
        "get_album_tracks_for_source",
        get_tracks,
    )

    result = metadata_completion.check_album_completion(
        db,
        {
            "id": "remote-deluxe-93",
            "name": "Mellon Collie and the Infinite Sadness",
            "total_tracks": 93,
        },
        "The Smashing Pumpkins",
    )

    assert source_calls == [("musicbrainz", "canonical-unavailable")]
    assert db.completeness_calls == [("local-album", None)]
    assert result["owned_tracks"] == 28
    assert result["expected_tracks"] == 28
    assert result["completion_percentage"] == 100.0
    assert result["status"] == "completed"


def test_album_completion_keeps_existing_behavior_without_canonical_pin(monkeypatch):
    db = _CanonicalCompletionDB(None)
    monkeypatch.setattr(
        metadata_completion,
        "get_album_tracks_for_source",
        lambda source, album_id: None,
    )

    result = metadata_completion.check_album_completion(
        db,
        {
            "id": "remote-edition",
            "name": "Album",
            "total_tracks": 12,
        },
        "Artist",
    )

    assert db.completeness_calls == []
    assert result["owned_tracks"] == 28
    assert result["expected_tracks"] == 12
    assert result["status"] == "completed"
