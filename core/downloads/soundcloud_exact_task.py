"""The download path for an exact SoundCloud upload (core/wishlist/soundcloud_exact).

The per-task worker hands a ``soundcloud_exact_<id>`` track here before any
of its title/artist machinery runs (cross-batch dedup, cached retry, source
reuse, staging match, the query loop). This path resolves the stored
SoundCloud url, keeps only the upload with the same SoundCloud id, and
starts that one file through the usual ``attempt_download_with_candidates``
(failed-download blocklist, monitor, post-processing, quality guard).

It never searches and never takes a different recording: an upload that is
gone, swapped or preview-only fails with the reason, and the wishlist row
waits under the normal retry backoff.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

from core.runtime_state import download_tasks, tasks_lock
from utils.logging_config import get_logger

logger = get_logger("downloads.soundcloud_exact")

MISSING_BLOCK = ("Exact SoundCloud source details are missing — remove this entry "
                 "and add the upload again.")
SOURCE_DISABLED = "SoundCloud is disabled as a download source in Settings"
SOURCE_UNAVAILABLE = "SoundCloud downloads need yt-dlp on the server"
UNAVAILABLE = "SoundCloud upload unavailable (deleted, made private or region-locked): {url}"
PREVIEW_ONLY = "SoundCloud only serves a 30-second preview of this upload (Go+) — not downloaded."


def _cancel_state(task_id: str, batch_id: Optional[str]):
    """(stop, free_legacy_slot), read the way the worker's cancellation
    checkpoint 1 reads it (core/downloads/task_worker.py). the caller holds
    tasks_lock. a deleted task just stops; a cancelled V2 task (it has a
    playlist_id) had its slot freed by the atomic cancel; a cancelled legacy
    batch task still holds one."""
    task = download_tasks.get(task_id)
    if task is None:
        return True, False
    if task.get('status') != 'cancelled':
        return False, False
    return True, bool(batch_id) and not task.get('playlist_id')


def _free_legacy_slot(task_id: str, batch_id: Optional[str], deps: Any) -> None:
    """outside tasks_lock: on_download_completed takes it again (a plain
    Lock), and it's idempotent if the cancel already freed the slot."""
    logger.warning("[Exact SoundCloud] legacy task %s cancelled - using legacy completion callback", task_id)
    try:
        deps.on_download_completed(batch_id, task_id, False)
    except Exception as completion_error:  # noqa: BLE001
        logger.error("Error in batch completion callback for %s: %s", task_id, completion_error)


def _exit_if_cancelled(task_id: str, batch_id: Optional[str], deps: Any) -> bool:
    with tasks_lock:
        stop, free = _cancel_state(task_id, batch_id)
    if free:
        _free_legacy_slot(task_id, batch_id, deps)
    return stop


def _default_db():
    from database.music_database import get_database
    return get_database()


def _profile_id(task_id: str, batch_id: Optional[str], track_data: dict) -> int:
    raw = track_data.get('profile_id') if isinstance(track_data, dict) else None
    if raw in (None, ''):
        from core.runtime_state import download_batches
        with tasks_lock:
            raw = (download_batches.get(batch_id) or {}).get('profile_id') if batch_id else None
    try:
        return int(raw or 1)
    except (TypeError, ValueError):
        return 1


def _pin_download_url(candidate: Any, url: str) -> None:
    """a private share link carries its token (and a short link can stand for
    one); make sure the download uses it even when the resolve's permalink
    dropped the token."""
    from core.wishlist.soundcloud_exact import needs_resolve
    if '/s-' not in url and not needs_resolve(url):
        return
    parts = str(getattr(candidate, 'filename', '') or '').split('||', 2)
    if len(parts) >= 2 and '/s-' not in parts[1]:
        parts[1] = url
        candidate.filename = '||'.join(parts)
        meta = getattr(candidate, '_source_metadata', None)
        if isinstance(meta, dict):
            meta['permalink_url'] = url


