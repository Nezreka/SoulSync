"""Server-side store for indexer download URLs (audit P0-03 / §16.1).

Prowlarr download URLs (and magnet URIs from private trackers) can embed
indexer API keys or signed, time-limited parameters. They must never reach
the browser: search results carry an opaque candidate token in the
``filename`` field instead, and the download path resolves the token back
to the real URL server-side. A token that is unknown or expired is
rejected — the client can no longer submit an arbitrary URL for the
server to forward to SABnzbd/NZBGet/qBittorrent.

Beyond the P0-03 baseline this store implements the two follow-ups the
audit kept open (§16.1):

- **Binding:** every token is stamped with the profile that ran the
  search and (when the search was entity-scoped) the Library-v2 track /
  album it was searched for. Resolution revalidates that binding — a
  token minted for profile A cannot be grabbed by profile B, and a token
  minted for one lib2 entity cannot be redirected at a different one.
  The binding travels via a ``contextvars.ContextVar`` set at the API
  boundary (``candidate_binding``), so plugin signatures stay unchanged
  and the context survives ``run_coroutine_threadsafe`` into the shared
  event loop.
- **Shared visibility:** entries live in a small SQLite database next to
  the main library database (WAL mode), so every Gunicorn worker sees
  the same tokens and a restart between search and grab no longer
  invalidates them. TTL and a size cap keep an indexer flood bounded.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sqlite3
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from utils.logging_config import get_logger

logger = get_logger("candidate_store")

# Recognizable, URL-unlike token prefix ("soulsync candidate, v1").
TOKEN_PREFIX = "ssc1-"

# Generous window between a search and the grab that uses its result —
# covers a long manual browsing session and queued auto-grabs. Deliberately
# NOT days: these URLs may be signed/short-lived on the indexer side too.
DEFAULT_TTL_SECONDS = 6 * 60 * 60

DEFAULT_MAX_ENTRIES = 5000

# Background flows (wishlist scans, watchlist workers, auto-grabs) run as
# the admin profile — the same default `get_current_profile_id()` uses.
ADMIN_PROFILE_ID = 1


@dataclass(frozen=True)
class CandidateBinding:
    """Who a candidate token belongs to and what it was searched for."""

    profile_id: int = ADMIN_PROFILE_ID
    lib2_track_id: Optional[int] = None
    lib2_album_id: Optional[int] = None


_binding_var: ContextVar[Optional[CandidateBinding]] = ContextVar(
    "candidate_binding", default=None
)


@contextlib.contextmanager
def candidate_binding(profile_id: int,
                      lib2_track_id: Optional[int] = None,
                      lib2_album_id: Optional[int] = None):
    """Scope all candidate put/resolve calls to a profile (+ lib2 entity).

    Set at the API boundary (search + download handlers) and by workers
    that act for a specific profile. Anything running without an explicit
    binding is treated as the admin profile, matching the existing
    background-caller behaviour of ``get_current_profile_id()``.
    """
    token = _binding_var.set(CandidateBinding(
        profile_id=int(profile_id),
        lib2_track_id=lib2_track_id,
        lib2_album_id=lib2_album_id,
    ))
    try:
        yield
    finally:
        _binding_var.reset(token)


def current_binding() -> CandidateBinding:
    return _binding_var.get() or CandidateBinding()


def _default_store_path() -> str:
    """A small dedicated SQLite file next to the main library database.

    Kept separate from the main DB on purpose: candidate tokens are
    ephemeral operational state, not library data — they don't belong in
    backups/migrations, and a separate file keeps their write traffic off
    the main database's lock.
    """
    override = os.environ.get("CANDIDATE_STORE_PATH")
    if override:
        return override
    db_path = os.environ.get("DATABASE_PATH", "database/music_library.db")
    return os.path.join(os.path.dirname(os.path.abspath(db_path)),
                        "candidate_store.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_tokens (
    token         TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    profile_id    INTEGER NOT NULL,
    lib2_track_id INTEGER,
    lib2_album_id INTEGER,
    expires_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidate_tokens_url
    ON candidate_tokens(url);
CREATE INDEX IF NOT EXISTS idx_candidate_tokens_expires
    ON candidate_tokens(expires_at);
"""


