"""where the server identity lives, and how a backup carries it.

the root secret is the file ``server_key`` next to the music database (the
folder that also holds ``.encryption_key``, mounted as a volume in docker, so
it survives container updates). a copy rides in the database's metadata
table, Fernet-encrypted with the same key as every saved credential, so a
database backup holds the identity inside the encryption that already
protects the rest:

* the file is there: it wins, and the copy is kept matching it;
* no file but a copy that decrypts (a restored backup, with its
  ``.encryption_key``): the file is written back from it, same identity;
* neither: a new identity. a fresh install always starts here.

a backup that holds the identity is a secret, and a restored copy must not
run next to the original: both would answer as the same server.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Callable, Optional

from core.connect.identity import (
    ServerIdentity,
    is_valid_key,
    read_key_file,
    write_key_file,
)

logger = logging.getLogger(__name__)

METADATA_KEY = "connect_server_key"

_lock = threading.Lock()
_cached: Optional[ServerIdentity] = None


def state_dir() -> Path:
    """the folder the database lives in (DATABASE_PATH's, as everywhere)."""
    db_path = os.environ.get("DATABASE_PATH") or os.path.join("database", "music_library.db")
    return Path(db_path).resolve().parent


def load_or_create(directory: str | Path, *,
                   mirror_get: Optional[Callable[[], Optional[str]]] = None,
                   mirror_set: Optional[Callable[[str], None]] = None) -> ServerIdentity:
    """the identity for ``directory``, restored from the mirror when the
    file is gone, made new when neither has one. a damaged file raises."""
    key = read_key_file(directory)
    if key is not None:
        if mirror_get is not None and mirror_set is not None:
            try:
                if mirror_get() != key:
                    mirror_set(key)
            except Exception as exc:  # noqa: BLE001 - the copy is a nicety, the file is the truth
                logger.warning("Server identity: could not update its database copy: %s", exc)
        return ServerIdentity(key=key)
    mirrored = None
    if mirror_get is not None:
        try:
            mirrored = mirror_get()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Server identity: its database copy is unreadable: %s", exc)
    if mirrored and is_valid_key(mirrored):
        write_key_file(directory, mirrored)
        identity = ServerIdentity(key=mirrored)
        logger.info("Server identity restored from the database (fingerprint %s)", identity.fingerprint)
        return identity
    key = secrets.token_hex(32)
    write_key_file(directory, key)
    if mirror_set is not None:
        try:
            mirror_set(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Server identity: could not save its database copy: %s", exc)
    identity = ServerIdentity(key=key)
    logger.info("New server identity made (fingerprint %s)", identity.fingerprint)
    return identity


def db_mirror(db, config_manager):
    """(get, set) for the Fernet-encrypted copy in ``db``'s metadata."""
    def get() -> Optional[str]:
        raw = db.get_metadata(METADATA_KEY)
        if not raw:
            return None
        value = config_manager._decrypt_value(raw)
        # a value that did not decrypt comes back as the token itself
        return value if isinstance(value, str) and is_valid_key(value) else None

    def put(key: str) -> None:
        token = config_manager._encrypt_value(key)
        if not token or not str(token).startswith("gAAAAA"):
            raise RuntimeError("the server key could not be encrypted")
        db.set_metadata(METADATA_KEY, token)

    return get, put


def server_identity(db=None, config_manager=None) -> ServerIdentity:
    """this server's identity, loaded once per process."""
    global _cached
    with _lock:
        if _cached is not None:
            return _cached
        if db is None:
            from database.music_database import get_database
            db = get_database()
        if config_manager is None:
            from core.settings import config_manager as _cm
            config_manager = _cm
        get, put = db_mirror(db, config_manager)
        _cached = load_or_create(state_dir(), mirror_get=get, mirror_set=put)
        return _cached


def public_view(identity: ServerIdentity) -> dict:
    """what may be shown: never the secret."""
    return {"lookup_id": identity.lookup_id, "fingerprint": identity.fingerprint,
            "public_key": identity.public_key}


def _reset_for_tests() -> None:
    global _cached
    with _lock:
        _cached = None


__all__ = ["METADATA_KEY", "state_dir", "load_or_create", "db_mirror", "server_identity",
           "public_view"]
