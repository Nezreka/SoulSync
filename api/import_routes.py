"""Import + reassign endpoints - lifted from web_server.py.

the staging browser (files/groups/scan-status/hints/suggestions), the
reassign family, the import search/match/process routes, the singles
processor, and the auto-import worker boot (module-local handle: the
staging deep-scan trigger and the wiring getter read it as a module
attribute). bodies byte-identical; only the decorator changed and
dev_mode_enabled / hydrabase_worker became getters.
"""

from flask import Blueprint, jsonify, request

from core.imports.album import build_album_import_match_payload
from core.imports.routes import ImportRouteRuntime as _ImportRouteRuntime
from core.imports.routes import album_match as _import_album_match
from core.imports.routes import album_process as _import_album_process
from core.imports.routes import process_single_import_file as _import_process_single_import_file
from core.imports.routes import search_albums as _import_search_albums
from core.imports.routes import search_sources as _import_search_sources
from core.imports.routes import search_tracks as _import_search_tracks
from core.imports.routes import singles_process as _import_singles_process
from core.imports.routes import staging_files as _import_staging_files
from core.imports.routes import staging_groups as _import_staging_groups
from core.imports.routes import staging_hints as _import_staging_hints
from core.imports.routes import staging_scan_status as _import_staging_scan_status
from core.imports.routes import staging_suggestions as _import_staging_suggestions
from core.profile_context import admin_only, get_current_profile_id
from core.runtime_state import add_activity_item
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
get_database = None
config_manager = None
docker_resolve_path = None
import_singles_executor = None
_post_process_matched_download = None
automation_engine = None
_dev_mode_enabled = None
_hydrabase_worker = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"import_routes.configure: unknown dep {name!r}")
        g[name] = value
    _boot_auto_import_worker()


bp = Blueprint('import_routes', __name__)


def create_blueprint():
    return bp

def _build_import_route_runtime():
    return _ImportRouteRuntime(
        post_process_matched_download=_post_process_matched_download,
        add_activity_item=add_activity_item,
        automation_engine=automation_engine,
        hydrabase_worker=_hydrabase_worker(),
        dev_mode_enabled=_dev_mode_enabled(),
        import_singles_executor=import_singles_executor,
        build_album_import_match_payload=build_album_import_match_payload,
        process_single_import_file=lambda runtime, file_info: _process_single_import_file(file_info),
        logger=logger,
    )


@bp.route('/api/import/staging/files', methods=['GET'])
def import_staging_files():
    payload, status = _import_staging_files(_build_import_route_runtime())
    return jsonify(payload), status


@bp.route('/api/import/staging/groups', methods=['GET'])
def import_staging_groups():
    payload, status = _import_staging_groups(_build_import_route_runtime())
    return jsonify(payload), status


@bp.route('/api/import/staging/scan-status', methods=['GET'])
def import_staging_scan_status():
    payload, status = _import_staging_scan_status(_build_import_route_runtime())
    return jsonify(payload), status


def _reassign_missing_fields(data):
    """Which required identifiers a reassign request left out.

    Named explicitly rather than left to degrade: without them the service
    would answer "Nothing to reassign", which reads like the album is empty
    instead of like the request was malformed.
    """
    return [field for field in ('source', 'local_album_id', 'album_id')
            if not str(data.get(field) or '').strip()]


