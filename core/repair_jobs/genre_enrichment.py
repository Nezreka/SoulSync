"""Findings-based, additive genre enrichment."""
import json
from core.repair_jobs import register_job
from core.library2.sql_util import owned_sql
from core.repair_jobs.base import JobContext, JobResult, RepairJob
from core.metadata.genre_enrichment import (collect_cached_candidates, collect_local_candidates,
    extract_provider_genres, parse_values, propose_genres, provider_ids_from_row,
    whitelist_from_config)


@register_job
class GenreEnrichmentJob(RepairJob):
    job_id = 'genre_enrichment'
    display_name = 'Genre Enrichment'
    description = 'Adds conservatively translated genres from stored metadata and caches'
    default_enabled = False
    default_interval_hours = 168
    default_settings = {'max_genres': 5, 'include_artists': True, 'include_albums': True, 'allow_live_calls': False}
    auto_fix = False
    help_text = ('Adds genres without removing existing values. Local fields and cached provider data are used first; '
                 'live enrichment calls are disabled by default. Fuzzy translations are conservative and ambiguous '
                 'values are shown for review. The maximum limits additions only while there is capacity.')

    def _settings(self, context):
        raw = context.config_manager.get('repair.jobs.genre_enrichment.settings', {}) if context.config_manager else {}
        out = dict(self.default_settings)
        if isinstance(raw, dict): out.update(raw)
        try:
            value = out['max_genres']
            if isinstance(value, bool) or not isinstance(value, int): raise ValueError
            if not 1 <= value <= 20: raise ValueError
            out['max_genres'] = value
        except (TypeError, ValueError): out['max_genres'] = 5
        for key in ('include_artists', 'include_albums', 'allow_live_calls'):
            if not isinstance(out[key], bool): out[key] = self.default_settings[key]
        return out

    def scan(self, context: JobContext) -> JobResult:
        result, settings = JobResult(), self._settings(context)
        cfg = context.config_manager
        if not (cfg and cfg.get('genre_whitelist.enabled', False)):
            result.skipped = 1
            if context.report_progress: context.report_progress(phase='Skipped — genre whitelist is disabled', log_type='info')
            return result
        whitelist = whitelist_from_config(cfg)
        if not whitelist:
            result.skipped = 1
            if context.report_progress: context.report_progress(phase='Skipped — genre whitelist is empty', log_type='info')
            return result
        conn = context.db._get_connection()
        conn.row_factory = None
        # Owned rows only, like every sibling job. v2 keeps a watched artist's
        # discography and the wishlist in the same tables, and findings raised
        # against those are not about the user's library — with allow_live_calls
        # on they also spend real provider requests.
        entity_specs = []
        if settings['include_artists']:
            entity_specs.append(('artist', 'lib2_artists a', owned_sql('artist', 'a')))
        if settings['include_albums']:
            entity_specs.append(('album', 'lib2_albums al', owned_sql('album', 'al')))
        total = 0
        for _, source, owned in entity_specs:
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM {source} WHERE {owned}").fetchone()
            total += int(count_row[0] or 0)
        if context.update_progress: context.update_progress(0, total)

        def iter_entities():
            for kind, _source, owned in entity_specs:
                cur = conn.cursor()
                if kind == 'album':
                    cur.execute(
                        "SELECT al.*, ar.name AS artist_name "
                        "FROM lib2_albums al "
                        "LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id "
                        f"WHERE {owned}"
                    )
                else:
                    cur.execute(f"SELECT * FROM lib2_artists a WHERE {owned}")
                columns = [x[0] for x in cur.description]
                for raw_row in cur:
                    yield kind, dict(zip(columns, raw_row, strict=True))

        run_cache, stats = {}, {'entities_scanned': 0, 'entities_with_existing_genres': 0,
            'local_candidates': 0, 'metadata_cache_hits': 0, 'translation_cache_hits': 0,
            'translation_cache_misses': 0, 'live_calls': 0, 'live_call_failures': 0,
            'accepted_additions': 0, 'ambiguous_candidates': 0, 'rejected_candidates': 0,
            'cap_limited_entities': 0}
        try:
            for index, (kind, row) in enumerate(iter_entities(), start=1):
                if context.check_stop(): return result
                result.scanned += 1; stats['entities_scanned'] += 1
                existing = parse_values(row.get('genres'))
                if existing: stats['entities_with_existing_genres'] += 1
                local = collect_local_candidates(row)
                stats['local_candidates'] += len(local)
                cached, hits = collect_cached_candidates(context.metadata_cache, row, kind)
                stats['metadata_cache_hits'] += hits
                live = []
                live_calls = live_failures = 0
                if settings['allow_live_calls']:
                    live, live_calls, live_failures = self._live_candidates(context, row, kind)
                    stats['live_calls'] += live_calls
                    stats['live_call_failures'] += live_failures
                proposal = propose_genres(existing, local + cached + live, whitelist, settings['max_genres'], context.metadata_cache, run_cache)
                for value in proposal['translations']:
                    if value.get('cache_hit'): stats['translation_cache_hits'] += 1
                    else: stats['translation_cache_misses'] += 1
                stats['accepted_additions'] += len(proposal['added_genres'])
                stats['ambiguous_candidates'] += len(proposal['ambiguous_genres'])
                stats['rejected_candidates'] += len(proposal['rejected_genres'])
                if proposal['omitted_due_to_cap']: stats['cap_limited_entities'] += 1
                if not proposal['added_genres'] and not proposal['ambiguous_genres']: continue
                entity_id, name = row.get('id'), row.get('name') or row.get('title')
                details = dict(proposal, entity=kind, name=name, max_genres=settings['max_genres'],
                               cache_stats={'metadata_cache_hits': hits, 'translation_cache_hits': sum(1 for x in proposal['translations'] if x.get('cache_hit')), 'live_calls': live_calls, 'live_call_failures': live_failures},
                               library_v2_native=True,
                               library_v2={('artist_ids' if kind == 'artist' else 'album_ids'): [int(entity_id)]},
                               artist_id=row.get('primary_artist_id') if kind == 'album' else row.get('id'),
                               artist_name=row.get('artist_name') if kind == 'album' else name,
                               album_title=name if kind == 'album' else None)
                if context.create_finding:
                    inserted = context.create_finding(job_id=self.job_id, finding_type=self.job_id,
                        severity='warning' if proposal['ambiguous_genres'] else 'info', entity_type=kind,
                        entity_id=f'lib2:{entity_id}', file_path=None, title=f'Enrich genres: {name}',
                        description=f"{len(proposal['added_genres'])} genre(s) can be added to {kind} {name}", details=details)
                    if inserted: result.findings_created += 1
                    else: result.findings_skipped_dedup += 1
                if context.update_progress and index % 100 == 0: context.update_progress(index, total)
            if context.update_progress: context.update_progress(total, total)
            if context.report_progress: context.report_progress(phase=f'Done — {result.findings_created} enrichment finding(s)', log_line=json.dumps(stats), log_type='info')
            return result
        finally:
            conn.close()

    @staticmethod
    def _live_candidates(context, row, kind):
        """Fetch detail by an existing provider ID only; never search by name."""
        from core.metadata import get_client_for_source
        ids = provider_ids_from_row(row, kind)
        methods = {'spotify': 'get_artist' if kind == 'artist' else 'get_album',
                   'itunes': 'get_artist' if kind == 'artist' else 'get_album',
                   'deezer': 'get_artist_info' if kind == 'artist' else 'get_album_raw',
                   'discogs': 'get_artist' if kind == 'artist' else 'get_album'}
        found, calls, failures = [], 0, 0
        for source in ('spotify', 'itunes', 'deezer', 'discogs'):
            source_id = ids.get(source)
            if not source_id: continue
            if context.metadata_cache and context.metadata_cache.get_entity(source, kind, str(source_id)) is not None:
                continue
            client = context.spotify_client if source == 'spotify' else context.itunes_client if source == 'itunes' else get_client_for_source(source)
            method = getattr(client, methods[source], None) if client else None
            if not method: continue
            try:
                calls += 1
                payload = method(str(source_id))
                if isinstance(payload, dict):
                    for value in extract_provider_genres(source, kind, payload):
                        found.append({'raw_genre': value, 'source': source, 'source_entity_id': str(source_id), 'origin': 'live_api'})
            except Exception:
                # A provider failure is local to this entity/source; the scan continues.
                failures += 1
                continue
        return found, calls, failures
