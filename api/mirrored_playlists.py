"""Mirrored playlist endpoints - lifted from web_server.py.

the mirrored playlist CRUD, preferences, pipeline run/status, server
link, discovery prepare/clear/retry and discovery-states routes, plus
the three helpers only they (and one wiring site) use: the visibility
check, the ownership lookup, and the sync-fingerprint invalidator.
bodies byte-identical; only the decorator changed and spotify_client /
matching_engine / the automation deps became getters.
"""

import json
import threading
import time

from flask import Blueprint, g, jsonify, request

from api.source_playlists import (
    _get_discovery_cache_key,
    _run_youtube_discovery_worker,
    youtube_discovery_executor,
    youtube_playlist_states,
)
from core.api_validation import parse_strict_bool, parse_strict_int
from core.profile_context import get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
get_database = None
config_manager = None
playlist_pipeline_progress_lock = None
playlist_pipeline_progress_states = None
_get_active_discovery_source = None
_load_sync_status_file = None
_save_sync_status_file = None
_spotify_client = None
_matching_engine = None
_get_automation_deps = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"mirrored_playlists.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('mirrored_playlists', __name__)


def create_blueprint():
    return bp

def mirrored_playlist_visible(playlist) -> bool:
    """Owner-or-admin gate for by-id mirrored-playlist routes (IDOR fix).

    Every /api/mirrored-playlists/<id>/* route used to act on any playlist id
    regardless of who owned it — profile 2 could read, rename, re-point,
    pipeline-run, or DELETE profile 3's playlists by iterating ids. The list
    endpoint was always profile-scoped; the by-id ones now match it. Callers
    answer 404 (not 403) on failure so foreign ids aren't probeable.

    Background/system callers (no request context) resolve to profile 1 via
    get_current_profile_id() and pass the admin check — automation pipelines
    keep working exactly as before."""
    if not playlist:
        return False
    try:
        if bool(getattr(g, "is_admin", True)):
            return True
    except RuntimeError:
        return True   # no request context = system caller
    try:
        return int(playlist.get('profile_id') or 1) == int(get_current_profile_id())
    except (TypeError, ValueError):
        return False


def _invalidate_mirror_sync_fingerprint(playlist_id):
    """Drop the smart-skip fingerprint for a mirror so the next sync really runs.

    Auto-Sync short-circuits when the track list is byte-identical to the last
    run. That is right for a provider refresh but wrong for an authoritative
    Quality Profile change, which must re-stamp the existing Wishlist rows even
    though not a single track id moved (P1-03). Clearing the stored hashes is
    the smallest change that makes the very next scheduled run authoritative,
    and it degrades safely: a lost preference write only costs one extra sync.
    """
    key = f"auto_mirror_{int(playlist_id)}"
    try:
        sync_statuses = _load_sync_status_file()
        status = sync_statuses.get(key)
        if not status:
            return
        for field in ('tracks_hash', 'mirror_tracks_hash', 'matched_tracks'):
            status.pop(field, None)
        sync_statuses[key] = status
        _save_sync_status_file(sync_statuses)
    except Exception as e:
        logger.debug("Could not invalidate sync fingerprint for %s: %s", key, e)


@bp.route('/api/mirrored-playlists/list', methods=['GET'])
def get_mirrored_playlists_list():
    """Return simple list of mirrored playlists for automation config dropdowns."""
    try:
        database = get_database()
        profile_id = get_current_profile_id()
        playlists = database.get_mirrored_playlists(profile_id=profile_id)
        spotify_authed = bool(_spotify_client() and _spotify_client().is_spotify_authenticated())
        return jsonify({
            "playlists": [{"id": p['id'], "name": p['name'], "source": p.get('source', '')} for p in playlists],
            "spotify_authenticated": spotify_authed
        })
    except Exception as e:
        return jsonify({"playlists": [], "spotify_authenticated": False}), 200


@bp.route('/api/mirrored-playlists', methods=['GET'])
def get_mirrored_playlists_endpoint():
    """List all mirrored playlists for the active profile."""
    try:
        from core.playlists.source_refs import describe_mirrored_source_ref
        from core.playlists.naming import effective_mirrored_name
        database = get_database()
        profile_id = get_current_profile_id()
        playlists = database.get_mirrored_playlists(profile_id=profile_id)
        # Single batched query instead of N per-playlist round-trips. Used to
        # take ~50ms per playlist (new connection + 4 sub-queries) — at 30
        # playlists that's 1.5s of modal load time just for status counts.
        batch_counts = database.get_all_mirrored_playlist_status_counts(profile_id=profile_id)
        for pl in playlists:
            counts = batch_counts.get(pl['id'], {
                'total': 0, 'discovered': 0, 'wishlisted': 0,
                'in_library': 0, 'library_checked': 0,
            })
            pl['discovered_count'] = counts['discovered']
            pl['total_count'] = counts['total']
            pl['wishlisted_count'] = counts['wishlisted']
            pl['in_library_count'] = counts['in_library']
            # How many of this playlist's tracks the sync matcher has ever
            # checked. 0 means nobody has looked, which is NOT the same as
            # owning none of it, and the card must not report the second when it
            # only knows the first.
            pl['library_checked_count'] = counts.get('library_checked', 0)
            source_ref = describe_mirrored_source_ref(pl)
            pl['source_ref'] = source_ref.source_ref
            pl['source_ref_kind'] = source_ref.source_ref_kind
            pl['source_ref_status'] = source_ref.source_ref_status
            pl['source_ref_error'] = source_ref.source_ref_error
            # The name the UI should show / sync uses: custom alias if set, else
            # the upstream name. Single source of truth so card + sync agree.
            pl['display_name'] = effective_mirrored_name(pl)
            pl['pipeline_state'] = _snapshot_playlist_pipeline_state(pl['id'])
        return jsonify(playlists)
    except Exception as e:
        logger.error(f"Error getting mirrored playlists: {e}")
        return jsonify({"error": str(e)}), 500

