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


# ── YouTube re-encode vs the import quality guard ──────────────────────────
#
# With re-encode on, search ranking uses the converted file (e.g. MP3 320).
# The import guard probes that file, so a profile that asked for Opus will
# reject a transcoded MP3 when Fallback is off. That is a quality mismatch
# (quarantine), not corruption.

_WANT_OPUS = {
    'fallback_enabled': False,
    'ranked_targets': [{'label': 'Opus', 'format': 'opus'}],
}
_WANT_OPUS_FALLBACK = {**_WANT_OPUS, 'fallback_enabled': True}
_WANT_AAC = {
    'fallback_enabled': False,
    'ranked_targets': [{'label': 'AAC', 'format': 'aac', 'min_bitrate': 128}],
}
_WANT_MP3_320 = {
    'fallback_enabled': False,
    'ranked_targets': [{'label': 'MP3 320kbps', 'format': 'mp3', 'min_bitrate': 320}],
}
_WANT_FLAC_THEN_MP3 = {
    'fallback_enabled': False,
    'ranked_targets': [
        {'label': 'FLAC 16-bit', 'format': 'flac', 'bit_depth': 16},
        {'label': 'MP3 320kbps', 'format': 'mp3', 'min_bitrate': 320},
    ],
}

_OPUS_160 = AudioQuality('opus', bitrate=160)
_AAC_128 = AudioQuality('aac', bitrate=128)
_MP3_320 = AudioQuality('mp3', bitrate=320)
_MP3_128 = AudioQuality('mp3', bitrate=128)


def test_transcoded_mp3_rejected_when_profile_asked_for_opus(monkeypatch):
    _patch_guard(monkeypatch, _MP3_320, _WANT_OPUS)
    reason = guards.check_quality_target('/x/Song.mp3', {})
    assert reason is not None
    assert 'Quality mismatch' in reason
    assert 'MP3 320kbps' in reason
    assert 'Opus' in reason
    assert 'corrupt' not in reason.lower()


def test_transcoded_mp3_accepted_via_fallback_when_profile_asked_for_opus(monkeypatch):
    _patch_guard(monkeypatch, _MP3_320, _WANT_OPUS_FALLBACK)
    assert guards.check_quality_target('/x/Song.mp3', {}) is None


def test_remuxed_opus_accepted_when_profile_asked_for_opus(monkeypatch):
    _patch_guard(monkeypatch, _OPUS_160, _WANT_OPUS)
    assert guards.check_quality_target('/x/Song.opus', {}) is None


def test_transcoded_mp3_accepted_when_profile_includes_mp3_320(monkeypatch):
    """Re-encode to MP3 320 satisfies an MP3 320 target even if YouTube's
    original stream was Opus — the guard checks the file on disk."""
    _patch_guard(monkeypatch, _MP3_320, _WANT_FLAC_THEN_MP3)
    assert guards.check_quality_target('/x/Song.mp3', {}) is None


def test_transcoded_mp3_128_rejected_when_profile_wants_mp3_320(monkeypatch):
    _patch_guard(monkeypatch, _MP3_128, _WANT_MP3_320)
    reason = guards.check_quality_target('/x/Song.mp3', {})
    assert reason is not None
    assert 'MP3 128kbps' in reason


def test_transcoded_aac_rejected_when_profile_asked_for_opus(monkeypatch):
    _patch_guard(monkeypatch, _AAC_128, _WANT_OPUS)
    reason = guards.check_quality_target('/x/Song.m4a', {})
    assert reason is not None
    assert 'AAC' in reason


def test_transcoded_aac_accepted_when_profile_asked_for_aac(monkeypatch):
    _patch_guard(monkeypatch, _AAC_128, _WANT_AAC)
    assert guards.check_quality_target('/x/Song.m4a', {}) is None


def test_transcode_output_unprobeable_is_not_rejected(monkeypatch):
    """If mutagen can't read the re-encoded file, the quality gate fails open
    (same as any other unreadable download) — it does not invent a mismatch."""
    _patch_guard(monkeypatch, None, _WANT_OPUS)
    assert guards.check_quality_target('/x/Song.mp3', {}) is None


def test_integrity_check_does_not_take_a_quality_profile():
    """Corruption/integrity is size + parse + duration. A successful re-encode
    to a valid MP3 is not 'corrupt' just because the profile asked for Opus."""
    import inspect
    from core.imports.file_integrity import check_audio_integrity
    params = inspect.signature(check_audio_integrity).parameters
    assert 'targets' not in params
    assert 'profile' not in params
    assert 'ranked_targets' not in params


def test_quality_mismatch_quarantine_is_not_an_integrity_trigger(monkeypatch, tmp_path):
    monkeypatch.setattr(
        guards, '_get_config_manager',
        lambda: types.SimpleNamespace(get=lambda k, d=None: str(tmp_path) if 'download_path' in k else d),
    )
    src = tmp_path / 'Song.mp3'
    src.write_bytes(b'ID3fake')
    guards.move_to_quarantine(
        str(src), {},
        'Quality mismatch: file is MP3 320kbps, does not satisfy any configured target (best wanted: Opus)',
        automation_engine=None, trigger='quality',
    )
    meta = json.loads(next((tmp_path / 'ss_quarantine').glob('*.json')).read_text(encoding='utf-8'))
    assert meta['trigger'] == 'quality'
    assert meta['trigger'] != 'integrity'
    assert 'corrupt' not in meta['quarantine_reason'].lower()

