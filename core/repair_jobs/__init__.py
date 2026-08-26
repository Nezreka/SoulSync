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
    'mbid_mismatch_detector': 'lib2',
    'native_enrichment_sweep': 'lib2',
    'replaygain_filler': 'lib2',
    'empty_folder_cleaner': 'filesystem',
    'metadata_gap_filler': 'lib2',
    'fake_lossless_detector': 'lib2',
    'lossy_converter': 'lib2',
    'album_tag_consistency': 'lib2',
    'live_commentary_cleaner': 'lib2',
    'short_preview_track': 'lib2',
    'skip_audit_cleanup': 'lib2',
    'monitored_discography_refresh': 'lib2',
    'audio_corruption_detector': 'lib2',
    'monitoring_list_reconcile': 'lib2',
    'quality_info_backfill': 'lib2',
    'expired_download_cleaner': 'filesystem',
    'library_reorganize': 'lib2',
    'genre_cleanup': 'lib2',
    'genre_enrichment': 'lib2',
    'comma_artist_splitter': 'lib2',
    'path_drift_reconcile': 'lib2',
    # Reborn on the lib2 retag engine — the retired legacy job read the
    # albums/artists/tracks tables this branch removed.
    'library_retag': 'lib2',
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
    # Reads embedded MusicBrainz ids and, on fix, strips or rewrites the
    # tag. Never moves, deletes or re-indexes a file.
    'mbid_mismatch_detector': frozenset({'observe', 'tags'}),
    # Writes provider ids, artwork and descriptive columns onto lib2
    # rows. Touches no file and changes nothing about what is wanted.
    'native_enrichment_sweep': frozenset({'metadata', 'artwork'}),
    'replaygain_filler': frozenset({'observe', 'tags'}),
    'empty_folder_cleaner': frozenset({'none'}),
    'metadata_gap_filler': frozenset({'observe', 'metadata', 'tags'}),
    'fake_lossless_detector': frozenset({'observe'}),
    'lossy_converter': frozenset({'observe', 'new_file', 'tags'}),
    'album_tag_consistency': frozenset({'observe', 'metadata', 'tags'}),
    'live_commentary_cleaner': frozenset({'observe', 'delete', 'wanted'}),
    'short_preview_track': frozenset({'observe', 'delete', 'wanted'}),
    'skip_audit_cleanup': frozenset({'none'}),
    'monitored_discography_refresh': frozenset({'discography', 'wanted'}),
    'audio_corruption_detector': frozenset({'observe', 'delete', 'wanted'}),
    'monitoring_list_reconcile': frozenset({'wanted'}),
    # Only re-probes and fills already-NULL bitrate/sample_rate/bit_depth/
    # quality_tier columns on existing rows — a catalogue metadata update,
    # nothing file/wanted/artwork related.
    'quality_info_backfill': frozenset({'metadata'}),
    'expired_download_cleaner': frozenset({'delete', 'wanted'}),
    'library_reorganize': frozenset({'observe', 'path'}),
    # Rewrites artists.genres / albums.genres to the kept (whitelisted) list.
    'genre_cleanup': frozenset({'observe', 'metadata'}),
    'genre_enrichment': frozenset({'observe', 'metadata'}),
    # Re-tags the affected files' embedded artist fields; the DB artist row
    # itself isn't touched by the fix.
    'comma_artist_splitter': frozenset({'observe', 'tags'}),
    # Repoints a stale index row at a file that is already on disk. 'path' is
    # the stored-path change; no file is ever moved, created or deleted.
    'path_drift_reconcile': frozenset({'observe', 'path'}),
    # Reports tag drift; applying writes the catalogue's values into the file.
    # No row moves, nothing is created or deleted.
    'library_retag': frozenset({'observe', 'tags'}),
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
    'album_completeness',
    # NOT 'library_reorganize': unlike the other entries here, nothing native
    # regenerates its 'path_mismatch' findings (core.library2.maintenance_sync
    # .sync_repair_change only mirrors lib2 file/path state under this same
    # job_id, it never calls create_finding). Pruning this one on every worker
    # start silently deletes pending admin-review findings with no
    # replacement — see docs/library-overhaul-branch-review-2026-07-19.md A2.
    'single_album_dedup',
    'unknown_artist_fixer',
    'canonical_version_resolve',
    'lib2_mirror_reconcile',
    'lib2_wishlist_reconcile',
    # Stable P1/P2 identities renamed neutrally at the P3 boundary.
    'lib2_upgrade_scan',
    'lib2_skips_cleanup',
    'lib2_discography_refresh',
    # Retired because `monitoring_list_reconcile` already queued every upgrade
    # candidate on its own hourly pass, whether this job ran or not. Its review
    # half was defeated by exactly that, so there was no configuration in which
    # both jobs made sense at once.
    'quality_upgrade_scan',
})

# Read-only compatibility for saved settings/automation references.  Runtime
# registration and API responses expose only the neutral identities.
JOB_ID_MIGRATIONS = {
    # Stable pre-V2 ids remain accepted by saved automations/API callers.
    # The quality-upgrade lineage has no live successor to migrate INTO:
    # queueing upgrades is not a job any more, it is what the wanted
    # projection does continuously. Their saved configs go inert rather than
    # being folded into an unrelated job's enabled/interval.
    'discography_backfill': 'monitored_discography_refresh',
    'lib2_skips_cleanup': 'skip_audit_cleanup',
    'lib2_discography_refresh': 'monitored_discography_refresh',
    # The two transitional jobs return as one neutral, complete invariant
    # repair (pending outbox + Artist/Watchlist + Track/Wishlist).
    'lib2_mirror_reconcile': 'monitoring_list_reconcile',
    'lib2_wishlist_reconcile': 'monitoring_list_reconcile',
}

# These retired jobs produced review findings whose replacement must be
# approved by the user. Keep their pending rows and service them through the
# compatibility fix handlers; only implementation identities with a fully
# regenerating replacement may be pruned at startup.
PRESERVED_RETIRED_FINDING_IDS = frozenset({
    'quality_upgrade_scanner',
    'quality_upgrade',
    # Nothing regenerates a `quality_below_cutoff` finding any more, so the
    # ones already sitting in the queue are the last of their kind. They stay
    # approvable through `_fix_quality_below_cutoff` instead of being pruned
    # out from under a user who was midway through reviewing them.
    'quality_upgrade_scan',
    'discography_backfill',
})

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
    'core.repair_jobs.quality_info_backfill',
    'core.repair_jobs.dead_file_cleaner',
    'core.repair_jobs.acoustid_scanner',
    'core.repair_jobs.missing_cover_art',
    'core.repair_jobs.mbid_mismatch_detector',
    'core.repair_jobs.native_enrichment_sweep',
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
    'core.repair_jobs.genre_cleanup',
    'core.repair_jobs.genre_enrichment',
    'core.repair_jobs.comma_artist_splitter',
    'core.repair_jobs.lib2_skips_cleanup',
    'core.repair_jobs.lib2_discography_refresh',
    'core.repair_jobs.monitoring_list_reconcile',
    'core.repair_jobs.expired_download_cleaner',
    'core.repair_jobs.library_reorganize',
    'core.repair_jobs.library_retag',
    'core.repair_jobs.path_drift_reconcile',
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