def _mirror_scope_profile_id():
    """None for admin, else the active profile.

    Merged from upstream/dev's ``mirrored_playlist_visible`` admin bypass: an
    admin can read/manage any profile's mirror, matching the parallel
    isolation-audit work upstream did on the same by-id mirror routes. ``None``
    is the existing "trusted caller who already resolved the mirror" scope
    (see ``_mirror_owner_clause``), so admin gets the unscoped read/write on a
    playlist_id that a non-admin would only get after passing the ownership
    check below.
    """
    try:
        is_admin = bool(getattr(g, "is_admin", True))
    except RuntimeError:
        is_admin = True  # no request context = system caller
    return None if is_admin else get_current_profile_id()


def _owned_mirrored_playlist(database, playlist_id):
    """The mirror ``playlist_id`` IF it belongs to the active SoulSync profile,
    or any mirror when the caller is admin.

    Request handlers must never look a mirror up by primary key alone: the ids
    are small integers and the synthetic ``auto_mirror_<pk>`` form is guessable,
    so an unscoped lookup let any profile read, rename, re-point, run, clear or
    delete another profile's mirror — and read its Quality Profile (P0-01).
    A foreign mirror is reported exactly like a missing one.
    """
    return database.get_mirrored_playlist(playlist_id, profile_id=_mirror_scope_profile_id())


