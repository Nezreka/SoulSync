"""Review-queue verification endpoints - lifted from web_server.py.

the /api/verification family: stream/play/compare a reviewed download,
fetch its history row, approve it as human-verified, delete a wrong file,
and clean orphaned rows. the quarantine review routes stay in web_server
for now but share two helpers defined here (_set_review_play_session,
_audio_file_duration_ms) - web_server imports them back.

function bodies are byte-identical to the originals; only the decorator
changed and the two rebindable boot globals became getters.
"""

import os

from flask import Blueprint, jsonify

from core.profile_context import admin_only, get_current_profile_id
from core.search import stream as _search_stream
from utils.async_helpers import run_async
from utils.logging_config import get_logger

logger = get_logger("api.verification")

# injected by configure()
get_database = None
config_manager = None
_resolve_library_file_path = None
_current_stream_state = None
_serve_audio_file_with_range = None
_AUDIO_MIME_TYPES = None
_download_orchestrator = None
_matching_engine = None


def configure(*, get_database_, config_manager_, resolve_library_file_path,
              current_stream_state, serve_audio_file_with_range,
              audio_mime_types, download_orchestrator_getter,
              matching_engine_getter):
    global get_database, config_manager, _resolve_library_file_path
    global _current_stream_state, _serve_audio_file_with_range
    global _AUDIO_MIME_TYPES, _download_orchestrator, _matching_engine
    get_database = get_database_
    config_manager = config_manager_
    _resolve_library_file_path = resolve_library_file_path
    _current_stream_state = current_stream_state
    _serve_audio_file_with_range = serve_audio_file_with_range
    _AUDIO_MIME_TYPES = audio_mime_types
    _download_orchestrator = download_orchestrator_getter
    _matching_engine = matching_engine_getter


bp = Blueprint('verification', __name__)


def _get_library_history_row(history_id):
    """Fetch one full library_history row as a dict (or None)."""
    conn = get_database()._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM library_history WHERE id = ?", (history_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def _resolve_history_audio_path(row):
    """Resolve a library_history row to a playable on-disk file.

    The recorded path can go stale: Docker↔host prefix differences, or the
    media server / organizer renaming files with exotic titles (e.g.
    凸】♀】♂】←Titan) after import. Fallback chain:
    1. the recorded path as-is,
    2. `_resolve_library_file_path` (transfer/download/library prefix swap),
    3. the tracks table — the media-server mirror knows the CURRENT path for
       this title+artist even after a rename — resolved the same way.
    """
    def _lookup_titled_paths(title):
        # tracks-table mirror: paths for this title (knows the CURRENT path
        # after a media-server rename). [] on any DB error → resolver returns None.
        try:
            conn = get_database()._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT f.path AS file_path FROM lib2_track_files f"
                "  JOIN lib2_tracks t ON t.id = f.track_id"
                " WHERE f.path IS NOT NULL AND LOWER(t.title) = LOWER(?)"
                "   AND COALESCE(f.file_state, 'active') <> 'deleted'",
                (title,))
            return [r[0] for r in cursor.fetchall() if r[0]]
        except Exception as e:
            logger.debug(f"[Verification] tracks-table path fallback failed: {e}")
            return []

    from core.matching.history_paths import resolve_history_audio_path
    return resolve_history_audio_path(
        row,
        exists=os.path.exists,
        resolve_library_path=_resolve_library_file_path,
        lookup_titled_paths=_lookup_titled_paths,
    )



