"""SoulSync Standalone Library Client — filesystem-based media server replacement.

Implements the same interface as Plex/Jellyfin/Navidrome clients so the
DatabaseUpdateWorker can scan the Transfer folder directly without an
external media server. Reads audio file tags via Mutagen, groups by
artist/album folder structure, and returns compatible data objects.
"""

import hashlib
import os
import re
import threading
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
        'musicbrainz_albumid': '', 'musicbrainz_albumcomment': '',
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
                for key in ('musicbrainz_albumid', 'musicbrainz_albumcomment'):
                    try:
                        result[key] = (tags.get(key, [''])[0] or '').strip()
                    except Exception:  # noqa: S110 - an odd frame just means "unknown"
                        pass

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

            # Duration from audio info. Bitrate: mutagen header when present,
            # else a size/duration average (Ogg Opus has no bitrate field).
            if hasattr(audio, 'info') and audio.info:
                if hasattr(audio.info, 'length'):
                    result['duration_ms'] = int(audio.info.length * 1000)
                from core.imports.file_ops import _kbps_from_stream_info
                kbps = _kbps_from_stream_info(audio.info, file_path)
                if kbps:
                    result['bitrate'] = int(kbps)
    except Exception as e:
        logger.debug(f"Could not read tags from {os.path.basename(file_path)}: {e}")

    # Fallback: parse filename if no title
    if not result['title']:
        basename = os.path.splitext(os.path.basename(file_path))[0]
        # Strip leading track numbers like "01 - Title" or "01. Title"
        cleaned = re.sub(r'^\d+[\s.\-_]+', '', basename).strip()
        result['title'] = cleaned or basename

    return result


from core.media_server.items import MediaAlbum, MediaArtist, MediaTrack


class SoulSyncTrack(MediaTrack):
    """A file on disk read through its tags. Shape owned by ``core.media_server.items``."""
    server = 'soulsync'

    def __init__(self, file_path: str, tags: Dict[str, Any], artist_ref=None, album_ref=None):
        exists = os.path.exists(file_path)
        try:
            file_size = os.path.getsize(file_path) if exists else None
        except OSError:
            file_size = None
        super().__init__(
            _stable_id(file_path),
            tags['title'],
            added_at=datetime.fromtimestamp(os.path.getmtime(file_path)) if exists else datetime.now(),
            duration_ms=tags['duration_ms'],
            track_number=tags['track_number'] or None,
            disc_number=tags['disc_number'] or 1,
            year=int(tags['year']) if tags['year'] else None,
            path=file_path,
            bitrate_kbps=tags['bitrate'],
            file_size=file_size,
            suffix=os.path.splitext(file_path)[1].lstrip('.').lower(),
        )
        self.file_path = file_path
        self._tags = tags
        self._artist_ref = artist_ref
        self._album_ref = album_ref

    def artist(self):
        return self._artist_ref

    def album(self):
        return self._album_ref


def _register_easy_release_comment() -> None:
    """Let easy-mode mutagen read picard's release comment (the disambiguation).

    vorbis comments read it as-is. id3 and mp4 keep it in a custom frame that
    easy mode only knows once it's registered.
    """
    try:
        from mutagen.easyid3 import EasyID3
        from mutagen.easymp4 import EasyMP4Tags
        if 'musicbrainz_albumcomment' not in EasyID3.valid_keys:
            EasyID3.RegisterTXXXKey('musicbrainz_albumcomment', 'MusicBrainz Album Comment')
        if 'musicbrainz_albumcomment' not in EasyMP4Tags.Get:
            EasyMP4Tags.RegisterFreeformKey('musicbrainz_albumcomment', 'MusicBrainz Album Comment')
    except Exception as e:  # noqa: BLE001 - without it id3/mp4 just read as plain releases
        logger.debug("release comment easy-key registration failed: %s", e)


_register_easy_release_comment()


