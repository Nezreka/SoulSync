"""
API key authentication for the SoulSync public API.
"""

import hashlib
import hmac
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import request, current_app, g

from .helpers import api_error


# Throttle persistence of `last_used_at` so every authenticated request
# does not rewrite the full app config. Maps key_hash -> last-persisted datetime.
_USAGE_WRITE_INTERVAL = timedelta(minutes=15)
_last_persisted_usage: dict[str, datetime] = {}
_usage_lock = threading.Lock()


def _should_persist_usage(key_hash: str, now: datetime) -> bool:
    """Return True if `last_used_at` for the given key should be written to disk.

    Thread-safe: tracks the last write per key hash in memory and only returns
    True once per `_USAGE_WRITE_INTERVAL`.
    """
    with _usage_lock:
        previous = _last_persisted_usage.get(key_hash)
        if previous is None or (now - previous) >= _USAGE_WRITE_INTERVAL:
            _last_persisted_usage[key_hash] = now
            return True
        return False


def generate_api_key(label=""):
    """Generate a new API key.

    Returns (raw_key, key_record).  The raw key is shown to the user
    exactly once; only the SHA-256 hash is persisted.
    """
    raw_key = f"sk_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    record = {
        "id": str(uuid.uuid4()),
        "label": label,
        "key_hash": key_hash,
        "key_prefix": raw_key[:11],          # "sk_" + first 8 chars
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
    }
    return raw_key, record


def _hash_key(raw_key):
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _request_api_key() -> str:
    """The raw API key the request carries, or ``""``.

    Accepted transports, first match wins: ``Authorization: Bearer <key>``,
    ``X-API-Key: <key>`` (the header the *arr apps and most dashboards use),
    then the ``?api_key=`` query param (``<img>`` tags can't send headers).
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        api_key = auth_header[len("Bearer "):].strip()
        if api_key:
            return api_key
    api_key = request.headers.get("X-API-Key", "").strip()
    if api_key:
        return api_key
    return (request.args.get("api_key") or "").strip()


def _match_api_key(config_mgr, api_key):
    """Return the stored key record matching ``api_key``, or None."""
    if not api_key:
        return None
    key_hash = _hash_key(api_key)
    for stored in config_mgr.get("api_keys", []) or []:
        if hmac.compare_digest(stored.get("key_hash") or "", key_hash):
            return stored
    return None


def _stamp_admin_context(profile_name="API"):
    """Key-authed requests act with admin rights (an admin minted the key)."""
    g.is_admin = True
    g.can_download = True
    g.allowed_sides = 'both'
    if getattr(g, 'profile_id', None) is None:
        g.profile_id = 1
        g.profile_name = profile_name


def require_api_key(f):
    """Decorator that enforces API key authentication."""

    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = _request_api_key()
        if not api_key:
            return api_error("AUTH_REQUIRED", "API key is required. "
                             "Pass via Authorization: Bearer <key> header, "
                             "X-API-Key header, or ?api_key= query parameter.", 401)

        config_mgr = current_app.soulsync["config_manager"]
        matched = _match_api_key(config_mgr, api_key)
        if not matched:
            return api_error("INVALID_KEY", "Invalid API key.", 403)

        # Update last-used timestamp (best-effort, throttled to avoid rewriting
        # the full app config on every authenticated request).
        now = datetime.now(timezone.utc)
        matched["last_used_at"] = now.isoformat()
        key_hash = matched["key_hash"]
        if _should_persist_usage(key_hash, now):
            # through key_store, not set(stored_keys): that list was read before
            # this request and writing it back would undo a revoke since then
            from . import key_store
            key_store.record_use(config_mgr, key_hash, matched["last_used_at"])

        # a key is minted by an admin and acts with admin rights. the session
        # gates ran first and may have left no profile (login/pin mode, or a
        # multi-profile install with nothing picked), which made v1 admin
        # routes like request approve answer "Admin only" to a valid key.
        try:
            _stamp_admin_context(matched.get("label") or "API")
        except RuntimeError:
            pass

        return f(*args, **kwargs)

    return decorated


def request_has_valid_api_key() -> bool:
    """True when the current request carries a valid API key.

    Accepts the same transports as :func:`require_api_key` (see
    :func:`_request_api_key`).

    The session gates (login / launch PIN) use this so key-authed callers that
    cannot do cookie sessions — e.g. the Companion extension's ``<img>`` tags
    hitting ``/api/image-proxy`` — are treated with the same trust as the
    ``/api/v1/*`` public API. A valid key is admin-minted, so this grants no
    more than the v1 path-prefix exemption already does.
    """
    api_key = _request_api_key()
    if not api_key:
        return False
    try:
        config_mgr = current_app.soulsync["config_manager"]
    except Exception:
        return False
    return _match_api_key(config_mgr, api_key) is not None


def apply_api_key_request_context() -> bool:
    """Stamp the admin profile context for a valid-API-key request.

    Returns True when the current request carries a valid API key, after
    setting the same ``g`` context :func:`require_api_key` sets (admin
    rights; profile 1 when none is set). ``before_request`` gates call this
    first and return early on True.

    Without this, key-authed callers that cannot do cookie sessions — e.g.
    the Companion extension's ``fetch()`` calls — reach the profile gate
    with no session profile and 401 ``profile_required`` on every non-v1
    API path, even though the login/launch-PIN gates already trust the key.
    A valid key is admin-minted, so this grants no more than the
    ``/api/v1/*`` path-prefix exemption already does.
    """
    if not request_has_valid_api_key():
        return False
    _stamp_admin_context()
    return True
