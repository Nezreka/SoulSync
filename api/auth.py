"""
API key authentication for the SoulSync public API.
"""

import hashlib
import hmac
import posixpath
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


# A key is either "admin" (everything, as before) or "read" (look, don't touch).
# A record without a scope is an older key and stays admin, so nothing changes
# on upgrade. A scope this code doesn't know is treated as read: a bad or
# future value should lose access, not gain it.
SCOPE_ADMIN = "admin"
SCOPE_READ = "read"
VALID_SCOPES = (SCOPE_ADMIN, SCOPE_READ)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_READ_KEY_REFUSAL = ("This API key is read-only. It can fetch library and status data "
                     "but cannot change anything or read settings, keys or profiles.")

# What a read key may fetch: GETs under /api/v1, except these. The deny-by-
# default shape is the point. Every route added later is closed to read keys
# until it is in /api/v1 on purpose, so a new GET can't leak by being forgotten.
# The first four return credentials, config or account detail (profiles carry
# the recovery question and which accounts have PINs and passwords). The video
# calendar is a GET that updates rows: it re-queues shows missing an air time
# before it answers (found by test_reachable_gets_write_nothing_when_run).
READ_KEY_API_PREFIX = "/api/v1/"
READ_KEY_DENIED_PREFIXES = (
    "/api/v1/settings",
    "/api/v1/api-keys",
    "/api/v1/api-keys-internal",
    "/api/v1/profiles",
    "/api/v1/video/calendar",
)

# the mcp endpoint takes a POST from a read key too. it only shows a read key
# the tools a read key could call here anyway, and each call is replayed
# through this same check (api/mcp.py).
READ_KEY_MCP_PATH = "/mcp"


def key_scope(record) -> str:
    """The scope of a stored key record: admin, read, or read for anything unknown."""
    scope = record.get("scope") if isinstance(record, dict) else None
    if scope is None:
        return SCOPE_ADMIN
    return scope if scope in VALID_SCOPES else SCOPE_READ


def read_key_reaches_mcp(method: str, path: str) -> bool:
    """the one route outside /api/v1 a read key may call: POST /mcp, exactly."""
    return method.upper() == "POST" and path == READ_KEY_MCP_PATH


def read_key_may_access(method: str, path: str) -> bool:
    """Whether a read-scope key may make this request."""
    if method.upper() not in _SAFE_METHODS:
        return False
    # a path that isn't already in its plain form (//, /./, /../) is refused
    # rather than normalised and trusted
    if posixpath.normpath(path) != path.rstrip("/") and path != "/":
        return False
    if not path.startswith(READ_KEY_API_PREFIX):
        return False
    for prefix in READ_KEY_DENIED_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return False
    return True


def generate_api_key(label="", scope=SCOPE_ADMIN):
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
        "scope": scope,
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
    # the scheme word is case-insensitive per the http spec, some clients
    # send "bearer <key>"
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        api_key = value.strip()
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
    key_hash = _hash_key(api_key).encode()
    for stored in config_mgr.get("api_keys", []) or []:
        # one odd record (say, from an imported config) must be skipped, not
        # allowed to break every key listed after it. non-str hashes are skipped
        # here, and encoding to bytes keeps compare_digest from raising on a
        # non-ascii str.
        stored_hash = stored.get("key_hash") if isinstance(stored, dict) else None
        if not isinstance(stored_hash, str):
            continue
        if hmac.compare_digest(stored_hash.encode("utf-8"), key_hash):
            return stored
    return None


def _stamp_admin_context(profile_name="API", scope=SCOPE_ADMIN):
    """Key-authed requests act with admin rights (an admin minted the key).

    A read key gets neither admin nor download rights, so every @admin_only
    route refuses it, GETs included.
    """
    is_admin = scope == SCOPE_ADMIN
    g.is_admin = is_admin
    g.can_download = is_admin
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
        scope = key_scope(matched)
        if scope == SCOPE_READ and not read_key_may_access(request.method, request.path):
            return api_error("INSUFFICIENT_SCOPE", _READ_KEY_REFUSAL, 403)

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

        # the companion extension calls from its own origin; seeing it ticks
        # "get the browser extension" on the setup checklist
        from core.onboarding.checklist import note_api_client
        note_api_client(request.headers.get("Origin"), config_mgr.get, config_mgr.set)

        # a key is minted by an admin and acts with admin rights. the session
        # gates ran first and may have left no profile (login/pin mode, or a
        # multi-profile install with nothing picked), which made v1 admin
        # routes like request approve answer "Admin only" to a valid key.
        try:
            _stamp_admin_context(matched.get("label") or "API", scope)
        except RuntimeError:
            pass

        return f(*args, **kwargs)

    return decorated


def matched_api_key():
    """The stored record for the key this request carries, or None."""
    api_key = _request_api_key()
    if not api_key:
        return None
    try:
        config_mgr = current_app.soulsync["config_manager"]
    except Exception:
        return None
    return _match_api_key(config_mgr, api_key)


def enforce_read_key_scope():
    """before_request check: a read key only gets what read_key_may_access allows.

    Runs ahead of the login, launch-PIN and profile gates, which each wave a
    valid key through. Admin keys and requests with no key pass untouched.
    Returns a 403 response to stop the request, or None.
    """
    record = matched_api_key()
    if record is None or key_scope(record) != SCOPE_READ:
        return None
    if read_key_may_access(request.method, request.path):
        return None
    if read_key_reaches_mcp(request.method, request.path):
        return None
    return api_error("INSUFFICIENT_SCOPE", _READ_KEY_REFUSAL, 403)


def request_has_valid_api_key() -> bool:
    """True when the current request carries a valid API key.

    Accepts the same transports as :func:`require_api_key` (see
    :func:`_request_api_key`).

    The session gates (login / launch PIN) use this so key-authed callers that
    cannot do cookie sessions — e.g. the Companion extension's ``<img>`` tags
    hitting ``/api/image-proxy`` — are treated with the same trust as the
    ``/api/v1/*`` public API. A valid admin key is admin-minted, so this grants
    no more than the v1 path-prefix exemption already does; a read key is held
    to its narrower scope by :func:`enforce_read_key_scope`, which runs before
    these gates.
    """
    return matched_api_key() is not None


def apply_api_key_request_context() -> bool:
    """Stamp the admin profile context for a valid-API-key request.

    Returns True when the current request carries a valid API key, after
    setting the same ``g`` context :func:`require_api_key` sets (admin
    rights, or none for a read key; profile 1 when none is set).
    ``before_request`` gates call this first and return early on True.

    Without this, key-authed callers that cannot do cookie sessions — e.g.
    the Companion extension's ``fetch()`` calls — reach the profile gate
    with no session profile and 401 ``profile_required`` on every non-v1
    API path, even though the login/launch-PIN gates already trust the key.
    A valid admin key is admin-minted, so this grants no more than the
    ``/api/v1/*`` path-prefix exemption already does.
    """
    record = matched_api_key()
    if record is None:
        return False
    _stamp_admin_context(scope=key_scope(record))
    return True
