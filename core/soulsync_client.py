"""SoulSync Standalone Library Client — filesystem-based media server replacement.

Implements the same interface as Plex/Jellyfin/Navidrome clients so the
DatabaseUpdateWorker can scan the Transfer folder directly without an
external media server. Reads audio file tags via Mutagen, groups by
artist/album folder structure, and returns compatible data objects.
"""

import hashlib
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from utils.logging_config import get_logger

logger = get_logger("soulsync_client")

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.wav', '.wma', '.aiff', '.aif', '.ape'}


def _stable_id(text: str) -> str:
    """Generate a stable integer-like ID from a string (for DB compatibility)."""
    return str(abs(int(hashlib.md5(text.encode('utf-8', errors='replace')).hexdigest(), 16)) % (10 ** 9))


def _read_tags(file_path: str) -> Dict[str, Any]:
    """Read audio tags from a file. Returns dict with title, artist, album, etc."""
    result = {
        'title': '', 'artist': '', 'album_artist': '', 'album': '',
        'track_number': 0, 'disc_number': 1, 'year': '',
        'genre': '', 'duration_ms': 0, 'bitrate': 0,
    }
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=True)
        if audio:
            if audio.tags:
                tags = audio.tags
                result['title'] = (tags.get('title', [''])[0] or '').strip()
                result['artist'] = (tags.get('artist', [''])[0] or '').strip()
                result['album_artist'] = (tags.get('albumartist', [''])[0] or '').strip()
                result['album'] = (tags.get('album', [''])[0] or '').strip()
                result['genre'] = (tags.get('genre', [''])[0] or '').strip()

                date_str = (tags.get('date', [''])[0] or tags.get('year', [''])[0] or '').strip()
                if date_str and len(date_str) >= 4:
                    result['year'] = date_str[:4]

                tn = tags.get('tracknumber', ['0'])[0]
                try:
                    result['track_number'] = int(str(tn).split('/')[0])
                except (ValueError, TypeError):
                    pass

                dn = tags.get('discnumber', ['1'])[0]
                try:
                    result['disc_number'] = int(str(dn).split('/')[0])
                except (ValueError, TypeError):
                    pass

            # Duration and bitrate from audio info
            if hasattr(audio, 'info') and audio.info:
                if hasattr(audio.info, 'length'):
                    result['duration_ms'] = int(audio.info.length * 1000)
                if hasattr(audio.info, 'bitrate'):
                    result['bitrate'] = int(audio.info.bitrate / 1000) if audio.info.bitrate else 0
    except Exception as e:
        logger.debug(f"Could not read tags from {os.path.basename(file_path)}: {e}")

    # Fallback: parse filename if no title
    if not result['title']:
        basename = os.path.splitext(os.path.basename(file_path))[0]
        # Strip leading track numbers like "01 - Title" or "01. Title"
        cleaned = re.sub(r'^\d+[\s.\-_]+', '', basename).strip()
        result['title'] = cleaned or basename

    return result


class SoulSyncTrack:
    """Track object compatible with DatabaseUpdateWorker expectations."""

    def __init__(self, file_path: str, tags: Dict[str, Any], artist_ref=None, album_ref=None):
        self.file_path = file_path
        self._tags = tags
        self._artist_ref = artist_ref
        self._album_ref = album_ref

        self.ratingKey = _stable_id(file_path)
        self.title = tags['title']
        self.duration = tags['duration_ms']
        self.trackNumber = tags['track_number'] or None
        self.discNumber = tags['disc_number'] or 1
        self.year = int(tags['year']) if tags['year'] else None
        self.userRating = None
        self.addedAt = datetime.fromtimestamp(os.path.getmtime(file_path)) if os.path.exists(file_path) else datetime.now()
        self.path = file_path
        self.bitRate = tags['bitrate']
        self.suffix = os.path.splitext(file_path)[1].lstrip('.').lower()
        # File size in bytes (powers Library Disk Usage card on Stats).
        # SoulSync standalone is the only "server" where we can read
        # size from disk directly — Plex/Jellyfin/Navidrome get theirs
        # from the API response.
        try:
            self.file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None
        except OSError:
            self.file_size = None

    def artist(self):
        return self._artist_ref

    def album(self):
        return self._album_ref


class SoulSyncAlbum:
    """Album object compatible with DatabaseUpdateWorker expectations."""

    def __init__(self, album_key: str, title: str, year: Optional[int],
                 artist_ref=None, track_list: List[SoulSyncTrack] = None):
        self.ratingKey = _stable_id(album_key)
        self.title = title
        self.year = year
        self._artist_ref = artist_ref
        self._tracks = track_list or []
        self.thumb = None
        self.addedAt = datetime.now()
        self.leafCount = len(self._tracks)  # Plex compat: track count
        self.duration = sum(t.duration for t in self._tracks)  # Total duration in ms

        # Collect genres from track tags
        genre_set = set()
        for t in self._tracks:
            if t._tags.get('genre'):
                genre_set.add(t._tags['genre'])
        self.genres = list(genre_set)

        # Set addedAt from earliest track
        if self._tracks:
            self.addedAt = min(t.addedAt for t in self._tracks)

        # Check for cover art in the album folder
        if self._tracks:
            album_dir = os.path.dirname(self._tracks[0].file_path)
            for cover_name in ['cover.jpg', 'cover.png', 'folder.jpg', 'folder.png']:
                cover_path = os.path.join(album_dir, cover_name)
                if os.path.isfile(cover_path):
                    self.thumb = cover_path
                    break

    def artist(self):
        return self._artist_ref

    def tracks(self):
        return self._tracks


