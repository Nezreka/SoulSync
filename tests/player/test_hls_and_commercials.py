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
    assert defs["commercials_mode"] == "cable"
    assert settings._commercial_era("90S") == "90s"
    assert settings._commercial_mode("CABLE") == "cable"


def test_cast_hls_bypasses_session_gate():
    from core.security.session_profile import no_profile_request_is_blocked
    assert no_profile_request_is_blocked('/api/player/cast-hls/youtube/abc123/index.m3u8', 'GET') is False
    assert no_profile_request_is_blocked('/api/player/cast-hls/youtube/abc123/seg/0.ts', 'GET') is False
    assert no_profile_request_is_blocked('/api/player/cast-hls/youtube/abc123/index.m3u8', 'OPTIONS') is False


def test_commercial_year_extraction_and_era():
    assert commercials.extract_year("1986 Nintendo NES Commercial") == 1986
    assert commercials.extract_year("1994-05-12") == 1994
    assert commercials.extract_year("No year here") is None
    assert commercials.era_for_year(1977) == "70s"
    assert commercials.era_for_year(1985) == "80s"
    assert commercials.era_for_year(1996) == "90s"
    assert commercials.era_for_year(2004) == "2000s"
    assert commercials.era_for_year(2024) is None
    assert commercials.MAX_CACHE_BYTES == 500 * 1024 * 1024


# ── cast sessions are cleaned up (a stopped cast never asks again) ───────────

class _FakeProc:
    def __init__(self):
        self.killed = False
        self.returncode = None

    def poll(self):
        return -9 if self.killed else None

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return -9


def _session(tmp_path, key, *, last_access, highest=0):
    out = tmp_path / key
    out.mkdir()
    for i in range(highest + 1):
        (out / f"seg_{i}.ts").write_bytes(b"x")
    s = hls_cast._HlsSession(key=key, start_seg=0, out_dir=str(out), proc=_FakeProc(),
                             err_path=out / "ffmpeg.log", last_access=last_access,
                             highest_written=highest)
    return s


def test_an_idle_cast_is_stopped_and_its_folder_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(hls_cast, "_sessions", {})
    idle = _session(tmp_path, "idle", last_access=0.0)
    live = _session(tmp_path, "live", last_access=1000.0)
    hls_cast._sessions.update({"idle": idle, "live": live})
    hls_cast.reap_once(now=1000.0 + 1)
    assert list(hls_cast._sessions) == ["live"]
    assert idle.proc.killed and not (tmp_path / "idle").exists()
    assert not live.proc.killed


def test_a_running_cast_keeps_only_segments_near_where_it_plays(tmp_path, monkeypatch):
    monkeypatch.setattr(hls_cast, "_sessions", {})
    s = _session(tmp_path, "movie", last_access=1000.0, highest=100)
    hls_cast._sessions["movie"] = s
    hls_cast.reap_once(now=1000.0)
    left = sorted(int(p.stem.split("_")[1]) for p in (tmp_path / "movie").glob("seg_*.ts"))
    assert left[0] == 100 - hls_cast.KEEP_BEHIND_SEGMENTS
    assert left[-1] == 100


def test_reusing_a_session_does_not_evict_other_casts(tmp_path, monkeypatch):
    monkeypatch.setattr(hls_cast, "_sessions", {})
    for k in ("a", "b", "c"):
        hls_cast._sessions[k] = _session(tmp_path, k, last_access=1000.0, highest=2)
    with hls_cast._lock:
        hls_cast._prune_locked(1001.0)
    assert sorted(hls_cast._sessions) == ["a", "b", "c"]