def _split_by_release(entries: List) -> List:
    """Split one album-name group into its releases, as ``[(key_suffix, entries)]``.

    two releases can share a title and artist and differ only by musicbrainz's
    disambiguation (#1299). when the group holds more than one release id, each
    release whose files carry that disambiguation (the album comment tag) gets
    its own album, keyed by its release id. everything else keeps the plain key.

    a lone release never splits, comment or not. picard writes the comment too,
    and re-keying every edition-tagged album would hand it a new album id and
    drop whatever hangs off the old one. the catch: an incremental scan that only
    sees the new release can't tell, so it keys it plain until the next full scan.
    """
    def release_id(tags):
        return (tags.get('musicbrainz_albumid') or '').strip().casefold()

    releases = {release_id(tags) for _, tags in entries} - {''}
    distinct = {release_id(tags) for _, tags in entries
                if release_id(tags) and (tags.get('musicbrainz_albumcomment') or '').strip()}
    if len(releases) < 2 or not distinct:
        return [('', entries)]
    groups: Dict[str, List] = {}
    for entry in entries:
        rid = release_id(entry[1])
        groups.setdefault(f"::{rid}" if rid in distinct else '', []).append(entry)
    return list(groups.items())


class SoulSyncAlbum(MediaAlbum):
    """An album folder's worth of files. Shape owned by ``core.media_server.items``."""
    server = 'soulsync'

    def __init__(self, album_key: str, title: str, year: Optional[int],
                 artist_ref=None, track_list: List[SoulSyncTrack] = None):
        tracks = track_list or []
        genre_set = set()
        for t in tracks:
            if t._tags.get('genre'):
                genre_set.add(t._tags['genre'])
        thumb = None
        if tracks:
            album_dir = os.path.dirname(tracks[0].file_path)
            for cover_name in ['cover.jpg', 'cover.png', 'folder.jpg', 'folder.png']:
                cover_path = os.path.join(album_dir, cover_name)
                if os.path.isfile(cover_path):
                    thumb = cover_path
                    break
        super().__init__(
            _stable_id(album_key),
            title,
            added_at=min(t.addedAt for t in tracks) if tracks else datetime.now(),
            year=year,
            thumb=thumb,
            genres=list(genre_set),
        )
        self._artist_ref = artist_ref
        self._tracks = tracks
        self.leafCount = len(self._tracks)  # Plex compat: track count
        self.duration = sum(t.duration for t in self._tracks)  # Total duration in ms

    def artist(self):
        return self._artist_ref

    def tracks(self):
        return self._tracks


class SoulSyncArtist(MediaArtist):
    """An artist folder. Shape owned by ``core.media_server.items``."""
    server = 'soulsync'

    def __init__(self, artist_key: str, title: str, album_list: List[SoulSyncAlbum] = None):
        albums = album_list or []
        genre_set = set()
        thumb = None
        for album in albums:
            for track in album.tracks():
                if track._tags.get('genre'):
                    genre_set.add(track._tags['genre'])
            if thumb is None and album.thumb:
                thumb = album.thumb
        super().__init__(
            _stable_id(artist_key),
            title,
            added_at=min(a.addedAt for a in albums) if albums else datetime.now(),
            genres=list(genre_set),
            summary='',
            thumb=thumb,
        )
        self._albums = albums

    def albums(self):
        return self._albums


from core.media_server.base import MediaServerBase
from core.media_server.contract import Capability


