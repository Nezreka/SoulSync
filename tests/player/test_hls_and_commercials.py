"""Unit tests for VOD HLS Cast playlist generation and Retro Commercials engine."""

from __future__ import annotations

from core.player import commercials, hls_cast, settings


def test_vod_hls_playlist_exact_duration_and_endlist():
    m3u8 = hls_cast.render_vod_playlist(15.5, query_string="ct=abc123", seg_sec=6)
    lines = [ln for ln in m3u8.strip().splitlines() if ln]
    assert lines[0] == "#EXTM3U"
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in lines
    assert lines[-1] == "#EXT-X-ENDLIST"
    extinfs = [ln for ln in lines if ln.startswith("#EXTINF:")]
    assert len(extinfs) == 3
    assert extinfs[0] == "#EXTINF:6.000,"
    assert extinfs[1] == "#EXTINF:6.000,"
    assert extinfs[2] == "#EXTINF:3.500,"
    segs = [ln for ln in lines if ln.startswith("seg/")]
    assert segs == ["seg/0.ts?ct=abc123", "seg/1.ts?ct=abc123", "seg/2.ts?ct=abc123"]


def test_commercial_era_inference():
    assert commercials.infer_era("decade-1980", "80s Movies") == "80s"
    assert commercials.infer_era("decade-1990", "90s TV") == "90s"
    assert commercials.infer_era("kids-cartoons", "Saturday Morning") == "kids"
    assert commercials.infer_era("genre-horror", "Late Night Chills") == "horror"
    assert commercials.infer_era("type-movies", "All Movies") == "mix"


def test_commercial_settings_defaults_and_validation():
    defs = settings.defaults()
    assert defs["commercials_enabled"] is False
    assert defs["commercials_era"] == "auto"
    assert defs["commercials_mode"] == "short"
    assert settings._commercial_era("90S") == "90s"
    assert settings._commercial_mode("CABLE") == "cable"


def test_cast_hls_bypasses_session_gate():
    from core.security.session_profile import requires_profile
    assert requires_profile('/api/player/cast-hls/youtube/abc123/index.m3u8', 'GET') is False
    assert requires_profile('/api/player/cast-hls/youtube/abc123/seg/0.ts', 'GET') is False
    assert requires_profile('/api/player/cast-hls/youtube/abc123/index.m3u8', 'OPTIONS') is False
