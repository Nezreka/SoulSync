"""Quality guard + quarantine isolation.

Locks two invariants the global-quality work depends on:

1. ``check_quality_target`` rejects with a 'what the file IS vs what was
   WANTED' reason (surfaced in the track-detail modal), and accepts when a
   target is met or fallback is on.
2. A quality mismatch is isolated from the AcoustID/force_import path: it
   uses ``trigger='quality'`` and the ``'quality'`` bypass flag, which must
   NOT bypass the AcoustID check and vice-versa. force_imported stays
   reserved for AcoustID version-mismatch acceptance.
"""

import json
import types

import pytest

import core.imports.guards as guards
import core.imports.file_ops as file_ops
from core.imports.pipeline import _should_skip_quarantine_check
from core.quality.model import AudioQuality


class _FakeDB:
    def __init__(self, profile):
        self._p = profile

    def get_quality_profile(self):
        return self._p


def _patch_guard(monkeypatch, probe_aq, profile, downsample=False):
    monkeypatch.setattr(file_ops, 'probe_audio_quality', lambda fp: probe_aq)
    monkeypatch.setattr(guards, 'MusicDatabase', lambda: _FakeDB(profile))
    monkeypatch.setattr(
        guards, '_get_config_manager',
        lambda: types.SimpleNamespace(get=lambda k, d=None: downsample if 'downsample' in k else d),
    )


_WANT_FLAC24 = {
    'fallback_enabled': False,
    'ranked_targets': [
        {'label': 'FLAC 24-bit/96kHz', 'format': 'flac', 'bit_depth': 24, 'min_sample_rate': 96000},
    ],
}
_WANT_FLAC24_FALLBACK = {**_WANT_FLAC24, 'fallback_enabled': True}


# ── check_quality_target ───────────────────────────────────────────────────

def test_rejects_with_wanted_vs_got_reason(monkeypatch):
    _patch_guard(monkeypatch, AudioQuality('flac', sample_rate=44100, bit_depth=16), _WANT_FLAC24)
    reason = guards.check_quality_target('/x/song.flac', {})
    assert reason is not None
    assert 'FLAC 16-bit' in reason          # what the file IS
    assert 'FLAC 24-bit/96kHz' in reason    # what was WANTED


def test_accepts_when_target_met(monkeypatch):
    _patch_guard(monkeypatch, AudioQuality('flac', sample_rate=96000, bit_depth=24), _WANT_FLAC24)
    assert guards.check_quality_target('/x/song.flac', {}) is None


def test_accepts_via_fallback(monkeypatch):
    _patch_guard(monkeypatch, AudioQuality('flac', sample_rate=44100, bit_depth=16), _WANT_FLAC24_FALLBACK)
    assert guards.check_quality_target('/x/song.flac', {}) is None


def test_skips_when_unprobeable(monkeypatch):
    _patch_guard(monkeypatch, None, _WANT_FLAC24)
    assert guards.check_quality_target('/x/song.flac', {}) is None


# ── force_import isolation ─────────────────────────────────────────────────

def test_quality_bypass_does_not_skip_acoustid():
    ctx = {'_skip_quarantine_check': 'quality'}
    assert _should_skip_quarantine_check(ctx, 'quality') is True
    assert _should_skip_quarantine_check(ctx, 'acoustid') is False


def test_acoustid_bypass_does_not_skip_quality():
    ctx = {'_skip_quarantine_check': 'acoustid'}
    assert _should_skip_quarantine_check(ctx, 'acoustid') is True
    assert _should_skip_quarantine_check(ctx, 'quality') is False


def test_quality_quarantine_persists_quality_trigger(monkeypatch, tmp_path):
    # A quality reject writes trigger='quality' (not 'acoustid') into the
    # sidecar, so Approve never routes it through the force_import path.
    monkeypatch.setattr(
        guards, '_get_config_manager',
        lambda: types.SimpleNamespace(get=lambda k, d=None: str(tmp_path) if 'download_path' in k else d),
    )
    src = tmp_path / 'song.flac'
    src.write_bytes(b'FLACfake')
    qpath = guards.move_to_quarantine(
        str(src), {}, 'Quality mismatch: file is FLAC 16-bit, wanted FLAC 24-bit/96kHz',
        automation_engine=None, trigger='quality',
    )
    sidecars = list((tmp_path / 'ss_quarantine').glob('*.json'))
    assert len(sidecars) == 1
    meta = json.loads(sidecars[0].read_text(encoding='utf-8'))
    assert meta['trigger'] == 'quality'
    assert meta['trigger'] != 'acoustid'
    assert 'wanted FLAC 24-bit/96kHz' in meta['quarantine_reason']
    assert qpath.endswith('.quarantined')