class CandidateStore:
    """Token -> download-URL map, SQLite-backed, TTL- and size-capped.

    Tokens are bound to the profile (and optionally the lib2 entity) in
    scope at ``put`` time; ``resolve`` revalidates that binding against
    the caller's current scope.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 max_entries: int = DEFAULT_MAX_ENTRIES,
                 path: Optional[str] = None) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.Lock()
        if path is None:
            # Private in-process store (tests, ad-hoc instances): a named
            # shared-cache memory DB so per-operation connections all see
            # the same data. The anchor connection keeps it alive.
            self._uri = f"file:candstore-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._is_uri = True
            self._anchor = sqlite3.connect(self._uri, uri=True,
                                           check_same_thread=False)
        else:
            self._uri = path
            self._is_uri = False
            self._anchor = None
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._uri, uri=self._is_uri, timeout=10,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if not self._is_uri:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def is_token(value: Optional[str]) -> bool:
        return bool(value) and value.startswith(TOKEN_PREFIX)

    def put(self, url: str) -> str:
        """Register a URL under the current binding; returns its token.

        The same URL under the same binding within the TTL returns the
        same token (repeated searches don't grow the store). The same URL
        under a DIFFERENT binding gets its own token — profile A's search
        result never doubles as profile B's grab authorization.
        """
        binding = current_binding()
        with self._lock, contextlib.closing(self._connect()) as conn:
            with conn:
                # Captured under the lock, not before it: expires_at must
                # reflect actual insertion order so a call that loses the
                # race for the lock can't record an earlier timestamp than
                # entries inserted moments before it — otherwise the
                # expiry-ordered eviction below could pick the entry this
                # very call is about to create.
                now = time.time()
                conn.execute(
                    "DELETE FROM candidate_tokens WHERE expires_at < ?", (now,))
                row = conn.execute(
                    "SELECT token FROM candidate_tokens WHERE url=? AND profile_id=?"
                    " AND COALESCE(lib2_track_id,-1)=COALESCE(?,-1)"
                    " AND COALESCE(lib2_album_id,-1)=COALESCE(?,-1)",
                    (url, binding.profile_id,
                     binding.lib2_track_id, binding.lib2_album_id),
                ).fetchone()
                if row is not None:
                    # Refresh the TTL — the candidate was just seen again.
                    conn.execute(
                        "UPDATE candidate_tokens SET expires_at=? WHERE token=?",
                        (now + self._ttl, row["token"]))
                    return row["token"]
                token = TOKEN_PREFIX + secrets.token_urlsafe(24)
                conn.execute(
                    "INSERT INTO candidate_tokens"
                    " (token, url, profile_id, lib2_track_id, lib2_album_id, expires_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (token, url, binding.profile_id,
                     binding.lib2_track_id, binding.lib2_album_id,
                     now + self._ttl))
                overflow = conn.execute(
                    "SELECT COUNT(*) AS n FROM candidate_tokens").fetchone()["n"] - self._max
                if overflow > 0:
                    # rowid tiebreaker: same-clock-tick inserts still evict
                    # oldest-first deterministically.
                    conn.execute(
                        "DELETE FROM candidate_tokens WHERE token IN ("
                        " SELECT token FROM candidate_tokens"
                        " ORDER BY expires_at ASC, rowid ASC LIMIT ?)", (overflow,))
                return token

    def resolve(self, token: str) -> Optional[str]:
        """The URL behind a token, or None when unknown, expired, or the
        token's binding doesn't match the caller's current scope."""
        if not self.is_token(token):
            return None
        binding = current_binding()
        now = time.time()
        with self._lock, contextlib.closing(self._connect()) as conn:
            with conn:
                row = conn.execute(
                    "SELECT url, profile_id, lib2_track_id, lib2_album_id, expires_at"
                    " FROM candidate_tokens WHERE token=?", (token,)).fetchone()
                if row is None:
                    return None
                if row["expires_at"] < now:
                    conn.execute(
                        "DELETE FROM candidate_tokens WHERE token=?", (token,))
                    return None
        if row["profile_id"] != binding.profile_id:
            logger.warning(
                "Candidate token rejected: minted for profile %s, grabbed as "
                "profile %s", row["profile_id"], binding.profile_id)
            return None
        # Entity binding is one-directional strict: a token searched FOR a
        # specific lib2 entity may not be redirected at a different one.
        # Tokens from generic searches (no entity) stay usable for entity
        # grabs — that is today's main UI flow — and an entity-bound token
        # used without entity context merely loses its lib2 linking.
        for col, ctx_val in (("lib2_track_id", binding.lib2_track_id),
                             ("lib2_album_id", binding.lib2_album_id)):
            if row[col] is not None and ctx_val is not None and row[col] != ctx_val:
                logger.warning(
                    "Candidate token rejected: minted for %s=%s, grabbed for "
                    "%s=%s", col, row[col], col, ctx_val)
                return None
        return row["url"]


_store: Optional[CandidateStore] = None
_store_lock = threading.Lock()


def get_candidate_store() -> CandidateStore:
    """Process-wide store over the shared SQLite file (all workers see it)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = CandidateStore(path=_default_store_path())
    return _store


__all__ = [
    "CandidateBinding",
    "CandidateStore",
    "TOKEN_PREFIX",
    "candidate_binding",
    "current_binding",
    "get_candidate_store",
]
