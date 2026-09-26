"""one listening-history importer per pile (#1293).

the shared worker is the one the app always had, driven by the account in
Settings. every profile that connected its own account on the service gets its
own worker, made on first use, so its crawl and resume state never touch
anyone else's. listenbrainz and last.fm each subclass this with their worker.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from core.listening_scope import SHARED_OWNER, profiles_with_account
from utils.logging_config import get_logger

logger = get_logger("listening_import.profiles")


class ProfileImportWorkers:
    worker_cls: Any = None
    source: str = ""

    def __init__(
        self,
        database,
        config_manager,
        *,
        cache_builder: Optional[Callable[..., Any]] = None,
        progress_callback: Optional[Callable[..., None]] = None,
    ):
        self.db = database
        self.config_manager = config_manager
        self._cache_builder = cache_builder
        self._progress_callback = progress_callback
        self._lock = threading.Lock()
        self._workers: Dict[int, Any] = {}
        self.shared = self.for_owner(SHARED_OWNER)

    def for_owner(self, owner: int):
        owner = int(owner)
        with self._lock:
            worker = self._workers.get(owner)
            if worker is None:
                worker = self.worker_cls(
                    self.db,
                    self.config_manager,
                    cache_builder=self._owner_cache_builder(owner),
                    progress_callback=self._owner_progress(owner),
                    profile_id=owner,
                )
                self._workers[owner] = worker
            return worker

    def is_running(self) -> bool:
        with self._lock:
            workers = list(self._workers.values())
        return any(w.is_running() for w in workers)

    @staticmethod
    def redact(error: Exception) -> str:
        """an error message with any credential taken out. subclasses know
        what their service's secrets look like."""
        return str(error)

    def profile_owners(self) -> List[int]:
        """profiles with their own account on this service."""
        return profiles_with_account(self.db, self.source)

    def start_profile(self, owner: int, *, full: bool = False) -> Dict[str, Any]:
        """kick off one profile's import in the background, e.g. right after it
        connects, so its stats aren't empty for an hour."""
        return self.for_owner(owner).start_import(full=full)

    def run_profiles(self, *, full: bool = False) -> Dict[int, Dict[str, Any]]:
        """run every profile's import, one after another. a profile that's
        already importing (just connected) comes back skipped."""
        results: Dict[int, Dict[str, Any]] = {}
        for owner in self.profile_owners():
            try:
                results[owner] = self.for_owner(owner).run_once(full=full)
            except Exception as e:
                safe = self.redact(e)
                logger.error("%s import for profile %s failed: %s", self.source, owner, safe)
                results[owner] = {"status": "error", "error": safe}
        return results

    def forget(self, owner: int, *, wait: float = 0) -> None:
        """drop a profile's worker once it disconnects. a running crawl is told
        to stop, and with wait we give it that long to actually stop."""
        owner = int(owner)
        if owner == SHARED_OWNER:
            return
        with self._lock:
            worker = self._workers.pop(owner, None)
        if worker is None:
            return
        worker.cancel()
        thread = worker._thread
        if wait and thread is not None and thread.is_alive():
            thread.join(wait)
            if thread.is_alive():
                logger.warning("%s import for profile %s still stopping after %ss", self.source, owner, wait)

    def on_connected(self, owner: int, previous_username: str, username: str) -> Dict[str, Any]:
        """a profile just saved its account. that makes its pile its own, so
        start filling it now rather than on the next hourly run.

        a different account than before means the old account's plays aren't
        this person's history anymore: stop that crawl and drop them first."""
        owner = int(owner)
        if owner == SHARED_OWNER:
            return {"status": "skipped", "reason": "the admin uses the shared history"}
        old = (previous_username or "").strip().casefold()
        if old and old != (username or "").strip().casefold():
            self.forget(owner, wait=20)
            removed = self.db.reset_listening_pile(owner, self.source)
            logger.info("profile %s switched %s accounts, dropped %s old plays", owner, self.source, removed)
        return self.start_profile(owner)

    def on_disconnected(self, owner: int) -> None:
        """its plays stay put in case it reconnects, only the crawl stops."""
        self.forget(owner)

    def _owner_cache_builder(self, owner: int):
        if not self._cache_builder:
            return None
        builder = self._cache_builder
        if owner == SHARED_OWNER:
            return builder
        # a profile's import only changes its own pile
        return lambda: builder(owners=[owner])

    def _owner_progress(self, owner: int):
        if not self._progress_callback:
            return None
        callback = self._progress_callback
        if owner == SHARED_OWNER:
            return callback
        return lambda state: callback(state, owner=owner)
