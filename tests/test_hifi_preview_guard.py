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