@bp.route('/api/mirrored-playlists/<int:playlist_id>', methods=['GET'])
def get_mirrored_playlist_endpoint(playlist_id):
    """Get a mirrored playlist with its tracks."""
    try:
        from core.playlists.source_refs import describe_mirrored_source_ref
        from core.playlists.naming import effective_mirrored_name
        database = get_database()
        playlist = _owned_mirrored_playlist(database, playlist_id)
        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404
        source_ref = describe_mirrored_source_ref(playlist)
        playlist['source_ref'] = source_ref.source_ref
        playlist['source_ref_kind'] = source_ref.source_ref_kind
        playlist['source_ref_status'] = source_ref.source_ref_status
        playlist['source_ref_error'] = source_ref.source_ref_error
        playlist['display_name'] = effective_mirrored_name(playlist)
        playlist['pipeline_state'] = _snapshot_playlist_pipeline_state(playlist_id)
        playlist['tracks'] = database.get_mirrored_playlist_tracks(playlist_id)
        return jsonify(playlist)
    except Exception as e:
        logger.error(f"Error getting mirrored playlist: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/mirrored-playlists/<int:playlist_id>/source-ref', methods=['PATCH'])
def update_mirrored_playlist_source_ref_endpoint(playlist_id):
    """Update the upstream source link/id for a mirrored playlist."""
    try:
        data = request.get_json() or {}
        source_ref = data.get('source_ref') or data.get('source_playlist_id') or data.get('url')

        database = get_database()
        playlist = _owned_mirrored_playlist(database, playlist_id)
        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404

        try:
            from core.playlists.source_refs import normalize_mirrored_source_ref
            normalized = normalize_mirrored_source_ref(
                playlist.get('source'),
                source_ref,
                playlist.get('description') or '',
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        existing = [
            pl for pl in database.get_mirrored_playlists(profile_id=playlist.get('profile_id', 1))
            if (
                pl.get('source') == playlist.get('source')
                and str(pl.get('source_playlist_id')) == str(normalized.source_playlist_id)
                and int(pl.get('id')) != int(playlist_id)
            )
        ]
        if existing:
            return jsonify({
                "error": f"That source is already mirrored as '{existing[0].get('name', 'another playlist')}'"
            }), 409

        ok = database.update_mirrored_playlist_source_ref(
            playlist_id,
            normalized.source_playlist_id,
            normalized.description,
            profile_id=_mirror_scope_profile_id(),
        )
        if not ok:
            return jsonify({"error": "Failed to update source reference"}), 500

        updated = _owned_mirrored_playlist(database, playlist_id) or {}
        return jsonify({"success": True, "playlist": updated})
    except Exception as e:
        logger.error(f"Error updating mirrored playlist source reference: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/mirrored-playlists/<int:playlist_id>/custom-name', methods=['PATCH'])
def update_mirrored_playlist_custom_name_endpoint(playlist_id):
    """Set or clear a user alias (custom display + sync name) for a mirrored
    playlist. A blank/missing custom_name CLEARS the alias (falls back to the
    upstream name). The upstream name keeps tracking on refresh either way."""
    try:
        from core.playlists.naming import effective_mirrored_name
        data = request.get_json() or {}
        database = get_database()
        playlist = _owned_mirrored_playlist(database, playlist_id)
        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404

        # `custom_name` may be '' / null to CLEAR the alias.
        ok = database.set_mirrored_playlist_custom_name(
            playlist_id, data.get('custom_name'), profile_id=_mirror_scope_profile_id()
        )
        if not ok:
            return jsonify({"error": "Failed to update name"}), 500

        updated = _owned_mirrored_playlist(database, playlist_id) or {}
        updated['display_name'] = effective_mirrored_name(updated)
        return jsonify({"success": True, "playlist": updated})
    except Exception as e:
        logger.error(f"Error updating mirrored playlist custom name: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/mirrored-playlists/<int:playlist_id>/preferences', methods=['PATCH'])
def update_mirrored_playlist_preferences_endpoint(playlist_id):
    """Update per-playlist download preferences (e.g. organize by playlist folder)."""
    try:
        data = request.get_json() or {}
        if not ({'organize_by_playlist', 'quality_profile_id'} & set(data)):
            return jsonify({"error": "No supported preference supplied"}), 400

        database = get_database()
        profile_id = _mirror_scope_profile_id()
        playlist = _owned_mirrored_playlist(database, playlist_id)
        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404

        organize_by_playlist = None
        if 'organize_by_playlist' in data:
            organize_by_playlist = parse_strict_bool(data.get('organize_by_playlist'))
            if organize_by_playlist is None:
                return jsonify({"error": "Invalid organize_by_playlist"}), 400

        quality_profile_id = None
        if 'quality_profile_id' in data:
            quality_profile_id = parse_strict_int(data.get('quality_profile_id'))
            if quality_profile_id is None:
                return jsonify({"error": "Invalid quality_profile_id"}), 400

        # Both fields are validated and written in ONE transaction: a rejected
        # Quality Profile used to leave the already-committed organize toggle
        # behind while the response said 400 (P2-02).
        outcome = database.update_mirrored_playlist_preferences(
            playlist_id,
            profile_id=profile_id,
            organize_by_playlist=organize_by_playlist,
            quality_profile_id=quality_profile_id,
        )
        if outcome == 'not_found':
            return jsonify({"error": "Playlist not found"}), 404
        if outcome == 'unknown_quality_profile':
            return jsonify({"error": "Unknown quality_profile_id"}), 400
        if outcome != 'ok':
            return jsonify({"error": "Failed to update preferences"}), 500

        if (
            quality_profile_id is not None
            and quality_profile_id != playlist.get('quality_profile_id')
        ):
            # A profile-only change must not be swallowed by the auto-sync
            # "tracks unchanged" fast path (P1-03).
            _invalidate_mirror_sync_fingerprint(playlist_id)

        updated = _owned_mirrored_playlist(database, playlist_id) or {}
        return jsonify({"success": True, "playlist": updated})
    except Exception as e:
        logger.error(f"Error updating mirrored playlist preferences: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/mirrored-playlists/resolve', methods=['GET'])
def resolve_mirrored_playlist_endpoint():
    """Resolve mirrored playlist by numeric id or upstream source id (e.g. Spotify playlist id)."""
    try:
        playlist_ref = request.args.get('ref') or request.args.get('playlist_id')
        source = request.args.get('source', 'spotify')
        profile_id = get_current_profile_id()
        if not playlist_ref:
            return jsonify({"error": "ref or playlist_id query param required"}), 400

        database = get_database()
        playlist = database.resolve_mirrored_playlist(
            playlist_ref,
            profile_id=profile_id,
            default_source=source,
        )
        if not playlist:
            return jsonify({"found": False, "playlist": None})
        # Belt and braces: the resolver is owner-scoped, but this endpoint is the
        # one that hands a persisted quality_profile_id to the browser (P0-01).
        if int(playlist.get('profile_id') or 1) != int(profile_id):
            return jsonify({"found": False, "playlist": None})
        return jsonify({"found": True, "playlist": playlist})
    except Exception as e:
        logger.error(f"Error resolving mirrored playlist: {e}")
        return jsonify({"error": str(e)}), 500


def _playlist_pipeline_state_key(playlist_id, profile_id=None):
    """Progress-state key for a manual pipeline run.

    Keyed by ``(profile, mirror)``: two profiles running a pipeline against
    mirrors that happen to share a primary key must not read or overwrite each
    other's progress, log lines or result (P0-01).
    """
    if profile_id is None:
        profile_id = get_current_profile_id()
    return f"p{int(profile_id)}:mirrored_{int(playlist_id)}"


def _snapshot_playlist_pipeline_state(playlist_id, profile_id=None):
    key = _playlist_pipeline_state_key(playlist_id, profile_id)
    with playlist_pipeline_progress_lock:
        state = playlist_pipeline_progress_states.get(key)
        return dict(state) if state else None


def _replace_playlist_pipeline_state(playlist_id, state, profile_id=None):
    key = _playlist_pipeline_state_key(playlist_id, profile_id)
    with playlist_pipeline_progress_lock:
        playlist_pipeline_progress_states[key] = dict(state)
        return dict(playlist_pipeline_progress_states[key])


def _update_playlist_pipeline_progress(playlist_id, profile_id=None, **kwargs):
    key = _playlist_pipeline_state_key(playlist_id, profile_id)
    with playlist_pipeline_progress_lock:
        state = playlist_pipeline_progress_states.setdefault(key, {
            'run_id': key,
            'playlist_id': int(playlist_id),
            'status': 'running',
            'progress': 0,
            'phase': 'Starting pipeline...',
            'log': [],
            'started_at': time.time(),
            'finished_at': None,
            'result': None,
            'error': None,
        })
        for field in ('status', 'progress', 'phase', 'result', 'error'):
            if field in kwargs:
                state[field] = kwargs[field]
        if 'log_line' in kwargs and kwargs.get('log_line'):
            state.setdefault('log', []).append({
                'message': kwargs.get('log_line'),
                'type': kwargs.get('log_type') or 'info',
                'timestamp': time.time(),
            })
            state['log'] = state['log'][-80:]
        if state.get('status') in ('finished', 'error', 'skipped') and not state.get('finished_at'):
            state['finished_at'] = time.time()
        return dict(state)


class _PlaylistPipelineDepsProxy:
    """Forward automation deps while routing progress into playlist UI state."""

    def __init__(self, base_deps, playlist_id, profile_id):
        self._base_deps = base_deps
        self._playlist_id = playlist_id
        self._profile_id = profile_id

    def __getattr__(self, name):
        return getattr(self._base_deps, name)

    def update_progress(self, _automation_id, **kwargs):
        _update_playlist_pipeline_progress(
            self._playlist_id, profile_id=self._profile_id, **kwargs
        )


def _run_mirrored_playlist_pipeline_for_ui(playlist_id, skip_wishlist=False, profile_id=1):
    # The caller is a bare thread, so neither Flask's `g` nor the request-scoped
    # profile survives. Without this the whole pipeline — sync, organize batch,
    # failed-track Wishlist writes — silently ran as admin (profile 1) for every
    # non-default user (P0-01). Declare the owner for the whole unit of work.
    from core.profile_context import set_background_profile, reset_background_profile
    profile_token = set_background_profile(int(profile_id))
    try:
        if _get_automation_deps() is None:
            raise RuntimeError("Automation dependencies are not available")

        from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
        from core.automation.handlers.sync_playlist import auto_sync_playlist
        from core.automation.handlers._pipeline_shared import run_sync_and_wishlist
        from core.playlists.pipeline import run_mirrored_playlist_pipeline

        deps = _PlaylistPipelineDepsProxy(_get_automation_deps(), playlist_id, profile_id)
        result = run_mirrored_playlist_pipeline(
            {
                'playlist_id': str(playlist_id),
                'all': False,
                'skip_wishlist': bool(skip_wishlist),
                'profile_id': int(profile_id),
                '_automation_id': _playlist_pipeline_state_key(playlist_id, profile_id),
            },
            deps,
            refresh_fn=auto_refresh_mirrored,
            sync_one_fn=auto_sync_playlist,
            sync_and_wishlist_fn=run_sync_and_wishlist,
        )

        status = result.get('status')
        if status == 'completed':
            _update_playlist_pipeline_progress(
                playlist_id,
                profile_id=profile_id,
                status='finished',
                progress=100,
                phase='Pipeline complete',
                result=result,
            )
        elif status == 'skipped':
            _update_playlist_pipeline_progress(
                playlist_id,
                profile_id=profile_id,
                status='skipped',
                progress=100,
                phase='Pipeline already running',
                error=result.get('reason') or 'Pipeline already running',
                result=result,
                log_line=result.get('reason') or 'Pipeline already running',
                log_type='warning',
            )
        else:
            _update_playlist_pipeline_progress(
                playlist_id,
                profile_id=profile_id,
                status='error',
                progress=100,
                phase='Pipeline error',
                error=result.get('error') or 'Pipeline failed',
                result=result,
            )
    except Exception as e:
        logger.error(f"Manual mirrored playlist pipeline failed for {playlist_id}: {e}")
        _update_playlist_pipeline_progress(
            playlist_id,
            profile_id=profile_id,
            status='error',
            progress=100,
            phase='Pipeline error',
            error=str(e),
            log_line=f'Pipeline failed: {e}',
            log_type='error',
        )
    finally:
        reset_background_profile(profile_token)


@bp.route('/api/mirrored-playlists/<int:playlist_id>/pipeline/run', methods=['POST'])
def run_mirrored_playlist_pipeline_endpoint(playlist_id):
    """Run the all-in-one mirrored playlist pipeline from the playlist UI."""
    try:
        database = get_database()
        profile_id = get_current_profile_id()
        playlist = _owned_mirrored_playlist(database, playlist_id)
        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404
        if playlist.get('source') in ('file', 'beatport'):
            return jsonify({"error": "This playlist source cannot be refreshed by the pipeline"}), 400
        if _get_automation_deps() is None:
            return jsonify({"error": "Playlist pipeline is not available"}), 503
        if _get_automation_deps().state.is_pipeline_running():
            return jsonify({"error": "A playlist pipeline is already running"}), 409

        data = request.get_json(silent=True) or {}
        state = _replace_playlist_pipeline_state(playlist_id, {
            'run_id': _playlist_pipeline_state_key(playlist_id, profile_id),
            'playlist_id': int(playlist_id),
            'playlist_name': playlist.get('name') or '',
            'status': 'running',
            'progress': 0,
            'phase': 'Starting pipeline...',
            'log': [{
                'message': f"Starting pipeline for {playlist.get('name') or playlist_id}",
                'type': 'info',
                'timestamp': time.time(),
            }],
            'started_at': time.time(),
            'finished_at': None,
            'result': None,
            'error': None,
        }, profile_id=profile_id)

        threading.Thread(
            target=_run_mirrored_playlist_pipeline_for_ui,
            args=(playlist_id, bool(data.get('skip_wishlist', False)), int(profile_id)),
            daemon=True,
            name=f"playlist-pipeline-{playlist_id}",
        ).start()

        return jsonify({"success": True, "state": state})
    except Exception as e:
        logger.error(f"Error starting mirrored playlist pipeline: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/mirrored-playlists/<int:playlist_id>/pipeline/status', methods=['GET'])
def get_mirrored_playlist_pipeline_status_endpoint(playlist_id):
    """Return the latest manual pipeline progress for a mirrored playlist."""
    try:
        if not mirrored_playlist_visible(get_database().get_mirrored_playlist(playlist_id)):
            return jsonify({"error": "Playlist not found"}), 404
        state = _snapshot_playlist_pipeline_state(playlist_id)
        if not state:
            return jsonify({
                "run_id": _playlist_pipeline_state_key(playlist_id),
                # The key is profile-scoped, so a foreign run reads as idle here
                # rather than leaking another profile's phase/log/result.
                "playlist_id": int(playlist_id),
                "status": "idle",
                "progress": 0,
                "phase": "Idle",
                "log": [],
                "result": None,
                "error": None,
            })
        return jsonify(state)
    except Exception as e:
        logger.error(f"Error getting mirrored playlist pipeline status: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/playlist-pipeline/history', methods=['GET'])
def get_playlist_pipeline_history_endpoint():
    """Return persisted run history for mirrored playlist pipeline executions."""
    try:
        database = get_database()
        profile_id = get_current_profile_id()
        limit = min(max(int(request.args.get('limit', 50)), 1), 100)
        offset = max(int(request.args.get('offset', 0)), 0)
        playlist_id_raw = request.args.get('playlist_id')
        playlist_id = int(playlist_id_raw) if playlist_id_raw else None
        data = database.get_playlist_pipeline_run_history(
            profile_id=profile_id,
            playlist_id=playlist_id,
            limit=limit,
            offset=offset,
        )
        for entry in data.get('history', []):
            for key in ('before_json', 'after_json', 'result_json', 'log_lines'):
                if entry.get(key):
                    try:
                        entry[key] = json.loads(entry[key])
                    except (json.JSONDecodeError, TypeError):
                        entry[key] = [] if key == 'log_lines' else {}
                else:
                    entry[key] = [] if key == 'log_lines' else {}
        return jsonify(data)
    except Exception as e:
        logger.error(f"Error getting playlist pipeline history: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/mirrored-playlists/<int:playlist_id>', methods=['DELETE'])
def delete_mirrored_playlist_endpoint(playlist_id):
    """Delete a mirrored playlist."""
    try:
        database = get_database()
        if database.delete_mirrored_playlist(playlist_id, profile_id=_mirror_scope_profile_id()):
            return jsonify({"success": True})
        return jsonify({"error": "Playlist not found"}), 404
    except Exception as e:
        logger.error(f"Error deleting mirrored playlist: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/mirrored-playlists/<int:playlist_id>/server-link', methods=['POST'])
def link_mirrored_playlist_server_endpoint(playlist_id):
    """Record which server playlist this mirror corresponds to.

    The relationship is matched BY NAME today, fresh on every visit to the
    server tab, which is why a disambiguation modal exists at all. This stores
    the answer once the server tab has actually resolved it.

    WRITE ONLY, for now. Nothing reads these columns: the write is landing on
    its own so it can be checked against real installs before any behaviour
    depends on it. Best-effort by design — a failure here must never disturb the
    tab that called it.
    """
    try:
        database = get_database()
        profile_id = get_current_profile_id()
        if not _owned_mirrored_playlist(database, playlist_id):
            return jsonify({"error": "Playlist not found"}), 404

        data = request.get_json(silent=True) or {}
        server_playlist_id = str(data.get('server_playlist_id') or '')
        server_type = str(data.get('server_type') or '')
        if not server_playlist_id or not server_type:
            return jsonify({"error": "server_playlist_id and server_type are required"}), 400

        linked = database.link_mirrored_playlist_to_server(
            playlist_id,
            server_playlist_id,
            server_type,
            profile_id=profile_id,
        )
        return jsonify({"success": bool(linked)})
    except Exception as e:
        logger.error(f"Error linking mirrored playlist {playlist_id} to server: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/mirrored-playlists/<int:playlist_id>/clear-discovery', methods=['POST'])
def clear_mirrored_discovery_endpoint(playlist_id):
    """Clear discovery data for all tracks in a mirrored playlist, including discovery cache."""
    try:
        database = get_database()
        profile_id = _mirror_scope_profile_id()
        if not _owned_mirrored_playlist(database, playlist_id):
            return jsonify({"error": "Playlist not found"}), 404

        # Clear discovery cache entries for these tracks so re-discovery does fresh lookups
        try:
            tracks = database.get_mirrored_playlist_tracks(playlist_id, profile_id=profile_id)
            if tracks:
                conn = database._get_connection()
                cursor = conn.cursor()
                for t in tracks:
                    cache_key = _get_discovery_cache_key(t.get('track_name', ''), t.get('artist_name', ''))
                    cursor.execute(
                        "DELETE FROM discovery_match_cache WHERE normalized_title = ? AND normalized_artist = ?",
                        (cache_key[0], cache_key[1])
                    )
                conn.commit()
                logger.info(f"Cleared discovery cache for {len(tracks)} tracks in playlist {playlist_id}")
        except Exception as cache_err:
            logger.warning(f"Error clearing discovery cache: {cache_err}")

        cleared = database.clear_mirrored_playlist_discovery(playlist_id, profile_id=profile_id)
        return jsonify({"success": True, "cleared": cleared})
    except Exception as e:
        logger.error(f"Error clearing mirrored discovery: {e}")
        return jsonify({"error": str(e)}), 500

# ==================== Discovery Pool ====================

@bp.route('/api/discovery-pool', methods=['GET'])
def get_discovery_pool():
    """List matched and failed discovery tracks, optionally filtered by playlist."""
    try:
        database = get_database()
        profile_id = get_current_profile_id()
        playlist_id = request.args.get('playlist_id', type=int)

        matched = database.get_discovery_pool_matched()
        failed = database.get_discovery_pool_failed(profile_id=profile_id, playlist_id=playlist_id)
        stats = database.get_discovery_pool_stats(profile_id=profile_id)

        # Playlist list for the filter dropdown
        playlists = database.get_mirrored_playlists(profile_id=profile_id)
        playlist_options = [{'id': p['id'], 'name': p['name']} for p in playlists]

        return jsonify({
            'matched': matched,
            'failed': failed,
            'stats': stats,
            'playlists': playlist_options,
        })
    except Exception as e:
        logger.error(f"Error getting discovery pool: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/wing-it-pool', methods=['GET'])
def get_wing_it_pool():
    """List Wing It auto-matched tracks (unverified best-effort guesses), optionally per-playlist.

    These are tracks that couldn't match a metadata source and got a raw-name Wing It stub. They
    count as 'discovered' so the Discovery Pool hides them — this surfaces them so the user can
    verify and re-match. Re-matching reuses the Discovery Pool's /api/discovery-pool/fix endpoint
    (both key off the mirrored_playlist_tracks.id), and a manual match drops the track from here.
    """
    try:
        database = get_database()
        profile_id = get_current_profile_id()
        playlist_id = request.args.get('playlist_id', type=int)

        tracks = database.get_wing_it_pool(profile_id=profile_id, playlist_id=playlist_id)
        matched = database.get_wing_it_pool(profile_id=profile_id, playlist_id=playlist_id, resolved=True)
        stats = database.get_wing_it_pool_stats(profile_id=profile_id)

        playlists = database.get_mirrored_playlists(profile_id=profile_id)
        playlist_options = [{'id': p['id'], 'name': p['name']} for p in playlists]

        return jsonify({
            'tracks': tracks,
            'matched': matched,
            'stats': stats,
            'playlists': playlist_options,
        })
    except Exception as e:
        logger.error(f"Error getting wing it pool: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/discovery-pool/fix', methods=['POST'])
def fix_discovery_pool_track():
    """Manually fix a failed discovery by linking a mirrored track to a Spotify/iTunes result."""
    try:
        data = request.get_json()
        track_id = data.get('track_id')
        spotify_track = data.get('spotify_track')
        if not track_id or not spotify_track:
            return jsonify({"error": "track_id and spotify_track required"}), 400

        database = get_database()

        # Build matched_data in the same format as the discovery flow
        artists = spotify_track.get('artists', [])
        album_raw = spotify_track.get('album', '')
        image_url = spotify_track.get('image_url', '')
        if not image_url and isinstance(album_raw, dict):
            images = album_raw.get('images', [])
            image_url = images[0].get('url', '') if images else ''
        # Ensure album carries the artwork too — download pipeline checks
        # album.images / album.image_url when extracting cover art.
        if isinstance(album_raw, dict):
            album_obj = dict(album_raw)
            if image_url and not album_obj.get('image_url'):
                album_obj['image_url'] = image_url
            if image_url and not album_obj.get('images'):
                album_obj['images'] = [{'url': image_url}]
        else:
            album_obj = {'name': album_raw or ''}
            if image_url:
                album_obj['image_url'] = image_url
                album_obj['images'] = [{'url': image_url}]

        matched_data = {
            'id': spotify_track.get('id', ''),
            'name': spotify_track.get('name', ''),
            'artists': [{'name': a} if isinstance(a, str) else a for a in artists],
            'album': album_obj,
            'duration_ms': spotify_track.get('duration_ms', 0),
            'image_url': image_url,
            'source': 'spotify',
        }

        # Update the mirrored track's extra_data (merges, so a wing-it track keeps its
        # wing_it_fallback flag — that + manual_match is how the Wing It Pool lists resolved guesses).
        extra_data = {
            'discovered': True,
            'provider': 'spotify',
            'confidence': 1.0,
            'matched_data': matched_data,
            'manual_match': True,
        }
        database.update_mirrored_track_extra_data(track_id, extra_data)

        # Also save to discovery cache so future discoveries hit the cache
        # Need to get the track's original name/artist for the cache key
        try:
            conn = database._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT track_name, artist_name FROM mirrored_playlist_tracks WHERE id = ?", (track_id,))
            row = cursor.fetchone()
            if row:
                cache_key = _get_discovery_cache_key(row['track_name'], row['artist_name'])
                database.save_discovery_cache_match(
                    cache_key[0], cache_key[1], _get_active_discovery_source(), 1.0, matched_data,
                    row['track_name'], row['artist_name']
                )
        except Exception as e:
            logger.debug("discovery cache match save failed: %s", e)

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error fixing discovery pool track: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/discovery-pool/cache/<int:entry_id>', methods=['DELETE'])
def delete_discovery_pool_cache_entry(entry_id):
    """Remove a single entry from the discovery match cache."""
    try:
        database = get_database()
        if database.delete_discovery_cache_entry(entry_id):
            return jsonify({"success": True})
        return jsonify({"error": "Entry not found"}), 404
    except Exception as e:
        logger.error(f"Error deleting discovery cache entry: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/discovery-pool/rematch', methods=['POST'])
def rematch_discovery_pool_track():
    """Replace a discovery cache entry with a new match chosen by the user."""
    try:
        data = request.get_json()
        cache_id = data.get('cache_id')
        original_title = (data.get('original_title') or '').strip()
        original_artist = (data.get('original_artist') or '').strip()
        spotify_track = data.get('spotify_track')

        if not cache_id:
            return jsonify({"error": "cache_id required"}), 400

        database = get_database()

        # If no spotify_track provided, just delete the cache entry (phase 1 of rematch)
        if not spotify_track:
            database.delete_discovery_cache_entry(cache_id)
            return jsonify({"success": True, "action": "cache_cleared"})

        # spotify_track provided — delete old cache and save new match (phase 2)
        database.delete_discovery_cache_entry(cache_id)

        # Build cache entry in same format as discovery flow
        artists = spotify_track.get('artists', [])
        album_raw = spotify_track.get('album', '')
        album_obj = album_raw if isinstance(album_raw, dict) else {'name': album_raw or ''}
        image_url = spotify_track.get('image_url', '')
        if not image_url and isinstance(album_raw, dict):
            images = album_raw.get('images', [])
            image_url = images[0].get('url', '') if images else ''

        matched_data = {
            'id': spotify_track.get('id', ''),
            'name': spotify_track.get('name', ''),
            'artists': [{'name': a} if isinstance(a, str) else a for a in artists],
            'album': album_obj,
            'duration_ms': spotify_track.get('duration_ms', 0),
            'image_url': image_url,
            'source': 'spotify',
        }

        # Save to discovery cache
        normalized_title = _matching_engine().normalize_string(original_title) if original_title else ''
        normalized_artist = _matching_engine().normalize_string(original_artist) if original_artist else ''
        database.save_discovery_cache_match(
            normalized_title=normalized_title,
            normalized_artist=normalized_artist,
            provider='spotify',
            confidence=1.0,
            matched_data=matched_data,
            original_title=original_title,
            original_artist=original_artist,
        )

        return jsonify({"success": True, "action": "rematched", "name": spotify_track.get('name', '')})
    except Exception as e:
        logger.error(f"Error in discovery pool rematch: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/mirrored-playlists/<int:playlist_id>/prepare-discovery', methods=['POST'])
def prepare_mirrored_discovery(playlist_id):
    """Register a mirrored playlist into youtube_playlist_states so the YouTube discovery pipeline can run."""
    try:
        database = get_database()
        profile_id = _mirror_scope_profile_id()
        playlist = _owned_mirrored_playlist(database, playlist_id)
        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404

        tracks_data = database.get_mirrored_playlist_tracks(playlist_id, profile_id=profile_id)
        url_hash = f"mirrored_{playlist_id}"

        # Build track list in the format the YouTube discovery worker expects
        tracks = []
        for t in tracks_data:
            # Parse extra_data if present
            extra = None
            if t.get('extra_data'):
                try:
                    extra = json.loads(t['extra_data']) if isinstance(t['extra_data'], str) else t['extra_data']
                except (json.JSONDecodeError, TypeError):
                    pass
            tracks.append({
                'id': t.get('source_track_id') or f"mirrored_{t['id']}",
                'db_track_id': t['id'],
                'name': t['track_name'],
                'artists': [t['artist_name']],
                'album': t.get('album_name', ''),
                'duration_ms': t.get('duration_ms', 0),
                'extra_data': extra,
            })

        # Determine current active metadata source for provider-mismatch detection
        _current_provider = _get_active_discovery_source()
        _use_spotify = (_current_provider == 'spotify') and _spotify_client() and _spotify_client().is_spotify_authenticated()

        # Check for cached discovery results in extra_data
        pre_discovered_results = []
        pre_discovered_count = 0
        has_pending = False

        from core.discovery.manual_match import is_drifted_for_redo

        for idx, track in enumerate(tracks):
            extra = track.get('extra_data')
            if extra and extra.get('discovered'):
                cached_provider = extra.get('provider', 'spotify')

                # See core.discovery.manual_match.is_drifted_for_redo —
                # provider-drift triggers re-discovery so the active source's
                # IDs / artwork take effect, but manual matches are exempt:
                # re-running would overwrite the user's deliberate pick with
                # whatever auto-search ranks first. Pre-fix, every Playlist
                # Pipeline run clobbered manual fixes for exactly this reason.
                if is_drifted_for_redo(extra, _current_provider):
                    has_pending = True
                    dur = track.get('duration_ms', 0)
                    pre_discovered_results.append({
                        'index': idx,
                        'yt_track': track['name'],
                        'yt_artist': track['artists'][0] if track['artists'] else 'Unknown',
                        'status': 'Provider changed',
                        'status_class': 'not-found',
                        'spotify_track': '',
                        'spotify_artist': '',
                        'spotify_album': '',
                        'duration': f"{int(dur) // 60000}:{(int(dur) % 60000) // 1000:02d}" if dur else '0:00',
                        'confidence': 0,
                    })
                    continue

                # Previously found match — provider matches current source
                matched = extra.get('matched_data', {})
                artists_raw = matched.get('artists', [])
                if artists_raw and isinstance(artists_raw[0], dict):
                    artist_str = ', '.join(a.get('name', '') for a in artists_raw)
                else:
                    artist_str = ', '.join(str(a) for a in artists_raw) if artists_raw else ''
                album_raw = matched.get('album', '')
                album_str = album_raw.get('name', '') if isinstance(album_raw, dict) else (str(album_raw) if album_raw else '')
                dur = track.get('duration_ms', 0)
                result = {
                    'index': idx,
                    'yt_track': track['name'],
                    'yt_artist': track['artists'][0] if track['artists'] else 'Unknown',
                    'status': 'Found',
                    'status_class': 'found',
                    'spotify_track': matched.get('name', ''),
                    'spotify_artist': artist_str,
                    'spotify_album': album_str,
                    'duration': f"{int(dur) // 60000}:{(int(dur) % 60000) // 1000:02d}" if dur else '0:00',
                    'discovery_source': extra.get('provider', 'spotify'),
                    'confidence': extra.get('confidence', 0),
                    'matched_data': matched,
                    'spotify_data': matched,
                }
                if extra.get('manual_match'):
                    result['manual_match'] = True
                pre_discovered_results.append(result)
                pre_discovered_count += 1
            elif extra and extra.get('discovery_attempted'):
                # Previously attempted but not found — also retry if provider changed
                cached_provider = extra.get('provider', 'spotify')
                if cached_provider != _current_provider:
                    has_pending = True
                dur = track.get('duration_ms', 0)
                pre_discovered_results.append({
                    'index': idx,
                    'yt_track': track['name'],
                    'yt_artist': track['artists'][0] if track['artists'] else 'Unknown',
                    'status': 'Provider changed' if cached_provider != _current_provider else 'Not Found',
                    'status_class': 'not-found',
                    'spotify_track': '',
                    'spotify_artist': '',
                    'spotify_album': '',
                    'duration': f"{int(dur) // 60000}:{(int(dur) % 60000) // 1000:02d}" if dur else '0:00',
                    'discovery_source': cached_provider,
                    'confidence': 0,
                })
            elif not extra or (not extra.get('discovered') and not extra.get('discovery_attempted')):
                # New track — no discovery data yet
                has_pending = True
                dur = track.get('duration_ms', 0)
                pre_discovered_results.append({
                    'index': idx,
                    'yt_track': track['name'],
                    'yt_artist': track['artists'][0] if track['artists'] else 'Unknown',
                    'status': '🆕 Pending',
                    'status_class': 'not-found',
                    'spotify_track': '',
                    'spotify_artist': '',
                    'spotify_album': '',
                    'duration': f"{int(dur) // 60000}:{(int(dur) % 60000) // 1000:02d}" if dur else '0:00',
                    'confidence': 0,
                })

        # Treat as cached when at least one track has a non-drifted cached
        # discovery — same predicate the per-track loop above uses (inverse
        # polarity), so a future field change only has to land in
        # core.discovery.manual_match.is_drifted_for_redo.
        has_cached = any(
            t.get('extra_data') and
            (t['extra_data'].get('discovered') or t['extra_data'].get('discovery_attempted')) and
            not is_drifted_for_redo(t['extra_data'], _current_provider)
            for t in tracks
        )

        playlist_data = {
            'id': url_hash,
            'name': playlist['name'],
            'tracks': tracks,
            'track_count': len(tracks),
            'url': f"mirrored://{playlist['source']}/{playlist['source_playlist_id']}",
            'source': playlist['source']
        }

        youtube_playlist_states[url_hash] = {
            'playlist': playlist_data,
            'phase': 'discovered' if has_cached else 'fresh',
            'discovery_results': pre_discovered_results if has_cached else [],
            'discovery_progress': 100 if has_cached else 0,
            'spotify_matches': pre_discovered_count if has_cached else 0,
            'spotify_total': len(tracks),
            'status': 'complete' if has_cached else 'parsed',
            'url': playlist_data['url'],
            'sync_playlist_id': None,
            'converted_spotify_playlist_id': None,
            'download_process_id': None,
            'created_at': time.time(),
            'last_accessed': time.time(),
            'discovery_future': None,
            'sync_progress': {}
        }

        logger.info(f"Prepared mirrored playlist for discovery: {playlist['name']} ({len(tracks)} tracks, cached={has_cached}, matches={pre_discovered_count})")
        return jsonify({
            "success": True,
            "url_hash": url_hash,
            "from_cache": has_cached,
            "cached_matches": pre_discovered_count,
            "total_tracks": len(tracks),
            "has_pending": has_pending,
        })
    except Exception as e:
        logger.error(f"Error preparing mirrored discovery: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/mirrored-playlists/<int:playlist_id>/retry-failed-discovery', methods=['POST'])
def retry_failed_mirrored_discovery(playlist_id):
    """Re-run discovery only for tracks that failed or are pending in a mirrored playlist."""
    try:
        if not mirrored_playlist_visible(get_database().get_mirrored_playlist(playlist_id)):
            return jsonify({"error": "Playlist not found"}), 404
        url_hash = f"mirrored_{playlist_id}"
        state = youtube_playlist_states.get(url_hash)
        if not state:
            return jsonify({"error": "Discovery state not found. Run discovery first."}), 404

        if state.get('phase') == 'discovering':
            return jsonify({"error": "Discovery already in progress"}), 400

        tracks = state['playlist']['tracks']
        results = state.get('discovery_results', [])

        # Build set of found track indices
        found_indices = set()
        kept_results = []
        for r in results:
            if r.get('status_class') == 'found':
                found_indices.add(r.get('index', -1))
                kept_results.append(r)

        already_found = len(found_indices)
        retry_count = len(tracks) - already_found

        if retry_count == 0:
            return jsonify({"success": True, "retry_count": 0, "already_found": already_found, "message": "All tracks already found"})

        # Flag found tracks to skip, clear flag on others
        for i, track in enumerate(tracks):
            track['skip_discovery'] = (i in found_indices)

        # Keep only found results, remove failed/pending
        state['discovery_results'] = kept_results
        state['phase'] = 'discovering'
        state['status'] = 'discovering'
        state['discovery_progress'] = 0
        # spotify_matches stays at found count (already_found)
        state['spotify_matches'] = already_found

        # Clear discovery_attempted in DB for failed tracks so they're retryable
        try:
            db = get_database()
            for i, track in enumerate(tracks):
                if i not in found_indices:
                    db_track_id = track.get('db_track_id')
                    if db_track_id:
                        db.update_mirrored_track_extra_data(db_track_id, {
                            'discovered': False,
                            'discovery_attempted': False,
                        })
        except Exception as db_err:
            logger.error(f"Error clearing discovery_attempted in DB: {db_err}")

        # Submit worker
        future = youtube_discovery_executor.submit(_run_youtube_discovery_worker, url_hash)
        state['discovery_future'] = future

        logger.error(f"Retrying failed discovery for {url_hash}: {retry_count} tracks to retry, {already_found} already found")
        return jsonify({
            "success": True,
            "retry_count": retry_count,
            "already_found": already_found,
        })
    except Exception as e:
        logger.error(f"Error retrying failed discovery: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route('/api/mirrored-playlists/discovery-states', methods=['GET'])
def get_mirrored_discovery_states():
    """Return discovery states for any mirrored playlists that have active/completed discoveries."""
    try:
        states = []
        for url_hash, state in youtube_playlist_states.items():
            if not url_hash.startswith('mirrored_'):
                continue
            states.append({
                'url_hash': url_hash,
                'playlist_id': int(url_hash.replace('mirrored_', '')),
                'playlist': state['playlist'],
                'phase': state['phase'],
                'status': state.get('status', ''),
                'discovery_progress': state.get('discovery_progress', 0),
                'spotify_matches': state.get('spotify_matches', 0),
                'spotify_total': state.get('spotify_total', 0),
                'discovery_results': state.get('discovery_results', []),
                'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
                'download_process_id': state.get('download_process_id'),
            })
        return jsonify({"states": states})
    except Exception as e:
        logger.error(f"Error getting mirrored discovery states: {e}")
        return jsonify({"error": str(e)}), 500
