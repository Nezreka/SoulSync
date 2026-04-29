"""Tests for HLS-related methods on ``HiFiClient``:

- ``_parse_hls_playlist(text, playlist_url)`` — parses m3u8 playlists
- ``_demux_flac(input_path, output_path)`` — ffmpeg demuxing error paths

The parsing logic is identical in ``tidal_download_client.py``; these tests
cover the shared behavior via ``hifi_client.HiFiClient``.
"""

import shutil
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


# ── stubs for module-level imports ────────────────────────────────────────

if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")
    spotipy.Spotify = object
    oauth2 = types.ModuleType("spotipy.oauth2")
    oauth2.SpotifyOAuth = object
    oauth2.SpotifyClientCredentials = object
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
            return "primary"

    settings_mod.config_manager = _DummyConfigManager()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["config.settings"] = settings_mod


from core.hifi_client import HiFiClient  # noqa: E402


# ── fixture ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    """HiFiClient with a temp download dir and no DB dependency."""
    return HiFiClient(download_path=str(tmp_path / "downloads"))


# ── _parse_hls_playlist: master playlist ─────────────────────────────────

MASTER_PLAYLIST = """\
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-STREAM-INF:BANDWIDTH=256000,CODECS="mp4a.40.2"
stream/low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=512000,CODECS="mp4a.40.5"
stream/high.m3u8
"""


def test_parse_master_playlist_returns_variant_uri(client):
    init, segments = client._parse_hls_playlist(
        MASTER_PLAYLIST, "https://cdn.example.com/master.m3u8"
    )
    assert init is None
    assert segments == ["https://cdn.example.com/stream/low.m3u8"]


def test_parse_master_playlist_picks_first_variant(client):
    """Master playlists should return only the first variant URI."""
    init, segments = client._parse_hls_playlist(
        MASTER_PLAYLIST, "https://cdn.example.com/master.m3u8"
    )
    assert len(segments) == 1


def test_parse_master_playlist_resolves_relative_uri(client):
    init, segments = client._parse_hls_playlist(
        MASTER_PLAYLIST, "https://cdn.example.com/playlists/master.m3u8"
    )
    assert segments[0] == "https://cdn.example.com/playlists/stream/low.m3u8"


# ── _parse_hls_playlist: variant playlist with init segment ──────────────

VARIANT_WITH_INIT = """\
#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:4
#EXT-X-MAP:URI="init.mp4"
#EXTINF:3.840000,
seg001.m4s
#EXTINF:3.840000,
seg002.m4s
#EXTINF:2.560000,
seg003.m4s
"""


def test_parse_variant_with_init_returns_init_and_segments(client):
    init, segments = client._parse_hls_playlist(
        VARIANT_WITH_INIT, "https://cdn.example.com/variant.m3u8"
    )
    assert init == "https://cdn.example.com/init.mp4"
    assert segments == [
        "https://cdn.example.com/seg001.m4s",
        "https://cdn.example.com/seg002.m4s",
        "https://cdn.example.com/seg003.m4s",
    ]


def test_parse_variant_with_init_resolves_relative_uris(client):
    init, segments = client._parse_hls_playlist(
        VARIANT_WITH_INIT, "https://cdn.example.com/audio/variant.m3u8"
    )
    assert init == "https://cdn.example.com/audio/init.mp4"
    assert segments[0] == "https://cdn.example.com/audio/seg001.m4s"


def test_parse_variant_with_absolute_init_uri(client):
    playlist = """\
#EXTM3U
#EXT-X-MAP:URI="https://other.cdn/init.mp4"
#EXTINF:3.0,
https://cdn.example.com/seg001.m4s
"""
    init, segments = client._parse_hls_playlist(
        playlist, "https://cdn.example.com/variant.m3u8"
    )
    assert init == "https://other.cdn/init.mp4"
    assert segments == ["https://cdn.example.com/seg001.m4s"]


# ── _parse_hls_playlist: variant playlist without init segment ───────────

VARIANT_NO_INIT = """\
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:9.984000,
seg001.ts
#EXTINF:9.984000,
seg002.ts
#EXT-X-ENDLIST
"""


def test_parse_variant_without_init_returns_none_init(client):
    init, segments = client._parse_hls_playlist(
        VARIANT_NO_INIT, "https://cdn.example.com/variant.m3u8"
    )
    assert init is None
    assert segments == [
        "https://cdn.example.com/seg001.ts",
        "https://cdn.example.com/seg002.ts",
    ]


# ── _parse_hls_playlist: error cases ─────────────────────────────────────


def test_parse_empty_playlist_raises_value_error(client):
    with pytest.raises(ValueError, match="No segment URIs"):
        client._parse_hls_playlist("#EXTM3U", "https://cdn.example.com/x.m3u8")


def test_parse_only_tags_raises_value_error(client):
    """Playlist with only header and MAP tag but no segment URIs."""
    playlist = """\
#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MAP:URI="init.mp4"
"""
    with pytest.raises(ValueError, match="No segment URIs"):
        client._parse_hls_playlist(playlist, "https://cdn.example.com/x.m3u8")


def test_parse_only_extm3u_raises_value_error(client):
    with pytest.raises(ValueError, match="No segment URIs"):
        client._parse_hls_playlist("#EXTM3U\n", "https://cdn.example.com/x.m3u8")


# ── _parse_hls_playlist: edge cases ──────────────────────────────────────


