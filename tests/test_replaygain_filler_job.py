"""ReplayGain Filler job (#437) — fills ReplayGain on library content that skipped
download post-processing (Lidarr / REST API / manual adds). Pure flag decision +
the apply handler's analyze→compute→write seam (ffmpeg mocked)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


# ── native (lib2:<id>) findings must use resolve_lib2_path, not the legacy
# resolver — same LV2-LYRICS-01 divergence, same fix, applied consistently. ──

def test_apply_native_finding_uses_lib2_resolver(tmp_path, monkeypatch):
    f = tmp_path / 'song.flac'
    f.write_bytes(b'\x00' * 64)

    def _boom(*a, **k):
        raise AssertionError('legacy resolver must not be used for a native lib2 finding')
    monkeypatch.setattr('core.repair_worker._resolve_file_path', _boom)
    resolve_calls = []
    monkeypatch.setattr(
        'core.library2.paths.resolve_lib2_path',
        lambda raw, config_manager=None: resolve_calls.append(raw) or str(f),
    )

    with patch('core.replaygain.is_ffmpeg_available', return_value=True), \
         patch('core.replaygain.analyze_track', return_value=(-12.0, -1.5)), \
         patch('core.replaygain.write_replaygain_tags', return_value=True), \
         patch('core.replaygain.RG_REFERENCE_LUFS', -18.0):
        res = _worker()._fix_missing_replaygain(
            'track', 'lib2:1', None, {'file_path': 'stored/song.flac'})

    assert res['success'] is True
    assert resolve_calls == ['stored/song.flac']


def test_apply_native_finding_refreshes_lib2_tag_cache(tmp_path, monkeypatch):
    f = tmp_path / 'song.flac'
    f.write_bytes(b'\x00' * 64)

    w = _worker()
    w.db = SimpleNamespace(_get_connection=lambda: MagicMock())
    monkeypatch.setattr(
        'core.library2.paths.resolve_lib2_path', lambda raw, config_manager=None: str(f))
    refreshed = {}
    monkeypatch.setattr(
        'core.library2.tag_cache.read_and_persist_tag_cache',
        lambda conn, file_id, path: refreshed.update(file_id=file_id, path=path) or True,
    )

    with patch('core.replaygain.is_ffmpeg_available', return_value=True), \
         patch('core.replaygain.analyze_track', return_value=(-12.0, -1.5)), \
         patch('core.replaygain.write_replaygain_tags', return_value=True), \
         patch('core.replaygain.RG_REFERENCE_LUFS', -18.0):
        res = w._fix_missing_replaygain(
            'track', 'lib2:1', None,
            {'file_path': 'song.flac', 'library_v2': {'file_id': 7}})

    assert res['success'] is True
    assert refreshed == {'file_id': 7, 'path': str(f)}


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
    from tests.support.catalogue_seed import seed_album, seed_artist, seed_track

    d = MusicDatabase(_os.path.join(_tempfile.mkdtemp(), 't.db'))
    conn = d._get_connection()
    artist_id = seed_artist(conn, server_id='ar1', name='A')
    album_id = seed_album(conn, server_id='al1', title='Al', artist_id=artist_id)
    for i in range(n):
        seed_track(conn, server_id=f'tr{i}', title=f'Song {i}', album_id=album_id,
                   artist_id=artist_id, file_path=f'/music/{i}.flac')
    conn.commit(); conn.close()
    return d


def _subjects_from_db(db):
    """The job enumerates Library-v2 file subjects — build the same shape here
    so these tests stay isolated from whatever real files happen to exist on
    the filesystem being scanned."""
    conn = db._get_connection()
    rows = conn.execute(
        """SELECT t.id AS id, t.title AS title, f.path AS path
             FROM lib2_tracks t
             JOIN lib2_track_files f ON f.track_id = t.id AND f.is_primary = 1
            ORDER BY t.id"""
    ).fetchall()
    conn.close()
    return [
        {'track_id': r['id'], 'title': r['title'], 'artist_name': 'A', 'path': r['path']}
        for r in rows
    ]


def test_scan_rescan_off_skips_tagged_tracks():
    db = _db_with_tracks(2)
    findings = []
    with patch('core.repair_jobs.replaygain_filler._resolve', side_effect=lambda p, c: p), \
         patch('core.replaygain.is_ffmpeg_available', return_value=True), \
         patch('core.replaygain.read_replaygain_tags', return_value={'track_gain': '-6.00 dB'}), \
         patch('core.library2.maintenance_subjects.active_file_subjects',
               return_value=_subjects_from_db(db)), \
         patch('core.repair_jobs.filesystem_subjects.filesystem_audio_files', return_value=[]):
        ReplayGainFillerJob().scan(_scan_ctx(db, _Cfg(), findings))
    assert findings == []                      # all tagged, rescan off → nothing


def test_scan_rescan_on_flags_tagged_tracks_as_retag():
    db = _db_with_tracks(2)
    findings = []
    cfg = _Cfg(**{'repair.jobs.replaygain_filler.settings.rescan_existing': True})
    with patch('core.repair_jobs.replaygain_filler._resolve', side_effect=lambda p, c: p), \
         patch('core.replaygain.is_ffmpeg_available', return_value=True), \
         patch('core.replaygain.read_replaygain_tags', return_value={'track_gain': '-6.00 dB'}), \
         patch('core.library2.maintenance_subjects.active_file_subjects',
               return_value=_subjects_from_db(db)), \
         patch('core.repair_jobs.filesystem_subjects.filesystem_audio_files', return_value=[]):
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
         patch('core.library2.maintenance_subjects.active_file_subjects',
               return_value=_subjects_from_db(db)), \
         patch('core.repair_jobs.filesystem_subjects.filesystem_audio_files', return_value=[]), \
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
         patch('core.replaygain.read_replaygain_tags', return_value=None), \
         patch('core.library2.maintenance_subjects.active_file_subjects',
               return_value=_subjects_from_db(db)), \
         patch('core.repair_jobs.filesystem_subjects.filesystem_audio_files', return_value=[]):
        ReplayGainFillerJob().scan(_scan_ctx(db, cfg, findings))
    assert len(findings) == 1
    assert findings[0]['finding_type'] == 'missing_replaygain'


def test_all_gain_writers_use_the_target():
    from pathlib import Path
    # The four web_server call sites were the artist-detail page's ReplayGain
    # endpoints; they went with the page (§50.4.4.24). What has to stay pinned
    # is that no writer reintroduces a hard-coded reference.
    ws = Path('web_server.py').read_text(encoding='utf-8')
    assert '_RG_REFERENCE_LUFS' not in ws
    pl = Path('core/imports/pipeline.py').read_text(encoding='utf-8')
    assert '_rg_target(config_manager) - lufs' in pl
