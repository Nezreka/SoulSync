import sys
import types
from types import SimpleNamespace

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

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod

from core.repair_jobs.unknown_artist_fixer import UnknownArtistFixerJob
import core.repair_jobs.unknown_artist_fixer as unknown_artist_fixer_module


class _FakeClient:
    def __init__(self, track_details=None, search_results=None):
        self.track_details = track_details or {}
        self.search_results = search_results or {}
        self.get_calls = []
        self.search_calls = []

    def get_track_details(self, track_id):
        self.get_calls.append(track_id)
        return self.track_details.get(track_id)

    def search_tracks(self, query, limit=5):
        self.search_calls.append((query, limit))
        return self.search_results.get(query, [])


def _install_tag_reader(monkeypatch, tags=None):
    fake_module = types.ModuleType("core.tag_writer")
    fake_module.read_file_tags = lambda path: tags or {}
    monkeypatch.setitem(sys.modules, "core.tag_writer", fake_module)


def test_unknown_artist_fixer_uses_primary_source_track_id_first(monkeypatch):
    job = UnknownArtistFixerJob()
    _install_tag_reader(monkeypatch)

    deezer_client = _FakeClient(
        track_details={
            "dz-1": {
                "primary_artist": "Deezer Artist",
                "album": {
                    "name": "Deezer Album",
                    "release_date": "2024-02-01",
                    "images": [{"url": "https://img/deezer"}],
                },
                "name": "Deezer Song",
                "track_number": 7,
                "disc_number": 1,
            }
        }
    )
    spotify_client = _FakeClient(
        track_details={
            "sp-1": {
                "primary_artist": "Spotify Artist",
                "album": {
                    "name": "Spotify Album",
                    "release_date": "2023-01-01",
                    "images": [{"url": "https://img/spotify"}],
                },
                "name": "Spotify Song",
                "track_number": 1,
                "disc_number": 1,
            }
        }
    )

    monkeypatch.setattr(unknown_artist_fixer_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(
        unknown_artist_fixer_module,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client}.get(source),
    )

    track = {
        "title": "Unknown Title",
        "album_title": "Unknown Album",
        "spotify_track_id": "sp-1",
        "deezer_id": "dz-1",
        "itunes_track_id": "",
    }

    result = job._resolve_metadata(SimpleNamespace(), track, "/tmp/track.flac")

    assert result["artist"] == "Deezer Artist"
    assert result["album"] == "Deezer Album"
    assert result["source"] == "deezer_track_id_lookup"
    assert deezer_client.get_calls == ["dz-1"]
    assert spotify_client.get_calls == []


def test_unknown_artist_fixer_searches_primary_source_first(monkeypatch):
    job = UnknownArtistFixerJob()
    _install_tag_reader(monkeypatch)

    candidate = SimpleNamespace(
        id="dz-song-1",
        name="Matching Title",
        album="Matching Album",
        artists=["Deezer Artist"],
        image_url="https://img/deezer-search",
    )

    deezer_client = _FakeClient(
        track_details={
            "dz-song-1": {
                "primary_artist": "Deezer Artist",
                "album": {
                    "name": "Matching Album",
                    "release_date": "2024-03-02",
                    "images": [{"url": "https://img/deezer-full"}],
                },
                "name": "Matching Title",
                "track_number": 4,
                "disc_number": 1,
            }
        },
        search_results={"Matching Title": [candidate]},
    )
    spotify_client = _FakeClient(
        search_results={
            "Matching Title": [
                SimpleNamespace(
                    id="sp-song-1",
                    name="Matching Title",
                    album="Matching Album",
                    artists=["Spotify Artist"],
                    image_url="https://img/spotify-search",
                )
            ]
        }
    )

    monkeypatch.setattr(unknown_artist_fixer_module, "get_primary_source", lambda: "deezer")
    monkeypatch.setattr(
        unknown_artist_fixer_module,
        "get_client_for_source",
        lambda source: {"deezer": deezer_client, "spotify": spotify_client}.get(source),
    )

    track = {
        "title": "Matching Title",
        "album_title": "Matching Album",
        "spotify_track_id": "",
        "deezer_id": "",
        "itunes_track_id": "",
    }

    result = job._resolve_metadata(SimpleNamespace(sleep_or_stop=lambda seconds: False), track, "/tmp/track.flac")

    assert result["artist"] == "Deezer Artist"
    assert result["album"] == "Matching Album"
    assert result["source"] == "deezer_title_search"
    assert deezer_client.search_calls == [("Matching Title", 5)]
    assert spotify_client.search_calls == []


