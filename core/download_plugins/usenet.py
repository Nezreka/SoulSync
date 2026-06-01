"""UsenetDownloadPlugin — composes Prowlarr search + usenet client
adapter + archive_pipeline into a uniform download source.

Mirrors ``TorrentDownloadPlugin`` in shape and lifecycle (see that
module's docstring for the full pipeline rationale). Differences:

- Search filters Prowlarr results to ``protocol='usenet'``.
- ``add_nzb`` replaces ``add_torrent``; for NZBs we usually have
  a direct HTTP URL the indexer exposes via Prowlarr.
- Usenet clients (SABnzbd, NZBGet) typically auto-extract during
  post-processing, so ``archive_pipeline.collect_audio_after_extraction``
  usually has nothing to extract and just walks loose files.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.archive_pipeline import collect_audio_after_extraction
from core.download_plugins.album_bundle import (
    TransientMissCounter,
    copy_audio_files_atomically,
    get_completed_no_path_window_seconds,
    pick_best_album_release,
    poll_album_download,
    resolve_reported_save_path,
)
from core.download_plugins.base import DownloadSourcePlugin
from core.download_plugins.torrent import (
    _adapter_state_to_display,
    _decode_filename,
    _guess_quality_from_title,
    _parse_indexer_id_filter,
    _parse_release_title,
    _row_to_status,
    _COMPLETE_STATES,
    _FILENAME_SEP,
    _POLL_INTERVAL_SECONDS,
    _POLL_TIMEOUT_SECONDS,
)
from core.download_plugins.types import AlbumResult, DownloadStatus, TrackResult
from core.prowlarr_client import (
    DEFAULT_MUSIC_CATEGORIES,
    ProwlarrClient,
    ProwlarrSearchResult,
)
from core.usenet_clients import get_active_adapter as get_active_usenet_adapter
from utils.async_helpers import run_async
from utils.logging_config import get_logger

logger = get_logger("download_plugins.usenet")


class UsenetDownloadPlugin(DownloadSourcePlugin):
    """Usenet download source backed by Prowlarr + an active usenet
    client adapter (SABnzbd or NZBGet)."""

    def __init__(self) -> None:
        self._prowlarr = ProwlarrClient()
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.shutdown_check = None

    def set_shutdown_check(self, check_callable):
        self.shutdown_check = check_callable

    def reload_settings(self) -> None:
        self._prowlarr.reload_settings()

    def is_configured(self) -> bool:
        if not self._prowlarr.is_configured():
            return False
        adapter = get_active_usenet_adapter()
        return bool(adapter and adapter.is_configured())

    async def check_connection(self) -> bool:
        if not self._prowlarr.is_configured():
            return False
        adapter = get_active_usenet_adapter()
        if not adapter or not adapter.is_configured():
            return False
        if not await self._prowlarr.check_connection():
            return False
        return await adapter.check_connection()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        timeout: Optional[int] = None,
        progress_callback=None,
    ) -> Tuple[List[TrackResult], List[AlbumResult]]:
        if not self._prowlarr.is_configured():
            return ([], [])
        try:
            indexer_ids = _parse_indexer_id_filter()
            results = await self._prowlarr.search(
                query,
                categories=DEFAULT_MUSIC_CATEGORIES,
                indexer_ids=indexer_ids,
            )
        except Exception as e:
            logger.error("Usenet plugin search failed: %s", e)
            return ([], [])
        return self._project_results(results)

    def _project_results(
        self, results: List[ProwlarrSearchResult]
    ) -> Tuple[List[TrackResult], List[AlbumResult]]:
        tracks: List[TrackResult] = []
        albums: List[AlbumResult] = []
        for result in results:
            if result.protocol != 'usenet':
                continue
            if not result.download_url:
                continue
            filename = f"{result.download_url}{_FILENAME_SEP}{result.title}"
            quality = _guess_quality_from_title(result.title)
            parsed_artist, parsed_title = _parse_release_title(result.title)
            tr = TrackResult(
                username='usenet',
                filename=filename,
                size=result.size,
                bitrate=None,
                duration=None,
                quality=quality,
                # Usenet doesn't expose per-uploader concurrency the way
                # Soulseek does; fill in neutral non-punishing values.
                free_upload_slots=1,
                upload_speed=0,
                queue_length=0,
                # Pre-fill artist + title so TrackResult.__post_init__
                # doesn't auto-parse the filename — same URL-in-filename
                # gotcha as the torrent plugin.
                artist=parsed_artist or result.indexer_name or 'Usenet',
                title=parsed_title or result.title,
                album=parsed_title or None,
                track_number=None,
                _source_metadata={
                    'indexer': result.indexer_name,
                    'indexer_id': result.indexer_id,
                    'grabs': result.grabs,
                    'protocol': 'usenet',
                },
            )
            tracks.append(tr)
            albums.append(AlbumResult(
                username='usenet',
                album_path=f"usenet/{result.guid}",
                album_title=parsed_title or result.title,
                artist=parsed_artist or None,
                track_count=1,
                total_size=result.size,
                tracks=[tr],
                dominant_quality=quality,
                year=None,
            ))
        return tracks, albums

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    async def download(
        self,
        username: str,
        filename: str,
        file_size: int = 0,
    ) -> Optional[str]:
        if not self.is_configured():
            return None
        nzb_url, display_name = _decode_filename(filename)
        if not nzb_url:
            logger.error("Usenet download missing URL in filename: %r", filename)
            return None

        download_id = str(uuid.uuid4())
        with self._lock:
            self.active_downloads[download_id] = {
                'id': download_id,
                'filename': filename,
                'username': 'usenet',
                'display_name': display_name,
                'state': 'Initializing',
                'progress': 0.0,
                'size': file_size,
                'transferred': 0,
                'speed': 0,
                'file_path': None,
                'audio_files': [],
                'job_id': None,
                'error': None,
            }

        thread = threading.Thread(
            target=self._download_thread,
            args=(download_id, nzb_url),
            daemon=True,
            name=f'usenet-dl-{download_id[:8]}',
        )
        thread.start()
        return download_id

    def _download_thread(self, download_id: str, nzb_url: str) -> None:
        adapter = get_active_usenet_adapter()
        if adapter is None or not adapter.is_configured():
            self._mark_error(download_id, "No usenet client configured")
            return

        try:
            job_id = run_async(adapter.add_nzb(nzb_url))
        except Exception as e:
            self._mark_error(download_id, f"add_nzb failed: {e}")
            return
        if not job_id:
            self._mark_error(download_id, "Usenet client refused the NZB")
            return

        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['job_id'] = job_id
                row['state'] = 'InProgress, Downloading'

        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        last_save_path: Optional[str] = None
        last_incomplete_path: Optional[str] = None
        # Tolerate transient None / unmapped 'error' reads — SAB
        # removes a job from the queue before adding it to history,
        # and on busy servers that gap spans several polls. See
        # ``album_bundle.TransientMissCounter`` for the shared rule.
        misses = TransientMissCounter()
        # Separate, LONGER window for "SAB says completed but hasn't
        # written the final save_path yet" — the per-track sibling of the
        # bundle fix (#721). Without this the thread called
        # ``_finalize_download(None)`` on the first Completed-no-path read
        # and errored a download that actually succeeded in SAB. Default
        # ~120s, converted to a poll count against the live interval.
        completed_no_path_misses = TransientMissCounter(
            max(misses.threshold,
                int(get_completed_no_path_window_seconds() / max(_POLL_INTERVAL_SECONDS, 0.001)) or 1)
        )
        while time.monotonic() < deadline:
            if self.shutdown_check and self.shutdown_check():
                return
            try:
                status = run_async(adapter.get_status(job_id))
            except Exception as e:
                logger.warning("Usenet poll error for %s: %s", job_id, e)
                status = None

            if status is None:
                if misses.record_miss():
                    self._mark_error(
                        download_id,
                        f"Usenet job disappeared from client (no status after {misses.threshold} polls)",
                    )
                    return
                time.sleep(_POLL_INTERVAL_SECONDS)
                continue

            if status.state != 'error':
                misses.reset()

            with self._lock:
                row = self.active_downloads.get(download_id)
                if row is not None:
                    row['progress'] = status.progress * 100.0
                    row['transferred'] = status.downloaded
                    row['speed'] = status.download_speed
                    row['size'] = status.size or row.get('size', 0)
                    row['state'] = _adapter_state_to_display(status.state)
                    row['error'] = status.error
            if status.save_path:
                last_save_path = status.save_path
            incomplete_path = getattr(status, 'incomplete_path', None)
            if incomplete_path:
                last_incomplete_path = incomplete_path

            if status.state in _COMPLETE_STATES:
                if last_save_path:
                    self._finalize_download(download_id, last_save_path)
                    return
                # Completed but no final save_path yet — SAB flips
                # History to 'Completed' before writing ``storage``.
                # Wait out the (longer) completed-no-path window rather
                # than erroring a download that actually succeeded.
                if completed_no_path_misses.record_miss():
                    if last_incomplete_path:
                        logger.warning(
                            "Usenet %s: '%s' completed but no final save_path after "
                            "%d polls — falling back to in-progress path %r",
                            download_id[:8], job_id, completed_no_path_misses.misses,
                            last_incomplete_path,
                        )
                        self._finalize_download(download_id, last_incomplete_path)
                        return
                    self._mark_error(
                        download_id,
                        "Usenet job completed but client never reported a save_path",
                    )
                    return
                logger.info(
                    "Usenet %s: '%s' completed on client but save_path not yet set — "
                    "retrying (poll %d/%d)",
                    download_id[:8], job_id,
                    completed_no_path_misses.misses, completed_no_path_misses.threshold,
                )
                time.sleep(_POLL_INTERVAL_SECONDS)
                continue
            if status.state == 'failed':
                self._mark_error(download_id, status.error or "Usenet client reported failure")
                return
            if status.state == 'error':
                logger.warning(
                    "Usenet poll: '%s' returned unmapped state — treating as transient",
                    job_id,
                )
                if misses.record_miss():
                    self._mark_error(
                        download_id,
                        "Usenet client returned unmapped state repeatedly",
                    )
                    return

            time.sleep(_POLL_INTERVAL_SECONDS)

        self._mark_error(download_id, "Usenet download timed out")

    def _finalize_download(self, download_id: str, save_path: Optional[str]) -> None:
        if not save_path:
            self._mark_error(download_id, "Usenet job completed but no save_path reported")
            return
        # Translate the client-reported path to one THIS process can read
        # (SAB reports its own container path; SoulSync may see the same
        # files at a different mount). See ``resolve_reported_save_path``.
        local_path = resolve_reported_save_path(save_path)
        if local_path != save_path:
            logger.info("Usenet %s: resolved client path %r -> %r",
                        download_id[:8], save_path, local_path)
        try:
            audio_files = collect_audio_after_extraction(Path(local_path))
        except Exception as e:
            self._mark_error(download_id, f"Post-extract walk failed: {e}")
            return
        if not audio_files:
            suffix = f" (resolved: {local_path})" if local_path != save_path else ""
            self._mark_error(download_id, f"No audio files found in {save_path}{suffix}")
            return
        primary = audio_files[0]
        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['state'] = 'Completed, Succeeded'
                row['progress'] = 100.0
                row['file_path'] = str(primary)
                row['audio_files'] = [str(path) for path in audio_files]
        logger.info("Usenet download complete: %s -> %s (%d audio files)",
                    download_id[:8], primary.name, len(audio_files))

    def _mark_error(self, download_id: str, message: str) -> None:
        logger.error("Usenet download %s failed: %s", download_id[:8], message)
        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['state'] = 'Completed, Errored'
                row['error'] = message

    # ------------------------------------------------------------------
    # Status / lifecycle
    # ------------------------------------------------------------------

    async def get_all_downloads(self) -> List[DownloadStatus]:
        with self._lock:
            rows = list(self.active_downloads.values())
        return [_row_to_status(r) for r in rows]

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is None:
                return None
            return _row_to_status(row)

    async def cancel_download(
        self,
        download_id: str,
        username: Optional[str] = None,
        remove: bool = False,
    ) -> bool:
        adapter = get_active_usenet_adapter()
        with self._lock:
            row = self.active_downloads.get(download_id)
            job_id = row.get('job_id') if row else None
        if adapter and job_id:
            try:
                await adapter.remove(job_id, delete_files=remove)
            except Exception as e:
                logger.warning("Usenet cancel via adapter failed: %s", e)
        with self._lock:
            if remove:
                self.active_downloads.pop(download_id, None)
            else:
                row = self.active_downloads.get(download_id)
                if row is not None:
                    row['state'] = 'Cancelled'
        return True

    async def clear_all_completed_downloads(self) -> bool:
        with self._lock:
            for did in list(self.active_downloads.keys()):
                state = self.active_downloads[did].get('state', '')
                if state.startswith('Completed') or state == 'Cancelled':
                    self.active_downloads.pop(did, None)
        return True

    # ------------------------------------------------------------------
    # Album-bundle flow
    # ------------------------------------------------------------------

    def download_album_to_staging(
        self,
        album_name: str,
        artist_name: str,
        staging_dir: str,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """Usenet sibling of ``TorrentDownloadPlugin.download_album_to_staging``.
        See that method's docstring for the contract."""
        result: Dict[str, Any] = {'success': False, 'files': [], 'error': None}
        if not self.is_configured():
            result['error'] = 'Usenet source not configured'
            return result

        adapter = get_active_usenet_adapter()
        if adapter is None or not adapter.is_configured():
            result['error'] = 'No active usenet client'
            return result

        def _emit(state: str, **extra) -> None:
            if progress_callback:
                try:
                    progress_callback({'state': state, **extra})
                except Exception as cb_exc:
                    logger.debug("[Usenet album] progress callback failed: %s", cb_exc)

        query = f"{artist_name} {album_name}".strip()
        _emit('searching', query=query)
        try:
            search_results = run_async(self._prowlarr.search(
                query, categories=DEFAULT_MUSIC_CATEGORIES,
                indexer_ids=_parse_indexer_id_filter(),
            ))
        except Exception as e:
            result['error'] = f'Prowlarr search failed: {e}'
            return result

        candidates = [r for r in search_results
                      if r.protocol == 'usenet' and r.download_url]
        if not candidates:
            # Album isn't available on this source — fall back to the per-track
            # flow (next configured source in hybrid mode) rather than hard-
            # failing the whole batch. Mirrors the torrent plugin + soulseek's
            # default fallback contract.
            result['error'] = f'No usenet results found for "{query}"'
            result['fallback'] = True
            return result

        picked = pick_best_album_release(
            candidates, _guess_quality_from_title, album_name=album_name,
        )
        if picked is None:
            # No candidate matched the requested album (or none passed filtering).
            # Fall back to per-track rather than grabbing a wrong album (#730).
            result['error'] = 'No NZB candidate matched the requested album'
            result['fallback'] = True
            return result

        logger.info("[Usenet album] Picked '%s' (size=%.1fMB grabs=%s indexer=%s)",
                    picked.title, picked.size / 1_048_576, picked.grabs, picked.indexer_name)
        _emit('queued', release=picked.title, size=picked.size, grabs=picked.grabs)

        try:
            job_id = run_async(adapter.add_nzb(picked.download_url))
        except Exception as e:
            result['error'] = f'Usenet client refused the NZB: {e}'
            return result
        if not job_id:
            result['error'] = 'Usenet client refused the NZB'
            return result

        _emit('downloading', release=picked.title)
        save_path = poll_album_download(
            get_status=lambda: run_async(adapter.get_status(job_id)),
            title=picked.title,
            emit=_emit,
            # Usenet completes into history as 'completed'; no 'seeding'
            # equivalent. Failed is explicit on history failures.
            complete_states=frozenset(['completed']),
            failed_states=frozenset(['failed']),
            is_shutdown=self.shutdown_check,
            log_prefix='[Usenet album]',
        )
        if save_path is None:
            # poll_album_download already emitted the terminal 'failed'
            # state on every failure path (timeout / disappeared /
            # explicit failure / unmapped). UI is unstuck either way.
            result['error'] = 'Usenet download failed or timed out'
            return result

        _emit('staging', release=picked.title)
        # SAB reports its own container path; SoulSync may mount the same
        # files elsewhere. Resolve to a locally-readable path before walking.
        local_path = resolve_reported_save_path(save_path)
        if local_path != save_path:
            logger.info("[Usenet album] Resolved client path %r -> %r", save_path, local_path)
        try:
            audio_files = collect_audio_after_extraction(Path(local_path))
        except Exception as e:
            result['error'] = f'Failed to walk audio files: {e}'
            return result
        if not audio_files:
            suffix = f' (resolved: {local_path})' if local_path != save_path else ''
            result['error'] = f'No audio files found in {save_path}{suffix}'
            return result

        copied = copy_audio_files_atomically(audio_files, Path(staging_dir))
        if not copied:
            result['error'] = 'No audio files copied to staging'
            return result
        logger.info("[Usenet album] Staged %d audio files for '%s'", len(copied), album_name)
        _emit('staged', count=len(copied))
        result['success'] = True
        result['files'] = copied
        return result

