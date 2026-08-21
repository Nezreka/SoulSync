"""
Inbound music request endpoint — accept a search query from external sources
(Discord bots, curl, etc.) and trigger the search-match-download pipeline.
"""

import threading
import time
import uuid
from datetime import datetime, timedelta

import requests as http_requests
from flask import request, current_app

from utils.logging_config import get_logger
from .auth import require_api_key
from .helpers import api_success, api_error

logger = get_logger("api_request")

# In-memory request tracking (ephemeral — survives until restart)
_pending_requests = {}
_requests_lock = threading.Lock()

# Max age before auto-cleanup
_MAX_REQUEST_AGE = timedelta(hours=1)

# How often the background cleanup timer runs. Short enough to keep memory
# bounded during idle periods, long enough that slow-polling external clients
# still see their request for close to the TTL.
_CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes

# Guards for the singleton background cleanup thread.
_cleanup_thread: "threading.Thread | None" = None
_cleanup_stop_event = threading.Event()
_cleanup_thread_lock = threading.Lock()


def _cleanup_old_requests():
    """Remove requests older than 1 hour to prevent unbounded growth."""
    cutoff = datetime.now() - _MAX_REQUEST_AGE
    with _requests_lock:
        expired = [rid for rid, r in _pending_requests.items()
                   if r.get('created_at', datetime.now()) < cutoff]
        for rid in expired:
            del _pending_requests[rid]
    return len(expired) if expired else 0


def _cleanup_loop():
    """Background thread: periodically evict expired requests."""
    while not _cleanup_stop_event.is_set():
        # wait() returns True if the event was set (shutdown), False on timeout
        if _cleanup_stop_event.wait(timeout=_CLEANUP_INTERVAL_SECONDS):
            return
        try:
            removed = _cleanup_old_requests()
            if removed:
                logger.debug(f"Request cleanup: evicted {removed} stale entries")
        except Exception as e:
            logger.warning(f"Request cleanup loop error: {e}")


def start_cleanup_thread() -> bool:
    """Start the background cleanup timer once per process.

    Returns True if a new thread was started, False if one was already
    running. Safe to call multiple times; callers in multi-worker setups
    should still gate on worker identity if they want exactly one thread
    across the entire deployment.
    """
    global _cleanup_thread
    with _cleanup_thread_lock:
        if _cleanup_thread is not None and _cleanup_thread.is_alive():
            return False
        _cleanup_stop_event.clear()
        _cleanup_thread = threading.Thread(
            target=_cleanup_loop,
            name="api-request-cleanup",
            daemon=True,
        )
        _cleanup_thread.start()
        logger.info("Started api/request cleanup timer (interval=%ss)" % _CLEANUP_INTERVAL_SECONDS)
        return True


def stop_cleanup_thread(timeout: float = 2.0) -> None:
    """Signal the cleanup thread to exit. Used in tests and shutdown paths."""
    global _cleanup_thread
    with _cleanup_thread_lock:
        thread = _cleanup_thread
        _cleanup_stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
    with _cleanup_thread_lock:
        _cleanup_thread = None
        _cleanup_stop_event.clear()


# How long to watch a transfer before giving up on seeing it finish, and how
# often to look. A single track is normally seconds; the ceiling is well inside
# the 1-hour record TTL, so a watch can never outlive the record it updates.
_COMPLETION_TIMEOUT_SECONDS = 15 * 60
_COMPLETION_POLL_SECONDS = 5


def _await_download_completion(request_id, soulseek, download_id, run_async):
    """Watch a transfer until it reaches a terminal state, then record it.

    Sets ``completed`` or ``failed``. On timeout the status is deliberately LEFT
    as ``downloading`` — the transfer may well still be going, and calling it
    failed because we stopped watching would be a worse lie than admitting the
    endpoint stopped looking. ``timed_out`` records that we did.

    States come from the shared classifier rather than a local list of strings.
    slskd reports compound states like "Completed, Errored", so a check for
    "Completed" reads a failed transfer as a successful one.
    """
    from core.downloads.status import classify_engine_state

    deadline = time.monotonic() + _COMPLETION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _cleanup_stop_event.wait(timeout=_COMPLETION_POLL_SECONDS):
            return  # server shutting down — stop watching, leave the last state
        with _requests_lock:
            if request_id not in _pending_requests:
                return  # expired or cleaned up under us

        try:
            status = run_async(soulseek.get_download_status(download_id))
        except Exception as e:  # noqa: BLE001 - a poll failure must not fail the download
            logger.debug("Request %s: could not read transfer state: %s", request_id, e)
            continue

        verdict = classify_engine_state(getattr(status, 'state', None) if status else None)
        if verdict == 'pending':
            continue

        with _requests_lock:
            req = _pending_requests.get(request_id)
            if req is None:
                return
            if verdict == 'success':
                req['status'] = 'completed'
            else:
                req['status'] = 'failed'
                req['error'] = f"Download {verdict}: {getattr(status, 'state', '') or 'unknown'}"
            req['completed_at'] = datetime.now().isoformat()
        return

    with _requests_lock:
        req = _pending_requests.get(request_id)
        if req is not None and req.get('status') == 'downloading':
            req['timed_out'] = True
            logger.info(
                "Request %s still downloading after %ss — no longer watching",
                request_id, _COMPLETION_TIMEOUT_SECONDS,
            )