def test_unknown_artist_fixer_supports_hydrabase_title_search(monkeypatch):
    job = UnknownArtistFixerJob()
    _install_tag_reader(monkeypatch)

    hydrabase_candidate = SimpleNamespace(
        id="hy-song-1",
        name="Hydra Match",
        album="Hydra Album",
        artists=["Hydra Artist"],
        image_url="https://img/hydra-search",
    )
    hydrabase_client = _FakeClient(
        track_details={
            "hy-song-1": {
                "primary_artist": "Hydra Artist",
                "album": {
                    "name": "Hydra Album",
                    "release_date": "2024-04-03",
                    "images": [{"url": "https://img/hydra-full"}],
                },
                "name": "Hydra Match",
                "track_number": 2,
                "disc_number": 1,
            }
        },
        search_results={"Hydra Match": [hydrabase_candidate]},
    )
    spotify_client = _FakeClient()

    monkeypatch.setattr(unknown_artist_fixer_module, "get_primary_source", lambda: "hydrabase")
    monkeypatch.setattr(
        unknown_artist_fixer_module,
        "get_client_for_source",
        lambda source: {"hydrabase": hydrabase_client, "spotify": spotify_client}.get(source),
    )

    track = {
        "title": "Hydra Match",
        "album_title": "Hydra Album",
        "spotify_track_id": "",
        "deezer_id": "",
        "itunes_track_id": "",
    }

    result = job._resolve_metadata(SimpleNamespace(sleep_or_stop=lambda seconds: False), track, "/tmp/track.flac")

    assert result["artist"] == "Hydra Artist"
    assert result["source"] == "hydrabase_title_search"
    assert hydrabase_client.search_calls == [("Hydra Match", 5)]
    assert spotify_client.search_calls == []


# ---------------------------------------------------------------------------
# Issue #646 — deferred imports inside scan() must resolve at runtime
# ---------------------------------------------------------------------------


def test_deferred_path_imports_resolve():
    """Issue #646 regression guard. The Unknown Artist Fixer's scan()
    defers `get_file_path_from_template_raw` + `get_audio_quality_string`
    imports to keep web_server's heavy boot off the test harness — but
    that means a stale import target only surfaces at *runtime*, mid-
    scan, with an ImportError. The fixer crashed with exactly that:

        ImportError: cannot import name '_build_path_from_template' from
        'core.repair_jobs.library_reorganize'

    after commit ca5c9316 rewrote `library_reorganize` and moved the
    helpers into the import pipeline.

    This test runs the same import statements scan() runs, so the next
    refactor that moves these helpers fails CI rather than reaching the
    user."""
    from core.imports.paths import get_file_path_from_template_raw  # noqa: F401
    from core.imports.file_ops import get_audio_quality_string  # noqa: F401


def test_deferred_path_helper_shape_matches_fixer_usage():
    """Pin the shape contract the fixer relies on: pass a template
    string + a context dict with the same keys scan() builds, expect a
    `(folder, filename_base)` tuple back. If either of those moves, the
    fixer's `folder, fname_base = ...` unpack would fail loudly here
    instead of producing a malformed expected_rel path."""
    from core.imports.paths import get_file_path_from_template_raw

    template = "$albumartist/$albumartist - $album/$track - $title"
    tmpl_ctx = {
        "artist": "Test Artist",
        "albumartist": "Test Artist",
        "album": "Test Album",
        "title": "Test Track",
        "track_number": 1,
        "disc_number": 1,
        "year": "2026",
        "quality": "FLAC 16bit",
        "albumtype": "Album",
    }

    result = get_file_path_from_template_raw(template, tmpl_ctx)

    assert isinstance(result, tuple) and len(result) == 2, \
        "Must return a 2-tuple — fixer does `folder, fname_base = result`"
    folder, fname_base = result
    assert isinstance(folder, str) and isinstance(fname_base, str)
    # Folder path must include the album-artist segment from the template.
    assert "Test Artist" in folder
    # Filename base must include the title from the template.
    assert "Test Track" in fname_base
