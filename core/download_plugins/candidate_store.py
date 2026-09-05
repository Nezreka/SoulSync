"""Server-side TTL store for indexer download URLs (audit P0-03).

Prowlarr download URLs (and magnet URIs from private trackers) can embed
indexer API keys or signed, time-limited parameters. They must never reach
the browser: search results carry an opaque candidate token in the
``filename`` field instead, and the download path resolves the token back
to the real URL server-side. A token that is unknown or expired is
rejected — the client can no longer submit an arbitrary URL for the
server to forward to SABnzbd/NZBGet/qBittorrent.

The store is in-memory and process-local, which matches the current
single-worker Gunicorn deployment (same assumption the plugins'
``active_downloads`` dicts already make). Entries expire after a TTL and
the store is size-capped so an indexer flood can't grow it unbounded.
"""

from __future__ import annotations

from copy import deepcopy
import secrets
import threading
import time
from typing import Any, Dict, Optional, Tuple

# Recognizable, URL-unlike token prefix ("soulsync candidate, v1").
TOKEN_PREFIX = "ssc1-"

# Generous window between a search and the grab that uses its result —
# covers a long manual browsing session and queued auto-grabs. Deliberately
# NOT days: these URLs may be signed/short-lived on the indexer side too.
DEFAULT_TTL_SECONDS = 6 * 60 * 60

DEFAULT_MAX_ENTRIES = 5000


class CandidateStore:
    """Token -> download-URL map with TTL and size cap."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.Lock()
        self._by_token: Dict[str, Tuple[str, float]] = {}   # token -> (url, expires_at)
        self._by_url: Dict[str, str] = {}                   # url -> token (dedup)
        # Structured indexer evidence needed at grab time. Kept beside the URL
        # so it remains server-side and cannot be forged by the browser.
        self._metadata_by_token: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def is_token(value: Optional[str]) -> bool:
        return bool(value) and value.startswith(TOKEN_PREFIX)

    def put(self, url: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Register a URL; returns its opaque token. The same URL within the
        TTL returns the same token (repeated searches don't grow the store).

        Optional metadata follows the same TTL and never crosses the client
        trust boundary. It carries structured evidence such as Prowlarr's
        category IDs from search to the later strict pre-grab gate.
        """
        with self._lock:
            # Captured under the lock, not before it: expires_at must
            # reflect actual insertion order so a call that loses the race
            # for the lock can't record an earlier timestamp than entries
            # inserted moments before it — otherwise the expiry-sorted
            # eviction in _evict_oldest_locked could pick the entry this
            # very call is about to create.
            now = time.monotonic()
            self._purge_locked(now)
            token = self._by_url.get(url)
            if token is not None and token in self._by_token:
                # Refresh the TTL — the candidate was just seen again.
                self._by_token[token] = (url, now + self._ttl)
                if metadata is not None:
                    self._metadata_by_token[token] = deepcopy(metadata)
                return token
            token = TOKEN_PREFIX + secrets.token_urlsafe(24)
            self._by_token[token] = (url, now + self._ttl)
            self._by_url[url] = token
            self._metadata_by_token[token] = deepcopy(metadata or {})
            if len(self._by_token) > self._max:
                self._evict_oldest_locked()
            return token

    def resolve(self, token: str) -> Optional[str]:
        """The URL behind a token, or None when unknown/expired."""
        url, _metadata = self.resolve_with_metadata(token)
        return url

    def resolve_with_metadata(self, token: str) -> Tuple[Optional[str], Dict[str, Any]]:
        """Return ``(url, metadata)`` or ``(None, {})`` when unavailable."""
        if not self.is_token(token):
            return None, {}
        now = time.monotonic()
        with self._lock:
            entry = self._by_token.get(token)
            if entry is None:
                return None, {}
            url, expires_at = entry
            if expires_at < now:
                self._by_token.pop(token, None)
                self._by_url.pop(url, None)
                self._metadata_by_token.pop(token, None)
                return None, {}
            return url, deepcopy(self._metadata_by_token.get(token, {}))

    def _purge_locked(self, now: float) -> None:
        expired = [t for t, (_u, exp) in self._by_token.items() if exp < now]
        for token in expired:
            url, _exp = self._by_token.pop(token)
            self._by_url.pop(url, None)
            self._metadata_by_token.pop(token, None)

    def _evict_oldest_locked(self) -> None:
        overflow = len(self._by_token) - self._max
        if overflow <= 0:
            return
        oldest = sorted(self._by_token.items(), key=lambda kv: kv[1][1])[:overflow]
        for token, (url, _exp) in oldest:
            self._by_token.pop(token, None)
            self._by_url.pop(url, None)
            self._metadata_by_token.pop(token, None)


_store = CandidateStore()


def get_candidate_store() -> CandidateStore:
    return _store


__all__ = ["CandidateStore", "get_candidate_store", "TOKEN_PREFIX"]
