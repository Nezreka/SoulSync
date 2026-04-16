"""
Inbound music request endpoint — accept a search query from external sources
(Discord bots, curl, etc.) and trigger the search-match-download pipeline.
"""

import threading
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


def _cleanup_old_requests():
    """Remove requests older than 1 hour to prevent unbounded growth."""
    cutoff = datetime.now() - _MAX_REQUEST_AGE
    with _requests_lock:
        expired = [rid for rid, r in _pending_requests.items()
                   if r.get('created_at', datetime.now()) < cutoff]
        for rid in expired:
            del _pending_requests[rid]


def _run_search_and_download(request_id, query, notify_url):
    """Background worker: search, download, update status, notify."""
    try:
        from utils.async_helpers import run_async

        with _requests_lock:
            if request_id in _pending_requests:
                _pending_requests[request_id]['status'] = 'searching'

        soulseek = current_app._get_current_object().soulsync.get('soulseek_client')
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
                _pending_requests[request_id]['completed_at'] = datetime.now().isoformat()

        # Send notification to callback URL if provided
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

        return api_success({
            "request_id": request_id,
            "status": "queued",
            "query": query,
        }), 202

    @bp.route("/request/<request_id>", methods=["GET"])
    @require_api_key
    def get_request_status(request_id):
        """Check the status of a music request.

        Returns current status: queued → searching → downloading → completed/not_found/failed
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
