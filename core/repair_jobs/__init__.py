"""Repair Jobs Registry — all available maintenance jobs for the Library Worker."""

import importlib

from core.repair_jobs.base import RepairJob, JobContext, JobResult
from utils.logging_config import get_logger

logger = get_logger("repair_jobs")

# Registry populated at import time by each job module
JOB_REGISTRY: dict[str, type[RepairJob]] = {}

_imports_done = False


def register_job(cls: type[RepairJob]) -> type[RepairJob]:
    """Decorator to register a RepairJob subclass."""
    JOB_REGISTRY[cls.job_id] = cls
    return cls


def get_all_jobs() -> dict[str, type[RepairJob]]:
    """Return the full job registry. Ensures all job modules are imported."""
    _import_all_jobs()
    return JOB_REGISTRY


_JOB_MODULES = [
    'core.repair_jobs.track_number_repair',
    'core.repair_jobs.cache_evictor',
    'core.repair_jobs.orphan_file_detector',
    'core.repair_jobs.dead_file_cleaner',
    'core.repair_jobs.duplicate_detector',
    'core.repair_jobs.acoustid_scanner',
    'core.repair_jobs.missing_cover_art',
    'core.repair_jobs.metadata_gap_filler',
    'core.repair_jobs.album_completeness',
    'core.repair_jobs.fake_lossless_detector',
    'core.repair_jobs.library_reorganize',
    'core.repair_jobs.mbid_mismatch_detector',
    'core.repair_jobs.single_album_dedup',
    'core.repair_jobs.lossy_converter',
]


def _import_all_jobs():
    """Import all job modules to trigger registration.

    Each module is imported individually so that a failure in one
    does not prevent the others from loading.
    """
    global _imports_done
    if _imports_done:
        return
    _imports_done = True

    for module_name in _JOB_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as e:
            logger.error("Failed to import job module %s: %s", module_name, e)
