"""MetaSync connection: pairing, pool-key handoff, status (/api/metasync endpoints).

MetaSync is a separate local program (its own UI, default
http://localhost:8790) that joins metadata pools — SIN is the first — on this
install's behalf. This module is SoulSync's half of that connection, driven
from Settings > Advanced > MetaSync:

* Pairing (one-click): SoulSync asks the node for a pairing code, the user
  approves it in the node's dashboard, then SoulSync claims the pairing by
  handing the node a freshly minted SoulSync ``sk_`` API key. The node answers
  with its own local API key, which is all SoulSync needs to talk to it after
  that. The manual alternative is pasting the node's local API key straight
  into the settings form; both paths land in the same stored state.
* Pool key: the user's own pool account key is stored here (SoulSync is the
  source of truth) and handed to the node on demand; the node exchanges it
  with the pool itself.
* Status / disconnect proxy the node's /local/status and /local/disconnect.
* Auto-detect (POST /api/metasync/detect): probes a short fixed list of
  places the node usually lives (see _detect_candidates) and stores the
  first one whose /health answers as a MetaSync node, so most users never
  type the node URL at all. The manual field stays as the fallback.

Auth model (matches the sidebar-weather posture): the GETs are undecorated
(they expose no secrets — keys are reported as set/not-set only), the writes
carry ``@admin_only`` from ``core.profile_context``.

Config keys (dot-notation via ConfigManager):
- ``metasync.node_url`` — where the node lives. Blank = not set up.
- ``metasync.pool_url`` / ``metasync.pool_key`` — the pool and the user's key
  for it. ``pool_key`` is a sensitive path (encrypted at rest, masked in
  settings reads; see core/settings.py).
- ``metasync.local_api_key`` — the node's key for SoulSync, issued at pairing
  or pasted by hand. Sensitive path too; it is NEVER returned by these
  endpoints, in full or in part.
- ``metasync.node_id`` / ``metasync.node_fingerprint`` — the paired node's
  identity, shown so the user can compare it with the node's dashboard.
- ``metasync.soulsync_key_id`` — id of the ``sk_`` key record minted for the
  node at claim time, so a re-pair or disconnect can revoke it.

The pending pairing (session id + code) lives in module memory only: the
code is a short-lived secret and is never written to the config store. A
server restart drops it; the user just requests a new code.

The minted ``sk_`` key's scope: the key machinery (api/auth.py) knows only
"admin" and "read", and a read key may only make safe GETs — it cannot
create or sync playlists, which is half of what the node exists to do. So
the claim mints an admin key; that is the least scope this machinery has
that covers the node's job (library / watchlist / wishlist reads + playlist
create / sync). If a narrower scope ever exists, mint that instead.
"""

from __future__ import annotations

import threading
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, request

from core.profile_context import admin_only
from utils.logging_config import get_logger

logger = get_logger("metasync_settings")

bp = Blueprint("metasync_settings", __name__)

# Set by configure() at app startup (web_server.py); None in tests until
# configure is called there.
config_manager = None

_NODE_TIMEOUT_SECONDS = 10

# Auto-detect probes: the node's port is fixed, and each probe gets a short
# leash — five dead candidates at the 10s node timeout would feel hung.
_NODE_PORT = 8790
_DETECT_TIMEOUT_SECONDS = 1.5

# The one pending pairing, if any: {session_id, pairing_code,
# node_fingerprint, node_url}. Module memory on purpose (see docstring).
_pairing_lock = threading.Lock()
_pending_pairing: dict | None = None


def configure(*, config_manager):
    globals()["config_manager"] = config_manager


def create_blueprint():
    return bp


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cfg():
    return config_manager


def _stored(key, default=""):
    value = _cfg().get(f"metasync.{key}", default)
    return value if value is not None else default


def _node_url(body=None):
    """The node URL for this request: body's, else the stored one."""
    if body:
        candidate = str(body.get("node_url") or "").strip()
        if candidate:
            return candidate.rstrip("/")
    return str(_stored("node_url")).strip().rstrip("/")


def _clear_config_leaf(key):
    """Blank one ``metasync.*`` leaf, secrets included.

    ConfigManager.set() treats ''/None for a sensitive path as "keep the
    existing value" (a settings form round-tripping a masked field must not
    wipe the secret), so clearing goes through the parent dict: replace the
    whole section with the same dict minus the leaf's value.
    """
    cfg = _cfg()
    parent, _, leaf = key.rpartition(".")
    section = dict(cfg.get(parent, {}) or {})
    section[leaf] = ""
    cfg.set(parent, section)


