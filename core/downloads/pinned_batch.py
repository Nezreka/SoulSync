"""Basic search downloads as real batches.

the user already picked the exact file on the basic search page. these used to
go straight to the orchestrator with a loose context and no task, so they never
showed on the Downloads page and got none of the batch machinery (monitor,
verification, history).

now each pick becomes a batch with one task per file, and every task is PINNED:
it downloads exactly that file through attempt_download_with_candidates, the
same way the redownload flow and the candidate picker already do. no search,
no swapping to another peer. `_user_manual_pick` keeps every existing "don't
swap files" guard in the monitor honest.

torrent, usenet and lidarr are NOT pinnable here: one download is a whole
release, not a file. callers keep those on the old direct route.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from core.runtime_state import download_batches, download_tasks, tasks_lock
from utils.logging_config import get_logger

logger = get_logger("downloads.pinned_batch")

# one download is a whole release on these, a file can't be pinned
RELEASE_LEVEL_SOURCES = frozenset({'torrent', 'usenet', 'lidarr'})


def is_pinnable(username: Optional[str]) -> bool:
    return bool(username) and str(username).lower() not in RELEASE_LEVEL_SOURCES


@dataclass
class PinnedBatchDeps:
    """what dispatch needs from the web server, injected so this stays testable"""
    start_monitoring: Callable[[str], None]
    submit: Callable[..., Any]
    attempt_download_with_candidates: Callable[..., bool]
    on_download_completed: Callable[..., None]


@dataclass
class PinnedFile:
    """one file the user picked, plus what the task should know about it"""
    candidate: Dict[str, Any]      # username, filename, size, bitrate, duration, quality, ...
    track_info: Dict[str, Any]     # what post-processing tags and files it by


def simple_track_info(result: Dict[str, Any], *, album_name: str = '') -> Dict[str, Any]:
    """track_info for an as-is download. `_simple_download` makes the candidate
    handoff write the search_result context the simple post-processing branch
    reads, so the file lands untouched, like the old direct route did."""
    title = result.get('title') or ''
    artist = result.get('artist') or ''
    album = album_name or result.get('album') or ''
    return {
        'id': '',
        'name': title,
        'artists': [{'name': artist}] if artist else [],
        'album': {'name': album} if album else {},
        'duration_ms': result.get('duration') or 0,
        'track_number': result.get('track_number') or 0,
        '_simple_download': True,
        '_simple_search_result': {
            'username': result.get('username'),
            'filename': result.get('filename'),
            'size': result.get('size', 0),
            'title': title or 'Unknown',
            'artist': artist or 'Unknown',
            'album': album,
            'quality': result.get('quality', 'Unknown'),
            'is_simple_download': True,
        },
    }


def candidate_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'username': result.get('username'),
        'filename': result.get('filename'),
        'size': result.get('size') or 0,
        'bitrate': result.get('bitrate') or 0,
        'duration': result.get('duration') or 0,
        'quality': result.get('quality') or '',
        'free_upload_slots': result.get('free_upload_slots') or 0,
        'upload_speed': result.get('upload_speed') or 0,
        'queue_length': result.get('queue_length') or 0,
        'artist': result.get('artist') or '',
        'title': result.get('title') or '',
        'album': result.get('album') or '',
    }


# a caller that names no library leaves the decision to the batch registry,
# which makes it while the request (and an admin's pick) still exists (#1199)
_UNDECIDED = object()


def create_pinned_batch(
    files: List[PinnedFile],
    *,
    name: str,
    profile_id: Any,
    is_album: bool = False,
    album_context: Optional[Dict[str, Any]] = None,
    artist_context: Optional[Dict[str, Any]] = None,
    library_owner_id: Any = _UNDECIDED,
) -> tuple[str, List[str]]:
    """Write the batch and its tasks. one short lock, no I/O inside it.
    returns (batch_id, task_ids) in file order."""
    batch_id = str(uuid.uuid4())
    task_ids = [str(uuid.uuid4()) for _ in files]
    now = time.time()
    playlist_id = f'basic_search_{batch_id[:8]}'
    with tasks_lock:
        batch = {
            'queue': list(task_ids),
            # every task is dispatched at once below, so the queue is already
            # walked and every slot is taken. the old direct route also sent
            # a whole album to slskd at once, this keeps that speed
            'queue_index': len(task_ids),
            'active_count': len(task_ids),
            'max_concurrent': max(len(task_ids), 1),
            'playlist_id': playlist_id,
            'playlist_name': name,
            'source_page': 'Search',
            'phase': 'downloading',
            'total_tracks': len(task_ids),
            'completed_count': 0,
            'failed_count': 0,
            'cancelled_tracks': set(),
            'permanently_failed_tracks': [],
            'auto_initiated': False,
            'profile_id': profile_id,
            'is_album_download': bool(is_album),
            'album_context': album_context or {},
            'artist_context': artist_context or {},
            # the user picked this exact file. a failure means THAT file
            # failed, not that the song should be hunted down elsewhere
            'skip_failed_wishlist': True,
        }
        if library_owner_id is not _UNDECIDED:
            # the library the request had selected, decided while a request
            # still existed; every later stage reads it back (#1199)
            batch['library_owner_id'] = library_owner_id
        download_batches[batch_id] = batch
        for index, (task_id, pinned) in enumerate(zip(task_ids, files, strict=True)):
            # the profile rides on track_info too: post-processing reads it
            # from there (import_profile_id) even if the batch is gone by then,
            # which is what files an own-library profile's download in its
            # own folder (#1199/#1279)
            track_info = dict(pinned.track_info)
            if profile_id is not None:
                track_info.setdefault('profile_id', profile_id)
            download_tasks[task_id] = {
                'status': 'queued',
                'track_info': track_info,
                'playlist_id': playlist_id,
                'batch_id': batch_id,
                'track_index': index,
                'download_id': None,
                'username': None,
                'filename': None,
                'retry_count': 0,
                # lets the Downloads page offer a same-file retry
                'cached_candidates': [pinned.candidate],
                'used_sources': set(),
                'status_change_time': now,
                'metadata_enhanced': False,
                '_user_manual_pick': True,
                '_pinned_candidate': pinned.candidate,
                'error_message': None,
            }
    return batch_id, task_ids


def _track_result(candidate: Dict[str, Any]):
    from core.download_plugins.types import TrackResult
    tr = TrackResult(
        username=candidate['username'],
        filename=candidate['filename'],
        size=candidate.get('size', 0),
        bitrate=candidate.get('bitrate', 0),
        duration=candidate.get('duration', 0),
        quality=candidate.get('quality', ''),
        free_upload_slots=candidate.get('free_upload_slots', 0),
        upload_speed=candidate.get('upload_speed', 0),
        queue_length=candidate.get('queue_length', 0),
    )
    tr.artist = candidate.get('artist', '')
    tr.title = candidate.get('title', '')
    tr.album = candidate.get('album', '')
    tr.confidence = 1.0
    return tr


def _track_object(track_info: Dict[str, Any]):
    """the attribute-style track attempt_download_with_candidates reads"""
    from core.itunes_client import Track as MetaTrack
    artists = []
    for artist in track_info.get('artists') or []:
        name = artist.get('name') if isinstance(artist, dict) else artist
        if name:
            artists.append(str(name))
    album = track_info.get('album')
    album_name = album.get('name', '') if isinstance(album, dict) else (album or '')
    return MetaTrack(
        id=str(track_info.get('id') or ''),
        name=track_info.get('name') or '',
        artists=artists or ['Unknown'],
        album=album_name,
        duration_ms=track_info.get('duration_ms') or 0,
        popularity=0,
    )


def dispatch_pinned_batch(batch_id: str, task_ids: List[str], deps: PinnedBatchDeps) -> None:
    """Start every task on its pinned file. monitoring is registered first:
    a fast streaming source can finish before the executor even returns."""
    deps.start_monitoring(batch_id)
    for task_id in task_ids:
        deps.submit(_run_pinned_task, task_id, batch_id, deps)


def _run_pinned_task(task_id: str, batch_id: str, deps: PinnedBatchDeps) -> None:
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task or task.get('status') == 'cancelled':
            return
        candidate = dict(task.get('_pinned_candidate') or {})
        track_info = task.get('track_info') or {}
    try:
        started = deps.attempt_download_with_candidates(
            task_id, [_track_result(candidate)], _track_object(track_info), batch_id)
    except Exception as exc:  # noqa: BLE001 - one bad file must not wedge the batch
        logger.error("[Pinned] task %s failed to start: %s", task_id, exc, exc_info=True)
        started = False
        # the message only. the status is left for the block below, which
        # fails the task AND tells the batch. setting 'failed' here made that
        # block think it was already reported, and the batch never finished
        with tasks_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['error_message'] = str(exc)
    if started:
        return
    # attempt_download_with_candidates already failed the task and freed its
    # slot when it could; make sure the task is terminal and the batch hears
    # about it, OUTSIDE tasks_lock (on_download_completed takes it)
    with tasks_lock:
        task = download_tasks.get(task_id)
        if not task:
            return
        already_terminal = task.get('status') in ('failed', 'not_found', 'cancelled', 'completed')
        if not already_terminal:
            task['status'] = 'failed'
            task['error_message'] = task.get('error_message') or (
                "The file couldn't be downloaded from this source")
    if not already_terminal:
        deps.on_download_completed(batch_id, task_id, success=False)
