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
        # Background download worker — lives on the engine because
        # it owns the cross-source state the worker mutates. Lazy
        # import keeps the engine module standalone.
        from core.download_engine.worker import BackgroundDownloadWorker
        self.worker = BackgroundDownloadWorker(self)

    # ------------------------------------------------------------------
    # Plugin registration
    # ------------------------------------------------------------------

    def register_plugin(self, source_name: str, plugin: Any) -> None:
        """Register a plugin under its canonical source name. Called
        once per source by the orchestrator after the registry's
        ``initialize`` builds the client instances.

        If the plugin exposes ``set_engine(engine)``, the engine
        passes a self-reference so the plugin can dispatch into
        ``engine.worker`` / read state / etc. Plugins that haven't
        been migrated to the engine yet simply don't define
        ``set_engine`` — they keep their pre-engine behavior
        unchanged.
        """
        if source_name in self._plugins:
            logger.warning("Plugin %s already registered with engine — overwriting", source_name)
        self._plugins[source_name] = plugin
        set_engine = getattr(plugin, 'set_engine', None)
        if callable(set_engine):
            try:
                set_engine(self)
            except Exception as exc:
                logger.warning(
                    "Plugin %s set_engine callback failed: %s", source_name, exc,
                )

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

    # ------------------------------------------------------------------
    # Cross-source query dispatch — Phase B2 surface
    # ------------------------------------------------------------------
    #
    # The orchestrator historically iterated every plugin in its own
    # ``get_all_downloads`` / ``get_download_status`` / ``cancel_download``
    # methods (with hand-maintained client lists, before the registry
    # came along). That iteration logic moves into the engine here so
    # the orchestrator becomes a thin pass-through (Phase B3).
    #
    # In Phase B these methods iterate the registered plugins and call
    # their existing ``get_all_downloads`` / ``cancel_download``
    # methods — same behavior as today, just in a new home. Phase C/D
    # will replace plugin-iteration with direct engine-state queries
    # once the thread worker is also lifted.
    #
    # All methods are async to match the per-plugin contract.

    async def get_all_downloads(self):
        """Aggregated view across every registered plugin's active
        downloads. Returns a flat list of DownloadStatus objects."""
        all_downloads = []
        for plugin in self._plugins.values():
            if plugin is None:
                continue
            try:
                all_downloads.extend(await plugin.get_all_downloads())
            except Exception:
                pass
        return all_downloads

    async def get_download_status(self, download_id: str):
        """Find a download_id across every plugin. Returns the first
        plugin's response or None if no plugin owns it."""
        for plugin in self._plugins.values():
            if plugin is None:
                continue
            try:
                status = await plugin.get_download_status(download_id)
                if status:
                    return status
            except Exception:
                pass
        return None

    async def cancel_download(self, download_id: str,
                              source_hint: Optional[str] = None,
                              remove: bool = False) -> bool:
        """Cancel a download. ``source_hint`` is the source name (or
        legacy username string like ``'deezer_dl'``) — when provided,
        routes directly to that plugin. When omitted, every plugin
        is asked in turn until one accepts the cancel."""
        # Direct routing when the caller knows the source.
        if source_hint:
            # Streaming source names ARE the username. Soulseek
            # uses a real peer username (anything not in our plugin
            # registry), so route those to the soulseek plugin.
            target_plugin = self._plugins.get(source_hint)
            if target_plugin is not None and source_hint != 'soulseek':
                try:
                    return await target_plugin.cancel_download(
                        download_id, source_hint, remove,
                    )
                except Exception:
                    return False
            soulseek = self._plugins.get('soulseek')
            if soulseek is not None:
                try:
                    return await soulseek.cancel_download(download_id, source_hint, remove)
                except Exception:
                    return False

        # No hint → ask every plugin until one cancels successfully.
        for plugin in self._plugins.values():
            if plugin is None:
                continue
            try:
                if await plugin.cancel_download(download_id, source_hint, remove):
                    return True
            except Exception:
                pass
        return False

    async def clear_all_completed_downloads(self) -> bool:
        """Best-effort cleanup of every plugin's completed-downloads
        list. Skips plugins that report not-configured (saves API
        calls + log noise)."""
        results = []
        for source_name, plugin in self._plugins.items():
            if plugin is None:
                continue
            if hasattr(plugin, 'is_configured') and not plugin.is_configured():
                logger.debug("Skipping %s clear_all_completed_downloads (not configured)", source_name)
                continue
            try:
                results.append(await plugin.clear_all_completed_downloads())
            except Exception as exc:
                logger.warning("%s clear_all_completed_downloads failed: %s", source_name, exc)
                results.append(False)
        return all(results) if results else True
