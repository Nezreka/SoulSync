"""Quarantine review endpoints - lifted from web_server.py.

the /api/quarantine family plus /api/review-queue/summary: list, stream,
play, A/B compare, audit, file tags, approve (one-click re-import),
recover-to-staging, delete, and clear. the two play-session helpers live
in api/verification.py - the other half of the review queue.

function bodies are byte-identical to the originals; only the decorator
changed and the two rebindable boot globals became getters at their two
call sites inside compare-stream.
"""

import os
import threading
import time

from flask import Blueprint, jsonify, request

from api.verification import _audio_file_duration_ms, _set_review_play_session
from core.runtime_state import download_tasks, tasks_lock
from core.search import stream as _search_stream
from utils.async_helpers import run_async
from utils.logging_config import get_logger

logger = get_logger("api.quarantine")

# injected by configure()
config_manager = None
docker_resolve_path = None
_serve_audio_file_with_range = None
_AUDIO_MIME_TYPES = None
_post_process_matched_download = None
_post_process_matched_download_with_verification = None
_download_orchestrator = None
_matching_engine = None
get_database = None
_automation_engine = None
_web_scan_manager = None


def configure(*, config_manager_, docker_resolve_path_,
              serve_audio_file_with_range, audio_mime_types,
              post_process_matched_download,
              post_process_matched_download_with_verification,
              download_orchestrator_getter, matching_engine_getter,
              get_database_, automation_engine_getter,
              web_scan_manager_getter):
    global config_manager, docker_resolve_path, _serve_audio_file_with_range
    global _AUDIO_MIME_TYPES, _post_process_matched_download
    global _post_process_matched_download_with_verification
    global _download_orchestrator, _matching_engine
    global get_database, _automation_engine, _web_scan_manager
    config_manager = config_manager_
    docker_resolve_path = docker_resolve_path_
    _serve_audio_file_with_range = serve_audio_file_with_range
    _AUDIO_MIME_TYPES = audio_mime_types
    _post_process_matched_download = post_process_matched_download
    _post_process_matched_download_with_verification = (
        post_process_matched_download_with_verification)
    _download_orchestrator = download_orchestrator_getter
    _matching_engine = matching_engine_getter
    get_database = get_database_
    # Rebindable at boot like the orchestrator pair, so the approve path sees
    # the live objects rather than whatever was None when this ran.
    _automation_engine = automation_engine_getter
    _web_scan_manager = web_scan_manager_getter


bp = Blueprint('quarantine', __name__)


