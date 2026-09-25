"""Native enrichment sweep — the twelve workers' equivalent for lib2 artists.

iss32-E02. Nezreka: *"artists that came from the old library get all twelve
workers. artists created in lib2 (featured credits, wishlist, discography) have
no legacy row so they only get native_enrich, which does provider id, artwork
and genres. both show as matched so you can't tell them apart."*

He is right about the cause and half right about the depth. The native path
(``core.library2.native_enrich``) can already resolve an entity against *every*
provider that supports it and write artwork, genres, summary, style, mood and
label straight onto the lib2 row. What was missing is that **nothing ever ran
it**: it fires on API events — bookmark, manual match, materialize — and never
again. An artist that arrived as a featured credit two months ago has been
sitting at whatever coverage its creating event happened to produce.

The twelve legacy workers are continuous loops over the legacy tables. Their
native counterpart is a scheduled job over the catalogue, which is where every
other migrated tool ended up (P1/P2). Hence this.

**Deliberately not a legacy shim.** Creating a legacy ``artists`` row for a
native artist would have been the cheap fix and would have made the legacy
table a mandatory stop on the way into lib2 — the exact opposite of where the
library is going (docs §32.3.1). Nothing here writes a legacy row.

**Still open after this**, and honestly so: the provider *bios* (Last.fm
bio/listeners/similar, Genius description, Discogs members) live in
``lib2_artists.enrichment`` and are written only by the Last.fm/Genius/Discogs
workers, against legacy rows. Reaching them natively means porting those three
workers, which is Stufe 2. Until then a native artist can be short exactly
those fields, and ``enrichment_depth`` on the artist read says so rather than
letting the UI claim parity.
"""

from __future__ import annotations

from core.repair_jobs import register_job
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from utils.logging_config import get_logger

logger = get_logger("repair_jobs.native_enrichment_sweep")

# One provider walk is a handful of rate-limited HTTP calls, so a scheduled run
# takes a bite rather than the whole library. The scan is ordered and the
# predicate shrinks as entities resolve, so consecutive runs make progress.
DEFAULT_BATCH = 200

# The per-provider gap pass is a second, independent budget: it walks every
# owned artist, album and track rather than the handful with no provider id at
# all, so a run has to take a smaller bite of a much larger backlog.
DEFAULT_BACKFILL_BATCH = 100


def _configured_services(config_manager) -> set:
    """Providers this instance actually has configured.

    Walking an unconfigured provider is not merely wasted work: Tidal's client
    starts an interactive login, which is how a background enrich once popped
    an OAuth tab in a user's browser (see schedule_native_entity_enrich).
    """
    try:
        from web_server import _library_v2_configured_match_services

        services = _library_v2_configured_match_services()
        return set(services) if services else None
    except Exception:  # noqa: BLE001 - fall back to letting each client decide
        return None


