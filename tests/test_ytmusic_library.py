"""Seam tests for the YouTube Music ACCOUNT-library reads.

``core.ytmusic_library`` answers "what playlists does the signed-in
account have?" — a different question from ``core.youtube_music_meta``
(which reads a single playlist by id/URL, anonymous or authenticated).
These pin the projection of ``get_library_playlists()`` rows and the
Liked Music virtual-row lookup, and — as with every other best-effort
ytmusicapi seam in this codebase — that every failure mode returns
``None``/``[]`` rather than raising.

Fixtures are trimmed copies of real ytmusicapi 1.12 responses (verified
against its ``mixins/library.py`` and ``parsers/browsing.py`` source).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from core.youtube_music_meta import LIKED_MUSIC_ID
from core.ytmusic_library import (
    fetch_liked_music_row,
    fetch_library_playlists,
    library_playlist_to_row,
    library_playlists_to_rows,
    ytmusic_playlist_url,
)

LIBRARY_ROW = {
    "playlistId": "PLQwVIlKxHM6rz0fDJVv_0UlXGEWf-bFys",
    "title": "Road Trip",
    "thumbnails": [{"url": "https://example.com/small.jpg"}, {"url": "https://example.com/large.jpg"}],
    "count": "42",
    "owned": True,
}


# ── URL helper ───────────────────────────────────────────────────────────


def test_ytmusic_playlist_url_form():
    assert ytmusic_playlist_url("PL123") == "https://music.youtube.com/playlist?list=PL123"
    assert ytmusic_playlist_url("LM") == "https://music.youtube.com/playlist?list=LM"


# ── library_playlist_to_row / library_playlists_to_rows ─────────────────


def test_library_row_projects_all_fields():
    row = library_playlist_to_row(LIBRARY_ROW)
    assert row["id"] == "PLQwVIlKxHM6rz0fDJVv_0UlXGEWf-bFys"
    assert row["name"] == "Road Trip"
    assert row["track_count"] == 42
    assert row["image_url"] == "https://example.com/large.jpg"
    assert row["description"] is None
    assert row["owner"] is None


def test_library_row_requires_id_and_title():
    assert library_playlist_to_row({**LIBRARY_ROW, "playlistId": ""}) is None
    assert library_playlist_to_row({**LIBRARY_ROW, "title": ""}) is None
    assert library_playlist_to_row("junk") is None


def test_library_row_missing_count_defaults_to_zero():
    row = library_playlist_to_row({**LIBRARY_ROW, "count": None})
    assert row["track_count"] == 0
    row = library_playlist_to_row({k: v for k, v in LIBRARY_ROW.items() if k != "count"})
    assert row["track_count"] == 0


def test_library_row_author_shapes():
    # list of {name, id} dicts — ytmusicapi's parse_artists_runs shape
    row = library_playlist_to_row({**LIBRARY_ROW, "author": [{"name": "Some User", "id": "UC123"}]})
    assert row["owner"] == "Some User"
    # bare string
    row = library_playlist_to_row({**LIBRARY_ROW, "author": "Some User"})
    assert row["owner"] == "Some User"
    # absent
    row = library_playlist_to_row({**LIBRARY_ROW})
    assert row["owner"] is None


def test_library_playlists_to_rows_filters_unusable_and_handles_none():
    rows = library_playlists_to_rows([LIBRARY_ROW, {"playlistId": "", "title": "no id"}, "junk"])
    assert len(rows) == 1
    assert rows[0]["id"] == LIBRARY_ROW["playlistId"]
    assert library_playlists_to_rows(None) == []


def test_library_row_drops_the_liked_music_auto_playlist_entry():
    # Some accounts surface Liked Music as an "Auto playlist" row in the
    # regular library grid, with no usable count. fetch_liked_music_row
    # already builds the correct row for this id from its own header lookup;
    # letting this one through too produces two "Liked Music" cards sharing
    # one id.
    auto_playlist_row = {
        "playlistId": LIKED_MUSIC_ID,
        "title": "Liked Music",
        "description": "Auto playlist",
        "owned": True,
        # deliberately no "count" — this is the observed real shape
    }
    assert library_playlist_to_row(auto_playlist_row) is None
    assert library_playlists_to_rows([LIBRARY_ROW, auto_playlist_row]) == [
        library_playlist_to_row(LIBRARY_ROW)
    ]
    assert library_playlists_to_rows([]) == []


# ── fetch_library_playlists ──────────────────────────────────────────────


def _install_fake_ytmusic(monkeypatch, *, library_impl=None, playlist_impl=None):
    class _FakeYTMusic:
        def __init__(self, *_a, **_k):
            pass

        def get_library_playlists(self, limit=None):
            return library_impl(limit) if library_impl else []

        def get_playlist(self, playlist_id, limit=100):
            return playlist_impl(playlist_id, limit) if playlist_impl else {}

    monkeypatch.setitem(sys.modules, "ytmusicapi", SimpleNamespace(YTMusic=_FakeYTMusic))


def test_fetch_library_playlists_requires_auth():
    assert fetch_library_playlists(None) is None
    assert fetch_library_playlists({}) is None


def test_fetch_library_playlists_missing_dependency_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "ytmusicapi", None)
    assert fetch_library_playlists({"Cookie": "x"}) is None


def test_fetch_library_playlists_exception_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *_a, **_k):
            raise RuntimeError("inner-tube down")

    monkeypatch.setitem(sys.modules, "ytmusicapi", SimpleNamespace(YTMusic=_Boom))
    assert fetch_library_playlists({"Cookie": "x"}) is None


def test_fetch_library_playlists_returns_raw_rows(monkeypatch):
    _install_fake_ytmusic(monkeypatch, library_impl=lambda limit: [LIBRARY_ROW])
    raw = fetch_library_playlists({"Cookie": "x"})
    assert raw == [LIBRARY_ROW]


# ── fetch_liked_music_row ────────────────────────────────────────────────


def test_fetch_liked_music_row_requires_auth():
    assert fetch_liked_music_row(None) is None
    assert fetch_liked_music_row({}) is None


def test_fetch_liked_music_row_missing_dependency_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "ytmusicapi", None)
    assert fetch_liked_music_row({"Cookie": "x"}) is None


def test_fetch_liked_music_row_exception_returns_none(monkeypatch):
    _install_fake_ytmusic(monkeypatch, playlist_impl=lambda pid, limit: (_ for _ in ()).throw(RuntimeError("boom")))
    assert fetch_liked_music_row({"Cookie": "x"}) is None


def test_fetch_liked_music_row_empty_is_none(monkeypatch):
    # Nothing liked yet — don't show a ghost card.
    _install_fake_ytmusic(monkeypatch, playlist_impl=lambda pid, limit: {"title": "Liked Music", "trackCount": 0})
    assert fetch_liked_music_row({"Cookie": "x"}) is None


def test_fetch_liked_music_row_projects_header(monkeypatch):
    def _playlist(pid, limit):
        assert pid == "LM"
        assert limit == 0  # header-only fetch, no track paging
        return {
            "title": "Liked Music",
            "trackCount": 128,
            "thumbnails": [{"url": "https://example.com/liked.jpg"}],
        }

    _install_fake_ytmusic(monkeypatch, playlist_impl=_playlist)
    row = fetch_liked_music_row({"Cookie": "x"})
    assert row == {
        "id": "LM",
        "name": "Liked Music",
        "track_count": 128,
        "image_url": "https://example.com/liked.jpg",
        "description": None,
        "owner": "You",
    }
