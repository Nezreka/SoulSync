"""Metadata Cache Maintenance Job — cleans expired, junk, and orphaned cache entries."""

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.cache_evictor")


@register_job
class CacheEvictorJob(RepairJob):
    job_id = 'cache_evictor'
    display_name = 'Cache Maintenance'
    description = 'Removes expired, junk, and orphaned metadata cache entries'
    help_text = (
        'Maintains the metadata cache that stores search results, album metadata, '
        'and cover art URLs from Spotify, iTunes, and Deezer.\n\n'
        'Runs four maintenance phases:\n'
        '1. TTL eviction — removes entries past their expiration date\n'
        '2. Junk cleanup — removes entries with empty or placeholder names (Unknown Artist, etc.)\n'
        '3. Orphan cleanup — removes search results pointing to deleted entities\n'
        '4. MusicBrainz cleanup — removes stale "not found" entries older than 30 days\n\n'
        'Fully automatic — runs silently without creating findings. Safe to leave enabled.'
    )
    icon = 'repair-icon-cache'
    default_enabled = True
    default_interval_hours = 6
    default_settings = {}
    auto_fix = True

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()

        cache = context.metadata_cache
        if not cache:
            logger.debug("No metadata cache available — skipping")
            return result

        # Phase 1: Evict expired entries (existing behavior)
        try:
            evicted = cache.evict_expired()
            result.auto_fixed += evicted
            result.scanned += evicted
            if evicted > 0:
                logger.info("Phase 1 — TTL eviction: removed %d expired entries", evicted)
        except Exception as e:
            logger.error("TTL eviction failed: %s", e, exc_info=True)
            result.errors += 1

        if context.check_stop():
            return result

        # Phase 2: Clean junk entities (empty/placeholder names)
        try:
            junk = cache.clean_junk_entities()
            result.auto_fixed += junk
            result.scanned += junk
            if junk > 0:
                logger.info("Phase 2 — junk cleanup: removed %d junk entries", junk)
        except Exception as e:
            logger.error("Junk cleanup failed: %s", e, exc_info=True)
            result.errors += 1

        if context.check_stop():
            return result

        # Phase 3: Clean orphaned search results
        try:
            orphans = cache.clean_orphaned_searches()
            result.auto_fixed += orphans
            result.scanned += orphans
            if orphans > 0:
                logger.info("Phase 3 — orphan cleanup: removed %d orphaned searches", orphans)
        except Exception as e:
            logger.error("Orphan cleanup failed: %s", e, exc_info=True)
            result.errors += 1

        if context.check_stop():
            return result

        # Phase 4: Clean stale MusicBrainz null results (failed lookups > 30 days)
        try:
            mb_nulls = cache.clean_stale_musicbrainz_nulls(max_age_days=30)
            result.auto_fixed += mb_nulls
            result.scanned += mb_nulls
            if mb_nulls > 0:
                logger.info("Phase 4 — MB null cleanup: removed %d stale null entries", mb_nulls)
        except Exception as e:
            logger.error("MB null cleanup failed: %s", e, exc_info=True)
            result.errors += 1

        return result
