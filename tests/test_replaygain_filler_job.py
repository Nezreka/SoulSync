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


# ── #1060: target loudness + rescan_existing ────────────────────────────────

import json as _json
import os as _os
import tempfile as _tempfile

import pytest as _pytest

from core.replaygain import get_target_lufs
from core.repair_jobs.base import JobContext
from core.repair_jobs.replaygain_filler import ReplayGainFillerJob


class _Cfg:
    def __init__(self, **kv):
        self._d = kv

    def get(self, key, default=None):
        return self._d.get(key, default)


@_pytest.mark.parametrize('raw,expected', [
    (None, -18.0), ('junk', -18.0),
    (-14, -14.0), ('-14', -14.0),
    (14, -14.0),            # tolerate positive input
    (-40, -30.0),           # clamp floor
    (-2, -5.0),             # clamp ceiling
])
def test_target_lufs_matrix(raw, expected):
    cfg = _Cfg(**({'repair.jobs.replaygain_filler.settings.target_lufs': raw} if raw is not None else {}))
    assert get_target_lufs(cfg) == expected


def test_target_lufs_none_config_is_reference():
    assert get_target_lufs(None) == -18.0


def test_apply_honours_custom_target(tmp_path):
    f = tmp_path / 'song.flac'
    f.write_bytes(b'\x00' * 64)
    written = {}

    def fake_write(path, gain, peak, *a, **k):
        written.update(gain=gain)
        return True

    w = RepairWorker(database=SimpleNamespace())
    w._config_manager = _Cfg(**{'repair.jobs.replaygain_filler.settings.target_lufs': -14})
    with patch('core.replaygain.is_ffmpeg_available', return_value=True), \
         patch('core.replaygain.analyze_track', return_value=(-12.0, -1.5)), \
         patch('core.replaygain.write_replaygain_tags', side_effect=fake_write):
        res = w._fix_missing_replaygain('track', '1', str(f), {'file_path': str(f)})
    assert res['success'] is True
    assert written['gain'] == -2.0        # -14 - (-12)


def test_retag_finding_type_dispatches_to_same_fix():
    from pathlib import Path
    src = Path('core/repair_worker.py').read_text(encoding='utf-8')
    assert "'replaygain_retag': self._fix_missing_replaygain" in src


def _scan_ctx(db, cfg, findings):
    return JobContext(db=db, transfer_folder='/tmp', config_manager=cfg,
                      create_finding=lambda **kw: findings.append(kw) or True)


def _db_with_tracks(n=3):
    from database.music_database import MusicDatabase
    d = MusicDatabase(_os.path.join(_tempfile.mkdtemp(), 't.db'))
    conn = d._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES ('AR1','A')")
    cur.execute("INSERT INTO albums (id, artist_id, title) VALUES ('AL1','AR1','Al')")
    for i in range(n):
        cur.execute("INSERT INTO tracks (id, album_id, artist_id, title, file_path) "
                    "VALUES (?, 'AL1', 'AR1', ?, ?)",
                    (f'T{i}', f'Song {i}', f'/music/{i}.flac'))
    conn.commit(); conn.close()
    return d


def test_scan_rescan_off_skips_tagged_tracks():
    db = _db_with_tracks(2)
    findings = []
    with patch('core.repair_jobs.replaygain_filler._resolve', side_effect=lambda p, c: p), \
         patch('core.replaygain.is_ffmpeg_available', return_value=True), \
         patch('core.replaygain.read_replaygain_tags', return_value={'track_gain': '-6.00 dB'}):
        ReplayGainFillerJob().scan(_scan_ctx(db, _Cfg(), findings))
    assert findings == []                      # all tagged, rescan off → nothing


def test_scan_rescan_on_flags_tagged_tracks_as_retag():
    db = _db_with_tracks(2)
    findings = []
    cfg = _Cfg(**{'repair.jobs.replaygain_filler.settings.rescan_existing': True})
    with patch('core.repair_jobs.replaygain_filler._resolve', side_effect=lambda p, c: p), \
         patch('core.replaygain.is_ffmpeg_available', return_value=True), \
         patch('core.replaygain.read_replaygain_tags', return_value={'track_gain': '-6.00 dB'}):
        res = ReplayGainFillerJob().scan(_scan_ctx(db, cfg, findings))
    assert res.findings_created == 2
    assert all(f['finding_type'] == 'replaygain_retag' for f in findings)
    assert findings[0]['details']['current_gain'] == '-6.00 dB'


def test_scan_rescan_cap_is_honoured_and_logged():
    db = _db_with_tracks(4)
    findings = []
    logs = []
    cfg = _Cfg(**{'repair.jobs.replaygain_filler.settings.rescan_existing': True})
    ctx = JobContext(db=db, transfer_folder='/tmp', config_manager=cfg,
                     create_finding=lambda **kw: findings.append(kw) or True,
                     report_progress=lambda **kw: logs.append(kw))
    with patch('core.repair_jobs.replaygain_filler._resolve', side_effect=lambda p, c: p), \
         patch('core.replaygain.is_ffmpeg_available', return_value=True), \
         patch('core.replaygain.read_replaygain_tags', return_value={'track_gain': '-6.00 dB'}), \
         patch.object(ReplayGainFillerJob, 'RESCAN_BATCH_LIMIT', 2):
        ReplayGainFillerJob().scan(ctx)
    assert len(findings) == 2                                  # capped
    assert any('capped' in str(l.get('log_line', '')) for l in logs)   # never silent


def test_untagged_tracks_still_flagged_normally_in_rescan_mode():
    db = _db_with_tracks(1)
    findings = []
    cfg = _Cfg(**{'repair.jobs.replaygain_filler.settings.rescan_existing': True})
    with patch('core.repair_jobs.replaygain_filler._resolve', side_effect=lambda p, c: p), \
         patch('core.replaygain.is_ffmpeg_available', return_value=True), \
         patch('core.replaygain.read_replaygain_tags', return_value=None):
        ReplayGainFillerJob().scan(_scan_ctx(db, cfg, findings))
    assert len(findings) == 1
    assert findings[0]['finding_type'] == 'missing_replaygain'


def test_all_gain_writers_use_the_target():
    from pathlib import Path
    ws = Path('web_server.py').read_text(encoding='utf-8')
    assert ws.count('_rg_get_target_lufs(config_manager)') == 4
    assert '_RG_REFERENCE_LUFS' not in ws
    pl = Path('core/imports/pipeline.py').read_text(encoding='utf-8')
    assert '_rg_target(config_manager) - lufs' in pl
