"""API key list / create / revoke, shared by both sets of key routes.

The Settings page calls ``/api/v1/api-keys-internal/*`` (browser session,
admin only) and API clients call ``/api/v1/api-keys`` (API key). Both used to
carry their own copy of this logic. Keys live in the app config under
``api_keys``; changes are copy-on-write under one lock so two changes at once
can't drop each other.
"""

import threading

from .auth import generate_api_key

_keys_lock = threading.Lock()


def public_view(record):
    """The fields safe to show: never the hash, never the raw key."""
    return {
        "id": record.get("id"),
        "label": record.get("label", ""),
        "key_prefix": record.get("key_prefix", ""),
        "created_at": record.get("created_at"),
        "last_used_at": record.get("last_used_at"),
    }


def created_view(raw_key, record):
    """The create/bootstrap response: the raw key, shown this one time."""
    return {
        "key": raw_key,
        "id": record["id"],
        "label": record["label"],
        "key_prefix": record["key_prefix"],
        "created_at": record["created_at"],
    }


def list_keys(cfg):
    # skip anything that isn't a key record (say, from an imported config)
    # rather than let one bad entry fail the whole list
    return [public_view(k) for k in (cfg.get("api_keys", []) or [])
            if isinstance(k, dict)]


def create_key(cfg, label=""):
    """Mint a key and store its record. Returns ``(raw_key, record)``."""
    raw_key, record = generate_api_key(label)
    with _keys_lock:
        cfg.set("api_keys", list(cfg.get("api_keys", []) or []) + [record])
    return raw_key, record


def bootstrap_key(cfg, label="Default"):
    """Mint the first key, only while none exist. Returns ``(raw_key, record)``,
    or None when a key already exists. Check and write happen under the lock,
    so two first-run requests at once can't both pass the check."""
    with _keys_lock:
        if cfg.get("api_keys", []):
            return None
        raw_key, record = generate_api_key(label)
        cfg.set("api_keys", [record])
    return raw_key, record


def record_use(cfg, key_hash, when):
    """Persist a key's last_used_at. Re-reads the list under the lock and
    writes that, never a list read before the request, so a key revoked in
    the meantime stays revoked. Writes nothing when the key is gone."""
    with _keys_lock:
        keys = list(cfg.get("api_keys", []) or [])
        for i, k in enumerate(keys):
            if isinstance(k, dict) and k.get("key_hash") == key_hash:
                keys[i] = {**k, "last_used_at": when}
                cfg.set("api_keys", keys)
                return True
    return False


def revoke_key(cfg, key_id):
    """Remove a key by id. Returns False when no key has that id."""
    with _keys_lock:
        keys = list(cfg.get("api_keys", []) or [])
        # a malformed entry is left in place: it can't match an id, and
        # revoke shouldn't rewrite records it doesn't own
        kept = [k for k in keys
                if not (isinstance(k, dict) and k.get("id") == key_id)]
        if len(kept) == len(keys):
            return False
        cfg.set("api_keys", kept)
    return True
