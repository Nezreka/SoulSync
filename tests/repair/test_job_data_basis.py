from types import SimpleNamespace

from core.repair_jobs import (
    JOB_DATA_BASIS,
    REPAIR_DATA_BASES,
    get_all_jobs,
)
from core.repair_worker import RepairWorker


def test_every_registered_repair_job_has_an_explicit_valid_data_basis():
    jobs = get_all_jobs()

    assert set(JOB_DATA_BASIS) == set(jobs)
    assert {job.data_basis for job in jobs.values()} <= REPAIR_DATA_BASES
    assert all(job.data_basis == JOB_DATA_BASIS[job_id] for job_id, job in jobs.items())


def test_representative_job_data_bases_are_deliberate():
    assert JOB_DATA_BASIS['metadata_gap_filler'] == 'legacy'
    assert JOB_DATA_BASIS['lib2_upgrade_scan'] == 'lib2'
    assert JOB_DATA_BASIS['empty_folder_cleaner'] == 'filesystem'
    assert JOB_DATA_BASIS['library_retag'] == 'mixed'


def test_worker_job_info_exposes_data_basis(monkeypatch):
    worker = RepairWorker.__new__(RepairWorker)
    worker._jobs = {'metadata_gap_filler': get_all_jobs()['metadata_gap_filler']()}
    worker._current_job_id = None
    worker.db = SimpleNamespace()
    monkeypatch.setattr(worker, '_ensure_jobs_loaded', lambda: None)
    monkeypatch.setattr(worker, '_get_pending_count_by_job', lambda: {})
    monkeypatch.setattr(
        worker,
        'get_job_config',
        lambda _job_id: {'enabled': False, 'interval_hours': 24, 'settings': {}},
    )
    monkeypatch.setattr(worker, '_get_last_run', lambda _job_id: None)

    assert worker.get_all_job_info()[0]['data_basis'] == 'legacy'
