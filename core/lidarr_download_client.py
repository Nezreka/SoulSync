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


class LidarrDownloadClient(DownloadSourcePlugin):
    """Lidarr download client — uses Lidarr as a download source for Usenet/torrent content.

    Implements the same interface as SoulseekClient, QobuzClient, TidalDownloadClient
    for seamless integration with the download orchestrator.
    """

    def __init__(self, download_path: str = None):
        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')
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
        self.shutdown_check = None
        self._load_config()

    def _load_config(self):
        self._url = (config_manager.get('lidarr_download.url', '') or '').rstrip('/')
        self._api_key = config_manager.get('lidarr_download.api_key', '') or ''
        self._root_folder = config_manager.get('lidarr_download.root_folder', '') or ''
        self._quality_profile = config_manager.get('lidarr_download.quality_profile', 'Any') or 'Any'
        self._cleanup = config_manager.get('lidarr_download.cleanup_after_import', True)
        # Whole albums only: Lidarr serves album downloads (the album-bundle
        # flow) and answers no per-track search, so single tracks go to the
        # next source in hybrid mode.
        self._albums_only = bool(config_manager.get('lidarr_download.albums_only', False))
        try:
            self._download_timeout = int(config_manager.get('lidarr_download.download_timeout', 21600) or 21600)
        except (TypeError, ValueError):
            self._download_timeout = 21600

    def set_shutdown_check(self, check_callable):
        self.shutdown_check = check_callable

    def reload_settings(self):
        self._load_config()
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
        if self._albums_only:
            # Whole albums only: per-track searches are left to other sources.
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
    _WAIT_ERRORS = {
        'not_grabbed': 'Lidarr found no release to grab',
        'failed': 'Lidarr download failed',
        'timeout': 'Download timed out',
        'gone': 'Album was removed from Lidarr',
        'shutdown': 'Server shutting down',
    }

    def download_album_to_staging(
        self,
        album_name: str,
        artist_name: str,
        staging_dir: str,
        progress_callback=None,
        release_group_id: str = '',
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
            return result

        if not self.is_configured():
            return _fail('Lidarr is not configured', True)

        _emit('searching', query=f"{artist_name} {album_name}".strip())
        album = self._find_album(album_name, artist_name, release_group_id)
        if not album:
            return _fail(f"Lidarr has no album '{album_name}' by '{artist_name}'")

        lidarr_album_id, error = self._ensure_album_searched(album)
        if not lidarr_album_id:
            return _fail(error or 'Failed to add album to Lidarr')

        release = f"{(album.get('artist') or {}).get('artistName', artist_name)} - {album.get('title', album_name)}"
        _emit('downloading', release=release, progress=0)
        outcome = self._wait_for_album(
            lidarr_album_id, album.get('foreignAlbumId', ''),
            on_progress=lambda pct: _emit('downloading', release=release, progress=round(pct, 1)),
        )
        if outcome != 'complete':
            return _fail(self._WAIT_ERRORS.get(outcome, outcome))

        _emit('staging', release=release)
        stats = (self._api_get(f'album/{lidarr_album_id}') or {}).get('statistics') or {}
        track_files = self._api_get('trackfile', params={'albumId': lidarr_album_id}) or []
        os.makedirs(staging_dir, exist_ok=True)
        files = []
        for tf in track_files:
            src = tf.get('path', '')
            if not src or not os.path.exists(src):
                continue
            dst = os.path.join(staging_dir, os.path.basename(src))
            shutil.copy2(src, dst)  # an OSError here is fallback-eligible upstream
            files.append(dst)
        if not files:
            return _fail('Lidarr imported the album but its files are not readable here')

        expected = stats.get('trackCount') or stats.get('totalTrackCount') or len(files)
        if self._cleanup and not self._album_in_use(album.get('foreignAlbumId', '')):
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
                    release_group_id: str = '') -> Optional[Dict[str, Any]]:
        """The Lidarr lookup record for this album, or None.

        By MusicBrainz release-group id when known. Otherwise by artist +
        album, then by album alone (long terms sometimes get a 503 from
        Lidarr's metadata proxy), accepting only a result whose artist and
        title both match: a near miss would grab someone else's album.
        """
        if release_group_id:
            found = self._api_get('album/lookup', params={'term': f'lidarr:{release_group_id}'})
            if found:
                return found[0] if isinstance(found, list) else found

        if not album_name or not artist_name:
            return None
        for term in (f"{artist_name} {album_name}", album_name):
            best, best_score = None, 0.0
            for candidate in (self._api_get('album/lookup', params={'term': term}) or [])[:20]:
                title_score = self._name_score(album_name, candidate.get('title', ''))
                artist_score = self._name_score(
                    artist_name, (candidate.get('artist') or {}).get('artistName', ''))
                if title_score < 0.8 or artist_score < 0.8:
                    continue
                score = title_score + artist_score
                if score > best_score:
                    best, best_score = candidate, score
            if best:
                return best
        return None

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

    def _album_lock(self, foreign_album_id: str) -> threading.Lock:
        with self._album_locks_guard:
            return self._album_locks.setdefault(foreign_album_id or '', threading.Lock())

    def _album_in_use(self, foreign_album_id: str, exclude_download_id: str = '') -> bool:
        with self._download_lock:
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

    def _ensure_album_searched(self, album: Dict[str, Any]) -> Tuple[Optional[int], Optional[str]]:
        """Make sure Lidarr has this album, monitored, with a search running.

        Serialized per album: parallel tracks of one album share the add and
        the search. Returns (lidarr album id, error).
        """
        foreign_album_id = album.get('foreignAlbumId', '')
        if not foreign_album_id:
            return None, 'Album has no MusicBrainz id'
        with self._album_lock(foreign_album_id):
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
                        'monitored': True,
                        'addOptions': {'searchForNewAlbum': False},
                    }) or self._existing_album(foreign_album_id)
                if not existing or not existing.get('id'):
                    return None, 'Failed to add album to Lidarr'

            lidarr_album_id = existing['id']
            if not existing.get('monitored'):
                # an album found by the artist refresh is unmonitored, and
                # Lidarr's later RSS grabs and upgrades skip it
                try:
                    url = f"{self._url}/api/v1/album/monitor"
                    http_requests.put(url, headers={'X-Api-Key': self._api_key},
                                      json={'albumIds': [lidarr_album_id], 'monitored': True}, timeout=15)
                except Exception as e:
                    logger.debug(f"Lidarr album monitor failed: {e}")

            has_files = ((existing.get('statistics') or {}).get('trackFileCount') or 0) > 0
            last = self._album_searched_at.get(foreign_album_id)
            recent = last is not None and time.monotonic() - last < self._SEARCH_DEDUP_SECONDS
            if not has_files and not recent and not self._queue_records(foreign_album_id):
                self._api_post('command', data={'name': 'AlbumSearch', 'albumIds': [lidarr_album_id]})
                self._album_searched_at[foreign_album_id] = time.monotonic()
            return lidarr_album_id, None

    def _queue_records(self, foreign_album_id: str) -> List[Dict[str, Any]]:
        queue = self._api_get('queue', params={'includeAlbum': 'true', 'pageSize': 200})
        records = (queue or {}).get('records') or [] if isinstance(queue, dict) else []
        return [r for r in records
                if (r.get('album') or {}).get('foreignAlbumId') == foreign_album_id]

    def _wait_for_album(self, lidarr_album_id: int, foreign_album_id: str,
                        on_progress=None, cancelled=None, poll_seconds: float = 2.0) -> str:
        """Poll Lidarr until the album is imported.

        Returns 'complete', 'not_grabbed' (nothing reached the queue within
        the grab window), 'failed', 'timeout', 'gone', 'shutdown' or
        'cancelled'. The overall deadline is long because a real download
        (a slow torrent, a Soulseek queue) can take hours; the short grab
        window is what catches "Lidarr found nothing".
        """
        start = time.monotonic()
        seen_in_queue = False
        while True:
            if self.shutdown_check and self.shutdown_check():
                return 'shutdown'
            if cancelled and cancelled():
                return 'cancelled'
            elapsed = time.monotonic() - start
            try:
                records = self._queue_records(foreign_album_id)
                for item in records:
                    seen_in_queue = True
                    status = (item.get('status') or '').lower()
                    if status == 'failed':
                        return 'failed'
                    size = max(item.get('size', 0) or 0, 1)
                    left = item.get('sizeleft', 0) or 0
                    if on_progress:
                        on_progress(max(0.0, 100.0 - left / size * 100))
                album_check = self._api_get(f'album/{lidarr_album_id}')
                if album_check is None and elapsed > 30:
                    return 'gone'
                files = ((album_check or {}).get('statistics') or {}).get('trackFileCount', 0) or 0
                # Lidarr imports file by file: wait for the queue to let go of
                # the album before calling it complete.
                if files > 0 and not records:
                    return 'complete'
            except Exception as e:
                logger.debug(f"Lidarr poll error: {e}")
            if not seen_in_queue and elapsed > self._GRAB_WINDOW_SECONDS:
                return 'not_grabbed'
            if elapsed > self._download_timeout:
                return 'timeout'
            time.sleep(poll_seconds)

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