@bp.route('/api/reassign/artists', methods=['GET'])
@admin_only
def reassign_search_artists():
    """Step 1 of an album reassign: find the artist it SHOULD belong to.

    Admin-only, like re-identify: it restages library files."""
    try:
        from core.imports.reassign_service import search_artists
        return jsonify({'success': True, 'artists': search_artists(
            request.args.get('source', ''), request.args.get('q', ''))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/reassign/albums', methods=['GET'])
@admin_only
def reassign_artist_albums():
    """Step 2: that artist's releases. Picking from THIS list is what
    guarantees the target is one the source can answer for."""
    try:
        from core.imports.reassign_service import artist_albums
        return jsonify({'success': True, 'albums': artist_albums(
            request.args.get('source', ''), request.args.get('artist_id', ''))})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/reassign/preview', methods=['POST'])
@admin_only
def reassign_preview():
    """Step 3: how the local files line up, BEFORE anything is staged."""
    try:
        from core.imports.reassign_service import preview_reassign
        data = request.get_json() or {}
        missing = _reassign_missing_fields(data)
        if missing:
            return jsonify({'success': False,
                            'error': f"Missing required field(s): {', '.join(missing)}"}), 400
        payload = preview_reassign(
            get_database(), data.get('source', ''),
            data.get('local_album_id'), data.get('album_id', ''))
        return jsonify(payload), (200 if payload.get('success') else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/reassign/apply', methods=['POST'])
@admin_only
def reassign_apply():
    """Step 4: stage each file with its hint. The import pipeline re-files
    them — tags, folder and database rows all come from the same code that
    handles a fresh download."""
    try:
        from core.imports.reassign_service import apply_reassign
        from core.imports.staging import get_staging_path
        data = request.get_json() or {}
        missing = _reassign_missing_fields(data)
        if missing:
            return jsonify({'success': False,
                            'error': f"Missing required field(s): {', '.join(missing)}"}), 400
        payload = apply_reassign(
            get_database(),
            source=data.get('source', ''),
            local_album_id=data.get('local_album_id'),
            album_id=data.get('album_id', ''),
            album_name=data.get('album_name', ''),
            artist_id=data.get('artist_id'),
            artist_name=data.get('artist_name', ''),
            album_type=data.get('album_type'),
            staging_dir=get_staging_path(),
            replace=bool(data.get('replace', True)),
            # Only ever true when the client has shown the user the preview and
            # they accepted an incomplete mapping.
            allow_partial=bool(data.get('allow_partial', False)),
        )
        return jsonify(payload), (200 if payload.get('success') else 400)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/import/staging/hints', methods=['GET'])
def import_staging_hints():
    payload, status = _import_staging_hints(_build_import_route_runtime())
    return jsonify(payload), status


@bp.route('/api/import/search/albums', methods=['GET'])
def import_search_albums():
    payload, status = _import_search_albums(
        _build_import_route_runtime(),
        request.args.get('q', ''),
        request.args.get('limit', 12),
        request.args.get('source', ''),
    )
    return jsonify(payload), status


@bp.route('/api/import/search/sources', methods=['GET'])
def import_search_sources_route():
    payload, status = _import_search_sources()
    return jsonify(payload), status


@bp.route('/api/import/album/match', methods=['POST'])
def import_album_match():
    payload, status = _import_album_match(_build_import_route_runtime(), request.get_json() or {})
    return jsonify(payload), status


@bp.route('/api/import/album/process', methods=['POST'])
def import_album_process():
    payload, status = _import_album_process(_build_import_route_runtime(), request.get_json() or {})
    return jsonify(payload), status


@bp.route('/api/import/search/tracks', methods=['GET'])
def import_search_tracks():
    payload, status = _import_search_tracks(
        _build_import_route_runtime(),
        request.args.get('q', ''),
        request.args.get('limit', 10),
    )
    return jsonify(payload), status


def _process_single_import_file(file_info):
    return _import_process_single_import_file(_build_import_route_runtime(), file_info)


@bp.route('/api/import/singles/process', methods=['POST'])
def import_singles_process():
    data = request.get_json() or {}
    payload, status = _import_singles_process(_build_import_route_runtime(), data.get('files', []))
    return jsonify(payload), status


# Auto-Import Worker
auto_import_worker = None


def _boot_auto_import_worker():
    """runs from configure(): the worker needs the injected deps, so it
    cannot boot at import time like it did as web_server top-level code."""
    global auto_import_worker
    try:
        from core.auto_import_worker import AutoImportWorker
        _ai_db = get_database()
        _ai_staging = docker_resolve_path(config_manager.get('import.staging_path', './Staging'))
        _ai_transfer = docker_resolve_path(config_manager.get('soulseek.transfer_path', './Transfer'))
        auto_import_worker = AutoImportWorker(
            database=_ai_db,
            staging_path=_ai_staging,
            transfer_path=_ai_transfer,
            process_callback=_post_process_matched_download,
            config_manager=config_manager,
            automation_engine=automation_engine,
        )
        if config_manager.get('auto_import.enabled', False):
            # NOT a bare start(): on an installation still migrating into lib2
            # the auto-importer would import against a half-built catalogue.
            # defer_or_start holds it behind the upgrade barrier, the same gate
            # every enrichment worker goes through (and api/auto_import.py's
            # start endpoint).
            from core.library2.migration_gate import defer_or_start

            defer_or_start(auto_import_worker, _ai_db)
            logger.info("Auto-import worker started")
        else:
            logger.info("Auto-import worker initialized (disabled)")
    except Exception as _ai_err:
        logger.error(f"Auto-import worker init failed: {_ai_err}")


# /api/auto-import* endpoints: lifted to api/auto_import.py.
@bp.route('/api/import/staging/suggestions', methods=['GET'])
def import_staging_suggestions():
    payload, status = _import_staging_suggestions()
    return jsonify(payload), status
