"""ReplayGain Filler job (#437) — fills ReplayGain on library content that skipped
download post-processing (Lidarr / REST API / manual adds). Pure flag decision +
the apply handler's analyze→compute→write seam (ffmpeg mocked)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from core.repair_jobs.replaygain_filler import needs_replaygain
from core.repair_worker import RepairWorker


# ── pure decision: does a track need ReplayGain? ────────────────────────────
def test_needs_rg_when_no_tags():
    assert needs_replaygain(None) is True


def test_needs_rg_when_track_gain_missing():
    assert needs_replaygain({'track_gain': None, 'track_peak': None}) is True


def test_needs_rg_when_track_gain_blank():
    assert needs_replaygain({'track_gain': '   '}) is True


def test_no_rg_needed_when_gain_present():
    assert needs_replaygain({'track_gain': '-6.50 dB'}) is False


def test_zero_gain_counts_as_tagged():
    # A legitimate "+0.00 dB" is already analyzed — must NOT be re-flagged forever.
    assert needs_replaygain({'track_gain': '+0.00 dB'}) is False


# ── apply handler: analyze → compute gain → write (ffmpeg mocked) ────────────
def _worker():
    w = RepairWorker(database=SimpleNamespace())
    w._config_manager = None
    return w


def test_apply_writes_rg_with_pipeline_gain_formula(tmp_path):
    f = tmp_path / 'song.flac'
    f.write_bytes(b'\x00' * 64)
    written = {}

    def fake_write(path, gain, peak, *a, **k):
        written.update(path=path, gain=gain, peak=peak)
        return True

    with patch('core.replaygain.is_ffmpeg_available', return_value=True), \
         patch('core.replaygain.analyze_track', return_value=(-12.0, -1.5)), \
         patch('core.replaygain.write_replaygain_tags', side_effect=fake_write), \
         patch('core.replaygain.RG_REFERENCE_LUFS', -18.0):
        res = _worker()._fix_missing_replaygain('track', '1', str(f), {'file_path': str(f)})

    assert res['success'] is True and res['action'] == 'applied_replaygain'
    # gain = reference - lufs = -18.0 - (-12.0) = -6.0  (same as the import pipeline)
    assert written['gain'] == -6.0
    assert written['peak'] == -1.5
    assert written['path'] == str(f)


def test_apply_errors_without_ffmpeg(tmp_path):
    f = tmp_path / 's.flac'
    f.write_bytes(b'\x00' * 64)
    with patch('core.replaygain.is_ffmpeg_available', return_value=False):
        res = _worker()._fix_missing_replaygain('track', '1', str(f), {'file_path': str(f)})
    assert res['success'] is False and 'ffmpeg' in res['error'].lower()


def test_apply_errors_when_file_missing():
    res = _worker()._fix_missing_replaygain(
        'track', '1', '/no/such/file.flac', {'file_path': '/no/such/file.flac'})
    assert res['success'] is False


def test_job_is_registered_and_opt_in():
    from core.repair_jobs import get_all_jobs
    j = get_all_jobs().get('replaygain_filler')
    assert j is not None and j.default_enabled is False
