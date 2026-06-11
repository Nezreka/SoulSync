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
    to_mirror_track_dict,
)
from core.playlists.sources.deezer import DeezerPlaylistSource
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
    # First artist only — matches the mirrored_playlist shape that the
    # legacy refresh_mirrored handler wrote (``t.artists[0]``).
    assert t.artist_name == "A"
    assert t.album_name == "Album"
    assert t.duration_ms == 180_000
    assert t.needs_discovery is False
    # Spotify authenticated API path emits matched_data so the discovery
    # worker can skip its search step and go straight to enrichment.
    assert t.extra["discovered"] is True
    assert t.extra["provider"] == "spotify"
    assert t.extra["matched_data"]["id"] == "t1"
    assert t.extra["matched_data"]["artists"] == [{"name": "A"}, {"name": "B"}]
    assert t.extra["matched_data"]["album"]["name"] == "Album"
    assert t.extra["matched_data"]["album"]["images"][0]["url"] == "img"


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
        self.refresh_playlist_calls: list[str] = []
        # Toggle to raise from refresh_playlist for the silent-swallow test.
        self.refresh_raises: Optional[Exception] = None

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
        # Pre-fix fallback — kept so adapters that haven't been
        # migrated still work, and so an accidental return to the
        # legacy entry-point is detectable in tests.
        self.refresh_called = True

    def refresh_playlist(self, mbid: str):
        self.refresh_playlist_calls.append(mbid)
        if self.refresh_raises is not None:
            raise self.refresh_raises
        return {"success": True, "result": "skipped", "playlist_mbid": mbid}


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


def test_listenbrainz_adapter_refresh_uses_targeted_manager_call():
    """Adapter must call ``manager.refresh_playlist(mbid)`` — the
    targeted single-playlist refresh — not the legacy
    ``update_all_playlists`` which re-pulls every cached LB row.
    """
    manager = _FakeLBManager()
    src = ListenBrainzPlaylistSource(lambda: manager)
    detail = src.refresh_playlist("lb-1")

    assert manager.refresh_playlist_calls == ["lb-1"]
    # Legacy entry-point must NOT be touched.
    assert manager.refresh_called is False
    # Refresh returned a detail (read-back via get_playlist).
    assert detail is not None
    assert detail.meta.source_playlist_id == "lb-1"


def test_listenbrainz_adapter_refresh_logs_and_returns_none_on_manager_error():
    """When the LB manager raises, the adapter MUST surface the
    failure as ``None`` (so the outer handler logs + counts it),
    not silently swallow and return a stale cache read.

    Pre-fix: ``except Exception: pass`` then ``return get_playlist()``
    — masking every LB API failure as a successful no-op refresh.
    """
    manager = _FakeLBManager()
    manager.refresh_raises = RuntimeError("LB API timed out")
    src = ListenBrainzPlaylistSource(lambda: manager)

    result = src.refresh_playlist("lb-1")

    assert result is None
    assert manager.refresh_playlist_calls == ["lb-1"]


def test_listenbrainz_adapter_refresh_resolves_synthetic_series_id():
    """Rolling-series synthetic ids (``lb_weekly_jams_<user>``) must
    resolve to the latest cached member MBID before calling the
    targeted manager refresh. Without resolution the manager would
    try to fetch the synthetic id as a real MBID and 404."""
    manager = _FakeLBManager()
    # Re-shape the fake's rows so the title matches a series LIKE pattern.
    manager._rows["created_for_user"] = [{
        "playlist_mbid": "weekly-mbid",
        "title": "Weekly Jams for nezreka, week of 2026-05-25 Mon",
        "creator": "ListenBrainz",
        "track_count": 1,
        "annotation": {},
        "last_updated": "2026-05-26",
    }]
    manager._tracks = {"weekly-mbid": []}
    # Stub the manager DB connection used by the resolution helper.
    import sqlite3
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE listenbrainz_playlists "
        "(playlist_mbid TEXT, title TEXT, profile_id INTEGER, last_updated TEXT)"
    )
    cur.execute(
        "INSERT INTO listenbrainz_playlists VALUES "
        "('weekly-mbid', 'Weekly Jams for nezreka, week of 2026-05-25 Mon', 1, '2026-05-26')"
    )
    conn.commit()
    manager.profile_id = 1
    manager._get_db_connection = lambda: conn

    src = ListenBrainzPlaylistSource(lambda: manager)
    src.refresh_playlist("lb_weekly_jams_nezreka")

    # Manager refresh got called with the RESOLVED real MBID, not the
    # synthetic one.
    assert manager.refresh_playlist_calls == ["weekly-mbid"]


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


