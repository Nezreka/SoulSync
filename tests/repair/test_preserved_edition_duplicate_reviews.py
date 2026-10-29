"""Startup must retain review decisions from superseded catalogue jobs."""

import json
from types import SimpleNamespace

from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


def test_startup_preserves_and_annotates_old_edition_and_duplicate_reviews(tmp_path):
    db = MusicDatabase(str(tmp_path / 'reviews.db'))
    conn = db._get_connection()
    for job, kind in [('canonical_version_resolve', 'canonical_version'), ('duplicate_detector', 'duplicate_track')]:
        conn.execute("INSERT INTO repair_findings(job_id,finding_type,severity,status,entity_id,title,details_json) "
                     "VALUES(?,?,'info','pending','1','Review',?)", (job, kind, json.dumps({'source': 'spotify', 'album_id': 'old'})))
    conn.commit()
    conn.close()
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db

    worker._prune_retired_job_findings()
    worker._prune_stale_legacy_findings()
    worker._prune_retired_job_findings()

    conn = db._get_connection()
    rows = conn.execute('SELECT status,entity_id,details_json,last_error FROM repair_findings').fetchall()
    conn.close()
    assert len(rows) == 2
    assert all(row[0] == 'pending' and row[1] == '1' for row in rows)
    assert all(json.loads(row[2])['legacy_review_required'] for row in rows)
    assert all('retained' in row[3] for row in rows)
    assert not worker._fix_canonical_version('album', '1', None, {'source': 'spotify', 'album_id': 'old'})['success']


def test_saved_edition_automation_resolves_to_findings_only_successor():
    class Config:
        def __init__(self):
            self.values = {'repair.jobs.canonical_version_resolve': {
                'enabled': True, 'interval_hours': 48,
                'settings': {'dry_run': False, 'min_score': 0.8, 'source_selection': 'best_fit'},
            }}
        def get(self, key, default=None):
            return self.values.get(key, default)
        def set(self, key, value):
            self.values[key] = value

    cfg = Config()
    worker = RepairWorker.__new__(RepairWorker)
    worker._migrate_legacy_job_configs(cfg)
    assert cfg.values['repair.jobs.album_edition_review'] == cfg.values['repair.jobs.canonical_version_resolve']
    from core.repair_jobs import get_all_jobs, JOB_ID_MIGRATIONS
    cls = get_all_jobs()[JOB_ID_MIGRATIONS['canonical_version_resolve']]
    assert cls.auto_fix is False
