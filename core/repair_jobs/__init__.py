"""Repair Jobs Registry — all available maintenance jobs for the Library Worker."""

import importlib

from core.repair_jobs.base import RepairJob, JobContext, JobResult
from utils.logging_config import get_logger

logger = get_logger("repair_jobs")

# Registry populated at import time by each job module
JOB_REGISTRY: dict[str, type[RepairJob]] = {}

# Exhaustive, reviewable declaration of which catalogue/data source each job
# scans. ``mixed`` means the legacy catalogue plus files on disk; provider
# lookups do not change a job's catalogue basis. Operational cache/directory
# jobs are classified as ``filesystem`` because they do not scan either music
# catalogue. Registration fails when a new job has no deliberate entry here.
REPAIR_DATA_BASES = frozenset({'legacy', 'lib2', 'filesystem', 'mixed'})
JOB_DATA_BASIS: dict[str, str] = {
    'track_number_repair': 'mixed',
    'cache_evictor': 'filesystem',
    'orphan_file_detector': 'mixed',
    'dead_file_cleaner': 'mixed',
    'duplicate_detector': 'legacy',
    'acoustid_scanner': 'mixed',
    'missing_cover_art': 'mixed',
    'missing_lyrics': 'mixed',
    'replaygain_filler': 'mixed',
    'empty_folder_cleaner': 'filesystem',
    'expired_download_cleaner': 'mixed',
    'metadata_gap_filler': 'legacy',
    'album_completeness': 'legacy',
    'fake_lossless_detector': 'filesystem',
    'quality_upgrade_scanner': 'mixed',
    'library_reorganize': 'mixed',
    'mbid_mismatch_detector': 'mixed',
    'single_album_dedup': 'legacy',
    'lossy_converter': 'mixed',
    'album_tag_consistency': 'mixed',
    'live_commentary_cleaner': 'legacy',
    'unknown_artist_fixer': 'mixed',
    'discography_backfill': 'legacy',
    'canonical_version_resolve': 'legacy',
    'library_retag': 'mixed',
    'quality_upgrade': 'mixed',
    'short_preview_track': 'legacy',
    'lib2_upgrade_scan': 'lib2',
    'lib2_skips_cleanup': 'lib2',
    'lib2_discography_refresh': 'lib2',
    'lib2_mirror_reconcile': 'lib2',
    'lib2_wishlist_reconcile': 'lib2',
    'audio_corruption_detector': 'mixed',
}

_imports_done = False


def register_job(cls: type[RepairJob]) -> type[RepairJob]:
    """Decorator to register a RepairJob subclass."""
    basis = JOB_DATA_BASIS.get(cls.job_id)
    if basis not in REPAIR_DATA_BASES:
        raise ValueError(f"Repair job {cls.job_id!r} has no valid data-basis declaration")
    cls.data_basis = basis
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
    'core.repair_jobs.missing_lyrics',
    'core.repair_jobs.replaygain_filler',
    'core.repair_jobs.empty_folder_cleaner',
    'core.repair_jobs.expired_download_cleaner',
    'core.repair_jobs.metadata_gap_filler',
    'core.repair_jobs.album_completeness',
    'core.repair_jobs.fake_lossless_detector',
    'core.repair_jobs.quality_upgrade_scanner',
    'core.repair_jobs.library_reorganize',
    'core.repair_jobs.mbid_mismatch_detector',
    'core.repair_jobs.single_album_dedup',
    'core.repair_jobs.lossy_converter',
    'core.repair_jobs.album_tag_consistency',
    'core.repair_jobs.live_commentary_cleaner',
    'core.repair_jobs.unknown_artist_fixer',
    'core.repair_jobs.discography_backfill',
    'core.repair_jobs.canonical_version_resolve',
    'core.repair_jobs.library_retag',
    'core.repair_jobs.quality_upgrade',
    'core.repair_jobs.short_preview_track',
    'core.repair_jobs.audio_corruption_detector',
    'core.repair_jobs.lib2_upgrade_scan',
    'core.repair_jobs.lib2_skips_cleanup',
    'core.repair_jobs.lib2_discography_refresh',
    'core.repair_jobs.lib2_mirror_reconcile',
    'core.repair_jobs.lib2_wishlist_reconcile',
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
