"""keep a sign-in with oidc to the provider's lifetime (#1838).

a sign-in with oidc records on its device row which account signed in, when
the session has to be renewed, and the refresh token the provider gave it
(fernet-encrypted, never in the cookie: the cookie is signed, not
encrypted). every request of that browser, and every check of its open
socket, asks ``check_device``:

- not an oidc session (no clock on the row): nothing to do
- before the renewal time: carry on
- past it: renew server to server. the renewal has to be for the same
  account and pass the required group again (oidc_signin.refresh). on
  success the clock moves on and a rotated refresh token is stored
- no refresh token, a refusal, or the provider out of reach: the session
  ends. ending access when renewal fails is the point of #1838, so an
  unreachable provider signs people out rather than letting them ride on
- oidc turned off, or the profile no longer linked to that account: the
  session ends too, since it could never be renewed

a failed READ of the row keeps the session, like the epoch and device
checks: a db hiccup must not sign everyone out.

two requests from one browser can hit the renewal time together. the
second must not spend a refresh token the first just rotated (providers
with reuse detection revoke the whole family), so renewals take a lock per
device and re-read the row once they have it. soulsync runs one worker,
so a lock in the process is enough.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("security.oidc_session")

_TTL = 5.0
# device id -> (read at, row). the row says when to renew, so between
# renewals the check costs a dict lookup
_cache: Dict[str, Tuple[float, Optional[dict]]] = {}
_cache_lock = threading.Lock()
_device_locks: Dict[str, threading.Lock] = {}


def _device_lock(device_id: str) -> threading.Lock:
    with _cache_lock:
        lock = _device_locks.get(device_id)
        if lock is None:
            lock = _device_locks[device_id] = threading.Lock()
        return lock


def _row(device_id: str, load: Callable[[str], Optional[dict]], *, fresh: bool = False) -> Optional[dict]:
    now = time.monotonic()
    if not fresh:
        with _cache_lock:
            hit = _cache.get(device_id)
            if hit and now - hit[0] < _TTL:
                return hit[1]
    row = load(device_id)   # raises on a read error; the caller keeps the session
    with _cache_lock:
        _cache[device_id] = (now, row)
    return row


def record_sign_in(db, device_id: Optional[str], identity: str, tokens: dict,
                   claims: dict, *, now: Optional[float] = None) -> bool:
    """start the clock for a browser that just signed in with oidc. False
    means it couldn't be recorded, and the sign-in must not be kept: a
    session without a clock would never end"""
    from core.security import oidc_signin
    if not device_id:
        return False
    expires_at = oidc_signin.token_expiry(tokens, claims, now=now)
    if expires_at is None:
        return False
    refresh_token = tokens.get("refresh_token")
    ok = db.set_profile_device_oidc(
        device_id, identity=identity, expires_at=expires_at,
        refresh_token=refresh_token if isinstance(refresh_token, str) and refresh_token else None)
    forget(device_id)
    return bool(ok)


def check_device(device_id: Optional[str], db, config_manager, *,
                 now: Optional[float] = None) -> bool:
    """True keeps the session, False ends it (the caller revokes the device)."""
    if not device_id:
        return True
    now = time.time() if now is None else now
    try:
        row = _row(device_id, db.get_profile_device_oidc)
    except Exception as e:  # noqa: BLE001 - unreadable keeps the session
        logger.debug("oidc session read failed, keeping the session: %s", e)
        return True
    if not row or not row.get("identity"):
        return True
    if row.get("revoked"):
        return False

    from core.security import oidc_signin
    settings = oidc_signin.read_settings(config_manager)
    if settings is None:
        logger.info("OIDC session ended: OIDC sign-in is off")
        return False
    if row.get("profile_identity") != row["identity"]:
        logger.info("OIDC session ended: profile %s is no longer linked to that account", row.get("owner_id"))
        return False
    if now < float(row.get("expires_at") or 0):
        return True

    with _device_lock(device_id):
        try:
            row = _row(device_id, db.get_profile_device_oidc, fresh=True)
        except Exception as e:  # noqa: BLE001
            logger.debug("oidc session re-read failed, keeping the session: %s", e)
            return True
        if not row or not row.get("identity"):
            return True
        if row.get("revoked"):
            return False
        if now < float(row.get("expires_at") or 0):
            return True   # another request renewed it while this one waited
        if not row.get("refresh_token"):
            logger.info("OIDC session ended for profile %s: expired, no refresh token", row.get("owner_id"))
            return _ended(db, device_id, row)
        try:
            doc = oidc_signin.discover(settings.issuer)
            renewal = oidc_signin.refresh(settings, doc, row["refresh_token"], row["identity"], now=now)
        except oidc_signin.OidcError as e:
            logger.info("OIDC session ended for profile %s: %s", row.get("owner_id"), e)
            return _ended(db, device_id, row)
        except Exception as e:  # noqa: BLE001 - an unexpected failure is a failed renewal
            logger.warning("OIDC session renewal failed for profile %s: %s", row.get("owner_id"), e)
            return _ended(db, device_id, row)
        saved = db.set_profile_device_oidc(
            device_id, expires_at=renewal.expires_at, refresh_token=renewal.refresh_token,
            keep_refresh_token=renewal.refresh_token is None)
        forget(device_id)
        if not saved:
            # a rotated token that couldn't be stored is spent: the next
            # renewal would be refused anyway, so end it now
            logger.warning("OIDC session ended for profile %s: renewal couldn't be saved", row.get("owner_id"))
            return _ended(db, device_id, row)
        return True


def _ended(db, device_id: str, row: dict) -> bool:
    """revoke a device whose renewal just failed, while its lock is still
    held: a request that waited on the lock then re-reads a revoked row and
    stops, instead of sending the same dead refresh token to the provider
    again. returns False, the verdict for the caller"""
    try:
        owner = row.get("owner_id")
        if owner is not None:
            db.revoke_profile_device(int(owner), device_id)
    except Exception as e:  # noqa: BLE001 - the caller revokes again via end_device
        logger.debug("revoking an ended oidc session failed: %s", e)
    from core.security import devices
    devices.forget(device_id)
    forget(device_id)
    return False


def end_device(db, device_id: str) -> None:
    """revoke a device whose oidc session ended, and drop the cached reads so
    the epoch/device checks (http and the socket sweep) see it at once"""
    try:
        row = _row(device_id, db.get_profile_device_oidc)
        owner = (row or {}).get("owner_id")
        if owner is not None:
            db.revoke_profile_device(int(owner), device_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("revoking an ended oidc session failed: %s", e)
    from core.security import devices
    devices.forget(device_id)
    forget(device_id)


def forget(device_id: str) -> None:
    with _cache_lock:
        _cache.pop(device_id, None)


def _reset_for_tests() -> None:
    with _cache_lock:
        _cache.clear()
        _device_locks.clear()


__all__ = ["record_sign_in", "check_device", "end_device", "forget"]
