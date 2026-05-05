"""BackgroundDownloadWorker — engine-owned thread spawning + state
lifecycle for downloads.

Today every streaming download client (YouTube, Tidal, Qobuz, HiFi,
Deezer, SoundCloud) hand-rolls the same thread-spawn pattern:

```python
async def download(self, ...):
    download_id = str(uuid.uuid4())
    with self._download_lock:
        self.active_downloads[download_id] = {...initial state...}
    threading.Thread(
        target=self._download_thread_worker,
        args=(download_id, target_id, display_name, ...),
        daemon=True,
    ).start()
    return download_id

def _download_thread_worker(self, download_id, target_id, display_name, ...):
    with self._download_semaphore:
        # rate-limit sleep
        # update state to 'InProgress, Downloading'
        file_path = self._download_sync(...)  # the source-specific atomic op
        # update state to 'Completed, Succeeded' / 'Errored'
```

That pattern is duplicated 6+ times across the codebase (~70 LOC
each, ~490 total). The worker class lifts it into the engine — each
plugin only has to provide the atomic op (``impl_callable``) and
declare its rate-limit policy. Adding a new download source becomes
a much smaller patch.

Phase C1 scope: introduce the worker. No client migrated yet — the
worker just exists for C2–C7 to migrate sources one at a time, each
under a passing pinning test.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("download_engine.worker")


# Type aliases for clarity. ``ImplCallable`` is the per-plugin
# atomic download operation — synchronous, returns a file path on
# success or raises (or returns None) on failure.
ImplCallable = Callable[[str, Any, str], Optional[str]]


class BackgroundDownloadWorker:
    """Engine-owned thread spawner for per-source downloads.

    State-machine semantics (preserved verbatim from the legacy
    per-client workers so consumers reading these fields keep
    working):

    - ``Initializing`` — set on dispatch, before the thread starts.
    - ``InProgress, Downloading`` — set when the worker thread
      acquires the semaphore and is about to call the impl.
    - ``Completed, Succeeded`` — set when impl returns a non-None
      file path. ``progress=100.0`` and ``file_path=<the path>``
      also written.
    - ``Errored`` — set when impl returns None OR raises. The
      record is left in place so downstream consumers can inspect
      what failed.

    Per-source serialization: each source gets a ``threading.Semaphore``
    (default size 1, configurable per-source via ``set_concurrency``).
    Same shape the existing clients use today (each source defines
    its own semaphore). Engine owning them centrally lets a future
    Phase E rate-limiter swap the semaphore for a smarter pool.

    Per-source delay-between-downloads: default 0 seconds (most
    sources don't need it). YouTube currently uses 3s, Qobuz uses
    1s — the legacy values get configured in via ``set_delay``
    when the source registers.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        # Per-source semaphores + delay state. The first dispatch
        # for a source auto-creates a semaphore with concurrency=1
        # if the source hasn't been configured explicitly.
        self._semaphores: Dict[str, threading.Semaphore] = {}
        self._delays: Dict[str, float] = {}
        self._last_download_at: Dict[str, float] = {}
        self._config_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Per-source rate-limit configuration
    # ------------------------------------------------------------------

    def set_concurrency(self, source_name: str, max_concurrent: int) -> None:
        """Set the max number of concurrent downloads for a source.
        Default is 1 (serial). Most sources will keep the default —
        the streaming APIs all rate-limit at the API gateway level
        anyway, parallel downloads just trade rate-limit errors for
        thread overhead."""
        with self._config_lock:
            self._semaphores[source_name] = threading.Semaphore(max_concurrent)

    def set_delay(self, source_name: str, seconds: float) -> None:
        """Set a minimum delay between successive downloads from the
        same source. YouTube uses 3s today (avoid yt-dlp 429s),
        Qobuz uses 1s. Other sources use 0 (no delay)."""
        with self._config_lock:
            self._delays[source_name] = float(seconds)

    def _get_semaphore(self, source_name: str) -> threading.Semaphore:
        with self._config_lock:
            sem = self._semaphores.get(source_name)
            if sem is None:
                sem = threading.Semaphore(1)
                self._semaphores[source_name] = sem
            return sem

    def _get_delay(self, source_name: str) -> float:
        with self._config_lock:
            return self._delays.get(source_name, 0.0)

    # ------------------------------------------------------------------
    # Dispatch — public API
    # ------------------------------------------------------------------

    def dispatch(
        self,
        source_name: str,
        target_id: Any,
        display_name: str,
        original_filename: str,
        impl_callable: ImplCallable,
        extra_record_fields: Optional[Dict[str, Any]] = None,
        username_override: Optional[str] = None,
        thread_name: Optional[str] = None,
    ) -> str:
        """Kick off a background download.

        Args:
            source_name: Canonical source name (e.g. 'youtube',
                'tidal'). Used as the engine state key + the
                username slot in the record (unless overridden).
            target_id: Source-specific identifier (track_id, video_id,
                permalink_url, album_foreign_id, etc.). Passed
                verbatim to ``impl_callable``.
            display_name: Human-readable label for logs / UI.
            original_filename: The encoded filename the orchestrator
                received (e.g. ``'12345||Song Title'``). Stored in
                the record's ``filename`` slot for context-key lookups.
            impl_callable: Synchronous function that performs the
                actual download. Signature:
                ``impl_callable(download_id, target_id, display_name) -> Optional[str]``.
                Returns the final file path on success or None /
                raises on failure.
            extra_record_fields: Per-source extras to merge into the
                initial record (e.g. ``{'video_id': '...', 'url':
                '...', 'title': '...'}`` for YouTube). Used to
                preserve source-specific slots that downstream
                consumers + status APIs read.
            username_override: Use this instead of ``source_name``
                in the record's ``username`` slot. Required for
                Deezer (legacy ``'deezer_dl'``) — every other source
                uses the canonical name.
            thread_name: Optional thread name for diagnostics. Deezer
                uses ``'deezer-dl-<track_id>'`` — Phase A pinning
                tests catch any drift in this convention.

        Returns:
            download_id (UUID4 string). The orchestrator polls via
            ``engine.get_download_status(download_id)`` for progress.
        """
        download_id = str(uuid.uuid4())

        record: Dict[str, Any] = {
            'id': download_id,
            'filename': original_filename,
            'username': username_override or source_name,
            'state': 'Initializing',
            'progress': 0.0,
            'size': 0,
            'transferred': 0,
            'speed': 0,
            'time_remaining': None,
            'file_path': None,
        }
        if extra_record_fields:
            record.update(extra_record_fields)

        self._engine.add_record(source_name, download_id, record)

        thread = threading.Thread(
            target=self._worker_loop,
            args=(source_name, download_id, target_id, display_name, impl_callable),
            daemon=True,
            name=thread_name,
        )
        thread.start()

        return download_id

    # ------------------------------------------------------------------
    # Worker thread — the lifted boilerplate
    # ------------------------------------------------------------------

    def _worker_loop(
        self,
        source_name: str,
        download_id: str,
        target_id: Any,
        display_name: str,
        impl_callable: ImplCallable,
    ) -> None:
        """Runs on the spawned daemon thread. Handles semaphore
        acquisition, rate-limit sleep, state lifecycle, exception
        capture. The plugin-specific work happens entirely inside
        ``impl_callable``."""
        try:
            with self._get_semaphore(source_name):
                # Rate-limit delay against the LAST download from
                # this source (not just this worker — semaphore
                # ensures serial access while delay is configured).
                delay = self._get_delay(source_name)
                if delay > 0:
                    last_at = self._last_download_at.get(source_name, 0.0)
                    elapsed = time.time() - last_at
                    if last_at > 0 and elapsed < delay:
                        wait_time = delay - elapsed
                        logger.info(
                            "Rate-limit delay for %s: waiting %.1fs before next download",
                            source_name, wait_time,
                        )
                        time.sleep(wait_time)

                self._engine.update_record(source_name, download_id, {
                    'state': 'InProgress, Downloading',
                })

                try:
                    file_path = impl_callable(download_id, target_id, display_name)
                except Exception as exc:
                    logger.error(
                        "%s download %s failed (impl raised): %s",
                        source_name, download_id, exc,
                    )
                    self._mark_terminal(
                        source_name, download_id,
                        success=False, error=str(exc),
                    )
                    return

                self._last_download_at[source_name] = time.time()

                if file_path:
                    # Atomic write — preserve Cancelled if user cancelled
                    # between impl returning and this write. Same guard
                    # _mark_terminal uses; Cin flagged both split sites.
                    self._engine.update_record_unless_state(
                        source_name, download_id,
                        {
                            'state': 'Completed, Succeeded',
                            'progress': 100.0,
                            'file_path': file_path,
                        },
                        skip_if_state_in=('Cancelled',),
                    )
                    logger.info(
                        "%s download %s completed: %s",
                        source_name, download_id, file_path,
                    )
                else:
                    self._mark_terminal(source_name, download_id, success=False)
                    logger.error(
                        "%s download %s failed (impl returned None)",
                        source_name, download_id,
                    )

        except Exception as exc:
            # Defensive — semaphore / sleep shouldn't blow up the
            # thread, but if they do the record needs SOME terminal
            # state or it sits at 'Initializing' forever.
            logger.exception(
                "%s worker_loop crashed for download %s: %s",
                source_name, download_id, exc,
            )
            self._mark_terminal(
                source_name, download_id,
                success=False, error=f'worker crash: {exc}',
            )

    def _mark_terminal(self, source_name: str, download_id: str,
                       success: bool, error: Optional[str] = None) -> None:
        """Write a terminal state, but DON'T clobber an explicit
        'Cancelled' state set by the user via cancel_download.
        Mirrors the legacy per-client guard
        (``if state != 'Cancelled': state = 'Errored'``) every
        client used to hand-roll inside its thread worker.

        Uses ``update_record_unless_state`` so the check + write are
        atomic under the engine's state_lock. Cin caught a race
        where a cancel landing between the read-snapshot + write
        could overwrite Cancelled back to Errored / Completed.
        """
        patch: Dict[str, Any] = {
            'state': 'Completed, Succeeded' if success else 'Errored',
        }
        if error is not None:
            patch['error'] = error
        self._engine.update_record_unless_state(
            source_name, download_id, patch, skip_if_state_in=('Cancelled',),
        )
