"""HiFi sometimes serves a PREVIEW manifest (~30s of segments) for a full-length
track, which slipped through the old 100KB-only size floor. The duration guards
catch it: a preview manifest is way shorter than the real track length."""

from __future__ import annotations

from core.hifi_client import sum_hls_segment_seconds, is_short_audio


_FULL = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MAP:URI="init.mp4"
#EXTINF:10.0,
seg0.mp4
#EXTINF:10.0,
seg1.mp4
#EXTINF:9.5,
seg2.mp4
#EXT-X-ENDLIST
"""

_PREVIEW = """#EXTM3U
#EXTINF:15.0,
p0.mp4
#EXTINF:15.0,
p1.mp4
#EXT-X-ENDLIST
"""


def test_sums_extinf_segment_durations():
    assert sum_hls_segment_seconds(_FULL) == 29.5      # 10 + 10 + 9.5
    assert sum_hls_segment_seconds(_PREVIEW) == 30.0    # the preview's true length


def test_no_extinf_is_unknown_zero():
    assert sum_hls_segment_seconds("#EXTM3U\nseg.mp4\n") == 0.0
    assert sum_hls_segment_seconds("") == 0.0


def test_preview_is_flagged_short_against_full_track():
    # Save Your Tears ~215s; a 30s preview manifest is obviously short
    assert is_short_audio(30.0, 215.0) is True


def test_full_length_download_is_not_flagged():
    assert is_short_audio(213.0, 215.0) is False        # ~1% trim → fine
    assert is_short_audio(215.0, 215.0) is False


def test_unknown_durations_never_reject():
    assert is_short_audio(0, 215) is False              # couldn't probe → don't reject
    assert is_short_audio(30, 0) is False               # expected unknown → don't reject
    assert is_short_audio(0, 0) is False


def test_legitimately_short_track_is_kept():
    # a real 40s interlude: actual ≈ expected → not a preview
    assert is_short_audio(40.0, 41.0) is False


def test_threshold_boundary():
    assert is_short_audio(79, 100) is True              # below 80%
    assert is_short_audio(85, 100) is False             # above 80%


# ── integration: the guards actually wire into _download_sync ────────────────
import pytest
import core.hifi_client as hc


class _Cfg:
    """Stub config so _download_sync just takes its defaults (no DB)."""
    def get(self, key, default=None):
        return default


def _bare_client(tmp_path):
    c = object.__new__(hc.HiFiClient)   # skip __init__ (DB / network)
    c.download_path = tmp_path
    c._engine = None
    c.shutdown_check = None
    return c


def test_download_sync_skips_preview_manifests_and_never_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, 'config_manager', _Cfg())
    c = _bare_client(tmp_path)
    c.get_track_info = lambda tid: {'duration_s': 215}          # real track length
    tiers = []
    c._get_hls_manifest = lambda tid, quality='lossless': (
        tiers.append(quality) or
        {'segment_uris': ['seg'], 'init_uri': None, 'extension': 'flac',
         'manifest_duration': 30.0})                            # preview at EVERY tier
    c._download_segment_with_retry = lambda url: pytest.fail("downloaded a preview segment!")

    result = c._download_sync('dl1', 12345, 'The Weeknd - Save Your Tears')
    assert result is None                                       # → orchestrator falls back
    assert tiers                                                # it did consult the manifest(s)


def test_download_sync_proceeds_past_the_gate_for_a_full_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, 'config_manager', _Cfg())
    c = _bare_client(tmp_path)
    c.get_track_info = lambda tid: {'duration_s': 215}
    c._get_hls_manifest = lambda tid, quality='lossless': {
        'segment_uris': ['seg'], 'init_uri': None, 'extension': 'flac',
        'manifest_duration': 215.0}                            # full length → must NOT skip
    seg_calls = []

    def _seg(url):
        seg_calls.append(url)
        raise RuntimeError("stop after the gate")
    c._download_segment_with_retry = _seg

    c._download_sync('dl1', 12345, 'x')
    assert seg_calls                                           # it got PAST the preview gate to download


def test_download_sync_does_not_reject_when_track_length_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, 'config_manager', _Cfg())
    c = _bare_client(tmp_path)
    c.get_track_info = lambda tid: {'duration_s': 0}           # expected unknown
    c._get_hls_manifest = lambda tid, quality='lossless': {
        'segment_uris': ['seg'], 'init_uri': None, 'extension': 'flac',
        'manifest_duration': 30.0}                            # short, but expected is unknown
    seg_calls = []
    c._download_segment_with_retry = lambda url: (seg_calls.append(url), (_ for _ in ()).throw(RuntimeError("stop")))[0]

    c._download_sync('dl1', 12345, 'x')
    assert seg_calls                                           # unknown length → no rejection, proceeds


# ── faked-header previews: claim full length everywhere, only ~30s of real audio ──
# (real numbers measured from issue #895's files: every "lossless" FLAC was a 30s
# preview with STREAMINFO total_samples faked to the full length.)
from core.hifi_client import is_fake_lossless_bitrate, is_preview_download, parse_ffmpeg_time


def test_real_issue895_files_are_all_flagged_by_bitrate():
    # (size_bytes, claimed_seconds) for the actual files — 16-bit/44.1kHz stereo FLAC.
    samples = [
        (4_080_000, 216),   # Save Your Tears  (151 kbps claimed)
        (6_770_000, 150),   # I Ain't Worried  (362 kbps — the highest, nearest the line)
        (2_240_000, 326),   # Lose Yourself     (55 kbps)
        (4_190_000, 285),   # The Real Slim Shady
        (4_910_000, 170),   # APT
    ]
    for size, secs in samples:
        assert is_fake_lossless_bitrate(size, secs, 44100, 16, 2) is True, (size, secs)


def test_real_full_lossless_is_not_flagged():
    # a genuine 16/44.1 lossless track is ~700-1100 kbps → well above the floor
    assert is_fake_lossless_bitrate(25_000_000, 216, 44100, 16, 2) is False   # ~926 kbps
    assert is_fake_lossless_bitrate(12_000_000, 216, 44100, 16, 2) is False   # ~444 kbps, still real


def test_bitrate_check_is_conservative_on_unknowns():
    assert is_fake_lossless_bitrate(0, 216, 44100, 16, 2) is False
    assert is_fake_lossless_bitrate(4_080_000, 0, 44100, 16, 2) is False
    assert is_fake_lossless_bitrate(4_080_000, 216, 0, 0, 0) is False


def test_is_preview_download_decode_path():
    # decoded 30s of a claimed 216s → fake, regardless of bitrate
    fake, why = is_preview_download(30.0, 216.0, is_lossless=False, size_bytes=99_000_000,
                                    sample_rate=44100, bits_per_sample=16, channels=2)
    assert fake and "decoded 30s of 216s" in why


def test_is_preview_download_bitrate_path_when_no_decoder():
    # real_seconds=0 (no ffmpeg) → fall back to the lossless bitrate check
    fake, why = is_preview_download(0.0, 216.0, is_lossless=True, size_bytes=4_080_000,
                                    sample_rate=44100, bits_per_sample=16, channels=2)
    assert fake and "kbps lossless" in why


def test_is_preview_download_passes_a_real_file():
    fake, _ = is_preview_download(214.0, 216.0, is_lossless=True, size_bytes=25_000_000,
                                  sample_rate=44100, bits_per_sample=16, channels=2)
    assert fake is False


def test_is_preview_download_lossy_no_decoder_is_not_flagged():
    # a lossy tier (mp3/m4a) with no decode info → can't bitrate-check → don't reject
    fake, _ = is_preview_download(0.0, 216.0, is_lossless=False, size_bytes=2_000_000,
                                  sample_rate=44100, bits_per_sample=16, channels=2)
    assert fake is False


def test_parse_ffmpeg_time_reads_the_last_progress_line():
    stderr = "frame=  ... time=00:00:12.34 bitrate=...\nframe= ... time=00:00:30.05 bitrate=..."
    assert abs(parse_ffmpeg_time(stderr) - 30.05) < 0.01
    assert parse_ffmpeg_time("no time here") == 0.0
