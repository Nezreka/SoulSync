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


# How long a request's transfer is watched before we stop looking, and how
# often the shared watcher sweeps. Comfortably inside the 1-hour record TTL, so
# a watch can never outlive the record it updates.
_COMPLETION_TIMEOUT_SECONDS = 15 * 60
_COMPLETION_POLL_SECONDS = 10

_watcher_thread = None
_watcher_lock = threading.Lock()


def _notify(req):
    """POST a terminal request record to its callback, if it asked for one."""
    url = req.get('notify_url')
    if not url:
        return
    payload = {k: v for k, v in req.items() if k not in ('created_at', 'notify_url', 'watch_until')}
    try:
        http_requests.post(url, json=payload, timeout=10)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to POST to notify_url {url}: {e}")


def _resolve_downloading_requests(soulseek, run_async):
    """One sweep: settle every watched request against a SINGLE bulk read.

    ONE API CALL FOR ALL OF THEM, not one per request. The endpoint allows 60
    requests a minute and each is watched for up to 15 minutes, so a per-request
    poll would mean hundreds of threads issuing hundreds of slskd calls a
    second — which is precisely the search flooding #1166 exists to prevent. A
    fix for one bug is not allowed to cause the other.

    Returns the records that reached a terminal state, for notification.
    """
    with _requests_lock:
        watched = {
            rid: req for rid, req in _pending_requests.items()
            if req.get('status') == 'downloading' and req.get('download_id')
        }
    if not watched:
        return []

    try:
        transfers = run_async(soulseek.get_all_downloads()) or []
    except Exception as e:  # noqa: BLE001 - a failed sweep must not settle anything
        logger.debug("Request watcher could not read transfers: %s", e)
        return []

    by_id = {}
    for t in transfers:
        tid = getattr(t, 'id', None) or (t.get('id') if isinstance(t, dict) else None)
        if tid:
            by_id[str(tid)] = t

    from core.downloads.status import classify_engine_state

    now = time.monotonic()
    settled = []
    with _requests_lock:
        for rid, _ in watched.items():
            req = _pending_requests.get(rid)
            if req is None or req.get('status') != 'downloading':
                continue

            transfer = by_id.get(str(req.get('download_id')))
            state = getattr(transfer, 'state', None) if transfer is not None else None
            verdict = classify_engine_state(state)

            if verdict == 'success':
                req['status'] = 'completed'
            elif verdict in ('failed', 'cancelled'):
                req['status'] = 'failed'
                req['error'] = f"Download {verdict}: {state or 'unknown'}"
            elif now >= req.get('watch_until', 0):
                # The transfer may well still be running. Calling it failed
                # because WE stopped watching is a worse lie than saying so.
                req['timed_out'] = True
                logger.info("Request %s still downloading after the watch window", rid)
                continue
            else:
                continue

            req['completed_at'] = datetime.now().isoformat()
            settled.append(dict(req))
    return settled


def _watcher_loop(app):
    """Single background sweep for every in-flight request."""
    while not _cleanup_stop_event.wait(timeout=_COMPLETION_POLL_SECONDS):
        try:
            with app.app_context():
                from utils.async_helpers import run_async
                soulseek = app.soulsync.get('download_orchestrator')
                if not soulseek:
                    continue
                for req in _resolve_downloading_requests(soulseek, run_async):
                    _notify(req)
        except Exception as e:  # noqa: BLE001 - the sweep must never die
            logger.error(f"Request watcher sweep failed: {e}")


def _ensure_watcher(app):
    """Start the shared watcher once, lazily, on the first real request."""
    global _watcher_thread
    with _watcher_lock:
        if _watcher_thread is not None and _watcher_thread.is_alive():
            return
        _watcher_thread = threading.Thread(
            target=_watcher_loop, args=(app,), name="api-request-watcher", daemon=True,
        )
        _watcher_thread.start()
        logger.info("Started api/request transfer watcher (interval=%ss)", _COMPLETION_POLL_SECONDS)


def _run_search_and_download(request_id, query, notify_url):
    """Background worker: search, start the download, hand off, notify.

    Returns as soon as the download is HANDED OFF. It used to sit here polling
    the transfer, which cost a live thread per request for up to fifteen
    minutes — at the endpoint's 60/minute that is hundreds of threads. The
    shared watcher settles them all from one bulk read instead.
    """
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
                    _pending_requests[request_id]['completed_at'] = datetime.now().isoformat()
                    terminal = dict(_pending_requests[request_id])
            _notify(terminal)
            return

        result = run_async(soulseek.search_and_download_best(query))

        terminal = None
        with _requests_lock:
            if request_id in _pending_requests:
                req = _pending_requests[request_id]
                if result:
                    # NOT terminal, so it is not stamped and no callback fires.
                    # The watcher settles it and notifies then — a webhook that
                    # always says "downloading" and never follows up is the same
                    # defect as a status that never leaves it. (#1168)
                    req['status'] = 'downloading'
                    req['download_id'] = result
                    req['watch_until'] = time.monotonic() + _COMPLETION_TIMEOUT_SECONDS
                else:
                    req['status'] = 'not_found'
                    req['error'] = 'No match found'
                    req['completed_at'] = datetime.now().isoformat()
                    terminal = dict(req)

        if terminal:
            _notify(terminal)

    except Exception as e:
        logger.error(f"Request {request_id} failed: {e}")
        terminal = None
        with _requests_lock:
            if request_id in _pending_requests:
                req = _pending_requests[request_id]
                req['status'] = 'failed'
                req['error'] = str(e)
                req['completed_at'] = datetime.now().isoformat()
                terminal = dict(req)
        if terminal:
            _notify(terminal)


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
                # Carried on the record so the SHARED watcher can call back when
                # it settles this request; the requesting thread is long gone by
                # then, having handed the transfer off rather than sat on it.
                'notify_url': notify_url,
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
        # One watcher for every request, started on first use. See
        # _resolve_downloading_requests for why it is not one per request.
        _ensure_watcher(app)
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
