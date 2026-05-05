"""
SoundCloud Download Client
Alternative music download source using yt-dlp's SoundCloud extractor.

This client provides:
- SoundCloud search via the `scsearch` extractor
- Anonymous public-track downloads (no auth required)
- Drop-in replacement compatible with the existing TidalDownloadClient /
  QobuzClient / HiFiClient / DeezerDownloadClient interface

The client is intentionally NOT wired into web_server.py, settings UI, or
the unified search dispatch. Build/test in isolation first; integration
ships in a follow-up PR once the client is verified end-to-end.

Quality reality check:
- Anonymous SoundCloud serves 128 kbps MP3 for most public tracks. A few
  uploaders flag tracks for 256 kbps AAC streaming via SoundCloud Go+, but
  those require an authenticated session; we only fetch the publicly
  available transcoding.
- No FLAC. SoundCloud doesn't expose lossless to anyone, ever.
- Many tracks (especially DJ mixes) are >60 minutes long. Downloads can
  be large; the integrity check still applies downstream.
"""

import os
import re
import asyncio
import uuid
import time
from typing import List, Optional, Dict, Any, Tuple, Callable
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

from utils.logging_config import get_logger
from config.settings import config_manager

# Standard data structures shared across all download clients so downstream
# matching/post-processing stays source-agnostic.
from core.soulseek_client import TrackResult, AlbumResult, DownloadStatus

logger = get_logger("soundcloud_client")


# Quality tiers — SoundCloud anonymous access only really delivers one
# quality, but we keep the structure consistent with other clients so
# UI/settings can reference a familiar shape later.
QUALITY_MAP = {
    'standard': {
        'label': 'MP3 128kbps',
        'extension': 'mp3',
        'bitrate': 128,
        'codec': 'mp3',
    },
}

# Hard limit on yt-dlp result count per search to keep search latency bounded.
DEFAULT_SEARCH_LIMIT = 25
MAX_SEARCH_LIMIT = 50

# Shorthand for `scsearch<N>:<query>` — yt-dlp's SoundCloud search prefix.
# Returns up to N tracks ranked by SoundCloud's own relevance.
_SC_SEARCH_PREFIX = "scsearch"

# Minimum acceptable download size — anything below is almost certainly a
# broken response or a "preview snippet" file. Real SoundCloud audio for
# even a 1-minute track exceeds 100KB at 128kbps.
_MIN_AUDIO_SIZE_BYTES = 100 * 1024

# Filesystem-safe replacement for the platform's reserved characters.
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_filename(name: str) -> str:
    """Replace reserved filesystem characters with underscores."""
    cleaned = _UNSAFE_FILENAME_CHARS.sub('_', name)
    # Collapse runs of underscores so we don't produce "track______name".
    cleaned = re.sub(r'_{2,}', '_', cleaned).strip(' ._')
    return cleaned or 'soundcloud_track'


from core.download_plugins.base import DownloadSourcePlugin