def _run_search_and_download(request_id, query, notify_url):
    """Background worker: search, download, update status, notify."""
    try:
        from utils.async_helpers import run_async

        with _requests_lock:
            if request_id in _pending_requests:
                _pending_requests[request_id]['status'] = 'searching'

        soulseek = current_app._get_current_object().soulsync.get('download_orchestrator')
        if not soulseek:
            with _requests_lock:
                if request_id in _pending_requests:
                    _pending_requests[request_id]['status'] = 'failed'
                    _pending_requests[request_id]['error'] = 'Download source not configured'
            return

        result = run_async(soulseek.search_and_download_best(query))

        with _requests_lock:
            if request_id in _pending_requests:
                if result:
                    _pending_requests[request_id]['status'] = 'downloading'
                    _pending_requests[request_id]['download_id'] = result
                else:
                    _pending_requests[request_id]['status'] = 'not_found'
                    _pending_requests[request_id]['error'] = 'No match found'
                    # A terminal state, so it is stamped. `downloading` is NOT
                    # terminal and no longer claims to be — see below.
                    _pending_requests[request_id]['completed_at'] = datetime.now().isoformat()

        # WATCH THE TRANSFER TO A TERMINAL STATE (#1168).
        #
        # `downloading` used to be written once and never revisited, so the
        # `completed` the docstring promised was unreachable and a caller had no
        # way to tell success from a transfer that died. Nothing else was going
        # to update it: this endpoint's requests do not go through the batch
        # engine that tracks everything else.
        if result:
            _await_download_completion(request_id, soulseek, result, run_async)

        # Send notification to callback URL if provided. Sent AFTER the wait, so
        # the payload carries a terminal status — a callback that always says
        # "downloading" and never follows up is the same defect in webhook form.
        if notify_url:
            try:
                with _requests_lock:
                    payload = dict(_pending_requests.get(request_id, {}))
                    # Remove non-serializable datetime
                    payload.pop('created_at', None)
                http_requests.post(notify_url, json=payload, timeout=10)
            except Exception as e:
                logger.warning(f"Failed to POST to notify_url {notify_url}: {e}")

    except Exception as e:
        logger.error(f"Request {request_id} failed: {e}")
        with _requests_lock:
            if request_id in _pending_requests:
                _pending_requests[request_id]['status'] = 'failed'
                _pending_requests[request_id]['error'] = str(e)
                _pending_requests[request_id]['completed_at'] = datetime.now().isoformat()


def register_routes(bp):

    @bp.route("/request", methods=["POST"])
    @require_api_key
    def create_request():
        """Accept a music search query and trigger the download pipeline.

        Body:
            query (str, required): Search query, e.g. "Artist - Track Name"
            notify_url (str, optional): URL to POST results to on completion
            metadata (dict, optional): Passthrough data included in automation events

        Returns 202 with request_id for async status polling.
        """
        body = request.get_json(silent=True) or {}
        query = (body.get("query") or "").strip()

        if not query:
            return api_error("BAD_REQUEST", "Missing 'query' in request body.", 400)

        # Cleanup old requests on each new request
        _cleanup_old_requests()

        request_id = str(uuid.uuid4())
        notify_url = (body.get("notify_url") or "").strip() or None
        metadata = body.get("metadata") or {}

        with _requests_lock:
            _pending_requests[request_id] = {
                'request_id': request_id,
                'query': query,
                'status': 'queued',
                'created_at': datetime.now(),
                'completed_at': None,
                'download_id': None,
                'error': None,
            }

        # Emit webhook_received event so automation engine triggers fire
        engine = current_app.soulsync.get('automation_engine')
        if engine:
            engine.emit('webhook_received', {
                'query': query,
                'request_id': request_id,
                'source': 'api',
                'metadata': metadata,
            })

        # Start background search-download (Feature A: works without automations)
        app = current_app._get_current_object()
        thread = threading.Thread(
            target=lambda: _run_with_app_context(app, request_id, query, notify_url),
            daemon=True
        )
        thread.start()

        logger.info(f"Music request queued: '{query}' (id={request_id})")

        # `api_success(..., status=202)`, NOT `api_success(...), 202`. The helper
        # already returns (response, status), so appending another status built
        # ((response, 200), 202) — a nested tuple Flask refuses, which it
        # reported as a 500. The request itself queued and downloaded fine, so
        # the endpoint looked healthy in the logs while every caller saw the
        # failure. (#1168)
        return api_success({
            "request_id": request_id,
            "status": "queued",
            "query": query,
        }, status=202)

    @bp.route("/request/<request_id>", methods=["GET"])
    @require_api_key
    def get_request_status(request_id):
        """Check the status of a music request.

        queued → searching → downloading → completed / not_found / failed

        `downloading` is transitional: the worker watches the transfer and
        settles on `completed` or `failed`. It can still be the LAST state you
        see if the transfer outlives the watch window, in which case
        `timed_out` is true and the download may yet be running — the endpoint
        stopped looking, which is not the same as the download stopping.
        """
        with _requests_lock:
            req = _pending_requests.get(request_id)

        if not req:
            return api_error("NOT_FOUND", "Request not found or expired.", 404)

        return api_success({
            "request_id": req['request_id'],
            "query": req['query'],
            "status": req['status'],
            "download_id": req.get('download_id'),
            "error": req.get('error'),
            "completed_at": req.get('completed_at'),
        })


def _run_with_app_context(app, request_id, query, notify_url):
    """Run the background worker within Flask app context."""
    with app.app_context():
        _run_search_and_download(request_id, query, notify_url)
