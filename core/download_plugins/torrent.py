"""TorrentDownloadPlugin — composes Prowlarr search + torrent client
adapter + archive_pipeline into a uniform download source.

Pipeline:
1. ``search(query)`` calls ``ProwlarrClient.search`` filtered to
   ``protocol='torrent'`` results, projects releases into
   ``TrackResult`` / ``AlbumResult`` shaped objects the existing
   search UI already understands. Encodes the indexer's
   ``downloadUrl`` (or magnet URI) into the filename so
   ``download()`` can recover it.
2. ``download(username, filename, ...)`` decodes the URL, asks the
   active torrent adapter (qBittorrent, Transmission, or Deluge per
   user's settings) to add it, spawns a background thread that
   polls the adapter for completion.
3. On completion the thread walks the adapter-reported save path
   via ``archive_pipeline.collect_audio_after_extraction`` and
   marks the download succeeded with the first audio file as the
   primary ``file_path`` (matches Lidarr's single-track-pick
   contract — picking which specific track to import happens in
   post-processing, not here).

Limitations:
- ``save_path`` is the torrent client's view of the disk. If
  SoulSync runs on a different host than qBit / Trans / Deluge,
  the post-processing pipeline can't see those files. The plugin
  works fine for the all-on-one-box case (most users); remote
  setups will need a future sync step (rclone / SMB / Docker
  bind mount).
- Track-level metadata isn't available until after download.
  Search results carry only the release title + indexer metadata;
  individual track names are populated when the matching pipeline
  walks the extracted audio files.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import config_manager
from core.archive_pipeline import collect_audio_after_extraction
from core.download_plugins.base import DownloadSourcePlugin
from core.download_plugins.types import AlbumResult, DownloadStatus, TrackResult
from core.prowlarr_client import (
    DEFAULT_MUSIC_CATEGORIES,
    ProwlarrClient,
    ProwlarrSearchResult,
)
from core.torrent_clients import get_active_adapter as get_active_torrent_adapter
from utils.async_helpers import run_async
from utils.logging_config import get_logger

logger = get_logger("download_plugins.torrent")


# Separator used to encode the download URL inside the filename
# field. Same convention Lidarr / YouTube use for embedding their
# own opaque identifiers — ``<download_url>||<display>``.
_FILENAME_SEP = '||'

# Adapter states that count as the download being on-disk and
# safe to walk. ``seeding`` and ``completed`` both mean the
# bits are there; the user can pause seeding manually if they
# don't want to keep sharing.
_COMPLETE_STATES = frozenset(['seeding', 'completed'])

# Max seconds the poll thread keeps watching one download before
# giving up. 6 hours covers slow private trackers without leaking
# threads forever on dead torrents.
_POLL_TIMEOUT_SECONDS = 6 * 60 * 60

# Poll cadence — torrent state changes slowly; no point hammering
# the WebUI more than once a second.
_POLL_INTERVAL_SECONDS = 2.0


class TorrentDownloadPlugin(DownloadSourcePlugin):
    """Torrent download source backed by Prowlarr + an active
    torrent client adapter."""

    def __init__(self) -> None:
        self._prowlarr = ProwlarrClient()
        # Track every download we've kicked off. Keyed by our own
        # uuid — NOT the adapter's hash — because the orchestrator
        # owns the lifecycle and we need a stable id even before
        # the adapter has assigned one.
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.shutdown_check = None

    def set_shutdown_check(self, check_callable):
        self.shutdown_check = check_callable

    def reload_settings(self) -> None:
        self._prowlarr.reload_settings()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        if not self._prowlarr.is_configured():
            return False
        adapter = get_active_torrent_adapter()
        return bool(adapter and adapter.is_configured())

    async def check_connection(self) -> bool:
        if not self._prowlarr.is_configured():
            return False
        adapter = get_active_torrent_adapter()
        if not adapter or not adapter.is_configured():
            return False
        # Probe both sides. A torrent download is useless if either
        # the indexer or the downloader is unreachable.
        prowlarr_ok = await self._prowlarr.check_connection()
        if not prowlarr_ok:
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
            logger.error("Torrent plugin search failed: %s", e)
            return ([], [])
        return self._project_results(results)

    def _project_results(
        self, results: List[ProwlarrSearchResult]
    ) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Turn Prowlarr releases into TrackResult / AlbumResult
        shaped objects. One TrackResult + one AlbumResult per
        release — Prowlarr search hits are at the release level,
        not the track level, so we can't synthesise track listings
        without downloading the actual torrent."""
        tracks: List[TrackResult] = []
        albums: List[AlbumResult] = []
        for result in results:
            if result.protocol != 'torrent':
                continue
            download_url = result.magnet_uri or result.download_url
            if not download_url:
                continue
            filename = f"{download_url}{_FILENAME_SEP}{result.title}"
            quality = _guess_quality_from_title(result.title)
            tr = TrackResult(
                username='torrent',
                filename=filename,
                size=result.size,
                bitrate=None,
                duration=None,
                quality=quality,
                # Torrent results don't have per-uploader slot / queue
                # data the way Soulseek does. Fill with neutral values
                # so the quality_score doesn't punish them artificially.
                free_upload_slots=max(1, result.seeders or 0),
                upload_speed=0,
                queue_length=0,
                artist=None,
                title=result.title,
                album=None,
                track_number=None,
                _source_metadata={
                    'indexer': result.indexer_name,
                    'indexer_id': result.indexer_id,
                    'seeders': result.seeders,
                    'leechers': result.leechers,
                    'grabs': result.grabs,
                    'protocol': 'torrent',
                },
            )
            tracks.append(tr)
            albums.append(AlbumResult(
                username='torrent',
                album_path=f"torrent/{result.guid}",
                album_title=result.title,
                artist=None,
                track_count=1,    # unknown until download finishes
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
        download_url, display_name = _decode_filename(filename)
        if not download_url:
            logger.error("Torrent download missing URL in filename: %r", filename)
            return None

        download_id = str(uuid.uuid4())
        with self._lock:
            self.active_downloads[download_id] = {
                'id': download_id,
                'filename': filename,
                'username': 'torrent',
                'display_name': display_name,
                'state': 'Initializing',
                'progress': 0.0,
                'size': file_size,
                'transferred': 0,
                'speed': 0,
                'file_path': None,
                'torrent_hash': None,
                'error': None,
            }

        thread = threading.Thread(
            target=self._download_thread,
            args=(download_id, download_url, display_name),
            daemon=True,
            name=f'torrent-dl-{download_id[:8]}',
        )
        thread.start()
        return download_id

    def _download_thread(self, download_id: str, download_url: str, display_name: str) -> None:
        """Background worker: hand the URL to the active adapter,
        poll until done, then walk the resulting directory."""
        adapter = get_active_torrent_adapter()
        if adapter is None or not adapter.is_configured():
            self._mark_error(download_id, "No torrent client configured")
            return

        try:
            torrent_hash = run_async(adapter.add_torrent(download_url))
        except Exception as e:
            self._mark_error(download_id, f"add_torrent failed: {e}")
            return
        if not torrent_hash:
            self._mark_error(download_id, "Torrent client refused the URL")
            return

        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['torrent_hash'] = torrent_hash
                row['state'] = 'InProgress, Downloading'

        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        last_save_path: Optional[str] = None
        while time.monotonic() < deadline:
            if self.shutdown_check and self.shutdown_check():
                return
            try:
                status = run_async(adapter.get_status(torrent_hash))
            except Exception as e:
                logger.warning("Torrent poll error for %s: %s", torrent_hash, e)
                status = None

            if status is None:
                # Adapter forgot about the torrent — probably user-removed.
                self._mark_error(download_id, "Torrent disappeared from client")
                return

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

            if status.state in _COMPLETE_STATES:
                self._finalize_download(download_id, last_save_path)
                return
            if status.state == 'error':
                self._mark_error(download_id, status.error or "Torrent client reported error")
                return

            time.sleep(_POLL_INTERVAL_SECONDS)

        self._mark_error(download_id, "Torrent download timed out")

    def _finalize_download(self, download_id: str, save_path: Optional[str]) -> None:
        """Adapter said complete. Walk the directory + pick the
        first audio file as the canonical ``file_path``."""
        if not save_path:
            self._mark_error(download_id, "Torrent completed but no save_path reported")
            return
        try:
            audio_files = collect_audio_after_extraction(Path(save_path))
        except Exception as e:
            self._mark_error(download_id, f"Post-extract walk failed: {e}")
            return
        if not audio_files:
            self._mark_error(download_id, f"No audio files found in {save_path}")
            return
        primary = audio_files[0]
        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['state'] = 'Completed, Succeeded'
                row['progress'] = 100.0
                row['file_path'] = str(primary)
        logger.info("Torrent download complete: %s -> %s (%d audio files)",
                    download_id[:8], primary.name, len(audio_files))

    def _mark_error(self, download_id: str, message: str) -> None:
        logger.error("Torrent download %s failed: %s", download_id[:8], message)
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
        adapter = get_active_torrent_adapter()
        with self._lock:
            row = self.active_downloads.get(download_id)
            torrent_hash = row.get('torrent_hash') if row else None
        if adapter and torrent_hash:
            try:
                await adapter.remove(torrent_hash, delete_files=remove)
            except Exception as e:
                logger.warning("Torrent cancel via adapter failed: %s", e)
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


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions — easy to unit-test)
# ---------------------------------------------------------------------------


def _decode_filename(filename: str) -> Tuple[Optional[str], str]:
    """Pull the encoded download URL out of the ``filename`` string.
    Returns ``(url, display_name)``. ``url`` is None when the string
    has no separator."""
    if not filename or _FILENAME_SEP not in filename:
        return (None, filename or '')
    url, display = filename.split(_FILENAME_SEP, 1)
    return (url, display)


def _guess_quality_from_title(title: str) -> str:
    """Read the quality hint from a release title — most music
    torrents put the encoding right in the name (FLAC, MP3 320,
    etc.). Falls back to ``'mp3'`` so quality_score doesn't crash."""
    if not title:
        return 'mp3'
    lower = title.lower()
    if 'flac' in lower:
        return 'flac'
    if re.search(r'\b24[\s-]?bit\b', lower) or 'hi-?res' in lower:
        return 'flac'
    if 'aac' in lower:
        return 'aac'
    if 'ogg' in lower:
        return 'ogg'
    return 'mp3'


def _parse_indexer_id_filter() -> List[int]:
    """Read the comma-separated indexer-ID allowlist from config.
    Empty list = search every enabled indexer."""
    raw = (config_manager.get('prowlarr.indexer_ids', '') or '').strip()
    if not raw:
        return []
    out: List[int] = []
    for chunk in raw.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            continue
    return out


def _adapter_state_to_display(state: str) -> str:
    """Translate the adapter-uniform state strings into the
    ``'InProgress, Downloading'`` / ``'Completed, Succeeded'``
    style the existing UI expects (matches Soulseek + Lidarr)."""
    mapping = {
        'queued':      'Queued',
        'downloading': 'InProgress, Downloading',
        'stalled':     'InProgress, Stalled',
        'seeding':     'Completed, Succeeded',
        'completed':   'Completed, Succeeded',
        'paused':      'Paused',
        'error':       'Completed, Errored',
    }
    return mapping.get(state, state.title())


def _row_to_status(row: Dict[str, Any]) -> DownloadStatus:
    return DownloadStatus(
        id=row['id'],
        filename=row['filename'],
        username=row['username'],
        state=row.get('state', 'Unknown'),
        progress=float(row.get('progress', 0.0)),
        size=int(row.get('size', 0)),
        transferred=int(row.get('transferred', 0)),
        speed=int(row.get('speed', 0)),
        time_remaining=None,
        file_path=row.get('file_path'),
    )