class SoundcloudClient(DownloadSourcePlugin):
    """SoundCloud download client built on yt-dlp's SoundCloud extractor.

    Mirrors the public surface of TidalDownloadClient / QobuzClient so the
    eventual integration step is a wiring change, not a refactor.
    """

    def __init__(self, download_path: Optional[str] = None):
        if yt_dlp is None:
            logger.warning("yt-dlp not installed — SoundCloud downloads unavailable")

        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')

        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"SoundCloud client using download path: {self.download_path}")

        # Optional shutdown predicate — wired by the runtime to short-circuit
        # in-flight downloads when the worker is shutting down.
        self.shutdown_check: Optional[Callable[[], bool]] = None

        self._engine = None

    # ------------------------------------------------------------------
    # Lifecycle / availability
    # ------------------------------------------------------------------

    def set_engine(self, engine):
        """Engine callback — wires the central thread worker + state store."""
        self._engine = engine

    def set_shutdown_check(self, check_callable: Optional[Callable[[], bool]]) -> None:
        self.shutdown_check = check_callable

    def is_available(self) -> bool:
        """True when yt-dlp is installed. Anonymous SoundCloud needs no auth."""
        return yt_dlp is not None

    def is_configured(self) -> bool:
        """True if the client has everything it needs to operate.

        Anonymous-only for now — if yt-dlp is present, we're configured.
        Future tier-2 OAuth would gate on stored credentials here.
        """
        return self.is_available()

    def is_authenticated(self) -> bool:
        """Anonymous-only client — always False until OAuth tier ships."""
        return False

    async def check_connection(self) -> bool:
        """Run a tiny SoundCloud query to verify the network path works."""
        if not self.is_available():
            return False

        try:
            tracks, _albums = await self.search("test", timeout=15)
            return True
        except Exception as exc:
            logger.warning(f"SoundCloud connection check failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        timeout: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
    ) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Search SoundCloud for the given query.

        Returns (tracks, albums). SoundCloud has no album concept (only
        playlists, which don't map to the album model the rest of SoulSync
        expects), so the album list is always empty.
        """
        if not self.is_available():
            logger.warning("SoundCloud not available for search (yt-dlp missing)")
            return ([], [])

        if not query or not isinstance(query, str):
            logger.warning(f"Invalid SoundCloud search query: {query!r}")
            return ([], [])

        # SoundCloud or a transient yt-dlp parse can fail; the caller still
        # gets an empty list, never a raised exception.
        limit = min(MAX_SEARCH_LIMIT, max(1, DEFAULT_SEARCH_LIMIT))
        search_url = f"{_SC_SEARCH_PREFIX}{limit}:{query}"

        logger.info(f"Searching SoundCloud for: {query} (limit={limit})")

        loop = asyncio.get_event_loop()
        try:
            entries = await loop.run_in_executor(None, self._extract_search_entries, search_url)
        except Exception as exc:
            logger.error(f"SoundCloud search failed: {exc}")
            return ([], [])

        if not entries:
            logger.info(f"No SoundCloud results for: {query}")
            return ([], [])

        track_results: List[TrackResult] = []
        for entry in entries:
            try:
                converted = self._sc_to_track_result(entry)
                if converted is not None:
                    track_results.append(converted)
            except Exception as exc:
                logger.debug(f"Skipping SoundCloud entry conversion error: {exc}")

        logger.info(f"Found {len(track_results)} SoundCloud tracks for '{query}'")
        return (track_results, [])

    def _extract_search_entries(self, search_url: str) -> List[Dict[str, Any]]:
        """Run yt-dlp in flat-extract mode to get a quick list of search hits.

        Flat extraction skips per-entry HTTP roundtrips during search, so
        results come back in roughly the time of one SoundCloud API call.
        Per-entry resolution happens later, at download time.
        """
        opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': True,
            'noplaylist': False,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(search_url, download=False)
            if not info or not isinstance(info, dict):
                return []
            entries = info.get('entries') or []
            return [e for e in entries if isinstance(e, dict)]

    def _sc_to_track_result(self, entry: Dict[str, Any]) -> Optional[TrackResult]:
        """Convert a yt-dlp SoundCloud entry into the standard TrackResult.

        Returns None when the entry lacks a usable URL — the worker can't
        download it later anyway, so dropping it from search results saves
        the user a confused "click → fail" interaction.
        """
        url = entry.get('url') or entry.get('webpage_url')
        if not url:
            return None

        # yt-dlp's flat-extract entry has `id`, `title`, `uploader`, and
        # sometimes `duration`. Other fields (artist, album) are usually
        # only present after a full extraction.
        title = (entry.get('title') or '').strip()
        uploader = (entry.get('uploader') or entry.get('uploader_id') or '').strip()

        # Many SoundCloud titles are formatted "Artist - Title" by the
        # uploader. If we don't have a separate artist field, try to peel
        # one off the title; fall back to the uploader otherwise.
        artist, parsed_title = self._split_artist_from_title(title, uploader)

        duration_seconds = entry.get('duration')
        duration_ms: Optional[int] = None
        if isinstance(duration_seconds, (int, float)) and duration_seconds > 0:
            duration_ms = int(duration_seconds * 1000)

        sc_track_id = str(entry.get('id') or '')
        if not sc_track_id:
            # No stable id → can't pass through the filename-based dispatch.
            return None

        display_name = f"{artist} - {parsed_title}".strip(' -') or parsed_title or sc_track_id
        # ``filename`` is the dispatch key downstream code uses to identify
        # the download. We cram the SoundCloud URL into it so the download
        # worker has everything it needs without re-querying SoundCloud.
        filename = f"{sc_track_id}||{url}||{display_name}"

        track_result = TrackResult(
            username='soundcloud',
            filename=filename,
            size=0,
            bitrate=128,                # Anonymous SoundCloud cap
            duration=duration_ms,
            quality='mp3',
            free_upload_slots=999,
            upload_speed=999_999,
            queue_length=0,
            artist=artist or None,
            title=parsed_title or None,
            album=None,
            track_number=None,
            _source_metadata={
                'source': 'soundcloud',
                'track_id': sc_track_id,
                'permalink_url': url,
                'uploader': uploader or None,
                'duration_seconds': duration_seconds,
            },
        )
        return track_result

    @staticmethod
    def _split_artist_from_title(title: str, uploader: str) -> Tuple[str, str]:
        """Best-effort parse of "Artist - Title" out of a SoundCloud title.

        SoundCloud uploaders frequently format their tracks as
        ``"Artist Name - Track Title"``. When that pattern is present, we
        use it. Otherwise the uploader's display name is the artist and
        the whole title stays as the title.

        This is best-effort — the matching logic downstream still has the
        original title in `_source_metadata` and can fall back to fuzzy
        comparison if our split was wrong.
        """
        if not title:
            return (uploader, '')

        # Match the FIRST " - " (most common separator). Avoid em-dash etc
        # for now; uploaders use plain hyphen 95%+ of the time.
        if ' - ' in title:
            artist_part, _sep, title_part = title.partition(' - ')
            artist_part = artist_part.strip()
            title_part = title_part.strip()
            # Sanity: very short artist parts (< 2 chars) are usually
            # punctuation noise, not real names.
            if len(artist_part) >= 2 and title_part:
                return (artist_part, title_part)

        return (uploader, title)

    # ------------------------------------------------------------------
    # Download orchestration
    # ------------------------------------------------------------------

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        """Kick off a SoundCloud download via engine.worker."""
        parts = filename.split('||', 2)
        if len(parts) < 2:
            logger.error(f"Invalid SoundCloud filename format: {filename}")
            return None

        sc_track_id = parts[0]
        permalink_url = parts[1]
        display_name = parts[2] if len(parts) > 2 else sc_track_id

        if not sc_track_id or not permalink_url:
            logger.error(f"Missing SoundCloud track id or url in: {filename}")
            return None
        if self._engine is None:
            logger.error("SoundCloud client has no engine reference — cannot dispatch download")
            return None

        logger.info(f"Starting SoundCloud download: {display_name}")

        # Worker passes (download_id, target_id, display_name) to impl;
        # SoundCloud's _download_sync wants permalink_url (not track_id),
        # so adapt by closing over permalink_url here.
        def _impl(download_id, _target_id, _display_name):
            return self._download_sync(download_id, permalink_url, display_name)

        return self._engine.worker.dispatch(
            source_name='soundcloud',
            target_id=sc_track_id,
            display_name=display_name,
            original_filename=filename,
            impl_callable=_impl,
            extra_record_fields={
                'track_id': sc_track_id,
                'permalink_url': permalink_url,
                'display_name': display_name,
            },
        )

    def _download_sync(self, download_id: str, permalink_url: str,
                       display_name: str) -> Optional[str]:
        """Synchronously download a single SoundCloud track via yt-dlp.

        Returns the absolute path to the saved file, or None on failure.
        Handles the shutdown_check via a per-progress yt-dlp hook so a
        long DJ mix can still be interrupted mid-download.
        """
        if not self.is_available():
            logger.error("SoundCloud download attempted with yt-dlp unavailable")
            return None

        safe_name = _sanitize_filename(display_name)
        # yt-dlp resolves the actual extension at download time (almost
        # always .mp3 for anonymous SoundCloud). The %(ext)s placeholder
        # lets it pick.
        out_template = str(self.download_path / f"{safe_name}.%(ext)s")
        speed_start = time.time()

        def _progress_hook(progress: Dict[str, Any]) -> None:
            if self.shutdown_check and self.shutdown_check():
                # yt-dlp catches DownloadError and treats other exceptions
                # as fatal — raise something it'll surface as a clean abort.
                raise yt_dlp.utils.DownloadError("Shutdown requested")

            status = progress.get('status')
            if status == 'downloading':
                downloaded = int(progress.get('downloaded_bytes') or 0)
                total = int(progress.get('total_bytes') or progress.get('total_bytes_estimate') or 0)

                # SoundCloud serves HLS-segmented audio. yt-dlp doesn't know
                # the final byte total upfront — `total_bytes` and
                # `total_bytes_estimate` reflect the CURRENT FRAGMENT size,
                # not the whole download, so a byte-based percentage stays
                # near 0 until the very end. Fall back to fragment progress
                # which yt-dlp DOES populate accurately for HLS.
                fragment_index = progress.get('fragment_index')
                fragment_count = progress.get('fragment_count')
                if (fragment_index is not None and fragment_count
                        and fragment_count > 0):
                    self._update_download_progress_fragmented(
                        download_id, downloaded, fragment_index, fragment_count, speed_start,
                    )
                else:
                    self._update_download_progress(download_id, downloaded, total, speed_start)
            elif status == 'finished':
                # yt-dlp signals 'finished' once the bytes are on disk; the
                # final size is authoritative. Mark progress at 99% — the
                # outer thread flips to 100% / Completed once we return.
                downloaded = int(progress.get('total_bytes') or progress.get('downloaded_bytes') or 0)
                self._update_download_progress(download_id, downloaded, downloaded, speed_start)

        opts = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'outtmpl': out_template,
            'progress_hooks': [_progress_hook],
            'format': 'bestaudio/best',
            # Disable yt-dlp's own retry storm — surface failures fast so
            # the worker decides whether to retry from another source.
            'retries': 1,
            'fragment_retries': 1,
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(permalink_url, download=True)
        except Exception as exc:
            # Cover yt_dlp.utils.DownloadError + everything else.
            logger.warning(f"SoundCloud download failed for '{display_name}': {exc}")
            return None

        if not isinstance(info, dict):
            logger.warning(f"SoundCloud yt-dlp returned no info dict for '{display_name}'")
            return None

        # yt-dlp's prepare_filename gives us the resolved on-disk path
        # honoring outtmpl + the actual extension it picked.
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                resolved_path = ydl.prepare_filename(info)
        except Exception as exc:
            logger.warning(f"Could not resolve final filename for '{display_name}': {exc}")
            return None

        if not resolved_path or not os.path.exists(resolved_path):
            logger.warning(f"SoundCloud download claimed success but file missing: {resolved_path}")
            return None

        try:
            final_size = os.path.getsize(resolved_path)
        except OSError:
            final_size = 0

        if final_size < _MIN_AUDIO_SIZE_BYTES:
            logger.warning(
                f"SoundCloud download too small ({final_size} bytes) for "
                f"'{display_name}' — likely a preview snippet, discarding"
            )
            try:
                os.remove(resolved_path)
            except OSError:
                pass
            return None

        logger.info(
            f"SoundCloud download complete: {resolved_path} "
            f"({final_size / (1024 * 1024):.1f} MB)"
        )
        return resolved_path

    def _update_download_progress_fragmented(self, download_id: str, downloaded: int,
                                             fragment_index: int, fragment_count: int,
                                             speed_start: float) -> None:
        """HLS-aware progress update — fragment_index / fragment_count
        gives an accurate signal even when each fragment's
        ``total_bytes`` only describes the current fragment."""
        if self._engine is None:
            return
        record = self._engine.get_record('soundcloud', download_id)
        if record is None:
            return

        now = time.time()
        elapsed = now - speed_start
        speed = int(downloaded / elapsed) if elapsed > 0 else 0

        progress = round(min((fragment_index / fragment_count) * 100, 99.9), 1) if fragment_count > 0 else 0.0

        # Estimate total size from per-fragment average.
        if fragment_index > 0 and downloaded > 0:
            est_total = int(downloaded * (fragment_count / fragment_index))
        else:
            est_total = downloaded

        time_remaining: Optional[int] = None
        remaining_fragments = max(0, fragment_count - fragment_index)
        if speed > 0 and remaining_fragments > 0 and fragment_index > 0:
            seconds_per_fragment = elapsed / fragment_index if fragment_index > 0 else 0
            time_remaining = int(remaining_fragments * seconds_per_fragment)

        self._engine.update_record('soundcloud', download_id, {
            'transferred': downloaded,
            'speed': speed,
            'progress': progress,
            'size': est_total,
            'time_remaining': time_remaining,
        })

    def _update_download_progress(self, download_id: str, downloaded: int,
                                  total: int, speed_start: float) -> None:
        """Byte-based progress update for non-HLS streams."""
        if self._engine is None:
            return
        record = self._engine.get_record('soundcloud', download_id)
        if record is None:
            return

        now = time.time()
        elapsed = now - speed_start
        speed = int(downloaded / elapsed) if elapsed > 0 else 0

        progress = record.get('progress', 0.0)
        if total > 0:
            progress = round(min((downloaded / total) * 100, 99.9), 1)

        time_remaining: Optional[int] = None
        if speed > 0 and total > 0:
            remaining = total - downloaded
            if remaining > 0:
                time_remaining = int(remaining / speed)

        self._engine.update_record('soundcloud', download_id, {
            'transferred': downloaded,
            'size': total,
            'speed': speed,
            'progress': progress,
            'time_remaining': time_remaining,
        })

    # ------------------------------------------------------------------
    # Status / cancellation
    # ------------------------------------------------------------------

    def _record_to_status(self, record: dict) -> DownloadStatus:
        return DownloadStatus(
            id=record['id'],
            filename=record['filename'],
            username=record['username'],
            state=record['state'],
            progress=record['progress'],
            size=record.get('size', 0),
            transferred=record.get('transferred', 0),
            speed=record.get('speed', 0),
            time_remaining=record.get('time_remaining'),
            file_path=record.get('file_path'),
        )

    async def get_all_downloads(self) -> List[DownloadStatus]:
        if self._engine is None:
            return []
        return [
            self._record_to_status(record)
            for record in self._engine.iter_records_for_source('soundcloud')
        ]

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        if self._engine is None:
            return None
        record = self._engine.get_record('soundcloud', download_id)
        return self._record_to_status(record) if record is not None else None

    async def cancel_download(self, download_id: str, username: Optional[str] = None,
                              remove: bool = False) -> bool:
        """Mark a download as cancelled. Co-operative — yt-dlp's
        progress hook checks shutdown_check on next callback."""
        if self._engine is None:
            return False
        if self._engine.get_record('soundcloud', download_id) is None:
            logger.warning(f"SoundCloud download {download_id} not found")
            return False
        self._engine.update_record('soundcloud', download_id, {'state': 'Cancelled'})
        logger.info(f"Marked SoundCloud download {download_id} as cancelled")
        if remove:
            self._engine.remove_record('soundcloud', download_id)
            logger.info(f"Removed SoundCloud download {download_id} from queue")
        return True

    async def clear_all_completed_downloads(self) -> bool:
        if self._engine is None:
            return True
        terminal = {'Completed, Succeeded', 'Cancelled', 'Errored', 'Aborted'}
        cleared = 0
        for record in list(self._engine.iter_records_for_source('soundcloud')):
            if record.get('state') in terminal:
                self._engine.remove_record('soundcloud', record['id'])
                cleared += 1
        logger.info(f"Cleared {cleared} completed SoundCloud downloads")
        return True
