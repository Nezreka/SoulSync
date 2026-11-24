"""
Lidarr Download Client
Download source using Lidarr's API for Usenet/torrent downloads.

This client provides:
- Album search via Lidarr's metadata lookup
- Download triggering via Lidarr's indexer/download client pipeline
- Progress monitoring via Lidarr's queue API
- Drop-in replacement compatible with Soulseek interface

Requires a running Lidarr instance with configured indexers and download clients.
Lidarr downloads full albums — SoulSync imports only the tracks it needs.
"""

import os
import re
import time
import uuid
import shutil
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path

import requests as http_requests

from utils.logging_config import get_logger
from core.settings import config_manager
from core.async_utils import run_blocking

# Import Soulseek data structures for drop-in replacement compatibility
from core.download_plugins.types import TrackResult, AlbumResult, DownloadStatus

logger = get_logger("lidarr_client")


from core.download_plugins.base import DownloadSourcePlugin


def _parse_time(value) -> Optional[datetime]:
    """A Lidarr timestamp, to the second (Lidarr stores a file's dateAdded to
    the second, a command's start to the millisecond), or None."""
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.replace(microsecond=0)


class LidarrDownloadClient(DownloadSourcePlugin):
    """Lidarr download client — uses Lidarr as a download source for Usenet/torrent content.

    Implements the same interface as SoulseekClient, QobuzClient, TidalDownloadClient
    for seamless integration with the download orchestrator.
    """

    def __init__(self, download_path: str = None):
        if download_path is None:
            download_path = (config_manager.get('lidarr_download.download_path', '')
                             or config_manager.get('soulseek.download_path', './downloads'))
        self.download_path = Path(download_path)
        try:
            self.download_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"Could not verify download path {self.download_path}: {e}")

        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._download_lock = threading.Lock()
        # One lock per album: the tracks of one album run in parallel workers,
        # and each used to add the same artist and album at the same time.
        self._album_locks: Dict[str, threading.Lock] = {}
        self._album_locks_guard = threading.Lock()
        # foreignAlbumId -> monotonic time of the last AlbumSearch we sent, so
        # parallel tracks of one album trigger one indexer search, not one each
        self._album_searched_at: Dict[str, float] = {}
        # foreignAlbumId -> album-bundle runs waiting on it right now. they are
        # not in active_downloads, and the cleanup must not delete an album
        # another run is still waiting on
        self._bundle_waits: Dict[str, int] = {}
        # (monotonic time, Lidarr's artist list): see _library_artists
        self._artist_list_cache = None
        self.shutdown_check = None
        self._load_config()

    def _load_config(self):
        self._url = (config_manager.get('lidarr_download.url', '') or '').rstrip('/')
        self._api_key = config_manager.get('lidarr_download.api_key', '') or ''
        self._root_folder = config_manager.get('lidarr_download.root_folder', '') or ''
        self._quality_profile = config_manager.get('lidarr_download.quality_profile', 'Any') or 'Any'
        # Lidarr owns the library when it imports every download: removing
        # its album after staging would make it forget the files it keeps
        self._owns_library = config_manager.get('lidarr_download.import_through_lidarr', False) is True
        self._cleanup = (config_manager.get('lidarr_download.cleanup_after_import', True)
                         and not self._owns_library)
        try:
            self._download_timeout = int(config_manager.get('lidarr_download.download_timeout', 21600) or 21600)
        except (TypeError, ValueError):
            self._download_timeout = 21600
        try:
            self._stall_timeout = int(config_manager.get('lidarr_download.stall_timeout', 1800) or 0)
        except (TypeError, ValueError):
            self._stall_timeout = 1800

    @staticmethod
    def own_download_path() -> str:
        """lidarr_download.download_path when set: the folder the orchestrator
        must not replace with Soulseek's on a settings reload."""
        return config_manager.get('lidarr_download.download_path', '') or ''

    def set_shutdown_check(self, check_callable):
        self.shutdown_check = check_callable

    def reload_settings(self):
        self._load_config()
        self._artist_list_cache = None
        logger.info("Lidarr settings reloaded")

    # ==================== Interface Methods ====================

    def is_configured(self) -> bool:
        return bool(self._url and self._api_key)

    def is_available(self) -> bool:
        return self.is_configured()

    async def check_connection(self) -> bool:
        if not self.is_configured():
            return False
        try:
            return await run_blocking(self._check_connection_sync)
        except Exception:
            return False

    def _check_connection_sync(self) -> bool:
        try:
            data = self._api_get('system/status')
            return data is not None and 'version' in data
        except Exception as e:
            logger.error(f"Lidarr connection check failed: {e}")
            return False

    async def search(self, query: str, timeout: int = None,
                     progress_callback=None) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Search Lidarr for albums matching the query.

        Returns individual tracks from matched albums as TrackResult objects,
        plus AlbumResult objects for album-level matching.
        """
        if not self.is_configured():
            return ([], [])

        try:
            return await run_blocking(self._search_sync, query)
        except Exception as e:
            logger.error(f"Lidarr search failed: {e}")
            return ([], [])

    def _search_sync(self, query: str) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Synchronous search implementation."""
        try:
            # album/lookup is a release-group search: a query that carries the
            # track title often leaves the wanted album out entirely. When the
            # task knows its album, look that up first (by MusicBrainz id when
            # known, else by artist + album) and keep the text query after it.
            albums_data = self._hinted_album_lookup()
            seen = {a.get('foreignAlbumId') for a in albums_data}
            for album in self._api_get('album/lookup', params={'term': query}) or []:
                if album.get('foreignAlbumId') not in seen:
                    albums_data.append(album)
                    seen.add(album.get('foreignAlbumId'))
            if not albums_data:
                return ([], [])

            track_results = []
            album_results = []

            for album in albums_data[:10]:  # Limit to 10 albums
                album_title = album.get('title', '')
                artist_data = album.get('artist', {})
                artist_name = artist_data.get('artistName', '')
                foreign_album_id = album.get('foreignAlbumId', '')
                release_date = album.get('releaseDate', '')
                year = release_date[:4] if release_date and len(release_date) >= 4 else ''

                # Get tracks from the album's releases
                releases = album.get('releases', [])
                tracks_in_album = []

                for release in releases:
                    media_list = release.get('media', [])
                    for media in media_list:
                        for track in media.get('tracks', []):
                            track_title = track.get('title', '')
                            track_number = track.get('trackNumber', 0) or track.get('absoluteTrackNumber', 0)
                            duration_ms = (track.get('duration', '') or 0)
                            if isinstance(duration_ms, str):
                                # Lidarr returns duration as "HH:MM:SS" or milliseconds
                                try:
                                    duration_ms = int(duration_ms)
                                except ValueError:
                                    duration_ms = 0

                            # Encode album info in filename for later retrieval
                            display = f"{artist_name} - {album_title} - {track_title}"
                            filename = f"{foreign_album_id}||{display}"

                            tr = TrackResult(
                                username='lidarr',
                                filename=filename,
                                size=0,
                                bitrate=1411,  # Assume lossless
                                duration=duration_ms,
                                quality='flac',
                                free_upload_slots=999,
                                upload_speed=999999,
                                queue_length=0,
                                artist=artist_name,
                                title=track_title,
                                album=album_title,
                                track_number=track_number,
                            )
                            track_results.append(tr)
                            tracks_in_album.append(tr)

                # If no track-level data, create album-level entry
                if not tracks_in_album:
                    display = f"{artist_name} - {album_title}"
                    filename = f"{foreign_album_id}||{display}"
                    tr = TrackResult(
                        username='lidarr',
                        filename=filename,
                        size=0,
                        bitrate=1411,
                        duration=0,
                        quality='flac',
                        free_upload_slots=999,
                        upload_speed=999999,
                        queue_length=0,
                        artist=artist_name,
                        title=album_title,
                        album=album_title,
                        track_number=None,
                    )
                    track_results.append(tr)

                # Build AlbumResult
                if tracks_in_album:
                    ar = AlbumResult(
                        username='lidarr',
                        album_path=f"lidarr/{foreign_album_id}",
                        album_title=album_title,
                        artist=artist_name,
                        track_count=len(tracks_in_album),
                        total_size=0,
                        tracks=tracks_in_album,
                        dominant_quality='flac',
                        year=year,
                    )
                    album_results.append(ar)

            logger.info(f"Lidarr search '{query}': {len(track_results)} tracks, {len(album_results)} albums")
            return (track_results, album_results)

        except Exception as e:
            logger.error(f"Lidarr search error: {e}")
            return ([], [])

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        """Trigger a download via Lidarr.

        Extracts the album ID from the filename (id||display pattern),
        adds the album to Lidarr if needed, and triggers a search.
        """
        if not self.is_configured():
            return None

        download_id = str(uuid.uuid4())

        # Parse album ID from filename
        album_foreign_id = ''
        display_name = filename
        if '||' in filename:
            parts = filename.split('||', 1)
            album_foreign_id = parts[0]
            display_name = parts[1]

        with self._download_lock:
            self.active_downloads[download_id] = {
                'id': download_id,
                'filename': filename,
                'display_name': display_name,
                'username': 'lidarr',
                'state': 'Initializing',
                'progress': 0.0,
                'file_path': None,
                'album_foreign_id': album_foreign_id,
            }

        # Start background download thread
        thread = threading.Thread(
            target=self._download_thread_worker,
            args=(download_id, album_foreign_id, display_name),
            daemon=True,
            name=f'lidarr-dl-{download_id[:8]}',
        )
        thread.start()
        return download_id

    def _download_thread_worker(self, download_id: str, album_foreign_id: str, display_name: str):
        """Background worker that manages the Lidarr download lifecycle."""
        try:
            # Step 1: Look up the album in Lidarr
            with self._download_lock:
                self.active_downloads[download_id]['state'] = 'Initializing'

            album_data = self._api_get('album/lookup', params={'term': f'lidarr:{album_foreign_id}'})
            if not album_data:
                # Try searching by the display name
                album_data = self._api_get('album/lookup', params={'term': display_name})

            if not album_data:
                self._set_error(download_id, 'Album not found in Lidarr')
                return

            album = album_data[0] if isinstance(album_data, list) else album_data

            # Steps 2-3: make sure Lidarr has the artist and the album, and
            # that one search for the album is running.
            lidarr_album_id, error = self._ensure_album_searched(album)
            if not lidarr_album_id:
                self._set_error(download_id, error or 'Failed to add album to Lidarr')
                return

            with self._download_lock:
                self.active_downloads[download_id]['state'] = 'InProgress, Downloading'
                self.active_downloads[download_id]['progress'] = 5.0

            # Step 4: wait until Lidarr has imported the album.
            def _progress(pct):
                with self._download_lock:
                    if download_id in self.active_downloads:
                        self.active_downloads[download_id]['progress'] = min(pct, 95.0)

            def _cancelled():
                with self._download_lock:
                    dl = self.active_downloads.get(download_id)
                    return dl is None or dl['state'] == 'Cancelled'

            outcome = self._wait_for_album(lidarr_album_id, album.get('foreignAlbumId', ''),
                                           on_progress=_progress, cancelled=_cancelled)
            if outcome == 'cancelled':
                return
            if outcome != 'complete':
                self._set_error(download_id, self._WAIT_ERRORS.get(outcome, outcome))
                return

            # Step 5: Find and import the wanted track.
            #
            # Lidarr grabs whole albums; SoulSync's matched-context
            # post-processing wants the SPECIFIC track the user
            # requested. Old behavior copied every track in the album
            # and reported `imported_files[0]` as `file_path` — which
            # almost always pointed to track 1, not the user's actual
            # track. Post-processing then tagged track 1 with the
            # requested track's metadata. Misfiling guaranteed.
            #
            # New behavior: identify the wanted track by title (parsed
            # from display_name), look up its trackFile via Lidarr's
            # `track` API, copy ONLY that file. For album-level
            # dispatches (no specific track in display_name), fall back
            # to copying the first imported file so existing
            # album-grab UX still works.
            with self._download_lock:
                self.active_downloads[download_id]['progress'] = 96.0

            try:
                wanted_title = self._extract_wanted_track_title(display_name)
                wanted_src = self._pick_track_file_for_wanted(lidarr_album_id, wanted_title)

                if wanted_src:
                    # Copy ONLY the matched track. Other album files stay
                    # in Lidarr's root folder and will be cleaned up by
                    # the cleanup step (Step 6) when configured.
                    dst_path = os.path.join(str(self.download_path),
                                            os.path.basename(wanted_src))
                    try:
                        shutil.copy2(wanted_src, dst_path)
                    except Exception as e:
                        self._set_error(download_id, f'Failed to copy wanted track: {e}')
                        return

                    with self._download_lock:
                        self.active_downloads[download_id]['state'] = 'Completed, Succeeded'
                        self.active_downloads[download_id]['progress'] = 100.0
                        self.active_downloads[download_id]['file_path'] = dst_path
                    logger.info(
                        f"Lidarr download complete: {display_name} "
                        f"-> {os.path.basename(dst_path)}"
                    )
                else:
                    # No specific track wanted (album dispatch) OR fuzzy
                    # match failed. Fall back to copying the first imported
                    # file so something always lands on disk; album-level
                    # callers still get a usable file_path.
                    track_files = self._api_get('trackfile', params={'albumId': lidarr_album_id})
                    if not track_files:
                        self._set_error(download_id, 'No files found after download')
                        return

                    imported_files = []
                    for tf in track_files:
                        src_path = tf.get('path', '')
                        if src_path and os.path.exists(src_path):
                            dst_path = os.path.join(str(self.download_path),
                                                    os.path.basename(src_path))
                            try:
                                shutil.copy2(src_path, dst_path)
                                imported_files.append(dst_path)
                            except Exception as e:
                                logger.warning(f"Failed to copy {src_path}: {e}")

                    if imported_files:
                        with self._download_lock:
                            self.active_downloads[download_id]['state'] = 'Completed, Succeeded'
                            self.active_downloads[download_id]['progress'] = 100.0
                            self.active_downloads[download_id]['file_path'] = imported_files[0]
                        if wanted_title:
                            logger.warning(
                                f"Lidarr: wanted track '{wanted_title}' not matched in album "
                                f"— falling back to first imported file ({len(imported_files)} total)"
                            )
                        else:
                            logger.info(
                                f"Lidarr album-level download complete: {display_name} "
                                f"({len(imported_files)} files)"
                            )
                    else:
                        self._set_error(download_id, 'Failed to import files')

            except Exception as e:
                self._set_error(download_id, f'Import failed: {e}')
                return

            # Step 6: Cleanup — remove from Lidarr if configured. Another
            # track of the same album may still be waiting on this album id,
            # so leave it until the last one is done.
            if self._cleanup and not self._album_in_use(album_foreign_id, download_id):
                try:
                    self._api_delete(f'album/{lidarr_album_id}', params={'deleteFiles': 'false'})
                    logger.debug(f"Cleaned up album {lidarr_album_id} from Lidarr")
                except Exception as e:
                    logger.debug("Lidarr album cleanup failed: %s", e)

        except Exception as e:
            logger.error(f"Lidarr download thread failed: {e}")
            self._set_error(download_id, str(e))

    # ==================== Album flow ====================

    # How long an album may sit with no download in Lidarr's queue before we
    # decide Lidarr found nothing to grab. Indexer searches (Soulseek ones
    # especially) can take minutes.
    _GRAB_WINDOW_SECONDS = 600
    _SEARCH_DEDUP_SECONDS = 900
    # a Lidarr restart or a slow answer is not "the album was deleted": only
    # this many failed album reads in a row are
    _GONE_AFTER_MISSES = 15
    # queue states that need a person in Lidarr, so waiting on them would just
    # hold the batch until the timeout
    _STUCK_IMPORT_STATES = frozenset(('importfailed', 'importblocked'))
    _WAIT_ERRORS = {
        'not_grabbed': 'Lidarr found no release to grab',
        'failed': 'Lidarr download failed',
        'timeout': 'Download timed out',
        'stalled': "Lidarr's download made no progress",
        'gone': 'Album was removed from Lidarr',
        'import_failed': "Lidarr could not import the album (see Activity in Lidarr)",
        'shutdown': 'Server shutting down',
    }

    def download_album_to_staging(
        self,
        album_name: str,
        artist_name: str,
        staging_dir: str,
        progress_callback=None,
        release_group_id: str = '',
        release_year: str = '',
        release_type: str = '',
        **_kwargs,
    ) -> Dict[str, Any]:
        """Album-bundle flow: have Lidarr fetch the whole album, then copy its
        imported files into ``staging_dir`` for the per-track staging matcher.

        One lookup (artist + album), one add and one search per album, where
        the per-track flow ran a lookup, an add and a search for every track.

        A miss is fallback-eligible only in hybrid mode: in Lidarr-only mode the
        per-track flow would just ask Lidarr again, once per track.
        """
        result: Dict[str, Any] = {'success': False, 'files': [], 'error': None}
        fallback = self._has_fallback_source()

        def _emit(state: str, **extra) -> None:
            if progress_callback:
                try:
                    progress_callback({'state': state, **extra})
                except Exception as cb_exc:
                    logger.debug("[Lidarr album] progress callback failed: %s", cb_exc)

        def _fail(error: str, can_fall_back: bool = fallback) -> Dict[str, Any]:
            result['error'] = error
            result['fallback'] = can_fall_back
            # the other album sources end on 'failed' too, so the batch row
            # does not sit on the last 'downloading' state
            _emit('failed', release=f"{artist_name} - {album_name}".strip(' -'))
            return result

        if not self.is_configured():
            return _fail('Lidarr is not configured', True)

        _emit('searching', query=f"{artist_name} {album_name}".strip())
        album = self._find_album(album_name, artist_name, release_group_id, release_year,
                                 release_type=release_type)
        if not album:
            return _fail(f"Lidarr has no album '{album_name}' by '{artist_name}'")

        lidarr_album_id, error = self._ensure_album_searched(album)
        if not lidarr_album_id:
            return _fail(error or 'Failed to add album to Lidarr')

        release = f"{(album.get('artist') or {}).get('artistName', artist_name)} - {album.get('title', album_name)}"
        foreign_album_id = album.get('foreignAlbumId', '')
        _emit('downloading', release=release, progress=0)
        with self._download_lock:
            self._bundle_waits[foreign_album_id] = self._bundle_waits.get(foreign_album_id, 0) + 1
        try:
            outcome = self._wait_for_album(
                lidarr_album_id, foreign_album_id,
                on_progress=lambda pct: _emit('downloading', release=release, progress=round(pct, 1)),
            )
            if outcome != 'complete':
                if fallback and outcome not in ('shutdown', 'cancelled', 'gone'):
                    self._release_album(lidarr_album_id, foreign_album_id, outcome)
                return _fail(self._WAIT_ERRORS.get(outcome, outcome))

            _emit('staging', release=release)
            stats = (self._api_get(f'album/{lidarr_album_id}') or {}).get('statistics') or {}
            track_files = self._api_get('trackfile', params={'albumId': lidarr_album_id}) or []
            sources = [Path(tf['path']) for tf in track_files
                       if tf.get('path') and os.path.isfile(tf['path'])]
            # atomic and collision-safe: two discs can both have a
            # "01 - Intro.flac", and a half-copied file must never sit in
            # staging under its real name
            from core.download_plugins.album_bundle import copy_audio_files_atomically
            files = copy_audio_files_atomically(sources, Path(staging_dir)) if sources else []
            if not files:
                return _fail('Lidarr imported the album but its files are not readable here')
        finally:
            with self._download_lock:
                left = self._bundle_waits.get(foreign_album_id, 1) - 1
                if left > 0:
                    self._bundle_waits[foreign_album_id] = left
                else:
                    self._bundle_waits.pop(foreign_album_id, None)

        expected = stats.get('trackCount') or stats.get('totalTrackCount') or len(files)
        if self._cleanup and not self._album_in_use(foreign_album_id):
            self._api_delete(f'album/{lidarr_album_id}', params={'deleteFiles': 'false'})

        logger.info(f"[Lidarr album] staged {len(files)} files for {release}")
        result.update({
            'success': True,
            'files': files,
            'expected_count': expected,
            'completed_count': len(files),
            'partial': len(files) < expected,
        })
        return result

    def _has_fallback_source(self) -> bool:
        mode = (config_manager.get('download_source.mode', '') or '').lower()
        if mode != 'hybrid':
            return False
        order = config_manager.get('download_source.hybrid_order', []) or []
        return any(str(s).lower() != 'lidarr' for s in order)

    def _find_album(self, album_name: str, artist_name: str,
                    release_group_id: str = '', release_year: str = '',
                    release_type: str = '') -> Optional[Dict[str, Any]]:
        """The Lidarr record for this album, or None.

        By MusicBrainz release-group id when known. Otherwise by artist and
        title: first in Lidarr's own library, then in its metadata search (by
        artist + album, then by album alone: long terms sometimes get a 503
        from Lidarr's metadata proxy). A candidate must match both artist and
        title, since a near miss would grab someone else's album, and
        ``release_year`` tells same-titled albums apart (a band's self-titled
        records), ``release_type`` an album from its same-titled single. None
        sends the batch to the next source.
        """
        if release_group_id:
            found = self._lookup(f'lidarr:{release_group_id}')
            if found:
                return found[0]

        if not album_name or not artist_name:
            return None
        library = self._pick_album(self._library_albums(artist_name), album_name, artist_name,
                                   release_year, trust_lone_candidate=False,
                                   release_type=release_type)
        if library:
            return library
        for term in (f"{artist_name} {album_name}", album_name):
            best = self._pick_album(self._lookup(term)[:20], album_name, artist_name, release_year,
                                    release_type=release_type)
            if best:
                return best
        return None

    # an edition note on one side only: Spotify says "Album (Deluxe Edition)",
    # the MusicBrainz release group Lidarr finds says "Album". Only edition
    # words go; "(Live)" or "(Remixes)" is a different record and stays.
    _EDITION_NOTE = re.compile(
        r'\s*(?:[\(\[][^\)\]]*\b(?:deluxe|expanded|edition|remaster(?:ed)?|anniversary|bonus'
        r'|special|collector\'?s|reissue)\b[^\)\]]*[\)\]]'
        r'|\s-\s(?:\d{4}\s)?remaster(?:ed)?(?:\s\d{4})?(?:\sversion)?)\s*$',
        re.IGNORECASE,
    )

    @classmethod
    def _album_title_score(cls, wanted: str, candidate: str) -> float:
        score = cls._name_score(wanted, candidate)
        bare_wanted = cls._EDITION_NOTE.sub('', wanted or '').strip()
        bare_candidate = cls._EDITION_NOTE.sub('', candidate or '').strip()
        if (bare_wanted, bare_candidate) != (wanted, candidate):
            score = max(score, cls._name_score(bare_wanted, bare_candidate))
        return score

    def _lookup(self, term: str) -> List[Dict[str, Any]]:
        """Lidarr's metadata search, asked twice: its proxy fails transiently."""
        for attempt in range(2):
            found = self._api_get('album/lookup', params={'term': term})
            if found is not None:
                return found if isinstance(found, list) else [found]
            if attempt == 0:
                time.sleep(2)
        return []

    def _library_albums(self, artist_name: str) -> List[Dict[str, Any]]:
        """Albums Lidarr already has by this artist.

        Lidarr's metadata search ranks by text relevance, so a short or common
        artist name ("PISS") buries the album under other artists' records;
        Lidarr's own library has no such noise.
        """
        albums = []
        for artist in self._library_artists():
            if self._name_score(artist_name, artist.get('artistName', '')) >= 0.8:
                for album in self._api_get('album', params={'artistId': artist.get('id')}) or []:
                    album.setdefault('artist', artist)
                    albums.append(album)
        return albums

    _ARTIST_LIST_TTL = 60

    def _library_artists(self) -> List[Dict[str, Any]]:
        """Every artist in Lidarr, kept for a minute: the per-track flow asks
        once per track, and a big library answers in megabytes."""
        cached = self._artist_list_cache
        if cached and time.monotonic() - cached[0] < self._ARTIST_LIST_TTL:
            return cached[1]
        artists = self._api_get('artist')
        if not isinstance(artists, list):
            return []
        self._artist_list_cache = (time.monotonic(), artists)
        return artists

    # spotify/deezer album types and the lidarr (musicbrainz) types they cover.
    # spotify calls an ep a single, so those two go together.
    _TYPE_FITS = {
        'album': {'album'}, 'compilation': {'album'},
        'single': {'single', 'ep'}, 'ep': {'ep', 'single'},
    }

    @staticmethod
    def _year_of(record: Dict[str, Any]) -> Optional[int]:
        try:
            return int(str(record.get('releaseDate') or '')[:4])
        except ValueError:
            return None

    def _pick_album(self, candidates, album_name: str, artist_name: str,
                    release_year: str = '', trust_lone_candidate: bool = True,
                    release_type: str = '') -> Optional[Dict[str, Any]]:
        """The best candidate matching artist and title.

        With a requested type, candidates of that type win when there are
        any (an album over its same-titled single). With a requested year, a
        candidate released within a year of it wins. When none is, a lone
        match is still taken (a reissue keeps its title but not its date),
        unless ``trust_lone_candidate`` is off: the library holds only the
        albums someone added, so its lone match may be another record with
        the same title.
        """
        matches = []
        for candidate in candidates or []:
            title_score = self._album_title_score(album_name, candidate.get('title', ''))
            artist_score = self._name_score(
                artist_name, (candidate.get('artist') or {}).get('artistName', ''))
            if title_score >= 0.8 and artist_score >= 0.8:
                matches.append((title_score + artist_score, candidate))
        if not matches:
            return None
        fits = self._TYPE_FITS.get(str(release_type or '').strip().lower())
        if fits:
            typed = [m for m in matches if str(m[1].get('albumType') or '').lower() in fits]
            if typed:
                matches = typed
        try:
            year = int(str(release_year or '')[:4])
        except ValueError:
            year = None
        if year:
            dated = [m for m in matches
                     if self._year_of(m[1]) is not None and abs(self._year_of(m[1]) - year) <= 1]
            if dated:
                matches = dated
            elif not trust_lone_candidate or len(matches) > 1:
                return None
        return max(matches, key=lambda m: m[0])[1]

    @classmethod
    def _name_score(cls, wanted: str, candidate: str) -> float:
        a, b = cls._normalize_for_match(wanted), cls._normalize_for_match(candidate)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        return SequenceMatcher(None, a, b).ratio()

    def _hinted_album_lookup(self) -> List[Dict[str, Any]]:
        """Lookup results for the album of the track being searched (from the
        download task's track hint), cached on the hint so the task's query
        ladder asks Lidarr once."""
        try:
            from core.downloads.track_hint import current_track_hint
            hint = current_track_hint()
        except Exception:
            return []
        if not hint:
            return []
        if '_lidarr_album' not in hint:
            album = self._find_album(
                str(hint.get('album') or '').strip(),
                str(hint.get('artist') or '').strip(),
                self._hint_release_group_id(hint),
                self._hint_release_year(hint),
                release_type=self._hint_release_type(hint),
            )
            hint['_lidarr_album'] = album
        return [hint['_lidarr_album']] if hint.get('_lidarr_album') else []

    @staticmethod
    def _hint_release_group_id(hint: Dict[str, Any]) -> str:
        info = ((hint.get('catalogue_context') or {}).get('track_info')) or {}
        album = info.get('album') if isinstance(info.get('album'), dict) else {}
        for source in (info, album):
            for key in ('musicbrainz_release_group_id', 'musicbrainz_releasegroup_id', 'release_group_mbid'):
                value = str(source.get(key) or '').strip()
                if value:
                    return value
        return ''

    @staticmethod
    def _hint_release_year(hint: Dict[str, Any]) -> str:
        info = ((hint.get('catalogue_context') or {}).get('track_info')) or {}
        album = info.get('album') if isinstance(info.get('album'), dict) else {}
        return str(album.get('release_date') or info.get('release_date') or '')[:4]

    @staticmethod
    def _hint_release_type(hint: Dict[str, Any]) -> str:
        info = ((hint.get('catalogue_context') or {}).get('track_info')) or {}
        album = info.get('album') if isinstance(info.get('album'), dict) else {}
        return str(album.get('album_type') or '').strip().lower()

    def _album_lock(self, foreign_album_id: str) -> threading.Lock:
        with self._album_locks_guard:
            return self._album_locks.setdefault(foreign_album_id or '', threading.Lock())

    def _album_in_use(self, foreign_album_id: str, exclude_download_id: str = '',
                      own_waits: int = 0) -> bool:
        """Whether a download or an album-bundle run still needs this album.
        ``own_waits``: bundle runs of the caller's own to leave out."""
        with self._download_lock:
            if self._bundle_waits.get(foreign_album_id, 0) > own_waits:
                return True
            return any(
                dl.get('album_foreign_id') == foreign_album_id
                and dl_id != exclude_download_id
                and dl.get('state') not in ('Completed, Succeeded', 'Errored', 'Cancelled')
                for dl_id, dl in self.active_downloads.items()
            )

    def _ensure_artist(self, artist_data: Dict[str, Any]) -> Optional[int]:
        """Lidarr id of the album's artist, adding the artist when missing."""
        if artist_data.get('id'):
            return artist_data['id']
        foreign_artist_id = artist_data.get('foreignArtistId', '')

        def _existing():
            if not foreign_artist_id:
                return None
            for a in self._api_get('artist', params={'mbId': foreign_artist_id}) or []:
                if a.get('foreignArtistId') == foreign_artist_id:
                    return a.get('id')
            return None

        artist_id = _existing()
        if artist_id:
            return artist_id
        try:
            added = self._api_post('artist', data={
                'foreignArtistId': foreign_artist_id,
                'artistName': artist_data.get('artistName', ''),
                'qualityProfileId': self._get_quality_profile_id(),
                'metadataProfileId': self._get_metadata_profile_id(),
                'rootFolderPath': self._get_root_folder(),
                'monitored': False,
                'addOptions': {'monitor': 'none', 'searchForMissingAlbums': False},
            })
        except Exception as e:
            logger.warning(f"Failed to add artist to Lidarr: {e}")
            added = None
        # A failed add usually means the artist already exists (added by a
        # previous run, or by hand in Lidarr): look it up again.
        return (added or {}).get('id') or _existing()

    def _existing_album(self, foreign_album_id: str) -> Optional[Dict[str, Any]]:
        found = self._api_get('album', params={'foreignAlbumId': foreign_album_id})
        if isinstance(found, list) and found:
            return found[0]
        return None

    def _ensure_album(self, album: Dict[str, Any], monitored: bool
                      ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Lidarr's record of this album, adding the artist and the album when
        missing. ``monitored`` True also monitors an album Lidarr already has
        (an album found by the artist refresh is unmonitored, and Lidarr's
        later RSS grabs and upgrades skip it); False leaves an existing album
        as it is and adds a missing one unmonitored. Call under the album's
        lock. Returns (album record, error).
        """
        foreign_album_id = album.get('foreignAlbumId', '')
        existing = self._existing_album(foreign_album_id)
        if not existing:
            artist_id = self._ensure_artist(album.get('artist') or {})
            if not artist_id:
                return None, 'Failed to add artist to Lidarr'
            # Adding the artist makes Lidarr refresh it, and that refresh
            # may create the album on its own; a POST then fails as a
            # duplicate. Give the refresh a moment, then add if still missing.
            for _ in range(10):
                existing = self._existing_album(foreign_album_id)
                if existing:
                    break
                time.sleep(1)
            if not existing:
                existing = self._api_post('album', data={
                    'foreignAlbumId': foreign_album_id,
                    'title': album.get('title', ''),
                    'artistId': artist_id,
                    'qualityProfileId': self._get_quality_profile_id(),
                    'rootFolderPath': self._get_root_folder(),
                    'monitored': monitored,
                    'addOptions': {'searchForNewAlbum': False},
                }) or self._existing_album(foreign_album_id)
            if not existing or not existing.get('id'):
                return None, 'Failed to add album to Lidarr'

        if monitored and not existing.get('monitored'):
            self._set_monitored(existing['id'], True)
        return existing, None

    def _set_monitored(self, lidarr_album_id: int, monitored: bool) -> None:
        try:
            url = f"{self._url}/api/v1/album/monitor"
            http_requests.put(url, headers={'X-Api-Key': self._api_key},
                              json={'albumIds': [lidarr_album_id], 'monitored': monitored}, timeout=15)
        except Exception as e:
            logger.debug(f"Lidarr album monitor failed: {e}")

    def _ensure_album_searched(self, album: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
        """Make sure Lidarr has this album, monitored, with a search running.

        Serialized per album: parallel tracks of one album share the add and
        the search. Returns (lidarr album id, error).
        """
        foreign_album_id = album.get('foreignAlbumId', '')
        if not foreign_album_id:
            return None, 'Album has no MusicBrainz id'
        with self._album_lock(foreign_album_id):
            existing, error = self._ensure_album(album, monitored=True)
            if not existing:
                return None, error
            lidarr_album_id = existing['id']

            has_files = ((existing.get('statistics') or {}).get('trackFileCount') or 0) > 0
            last = self._album_searched_at.get(foreign_album_id)
            recent = last is not None and time.monotonic() - last < self._SEARCH_DEDUP_SECONDS
            if not has_files and not recent and not self._queue_records(foreign_album_id):
                self._api_post('command', data={'name': 'AlbumSearch', 'albumIds': [lidarr_album_id]})
                self._album_searched_at[foreign_album_id] = time.monotonic()
            return lidarr_album_id, None

    def _queue_records(self, foreign_album_id: str) -> Optional[List[Dict[str, Any]]]:
        """This album's queue records, or None when the queue could not be
        read. None is "unknown", never "nothing queued": an empty answer lets
        the wait call a half-imported album complete."""
        queue = self._api_get('queue', params={'includeAlbum': 'true', 'pageSize': 1000})
        if not isinstance(queue, dict):
            return None
        return [r for r in (queue.get('records') or [])
                if (r.get('album') or {}).get('foreignAlbumId') == foreign_album_id]

    def _wait_for_album(self, lidarr_album_id: int, foreign_album_id: str,
                        on_progress=None, cancelled=None, poll_seconds: float = 2.0) -> str:
        """Poll Lidarr until the album is imported.

        Returns 'complete', 'not_grabbed' (nothing reached the queue within
        the grab window), 'stalled' (what Lidarr grabbed has not moved for
        ``lidarr_download.stall_timeout`` seconds: a torrent with no seeders
        sits in the queue until the deadline otherwise), 'failed', 'timeout',
        'gone', 'shutdown' or 'cancelled'. The overall deadline is long
        because a real download (a slow torrent, a Soulseek queue) can take
        hours; the grab window and the stall timeout are what catch "Lidarr
        found nothing" and "Lidarr found something dead".
        """
        start = time.monotonic()
        seen_in_queue = False
        album_misses = 0
        # queue record id -> bytes left at the last poll; any change (a new
        # grab, bytes arriving, a record leaving) counts as progress
        left_by_record: Dict[Any, int] = {}
        last_progress = start
        while True:
            if self.shutdown_check and self.shutdown_check():
                return 'shutdown'
            if cancelled and cancelled():
                return 'cancelled'
            now = time.monotonic()
            elapsed = now - start
            try:
                records = self._queue_records(foreign_album_id)
                if records is not None:
                    current = {r.get('id'): r.get('sizeleft', 0) or 0 for r in records}
                    if current != left_by_record:
                        left_by_record = current
                        last_progress = now
                    # only a download still fetching can stall. Judged by its
                    # state, not its bytes: a magnet that never got metadata
                    # reports size 0 and 0 bytes left, just like a finished
                    # download waiting on its import (the import states below
                    # judge that one)
                    if (any(self._still_fetching(r) for r in records) and self._stall_timeout > 0
                            and now - last_progress > self._stall_timeout):
                        return 'stalled'
                for item in records or []:
                    seen_in_queue = True
                    status = (item.get('status') or '').lower()
                    if status == 'failed':
                        return 'failed'
                    if (item.get('trackedDownloadState') or '').lower() in self._STUCK_IMPORT_STATES:
                        return 'import_failed'
                    size = max(item.get('size', 0) or 0, 1)
                    left = item.get('sizeleft', 0) or 0
                    if on_progress:
                        on_progress(max(0.0, 100.0 - left / size * 100))
                album_check = self._api_get(f'album/{lidarr_album_id}')
                if album_check is None:
                    album_misses += 1
                    if album_misses >= self._GONE_AFTER_MISSES and elapsed > 30:
                        return 'gone'
                else:
                    album_misses = 0
                files = ((album_check or {}).get('statistics') or {}).get('trackFileCount', 0) or 0
                # Lidarr imports file by file: wait for the queue to let go of
                # the album before calling it complete. An unreadable queue
                # (None) says nothing either way.
                if files > 0 and records is not None and not records:
                    return 'complete'
            except Exception as e:
                logger.debug(f"Lidarr poll error: {e}")
            if not seen_in_queue and elapsed > self._GRAB_WINDOW_SECONDS:
                return 'not_grabbed'
            if elapsed > self._download_timeout:
                return 'timeout'
            time.sleep(poll_seconds)

    # queue states where Lidarr is not waiting on the download client: the
    # download is done and the import runs, or a delay profile holds the
    # release on purpose
    _NOT_FETCHING_STATES = frozenset(('importpending', 'importing', 'imported'))
    _NOT_FETCHING_STATUSES = frozenset(('completed', 'delay'))

    @classmethod
    def _still_fetching(cls, record: Dict[str, Any]) -> bool:
        return ((record.get('status') or '').lower() not in cls._NOT_FETCHING_STATUSES
                and (record.get('trackedDownloadState') or '').lower() not in cls._NOT_FETCHING_STATES)

    def _release_album(self, lidarr_album_id: int, foreign_album_id: str, outcome: str) -> None:
        """Take Lidarr off an album the batch hands to the next source.

        Left alone, both deliver it: a stalled torrent can still finish, and a
        monitored album is grabbed again on Lidarr's next RSS sync. The grab
        that stalled or ran out of time is removed from the client and
        blocklisted, so Lidarr's next search picks another release. With
        ``import_through_lidarr`` the album stays monitored: the next source's
        files go to Lidarr too, and Lidarr keeps one copy of each track.
        Otherwise it is unmonitored, and SoulSync's copy is the only one.

        Skipped while another run or download still needs this album. Called
        from inside the caller's own bundle wait, which is left out.
        """
        if self._album_in_use(foreign_album_id, own_waits=1):
            return
        removed = 0
        if outcome in ('stalled', 'timeout'):
            for record in self._queue_records(foreign_album_id) or []:
                if record.get('id') is not None and self._api_delete(
                        f"queue/{record['id']}",
                        params={'removeFromClient': 'true', 'blocklist': 'true'}):
                    removed += 1
        if not self._owns_library:
            self._set_monitored(lidarr_album_id, False)
        logger.info("[Lidarr album] released album %s to the next source (%s): "
                    "%d grab(s) removed, %s", lidarr_album_id, outcome, removed,
                    'still monitored' if self._owns_library else 'unmonitored')

    def import_into_library(self, paths: List[str], album_name: str, artist_name: str,
                            release_group_id: str = '', release_year: str = '',
                            release_type: str = '', monitored: bool = False) -> Dict[str, Any]:
        """Have Lidarr import files another source downloaded.

        For ``import_through_lidarr``: Lidarr is the only writer of its root
        folder, so a file SoulSync got elsewhere reaches the library through
        Lidarr's Manual Import, with Lidarr's naming and its rules on what an
        upgrade is. Lidarr identifies each file itself; a file it rejects (not
        an upgrade of the track it has, no close-enough match) stays where it
        is. Copies, so SoulSync's own file and its database rows stay valid.
        The album is added when Lidarr does not have it, monitored only when
        ``monitored`` (a whole album; a playlist's one track must not make
        Lidarr fetch the rest of its album).

        ``paths`` must be readable by Lidarr at the same path. Returns
        ``{'imported': n, 'rejected': {path: reason}, 'error': str or None}``.
        """
        result: Dict[str, Any] = {'imported': 0, 'rejected': {}, 'error': None}
        wanted = {os.path.normpath(p) for p in paths or [] if p}
        if not wanted:
            return result
        if not self.is_configured():
            result['error'] = 'Lidarr is not configured'
            return result
        album = self._find_album(album_name, artist_name, release_group_id, release_year,
                                 release_type=release_type)
        if not album or not album.get('foreignAlbumId'):
            result['error'] = f"Lidarr has no album '{album_name}' by '{artist_name}'"
            return result
        with self._album_lock(album['foreignAlbumId']):
            record, error = self._ensure_album(album, monitored=monitored)
        if not record:
            result['error'] = error
            return result
        artist_id = record.get('artistId') or (record.get('artist') or {}).get('id')
        return self.import_files(wanted, artist_id=artist_id, result=result)

    # how long a Manual Import may run before its outcome is read anyway
    _IMPORT_WAIT_SECONDS = 1800
    _COMMAND_DONE = frozenset(('completed', 'failed', 'aborted', 'cancelled', 'orphaned'))

    def import_files(self, paths, artist_id: Optional[int] = None,
                     result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Manual Import ``paths`` as Lidarr identifies them, and read back
        what landed.

        Manual Import lists a folder, so the paths are grouped by folder; the
        files Lidarr matched without a rejection are sent in one command, and
        once it has run every sent file is checked track by track: its tracks
        must have a file Lidarr added during the command (Lidarr can still
        turn one down while importing, and the command reports success all
        the same). Copies; the caller decides what happens to the source.

        Returns ``{'imported': n, 'paths': {source: path in Lidarr},
        'rejected': {source: reason}, 'error': str or None}``.
        """
        result = result if result is not None else {'imported': 0, 'rejected': {}, 'error': None}
        result.setdefault('paths', {})
        wanted = {os.path.normpath(p) for p in paths or [] if p}
        files, listed, sent = [], set(), {}
        for folder in sorted({os.path.dirname(p) for p in wanted}):
            params = {'folder': folder, 'filterExistingFiles': 'false'}
            if artist_id:
                params['artistId'] = artist_id
            items = self._api_get('manualimport', params=params)
            if not isinstance(items, list):
                result['error'] = f'Lidarr could not read {folder}'
                continue
            for item in items:
                path = os.path.normpath(item.get('path') or '')
                if path not in wanted:
                    continue
                listed.add(path)
                reasons = [r.get('reason') for r in item.get('rejections') or [] if r.get('reason')]
                album_id = (item.get('album') or {}).get('id')
                track_ids = [t['id'] for t in item.get('tracks') or [] if t.get('id')]
                if not reasons and not (album_id and track_ids and item.get('albumReleaseId')):
                    reasons = ['Lidarr could not match it to a track']
                if reasons:
                    result['rejected'][path] = '; '.join(reasons)
                    continue
                sent[path] = (album_id, track_ids)
                files.append({
                    'path': item['path'],
                    'artistId': (item.get('artist') or {}).get('id') or artist_id,
                    'albumId': album_id,
                    'albumReleaseId': item['albumReleaseId'],
                    'trackIds': track_ids,
                    'quality': item.get('quality'),
                    'indexerFlags': item.get('indexerFlags') or 0,
                    'disableReleaseSwitching': False,
                })
        for path in wanted - listed:
            result['rejected'].setdefault(path, 'Lidarr did not list the file')
        if not files:
            return result

        command = self._api_post('command', data={
            'name': 'ManualImport', 'files': files,
            'importMode': 'copy', 'replaceExistingFiles': False,
        })
        if not isinstance(command, dict) or not command.get('id'):
            result['error'] = 'Lidarr refused the import'
            return result
        done = self._wait_for_command(command['id'])
        if not done or done.get('status') != 'completed':
            # read back all the same: part of it may have landed
            result['error'] = f"Lidarr's import ended {(done or {}).get('status') or 'without an answer'}"
        since = _parse_time((done or command).get('started') or command.get('queued'))
        landed = self._landed_files({album_id for album_id, _ in sent.values()}, since)
        for path, (_album_id, track_ids) in sent.items():
            finals = [landed.get(track_id) for track_id in track_ids]
            if all(finals):
                result['paths'][path] = finals[0]
            else:
                result['rejected'][path] = 'Lidarr did not import it (see History in Lidarr)'
        result['imported'] = len(result['paths'])
        return result

    def _wait_for_command(self, command_id) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + self._IMPORT_WAIT_SECONDS
        while time.monotonic() < deadline:
            if self.shutdown_check and self.shutdown_check():
                return None
            state = self._api_get(f'command/{command_id}')
            if isinstance(state, dict) and (state.get('status') or '').lower() in self._COMMAND_DONE:
                return state
            time.sleep(3)
        return None

    def _landed_files(self, album_ids, since) -> Dict[int, str]:
        """track id -> path of its file, for files Lidarr added at or after
        ``since`` (a time on Lidarr's own clock; None accepts any file)."""
        landed: Dict[int, str] = {}
        for album_id in album_ids:
            files = {tf.get('id'): tf for tf in self._api_get('trackfile', params={'albumId': album_id}) or []}
            for track in self._api_get('track', params={'albumId': album_id}) or []:
                track_file = files.get(track.get('trackFileId'))
                if not track_file or not track_file.get('path'):
                    continue
                added = _parse_time(track_file.get('dateAdded'))
                if since is None or (added is not None and added >= since):
                    landed[track.get('id')] = track_file['path']
        return landed

    def _set_error(self, download_id: str, error: str):
        with self._download_lock:
            if download_id in self.active_downloads:
                self.active_downloads[download_id]['state'] = 'Errored'
                self.active_downloads[download_id]['error'] = error
        logger.error(f"Lidarr download error: {error}")

    @staticmethod
    def _extract_wanted_track_title(display_name: str) -> str:
        """Pull the track title out of the dispatch display string.

        ``_search_sync`` builds two display shapes:
        - Track dispatch: ``f"{artist} - {album} - {track_title}"``
        - Album dispatch: ``f"{artist} - {album}"``

        Need >=3 parts to confidently identify a track. 2-part strings
        are album-level dispatches — return empty so the caller falls
        back to copying the first file (correct behavior for "give me
        the whole album"). Track titles that themselves contain ``' - '``
        (e.g. live versions) get rejoined from parts[2:].
        """
        if not display_name:
            return ''
        parts = display_name.split(' - ')
        if len(parts) < 3:
            return ''
        return ' - '.join(parts[2:]).strip()

    def _pick_track_file_for_wanted(self, lidarr_album_id: int,
                                    wanted_title: str) -> Optional[str]:
        """Find the on-disk path of the imported file matching the wanted track.

        Walks Lidarr's `track` API to map track titles → trackFileIds,
        then resolves the trackFileId to a path via `trackfile`. Returns
        None when the album has no usable wanted-track match (caller
        falls back to the first imported file in that case so
        album-level dispatches still work).
        """
        if not wanted_title:
            return None

        tracks = self._api_get('track', params={'albumId': lidarr_album_id})
        if not tracks or not isinstance(tracks, list):
            return None

        # Normalize for case-insensitive fuzzy match. Lidarr's track titles
        # come from MusicBrainz so they're usually canonical, but
        # punctuation / casing varies.
        wanted_norm = self._normalize_for_match(wanted_title)
        best_track_file_id: Optional[int] = None
        best_score = 0.0
        for t in tracks:
            track_title = t.get('title', '') or ''
            track_file_id = t.get('trackFileId')
            if not track_file_id:
                continue
            score = self._title_similarity(wanted_norm,
                                            self._normalize_for_match(track_title))
            if score > best_score:
                best_score = score
                best_track_file_id = track_file_id

        # 0.7 threshold avoids picking the wrong track when none match
        # well — caller falls back to first-imported behavior in that case.
        if best_score < 0.7 or best_track_file_id is None:
            return None

        # Resolve trackFileId → path. /trackfile/{id} returns one record.
        tf = self._api_get(f'trackfile/{best_track_file_id}')
        if not tf:
            return None
        path = tf.get('path', '')
        if path and os.path.exists(path):
            return path
        return None

    @staticmethod
    def _normalize_for_match(s: str) -> str:
        """Lower + strip punctuation + collapse whitespace for fuzzy compare."""
        if not s:
            return ''
        cleaned = re.sub(r'[^\w\s]', '', s.lower())
        return ' '.join(cleaned.split())

    @staticmethod
    def _title_similarity(a: str, b: str) -> float:
        """Cheap title similarity: equal → 1.0, substring → 0.85,
        token overlap ratio otherwise. Avoids pulling SequenceMatcher
        for every comparison since this runs in the hot download path."""
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if a in b or b in a:
            return 0.85
        a_tokens = set(a.split())
        b_tokens = set(b.split())
        if not a_tokens or not b_tokens:
            return 0.0
        intersection = a_tokens & b_tokens
        union = a_tokens | b_tokens
        return len(intersection) / len(union) if union else 0.0

    async def get_all_downloads(self) -> List[DownloadStatus]:
        with self._download_lock:
            return [self._to_status(dl) for dl in self.active_downloads.values()]

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        with self._download_lock:
            dl = self.active_downloads.get(download_id)
            return self._to_status(dl) if dl else None

    def _to_status(self, dl: Dict) -> DownloadStatus:
        filename = dl['filename']
        # Append error info to filename for UI visibility when errored
        if dl['state'] == 'Errored' and dl.get('error'):
            filename = f"{filename} — {dl['error']}"
        return DownloadStatus(
            id=dl['id'],
            filename=filename,
            username='lidarr',
            state=dl['state'],
            progress=dl['progress'],
            size=0,
            transferred=0,
            speed=0,
            file_path=dl.get('file_path'),
        )

    async def cancel_download(self, download_id: str, username: str = None,
                              remove: bool = False) -> bool:
        with self._download_lock:
            if download_id in self.active_downloads:
                if remove:
                    del self.active_downloads[download_id]
                else:
                    self.active_downloads[download_id]['state'] = 'Cancelled'
                return True
        return False

    async def clear_all_completed_downloads(self) -> bool:
        with self._download_lock:
            self.active_downloads = {
                k: v for k, v in self.active_downloads.items()
                if v['state'] not in ('Completed, Succeeded', 'Errored', 'Cancelled')
            }
        return True

    # ==================== Lidarr API Helpers ====================

    def _api_get(self, endpoint: str, params: dict = None) -> Optional[Any]:
        try:
            url = f"{self._url}/api/v1/{endpoint}"
            headers = {'X-Api-Key': self._api_key}
            resp = http_requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug(f"Lidarr API GET {endpoint} failed: {e}")
            return None

    def _api_post(self, endpoint: str, data: dict = None) -> Optional[Any]:
        try:
            url = f"{self._url}/api/v1/{endpoint}"
            headers = {'X-Api-Key': self._api_key, 'Content-Type': 'application/json'}
            resp = http_requests.post(url, headers=headers, json=data, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug(f"Lidarr API POST {endpoint} failed: {e}")
            return None

    def _api_delete(self, endpoint: str, params: dict = None) -> bool:
        try:
            url = f"{self._url}/api/v1/{endpoint}"
            headers = {'X-Api-Key': self._api_key}
            resp = http_requests.delete(url, headers=headers, params=params, timeout=15)
            return resp.ok
        except Exception:
            return False

    def _get_root_folder(self) -> str:
        if self._root_folder:
            return self._root_folder
        # Fetch from Lidarr
        folders = self._api_get('rootfolder')
        if folders and isinstance(folders, list) and len(folders) > 0:
            return folders[0].get('path', '/music')
        return '/music'

    def _get_quality_profile_id(self) -> int:
        profiles = self._api_get('qualityprofile')
        if not profiles:
            return 1
        # Find matching profile by name, or use first
        for p in profiles:
            if p.get('name', '').lower() == self._quality_profile.lower():
                return p['id']
        return profiles[0].get('id', 1) if profiles else 1

    def _get_metadata_profile_id(self) -> int:
        """Resolve a usable metadataProfileId for adding artists.

        Lidarr requires `metadataProfileId` when creating artist records.
        The default profile is usually id=1, but on installs where the
        user deleted/recreated profiles, that id may not exist — leading
        to the API rejecting the artist-add with a 400. Fetch live to
        pick whatever's actually configured. Falls back to 1 only when
        the API call fails entirely (preserves previous behavior so this
        change can't make things worse).
        """
        profiles = self._api_get('metadataprofile')
        if profiles and isinstance(profiles, list):
            for p in profiles:
                pid = p.get('id')
                if isinstance(pid, int):
                    return pid
        return 1
