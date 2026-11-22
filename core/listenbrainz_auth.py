"""the listenbrainz token soulsync sends on its own reads of the official api.

since nov 2026 listenbrainz wants a token on some popularity endpoints
(``popularity/top-recordings-for-artist``, ``top-release-groups-for-artist``)
and answers 401 without one. the open ones take a token too and give a token
better rate limits, so every read of api.listenbrainz.org sends one when
there is one.

which token: the global one in Settings first, then the profiles' tokens,
admins first, then by id. only tokens for the official server count (a
koito / maloja / self-hosted token means nothing to api.listenbrainz.org).
a token listenbrainz turns away is skipped for a few hours, then tried again.

the token is never logged; the refusal memory is keyed by a hash of it.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

from utils.logging_config import get_logger

logger = get_logger("listenbrainz_auth")

OFFICIAL_HOSTS = frozenset({"api.listenbrainz.org", "listenbrainz.org", "www.listenbrainz.org"})
# a refused token rests this long, then is tried again (a fixed account, a
# listenbrainz hiccup)
REJECT_TTL = 6 * 3600
# the settings + profiles read is cached this long, so a newly connected
# account is picked up within a minute without a db read on every call
CANDIDATE_TTL = 60.0

NO_TOKEN_MESSAGE = ("connect ListenBrainz (Settings > Connections) to use its top tracks; "
                    "until then they come from Deezer and your library")

_lock = threading.Lock()
_rejected: Dict[str, float] = {}
_candidates_cache: List = [0.0, []]
_missing_logged = [False]


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def is_official(base_url: Optional[str]) -> bool:
    """empty (the default) or the official listenbrainz server."""
    text = str(base_url or "").strip()
    if not text:
        return True
    host = urlparse(text if "//" in text else f"https://{text}").hostname or ""
    return host.lower() in OFFICIAL_HOSTS


def _candidates() -> List[str]:
    """every usable token, in the order they are tried."""
    tokens: List[str] = []
    try:
        from core.settings import config_manager
        token = str(config_manager.get("listenbrainz.token", "") or "").strip()
        if token and is_official(config_manager.get("listenbrainz.base_url", "")):
            tokens.append(token)
    except Exception as exc:  # noqa: BLE001
        logger.debug("listenbrainz settings token read failed: %s", exc)
    try:
        from database.music_database import get_database
        profiles = get_database().get_profiles_with_listenbrainz() or []
        profiles = sorted(profiles, key=lambda p: (not p.get("is_admin"), int(p.get("id") or 0)))
        for prof in profiles:
            token = str(prof.get("token") or "").strip()
            if token and is_official(prof.get("base_url")):
                tokens.append(token)
    except Exception as exc:  # noqa: BLE001
        logger.debug("listenbrainz profile tokens read failed: %s", exc)
    seen = set()
    return [t for t in tokens if not (t in seen or seen.add(t))]


def _cached_candidates() -> List[str]:
    now = time.monotonic()
    with _lock:
        if now < _candidates_cache[0]:
            return list(_candidates_cache[1])
    tokens = _candidates()
    with _lock:
        _candidates_cache[0] = now + CANDIDATE_TTL
        _candidates_cache[1] = list(tokens)
    return tokens


def token() -> Optional[str]:
    """the token to send, or None when there is none that works."""
    now = time.time()
    for candidate in _cached_candidates():
        with _lock:
            until = _rejected.get(_fingerprint(candidate))
            if until is not None and now >= until:
                _rejected.pop(_fingerprint(candidate), None)
                until = None
        if until is None:
            with _lock:
                _missing_logged[0] = False
            return candidate
    return None


def available() -> bool:
    return token() is not None


def headers(tok: Optional[str]) -> Dict[str, str]:
    return {"Authorization": f"Token {tok}"} if tok else {}


def reject(tok: Optional[str]) -> None:
    """listenbrainz answered 401 to this token: skip it for REJECT_TTL."""
    if not tok:
        return
    with _lock:
        _rejected[_fingerprint(tok)] = time.time() + REJECT_TTL
    logger.warning("listenbrainz turned a saved token away; it is skipped for %d hours. "
                   "check the ListenBrainz token in Settings", REJECT_TTL // 3600)


def note_missing() -> None:
    """a read that needs a token was skipped: say so once, not per artist."""
    with _lock:
        if _missing_logged[0]:
            return
        _missing_logged[0] = True
    logger.info("listenbrainz top tracks skipped: %s", NO_TOKEN_MESSAGE)


def reset() -> None:
    """tests: forget refusals, the cached tokens and the logged note."""
    with _lock:
        _rejected.clear()
        _candidates_cache[0] = 0.0
        _candidates_cache[1] = []
        _missing_logged[0] = False


__all__ = ["NO_TOKEN_MESSAGE", "REJECT_TTL", "available", "headers", "is_official",
           "note_missing", "reject", "reset", "token"]
