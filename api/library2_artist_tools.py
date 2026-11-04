"""Artist-page URL adapters over the shared native tag/RG/provenance tools.

Legacy numbers are resolved through imported references, never guessed to be
native row ids. Explicit ``lib2:<id>`` subjects also support native-only rows.
No retired artist/album/track table is read here.
"""
from contextlib import closing
from functools import wraps
import threading

from flask import jsonify, request

from core.library_scope import carrying_scope
from core.library2.job_registry import JobAlreadyRunning
from utils.logging_config import get_logger

logger = get_logger('library2.artist_tools')


class ArtistToolError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def register_artist_tool_routes(route, *, get_database, config_manager, registry,
                                profile, hidden, sync_tracks_to_server=None,
                                media_server_engine_getter=None):
    jobs = {}

    def expose(rule, **options):
        def decorate(fn):
            @wraps(fn)
            def handle(**kwargs):
                try:
                    from core.library2.migration_gate import migration_required, request_can_mutate
                    if request_can_mutate(request.method) and migration_required(get_database()):
                        return jsonify(success=False, error='Library migration is still preparing artist credits'), 503
                    return fn(**kwargs)
                except ArtistToolError as exc:
                    return jsonify(success=False, error=str(exc)), exc.status
                except JobAlreadyRunning as exc:
                    return jsonify(success=False, error=str(exc), job_id=exc.state['job_id']), 409
                except Exception as exc:
                    logger.exception('Artist tool request failed')
                    return jsonify(success=False, error=str(exc)), 500
            return route(rule, **options)(handle)
        return decorate

    def resolve(conn, entity, subject):
        from core.library2.catalogue_identity import resolve_native_id
        if isinstance(subject, bool):
            raise ArtistToolError('Invalid catalogue reference')
        value = str(subject).strip()
        native = value.startswith('lib2:')
        number = value[5:] if native else value
        if not number or (native and (not number.isdigit() or int(number) <= 0)):
            raise ArtistToolError('Invalid catalogue reference')
        column = 'id' if native else f'legacy_{entity}_id'
        reference = int(number) if native else number
        rows = conn.execute(f'SELECT id FROM lib2_{entity}s WHERE {column}=?', (reference,)).fetchall()
        if entity == 'track' and not native:
            rows += conn.execute('SELECT track_id FROM lib2_track_files WHERE legacy_track_id=?',
                                 (reference,)).fetchall()
        ids = {resolve_native_id(conn, entity, row[0]) for row in rows if row[0] is not None}
        if not ids:
            raise ArtistToolError(f'{entity.capitalize()} not found', 404)
        if len(ids) != 1:
            raise ArtistToolError('Ambiguous imported catalogue reference', 409)
        result = ids.pop()
        if hidden(conn, entity, result):
            raise ArtistToolError(f'{entity.capitalize()} not found', 404)
        return result

    def body_tracks():
        body = request.get_json(silent=True) or {}
        values = body.get('track_ids') if isinstance(body, dict) else None
        if not isinstance(values, list) or not values:
            raise ArtistToolError('track_ids must be a nonempty list')
        with closing(get_database()._get_connection()) as conn:
            ids = list(dict.fromkeys(resolve(conn, 'track', value) for value in values))
        return body, ids

    def server_type():
        getter = getattr(config_manager, 'get_active_media_server', None)
        return getter() if getter else None

    def preview(ids):
        from core.library2 import retag
        from core.repair_jobs.base import hand_tagged_path_keys
        db = get_database()
        contexts = []
        with closing(db._get_connection()) as conn:
            for start in range(0, len(ids), retag.MAX_TRACKS):
                contexts.extend(retag.track_contexts(conn, ids[start:start + retag.MAX_TRACKS]))
        return retag.tag_preview(contexts, hand_tagged_keys=hand_tagged_path_keys(db))

    def sync(ids, server):
        if not sync_tracks_to_server:
            raise ArtistToolError('Media-server sync is unavailable')
        from core.library2 import retag
        from core.library2.media_server_sync import scan_library_id
        rows = []
        with closing(get_database()._get_connection()) as conn:
            for start in range(0, len(ids), retag.MAX_TRACKS):
                for context in retag.track_contexts(conn, ids[start:start + retag.MAX_TRACKS]):
                    mappings = conn.execute(
                        'SELECT server_id FROM lib2_media_server_mappings '
                        'WHERE entity_type=? AND entity_id=? AND server_source=? AND server_library_id=?',
                        ('track', context['id'], server, scan_library_id())).fetchall()
                    if len(mappings) == 1:
                        rows.append({**context['db_data'], 'id': mappings[0][0], 'server_source': server})
        return sync_tracks_to_server(rows, server)

    def launch(kind, key, run, total=0):
        state = registry.start(kind, total=total)
        job_id = state['job_id']
        jobs[(profile(), key)] = job_id
        for old in list(jobs):
            if registry.get(jobs[old]) is None:
                jobs.pop(old, None)
        def work():
            try:
                registry.update(job_id, result=run(job_id))
            except Exception as exc:
                registry.update(job_id, error=str(exc))
            finally:
                registry.finish(job_id)
        threading.Thread(target=carrying_scope(work), name=f'ArtistTools-{kind}', daemon=True).start()
        return jsonify(success=True, started=True, job_id=job_id)

    def status(key):
        state = registry.get(jobs.get((profile(), key), ''))
        if not state:
            return jsonify(status='idle')
        result = state.get('result') or {}
        if state.get('error'):
            result = {**result, 'failed': max(1, result.get('failed', 0)),
                      'errors': [{'error': state['error']}]}
        return jsonify(**{**result, 'status': 'running' if state['running'] else 'done',
                          'processed': state['current'], 'total': state['total'],
                          'current_track': state.get('current_track', ''),
                          'error': state.get('error')})

    @expose('/api/library/track/<legacy_id>/tag-preview')
    def lib2_artist_tag_preview(legacy_id):
        with closing(get_database()._get_connection()) as conn:
            tid = resolve(conn, 'track', legacy_id)
        entries = preview([tid])
        if not entries:
            raise ArtistToolError('Track not found', 404)
        return jsonify(success=True, server_type=server_type(), **entries[0])

    @expose('/api/library/tracks/tag-preview-batch', methods=['POST'])
    def lib2_artist_tag_preview_batch():
        _, ids = body_tracks()
        return jsonify(success=True, tracks=preview(ids), server_type=server_type())

    @expose('/api/library/track/<legacy_id>/source-info')
    def lib2_artist_source_info(legacy_id):
        from core.library2.source_info import track_source_info
        with closing(get_database()._get_connection()) as conn:
            tid = resolve(conn, 'track', legacy_id)
            return jsonify(success=True, downloads=track_source_info(conn, tid))

    @expose('/api/library/track/<legacy_id>/write-tags', methods=['POST'])
    def lib2_artist_write_tags(legacy_id):
        from core.library2 import retag
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise ArtistToolError('JSON body must be an object')
        with closing(get_database()._get_connection()) as conn:
            tid = resolve(conn, 'track', legacy_id)
        state = registry.start('retag', total=1)
        try:
            result = retag.write_tags(get_database(), [tid], embed_cover=body.get('embed_cover', True))
            if body.get('sync_to_server') and not result['failed']:
                result['server_sync'] = sync([tid], server_type())
            registry.update(state['job_id'], result=result)
            error = (result.get('errors') or [{}])[0].get('error') if result['failed'] else None
            return jsonify(success=not result['failed'], error=error, **result)
        finally:
            registry.finish(state['job_id'])

    @expose('/api/library/tracks/write-tags-batch', methods=['POST'])
    def lib2_artist_write_tags_batch():
        from core.library2 import retag
        body, ids = body_tracks()
        server = server_type()
        def run(job_id):
            def progress(_stage, current, total):
                registry.update(job_id, current=current, total=total)
            result = retag.write_tags(get_database(), ids, embed_cover=body.get('embed_cover', True), progress=progress)
            if body.get('sync_to_server'):
                failed = {error.get('track_id') for error in result.get('errors', [])}
                outcome = sync([tid for tid in ids if tid not in failed], server)
                result.update(sync_phase='done', sync_server=server,
                              sync_synced=outcome['synced'], sync_failed=outcome['failed'])
            return result
        return launch('retag', 'retag', run, len(ids))

    @expose('/api/library/tracks/write-tags-batch/status')
    def lib2_artist_write_tags_status():
        return status('retag')

    def check_ffmpeg():
        from core.replaygain import is_ffmpeg_available
        if not is_ffmpeg_available():
            raise ArtistToolError('ffmpeg not found on PATH', 500)

    def sync_artist(aid):
        from core.library2.artist_sync import sync_artist as run
        return run(get_database(), aid,
                   media_server_engine_getter() if media_server_engine_getter else None)

    @expose('/api/library/artist/<legacy_id>/sync', methods=['POST'])
    def lib2_artist_server_sync_legacy(legacy_id):
        with closing(get_database()._get_connection()) as conn:
            aid = resolve(conn, 'artist', legacy_id)
        return jsonify(sync_artist(aid))

    @expose('/api/library/v2/artists/<int:artist_id>/sync', methods=['POST'])
    def lib2_artist_server_sync(artist_id):
        with closing(get_database()._get_connection()) as conn:
            aid = resolve(conn, 'artist', f'lib2:{artist_id}')
        return launch('artist-sync', ('artist-sync', aid), lambda _job: sync_artist(aid))

    @expose('/api/library/track/<legacy_id>/analyze-replaygain', methods=['POST'])
    def lib2_artist_track_rg(legacy_id):
        from core.library2.replaygain import analyze_track_replaygain
        from core.replaygain import RG_REFERENCE_LUFS
        check_ffmpeg()
        with closing(get_database()._get_connection()) as conn:
            tid = resolve(conn, 'track', legacy_id)
            result = analyze_track_replaygain(conn, tid, config_manager=config_manager)
        gain = result.get('track_gain_db')
        return jsonify(success=result['analyzed'], **result,
                       track_gain=f'{gain:+.2f} dB' if gain is not None else None,
                       lufs=RG_REFERENCE_LUFS - gain if gain is not None else None)

    @expose('/api/library/album/<legacy_id>/analyze-replaygain', methods=['POST'])
    def lib2_artist_album_rg(legacy_id):
        from core.library2.replaygain import analyze_album_replaygain
        check_ffmpeg()
        with closing(get_database()._get_connection()) as conn:
            aid = resolve(conn, 'album', legacy_id)
        def run(job_id):
            def progress(current, total, title):
                registry.update(job_id, current=current, total=total, current_track=title)
            with closing(get_database()._get_connection()) as conn:
                return analyze_album_replaygain(conn, aid, config_manager=config_manager, progress=progress)
        return launch('replaygain', ('album', aid), run)

    @expose('/api/library/album/<legacy_id>/analyze-replaygain/status')
    def lib2_artist_album_rg_status(legacy_id):
        with closing(get_database()._get_connection()) as conn:
            aid = resolve(conn, 'album', legacy_id)
        return status(('album', aid))

    @expose('/api/library/tracks/analyze-replaygain-batch', methods=['POST'])
    def lib2_artist_tracks_rg():
        from core.library2.replaygain import analyze_track_replaygain
        check_ffmpeg()
        _, ids = body_tracks()
        def run(job_id):
            result = {'analyzed': 0, 'failed': 0, 'errors': []}
            with closing(get_database()._get_connection()) as conn:
                for index, tid in enumerate(ids, 1):
                    outcome = analyze_track_replaygain(conn, tid, config_manager=config_manager)
                    result['analyzed' if outcome['analyzed'] else 'failed'] += 1
                    if outcome.get('error'):
                        result['errors'].append({'track_id': tid, 'error': outcome['error']})
                    registry.update(job_id, current=index)
            return result
        return launch('replaygain', 'tracks-rg', run, len(ids))

    @expose('/api/library/tracks/analyze-replaygain-batch/status')
    def lib2_artist_tracks_rg_status():
        return status('tracks-rg')
