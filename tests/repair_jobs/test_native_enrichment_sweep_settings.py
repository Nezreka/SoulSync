from core.repair_jobs.base import JobContext
from core.repair_jobs.native_enrichment_sweep import (
    DEFAULT_BATCH,
    NativeEnrichmentSweepJob,
)


class _Config:
    def __init__(self, settings):
        self.settings = settings

    def get(self, key, default=None):
        if key == 'repair.jobs.native_enrichment_sweep.settings':
            return self.settings
        return default


def _context(settings):
    return JobContext(
        db=None,
        transfer_folder='/tmp',
        config_manager=_Config(settings),
    )


def test_batch_size_comes_from_the_persisted_job_settings():
    assert NativeEnrichmentSweepJob()._batch_size(_context({'batch_size': 17})) == 17


def test_batch_size_falls_back_for_invalid_or_boolean_values():
    job = NativeEnrichmentSweepJob()
    assert job._batch_size(_context({'batch_size': 'broken'})) == DEFAULT_BATCH
    assert job._batch_size(_context({'batch_size': True})) == DEFAULT_BATCH


def test_batch_size_does_not_require_an_undeclared_context_attribute():
    context = _context({})
    assert not hasattr(context, 'settings')
    assert NativeEnrichmentSweepJob()._batch_size(context) == DEFAULT_BATCH