@bp.route('/api/verification/<int:history_id>/stream', methods=['GET'])
def stream_verification_item(history_id):
    """Stream a completed download for the verification review queue (listen
    before approving). Path comes ONLY from the history row — no client paths."""
    try:
        row = _get_library_history_row(history_id)
        if not row:
            return jsonify({"error": "History entry not found"}), 404
        file_path = _resolve_history_audio_path(row)
        if not file_path:
            return jsonify({"error": "File not found on disk"}), 404
        # _AUDIO_MIME_TYPES keys keep the dot ('.flac') — don't strip it, or
        # everything falls back to audio/mpeg and FLAC playback breaks.
        ext = os.path.splitext(file_path)[1].lower()
        mimetype = _AUDIO_MIME_TYPES.get(ext, 'audio/mpeg')
        return _serve_audio_file_with_range(file_path, mimetype_override=mimetype)
    except Exception as e:
        logger.error(f"[Verification] Error streaming history {history_id}: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route('/api/verification/<int:history_id>/entry', methods=['GET'])
def get_verification_entry(history_id):
    """Full library_history row for one review-queue item — feeds the Audit
    Trail modal when opened from the Downloads page (where the history-page
    entry cache is not populated)."""
    try:
        row = _get_library_history_row(history_id)
        if not row:
            return jsonify({"success": False, "error": "History entry not found"}), 404
        return jsonify({"success": True, "entry": row})
    except Exception as e:
        logger.error(f"[Verification] Entry fetch failed for {history_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/verification/config', methods=['GET'])
def get_verification_config():
    """Whether AcoustID/download-verification is enabled — if not, the review
    queue collapses to quarantine-only in the UI."""
    try:
        enabled = bool(config_manager.get('acoustid.enabled', False))
        require_verified = bool(config_manager.get('acoustid.require_verified', False))
        return jsonify({"success": True, "acoustid_enabled": enabled, "require_verified": require_verified})
    except Exception as e:
        return jsonify({"success": True, "acoustid_enabled": True, "error": str(e)})


def _audio_file_duration_ms(path):
    """Best-effort duration of an on-disk audio file (0 when unreadable).
    mutagen detects the format from content, so this also works for
    quarantined files whose extension was swapped to `.quarantined`."""
    try:
        import mutagen
        mf = mutagen.File(path)
        if mf and mf.info and getattr(mf.info, 'length', 0):
            return int(mf.info.length * 1000)
    except Exception:  # noqa: S110 — duration probe is best-effort; fall through to 0
        pass
    return 0


def _set_review_play_session(file_path, title, artist, album, mimetype=None):
    """Point THIS listener's media-player session at a local file — same
    mechanism as /api/library/play, so the bottom player UI drives playback
    (seek/stop/volume) instead of an invisible Audio element."""
    sess = _current_stream_state()
    with sess.lock:
        sess.update({
            "status": "ready",
            "progress": 100,
            "track_info": {
                "title": title or os.path.basename(file_path),
                "artist": artist or 'Unknown Artist',
                "album": album or '',
            },
            "file_path": file_path,
            "stream_url": None,
            "error_message": None,
            "is_library": True,
            # Content-Type hint for /stream/audio — needed for quarantined
            # files whose on-disk extension is `.quarantined`. Keyed to the
            # exact path so a stale hint can never leak onto another file.
            "mimetype_override": mimetype,
            "mimetype_override_path": file_path if mimetype else None,
        })


@bp.route('/api/verification/<int:history_id>/play', methods=['POST'])
def play_verification_item(history_id):
    """Load the downloaded file into the media player (review queue ▶)."""
    try:
        row = _get_library_history_row(history_id)
        if not row:
            return jsonify({"success": False, "error": "History entry not found"}), 404
        file_path = _resolve_history_audio_path(row)
        if not file_path:
            return jsonify({"success": False, "error": "File not found on disk"}), 404
        _set_review_play_session(
            file_path, row.get('title'), row.get('artist_name'), row.get('album_name'))
        return jsonify({"success": True, "track_info": {
            "title": row.get('title') or os.path.basename(file_path),
            "artist": row.get('artist_name') or '',
            "album": row.get('album_name') or '',
            "image_url": row.get('thumb_url') or None,
        }})
    except Exception as e:
        logger.error(f"[Verification] Play failed for {history_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/verification/<int:history_id>/compare-stream', methods=['POST'])
def compare_stream_verification_item(history_id):
    """Find the expected track on Soulseek/streaming sources for an A/B
    comparison — the SAME pipeline as the /search page play button, but fed
    server-side so the local file's duration guides candidate ranking (a
    missing duration lets e.g. 10-hour YouTube loops win and time out)."""
    try:
        row = _get_library_history_row(history_id)
        if not row:
            return jsonify({"success": False, "error": "History entry not found"}), 404
        local = _resolve_history_audio_path(row)
        duration_ms = _audio_file_duration_ms(local) if local else 0
        result = _search_stream.stream_search_track(
            track_name=row.get('title') or '',
            artist_name=row.get('artist_name') or '',
            album_name=row.get('album_name') or '',
            duration_ms=duration_ms,
            config_manager=config_manager,
            download_orchestrator=_download_orchestrator(),
            matching_engine=_matching_engine(),
            run_async=run_async,
        )
        if result is None:
            return jsonify({"success": False,
                            "error": "No suitable stream candidate found"}), 404
        result['title'] = row.get('title') or ''
        result['artist'] = row.get('artist_name') or ''
        result['album'] = row.get('album_name') or ''
        result['image_url'] = row.get('thumb_url') or None
        return jsonify({"success": True, "result": result})
    except Exception as e:
        logger.error(f"[Verification] Compare-stream failed for {history_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500



@bp.route('/api/verification/<int:history_id>/approve', methods=['POST'])
@admin_only
def approve_verification_item(history_id):
    """User confirmed the file IS the right track: set human_verified on the
    history row, the file tag, and (best-effort) the tracks row. The AcoustID
    scanner skips human-verified files entirely. Admin-only: mutates shared
    library/verification state."""
    try:
        from core.matching.verification_status import HUMAN_VERIFIED
        from core.tag_writer import write_verification_status
        db = get_database()
        row = _get_library_history_row(history_id)
        if not row:
            return jsonify({"success": False, "error": "History entry not found"}), 404
        file_path = row.get('file_path') or ''
        on_disk = _resolve_history_audio_path(row)
        with db._get_connection() as conn:
            conn.execute(
                "UPDATE library_history SET verification_status = ? WHERE id = ?",
                (HUMAN_VERIFIED, history_id))
            # The tracks row may carry either the recorded or the resolved path.
            for p in {p for p in (file_path, on_disk) if p}:
                conn.execute(
                    "UPDATE lib2_track_files SET verification_status = ? WHERE path = ?",
                    (HUMAN_VERIFIED, p))
            conn.commit()
        # iss29-E06: the resolver-backed lib2 update runs on its OWN
        # transaction, after the write above is committed. It may still have to
        # consult the path resolver for a mapped-path setup, and the resolver
        # touches the filesystem — doing that inside the transaction above held
        # the single SQLite writer across network stats on a NAS-backed library.
        from core.library2.verification import mark_file_verification_status
        with db._get_connection() as conn:
            lib2_updated = mark_file_verification_status(
                conn,
                {p for p in (file_path, on_disk) if p},
                HUMAN_VERIFIED,
                config_manager=config_manager,
            )
            conn.commit()
        tag_written = bool(on_disk) and write_verification_status(on_disk, HUMAN_VERIFIED)
        # F-10: the human step of the pipeline story. Fail-open — a history row
        # without acquisition correlation (ordinary library import) writes
        # nothing and never blocks the approval itself.
        try:
            from core.acquisition.pipeline_callback import notify_verification_decision
            notify_verification_decision(
                history_id, decision="human_verified",
                actor=f"profile:{get_current_profile_id()}",
            )
        except Exception as journal_error:  # noqa: BLE001
            logger.debug("[Verification] approve journal skipped: %s", journal_error)
        return jsonify({
            "success": True,
            "tag_written": tag_written,
            "lib2_files_updated": lib2_updated,
        })
    except Exception as e:
        logger.error(f"[Verification] Approve failed for {history_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/verification/<int:history_id>/delete', methods=['POST'])
@admin_only
def delete_verification_item(history_id):
    """User decided the file is wrong: delete it from disk and drop the
    history row (the media-server mirror cleans the tracks row on next scan).
    Admin-only: it removes a file from disk + the library."""
    try:
        db = get_database()
        row = _get_library_history_row(history_id)
        if not row:
            return jsonify({"success": False, "error": "History entry not found"}), 404
        on_disk = _resolve_history_audio_path(row)
        file_deleted = False
        if on_disk and os.path.exists(on_disk):
            os.remove(on_disk)
            file_deleted = True
            logger.info(f"[Verification] Deleted rejected file: {on_disk}")
        # F-10: journal the rejection BEFORE the row (and with it the stored
        # acquisition correlation) is deleted — afterwards there is nothing
        # left to correlate against. Fail-open as everywhere else.
        try:
            from core.acquisition.pipeline_callback import notify_verification_decision
            notify_verification_decision(
                history_id, decision="rejected", reason_code="human_rejected",
                actor=f"profile:{get_current_profile_id()}",
            )
        except Exception as journal_error:  # noqa: BLE001
            logger.debug("[Verification] reject journal skipped: %s", journal_error)
        db.delete_library_history_rows([history_id])
        return jsonify({"success": True, "file_deleted": file_deleted})
    except Exception as e:
        logger.error(f"[Verification] Delete failed for {history_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/verification/clean-orphans', methods=['POST'])
@admin_only
def clean_orphan_verification_items():
    """Remove dead review-queue rows whose file no longer exists anywhere
    (deleted / replaced / re-downloaded elsewhere). These are append-only
    library_history rows that can never be healed — there's no file left to
    confirm — so they linger in the Unverified list forever (#934).

    User-initiated only, never automatic: it does a filesystem check, which
    would mass-false-positive if the library mount were down. The pure helper
    flags that signature (every reviewed file unreachable) and we refuse. Only
    history ROWS are deleted — the files are already gone; this never removes a
    file. Admin-only: it mutates shared review state."""
    try:
        from core.downloads.orphan_history import find_orphan_history_ids
        db = get_database()
        # get_library_history_unverified() returns unverified + force_imported rows.
        # Check ALL of them so the mount-down gate sees the true count, but only
        # DELETE 'unverified' orphans — 'force_imported' is a deliberate user
        # decision (accepted a version mismatch) and stays for human approval.
        rows = db.get_library_history_unverified() or []
        result = find_orphan_history_ids(
            rows, _resolve_history_audio_path,
            deletable=lambda r: r.get('verification_status') == 'unverified')
        if result['suspicious']:
            return jsonify({
                "success": False,
                "error": "Every reviewed file is unreachable — your library may be "
                         "offline right now. Nothing was removed.",
            }), 409
        orphan_ids = result['orphan_ids']
        removed = db.delete_library_history_rows(orphan_ids) if orphan_ids else 0
        logger.info("[Verification] Cleaned %d orphaned review rows (checked %d)",
                    removed, result['checked'])
        return jsonify({"success": True, "removed": removed, "checked": result['checked']})
    except Exception as e:
        logger.error(f"[Verification] Clean orphans failed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500



def create_blueprint() -> Blueprint:
    return bp