# ─── Deezer ─────────────────────────────────────────────────────────────


class _FakeDeezerClient:
    def is_authenticated(self) -> bool:
        return True  # Deezer public API always available

    def get_user_playlists(self):
        return []  # stub-interface variant returns []

    def get_playlist(self, playlist_id: str):
        return {
            "id": playlist_id,
            "name": "Deez Mix",
            "description": "deezer",
            "track_count": 1,
            "image_url": "img",
            "owner": "user",
            "tracks": [{
                "id": "dt1",
                "name": "Song",
                "artists": ["Deez Artist"],
                "album": "Deez Album",
                "duration_ms": 240_000,
                "track_number": 1,
            }],
        }


def test_deezer_adapter_projection():
    src = DeezerPlaylistSource(lambda: _FakeDeezerClient())
    assert src.is_authenticated() is True
    assert src.list_playlists() == []  # user playlists need OAuth

    detail = src.get_playlist("d1")
    assert detail is not None
    assert detail.meta.source == "deezer"
    assert detail.meta.image_url == "img"
    t = detail.tracks[0]
    assert t.source_track_id == "dt1"
    assert t.artist_name == "Deez Artist"
    assert t.album_name == "Deez Album"
    assert t.needs_discovery is False


# ─── to_mirror_track_dict projection helper ─────────────────────────────


def test_mirror_dict_minimal_track_has_no_extra_data():
    track = NormalizedTrack(
        position=0,
        track_name="Song",
        artist_name="Artist",
        album_name="Album",
        duration_ms=200_000,
        source_track_id="abc",
    )
    d = to_mirror_track_dict(track)
    assert d == {
        "track_name": "Song",
        "artist_name": "Artist",
        "album_name": "Album",
        "duration_ms": 200_000,
        "source_track_id": "abc",
    }
    assert "extra_data" not in d


def test_mirror_dict_spotify_authed_emits_matched_data():
    """The Spotify adapter's authenticated-API path planted
    ``discovered`` + ``matched_data`` in ``extra``; projection must
    serialize them into ``extra_data`` matching the legacy refresh
    handler's shape (pre-extraction)."""
    track = NormalizedTrack(
        position=0,
        track_name="Run",
        artist_name="Adele",
        album_name="25",
        duration_ms=295_000,
        source_track_id="track123",
        extra={
            "discovered": True,
            "provider": "spotify",
            "confidence": 1.0,
            "matched_data": {
                "id": "track123",
                "name": "Run",
                "artists": [{"name": "Adele"}],
                "album": {"name": "25"},
                "duration_ms": 295_000,
                "image_url": None,
            },
        },
    )
    d = to_mirror_track_dict(track)
    assert "extra_data" in d
    import json as _json
    extra = _json.loads(d["extra_data"])
    assert extra["discovered"] is True
    assert extra["provider"] == "spotify"
    assert extra["confidence"] == 1.0
    assert extra["matched_data"]["id"] == "track123"
    assert extra["matched_data"]["artists"] == [{"name": "Adele"}]


def test_default_discover_tracks_is_no_op():
    """Adapters whose tracks already carry provider IDs (Spotify,
    Tidal, Qobuz, YouTube, Deezer, Spotify-public, iTunes-link,
    SoulSync-Discovery) inherit the ABC default — return tracks
    unchanged."""
    track = NormalizedTrack(
        position=0,
        track_name="Song",
        artist_name="Artist",
        source_track_id="abc",
        needs_discovery=False,
    )
    src = SpotifyPlaylistSource(lambda: None)
    out = src.discover_tracks([track])
    assert out == [track]