class SoulSyncArtist:
    """Artist object compatible with DatabaseUpdateWorker expectations."""

    def __init__(self, artist_key: str, title: str, album_list: List[SoulSyncAlbum] = None):
        self.ratingKey = _stable_id(artist_key)
        self.title = title
        self._albums = album_list or []
        self.genres = []
        self.summary = ''
        self.thumb = None
        self.addedAt = datetime.now()

        # Collect genres from tracks
        genre_set = set()
        for album in self._albums:
            for track in album.tracks():
                if track._tags.get('genre'):
                    genre_set.add(track._tags['genre'])
        self.genres = list(genre_set)

        # Set addedAt from earliest album
        if self._albums:
            self.addedAt = min(a.addedAt for a in self._albums)

        # Use first album's thumb as artist thumb
        for album in self._albums:
            if album.thumb:
                self.thumb = album.thumb
                break

    def albums(self):
        return self._albums


from core.media_server.contract import MediaServerClient


class SoulSyncClient(MediaServerClient):
    """Filesystem-based media server client for standalone SoulSync operation.

    Scans the Transfer folder recursively, reads audio file tags, and
    returns artist/album/track objects in the same format as the
    Plex/Jellyfin/Navidrome clients. Designed as a drop-in replacement
    for the DatabaseUpdateWorker.
    """

    def __init__(self):
        from config.settings import config_manager
        self._config_manager = config_manager
        self._transfer_path = ''
        self._progress_callback = None
        self._cache = None  # Cached scan result
        self._cache_time = 0
        self._cache_ttl = 300  # 5 minute cache
        self._last_scan_time = None
        self._reload_config()

    def _reload_config(self):
        transfer = self._config_manager.get('soulseek.transfer_path', './Transfer')
        # Docker path resolution
        if os.path.exists('/.dockerenv') and len(transfer) >= 3 and transfer[1] == ':':
            drive = transfer[0].lower()
            rest = transfer[2:].replace('\\', '/')
            transfer = f"/host/mnt/{drive}{rest}"
        self._transfer_path = transfer

    def reload_config(self):
        self._reload_config()
        self._cache = None

    def ensure_connection(self) -> bool:
        self._reload_config()
        return os.path.isdir(self._transfer_path)

    def is_connected(self) -> bool:
        return os.path.isdir(self._transfer_path)

    def set_progress_callback(self, callback: Callable):
        self._progress_callback = callback

    def clear_cache(self):
        self._cache = None
        self._cache_time = 0

    def get_cache_stats(self) -> Dict[str, int]:
        if not self._cache:
            return {'artists': 0, 'albums': 0, 'tracks': 0}
        return {
            'artists': len(self._cache),
            'albums': sum(len(a.albums()) for a in self._cache),
            'tracks': sum(sum(len(alb.tracks()) for alb in a.albums()) for a in self._cache),
        }

    def _emit_progress(self, msg: str):
        if self._progress_callback:
            try:
                self._progress_callback(msg)
            except Exception as e:
                logger.debug("progress callback failed: %s", e)

    # ── Core Scanning ──

    def _scan_transfer(self, since_mtime: float = 0) -> List[SoulSyncArtist]:
        """Scan the Transfer folder and build artist/album/track hierarchy."""
        if not os.path.isdir(self._transfer_path):
            logger.warning(f"Transfer path not found: {self._transfer_path}")
            return []

        self._emit_progress(f"Scanning {self._transfer_path}...")
        logger.info(f"[SoulSync] Scanning Transfer folder: {self._transfer_path}")

        # Walk filesystem and collect all audio files with tags
        file_entries = []  # (file_path, tags)
        scanned = 0

        for root, _dirs, files in os.walk(self._transfer_path):
            for filename in files:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue

                file_path = os.path.join(root, filename)

                # Incremental: skip files older than since_mtime
                if since_mtime > 0:
                    try:
                        if os.path.getmtime(file_path) < since_mtime:
                            continue
                    except OSError:
                        continue

                tags = _read_tags(file_path)
                file_entries.append((file_path, tags))
                scanned += 1

                if scanned % 100 == 0:
                    self._emit_progress(f"Reading tags: {scanned} files...")

        logger.info(f"[SoulSync] Found {len(file_entries)} audio files")
        self._emit_progress(f"Found {len(file_entries)} audio files, building library...")

        # Group by artist → album
        # Key: (artist_name_lower) → { album_name_lower → [(file_path, tags)] }
        artist_map: Dict[str, Dict[str, List]] = {}
        artist_names: Dict[str, str] = {}  # lower → canonical name

        for file_path, tags in file_entries:
            # Prefer album artist, fall back to track artist, then folder name
            artist_name = tags['album_artist'] or tags['artist']
            if not artist_name:
                # Try to extract from folder structure (Transfer/Artist/Album/track)
                rel = os.path.relpath(file_path, self._transfer_path).replace('\\', '/')
                parts = rel.split('/')
                if len(parts) >= 3:
                    artist_name = parts[0]
                elif len(parts) >= 2:
                    artist_name = parts[0]
                else:
                    artist_name = 'Unknown Artist'

            album_name = tags['album']
            if not album_name:
                # Try folder name
                album_dir = os.path.basename(os.path.dirname(file_path))
                if album_dir and album_dir != os.path.basename(self._transfer_path):
                    album_name = album_dir
                else:
                    album_name = tags['title'] or 'Unknown Album'

            a_key = artist_name.lower().strip()
            al_key = album_name.lower().strip()

            if a_key not in artist_map:
                artist_map[a_key] = {}
                artist_names[a_key] = artist_name
            if al_key not in artist_map[a_key]:
                artist_map[a_key][al_key] = []

            artist_map[a_key][al_key].append((file_path, tags))

        # Build object hierarchy
        artists = []
        for a_key, albums_dict in artist_map.items():
            canonical_artist = artist_names[a_key]
            album_objects = []

            for al_key, track_entries in albums_dict.items():
                # Get canonical album name from first track
                canonical_album = track_entries[0][1]['album'] or al_key
                year = None
                for _, t in track_entries:
                    if t['year']:
                        try:
                            year = int(t['year'])
                        except ValueError:
                            pass
                        break

                # Build tracks
                track_objects = []
                for fp, tg in sorted(track_entries, key=lambda x: (x[1]['disc_number'], x[1]['track_number'])):
                    track_objects.append(SoulSyncTrack(fp, tg))

                album_key = f"{canonical_artist}::{canonical_album}"
                album_obj = SoulSyncAlbum(album_key, canonical_album, year, track_list=track_objects)

                # Link tracks back to album
                for t in track_objects:
                    t._album_ref = album_obj

                album_objects.append(album_obj)

            artist_obj = SoulSyncArtist(canonical_artist, canonical_artist, album_objects)

            # Link albums and tracks back to artist
            for album in album_objects:
                album._artist_ref = artist_obj
                for track in album.tracks():
                    track._artist_ref = artist_obj

            artists.append(artist_obj)

        logger.info(f"[SoulSync] Built library: {len(artists)} artists, "
                     f"{sum(len(a.albums()) for a in artists)} albums, "
                     f"{sum(sum(len(al.tracks()) for al in a.albums()) for a in artists)} tracks")

        return artists

    def _get_cached_scan(self) -> List[SoulSyncArtist]:
        """Return cached scan or perform a new one."""
        import time
        now = time.time()
        if self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache
        self._cache = self._scan_transfer()
        self._cache_time = now
        self._last_scan_time = datetime.now().isoformat()
        return self._cache

    # ── Public Interface (matches Plex/Jellyfin/Navidrome) ──

    def get_all_artists(self) -> List[SoulSyncArtist]:
        """Get all artists from the Transfer folder."""
        return self._get_cached_scan()

    def get_all_artist_ids(self) -> Set[str]:
        """Get all artist IDs for removal detection."""
        return {a.ratingKey for a in self._get_cached_scan()}

    def get_all_album_ids(self) -> Set[str]:
        """Get all album IDs for removal detection."""
        ids = set()
        for artist in self._get_cached_scan():
            for album in artist.albums():
                ids.add(album.ratingKey)
        return ids

    def get_recently_added_albums(self, max_results: int = 400) -> List[SoulSyncAlbum]:
        """Get recently added/modified albums (for incremental scan)."""
        import time
        # Use last scan time or default to 7 days ago
        since = 0
        if self._last_scan_time:
            try:
                since = datetime.fromisoformat(self._last_scan_time).timestamp()
            except (ValueError, TypeError):
                pass
        if since == 0:
            since = time.time() - (7 * 86400)  # 7 days ago

        # Scan only recent files
        artists = self._scan_transfer(since_mtime=since)
        all_albums = []
        for artist in artists:
            all_albums.extend(artist.albums())

        # Sort by most recent first
        all_albums.sort(key=lambda a: a.addedAt, reverse=True)
        return all_albums[:max_results]

    def get_recently_updated_albums(self, max_results: int = 400) -> List[SoulSyncAlbum]:
        """Alias for get_recently_added_albums (filesystem has no update concept)."""
        return self.get_recently_added_albums(max_results)

    def get_recently_added_tracks(self, max_results: int = 400) -> List[SoulSyncTrack]:
        """Get recently added tracks."""
        albums = self.get_recently_added_albums(max_results * 2)
        all_tracks = []
        for album in albums:
            all_tracks.extend(album.tracks())
        all_tracks.sort(key=lambda t: t.addedAt, reverse=True)
        return all_tracks[:max_results]

    def get_recently_updated_tracks(self, max_results: int = 400) -> List[SoulSyncTrack]:
        return self.get_recently_added_tracks(max_results)
