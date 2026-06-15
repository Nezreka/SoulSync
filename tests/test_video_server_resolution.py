"""Seam tests: the video side resolves its server (Plex/Jellyfin) INDEPENDENTLY
of the music 'active server' pointer — so music-only servers never apply and a
mixed setup (Navidrome music + Plex video) works."""

from __future__ import annotations

import config.settings as cs
import pytest

from core.video.sources import resolve_video_server
from database.video_database import VideoDatabase


def _set_cm(monkeypatch, plex, jelly, active):
    class CM:
        def get_plex_config(self): return {"base_url": "http://p", "token": "t"} if plex else {}
        def get_jellyfin_config(self): return {"base_url": "http://j"} if jelly else {}
        def get_active_media_server(self): return active
    monkeypatch.setattr(cs, "config_manager", CM())


@pytest.fixture()
def vdb(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


def test_plex_only(monkeypatch, vdb):
    _set_cm(monkeypatch, True, False, "plex")
    assert resolve_video_server(vdb) == "plex"


def test_jellyfin_only(monkeypatch, vdb):
    _set_cm(monkeypatch, False, True, "jellyfin")
    assert resolve_video_server(vdb) == "jellyfin"


def test_none_when_only_a_music_server(monkeypatch, vdb):
    _set_cm(monkeypatch, False, False, "navidrome")
    assert resolve_video_server(vdb) is None


def test_independent_of_music_active(monkeypatch, vdb):
    # Navidrome is the music server, but Plex is configured → video uses Plex.
    _set_cm(monkeypatch, True, False, "navidrome")
    assert resolve_video_server(vdb) == "plex"


def test_both_configured_default_then_explicit_pick(monkeypatch, vdb):
    _set_cm(monkeypatch, True, True, "plex")
    assert resolve_video_server(vdb) == "plex"           # both → Plex default
    vdb.set_setting("video_server", "jellyfin")
    assert resolve_video_server(vdb) == "jellyfin"       # explicit video pick wins


def test_does_not_follow_music_active_server(monkeypatch, vdb):
    # Both configured + music set to Jellyfin, but NO explicit video pick → video
    # stays on Plex. Changing the music server must never change video.
    _set_cm(monkeypatch, True, True, "jellyfin")
    assert resolve_video_server(vdb) == "plex"
    vdb.set_setting("video_server", "jellyfin")          # only an explicit pick switches video
    assert resolve_video_server(vdb) == "jellyfin"
