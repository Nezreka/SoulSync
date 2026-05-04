"""DownloadEngine — central owner of cross-source download state.

Phase B scope: skeleton only. The engine exposes a place for
plugins to register, a single ``active_downloads`` dict keyed by
``(source, download_id)``, and a ``state_lock`` that guards mutations
across the multi-threaded download worker pool.

Subsequent phases bolt more capability on top:
- ``dispatch_download(plugin, target_id)`` (Phase C — replaces every
  client's ``_download_thread_worker`` boilerplate).
- ``search(query, source_chain)`` (Phase D — replaces every client's
  retry ladder + quality filter).
- ``rate_limit.acquire(source)`` (Phase E — replaces every client's
  semaphore + last-download-timestamp dance).
- ``search_with_fallback`` / ``download_with_fallback`` (Phase F —
  unifies hybrid mode across search and download).

The engine is constructed by ``DownloadOrchestrator.__init__`` and
each plugin from the registry is registered with it. In Phase B
nothing in the existing code paths goes through the engine yet —
this commit is pure additive scaffolding so subsequent commits can
introduce engine-driven behavior one piece at a time without a
big-bang switchover.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Iterator, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("download_engine")


# Type alias for the per-download state dict. Today's clients each
# define their own slightly-different shape (see Phase A pinning
# tests); the engine stores them as opaque dicts and the per-plugin
# accessor preserves the source-specific fields.
DownloadRecord = Dict[str, Any]


class DownloadEngine:
    """Central state for every active download across every source.

    State is keyed by ``(source_name, download_id)`` so the same
    UUID could hypothetically appear in two sources without
    collision (in practice each source generates its own UUID4
    so collisions are negligible — the source qualifier exists
    so the engine can answer "which plugin owns this download" in
    O(1) without iterating every plugin).

    Thread safety: every state mutation goes through ``state_lock``.
    Read-only accessors (``get_record``, ``iter_records_for_source``)
    take the lock briefly and return a SHALLOW COPY so the caller
    can iterate without holding the lock. Callers that need to
    mutate a record should use ``update_record`` which takes the
    lock and applies the patch atomically.
    """

    def __init__(self) -> None:
        self.state_lock = threading.RLock()
        # Composite key: (source_name, download_id) → record dict.
        # RLock so a plugin's worker callback can re-enter while
        # holding the lock for its own update.
        self._records: Dict[Tuple[str, str], DownloadRecord] = {}
        # Plugins that have registered with the engine. Source name
        # → plugin instance. The engine itself doesn't use plugins
        # until later phases, but holding the references here keeps
        # plugin lookup local to the engine instead of forcing every
        # caller to also touch the registry.
        self._plugins: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Plugin registration
    # ------------------------------------------------------------------

    def register_plugin(self, source_name: str, plugin: Any) -> None:
        """Register a plugin under its canonical source name. Called
        once per source by the orchestrator after the registry's
        ``initialize`` builds the client instances.

        Phase B is purely informational — the engine doesn't yet
        dispatch through plugins. Subsequent phases use these
        references to call ``plugin._download_impl`` /
        ``plugin._search_raw`` etc.
        """
        if source_name in self._plugins:
            logger.warning("Plugin %s already registered with engine — overwriting", source_name)
        self._plugins[source_name] = plugin

    def get_plugin(self, source_name: str) -> Optional[Any]:
        return self._plugins.get(source_name)

    def registered_sources(self) -> List[str]:
        return list(self._plugins.keys())

    # ------------------------------------------------------------------
    # Active-downloads state — Phase B core surface
    # ------------------------------------------------------------------

    def add_record(self, source_name: str, download_id: str, record: DownloadRecord) -> None:
        """Insert a fresh download record. Used by clients (today
        directly via their own dicts; Phase B2 routes them through
        here)."""
        with self.state_lock:
            key = (source_name, download_id)
            if key in self._records:
                logger.warning("Replacing existing download record for %s/%s", source_name, download_id)
            self._records[key] = dict(record)

    def update_record(self, source_name: str, download_id: str, patch: DownloadRecord) -> None:
        """Apply a partial patch to an existing record. No-op if the
        record was already removed (e.g. cancelled mid-update)."""
        with self.state_lock:
            existing = self._records.get((source_name, download_id))
            if existing is None:
                return
            existing.update(patch)

    def remove_record(self, source_name: str, download_id: str) -> Optional[DownloadRecord]:
        """Delete a record (cancellation cleanup). Returns the
        removed record or None if not found."""
        with self.state_lock:
            return self._records.pop((source_name, download_id), None)

    def get_record(self, source_name: str, download_id: str) -> Optional[DownloadRecord]:
        """Return a SHALLOW COPY of the record. Caller mutations
        don't affect engine state — use ``update_record`` for that."""
        with self.state_lock:
            record = self._records.get((source_name, download_id))
            return dict(record) if record is not None else None

    def iter_records_for_source(self, source_name: str) -> Iterator[DownloadRecord]:
        """Yield SHALLOW COPIES of every record owned by a source.
        Holds the lock briefly to snapshot, then yields outside the
        lock so callers can spend arbitrary time on each record."""
        with self.state_lock:
            snapshot = [
                dict(record)
                for (source, _), record in self._records.items()
                if source == source_name
            ]
        for record in snapshot:
            yield record

    def iter_all_records(self) -> Iterator[Tuple[str, DownloadRecord]]:
        """Yield ``(source_name, record_copy)`` for every active
        download across every source. Used by Phase B3's unified
        ``get_all_downloads`` query."""
        with self.state_lock:
            snapshot = [
                (source, dict(record))
                for (source, _), record in self._records.items()
            ]
        for source, record in snapshot:
            yield source, record

    def find_record(self, download_id: str) -> Optional[Tuple[str, DownloadRecord]]:
        """Look up a record by download_id alone (no source hint).
        Used by ``cancel_download`` / ``get_download_status`` API
        endpoints that don't pass the source name. Returns
        ``(source_name, record_copy)`` or None.

        O(N) over total downloads — fine for the tens-to-hundreds
        of in-flight transfers SoulSync sees, would need an index
        if downloads scaled to thousands.
        """
        with self.state_lock:
            for (source, dl_id), record in self._records.items():
                if dl_id == download_id:
                    return source, dict(record)
        return None
