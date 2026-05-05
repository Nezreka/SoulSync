"""DownloadEngine — central owner of cross-source download state.

Phase B scope: skeleton only. The engine exposes a place for
plugins to register, a single ``active_downloads`` dict keyed by
``(source, download_id)``, and per-source RLocks that guard mutations
without serializing workers across different sources.

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

    Thread safety: per-source lock sharding. Each source gets its own
    RLock — progress callbacks on Deezer don't block Tidal's worker
    and vice versa, matching the pre-refactor behavior where each
    client owned its own download lock. Read-only accessors
    (``get_record``, ``iter_records_for_source``) take the source's
    lock briefly and return a SHALLOW COPY so the caller can iterate
    without holding the lock. Callers that need to mutate a record
    should use ``update_record`` which takes the lock and applies the
    patch atomically.
    """

    def __init__(self) -> None:
        # Nested dict: source_name → {download_id → record}. Replaces
        # the original single-dict composite-key layout so
        # ``iter_records_for_source`` is O(source_records) instead of
        # O(total_records).
        self._records: Dict[str, Dict[str, DownloadRecord]] = {}
        # Per-source RLocks. Each source gets its own so progress
        # updates on one source never block writes on another. RLock
        # so a plugin's worker callback can re-enter while holding the
        # lock for its own update. Lazily created via ``_source_lock``;
        # the meta-lock guards creation against the create-race window
        # where two threads could both miss + both create.
        self._source_locks: Dict[str, threading.RLock] = {}
        self._source_locks_lock = threading.Lock()
        # Plugins that have registered with the engine. Source name
        # → plugin instance.
        self._plugins: Dict[str, Any] = {}
        # Alias → canonical-name map. Lets engine resolve legacy
        # source-name strings (e.g. ``'deezer_dl'`` for Deezer) to
        # the canonical key in ``_plugins``. Cin's review caught
        # that engine.cancel_download(source_hint='deezer_dl')
        # silently fell through to Soulseek because alias resolution
        # only existed at the registry, not on the engine.
        self._aliases: Dict[str, str] = {}
        # Background download worker — lives on the engine because
        # it owns the cross-source state the worker mutates. Lazy
        # import keeps the engine module standalone.
        from core.download_engine.worker import BackgroundDownloadWorker
        self.worker = BackgroundDownloadWorker(self)

    # ------------------------------------------------------------------
    # Plugin registration
    # ------------------------------------------------------------------

    def register_plugin(self, source_name: str, plugin: Any,
                        aliases: Tuple[str, ...] = ()) -> None:
        """Register a plugin under its canonical source name. Called
        once per source by the orchestrator after the registry's
        ``initialize`` builds the client instances.

        ``aliases`` is the list of legacy source-name strings that
        should resolve to this plugin (e.g. ``'deezer_dl'`` for
        Deezer). Without alias resolution the engine couldn't route
        cancel/lookup calls that came in with the legacy name.

        If the plugin exposes ``set_engine(engine)``, the engine
        passes a self-reference so the plugin can dispatch into
        ``engine.worker`` / read state / etc. Plugins that haven't
        been migrated to the engine yet simply don't define
        ``set_engine`` — they keep their pre-engine behavior
        unchanged.

        Also reads the plugin's declared ``RateLimitPolicy`` (via
        the ``rate_limit_policy()`` method or ``RATE_LIMIT_POLICY``
        class attribute) and applies it to the worker. Plugins that
        don't declare a policy get the conservative default
        (concurrency=1, delay=0).
        """
        if source_name in self._plugins:
            logger.warning("Plugin %s already registered with engine — overwriting", source_name)
        self._plugins[source_name] = plugin
        for alias in aliases:
            self._aliases[alias] = source_name

        # Apply the plugin's rate-limit policy BEFORE set_engine so
        # set_engine callbacks can override per-source if they need
        # config-driven values (e.g. YouTube's user-tunable delay).
        from core.download_engine.rate_limit import resolve_policy
        policy = resolve_policy(plugin)
        self.worker.set_concurrency(source_name, policy.download_concurrency)
        self.worker.set_delay(source_name, policy.download_delay_seconds)

        set_engine = getattr(plugin, 'set_engine', None)
        if callable(set_engine):
            try:
                set_engine(self)
            except Exception as exc:
                logger.warning(
                    "Plugin %s set_engine callback failed: %s", source_name, exc,
                )

    def get_plugin(self, source_name: str) -> Optional[Any]:
        """Return the plugin instance for the given source name.
        Resolves through aliases — e.g. ``get_plugin('deezer_dl')``
        returns the same instance as ``get_plugin('deezer')``."""
        if source_name in self._plugins:
            return self._plugins[source_name]
        canonical = self._aliases.get(source_name)
        if canonical:
            return self._plugins.get(canonical)
        return None

    def _resolve_canonical(self, source_name: str) -> Optional[str]:
        """Return the canonical source name for an input that may be
        an alias. Returns None if the input matches neither a
        canonical name nor an alias."""
        if source_name in self._plugins:
            return source_name
        return self._aliases.get(source_name)

    def registered_sources(self) -> List[str]:
        return list(self._plugins.keys())

    def _source_lock(self, source_name: str) -> threading.RLock:
        """Return the per-source RLock, lazy-creating it on first use.
        The meta-lock around the cache lookup closes the create-race
        window where two threads both miss + both create a fresh lock.
        """
        with self._source_locks_lock:
            lock = self._source_locks.get(source_name)
            if lock is None:
                lock = threading.RLock()
                self._source_locks[source_name] = lock
            return lock

    # ------------------------------------------------------------------
    # Active-downloads state — Phase B core surface
    # ------------------------------------------------------------------

    def add_record(self, source_name: str, download_id: str, record: DownloadRecord) -> None:
        """Insert a fresh download record. Used by clients (today
        directly via their own dicts; Phase B2 routes them through
        here)."""
        with self._source_lock(source_name):
            source_bucket = self._records.setdefault(source_name, {})
            if download_id in source_bucket:
                logger.warning("Replacing existing download record for %s/%s", source_name, download_id)
            source_bucket[download_id] = dict(record)

    def update_record(self, source_name: str, download_id: str, patch: DownloadRecord) -> None:
        """Apply a partial patch to an existing record. No-op if the
        record was already removed (e.g. cancelled mid-update)."""
        with self._source_lock(source_name):
            existing = self._records.get(source_name, {}).get(download_id)
            if existing is None:
                return
            existing.update(patch)

    def update_record_unless_state(self, source_name: str, download_id: str,
                                   patch: DownloadRecord,
                                   skip_if_state_in: Tuple[str, ...] = ()) -> bool:
        """Atomically check the record's state and apply ``patch`` only
        if the current state is NOT in ``skip_if_state_in``. Returns
        True if the patch was applied, False if it was skipped (or
        the record didn't exist).

        Used by the background download worker's ``_mark_terminal``
        to avoid the read-then-write race Cin flagged: a cancel
        landing between the snapshot and update could be overwritten
        back to Errored / Completed. Holding the source's lock across
        the check + write closes the window.
        """
        with self._source_lock(source_name):
            existing = self._records.get(source_name, {}).get(download_id)
            if existing is None:
                return False
            if existing.get('state') in skip_if_state_in:
                return False
            existing.update(patch)
            return True

    def remove_record(self, source_name: str, download_id: str) -> Optional[DownloadRecord]:
        """Delete a record (cancellation cleanup). Returns the
        removed record or None if not found."""
        with self._source_lock(source_name):
            source_bucket = self._records.get(source_name)
            if not source_bucket:
                return None
            removed = source_bucket.pop(download_id, None)
            # Drop the empty source bucket so iteration / membership
            # checks don't see a stale source key.
            if not source_bucket:
                self._records.pop(source_name, None)
            return removed

    def get_record(self, source_name: str, download_id: str) -> Optional[DownloadRecord]:
        """Return a SHALLOW COPY of the record. Caller mutations
        don't affect engine state — use ``update_record`` for that."""
        with self._source_lock(source_name):
            record = self._records.get(source_name, {}).get(download_id)
            return dict(record) if record is not None else None

    def iter_records_for_source(self, source_name: str) -> Iterator[DownloadRecord]:
        """Yield SHALLOW COPIES of every record owned by a source.
        Holds the source's lock briefly to snapshot, then yields
        outside the lock so callers can spend arbitrary time on each
        record.

        With the nested-dict layout this is O(source_records) — only
        touches the bucket for the requested source, not every record
        across every source.
        """
        with self._source_lock(source_name):
            source_bucket = self._records.get(source_name, {})
            snapshot = [dict(record) for record in source_bucket.values()]
        for record in snapshot:
            yield record

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
        downloads. Per-plugin exceptions are swallowed (one source
        failing shouldn't take down cross-source aggregation) but
        logged at debug level — same defensive shape the legacy
        orchestrator had."""
        all_downloads = []
        for source_name, plugin in self._plugins.items():
            if plugin is None:
                continue
            try:
                all_downloads.extend(await plugin.get_all_downloads())
            except Exception as exc:
                logger.debug("%s get_all_downloads failed: %s", source_name, exc)
        return all_downloads

    async def get_download_status(self, download_id: str):
        """Find a download_id across every plugin. Returns the first
        plugin's response or None if no plugin owns it."""
        for source_name, plugin in self._plugins.items():
            if plugin is None:
                continue
            try:
                status = await plugin.get_download_status(download_id)
                if status:
                    return status
            except Exception as exc:
                logger.debug("%s get_download_status failed: %s", source_name, exc)
        return None

    async def cancel_download(self, download_id: str,
                              source_hint: Optional[str] = None,
                              remove: bool = False) -> bool:
        """Cancel a download. ``source_hint`` is the source name (or
        legacy alias like ``'deezer_dl'``, or a real Soulseek peer
        username) — when provided, routes directly to that plugin.
        When omitted, every plugin is asked in turn until one accepts.

        Cin's review caught a bug here: legacy alias strings like
        ``'deezer_dl'`` weren't resolved to the canonical ``'deezer'``
        plugin name, so the cancel silently fell through to Soulseek.
        Resolution now goes through ``_resolve_canonical`` first.
        """
        # Direct routing when the caller knows the source.
        if source_hint:
            canonical = self._resolve_canonical(source_hint)
            # Streaming source names (or aliases) resolve to a
            # registered plugin. Anything else (real Soulseek peer
            # name not in our registry) routes to Soulseek.
            if canonical and canonical != 'soulseek':
                target_plugin = self._plugins.get(canonical)
                if target_plugin is not None:
                    try:
                        return await target_plugin.cancel_download(
                            download_id, source_hint, remove,
                        )
                    except Exception as exc:
                        logger.debug("%s cancel_download failed: %s", canonical, exc)
                        return False
            soulseek = self._plugins.get('soulseek')
            if soulseek is not None:
                try:
                    return await soulseek.cancel_download(download_id, source_hint, remove)
                except Exception as exc:
                    logger.debug("soulseek cancel_download failed: %s", exc)
                    return False

        # No hint → ask every plugin until one cancels successfully.
        for source_name, plugin in self._plugins.items():
            if plugin is None:
                continue
            try:
                if await plugin.cancel_download(download_id, source_hint, remove):
                    return True
            except Exception as exc:
                logger.debug("%s cancel_download failed: %s", source_name, exc)
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

    # ------------------------------------------------------------------
    # Hybrid fallback — Phase F surface
    # ------------------------------------------------------------------

    async def search_with_fallback(self, query: str, source_chain,
                                   timeout=None, progress_callback=None):
        """Try each source in ``source_chain`` until one returns
        tracks. Skips unconfigured / unregistered sources, swallows
        per-source exceptions. Returns the first non-empty
        (tracks, albums) tuple, or ``([], [])`` when every source
        in the chain is exhausted.

        Replaces orchestrator's hand-rolled hybrid search loop. The
        chain is ordered (most-preferred first).
        """
        for i, source_name in enumerate(source_chain):
            plugin = self._plugins.get(source_name)
            if plugin is None:
                logger.info(f"Skipping {source_name} (not available)")
                continue
            if hasattr(plugin, 'is_configured') and not plugin.is_configured():
                logger.info(f"Skipping {source_name} (not configured)")
                continue

            try:
                logger.info(f"Trying {source_name} (priority {i+1}): {query}")
                tracks, albums = await plugin.search(query, timeout, progress_callback)
                if tracks:
                    logger.info(f"{source_name} found {len(tracks)} tracks")
                    return (tracks, albums)
            except Exception as e:
                logger.warning(f"{source_name} search failed: {e}")

        logger.warning(
            "Hybrid search: all sources (%s) found nothing for: %s",
            ', '.join(source_chain), query,
        )
        return ([], [])

    async def download_with_fallback(self, username: str, filename: str,
                                     file_size: int, source_chain) -> Optional[str]:
        """Try each source in ``source_chain`` until one accepts the
        download (returns a non-None download_id). Fixes the legacy
        bug where hybrid mode silently routed to a single source via
        the username hint with no retry on failure.

        ``username`` is treated as a hint when it matches a source
        name in the chain — that source is tried FIRST regardless of
        chain order. Anything else (e.g. a real Soulseek peer name)
        routes through the chain in declared order.
        """
        # Promote a matching source-name hint to the head of the chain.
        ordered_chain = list(source_chain)
        if username and username in ordered_chain:
            ordered_chain.remove(username)
            ordered_chain.insert(0, username)

        for source_name in ordered_chain:
            plugin = self._plugins.get(source_name)
            if plugin is None:
                continue
            if hasattr(plugin, 'is_configured') and not plugin.is_configured():
                continue
            try:
                download_id = await plugin.download(username, filename, file_size)
                if download_id is not None:
                    return download_id
                logger.info(f"{source_name} declined download — trying next in chain")
            except Exception as e:
                logger.warning(f"{source_name} download raised — trying next in chain: {e}")

        logger.warning(
            "Hybrid download: every source in chain (%s) refused %r",
            ', '.join(ordered_chain), filename,
        )
        return None
