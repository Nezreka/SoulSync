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
import core.imports.pipeline as pipeline
import core.imports.file_ops as file_ops
import core.quality.selection as selection
from core.imports.pipeline import _should_skip_quarantine_check
from core.quality.model import AudioQuality


def _patch_guard(monkeypatch, probe_aq, profile, downsample=False):
    monkeypatch.setattr(file_ops, 'probe_audio_quality', lambda fp: probe_aq)
    # check_quality_target resolves the profile via load_profile_by_id
    # (context['track_info']['quality_profile_id'] when present, else the
    # global default) — patch that seam directly rather than faking a DB.
    monkeypatch.setattr(selection, 'load_profile_by_id', lambda profile_id: profile)

    def _cfg_get(k, d=None):
        if 'downsample' in k:
            return downsample
        return d

    monkeypatch.setattr(
        guards, '_get_config_manager',
        lambda: types.SimpleNamespace(get=_cfg_get),
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


def test_empty_targets_accept_everything(monkeypatch):
    # There is no separate "skip the check entirely" master toggle: a profile
    # with an empty ranked_targets list already means "accept anything" —
    # composing "no quality check" this way (or via fallback_enabled=True)
    # replaces the old import.quality_filter_enabled setting.
    _patch_guard(
        monkeypatch, AudioQuality('flac', sample_rate=44100, bit_depth=16),
        {'fallback_enabled': False, 'ranked_targets': []},
    )
    assert guards.check_quality_target('/x/song.flac', {}) is None


def test_accepts_context_with_null_track_info(monkeypatch):
    _patch_guard(monkeypatch, AudioQuality('flac', sample_rate=96000, bit_depth=24), _WANT_FLAC24)
    assert guards.check_quality_target('/x/song.flac', {'track_info': None}) is None


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


def test_manual_import_bypass_list_skips_quality_but_not_acoustid():
    # The Import page's explicit-match flows set this exact list (#1017):
    # the quality profile has no veto on a file the user hand-matched, but
    # AcoustID/integrity/silence still guard against a mislabeled file.
    ctx = {'_skip_quarantine_check': ['quality', 'bit_depth']}
    assert _should_skip_quarantine_check(ctx, 'quality') is True
    assert _should_skip_quarantine_check(ctx, 'bit_depth') is True
    assert _should_skip_quarantine_check(ctx, 'acoustid') is False
    assert _should_skip_quarantine_check(ctx, 'integrity') is False
    assert _should_skip_quarantine_check(ctx, 'silence') is False


def test_force_grab_approval_is_narrow_and_marks_context(monkeypatch):
    calls = []

    def approve(context, *, reason_code, trigger, reason):
        calls.append((reason_code, trigger, reason))
        return reason_code == 'quality_not_allowed'

    monkeypatch.setattr(
        'core.acquisition.pipeline_callback.notify_force_quarantine_auto_approved',
        approve,
    )
    context = {'_acquisition_import_id': 'aim1-test'}

    assert pipeline._try_force_grab_quarantine_approval(
        context,
        reason_code='quality_not_allowed',
        trigger='quality',
        reason='Below profile',
    ) is True
    assert context['_force_approved_quarantine_reason'] == 'quality_not_allowed'
    assert calls == [('quality_not_allowed', 'quality', 'Below profile')]


def test_force_grab_approval_fail_closed_for_other_reason(monkeypatch):
    monkeypatch.setattr(
        'core.acquisition.pipeline_callback.notify_force_quarantine_auto_approved',
        lambda *_args, **_kwargs: False,
    )
    context = {'_acquisition_import_id': 'aim1-test'}

    assert pipeline._try_force_grab_quarantine_approval(
        context,
        reason_code='acoustid_mismatch',
        trigger='acoustid',
        reason='Fingerprint mismatch',
    ) is False
    assert '_force_approved_quarantine_reason' not in context


def test_manual_import_bypass_list_skips_quality_but_not_acoustid():
    # The Import page's explicit-match flows set this exact list (#1017):
    # the quality profile has no veto on a file the user hand-matched, but
    # AcoustID/integrity/silence still guard against a mislabeled file.
    ctx = {'_skip_quarantine_check': ['quality', 'bit_depth']}
    assert _should_skip_quarantine_check(ctx, 'quality') is True
    assert _should_skip_quarantine_check(ctx, 'bit_depth') is True
    assert _should_skip_quarantine_check(ctx, 'acoustid') is False
    assert _should_skip_quarantine_check(ctx, 'integrity') is False
    assert _should_skip_quarantine_check(ctx, 'silence') is False


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
