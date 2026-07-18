"""Repair Jobs Registry — all available maintenance jobs for the Library Worker."""

import importlib

from core.repair_jobs.base import RepairJob, JobContext, JobResult
from utils.logging_config import get_logger

logger = get_logger("repair_jobs")

# Registry populated at import time by each job module
JOB_REGISTRY: dict[str, type[RepairJob]] = {}

# P3 invariant: a registered catalogue job reads Library v2.  Pure operational
# jobs may read the filesystem only; the old ``legacy``/``mixed`` bases are no
# longer valid registration choices.
REPAIR_DATA_BASES = frozenset({'lib2', 'filesystem'})
JOB_DATA_BASIS: dict[str, str] = {
    'track_number_repair': 'lib2',
    'cache_evictor': 'filesystem',
    'orphan_file_detector': 'lib2',
    'dead_file_cleaner': 'lib2',
    'acoustid_scanner': 'lib2',
    'missing_cover_art': 'lib2',
    'missing_lyrics': 'lib2',
    'replaygain_filler': 'lib2',
    'empty_folder_cleaner': 'filesystem',
    'metadata_gap_filler': 'lib2',
    'fake_lossless_detector': 'lib2',
    'lossy_converter': 'lib2',
    'album_tag_consistency': 'lib2',
    'live_commentary_cleaner': 'lib2',
    'short_preview_track': 'lib2',
    'quality_upgrade_scan': 'lib2',
    'skip_audit_cleanup': 'lib2',
    'monitored_discography_refresh': 'lib2',
    'audio_corruption_detector': 'lib2',
}

# Exhaustive Library-v2 interoperability contract.  ``JOB_DATA_BASIS`` says
# where a job currently reads; this manifest says what a successful run/fix can
# change and therefore what the native Library-v2 lifecycle must reconcile. It
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
    'delete',        # file or native catalogue row may be removed
    'wanted',        # wishlist/upgrade/monitor projection changes
    'discography',   # provider catalogue expansion/backfill
})

JOB_LIBRARY_V2_EFFECTS: dict[str, frozenset[str]] = {
    'track_number_repair': frozenset({'metadata', 'tags', 'path'}),
    'cache_evictor': frozenset({'none'}),
    'orphan_file_detector': frozenset({'observe', 'path', 'new_file', 'delete'}),
    'dead_file_cleaner': frozenset({'observe', 'delete'}),
    'acoustid_scanner': frozenset({'observe', 'tags', 'metadata'}),
    'missing_cover_art': frozenset({'observe', 'metadata', 'tags', 'artwork'}),
    'missing_lyrics': frozenset({'observe', 'tags'}),
    'replaygain_filler': frozenset({'observe', 'tags'}),
    'empty_folder_cleaner': frozenset({'none'}),
    'metadata_gap_filler': frozenset({'observe', 'metadata', 'tags'}),
    'fake_lossless_detector': frozenset({'observe'}),
    'lossy_converter': frozenset({'observe', 'new_file', 'tags'}),
    'album_tag_consistency': frozenset({'observe', 'metadata', 'tags'}),
    'live_commentary_cleaner': frozenset({'observe', 'delete', 'wanted'}),
    'short_preview_track': frozenset({'observe', 'delete', 'wanted'}),
    'quality_upgrade_scan': frozenset({'observe', 'wanted'}),
    'skip_audit_cleanup': frozenset({'none'}),
    'monitored_discography_refresh': frozenset({'discography', 'wanted'}),
    'audio_corruption_detector': frozenset({'observe', 'delete', 'wanted'}),
}

# Jobs deliberately retired after their function moved to a native Library-v2
# engine (P2 consolidation). Listed explicitly so the worker can prune their
# leftover pending findings deterministically — never inferred from "not in
# registry", which would also hit jobs that merely failed to import.
RETIRED_JOB_IDS = frozenset({
    'quality_upgrade_scanner',
    'quality_upgrade',
    'discography_backfill',
    'duplicate_detector',
    'expired_download_cleaner',
    'album_completeness',
    'library_reorganize',
    'mbid_mismatch_detector',
    'single_album_dedup',
    'unknown_artist_fixer',
    'canonical_version_resolve',
    'library_retag',
    'lib2_mirror_reconcile',
    'lib2_wishlist_reconcile',
    # Stable P1/P2 identities renamed neutrally at the P3 boundary.
    'lib2_upgrade_scan',
    'lib2_skips_cleanup',
    'lib2_discography_refresh',
})

# Read-only compatibility for saved settings/automation references.  Runtime
# registration and API responses expose only the neutral identities.
JOB_ID_MIGRATIONS = {
    'lib2_upgrade_scan': 'quality_upgrade_scan',
    'lib2_skips_cleanup': 'skip_audit_cleanup',
    'lib2_discography_refresh': 'monitored_discography_refresh',
}

_imports_done = False


def register_job(cls: type[RepairJob]) -> type[RepairJob]:
    """Decorator to register a RepairJob subclass."""
    # Retired modules remain importable during the rollback window and for
    # focused algorithm tests, but importing one must never re-introduce its
    # superseded job identity into the runtime registry.
    if cls.job_id in RETIRED_JOB_IDS:
        return cls
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
    'core.repair_jobs.acoustid_scanner',
    'core.repair_jobs.missing_cover_art',
    'core.repair_jobs.missing_lyrics',
    'core.repair_jobs.replaygain_filler',
    'core.repair_jobs.empty_folder_cleaner',
    'core.repair_jobs.metadata_gap_filler',
    'core.repair_jobs.fake_lossless_detector',
    'core.repair_jobs.lossy_converter',
    'core.repair_jobs.album_tag_consistency',
    'core.repair_jobs.live_commentary_cleaner',
    'core.repair_jobs.short_preview_track',
    'core.repair_jobs.audio_corruption_detector',
    'core.repair_jobs.lib2_upgrade_scan',
    'core.repair_jobs.lib2_skips_cleanup',
    'core.repair_jobs.lib2_discography_refresh',
    # Overrides mature job identities with P3-native catalogue boundaries.
    'core.repair_jobs.native_p3',
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
