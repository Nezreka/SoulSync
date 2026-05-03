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
import asyncio
import uuid
import shutil
import threading
from typing import List, Optional, Dict, Any, Tuple
from pathlib import Path

import requests as http_requests

from utils.logging_config import get_logger
from config.settings import config_manager

# Import Soulseek data structures for drop-in replacement compatibility
from core.soulseek_client import TrackResult, AlbumResult, DownloadStatus

logger = get_logger("lidarr_client")


class LidarrDownloadClient:
    """Lidarr download client — uses Lidarr as a download source for Usenet/torrent content.

    Implements the same interface as SoulseekClient, QobuzClient, TidalDownloadClient
    for seamless integration with the download orchestrator.
    """

    def __init__(self, download_path: str = None):
        if download_path is None:
            download_path = config_manager.get('soulseek.download_path', './downloads')
        self.download_path = Path(download_path)
        self.download_path.mkdir(parents=True, exist_ok=True)

        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._download_lock = threading.Lock()
        self.shutdown_check = None
        self._load_config()

    def _load_config(self):
        self._url = (config_manager.get('lidarr_download.url', '') or '').rstrip('/')
        self._api_key = config_manager.get('lidarr_download.api_key', '') or ''
        self._root_folder = config_manager.get('lidarr_download.root_folder', '') or ''
        self._quality_profile = config_manager.get('lidarr_download.quality_profile', 'Any') or 'Any'
        self._cleanup = config_manager.get('lidarr_download.cleanup_after_import', True)

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
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._check_connection_sync)
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
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._search_sync, query)
        except Exception as e:
            logger.error(f"Lidarr search failed: {e}")
            return ([], [])

    def _search_sync(self, query: str) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Synchronous search implementation."""
        try:
            # Search for albums
            albums_data = self._api_get('album/lookup', params={'term': query})
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
            artist_data = album.get('artist', {})

            # Step 2: Ensure artist exists in Lidarr
            artist_id = artist_data.get('id')
            if not artist_id:
                # Add artist to Lidarr
                try:
                    root_folder = self._get_root_folder()
                    quality_profile_id = self._get_quality_profile_id()

                    metadata_profile_id = self._get_metadata_profile_id()
                    add_artist = {
                        'foreignArtistId': artist_data.get('foreignArtistId', ''),
                        'artistName': artist_data.get('artistName', ''),
                        'qualityProfileId': quality_profile_id,
                        'metadataProfileId': metadata_profile_id,
                        'rootFolderPath': root_folder,
                        'monitored': False,
                        'addOptions': {'monitor': 'none', 'searchForMissingAlbums': False},
                    }
                    result = self._api_post('artist', data=add_artist)
                    artist_id = result.get('id') if result else None
                except Exception as e:
                    logger.warning(f"Failed to add artist to Lidarr: {e}")

            # Step 3: Add album and trigger search
            with self._download_lock:
                self.active_downloads[download_id]['state'] = 'InProgress, Downloading'
                self.active_downloads[download_id]['progress'] = 5.0

            try:
                root_folder = self._get_root_folder()
                quality_profile_id = self._get_quality_profile_id()

                add_album = {
                    'foreignAlbumId': album.get('foreignAlbumId', ''),
                    'title': album.get('title', ''),
                    'artistId': artist_id,
                    'qualityProfileId': quality_profile_id,
                    'rootFolderPath': root_folder,
                    'monitored': True,
                    'addOptions': {'searchForNewAlbum': True},
                }

                # Check if album already exists
                existing = self._api_get('album', params={'foreignAlbumId': album.get('foreignAlbumId', '')})
                if existing and isinstance(existing, list) and len(existing) > 0:
                    lidarr_album_id = existing[0].get('id')
                    # Trigger search for existing album
                    self._api_post('command', data={
                        'name': 'AlbumSearch',
                        'albumIds': [lidarr_album_id],
                    })
                else:
                    result = self._api_post('album', data=add_album)
                    lidarr_album_id = result.get('id') if result else None

                if not lidarr_album_id:
                    self._set_error(download_id, 'Failed to add album to Lidarr')
                    return

            except Exception as e:
                self._set_error(download_id, f'Failed to trigger download: {e}')
                return

            # Step 4: Poll until Lidarr reports the album has imported files.
            #
            # Old approach used `for/else` with `break` from the inner queue
            # loop, but inner-break only escaped the queue iteration — the
            # outer poll loop kept spinning even after we'd detected
            # completion. Replaced with an explicit `download_complete` flag
            # that breaks the OUTER loop once trackFileCount > 0.
            max_polls = 600  # 10 minutes max
            download_complete = False
            for poll in range(max_polls):
                if self.shutdown_check and self.shutdown_check():
                    self._set_error(download_id, 'Server shutting down')
                    return

                with self._download_lock:
                    if download_id not in self.active_downloads:
                        return
                    if self.active_downloads[download_id]['state'] == 'Cancelled':
                        return

                try:
                    queue = self._api_get('queue', params={'includeAlbum': 'true'})
                    if queue and 'records' in queue:
                        for item in queue['records']:
                            item_album = item.get('album', {})
                            if item_album.get('foreignAlbumId') == album.get('foreignAlbumId', ''):
                                # Surface progress while still downloading.
                                status = item.get('status', '').lower()
                                size_left = item.get('sizeleft', 0)
                                size_total = max(item.get('size', 1), 1)
                                progress = 100.0 - (size_left / size_total * 100)

                                with self._download_lock:
                                    self.active_downloads[download_id]['progress'] = min(progress, 95.0)

                                if status in ('failed', 'warning'):
                                    self._set_error(download_id, f'Lidarr download failed: {status}')
                                    return
                                # 'completed' / 'imported' in the queue is
                                # transient — Lidarr drops the item once
                                # import finishes. Don't break here; let the
                                # trackFileCount check below decide.

                    # Authoritative completion signal: album has imported
                    # files. Cheap to call (single GET on a known id) and
                    # works even when the queue record disappeared between
                    # polls.
                    if poll > 5:  # Give Lidarr a few seconds to start
                        album_check = self._api_get(f'album/{lidarr_album_id}')
                        if (album_check
                                and album_check.get('statistics', {}).get('trackFileCount', 0) > 0):
                            download_complete = True
                            break

                except Exception as e:
                    logger.debug(f"Queue poll error: {e}")

                time.sleep(1)

            if not download_complete:
                self._set_error(download_id, 'Download timed out')
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

            # Step 6: Cleanup — remove from Lidarr if configured
            if self._cleanup:
                try:
                    self._api_delete(f'album/{lidarr_album_id}', params={'deleteFiles': 'false'})
                    logger.debug(f"Cleaned up album {lidarr_album_id} from Lidarr")
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Lidarr download thread failed: {e}")
            self._set_error(download_id, str(e))

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