def run_exact_soundcloud_task(task_id: str, batch_id: Optional[str], track_data: dict,
                              track: Any, deps: Any, *, db: Any = None) -> None:
    from core.downloads.task_worker import _candidate_ordering, _notify_acquisition_retry_exhausted
    from core.downloads.validation import filter_soundcloud_previews
    from core.wishlist import soundcloud_exact as exact

    def _fail(reason: str) -> None:
        # a cancel that landed since the last checkpoint takes the cancelled
        # exit: 'failed' must not overwrite it, nor complete the task twice
        with tasks_lock:
            stop, free = _cancel_state(task_id, batch_id)
            if not stop:
                download_tasks[task_id]['status'] = 'failed'
                download_tasks[task_id]['error_message'] = reason
        if stop:
            if free:
                _free_legacy_slot(task_id, batch_id, deps)
            return
        logger.warning("[Exact SoundCloud] task %s failed: %s", task_id, reason)
        _notify_acquisition_retry_exhausted(track_data, reason)
        if batch_id:
            try:
                deps.on_download_completed(batch_id, task_id, False)
            except Exception as completion_error:  # noqa: BLE001
                logger.error("Error in batch completion callback for %s: %s", task_id, completion_error)

    # a cancel can land between the worker's checkpoint 1 and here: it must
    # not be overwritten with 'searching'
    with tasks_lock:
        stop, free = _cancel_state(task_id, batch_id)
        if not stop:
            download_tasks[task_id]['status'] = 'searching'
            download_tasks[task_id]['current_source'] = 'soundcloud'
            download_tasks[task_id]['candidates'] = []
            download_tasks[task_id].setdefault('used_sources', set())
    if stop:
        if free:
            _free_legacy_slot(task_id, batch_id, deps)
        return

    block = exact.exact_source_for(track_data)
    if block is None:
        block = exact.exact_source_for(track_data, db if db is not None else _default_db(),
                                       _profile_id(task_id, batch_id, track_data))
    if block is None:
        return _fail(MISSING_BLOCK)

    url = exact.download_url(block)
    with tasks_lock:
        if task_id in download_tasks:
            download_tasks[task_id]['current_query'] = url

    enabled, reason = exact.source_state(deps.download_orchestrator)
    if not enabled:
        return _fail(SOURCE_DISABLED if reason == 'source_disabled' else SOURCE_UNAVAILABLE)

    if _exit_if_cancelled(task_id, batch_id, deps):
        return
    client = exact.soundcloud_client(deps.download_orchestrator)
    try:
        results, _albums = deps.run_async(client.resolve_url(url))
    except Exception as exc:  # noqa: BLE001 - resolve_url never raises; the loop can
        logger.warning("[Exact SoundCloud] resolve failed for %s: %s", url, exc)
        results = []
    if _exit_if_cancelled(task_id, batch_id, deps):
        return

    wanted = str(block.get('sc_id') or '')
    matches = [r for r in (results or [])
               if str((getattr(r, '_source_metadata', None) or {}).get('track_id') or '') == wanted]
    if not matches:
        return _fail(UNAVAILABLE.format(url=url))

    # a Go+ upload served anonymously: every stream the resolve listed is the
    # snippet (soundcloud_client._is_preview_format). the duration leg below
    # only fires when the expected length is independent of this resolve.
    matches = [r for r in matches if not (getattr(r, '_source_metadata', None) or {}).get('preview_only')]
    expected = track if getattr(track, 'duration_ms', 0) else SimpleNamespace(
        duration_ms=int(block.get('duration_ms') or 0))
    matches = filter_soundcloud_previews(matches, expected)
    if not matches:
        return _fail(PREVIEW_ONLY)

    candidate = matches[0]
    _pin_download_url(candidate, url)
    candidate.confidence = 1.0
    with tasks_lock:
        stop, free = _cancel_state(task_id, batch_id)
        if not stop:
            download_tasks[task_id]['cached_candidates'] = [candidate]
    if stop:
        if free:
            _free_legacy_slot(task_id, batch_id, deps)
        return

    quality_first, quality_targets = _candidate_ordering(track_data)
    if deps.attempt_download_with_candidates(
            task_id, [candidate], track, batch_id,
            quality_first=quality_first, quality_targets=quality_targets):
        logger.info("[Exact SoundCloud] task %s: started %s", task_id, url)
        return

    with tasks_lock:
        stop, free = _cancel_state(task_id, batch_id)
        why = [] if stop else list(download_tasks[task_id].get('start_failures') or ())
    if stop:
        if free:
            _free_legacy_slot(task_id, batch_id, deps)
        return
    return _fail("SoundCloud download failed to start"
                 + (f": {'; '.join(why[:2])}" if why else f" for {url}"))


__all__ = ["run_exact_soundcloud_task"]
