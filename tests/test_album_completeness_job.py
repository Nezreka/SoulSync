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
        def get(self, key, default=None):
            return default

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.repair_jobs.album_completeness import AlbumCompletenessJob
import core.repair_jobs.album_completeness as album_completeness_module


class _FakeCursor:
    def __init__(self, owned_track_numbers):
        self._owned_track_numbers = owned_track_numbers
        self._last_query = ""

    def execute(self, query, params=None):
        self._last_query = query
        return self

    def fetchall(self):
        if "SELECT track_number" in self._last_query:
            return [(track_number,) for track_number in self._owned_track_numbers]
        return []


class _FakeConnection:
    def __init__(self, owned_track_numbers):
        self._cursor = _FakeCursor(owned_track_numbers)

    def cursor(self):
        return self._cursor

    def close(self):
        return None


class _FakeDB:
    def __init__(self, owned_track_numbers):
        self._owned_track_numbers = owned_track_numbers

    def _get_connection(self):
        return _FakeConnection(self._owned_track_numbers)


class _FakeSpotifyClient:
    def __init__(self, track_count=5):
        self.track_count = track_count
        self.calls = []

    def is_spotify_authenticated(self):
        return True

    def get_album_tracks(self, album_id):
        self.calls.append(album_id)
        return {
            "items": [
                {"id": f"sp-{i}", "name": f"Spotify Track {i}", "track_number": i, "disc_number": 1, "artists": []}
                for i in range(1, self.track_count + 1)
            ]
        }


class _FakeDeezerClient:
    def __init__(self, track_count=2):
        self.track_count = track_count
        self.calls = []

    def is_authenticated(self):
        return True

    def get_album_tracks(self, album_id):
        self.calls.append(album_id)
        return {
            "items": [
                {"id": f"dz-{i}", "name": f"Deezer Track {i}", "track_number": i, "disc_number": 1, "artists": []}
                for i in range(1, self.track_count + 1)
            ]
        }


class _FakeITunesClient:
    def __init__(self):
        self.calls = []

    def is_authenticated(self):
        return True

    def get_album_tracks(self, album_id):
        self.calls.append(album_id)
        return {"items": []}


class _FakeDiscogsClient:
    def __init__(self, track_count=3):
        self.track_count = track_count
        self.calls = []

    def get_album_tracks(self, album_id):
        self.calls.append(album_id)
        return {
            "items": [
                {"id": f"dg-{i}", "name": f"Discogs Track {i}", "track_number": i, "disc_number": 1, "artists": []}
                for i in range(1, self.track_count + 1)
            ]
        }


class _FakeHydrabaseClient:
    def __init__(self, track_count=4):
        self.track_count = track_count
        self.calls = []

    def is_connected(self):
        return True

    def get_album_tracks_dict(self, album_id):
        self.calls.append(album_id)
        return {
            "items": [
                {"id": f"hy-{i}", "name": f"Hydrabase Track {i}", "track_number": i, "disc_number": 1, "artists": []}
                for i in range(1, self.track_count + 1)
            ]
        }


def test_album_completeness_uses_primary_provider_first(monkeypatch):
    job = AlbumCompletenessJob()
    context = types.SimpleNamespace(
        db=_FakeDB(owned_track_numbers={1}),
        spotify_client=_FakeSpotifyClient(track_count=5),
        is_spotify_rate_limited=lambda: False,
    )

    deezer_client = _FakeDeezerClient(track_count=2)
    itunes_client = _FakeITunesClient()
    calls = []
    album_ids = {
        "spotify": "spotify-album",
        "itunes": "itunes-album",
        "deezer": "deezer-album",
        "discogs": "",
        "hydrabase": "",
    }

    monkeypatch.setattr(album_completeness_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        lambda source, album_id: (
            calls.append((source, album_id)) or
            (
                deezer_client.get_album_tracks(album_id) if source == "deezer" else
                itunes_client.get_album_tracks(album_id) if source == "itunes" else
                context.spotify_client.get_album_tracks(album_id) if source == "spotify" else
                {"items": []}
            )
        )
    )

    expected_total = job._get_expected_total(context, "deezer", album_ids)
    missing_tracks = job._find_missing_tracks(context, "deezer", 42, album_ids)

    assert expected_total == 2
    assert calls == [("deezer", "deezer-album"), ("deezer", "deezer-album")]
    assert deezer_client.calls == ["deezer-album", "deezer-album"]
    assert context.spotify_client.calls == []
    assert itunes_client.calls == []

    assert [track["track_number"] for track in missing_tracks] == [2]
    assert missing_tracks[0]["source"] == "deezer"
    assert missing_tracks[0]["source_track_id"] == "dz-2"
    assert missing_tracks[0]["spotify_track_id"] == "dz-2"