class _NodeUnreachable(Exception):
    """The node could not be reached at all (no answer, not a bad answer)."""


def _node_request(method, node_url, path, *, local_key=None, json_body=None,
                  timeout=_NODE_TIMEOUT_SECONDS):
    """One call to the node (its /local/* surface, or /health for probing).

    Returns (status_code, payload dict). Raises _NodeUnreachable when no
    answer came back — the caller decides what that means, because for the
    pairing claim "no answer" and "the node said no" are different worlds
    (the claim may have completed on the node's side).
    """
    headers = {}
    if local_key:
        headers["Authorization"] = f"Bearer {local_key}"
    try:
        resp = requests.request(
            method,
            node_url.rstrip("/") + path,
            json=json_body,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise _NodeUnreachable(str(exc)) from exc
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return resp.status_code, payload


def _settings_view():
    """What the settings page may see: urls and set/not-set flags, no secrets."""
    local_key = str(_stored("local_api_key"))
    pool_key = str(_stored("pool_key"))
    with _pairing_lock:
        pairing = dict(_pending_pairing) if _pending_pairing else None
    return {
        "success": True,
        "node_url": str(_stored("node_url")),
        "pool_url": str(_stored("pool_url")),
        "pool_key_set": bool(pool_key),
        "local_key_set": bool(local_key),
        "node_id": str(_stored("node_id")),
        "node_fingerprint": str(_stored("node_fingerprint")),
        "pairing_pending": bool(pairing),
        "pairing_fingerprint": pairing.get("node_fingerprint") if pairing else None,
    }


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@bp.route("/api/metasync", methods=["GET"])
def get_metasync_settings():
    return jsonify(_settings_view())


@bp.route("/api/metasync/settings", methods=["PUT"])
@admin_only
def put_metasync_settings():
    body = request.get_json(silent=True) or {}
    cfg = _cfg()
    if "node_url" in body:
        cfg.set("metasync.node_url", str(body.get("node_url") or "").strip().rstrip("/"))
    if "pool_url" in body:
        cfg.set("metasync.pool_url", str(body.get("pool_url") or "").strip().rstrip("/"))
    # Secrets: only a non-empty value replaces the stored one. An empty field
    # means "unchanged" — the form never receives the stored secret, so it
    # cannot send it back (ConfigManager.set guards sensitive paths the same
    # way; this keeps the intent explicit).
    pool_key = str(body.get("pool_key") or "").strip()
    if pool_key:
        cfg.set("metasync.pool_key", pool_key)
    local_key = str(body.get("local_api_key") or "").strip()
    if local_key:
        cfg.set("metasync.local_api_key", local_key)
    return jsonify(_settings_view())


def _detect_candidates():
    """Where the node might live, best guess first, deduplicated.

    SSRF posture: this list is fixed by construction — the stored URL, the
    host of this very request, two loopback aliases and the Docker host
    alias, all on the node's one fixed port. Nothing user-supplied reaches
    it: the detect endpoint takes no URL or host input in its body.
    """
    candidates = []

    def _add(url):
        url = str(url or "").strip().rstrip("/")
        if url and url not in candidates:
            candidates.append(url)

    # A stored URL is the user's own statement of where their node is —
    # verify it before guessing anywhere else.
    _add(_stored("node_url"))
    # The address the admin's browser used to reach SoulSync: MetaSync
    # normally runs on that same machine (localhost and LAN access alike).
    host = urlparse(request.host_url).hostname
    if host:
        _add(f"http://{host}:{_NODE_PORT}")
    _add(f"http://localhost:{_NODE_PORT}")
    _add(f"http://127.0.0.1:{_NODE_PORT}")
    # SoulSync in Docker, MetaSync on the host machine.
    _add(f"http://host.docker.internal:{_NODE_PORT}")
    return candidates


@bp.route("/api/metasync/detect", methods=["POST"])
@admin_only
def post_detect():
    """Probe the candidate list for a live MetaSync node.

    A candidate counts only when its /health answers 200 AND names the
    service "metasync-node" — any other program that happens to sit on
    port 8790 must never be mistaken for the node. Finding nothing is a
    normal outcome, not an error: the manual URL field is the fallback.
    """
    for candidate in _detect_candidates():
        try:
            status, payload = _node_request(
                "GET", candidate, "/health", timeout=_DETECT_TIMEOUT_SECONDS)
        except _NodeUnreachable:
            continue
        if status == 200 and payload.get("service") == "metasync-node":
            _cfg().set("metasync.node_url", candidate)
            logger.info(f"metasync detect: node found at {candidate}")
            return jsonify({
                "success": True,
                "found": True,
                "node_url": candidate,
                "version": str(payload.get("version") or ""),
            })
    return jsonify({"success": True, "found": False})


@bp.route("/api/metasync/pair/request", methods=["POST"])
@admin_only
def post_pair_request():
    global _pending_pairing
    body = request.get_json(silent=True) or {}
    node_url = _node_url(body)
    if not node_url:
        return jsonify({"success": False,
                        "error": "Enter the MetaSync URL first."}), 400
    _cfg().set("metasync.node_url", node_url)
    try:
        status, payload = _node_request("POST", node_url, "/local/pair/request")
    except _NodeUnreachable:
        return jsonify({"success": False,
                        "error": f"MetaSync is unreachable at {node_url}. "
                                 "Is it running?"}), 200
    if status != 200 or not payload.get("session_id"):
        error = payload.get("error") or f"MetaSync answered HTTP {status}."
        if error == "pairing_unavailable":
            error = ("MetaSync already has a pairing in progress — approve or "
                     "reject it in the MetaSync dashboard, then try again.")
        return jsonify({"success": False, "error": error}), 200
    with _pairing_lock:
        _pending_pairing = {
            "session_id": payload["session_id"],
            "pairing_code": payload.get("pairing_code", ""),
            "node_fingerprint": payload.get("node_fingerprint", ""),
            "node_url": node_url,
        }
    return jsonify({
        "success": True,
        "pairing_code": payload.get("pairing_code", ""),
        "node_fingerprint": payload.get("node_fingerprint", ""),
    })


@bp.route("/api/metasync/pair/claim", methods=["POST"])
@admin_only
def post_pair_claim():
    global _pending_pairing
    with _pairing_lock:
        pairing = dict(_pending_pairing) if _pending_pairing else None
    if not pairing:
        return jsonify({"success": False,
                        "error": "No pairing in progress — request a code first."}), 400
    node_url = pairing.get("node_url") or _node_url()
    cfg = _cfg()

    # A fresh key per claim attempt. What happens to it depends on the
    # outcome (see below) — it must never linger unaccounted for.
    from api import key_store
    from api.auth import SCOPE_ADMIN
    raw_key, record = key_store.create_key(cfg, "MetaSync", SCOPE_ADMIN)

    # The address the node should call back on: the one this request arrived
    # through, which is by construction an address this install answers on.
    soulsync_url = request.host_url.rstrip("/")
    try:
        status, payload = _node_request("POST", node_url, "/local/pair/claim", json_body={
            "session_id": pairing["session_id"],
            "pairing_code": pairing["pairing_code"],
            "soulsync_name": "SoulSync",
            "soulsync_url": soulsync_url,
            "soulsync_api_key": raw_key,
        })
    except _NodeUnreachable:
        # The claim may still have completed on the node's side (answer lost
        # on the wire). Keep the minted key and remember its id: if it was
        # claimed, revoking it here would break a working pairing; the next
        # successful claim or a disconnect revokes it instead.
        cfg.set("metasync.soulsync_key_id", record["id"])
        return jsonify({"success": False,
                        "error": f"MetaSync at {node_url} did not answer. The "
                                 "claim may or may not have completed — check "
                                 "the MetaSync dashboard before retrying."}), 200
    if status != 200 or not payload.get("local_api_key"):
        # The node answered and said no (not approved yet, code expired, ...).
        # It stored nothing, so the minted key is dead weight — revoke it.
        key_store.revoke_key(cfg, record["id"])
        return jsonify({"success": False,
                        "error": "MetaSync did not accept the claim. Approve "
                                 "the code in the MetaSync dashboard first — "
                                 "and request a new code if this one expired "
                                 "(codes last 10 minutes)."}), 200

    # Success: the node stored the key we just minted. Retire the previous
    # pairing's key, if any — the node now holds the new one.
    previous_key_id = str(_stored("soulsync_key_id"))
    if previous_key_id and previous_key_id != record["id"]:
        key_store.revoke_key(cfg, previous_key_id)
    cfg.set("metasync.soulsync_key_id", record["id"])
    cfg.set("metasync.local_api_key", payload["local_api_key"])
    cfg.set("metasync.node_id", str(payload.get("node_id") or ""))
    cfg.set("metasync.node_fingerprint", str(payload.get("node_fingerprint") or ""))
    with _pairing_lock:
        _pending_pairing = None
    return jsonify({
        "success": True,
        "node_id": str(payload.get("node_id") or ""),
        "node_fingerprint": str(payload.get("node_fingerprint") or ""),
    })


@bp.route("/api/metasync/handoff", methods=["POST"])
@admin_only
def post_handoff():
    body = request.get_json(silent=True) or {}
    cfg = _cfg()
    node_url = _node_url(body)
    local_key = str(_stored("local_api_key"))
    if not local_key:
        return jsonify({"success": False,
                        "error": "Connect MetaSync first (pairing or a pasted "
                                 "local API key) — there is nothing to hand "
                                 "the pool key to yet."}), 400
    pool_url = str(body.get("pool_url") or _stored("pool_url")).strip().rstrip("/")
    pool_key = str(body.get("pool_key") or "").strip() or str(_stored("pool_key"))
    if not pool_url or not pool_key:
        return jsonify({"success": False,
                        "error": "Enter the pool URL and your pool key first."}), 400
    # SoulSync is the source of truth for the pool key: persist what was used.
    cfg.set("metasync.pool_url", pool_url)
    cfg.set("metasync.pool_key", pool_key)
    try:
        # The node's handoff reads the key as "pool_api_key" (its own tests
        # pin that name); the contract name "pool_key" goes along so both
        # spellings reach it. Same value in both.
        status, payload = _node_request(
            "POST", node_url, "/local/handoff/pool-key",
            local_key=local_key,
            json_body={"pool_url": pool_url,
                       "pool_key": pool_key,
                       "pool_api_key": pool_key})
    except _NodeUnreachable:
        return jsonify({"success": False,
                        "error": f"MetaSync is unreachable at {node_url}. "
                                 "The pool key was saved here but not handed "
                                 "over."}), 200
    if status == 401:
        return jsonify({"success": False,
                        "error": "MetaSync rejected the stored local key. "
                                 "Reconnect (pair again or paste a fresh local "
                                 "API key)."}), 200
    if status != 200 or not payload.get("ok"):
        return jsonify({"success": False,
                        "error": payload.get("error") or
                                 f"MetaSync answered HTTP {status}."}), 200
    return jsonify({
        "success": True,
        "exchange_status": payload.get("exchange_status"),
        "exchange_error": payload.get("exchange_error"),
        "pool": payload.get("pool"),
    })


@bp.route("/api/metasync/status", methods=["GET"])
def get_metasync_status():
    node_url = _node_url()
    local_key = str(_stored("local_api_key"))
    if not node_url:
        return jsonify({"success": False, "connected": False,
                        "error": "MetaSync URL is not set yet."})
    if not local_key:
        return jsonify({"success": False, "connected": False,
                        "error": "Not connected — no local API key stored. "
                                 "Pair with MetaSync or paste its local key."})
    try:
        status, payload = _node_request("GET", node_url, "/local/status",
                                        local_key=local_key)
    except _NodeUnreachable:
        return jsonify({"success": False, "connected": False,
                        "error": f"MetaSync is unreachable at {node_url}."})
    if status == 401:
        return jsonify({"success": False, "connected": False,
                        "error": "MetaSync rejected the stored local key. "
                                 "Reconnect (pair again or paste a fresh local "
                                 "API key)."})
    if status != 200:
        return jsonify({"success": False, "connected": False,
                        "error": f"MetaSync answered HTTP {status}."})
    return jsonify({"success": True, "connected": True, "status": payload})


@bp.route("/api/metasync/disconnect", methods=["POST"])
@admin_only
def post_disconnect():
    global _pending_pairing
    node_url = _node_url()
    local_key = str(_stored("local_api_key"))
    node_notified = False
    if node_url and local_key:
        try:
            status, _payload = _node_request("POST", node_url,
                                             "/local/disconnect",
                                             local_key=local_key, json_body={})
            node_notified = status == 200
        except _NodeUnreachable:
            node_notified = False
    # Local state is cleared whether or not the node could be told: the user
    # asked to disconnect, and a node that is off cannot be holding a live
    # instruction from us. node_notified says plainly which case it was.
    cfg = _cfg()
    with _pairing_lock:
        _pending_pairing = None
    key_id = str(_stored("soulsync_key_id"))
    if key_id:
        from api import key_store
        key_store.revoke_key(cfg, key_id)
    _clear_config_leaf("metasync.local_api_key")
    cfg.set("metasync.node_id", "")
    cfg.set("metasync.node_fingerprint", "")
    cfg.set("metasync.soulsync_key_id", "")
    return jsonify({"success": True, "node_notified": node_notified})