@bp.route('/api/quarantine/clear', methods=['POST'])
def clear_quarantine():
    """Delete all files and folders inside the ss_quarantine directory."""
    import shutil
    try:
        download_path = docker_resolve_path(config_manager.get('soulseek.download_path', './downloads'))
        quarantine_path = os.path.join(download_path, 'ss_quarantine')

        if not os.path.isdir(quarantine_path):
            return jsonify({"success": True, "message": "Quarantine folder is already empty."})

        removed_files = 0
        for entry in os.listdir(quarantine_path):
            entry_path = os.path.join(quarantine_path, entry)
            try:
                if os.path.isfile(entry_path):
                    os.remove(entry_path)
                    removed_files += 1
                elif os.path.isdir(entry_path):
                    shutil.rmtree(entry_path)
                    removed_files += 1
            except Exception as e:
                logger.error(f"[Quarantine] Failed to remove {entry}: {e}")

        logger.info(f"[Quarantine] Cleared {removed_files} item(s) from quarantine folder")
        return jsonify({"success": True, "message": f"Quarantine cleared ({removed_files} item{'s' if removed_files != 1 else ''} removed)."})
    except Exception as e:
        logger.error(f"[Quarantine] Error clearing quarantine: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _get_quarantine_dir():
    return os.path.join(
        docker_resolve_path(config_manager.get('soulseek.download_path', './downloads')),
        'ss_quarantine',
    )


@bp.route('/api/quarantine/list', methods=['GET'])
def list_quarantine():
    """Return all quarantined files with sidecar metadata."""
    try:
        from core.imports.quarantine import list_quarantine_entries
        entries = list_quarantine_entries(_get_quarantine_dir())
        return jsonify({"success": True, "entries": entries})
    except Exception as e:
        logger.error(f"[Quarantine] Error listing entries: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/review-queue/summary', methods=['GET'])
def review_queue_summary():
    """counts for the review badge, cheap enough to poll.

    the page used to load the quarantine list once and never again, so the
    number at the top was whatever it was when you last opened the tab. this is
    a listdir plus one indexed count, so the badge can just ride the downloads
    poll and the dashboard can show it too.
    """
    try:
        from core.imports.quarantine import count_quarantine_entries
        from database.music_database import MusicDatabase

        quarantined = count_quarantine_entries(_get_quarantine_dir())
        unverified = MusicDatabase().count_library_history_unverified()
        return jsonify({
            "success": True,
            "quarantine": quarantined,
            "unverified": unverified,
            "total": quarantined + unverified,
        })
    except Exception as e:
        logger.error(f"[ReviewQueue] Error building summary: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quarantine/<entry_id>', methods=['DELETE'])
def delete_quarantine_item(entry_id):
    """Delete a quarantined file + sidecar.

    With ``?siblings=1``, every other entry sharing its group key goes too
    (#1208). One rejected track can pile up a hundred candidates, and clearing
    them a row at a time - each with its own confirm - is not a review workflow.
    Same grouping the UI folds rows by, and the same one approve already uses
    for its sibling cleanup.
    """
    try:
        from core.imports.quarantine import delete_quarantine_entry
        want_siblings = str(request.args.get('siblings', '')).lower() in ('1', 'true', 'yes')
        # Read the siblings BEFORE deleting: the group key is looked up THROUGH
        # this entry, so once its sidecar is gone the group is unfindable.
        sibling_ids = []
        if want_siblings:
            from core.imports.quarantine import find_quarantine_siblings
            try:
                sibling_ids = find_quarantine_siblings(_get_quarantine_dir(), entry_id)
            except Exception as sib_exc:
                logger.warning(f"[Quarantine] Sibling lookup for {entry_id} failed: {sib_exc}")
        ok = delete_quarantine_entry(_get_quarantine_dir(), entry_id)
        if not ok:
            return jsonify({"success": False, "error": "Entry not found"}), 404
        deleted = 1
        for sib_id in sibling_ids:
            try:
                if delete_quarantine_entry(_get_quarantine_dir(), sib_id):
                    deleted += 1
            except Exception as sib_exc:
                logger.warning(f"[Quarantine] Failed deleting sibling {sib_id}: {sib_exc}")
        if deleted > 1:
            logger.info(f"[Quarantine] Deleted {entry_id} + {deleted - 1} sibling candidate(s)")
        return jsonify({"success": True, "deleted": deleted})
    except Exception as e:
        logger.error(f"[Quarantine] Error deleting {entry_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quarantine/<entry_id>/approve', methods=['POST'])
def approve_quarantine_item(entry_id):
    """One-click approve: restore the file and re-run post-process with the
    quarantine gates skipped for this explicit user-approved pass."""
    try:
        from core.imports.quarantine import approve_quarantine_entry
        # Restore inside the soulseek download dir so existing path-resolution
        # logic finds it. Unique subdir keeps it from re-mingling with active
        # transfers.
        restore_dir = os.path.join(
            docker_resolve_path(config_manager.get('soulseek.download_path', './downloads')),
            'Transfer',
        )
        _req = request.get_json(silent=True) or {}
        # #876: capture the sibling alternatives BEFORE approving — the approve
        # restores (moves) this entry's file out of quarantine, after which its
        # own group_key can no longer be looked up by id. Read-only here; the
        # actual deletion happens only after the re-import is safely kicked off.
        _sibling_ids = []
        if _req.get('remove_siblings'):
            from core.imports.quarantine import find_quarantine_siblings
            try:
                _sibling_ids = find_quarantine_siblings(_get_quarantine_dir(), entry_id)
            except Exception as sib_exc:
                logger.warning(f"[Quarantine] Sibling lookup for {entry_id} failed: {sib_exc}")
        result = approve_quarantine_entry(_get_quarantine_dir(), entry_id, restore_dir)
        if result is None:
            return jsonify({
                "success": False,
                "error": "Cannot one-click approve — entry has thin sidecar (no embedded context). Use 'Recover to Staging' instead.",
            }), 400
        restored_path, context, trigger = result
        # User approval means "import this file"; skip all quarantine gates
        # for this one restored pass so multi-reason failures do not loop.
        context['_skip_quarantine_check'] = 'all'
        context['_approved_quarantine_trigger'] = trigger
        # Acquisition tracks: the approve resolves this track by hand, so the
        # persistent retry walk must not be resumable after a restart.
        try:
            from core.acquisition.pipeline_callback import notify_quarantine_approved
            notify_quarantine_approved(context)
        except Exception as _acq_journal_exc:
            logger.debug(f"[Quarantine] acquisition retry journal close skipped: {_acq_journal_exc}")
        # If the caller (download-modal chooser) passed the originating task, run
        # the re-import through the verification WRAPPER with that task_id so the
        # task is marked completed on success — otherwise the modal row stays
        # stuck on "Quarantined" even though the file imported. The sidecar
        # context lost task_id/batch_id (the wrapper pops them before quarantine),
        # so we re-supply them here. Manager-tab approvals (no task_id) keep the
        # original inner-pipeline path.
        _task_id = (_req.get('task_id') or '').strip() or None
        _batch_id = None
        if _task_id:
            with tasks_lock:
                _t = download_tasks.get(_task_id)
                if isinstance(_t, dict):
                    _batch_id = _t.get('batch_id')
        else:
            # Manager-tab approve: no task_id in the request. Find the task that
            # owns this quarantine entry so the re-import marks it completed in
            # the downloads list without waiting for the batch to finish.
            with tasks_lock:
                for _tid, _t in download_tasks.items():
                    if isinstance(_t, dict) and _t.get('quarantine_entry_id') == entry_id:
                        _task_id = _tid
                        _batch_id = _t.get('batch_id')
                        break
        if _task_id:
            context['task_id'] = _task_id
            if _batch_id:
                context['batch_id'] = _batch_id
        context_key = f"approve_{entry_id}_{int(time.time())}"
        if _task_id:
            _reprocess = lambda: _post_process_matched_download_with_verification(
                context_key, context, restored_path, _task_id, _batch_id,
            )
        else:
            def _reprocess():
                _post_process_matched_download(context_key, context, restored_path)
                # A manager approval can outlive its original in-memory task.
                # Without a batch completion callback, external media servers
                # never learn about the newly imported file. Request the same
                # coalesced scan used by direct downloads, but only after the
                # pipeline produced a real final file and no rejection flag.
                try:
                    from core.imports.pipeline import import_rejection_reason
                    automation_engine = _automation_engine()
                    web_scan_manager = _web_scan_manager()
                    final_path = (
                        context.get('_final_processed_path')
                        or context.get('_final_path')
                    )
                    auto_scan_on = (
                        automation_engine is None
                        or automation_engine.is_event_action_enabled(
                            'batch_complete', 'scan_library'
                        )
                    )
                    if (
                        web_scan_manager
                        and auto_scan_on
                        and final_path
                        and os.path.isfile(final_path)
                        and import_rejection_reason(context) is None
                    ):
                        web_scan_manager.request_scan(
                            "Quarantine approval completed"
                        )
                except Exception as scan_exc:
                    logger.warning(
                        "[Quarantine] Post-approval media scan failed: %s",
                        scan_exc,
                    )
        threading.Thread(target=_reprocess, daemon=True).start()
        logger.info(f"[Quarantine] Approved {entry_id} (original_trigger={trigger}, bypass=all, task={_task_id}) → re-running pipeline")
        # #876: once one alternative for a song is accepted, the other
        # quarantined attempts at the SAME intended target are redundant
        # failed downloads of a track the user now owns. Delete the siblings
        # captured above (scoped to the quarantine manager via `remove_siblings`
        # — the download-modal chooser passes no flag and is unaffected).
        removed_siblings = []
        if _sibling_ids:
            from core.imports.quarantine import delete_quarantine_entry
            try:
                for sib_id in _sibling_ids:
                    if delete_quarantine_entry(_get_quarantine_dir(), sib_id):
                        removed_siblings.append(sib_id)
                if removed_siblings:
                    logger.info(f"[Quarantine] Auto-removed {len(removed_siblings)} sibling alternative(s) of {entry_id}: {removed_siblings}")
            except Exception as sib_exc:
                logger.warning(f"[Quarantine] Sibling cleanup for {entry_id} failed: {sib_exc}")
        # Cancel any still-running quarantine-retry task for the same track so
        # the engine doesn't keep fetching new candidates after the user has
        # already accepted one. Match by track title from the restored context.
        cancelled_retry_task = None
        try:
            ti = context.get('track_info') if isinstance(context.get('track_info'), dict) else {}
            _approved_name = (ti.get('name') or '').strip().lower()
            if _approved_name:
                with tasks_lock:
                    for _tid, _t in download_tasks.items():
                        if not _t.get('_quarantine_retry'):
                            continue
                        if _t.get('status') in ('completed', 'cancelled', 'failed'):
                            continue
                        _tti = _t.get('track_info') if isinstance(_t.get('track_info'), dict) else {}
                        if (_tti.get('name') or '').strip().lower() == _approved_name:
                            _t['status'] = 'cancelled'
                            _t['_quarantine_approved_alternative'] = True
                            cancelled_retry_task = _tid
                            logger.info(f"[Quarantine] Cancelled in-flight retry task {_tid} for '{_approved_name}' (user approved alternative)")
                            break
        except Exception as _crt_exc:
            logger.debug(f"[Quarantine] Retry-cancel scan failed: {_crt_exc}")
        return jsonify({
            "success": True,
            "trigger_bypassed": "all",
            "original_trigger": trigger,
            "removed_siblings": removed_siblings,
            "cancelled_retry_task": cancelled_retry_task,
        })
    except Exception as e:
        logger.error(f"[Quarantine] Error approving {entry_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quarantine/<entry_id>/stream', methods=['GET'])
def stream_quarantine_item(entry_id):
    """Stream a quarantined audio file in-app (range-supported) so the user can
    listen before deciding to approve, search again, or delete it. The file
    lives in the quarantine dir with a `.quarantined` suffix, so the real audio
    extension (and thus Content-Type) is recovered from the sidecar."""
    try:
        from core.imports.quarantine import get_quarantine_entry_stream_info
        info = get_quarantine_entry_stream_info(_get_quarantine_dir(), entry_id)
        if info is None:
            return jsonify({"error": "Quarantined file not found"}), 404
        file_path, extension = info
        mimetype = _AUDIO_MIME_TYPES.get(extension, 'audio/mpeg')
        return _serve_audio_file_with_range(file_path, mimetype_override=mimetype)
    except Exception as e:
        logger.error(f"[Quarantine] Error streaming {entry_id}: {e}")
        return jsonify({"error": str(e)}), 500


def _get_quarantine_entry(entry_id):
    from core.imports.quarantine import list_quarantine_entries
    for entry in list_quarantine_entries(_get_quarantine_dir()):
        if entry.get('id') == entry_id:
            return entry
    return None


@bp.route('/api/quarantine/<entry_id>/play', methods=['POST'])
def play_quarantine_item(entry_id):
    """Load a quarantined file into the media player (review queue ▶)."""
    try:
        from core.imports.quarantine import get_quarantine_entry_stream_info
        info = get_quarantine_entry_stream_info(_get_quarantine_dir(), entry_id)
        if info is None:
            return jsonify({"success": False, "error": "Quarantined file not found"}), 404
        file_path, extension = info
        entry = _get_quarantine_entry(entry_id) or {}
        title = entry.get('expected_track') or entry.get('original_filename') or os.path.basename(file_path)
        _set_review_play_session(
            file_path, f"{title} (quarantined)", entry.get('expected_artist'), '',
            mimetype=_AUDIO_MIME_TYPES.get(extension, 'audio/mpeg'))
        return jsonify({"success": True, "track_info": {
            "title": f"{title} (quarantined)",
            "artist": entry.get('expected_artist') or '',
            "album": '',
        }})
    except Exception as e:
        logger.error(f"[Quarantine] Play failed for {entry_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quarantine/<entry_id>/compare-stream', methods=['POST'])
def compare_stream_quarantine_item(entry_id):
    """Stream-search the EXPECTED track for a quarantined file (A/B compare),
    using the quarantined file's duration to guide candidate ranking."""
    try:
        from core.imports.quarantine import get_quarantine_entry_stream_info
        entry = _get_quarantine_entry(entry_id)
        if not entry:
            return jsonify({"success": False, "error": "Quarantine entry not found"}), 404
        track_name = entry.get('expected_track') or ''
        artist_name = entry.get('expected_artist') or ''
        if not track_name:
            return jsonify({"success": False,
                            "error": "Entry has no expected-track metadata"}), 400
        info = get_quarantine_entry_stream_info(_get_quarantine_dir(), entry_id)
        duration_ms = _audio_file_duration_ms(info[0]) if info else 0
        result = _search_stream.stream_search_track(
            track_name=track_name,
            artist_name=artist_name,
            album_name='',
            duration_ms=duration_ms,
            config_manager=config_manager,
            download_orchestrator=_download_orchestrator(),
            matching_engine=_matching_engine(),
            run_async=run_async,
        )
        if result is None:
            return jsonify({"success": False,
                            "error": "No suitable stream candidate found"}), 404
        result['title'] = track_name
        result['artist'] = artist_name
        return jsonify({"success": True, "result": result})
    except Exception as e:
        logger.error(f"[Quarantine] Compare-stream failed for {entry_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quarantine/<entry_id>/entry', methods=['GET'])
def get_quarantine_audit_entry(entry_id):
    """Synthesize a library_history-shaped entry from a quarantine sidecar so
    the review queue opens the SAME Audit Trail modal for quarantined files
    (they were never imported, so no history row exists). ``id`` is None on
    purpose — the modal fetches tags/lyrics through ``_file_tags_url``."""
    try:
        from core.imports.quarantine import (
            get_quarantine_entry_context, get_quarantine_entry_stream_info)
        entry = _get_quarantine_entry(entry_id)
        if not entry:
            return jsonify({"success": False, "error": "Quarantine entry not found"}), 404
        ctx = get_quarantine_entry_context(_get_quarantine_dir(), entry_id)
        info = get_quarantine_entry_stream_info(_get_quarantine_dir(), entry_id)
        osr = ctx.get('original_search_result') if isinstance(ctx.get('original_search_result'), dict) else {}
        username = (osr.get('username') or '') if isinstance(osr, dict) else ''
        streaming = ('tidal', 'youtube', 'qobuz', 'hifi', 'deezer_dl',
                     'lidarr', 'soundcloud', 'amazon')
        ti = ctx.get('track_info') if isinstance(ctx.get('track_info'), dict) else {}
        album_raw = ti.get('album', '')
        album_name = album_raw.get('name', '') if isinstance(album_raw, dict) else str(album_raw or '')
        synthetic = {
            'id': None,
            'event_type': 'download',
            'title': entry.get('expected_track') or entry.get('original_filename') or '',
            'artist_name': entry.get('expected_artist') or '',
            'album_name': album_name,
            'created_at': entry.get('timestamp') or '',
            'thumb_url': entry.get('thumb_url') or '',
            'file_path': info[0] if info else '',
            'quality': ctx.get('_audio_quality') or '',
            'download_source': username if username in streaming else ('soulseek' if username else ''),
            'source_filename': entry.get('source_filename') or '',
            'source_artist': (osr.get('artist') or '') if isinstance(osr, dict) else '',
            'source_track_title': (osr.get('title') or osr.get('name') or '') if isinstance(osr, dict) else '',
            'acoustid_result': 'fail' if entry.get('trigger') == 'acoustid' else None,
            'verification_status': None,
            '_quarantined': True,
            '_quarantine_reason': entry.get('reason') or '',
            '_file_tags_url': f"/api/quarantine/{entry_id}/file-tags",
        }
        return jsonify({"success": True, "entry": synthetic})
    except Exception as e:
        logger.error(f"[Quarantine] Audit entry failed for {entry_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/quarantine/<entry_id>/file-tags', methods=['GET'])
def get_quarantine_file_tags(entry_id):
    """Embedded tags of a quarantined file — feeds the Audit modal's Tags /
    Lyrics tabs. mutagen detects the format from content, so the swapped
    `.quarantined` extension is no obstacle."""
    try:
        from core.imports.quarantine import get_quarantine_entry_stream_info
        from core.library.file_tags import read_embedded_tags
        info = get_quarantine_entry_stream_info(_get_quarantine_dir(), entry_id)
        if info is None:
            return jsonify({'success': False, 'error': 'Quarantined file not found'}), 404
        result = read_embedded_tags(info[0])
        return jsonify({'success': True, **result})
    except Exception as e:
        logger.error(f"[Quarantine] File-tags failed for {entry_id}: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500



@bp.route('/api/quarantine/<entry_id>/recover', methods=['POST'])
def recover_quarantine_item(entry_id):
    """Fallback for legacy thin sidecars: move file into Staging so the user
    can manually finish via the existing Import flow."""
    try:
        from core.acquisition.recovery import recover_quarantine_entry_to_staging
        from core.imports.staging import get_staging_path
        recovery = recover_quarantine_entry_to_staging(
            get_database()._get_connection,
            quarantine_dir=_get_quarantine_dir(),
            staging_dir=get_staging_path(),
            entry_id=entry_id,
        )
        if not recovery:
            return jsonify({"success": False, "error": "Entry not found"}), 404
        return jsonify({
            "success": True,
            "staged_path": recovery.staged_path,
            "recovery": recovery.to_public_dict(),
        })
    except Exception as e:
        logger.error(f"[Quarantine] Error recovering {entry_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500



def create_blueprint() -> Blueprint:
    return bp