def test_album_completeness_supports_discogs_primary(monkeypatch):
    job = AlbumCompletenessJob()
    context = types.SimpleNamespace(
        db=_FakeDB(owned_track_numbers={1}),
        spotify_client=_FakeSpotifyClient(track_count=5),
        is_spotify_rate_limited=lambda: False,
    )

    discogs_client = _FakeDiscogsClient(track_count=3)
    itunes_client = _FakeITunesClient()
    deezer_client = _FakeDeezerClient(track_count=2)
    calls = []
    album_ids = {
        "spotify": "spotify-album",
        "itunes": "itunes-album",
        "deezer": "deezer-album",
        "discogs": "discogs-release",
        "hydrabase": "",
    }

    monkeypatch.setattr(album_completeness_module, "get_primary_source", lambda: "discogs")
    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        lambda source, album_id: (
            calls.append((source, album_id)) or
            (
                discogs_client.get_album_tracks(album_id) if source == "discogs" else
                itunes_client.get_album_tracks(album_id) if source == "itunes" else
                deezer_client.get_album_tracks(album_id) if source == "deezer" else
                context.spotify_client.get_album_tracks(album_id) if source == "spotify" else
                {"items": []}
            )
        )
    )

    expected_total = job._get_expected_total(context, "discogs", album_ids)
    missing_tracks = job._find_missing_tracks(context, "discogs", 42, album_ids)

    assert expected_total == 3
    assert calls == [("discogs", "discogs-release"), ("discogs", "discogs-release")]
    assert discogs_client.calls == ["discogs-release", "discogs-release"]
    assert context.spotify_client.calls == []
    assert itunes_client.calls == []
    assert deezer_client.calls == []

    assert [track["track_number"] for track in missing_tracks] == [2, 3]
    assert missing_tracks[0]["source"] == "discogs"
    assert missing_tracks[0]["source_track_id"] == "dg-2"


def test_album_completeness_supports_hydrabase_primary(monkeypatch):
    job = AlbumCompletenessJob()
    context = types.SimpleNamespace(
        db=_FakeDB(owned_track_numbers={1}),
        spotify_client=_FakeSpotifyClient(track_count=5),
        is_spotify_rate_limited=lambda: False,
    )

    hydrabase_client = _FakeHydrabaseClient(track_count=4)
    itunes_client = _FakeITunesClient()
    deezer_client = _FakeDeezerClient(track_count=2)
    calls = []
    album_ids = {
        "spotify": "spotify-album",
        "itunes": "itunes-album",
        "deezer": "deezer-album",
        "discogs": "",
        "hydrabase": "soul-album",
    }

    monkeypatch.setattr(album_completeness_module, "get_primary_source", lambda: "hydrabase")
    monkeypatch.setattr(
        album_completeness_module,
        "get_album_tracks_for_source",
        lambda source, album_id: (
            calls.append((source, album_id)) or
            (
                hydrabase_client.get_album_tracks_dict(album_id) if source == "hydrabase" else
                itunes_client.get_album_tracks(album_id) if source == "itunes" else
                deezer_client.get_album_tracks(album_id) if source == "deezer" else
                context.spotify_client.get_album_tracks(album_id) if source == "spotify" else
                {"items": []}
            )
        )
    )

    expected_total = job._get_expected_total(context, "hydrabase", album_ids)
    missing_tracks = job._find_missing_tracks(context, "hydrabase", 42, album_ids)

    assert expected_total == 4
    assert calls == [("hydrabase", "soul-album"), ("hydrabase", "soul-album")]
    assert hydrabase_client.calls == ["soul-album", "soul-album"]
    assert context.spotify_client.calls == []
    assert itunes_client.calls == []
    assert deezer_client.calls == []

    assert [track["track_number"] for track in missing_tracks] == [2, 3, 4]
    assert missing_tracks[0]["source"] == "hydrabase"
    assert missing_tracks[0]["source_track_id"] == "hy-2"
