"""The ``library_cleanup`` action and the seeded "Weekly Cleanup" automation.

LettuceSnob (Sep 17 2026) asked for a way to trigger the recycle bin scrub;
until now the bin's retention only ran when someone opened the Recycle Bin
tab, and the quarantine had an action nobody scheduled. Pins here:

* the handler reads its paths and the keep window from config, runs the core
  sweep, narrates through deps.update_progress and returns a flat summary
* both halves default ON for a seeded row (empty config), each can be
  switched off, both off is a skip not a silent no-op
* an exception is reported as status=error, never raised into the engine
* the seam: register_all wires the action, and ensure_system_automations on
  a REAL MusicDatabase seeds "Weekly Cleanup" every 7 days and SWITCHED OFF,
  and a later startup leaves a user's ON alone
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, List

import pytest

from core.automation.deps import AutomationDeps, AutomationState
from core.automation.handlers import register_all
from core.automation.handlers.library_cleanup import auto_library_cleanup
from core.automation_engine import SYSTEM_AUTOMATIONS, AutomationEngine
from database.music_database import MusicDatabase


class _StubLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


class _StubConfig:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return 'plex'


def _build_deps(**overrides) -> AutomationDeps:
    defaults = dict(
        engine=object(), state=AutomationState(), config_manager=_StubConfig(),
        update_progress=lambda *a, **k: None, logger=_StubLogger(),
        get_database=lambda: object(), spotify_client=None, tidal_client=None,
        web_scan_manager=None,
        process_wishlist_automatically=lambda **k: None,
        process_watchlist_scan_automatically=lambda **k: None,
        is_wishlist_actually_processing=lambda: False,
        is_watchlist_actually_scanning=lambda: False,
        get_watchlist_scan_state=lambda: {},
        run_playlist_discovery_worker=lambda *a, **k: None,
        run_sync_task=lambda *a, **k: None,
        run_playlist_organize_download=lambda **k: {'status': 'skipped'},
        missing_download_executor=None, load_sync_status_file=lambda: {},
        get_deezer_client=lambda: None, parse_youtube_playlist=lambda url: None,
        get_sync_states=lambda: {}, set_db_update_automation_id=lambda v: None,
        get_db_update_state=lambda: {}, db_update_lock=threading.Lock(),
        db_update_executor=None, run_db_update_task=lambda *a, **k: None,
        run_deep_scan_task=lambda *a, **k: None,
        get_duplicate_cleaner_state=lambda: {}, duplicate_cleaner_lock=threading.Lock(),
        duplicate_cleaner_executor=None, run_duplicate_cleaner=lambda: None,
        run_repair_job_now=lambda *a, **k: True, download_orchestrator=None,
        run_async=lambda coro: None, tasks_lock=threading.Lock(),
        get_download_batches=lambda: {}, get_download_tasks=lambda: {},
        sweep_empty_download_directories=lambda: 0, get_staging_path=lambda: '/staging',
        docker_resolve_path=lambda p: p, get_current_profile_id=lambda: 1,
        get_watchlist_scanner=lambda spc: None, get_app=lambda: None,
        get_beatport_data_cache=lambda: {'cache_lock': threading.Lock(), 'homepage': {}},
        init_automation_progress=lambda *a, **k: None,
        record_progress_history=lambda *a, **k: None,
        build_personalized_manager=lambda: None,
    )
    defaults.update(overrides)
    return AutomationDeps(**defaults)  # type: ignore[arg-type]


def _folders(tmp_path):
    """A quarantine with one file and a bin with one manifested old file."""
    from core.library import deleted_quarantine as dq
    from datetime import datetime, timedelta, timezone
    downloads = tmp_path / 'downloads'
    (downloads / 'ss_quarantine').mkdir(parents=True)
    qfile = downloads / 'ss_quarantine' / 'bad.flac'
    qfile.write_bytes(b'')
    transfer = tmp_path / 'Transfer'
    root = transfer / '.deleted'
    (root / 'A').mkdir(parents=True)
    binned = root / 'A' / 'old.flac'
    binned.write_bytes(b'')
    dq.record_deleted_entry(str(root), str(binned), str(transfer / 'A' / 'old.flac'), 'repair')
    manifest = dq._load_manifest(str(root))
    manifest['A/old.flac']['deleted_at'] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    dq._save_manifest(str(root), manifest)
    return downloads, transfer, qfile, binned


def _deps_for(tmp_path, keep_days=None, **more):
    downloads, transfer, qfile, binned = _folders(tmp_path)
    values = {'soulseek.download_path': str(downloads), 'soulseek.transfer_path': str(transfer)}
    if keep_days is not None:
        values['library.deleted_keep_days'] = keep_days
    lines: List[Dict[str, Any]] = []

    def _progress(automation_id, **kw):
        lines.append({'automation_id': automation_id, **kw})
    deps = _build_deps(config_manager=_StubConfig(values), update_progress=_progress, **more)
    return deps, lines, qfile, binned


# ── the handler ──────────────────────────────────────────────────────────────

class TestHandler:
    def test_an_empty_config_runs_both_halves(self, tmp_path):
        deps, lines, qfile, binned = _deps_for(tmp_path, keep_days=7)
        out = auto_library_cleanup({'_automation_id': 42}, deps)
        assert out == {'status': 'completed', 'removed': 2, 'quarantine_removed': 1,
                       'recycle_purged': 1, 'errors': 0}
        assert not qfile.exists() and not binned.exists()
        # narrated under the automation's id, one line per half
        assert all(line['automation_id'] == 42 for line in lines)
        said = [line['log_line'] for line in lines if 'log_line' in line]
        assert said == ['Quarantine: removed 1 item(s)',
                        'Recycle bin: purged 1 file(s) older than 7 day(s)']

    def test_keep_forever_empties_the_bin(self, tmp_path):
        deps, lines, qfile, binned = _deps_for(tmp_path)   # no retention key at all
        out = auto_library_cleanup({}, deps)
        assert out['recycle_purged'] == 1 and not binned.exists()
        assert 'Recycle bin: emptied, 1 file(s) deleted for good' in [
            line.get('log_line') for line in lines]

    def test_a_fresh_file_survives_the_keep_window(self, tmp_path):
        deps, lines, qfile, binned = _deps_for(tmp_path, keep_days=60)
        out = auto_library_cleanup({}, deps)
        assert out['recycle_purged'] == 0 and binned.exists()
        assert out['quarantine_removed'] == 1

    @pytest.mark.parametrize('off', [False, 'false', '0', 'off', ''])
    def test_the_quarantine_half_can_be_switched_off(self, tmp_path, off):
        deps, lines, qfile, binned = _deps_for(tmp_path)
        out = auto_library_cleanup({'quarantine': off}, deps)
        assert out['quarantine_removed'] == 0 and qfile.exists()
        assert out['recycle_purged'] == 1 and not binned.exists()

    def test_the_bin_half_can_be_switched_off(self, tmp_path):
        deps, lines, qfile, binned = _deps_for(tmp_path)
        out = auto_library_cleanup({'recycle_bin': False}, deps)
        assert out['recycle_purged'] == 0 and binned.exists()
        assert out['quarantine_removed'] == 1 and not qfile.exists()

    def test_both_off_is_a_skip_that_touches_nothing(self, tmp_path):
        deps, lines, qfile, binned = _deps_for(tmp_path)
        out = auto_library_cleanup({'quarantine': False, 'recycle_bin': False}, deps)
        assert out == {'status': 'skipped', 'reason': 'both steps switched off'}
        assert qfile.exists() and binned.exists()

    def test_paths_go_through_docker_resolve(self, tmp_path):
        # the config holds container paths; the handler must resolve them the
        # way every other handler does, or it sweeps the wrong folders
        deps, lines, qfile, binned = _deps_for(tmp_path)
        seen = []
        real_cfg = deps.config_manager
        deps = _build_deps(config_manager=real_cfg,
                           docker_resolve_path=lambda p: seen.append(p) or p)
        auto_library_cleanup({}, deps)
        assert seen == [real_cfg.get('soulseek.download_path'), real_cfg.get('soulseek.transfer_path')]

    def test_a_bad_keep_value_reads_as_keep_forever(self, tmp_path):
        deps, lines, qfile, binned = _deps_for(tmp_path, keep_days='lots')
        out = auto_library_cleanup({}, deps)
        assert out['recycle_purged'] == 1

    def test_a_crash_is_an_error_result_not_an_exception(self, tmp_path, monkeypatch):
        deps, lines, qfile, binned = _deps_for(tmp_path)
        import core.automation.handlers.library_cleanup as mod

        def _boom(**kw):
            raise RuntimeError('no disk')
        monkeypatch.setattr(mod, 'run_library_cleanup', _boom)
        out = auto_library_cleanup({'_automation_id': 7}, deps)
        assert out == {'status': 'error', 'error': 'no disk'}
        assert {'automation_id': 7, 'log_line': 'no disk', 'log_type': 'error'} in lines

    def test_one_bad_entry_counts_as_an_error_and_the_run_still_completes(self, tmp_path, monkeypatch):
        deps, lines, qfile, binned = _deps_for(tmp_path)
        from core.library import cleanup as core_cleanup
        real_remove = os.remove

        def _remove(p):
            if p.endswith('bad.flac'):
                raise PermissionError('locked')
            real_remove(p)
        monkeypatch.setattr(core_cleanup.os, 'remove', _remove)
        out = auto_library_cleanup({}, deps)
        assert out['status'] == 'completed'
        assert out['quarantine_removed'] == 0 and out['errors'] == 1
        assert out['recycle_purged'] == 1


# ── the seam: registration + the seeded row on a real database ───────────────

class _RecordingEngine:
    def __init__(self):
        self.handlers: Dict[str, Any] = {}

    def register_action_handler(self, name, handler, guard_fn=None):
        self.handlers[name] = handler

    def register_progress_callbacks(self, *a, **kw):
        pass


def test_register_all_wires_the_action_to_the_handler(tmp_path):
    engine = _RecordingEngine()
    deps, lines, qfile, binned = _deps_for(tmp_path)

    class _Scan:
        def add_scan_completion_callback(self, cb): pass
    deps = _build_deps(engine=engine, config_manager=deps.config_manager,
                       update_progress=deps.update_progress, web_scan_manager=_Scan())
    register_all(deps)
    assert 'library_cleanup' in engine.handlers
    out = engine.handlers['library_cleanup']({})
    assert out['status'] == 'completed' and out['removed'] == 2
    assert not qfile.exists() and not binned.exists()


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'automations.db'))


@pytest.fixture()
def engine(db):
    eng = AutomationEngine(db)
    eng._running = True
    eng.schedule_automation = lambda automation_id: None   # no live timers
    return eng


def test_the_spec_is_weekly_and_off_on_create():
    spec = next(s for s in SYSTEM_AUTOMATIONS if s['action_type'] == 'library_cleanup')
    assert spec['name'] == 'Weekly Cleanup'
    assert spec['trigger_type'] == 'schedule'
    assert spec['trigger_config'] == {'interval': 7, 'unit': 'days'}
    assert spec['enabled_on_create'] is False
    assert spec.get('owned_by') is None      # music page


def test_seeding_creates_weekly_cleanup_switched_off(engine, db):
    engine.ensure_system_automations()
    row = db.get_system_automation_by_action('library_cleanup')
    assert row, 'Weekly Cleanup was not seeded'
    assert row['name'] == 'Weekly Cleanup'
    assert row['is_system'] == 1
    assert not row['enabled']


def test_every_other_seed_still_comes_up_enabled(engine, db):
    # the off-on-create flag must not leak onto its neighbours
    engine.ensure_system_automations()
    for spec in SYSTEM_AUTOMATIONS:
        if spec['action_type'] == 'library_cleanup':
            continue
        row = db.get_system_automation_by_action(spec['action_type'])
        assert row and row['enabled'], f"{spec['name']} came up disabled"


def test_a_user_who_switched_it_on_stays_on_across_restarts(engine, db):
    engine.ensure_system_automations()
    row = db.get_system_automation_by_action('library_cleanup')
    assert db.toggle_automation(row['id'])
    assert db.get_automation(row['id'])['enabled']
    # next boot re-runs the seeder: the row exists, so it is left alone
    engine.ensure_system_automations()
    engine.ensure_system_automations()
    assert db.get_automation(row['id'])['enabled']
    assert db.get_automation(row['id'])['id'] == row['id']   # and not re-created
