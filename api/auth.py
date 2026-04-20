"""
API key authentication for the SoulSync public API.
"""

import hashlib
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import request, current_app

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


def require_api_key(f):
    """Decorator that enforces API key authentication."""

    @wraps(f)
    def decorated(*args, **kwargs):
        # Extract key from header or query param
        api_key = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]
        if not api_key:
            api_key = request.args.get("api_key")

        if not api_key:
            return api_error("AUTH_REQUIRED", "API key is required. "
                             "Pass via Authorization: Bearer <key> header "
                             "or ?api_key= query parameter.", 401)

        config_mgr = current_app.soulsync["config_manager"]
        stored_keys = config_mgr.get("api_keys", [])
        key_hash = _hash_key(api_key)

        matched = None
        for stored in stored_keys:
            if stored.get("key_hash") == key_hash:
                matched = stored
                break

        if not matched:
            return api_error("INVALID_KEY", "Invalid API key.", 403)

        # Update last-used timestamp (best-effort, throttled to avoid rewriting
        # the full app config on every authenticated request).
        now = datetime.now(timezone.utc)
        matched["last_used_at"] = now.isoformat()
        if _should_persist_usage(key_hash, now):
            config_mgr.set("api_keys", stored_keys)

        return f(*args, **kwargs)

    return decorated
