"""Seam tests: the video side resolves its server (Plex/Jellyfin) INDEPENDENTLY
of the music 'active server' pointer — so music-only servers never apply and a
mixed setup (Navidrome music + Plex video) works."""

from __future__ import annotations

import config.settings as cs
import pytest

from core.video.sources import (resolve_video_server, video_plex_config,
                                 video_jellyfin_config)
from database.video_database import VideoDatabase


def _set_cm(monkeypatch, plex, jelly, active):
    class CM:
        def get_plex_config(self): return {"base_url": "http://p", "token": "t"} if plex else {}
        def get_jellyfin_config(self): return {"base_url": "http://j", "api_key": "k"} if jelly else {}
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


# ── Effective connection config: video's OWN creds, or inherited from music ──

def test_plex_config_inherits_music_when_unset(monkeypatch, vdb):
    _set_cm(monkeypatch, True, False, "plex")
    cfg = video_plex_config(vdb)
    assert cfg["base_url"] == "http://p" and cfg["token"] == "t"
    assert cfg["source"] == "music"                      # inherited, read-only


def test_plex_config_own_creds_override_music(monkeypatch, vdb):
    _set_cm(monkeypatch, True, False, "plex")
    vdb.set_setting("video_plex_url", "http://video-plex")
    vdb.set_setting("video_plex_token", "vt")
    cfg = video_plex_config(vdb)
    assert cfg["base_url"] == "http://video-plex" and cfg["token"] == "vt"
    assert cfg["source"] == "video"                      # video's own, not music's


def test_own_creds_do_not_touch_music_config(monkeypatch, vdb):
    # Setting video's own Plex creds must not change what music reports.
    _set_cm(monkeypatch, True, False, "plex")
    vdb.set_setting("video_plex_url", "http://video-plex")
    vdb.set_setting("video_plex_token", "vt")
    assert cs.config_manager.get_plex_config()["base_url"] == "http://p"


def test_video_jellyfin_override_while_music_is_plex(monkeypatch, vdb):
    # The headline scenario: music = Plex, video adds its OWN Jellyfin + picks it.
    _set_cm(monkeypatch, True, False, "plex")
    vdb.set_setting("video_jellyfin_url", "http://video-jelly")
    vdb.set_setting("video_jellyfin_key", "jk")
    jcfg = video_jellyfin_config(vdb)
    assert jcfg["base_url"] == "http://video-jelly" and jcfg["source"] == "video"
    vdb.set_setting("video_server", "jellyfin")
    assert resolve_video_server(vdb) == "jellyfin"       # video on Jellyfin, music still Plex


def test_partial_own_creds_falls_back_to_inherited(monkeypatch, vdb):
    # URL without token isn't a usable override → inherit music's full config.
    _set_cm(monkeypatch, True, False, "plex")
    vdb.set_setting("video_plex_url", "http://video-plex")  # token missing
    cfg = video_plex_config(vdb)
    assert cfg["base_url"] == "http://p" and cfg["source"] == "music"