@register_job
class NativeEnrichmentSweepJob(RepairJob):
    job_id = 'native_enrichment_sweep'
    display_name = 'Native Artist Enrichment'
    description = 'Resolves provider IDs, artwork and genres for artists that only exist in the new library'
    help_text = (
        'Artists that were created inside the new library — from a featured credit, '
        'a wishlist entry, or a discography browse — have no row in the old library '
        'tables, so the twelve metadata workers never see them.\n\n'
        'This job walks those artists and resolves them against every provider you have '
        'configured, writing provider IDs, artwork, genres and descriptive metadata '
        'straight onto the library entry. It is the new library\'s equivalent of the '
        'metadata workers, and it never creates an old-library row to get there.\n\n'
        'It then fills per-provider gaps across the rest of the library: an artist, '
        'album or track that one source matched and another did not. An entry that '
        'Spotify knows is not "done" while MusicBrainz has never been asked about '
        'it — and a missing MusicBrainz ID is what leaves the AcoustID check without '
        'the alternate spellings it needs to recognise a non-Latin artist name.\n\n'
        'Rate-limited by the providers themselves, and processed in batches, so a large '
        'backlog is worked off across several runs rather than in one long burst.'
    )
    icon = 'repair-icon-enrichment'
    # ON by default, unlike every other job in this package — and the exception
    # is the point. The other jobs are opt-in diagnostics; nothing is broken if
    # they never run. This one is the native replacement for twelve metadata
    # workers that run continuously against the legacy tables. Shipping it off
    # would mean answering "v2 should get filled the same way v1 does now" with
    # a switch in the off position, i.e. leaving the reported regression in
    # place behind a setting nobody knows to flip (iss32-E02).
    default_enabled = True
    default_interval_hours = 24
    default_settings = {
        'batch_size': DEFAULT_BATCH,
        'backfill_batch_size': DEFAULT_BACKFILL_BATCH,
    }
    auto_fix = True

    def _setting(self, context: JobContext, key: str, default: int) -> int:
        settings = {}
        if context.config_manager:
            raw = context.config_manager.get(
                'repair.jobs.native_enrichment_sweep.settings', {})
            if isinstance(raw, dict):
                settings = raw
        try:
            value = settings.get(key, default)
            if isinstance(value, bool):
                raise ValueError
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    def _batch_size(self, context: JobContext) -> int:
        return self._setting(context, 'batch_size', DEFAULT_BATCH)

    def _backfill_size(self, context: JobContext) -> int:
        return self._setting(context, 'backfill_batch_size', DEFAULT_BACKFILL_BATCH)

    def _pending(self, conn, limit: int):
        """Native artists still short of a catalog provider id."""
        from core.library2.native_enrich import _pending_unmapped_artists

        return [
            row for row in _pending_unmapped_artists(conn, None)
            if row.get("legacy_artist_id") is None
        ][:limit]

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        limit = self._batch_size(context)
        services = _configured_services(context.config_manager)

        conn = None
        try:
            conn = context.db._get_connection()
            pending = self._pending(conn, limit)
        except Exception as e:  # noqa: BLE001
            logger.error("native enrichment sweep: subject enumeration failed: %s", e)
            result.errors += 1
            if conn:
                conn.close()
            return result

        total = len(pending)
        if context.update_progress:
            context.update_progress(0, total)
        if context.report_progress:
            context.report_progress(
                phase=f'Enriching {total} native artist(s)...', total=total)

        backfilled = None
        try:
            from core.library2.native_enrich import enrich_native_entity_all_services

            for i, row in enumerate(pending):
                if context.check_stop():
                    return result
                if context.wait_if_paused():
                    return result
                artist_id = int(row["id"])
                try:
                    resolved = enrich_native_entity_all_services(
                        conn, "artist", artist_id, commit=True, services=services)
                except Exception as e:  # noqa: BLE001 — one artist must not end the run
                    logger.debug("native enrich of artist %s failed: %s", artist_id, e)
                    result.errors += 1
                    resolved = {}
                result.scanned += 1
                if resolved:
                    result.auto_fixed += 1
                    if context.report_progress:
                        context.report_progress(
                            scanned=i + 1, total=total,
                            log_line=f'Enriched artist #{artist_id}: '
                                     f'{", ".join(sorted(resolved))}',
                            log_type='success')
                if context.update_progress:
                    context.update_progress(i + 1, total)

            backfilled = self._backfill_provider_gaps(context, conn, services, result)
        finally:
            if conn:
                conn.close()

        if context.report_progress:
            context.report_progress(
                scanned=total, total=total, phase='Complete',
                log_line=(f'{result.auto_fixed} of {total} native artist(s) resolved'
                          if total else 'No native artists needed enrichment'),
                log_type='success')
            if backfilled:
                context.report_progress(
                    log_line=(f'Provider gaps: {backfilled["matched"]} filled, '
                              f'{backfilled["not_found"]} still unknown, '
                              f'out of {backfilled["scanned"]} checked'),
                    log_type='success' if backfilled["matched"] else 'info')
        return result

    def _backfill_provider_gaps(self, context: JobContext, conn, services,
                                result: JobResult):
        """Second phase: an entity one provider matched and another has never
        been asked about.

        Skipped outright when the configured-provider set is unknown, rather
        than falling back to "walk them all" — an unconfigured Tidal client
        starts an interactive login, and a scheduled job is the last place that
        should open a browser tab.
        """
        if not services:
            return None
        limit = self._backfill_size(context)
        if context.check_stop() or context.wait_if_paused():
            return None
        if context.report_progress:
            context.report_progress(phase='Filling provider gaps...')
        try:
            from core.library2.native_enrich import backfill_missing_provider_ids

            stats = backfill_missing_provider_ids(
                conn, services=sorted(services), limit=limit,
                should_stop=context.check_stop)
        except Exception as e:  # noqa: BLE001 — the artist pass already counted
            logger.error("provider gap backfill failed: %s", e)
            result.errors += 1
            return None
        result.scanned += stats["scanned"]
        result.auto_fixed += stats["matched"]
        result.errors += stats["errors"]
        return stats

    def estimate_scope(self, context: JobContext) -> int:
        conn = None
        try:
            conn = context.db._get_connection()
            return len(self._pending(conn, self._batch_size(context)))
        except Exception:  # noqa: BLE001
            return 0
        finally:
            if conn:
                conn.close()
