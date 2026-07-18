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
    'library_reorganize': 'mixed',
    'mbid_mismatch_detector': 'mixed',
    'single_album_dedup': 'legacy',
    'lossy_converter': 'mixed',
    'album_tag_consistency': 'mixed',
    'live_commentary_cleaner': 'legacy',
    'unknown_artist_fixer': 'mixed',
    'canonical_version_resolve': 'legacy',
    'library_retag': 'mixed',
    'short_preview_track': 'legacy',
    'lib2_upgrade_scan': 'lib2',
    'lib2_skips_cleanup': 'lib2',
    'lib2_discography_refresh': 'lib2',
    'lib2_mirror_reconcile': 'lib2',
    'lib2_wishlist_reconcile': 'lib2',
    'audio_corruption_detector': 'mixed',
}

# Exhaustive Library-v2 interoperability contract.  ``JOB_DATA_BASIS`` says
# where a job currently reads; this manifest says what a successful run/fix can
# change and therefore what the optional Library-v2 bridge must reconcile.  It
# deliberately lives next to the registry so adding a job without considering
# Library v2 fails at import time instead of silently shipping another stale
# cache/path/history boundary.
LIBRARY_V2_EFFECTS = frozenset({
    'none',          # operational/cache-only; no music-library state changes
    'observe',       # findings only; subjects still need lib2 identity links
    'metadata',      # artist/album/track catalogue fields or provider ids
    'tags',          # embedded tags / lyrics / ReplayGain / verification tag
    'artwork',       # embedded/sidecar/provider artwork and lib2 art cache
    'path',          # file rename/move
    'new_file',      # a new derivative/imported file may be created
    'delete',        # file or legacy row may be removed
    'wanted',        # wishlist/upgrade/monitor projection changes
    'discography',   # provider catalogue expansion/backfill
})

JOB_LIBRARY_V2_EFFECTS: dict[str, frozenset[str]] = {
    'track_number_repair': frozenset({'metadata', 'tags', 'path'}),
    'cache_evictor': frozenset({'none'}),
    'orphan_file_detector': frozenset({'observe', 'path', 'new_file', 'delete'}),
    'dead_file_cleaner': frozenset({'observe', 'delete'}),
    'duplicate_detector': frozenset({'observe', 'delete'}),
    'acoustid_scanner': frozenset({'observe', 'tags', 'metadata'}),
    'missing_cover_art': frozenset({'observe', 'metadata', 'tags', 'artwork'}),
    'missing_lyrics': frozenset({'observe', 'tags'}),
    'replaygain_filler': frozenset({'observe', 'tags'}),
    'empty_folder_cleaner': frozenset({'none'}),
    'expired_download_cleaner': frozenset({'observe', 'delete', 'wanted'}),
    'metadata_gap_filler': frozenset({'observe', 'metadata', 'tags'}),
    'album_completeness': frozenset({'observe', 'metadata', 'tags', 'new_file', 'wanted'}),
    'fake_lossless_detector': frozenset({'observe'}),
    'library_reorganize': frozenset({'observe', 'metadata', 'tags', 'path'}),
    'mbid_mismatch_detector': frozenset({'observe', 'metadata', 'tags'}),
    'single_album_dedup': frozenset({'observe', 'delete', 'wanted'}),
    'lossy_converter': frozenset({'observe', 'new_file', 'tags'}),
    'album_tag_consistency': frozenset({'observe', 'metadata', 'tags'}),
    'live_commentary_cleaner': frozenset({'observe', 'delete', 'wanted'}),
    'unknown_artist_fixer': frozenset({'observe', 'metadata', 'tags', 'artwork', 'path'}),
    'canonical_version_resolve': frozenset({'observe', 'metadata'}),
    'library_retag': frozenset({'observe', 'metadata', 'tags', 'artwork'}),
    'short_preview_track': frozenset({'observe', 'delete', 'wanted'}),
    'lib2_upgrade_scan': frozenset({'observe', 'wanted'}),
    'lib2_skips_cleanup': frozenset({'none'}),
    'lib2_discography_refresh': frozenset({'discography', 'wanted'}),
    'lib2_mirror_reconcile': frozenset({'wanted'}),
    'lib2_wishlist_reconcile': frozenset({'wanted'}),
    'audio_corruption_detector': frozenset({'observe', 'delete', 'wanted'}),
}

# Jobs deliberately retired after their function moved to a native Library-v2
# engine (P2 consolidation). Listed explicitly so the worker can prune their
# leftover pending findings deterministically — never inferred from "not in
# registry", which would also hit jobs that merely failed to import.
RETIRED_JOB_IDS = frozenset({
    'quality_upgrade_scanner',  # -> lib2_upgrade_scan mode='review'
    'quality_upgrade',          # -> lib2_upgrade_scan mode='automatic'
    'discography_backfill',     # -> lib2_discography_refresh + Wanted views
})

_imports_done = False


def register_job(cls: type[RepairJob]) -> type[RepairJob]:
    """Decorator to register a RepairJob subclass."""
    basis = JOB_DATA_BASIS.get(cls.job_id)
    if basis not in REPAIR_DATA_BASES:
        raise ValueError(f"Repair job {cls.job_id!r} has no valid data-basis declaration")
    effects = JOB_LIBRARY_V2_EFFECTS.get(cls.job_id)
    if not effects or not effects.issubset(LIBRARY_V2_EFFECTS):
        raise ValueError(
            f"Repair job {cls.job_id!r} has no valid Library-v2 effects declaration"
        )
    if 'none' in effects and len(effects) != 1:
        raise ValueError(
            f"Repair job {cls.job_id!r} mixes the 'none' Library-v2 effect with mutations"
        )
    cls.data_basis = basis
    cls.library_v2_effects = effects
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
    'core.repair_jobs.library_reorganize',
    'core.repair_jobs.mbid_mismatch_detector',
    'core.repair_jobs.single_album_dedup',
    'core.repair_jobs.lossy_converter',
    'core.repair_jobs.album_tag_consistency',
    'core.repair_jobs.live_commentary_cleaner',
    'core.repair_jobs.unknown_artist_fixer',
    'core.repair_jobs.canonical_version_resolve',
    'core.repair_jobs.library_retag',
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