class SoulSyncClient(MediaServerBase):
    # Reads the files itself, so a tag write is visible on the next scan.
    CAPABILITIES = frozenset({
        Capability.RECENTLY_ADDED, Capability.SCAN_TRIGGER, Capability.TAGS_AUTODETECTED,
    })

    """Filesystem-based media server client for standalone SoulSync operation.

    Scans the Transfer folder recursively, reads audio file tags, and
    returns artist/album/track objects in the same format as the
    Plex/Jellyfin/Navidrome clients. Designed as a drop-in replacement
    for the DatabaseUpdateWorker.
    """

    def __init__(self):
        from core.settings import config_manager
        self._config_manager = config_manager
        self._transfer_path = ''
        self._progress_callback = None
        self._cache = None  # Cached scan result
        self._cache_time = 0
        self._cache_ttl = 300  # 5 minute cache
        self._last_scan_time = None
        self._scan_thread = None  # Background rescan started by trigger_library_scan
        self._scan_lock = threading.Lock()  # Serializes trigger check + assign + start
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
            # Prune hidden directories in place. The atomic-publish staging tree
            # lives at <transfer>/.soulsync_atomic_staging, so without this the
            # scan would index a half-downloaded album as real library tracks —
            # exactly the partial-album visibility that feature exists to
            # prevent, just inflicted by SoulSync on itself instead of by Plex.
            # Also skips the usual hidden clutter (.Trash, .DS_Store dirs, …),
            # none of which is ever a music library.
            _dirs[:] = [d for d in _dirs if not d.startswith('.')]
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

            for al_key, name_entries in albums_dict.items():
                for release_suffix, track_entries in _split_by_release(name_entries):
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

                    album_key = f"{canonical_artist}::{canonical_album}{release_suffix}"
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

    # ── Library Scan ──

    def trigger_library_scan(self, library_name: str = "Music") -> bool:
        """Trigger a standalone library scan.

        The other clients ask an external server to rescan; standalone
        SoulSync IS the server, so a scan is a filesystem walk of the
        Transfer folder. The walk reads every file's tags and can take
        a while on a large library, so it runs on a background thread —
        the same fire-and-forget shape as the server APIs. When the
        walk finishes, its result replaces the scan cache, so the next
        get_all_artists() (e.g. the DatabaseUpdateWorker) sees the new
        files without waiting out the cache TTL.

        ``library_name`` is accepted for parity with the other clients
        and ignored — standalone has exactly one library. Returns True
        when a scan is running after the call (started here, or one
        was already in progress). Never raises, matching the other
        clients: failures are logged and reported as False.
        """
        thread = None
        try:
            if not self.is_connected():
                logger.warning("[SoulSync] Library scan requested but the Transfer path is not available")
                return False
            # The check + assign + start must be atomic: callers are
            # concurrent by design (scan-manager timer thread, the
            # audiobook monitor's per-book nudge threads), and two
            # triggers racing the check would each spawn a full
            # Transfer walk. is_alive() is also False in the
            # assign→start gap, so the lock has to cover start() too.
            with self._scan_lock:
                if self.is_library_scanning():
                    logger.info("[SoulSync] Library scan already in progress")
                    return True
                thread = threading.Thread(
                    target=self._run_triggered_scan,
                    daemon=True,
                    name="soulsync-library-scan",
                )
                self._scan_thread = thread
                thread.start()
            logger.info("[SoulSync] Library scan triggered (background filesystem rescan)")
            return True
        except Exception as e:
            logger.error(f"[SoulSync] Failed to trigger library scan: {e}")
            if thread is not None and self._scan_thread is thread and not thread.is_alive():
                self._scan_thread = None
            return False

    def is_library_scanning(self, library_name: str = "Music") -> bool:
        """Whether a triggered background rescan is still running."""
        thread = self._scan_thread
        return thread is not None and thread.is_alive()

    def _run_triggered_scan(self):
        """Background body for trigger_library_scan: run a full rescan,
        then swap the cache so readers pick up the new library state.
        A walk that raises — or that finds the Transfer path gone
        (unmounted between the trigger's connectivity check and the
        walk, where _scan_transfer would otherwise return an empty
        list) — keeps the previous cache: stale data beats an emptied
        library view."""
        import time
        try:
            if not os.path.isdir(self._transfer_path):
                logger.error(f"[SoulSync] Triggered library scan aborted — Transfer path not available: {self._transfer_path}")
                return
            artists = self._scan_transfer()
            self._cache = artists
            self._cache_time = time.time()
            self._last_scan_time = datetime.now().isoformat()
        except Exception as e:
            logger.error(f"[SoulSync] Triggered library scan failed: {e}")
        finally:
            if self._scan_thread is threading.current_thread():
                self._scan_thread = None

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
