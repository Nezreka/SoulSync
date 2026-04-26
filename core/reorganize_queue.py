"""FIFO queue for library album reorganize requests.

Replaces the single-slot "one reorganize at a time, return 409 on
collision" model with a queue: clicks always succeed (or surface
"already queued" on dedupe), the user can fan-out clicks across
albums or hit "Reorganize All", and a single background worker
chews through the queue in submission order.

Design rules:

- **Single global queue**, single worker thread. Reorganize is
  I/O-heavy (file copy, mutagen tagging, AcoustID, possibly ffmpeg)
  and post-process is not designed for cross-album concurrency.
  In-album track parallelism still happens inside `reorganize_album`
  (3 worker threads — see `_REORGANIZE_MAX_WORKERS`).

- **Dedupe on enqueue**: an album that's already queued or currently
  running is rejected silently. Stops the user from spamming the
  same album N times by clicking the button repeatedly.

- **Per-item source**: each queued item carries its own `source`
  string (the user's per-album modal pick). Worker passes it
  through to `reorganize_album(primary_source=..., strict_source=...)`.

- **Continue on failure**: a failed item doesn't stop the queue.
  Worker logs the failure, marks the item `failed`, moves on.

- **Cancel queued items**: items in `queued` state can be cancelled
  (drop from queue). The currently-running item can NOT be cancelled
  mid-flight — Python threads aren't cleanly killable, and post-
  process spawns subprocesses we can't safely interrupt. Cancel
  changes the item's status to `cancelled` and removes it from the
  active queue.

- **In-memory only**: queue state lives in a module-level singleton.
  A server restart loses the queue (in-flight item likely also lost
  half-way through post-process). DB persistence is a follow-up if
  this turns out to matter operationally.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("reorganize_queue")


# How many recently-completed items to retain for the snapshot endpoint.
# The status panel uses these to show "just-finished" cards briefly so
# the user sees outcomes scroll past instead of items vanishing.
_RECENT_HISTORY_CAP = 30


@dataclass
class QueueItem:
    """One album waiting (or being processed) in the reorganize queue."""

    queue_id: str                       # uuid; how the API references this item
    album_id: str
    album_title: str                    # captured at enqueue time for UI display
    artist_id: Optional[str]
    artist_name: str                    # captured at enqueue time for UI display
    source: Optional[str]               # the user's per-modal pick (None = auto)
    enqueued_at: float
    status: str = 'queued'              # queued | running | done | failed | cancelled
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # Populated by the worker after each item finishes — surfaced to the
    # status panel so users see counts + per-item error messages.
    result_status: Optional[str] = None  # mirrors `reorganize_album` summary['status']
    result_source: Optional[str] = None  # which source the orchestrator actually used
    moved: int = 0
    skipped: int = 0
    failed: int = 0
    error: Optional[str] = None         # shorthand for the first error, for the toast
    # Live-progress fields for the currently-running item; cleared when
    # the worker moves on so the snapshot stays small.
    current_track: Optional[str] = None
    progress_total: int = 0
    progress_processed: int = 0

    def to_snapshot(self) -> dict:
        return {
            'queue_id': self.queue_id,
            'album_id': self.album_id,
            'album_title': self.album_title,
            'artist_id': self.artist_id,
            'artist_name': self.artist_name,
            'source': self.source,
            'enqueued_at': self.enqueued_at,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'status': self.status,
            'result_status': self.result_status,
            'result_source': self.result_source,
            'moved': self.moved,
            'skipped': self.skipped,
            'failed': self.failed,
            'error': self.error,
            'current_track': self.current_track,
            'progress_total': self.progress_total,
            'progress_processed': self.progress_processed,
        }


class ReorganizeQueue:
    """Module-level singleton that owns the queue + worker thread.

    Use the module-level :func:`get_queue` accessor — don't construct
    directly. The class is documented public-style so tests can spin
    up isolated instances.
    """

    def __init__(self, *, runner: Optional[Callable[[QueueItem], dict]] = None):
        """
        Args:
            runner: Callable that takes a `QueueItem` and runs the
                actual reorganize, returning a summary dict with
                ``status``, ``source``, ``moved``, ``skipped``,
                ``failed``, ``errors`` keys (the shape
                ``reorganize_album`` already returns). Tests inject
                a fake runner; production wires the real one in
                via :func:`set_runner`.
        """
        # Single Condition variable owns both mutual exclusion and the
        # idle-worker wait. Using a Condition (vs Lock + Event) closes a
        # race where the worker could clear an event right after enqueue
        # set it, causing the new item to sleep for the timeout window.
        # cond.wait() releases the lock and re-acquires on notify, so
        # state checks and waits are properly interleaved.
        self._cond = threading.Condition()
        self._items: List[QueueItem] = []        # everything ever submitted (active + recent)
        self._runner = runner
        self._worker: Optional[threading.Thread] = None
        self._stopped = False

    # -- public API --------------------------------------------------

    def set_runner(self, runner: Callable[[QueueItem], dict]) -> None:
        """Inject the function that does the actual reorganize work.
        Web_server calls this once at startup with a closure over the
        injected dependencies (post-process fn, db, etc.)."""
        with self._cond:
            self._runner = runner

    def enqueue(
        self,
        *,
        album_id: str,
        album_title: str,
        artist_id: Optional[str],
        artist_name: str,
        source: Optional[str] = None,
    ) -> dict:
        """Add an album to the queue. Returns a result dict:

            {'queued': True, 'queue_id': '...', 'position': N}
            {'queued': False, 'reason': 'already_queued', 'queue_id': '...'}

        Dedupe: if this album is already in `queued` or `running`
        status, returns the existing entry's queue_id rather than
        adding a duplicate. ``cancelled`` / ``done`` / ``failed``
        items don't block re-enqueue (user retried after a failure).
        """
        with self._cond:
            for existing in self._items:
                if existing.album_id == album_id and existing.status in ('queued', 'running'):
                    return {
                        'queued': False,
                        'reason': 'already_queued',
                        'queue_id': existing.queue_id,
                    }

            item = QueueItem(
                queue_id=uuid.uuid4().hex[:12],
                album_id=album_id,
                album_title=album_title,
                artist_id=artist_id,
                artist_name=artist_name,
                source=source,
                enqueued_at=time.time(),
            )
            self._items.append(item)
            position = sum(1 for i in self._items if i.status == 'queued')
            self._ensure_worker()
            self._cond.notify_all()
            logger.info(
                f"[Queue] Enqueued '{album_title}' (album_id={album_id}, "
                f"queue_id={item.queue_id}, position={position}, source={source or 'auto'})"
            )
            return {
                'queued': True,
                'queue_id': item.queue_id,
                'position': position,
            }

    def enqueue_many(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        """Bulk-enqueue a list of items. Each ``item`` is a dict with
        the same keys :meth:`enqueue` accepts (``album_id``,
        ``album_title``, ``artist_id``, ``artist_name``, ``source``).
        Dedupe still applies per-album-id.

        Holds the queue lock for the entire batch so two things hold:
        (1) the worker can't start draining mid-batch, and (2) duplicate
        album_ids inside the same batch get deduped against each other,
        not just against pre-existing items. Without (2), a fast runner
        could finish the first copy before the loop reached the second
        and both would enqueue.

        Returns a tally dict ``{'enqueued': N, 'already_queued': M,
        'total': len(items)}`` so the caller can report bulk results
        without doing the counting themselves. Used by the bulk
        Reorganize-All endpoint and any future maintenance jobs that
        enqueue at scale.
        """
        enqueued = 0
        already = 0
        seen_in_batch: set = set()
        with self._cond:
            # Snapshot album_ids that already block re-enqueue so we don't
            # rescan self._items per row.
            blocked = {
                i.album_id for i in self._items if i.status in ('queued', 'running')
            }
            for raw in items:
                album_id = str(raw['album_id'])
                if album_id in blocked or album_id in seen_in_batch:
                    already += 1
                    continue
                seen_in_batch.add(album_id)
                item = QueueItem(
                    queue_id=uuid.uuid4().hex[:12],
                    album_id=album_id,
                    album_title=raw.get('album_title') or 'Unknown Album',
                    artist_id=str(raw['artist_id']) if raw.get('artist_id') is not None else None,
                    artist_name=raw.get('artist_name') or 'Unknown Artist',
                    source=raw.get('source'),
                    enqueued_at=time.time(),
                )
                self._items.append(item)
                enqueued += 1
                logger.info(
                    f"[Queue] Bulk-enqueued '{item.album_title}' (album_id={album_id}, "
                    f"queue_id={item.queue_id}, source={item.source or 'auto'})"
                )
            if enqueued:
                self._ensure_worker()
                self._cond.notify_all()
        return {'enqueued': enqueued, 'already_queued': already, 'total': len(items)}

    def cancel(self, queue_id: str) -> dict:
        """Cancel a queued item. The currently-running item cannot be
        cancelled (Python threads aren't cleanly killable; post-process
        may have spawned ffmpeg)."""
        with self._cond:
            for item in self._items:
                if item.queue_id != queue_id:
                    continue
                if item.status == 'queued':
                    item.status = 'cancelled'
                    item.finished_at = time.time()
                    logger.info(f"[Queue] Cancelled queued item {queue_id} ('{item.album_title}')")
                    return {'cancelled': True}
                if item.status == 'running':
                    return {'cancelled': False, 'reason': 'running_cant_cancel'}
                return {'cancelled': False, 'reason': 'not_active'}
        return {'cancelled': False, 'reason': 'not_found'}

    def clear_queued(self) -> int:
        """Cancel ALL queued items (running item continues). Returns
        the count of items cancelled."""
        cancelled = 0
        with self._cond:
            now = time.time()
            for item in self._items:
                if item.status == 'queued':
                    item.status = 'cancelled'
                    item.finished_at = now
                    cancelled += 1
        if cancelled:
            logger.info(f"[Queue] Bulk-cancelled {cancelled} queued items")
        return cancelled

    def snapshot(self) -> dict:
        """Current queue state for the status panel. Returns:

            {
                'active': item dict | None,
                'queued': [item dicts in FIFO order],
                'recent': [item dicts in finish order, newest first, capped],
                'totals': {'queued': N, 'running': M, 'done_today': K, ...},
            }
        """
        with self._cond:
            active = next((i for i in self._items if i.status == 'running'), None)
            queued = [i for i in self._items if i.status == 'queued']
            recent = [i for i in self._items if i.status in ('done', 'failed', 'cancelled')]
            recent.sort(key=lambda i: i.finished_at or 0, reverse=True)
            recent = recent[:_RECENT_HISTORY_CAP]

            return {
                'active': active.to_snapshot() if active else None,
                'queued': [i.to_snapshot() for i in queued],
                'recent': [i.to_snapshot() for i in recent],
                'totals': {
                    'queued': len(queued),
                    'running': 1 if active else 0,
                    'done': sum(1 for i in self._items if i.status == 'done'),
                    'failed': sum(1 for i in self._items if i.status == 'failed'),
                    'cancelled': sum(1 for i in self._items if i.status == 'cancelled'),
                },
            }

    def stop(self) -> None:
        """Stop the worker (called on server shutdown)."""
        with self._cond:
            self._stopped = True
            self._cond.notify_all()

    # -- internals ---------------------------------------------------

    def _ensure_worker(self) -> None:
        """Lazy worker start — only spawn the thread when there's
        actually something to process. Caller MUST hold ``_cond``."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._run, daemon=True, name='ReorganizeQueueWorker'
        )
        self._worker.start()

    def _claim_next_or_wait(self) -> Optional[QueueItem]:
        """Atomically pick the next queued item AND flip it to 'running'
        under a single lock acquisition. If the queue is empty, block
        on ``_cond.wait()`` (which releases the lock while sleeping)
        and return None when we're notified or timeout. Returning the
        item already-marked-running closes the cancel-vs-run race: a
        cancel() call now sees status='running' and is rejected."""
        with self._cond:
            while not self._stopped:
                for item in self._items:
                    if item.status == 'queued':
                        item.status = 'running'
                        item.started_at = time.time()
                        return item
                # No queued items — wait for an enqueue or shutdown.
                # 60s timeout so a stuck notify (shouldn't happen, but
                # defensive) doesn't park the worker forever.
                self._cond.wait(timeout=60)
            return None

    def _run(self) -> None:
        """Worker loop: pull next queued, run it, mark done, repeat.
        Idles on `_cond.wait()` when queue is empty."""
        logger.info("[Queue] Worker thread started")
        while not self._stopped:
            item = self._claim_next_or_wait()
            if item is None:
                # Only happens on shutdown — `_claim_next_or_wait` only
                # returns None once `_stopped` is True. Loop back to the
                # `while not self._stopped` check, which exits.
                continue
            logger.info(f"[Queue] Starting '{item.album_title}' (queue_id={item.queue_id})")

            try:
                runner = self._runner
                if runner is None:
                    raise RuntimeError("Queue has no runner configured — call set_runner() at startup")
                summary = runner(item)
            except Exception as e:
                logger.error(
                    f"[Queue] Runner raised for '{item.album_title}': {e}",
                    exc_info=True,
                )
                with self._cond:
                    item.status = 'failed'
                    item.error = str(e)
                    item.finished_at = time.time()
                continue

            with self._cond:
                item.moved = int(summary.get('moved', 0))
                item.skipped = int(summary.get('skipped', 0))
                item.failed = int(summary.get('failed', 0))
                item.result_status = summary.get('status')
                item.result_source = summary.get('source')
                errors = summary.get('errors') or []
                if errors:
                    first_err = errors[0] if isinstance(errors[0], dict) else {'error': str(errors[0])}
                    item.error = first_err.get('error') or first_err.get('reason')
                # 'failed' status only when the run produced concrete failed tracks
                # OR ended in a non-completed state (no_source_id / no_album / etc).
                item.status = 'failed' if (item.failed > 0 or item.result_status not in (None, 'completed')) else 'done'
                item.finished_at = time.time()
                # Clear live-progress fields — done items don't need them.
                item.current_track = None
                item.progress_total = 0
                item.progress_processed = 0

            logger.info(
                f"[Queue] Finished '{item.album_title}' — status={item.status}, "
                f"moved={item.moved}, skipped={item.skipped}, failed={item.failed}"
            )
        logger.info("[Queue] Worker thread exiting")

    # Called by the runner (or test) to push live progress onto the
    # currently-running item. Safe to call from worker thread inside
    # reorganize_album's on_progress callback.
    def update_active_progress(self, *, queue_id: str, **fields) -> None:
        with self._cond:
            for item in self._items:
                if item.queue_id == queue_id and item.status == 'running':
                    if 'current_track' in fields:
                        item.current_track = fields['current_track']
                    if 'total' in fields:
                        item.progress_total = int(fields['total'])
                    if 'processed' in fields:
                        item.progress_processed = int(fields['processed'])
                    if 'moved' in fields:
                        item.moved = int(fields['moved'])
                    if 'skipped' in fields:
                        item.skipped = int(fields['skipped'])
                    if 'failed' in fields:
                        item.failed = int(fields['failed'])
                    return


# Module-level singleton accessor ---------------------------------------------

_singleton: Optional[ReorganizeQueue] = None
_singleton_lock = threading.Lock()


def get_queue() -> ReorganizeQueue:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = ReorganizeQueue()
        return _singleton


def reset_queue_for_tests() -> None:
    """Test-only: drop the singleton so the next get_queue() returns
    a fresh instance. Production code never calls this."""
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.stop()
        _singleton = None
