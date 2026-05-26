"""Adapter contract tests for ``core/playlists/sources/``.

These pin the projection from each backing client's native shape into
the unified ``PlaylistMeta`` / ``NormalizedTrack`` shape. Adapters are
fed minimal fakes (not real clients) so the test is independent of the
live API surface — the goal is to lock in the field mapping so later
phases that consume the unified interface can rely on it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, List, Optional

import pytest

from core.playlists.sources import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    PlaylistSourceRegistry,
    get_registry,
)
from core.playlists.sources.itunes_link import ITunesLinkPlaylistSource
from core.playlists.sources.lastfm import LastFMPlaylistSource
from core.playlists.sources.listenbrainz import ListenBrainzPlaylistSource
from core.playlists.sources.qobuz import QobuzPlaylistSource
from core.playlists.sources.soulsync_discovery import SoulSyncDiscoveryPlaylistSource
from core.playlists.sources.spotify import SpotifyPlaylistSource
from core.playlists.sources.spotify_public import SpotifyPublicPlaylistSource
from core.playlists.sources.tidal import TidalPlaylistSource
from core.playlists.sources.youtube import YouTubePlaylistSource


# ─── Spotify ────────────────────────────────────────────────────────────


class _FakeSpotifyClient:
    """Stand-in for ``core.spotify_client.SpotifyClient``."""

    def __init__(self, authed: bool = True):
        self._authed = authed

    def is_authenticated(self) -> bool:
        return self._authed

    def get_user_playlists_metadata_only(self):
        return [
            SimpleNamespace(
                id="pl1",
                name="Drive",
                description="long drives",
                owner="me",
                public=True,
                collaborative=False,
                tracks=[],
                total_tracks=2,
            )
        ]

    def get_playlist_by_id(self, playlist_id: str):
        track = SimpleNamespace(
            id="t1",
            name="Run",
            artists=["A", "B"],
            album="Album",
            duration_ms=180_000,
            popularity=50,
            preview_url=None,
            external_urls={"spotify": "url"},
            image_url="img",
        )
        return SimpleNamespace(
            id=playlist_id,
            name="Drive",
            description="long drives",
            owner="me",
            public=True,
            collaborative=False,
            tracks=[track],
            total_tracks=1,
        )


def test_spotify_adapter_lists_and_fetches():
    client = _FakeSpotifyClient()
    src = SpotifyPlaylistSource(lambda: client)

    assert isinstance(src, PlaylistSource)
    assert src.is_authenticated() is True

    metas = src.list_playlists()
    assert len(metas) == 1
    m = metas[0]
    assert m.source == "spotify"
    assert m.source_playlist_id == "pl1"
    assert m.name == "Drive"
    assert m.track_count == 2

    detail = src.get_playlist("pl1")
    assert detail is not None
    assert detail.meta.track_count == 1
    t = detail.tracks[0]
    assert t.source_track_id == "t1"
    assert t.track_name == "Run"
    assert t.artist_name == "A, B"
    assert t.album_name == "Album"
    assert t.duration_ms == 180_000
    assert t.needs_discovery is False


def test_spotify_adapter_handles_unauthed():
    src = SpotifyPlaylistSource(lambda: _FakeSpotifyClient(authed=False))
    assert src.is_authenticated() is False
    assert src.list_playlists() == []
    assert src.get_playlist("pl1") is None


def test_spotify_adapter_handles_missing_client():
    src = SpotifyPlaylistSource(lambda: None)
    assert src.is_authenticated() is False
    assert src.list_playlists() == []


# ─── Tidal ──────────────────────────────────────────────────────────────


class _FakeTidalClient:
    def __init__(self, authed: bool = True):
        self._authed = authed

    def is_authenticated(self) -> bool:
        return self._authed

    def get_user_playlists_metadata_only(self):
        return [
            SimpleNamespace(
                id="tpl",
                name="Tidal Mix",
                description="",
                tracks=[],
                external_urls={"tidal": "url"},
                owner={"name": "broque"},
                public=True,
            )
        ]

    def get_playlist(self, playlist_id: str):
        track = SimpleNamespace(
            id="ttrk",
            name="Wave",
            artists=["X"],
            album="Ocean",
            duration_ms=200_000,
            external_urls={},
            popularity=0,
            explicit=False,
        )
        return SimpleNamespace(
            id=playlist_id,
            name="Tidal Mix",
            description="",
            tracks=[track],
            external_urls={},
            owner={"name": "broque"},
            public=True,
        )


def test_tidal_adapter_projection():
    src = TidalPlaylistSource(lambda: _FakeTidalClient())

    metas = src.list_playlists()
    assert metas[0].owner == "broque"
    assert metas[0].source == "tidal"

    detail = src.get_playlist("tpl")
    assert detail is not None
    assert detail.tracks[0].source_track_id == "ttrk"
    assert detail.tracks[0].album_name == "Ocean"
    assert detail.tracks[0].needs_discovery is False


# ─── Qobuz ──────────────────────────────────────────────────────────────


class _FakeQobuzClient:
    def is_authenticated(self) -> bool:
        return True

    def get_user_playlists(self):
        return [{
            "id": "q1",
            "name": "Q Mix",
            "description": "qobuz",
            "public": False,
            "track_count": 2,
            "image_url": "img",
            "external_urls": {"qobuz": "url"},
        }]

    def get_playlist(self, playlist_id: str):
        return {
            "id": playlist_id,
            "name": "Q Mix",
            "description": "qobuz",
            "public": False,
            "track_count": 1,
            "image_url": "img",
            "external_urls": {"qobuz": "url"},
            "tracks": [{
                "id": "qt1",
                "name": "Track",
                "artists": ["Q-Artist"],
                "album": "Q-Album",
                "duration_ms": 300_000,
                "image_url": "ti",
            }],
        }


def test_qobuz_adapter_projection():
    src = QobuzPlaylistSource(lambda: _FakeQobuzClient())
    metas = src.list_playlists()
    assert metas[0].source_playlist_id == "q1"

    detail = src.get_playlist("q1")
    assert detail.tracks[0].source_track_id == "qt1"
    assert detail.tracks[0].duration_ms == 300_000
    assert detail.tracks[0].image_url == "ti"


# ─── Spotify Public ─────────────────────────────────────────────────────


def test_spotify_public_adapter_invalid_url():
    src = SpotifyPublicPlaylistSource()
    # invalid URL → parser returns None → adapter returns None
    assert src.get_playlist("not-a-spotify-url") is None
    assert src.supports_listing is False
    assert src.list_playlists() == []


def test_spotify_public_adapter_projects_scrape(monkeypatch):
    src = SpotifyPublicPlaylistSource()

    def fake_parse(url: str):
        return {"type": "playlist", "id": "xyz"}

    def fake_scrape(spotify_type: str, spotify_id: str):
        return {
            "id": spotify_id,
            "type": "playlist",
            "name": "Embed",
            "subtitle": "owner",
            "tracks": [
                {
                    "id": "sptrk",
                    "name": "Song",
                    "artists": [{"name": "Artist"}],
                    "duration_ms": 100_000,
                    "is_explicit": False,
                    "track_number": 1,
                },
            ],
            "url": "https://open.spotify.com/playlist/xyz",
            "url_hash": "abc123",
        }

    monkeypatch.setattr("core.spotify_public_scraper.parse_spotify_url", fake_parse)
    monkeypatch.setattr("core.spotify_public_scraper.scrape_spotify_embed", fake_scrape)

    detail = src.get_playlist("https://open.spotify.com/playlist/xyz")
    assert detail is not None
    assert detail.meta.source_playlist_id == "abc123"
    assert detail.meta.source_url == "https://open.spotify.com/playlist/xyz"
    assert detail.tracks[0].artist_name == "Artist"
    assert detail.tracks[0].source_track_id == "sptrk"


# ─── YouTube ────────────────────────────────────────────────────────────


def test_youtube_adapter_projection():
    def parser(url: str):
        return {
            "id": "yt_pl",
            "name": "YT Mix",
            "track_count": 1,
            "url": url,
            "image_url": "thumb",
            "tracks": [{
                "id": "vid1",
                "name": "Track",
                "artists": ["Channel"],
                "duration_ms": 240_000,
                "url": "https://youtu.be/vid1",
            }],
        }

    src = YouTubePlaylistSource(parser)
    detail = src.get_playlist("https://youtube.com/playlist?list=yt_pl")
    assert detail is not None
    assert detail.meta.source == "youtube"
    assert detail.meta.source_url == "https://youtube.com/playlist?list=yt_pl"
    assert len(detail.meta.source_playlist_id) == 12  # md5[:12]
    assert detail.tracks[0].source_track_id == "vid1"


def test_youtube_adapter_failed_parse():
    src = YouTubePlaylistSource(lambda url: None)
    assert src.get_playlist("https://bad") is None


# ─── iTunes Link ────────────────────────────────────────────────────────


def test_itunes_link_adapter_projection():
    def parser(url: str):
        return {
            "id": "1234",
            "type": "album",
            "name": "Album X",
            "subtitle": "Artist",
            "url": url,
            "url_hash": "abcd1234",
            "track_count": 1,
            "image_url": "art",
            "tracks": [{
                "id": "555",
                "name": "Song",
                "artists": ["Artist"],
                "album": {"name": "Album X"},
                "duration_ms": 220_000,
                "image_url": "art",
            }],
        }

    src = ITunesLinkPlaylistSource(parser)
    detail = src.get_playlist("https://music.apple.com/us/album/1234")
    assert detail is not None
    assert detail.meta.source == "itunes_link"
    assert detail.meta.source_playlist_id == "abcd1234"
    assert detail.tracks[0].album_name == "Album X"
    assert detail.tracks[0].source_track_id == "555"


# ─── ListenBrainz ───────────────────────────────────────────────────────


class _FakeLBManager:
    def __init__(self, authed: bool = True):
        self.client = SimpleNamespace(is_authenticated=lambda: authed)
        self._rows = {
            "created_for_user": [{
                "playlist_mbid": "lb-1",
                "title": "Weekly Discovery",
                "creator": "ListenBrainz",
                "track_count": 1,
                "annotation": {"note": "weekly"},
                "last_updated": "2026-05-26",
            }],
            "user_created": [],
            "collaborative": [],
        }
        self._tracks = {
            "lb-1": [{
                "track_name": "Discovery Track",
                "artist_name": "MB Artist",
                "album_name": "MB Album",
                "duration_ms": 250_000,
                "recording_mbid": "rec-1",
                "release_mbid": "rel-1",
                "album_cover_url": "cover",
                "additional_metadata": {},
            }],
        }
        self.refresh_called = False

    def get_cached_playlists(self, playlist_type: str):
        return self._rows.get(playlist_type, [])

    def get_playlist_type(self, mbid: str) -> str:
        for ptype, rows in self._rows.items():
            if any(r["playlist_mbid"] == mbid for r in rows):
                return ptype
        return ""

    def get_cached_tracks(self, mbid: str):
        return self._tracks.get(mbid, [])

    def update_all_playlists(self):
        self.refresh_called = True


def test_listenbrainz_adapter_marks_needs_discovery():
    manager = _FakeLBManager()
    src = ListenBrainzPlaylistSource(lambda: manager)

    metas = src.list_playlists()
    assert len(metas) == 1
    assert metas[0].source == "listenbrainz"
    assert metas[0].extra["playlist_type"] == "created_for_user"

    detail = src.get_playlist("lb-1")
    assert detail is not None
    assert detail.meta.track_count == 1
    t = detail.tracks[0]
    assert t.needs_discovery is True
    assert t.source_track_id == "rec-1"
    assert t.extra["recording_mbid"] == "rec-1"


def test_listenbrainz_adapter_refresh_calls_manager():
    manager = _FakeLBManager()
    src = ListenBrainzPlaylistSource(lambda: manager)
    src.refresh_playlist("lb-1")
    assert manager.refresh_called is True


# ─── Last.fm ────────────────────────────────────────────────────────────


class _FakeLastFMManager:
    def __init__(self):
        self._rows = [{
            "playlist_mbid": "lfm-1",
            "title": "Last.fm Radio: Seed",
            "creator": "Last.fm",
            "track_count": 1,
            "annotation": {"seed": "track"},
            "last_updated": "2026-05-26",
        }]
        self._tracks = [{
            "track_name": "Similar",
            "artist_name": "Artist",
            "album_name": None,
            "duration_ms": 200_000,
            "recording_mbid": "lfm-rec-1",
            "release_mbid": None,
            "album_cover_url": None,
            "additional_metadata": {},
        }]

    def get_cached_playlists(self, playlist_type: str):
        if playlist_type == "lastfm_radio":
            return self._rows
        return []

    def get_cached_tracks(self, mbid: str):
        if mbid == "lfm-1":
            return self._tracks
        return []


def test_lastfm_adapter_projects_radio_rows():
    src = LastFMPlaylistSource(lambda: _FakeLastFMManager())

    metas = src.list_playlists()
    assert len(metas) == 1
    assert metas[0].source == "lastfm"
    assert metas[0].owner == "Last.fm"

    detail = src.get_playlist("lfm-1")
    assert detail is not None
    assert detail.tracks[0].needs_discovery is True
    assert detail.tracks[0].source_track_id == "lfm-rec-1"


# ─── SoulSync Discovery ─────────────────────────────────────────────────


class _FakeDiscoveryManager:
    def __init__(self):
        self.refresh_calls = []
        self._records = [SimpleNamespace(
            id=42,
            profile_id=1,
            kind="hidden_gems",
            variant="",
            name="Hidden Gems",
            config=None,
            track_count=1,
            last_generated_at="2026-05-26T00:00:00Z",
            last_synced_at=None,
            last_generation_source="discovery_pool",
            last_generation_error=None,
            is_stale=False,
        )]
        self._tracks = [SimpleNamespace(
            track_name="Gem",
            artist_name="Indie",
            album_name="EP",
            spotify_track_id="sp-gem",
            itunes_track_id=None,
            deezer_track_id=None,
            album_cover_url="art",
            duration_ms=180_000,
            popularity=20,
            track_data_json=None,
            source="discovery",
            primary_id=lambda: "sp-gem",
        )]

    def list_playlists(self, profile_id: int = 1):
        return self._records

    def get_playlist_tracks(self, playlist_id: int):
        return self._tracks if playlist_id == 42 else []

    def refresh_playlist(self, kind: str, variant: str = "", profile_id: int = 1, config_overrides=None):
        self.refresh_calls.append((kind, variant, profile_id))
        return self._records[0]


def test_soulsync_discovery_adapter_tracks_dont_need_discovery():
    manager = _FakeDiscoveryManager()
    src = SoulSyncDiscoveryPlaylistSource(lambda: manager, profile_id_getter=lambda: 1)

    metas = src.list_playlists()
    assert metas[0].source == "soulsync_discovery"
    assert metas[0].source_playlist_id == "42"
    assert metas[0].extra["kind"] == "hidden_gems"

    detail = src.get_playlist("42")
    assert detail is not None
    t = detail.tracks[0]
    assert t.needs_discovery is False
    assert t.source_track_id == "sp-gem"
    assert t.album_name == "EP"
    assert t.extra["spotify_track_id"] == "sp-gem"


def test_soulsync_discovery_adapter_refresh_invokes_manager():
    manager = _FakeDiscoveryManager()
    src = SoulSyncDiscoveryPlaylistSource(lambda: manager, profile_id_getter=lambda: 1)
    src.refresh_playlist("42")
    assert manager.refresh_calls == [("hidden_gems", "", 1)]


# ─── Registry ───────────────────────────────────────────────────────────


def test_registry_lazy_construct_and_cache():
    reg = PlaylistSourceRegistry()
    constructed = []

    def factory():
        constructed.append(True)
        return SpotifyPlaylistSource(lambda: None)

    reg.register("spotify", factory)
    assert constructed == []  # not built yet

    first = reg.get_source("spotify")
    second = reg.get_source("spotify")
    assert first is second  # cached
    assert len(constructed) == 1


def test_registry_re_register_invalidates_instance():
    reg = PlaylistSourceRegistry()
    reg.register("spotify", lambda: SpotifyPlaylistSource(lambda: None))
    first = reg.get_source("spotify")
    reg.register("spotify", lambda: SpotifyPlaylistSource(lambda: None))
    second = reg.get_source("spotify")
    assert first is not second


def test_registry_unknown_source_returns_none():
    reg = PlaylistSourceRegistry()
    assert reg.get_source("nope") is None
