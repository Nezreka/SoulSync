"""Metadata Cache Evictor Job — periodically cleans expired cache entries."""

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_job.cache_evictor")


@register_job
class CacheEvictorJob(RepairJob):
    job_id = 'cache_evictor'
    display_name = 'Cache Evictor'
    description = 'Removes expired metadata cache entries'
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

        try:
            evicted = cache.evict_expired()
            result.auto_fixed = evicted
            result.scanned = evicted
            logger.info("Cache evictor: removed %d expired entries", evicted)
        except Exception as e:
            logger.error("Cache eviction failed: %s", e, exc_info=True)
            result.errors = 1

        return result