def test_listenbrainz_discover_tracks_uses_callable():
    """When the LB adapter is wired with a discover_callable, MB
    tracks get matched_data populated; ``needs_discovery`` flips to
    False on matches; non-matches stay as-is."""

    def fake_discover(track_dicts):
        # Match the first, leave second unmatched.
        return [
            {
                "id": "matched-1",
                "name": "Matched",
                "artists": ["Artist 1"],
                "album": {"name": "Album"},
                "duration_ms": 200_000,
                "image_url": "art",
                "source": "spotify",
                "_provider": "spotify",
                "_confidence": 0.95,
            },
            None,
        ]

    src = ListenBrainzPlaylistSource(
        lambda: None,
        discover_callable=fake_discover,
    )
    tracks = [
        NormalizedTrack(
            position=0,
            track_name="Song A",
            artist_name="Artist 1",
            source_track_id="mbid-1",
            needs_discovery=True,
        ),
        NormalizedTrack(
            position=1,
            track_name="Song B",
            artist_name="Artist 2",
            source_track_id="mbid-2",
            needs_discovery=True,
        ),
    ]
    out = src.discover_tracks(tracks)
    assert len(out) == 2

    assert out[0].needs_discovery is False
    assert out[0].source_track_id == "matched-1"
    assert out[0].extra["discovered"] is True
    assert out[0].extra["provider"] == "spotify"
    assert out[0].extra["confidence"] == 0.95
    assert out[0].extra["matched_data"]["id"] == "matched-1"

    # Unmatched stays as-is.
    assert out[1].needs_discovery is True
    assert out[1].source_track_id == "mbid-2"
    assert "matched_data" not in (out[1].extra or {})


def test_listenbrainz_discover_tracks_no_callable_is_no_op():
    """If no ``discover_callable`` is wired, the adapter returns the
    list unchanged — refresh paths that haven't enabled discovery
    still work."""
    src = ListenBrainzPlaylistSource(lambda: None, discover_callable=None)
    tracks = [
        NormalizedTrack(
            position=0,
            track_name="T",
            artist_name="A",
            needs_discovery=True,
        )
    ]
    assert src.discover_tracks(tracks) == tracks


def test_lastfm_discover_tracks_shares_listenbrainz_implementation():
    """Last.fm radio tracks have the same MB-metadata shape as LB
    tracks, so the adapter reuses LB's ``discover_tracks``."""

    def fake_discover(track_dicts):
        return [{
            "id": "lfm-matched",
            "name": "Match",
            "artists": ["Artist"],
            "album": {"name": ""},
            "duration_ms": 200_000,
            "image_url": "",
            "source": "spotify",
            "_provider": "spotify",
        }]

    src = LastFMPlaylistSource(lambda: None, discover_callable=fake_discover)
    tracks = [
        NormalizedTrack(
            position=0,
            track_name="T",
            artist_name="A",
            needs_discovery=True,
        )
    ]
    out = src.discover_tracks(tracks)
    assert out[0].needs_discovery is False
    assert out[0].source_track_id == "lfm-matched"
    assert out[0].extra["matched_data"]["id"] == "lfm-matched"


def test_mirror_dict_spotify_public_emits_spotify_hint():
    """Public-embed path: track ID known but album art / canonical
    metadata missing, so we emit a ``spotify_hint`` for the discovery
    worker instead of marking discovered."""
    track = NormalizedTrack(
        position=0,
        track_name="Song",
        artist_name="Artist",
        duration_ms=200_000,
        source_track_id="sptrk",
        extra={
            "spotify_hint": {
                "id": "sptrk",
                "name": "Song",
                "artists": [{"name": "Artist"}],
            },
        },
    )
    d = to_mirror_track_dict(track)
    import json as _json
    extra = _json.loads(d["extra_data"])
    assert extra["discovered"] is False
    assert extra["spotify_hint"]["id"] == "sptrk"


def test_spotify_public_adapter_paginates_past_100(monkeypatch):
    """#838: auto-sync truncated >100-track playlists to 100 because the adapter
    called the embed scraper (≤100) directly instead of the full-fetch wrapper.
    With the wrapper, a playlist whose full fetch returns 150 tracks keeps all 150."""
    src = SpotifyPublicPlaylistSource()

    monkeypatch.setattr(
        "core.spotify_public_scraper.parse_spotify_url",
        lambda url: {"type": "playlist", "id": "big"},
    )
    full = {
        "id": "big", "type": "playlist", "name": "Big PL", "subtitle": "owner",
        "url": "https://open.spotify.com/playlist/big", "url_hash": "bighash",
        "tracks": [
            {"id": f"t{i}", "name": f"Song {i}", "artists": [{"name": "A"}],
             "duration_ms": 1000, "is_explicit": False, "track_number": i + 1}
            for i in range(150)
        ],
    }
    # The full paginated path succeeds → wrapper returns all 150 (no embed cap).
    monkeypatch.setattr(
        "core.spotify_public_api.fetch_public_playlist_full",
        lambda spotify_id: full,
    )

    detail = src.get_playlist("https://open.spotify.com/playlist/big")
    assert detail is not None
    assert len(detail.tracks) == 150, "pre-#838 the adapter capped this at 100"
    assert detail.meta.track_count == 150