def test_parse_skips_blank_lines(client):
    playlist = """\
#EXTM3U

#EXT-X-MAP:URI="init.mp4"

#EXTINF:3.0,

seg001.m4s

"""
    init, segments = client._parse_hls_playlist(
        playlist, "https://cdn.example.com/x.m3u8"
    )
    assert init == "https://cdn.example.com/init.mp4"
    assert segments == ["https://cdn.example.com/seg001.m4s"]


def test_parse_skips_unknown_tags(client):
    """Tags like #EXT-X-VERSION, #EXT-X-TARGETDURATION should be ignored."""
    playlist = """\
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-PLAYLIST-TYPE:VOD
#EXTINF:5.0,
seg001.ts
"""
    init, segments = client._parse_hls_playlist(
        playlist, "https://cdn.example.com/x.m3u8"
    )
    assert init is None
    assert segments == ["https://cdn.example.com/seg001.ts"]


def test_parse_captures_last_map_tag(client):
    """If multiple EXT-X-MAP tags appear, the last one wins (overwrites init_uri)."""
    playlist = """\
#EXTM3U
#EXT-X-MAP:URI="init-first.mp4"
#EXTINF:3.0,
seg001.m4s
#EXT-X-MAP:URI="init-second.mp4"
#EXTINF:3.0,
seg002.m4s
"""
    init, segments = client._parse_hls_playlist(
        playlist, "https://cdn.example.com/x.m3u8"
    )
    assert init == "https://cdn.example.com/init-second.mp4"
    assert len(segments) == 2


def test_parse_master_breaks_on_first_variant(client):
    """Parser should stop after finding the first variant URI."""
    playlist = """\
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=256000
variant-low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=512000
variant-high.m3u8
#EXTINF:3.0,
should-not-appear.ts
"""
    init, segments = client._parse_hls_playlist(
        playlist, "https://cdn.example.com/master.m3u8"
    )
    assert init is None
    assert segments == ["https://cdn.example.com/variant-low.m3u8"]


def test_parse_master_skips_comment_after_stream_inf(client):
    """The line immediately after #EXT-X-STREAM-INF must be a non-comment URI."""
    playlist = """\
#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=256000
# some comment
variant.m3u8
"""
    init, segments = client._parse_hls_playlist(
        playlist, "https://cdn.example.com/master.m3u8"
    )
    assert segments == ["https://cdn.example.com/variant.m3u8"]


def test_parse_handles_mixed_absolute_and_relative_uris(client):
    playlist = """\
#EXTM3U
#EXTINF:3.0,
https://cdn-a.example.com/seg001.m4s
#EXTINF:3.0,
seg002.m4s
#EXTINF:3.0,
https://cdn-b.example.com/seg003.m4s
"""
    init, segments = client._parse_hls_playlist(
        playlist, "https://cdn.example.com/variant.m3u8"
    )
    assert segments == [
        "https://cdn-a.example.com/seg001.m4s",
        "https://cdn.example.com/seg002.m4s",
        "https://cdn-b.example.com/seg003.m4s",
    ]


def test_parse_single_segment(client):
    playlist = """\
#EXTM3U
#EXTINF:3.0,
only-segment.m4s
"""
    init, segments = client._parse_hls_playlist(
        playlist, "https://cdn.example.com/x.m3u8"
    )
    assert init is None
    assert segments == ["https://cdn.example.com/only-segment.m4s"]


# ── _demux_flac: error paths ─────────────────────────────────────────────


def test_demux_flac_raises_when_ffmpeg_not_found(client, tmp_path, monkeypatch):
    """When ffmpeg is nowhere on PATH and not in tools/, RuntimeError is raised."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    inp = tmp_path / "input.mp4"
    inp.touch()
    out = tmp_path / "output.flac"

    with patch.object(Path, "exists", return_value=False):
        with pytest.raises(RuntimeError, match="ffmpeg is required"):
            client._demux_flac(inp, out)


def test_demux_flac_raises_on_ffmpeg_failure(client, tmp_path, monkeypatch):
    """When ffmpeg exits non-zero, RuntimeError includes stderr."""
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")

    inp = tmp_path / "input.mp4"
    inp.touch()
    out = tmp_path / "output.flac"

    fake_result = subprocess.CalledProcessError(
        returncode=1,
        cmd=["ffmpeg"],
        stderr="Invalid data found when processing input",
    )

    with patch("subprocess.run", side_effect=fake_result):
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            client._demux_flac(inp, out)


def test_demux_flac_uses_tools_dir_fallback(client, tmp_path, monkeypatch):
    """When shutil.which fails but tools/ffmpeg exists, it should be used."""
    monkeypatch.setattr("shutil.which", lambda _: None)

    inp = tmp_path / "input.mp4"
    inp.touch()
    out = tmp_path / "output.flac"

    tools_dir = Path(__file__).parent.parent / "tools"

    original_exists = Path.exists
    original_which = shutil.which

    def fake_exists(self):
        if str(self) == str(tools_dir / "ffmpeg"):
            return True
        return original_exists(self)

    def fake_which(name):
        if name == "ffmpeg":
            return None
        return original_which(name)

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(Path, "exists", fake_exists)

    fake_result = subprocess.CalledProcessError(
        returncode=1, cmd=["ffmpeg"], stderr="fail"
    )
    with patch("subprocess.run", side_effect=fake_result) as mock_run:
        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            client._demux_flac(inp, out)

    call_args = mock_run.call_args[0][0]
    assert call_args[0] == str(tools_dir / "ffmpeg")
