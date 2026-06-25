"""``check_flac_bit_depth`` is a thin legacy wrapper that delegates to the
unified ``check_quality_target`` guard, which probes the REAL file (mutagen)
and ranks it against the v3 ranked-targets list.

The old per-quality ``bit_depth_fallback`` config and the "reject a higher bit
depth" semantics are gone by design: bit depth is now a MINIMUM, so a 24-bit
file satisfies a 16-bit target. These tests pin the wrapper's current
behaviour (deeper coverage of ``check_quality_target`` lives in
``tests/imports/test_quality_guard.py``).
"""

from types import SimpleNamespace

import core.imports.guards as guards
import core.imports.file_ops as file_ops
from core.quality.model import AudioQuality


class _FakeDB:
    def __init__(self, profile):
        self._profile = profile

    def get_quality_profile(self):
        return self._profile


_WANT_FLAC24 = {
    "fallback_enabled": False,
    "ranked_targets": [
        {"label": "FLAC 24-bit/96kHz", "format": "flac", "bit_depth": 24, "min_sample_rate": 96000},
    ],
}


def _patch(monkeypatch, aq, profile):
    monkeypatch.setattr(file_ops, "probe_audio_quality", lambda fp: aq)
    monkeypatch.setattr(guards, "MusicDatabase", lambda: _FakeDB(profile))

    # Key-aware config stub: the import quality filter is ON (its default), so
    # the guard runs; everything else (downsample, etc.) is OFF. A blanket False
    # would wrongly disable the filter itself via import.quality_filter_enabled.
    def _cfg_get(key, default=None):
        if key == "import.quality_filter_enabled":
            return True
        return False

    monkeypatch.setattr(
        guards, "_get_config_manager",
        lambda: SimpleNamespace(get=_cfg_get),
    )


def test_check_flac_bit_depth_rejects_below_target(monkeypatch):
    # 16-bit file, target wants 24-bit, fallback off → rejected.
    _patch(monkeypatch, AudioQuality("flac", sample_rate=44100, bit_depth=16), _WANT_FLAC24)
    reason = guards.check_flac_bit_depth("/tmp/Song One.flac", {})
    assert reason is not None
    assert "FLAC 24-bit/96kHz" in reason


def test_check_flac_bit_depth_accepts_when_meeting_target(monkeypatch):
    # 24-bit/96k file meets the 24-bit target → accepted.
    _patch(monkeypatch, AudioQuality("flac", sample_rate=96000, bit_depth=24), _WANT_FLAC24)
    assert guards.check_flac_bit_depth("/tmp/Song One.flac", {}) is None
