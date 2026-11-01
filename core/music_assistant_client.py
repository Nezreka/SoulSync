"""Music Assistant as a media server (#1050).

Music Assistant (music-assistant.io) keeps one library over any number of
music sources and exposes it through a JSON-RPC style API: ``POST /api``
with ``{"message_id", "command", "args"}`` and a bearer token. This client
reads that library the way the Plex, Jellyfin and Navidrome clients read
theirs and hands the scan the same artist, album and track objects
(``core.media_server.items``).

File paths: Music Assistant only knows a path for a track whose provider
mapping is a *Local files* or *SMB* source. That mapping's ``item_id`` is
the path relative to the source's folder, and the folder is the source's
``path`` config value. The client is scoped to one such source instance
(``music_assistant.source_instance``); tracks without a mapping for it have
no path and are skipped, the same way a Plex section SoulSync cannot mount
would be. The joined path then goes through the usual server path mapping
(``core.library.server_paths``) like every other server's.

Writes (playlists, sync) come back as background tasks; every write here
waits on ``tasks/get`` before reporting success. Playlist images cannot be
uploaded, so ``PLAYLIST_IMAGE`` is not declared.

Tested against Music Assistant 2.10.6 (schema 65). Commands used exist
there; ``storage/source_folder`` and ``favorites/set_item`` from newer
builds are not relied on.
"""

from __future__ import annotations

import posixpath
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
from urllib.parse import quote

import requests

from core.media_server.base import MediaServerBase
from core.media_server.contract import Capability
from core.media_server.items import MediaAlbum, MediaArtist, MediaTrack, parse_iso_date
from core.media_server.types import PlaylistInfo, TrackInfo
from core.settings import config_manager
from utils.logging_config import get_logger

logger = get_logger("music_assistant_client")

#: Music Assistant source domains that map a track to a file on disk.
FILE_SOURCE_DOMAINS = ('filesystem_local', 'filesystem_smb')
#: The provider instance or domain that addresses Music Assistant's own
#: library in ``music/*/get`` and ``*_tracks`` commands.
LIBRARY = 'library'
PAGE_SIZE = 500
TASK_DONE = ('finished', 'completed', 'success', 'done')
TASK_FAILED = ('failed', 'error', 'cancelled', 'canceled')


class MusicAssistantError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def _external_id(item: Dict[str, Any], kind: str) -> Optional[str]:
    """``external_ids`` is a set of (kind, value) pairs, serialised as a list
    of two-element lists."""
    for pair in item.get('external_ids') or []:
        try:
            if pair[0] == kind and pair[1]:
                return str(pair[1])
        except (IndexError, TypeError):
            continue
    return None


def _mapping_for(item: Dict[str, Any], instance: Optional[str]) -> Optional[Dict[str, Any]]:
    """The item's provider mapping on the configured file source, else the
    first file-source mapping, else None."""
    mappings = item.get('provider_mappings') or []
    if instance:
        for m in mappings:
            if m.get('provider_instance') == instance:
                return m
    for m in mappings:
        if m.get('provider_domain') in FILE_SOURCE_DOMAINS:
            return m
    return None


def _added_at(item: Dict[str, Any]) -> Optional[datetime]:
    value = item.get('date_added')
    if isinstance(value, str):
        return parse_iso_date(value)
    stamp = item.get('timestamp_added')
    if isinstance(stamp, (int, float)) and stamp > 0:
        try:
            return datetime.fromtimestamp(stamp)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _thumb_ref(item: Dict[str, Any]) -> Optional[str]:
    """A reference ``image_url`` can turn into a loadable URL: the image's
    own URL when it is remotely accessible, else ``ma:<provider>:<path>``."""
    images = (item.get('metadata') or {}).get('images') or []
    for image in images:
        if image.get('type') not in (None, 'thumb', 'landscape', 'fanart', 'logo', 'banner', 'cover'):
            continue
        path, provider = image.get('path'), image.get('provider')
        if not path:
            continue
        if image.get('remotely_accessible') and str(path).startswith(('http://', 'https://')):
            return str(path)
        return f"ma:{provider or 'library'}:{path}"
    return None


class MusicAssistantArtist(MediaArtist):
    server = 'music_assistant'

    def __init__(self, item: Dict[str, Any], client: 'MusicAssistantClient'):
        meta = item.get('metadata') or {}
        super().__init__(
            str(item.get('item_id', '')),
            item.get('name') or 'Unknown Artist',
            client=client, data=item, added_at=_added_at(item),
            genres=sorted(meta.get('genres') or []),
            summary=meta.get('description') or '',
            thumb=_thumb_ref(item),
        )
        self.musicBrainzId = _external_id(item, 'musicbrainz_artistid')


class MusicAssistantAlbum(MediaAlbum):
    server = 'music_assistant'

    def __init__(self, item: Dict[str, Any], client: 'MusicAssistantClient'):
        meta = item.get('metadata') or {}
        artists = item.get('artists') or []
        super().__init__(
            str(item.get('item_id', '')),
            item.get('name') or 'Unknown Album',
            client=client, data=item, added_at=_added_at(item),
            year=item.get('year'),
            thumb=_thumb_ref(item),
            genres=sorted(meta.get('genres') or []),
            artist_id=str(artists[0].get('item_id', '')) if artists else '',
        )
        self.musicBrainzId = _external_id(item, 'musicbrainz_albumid') or _external_id(item, 'musicbrainz_releasegroupid')


class MusicAssistantTrack(MediaTrack):
    server = 'music_assistant'

    def __init__(self, item: Dict[str, Any], client: 'MusicAssistantClient'):
        mapping = _mapping_for(item, client.source_instance if client else None)
        fmt = (mapping or {}).get('audio_format') or {}
        path = client.resolve_path(mapping) if (client and mapping) else None
        album = item.get('album') or {}
        artists = item.get('artists') or []
        suffix = None
        if path and '.' in posixpath.basename(path):
            suffix = posixpath.basename(path).rsplit('.', 1)[-1].lower()
        super().__init__(
            str(item.get('item_id', '')),
            item.get('name') or 'Unknown Track',
            client=client, data=item, added_at=_added_at(item),
            duration_ms=int(item.get('duration') or 0) * 1000,  # seconds on the wire
            track_number=item.get('track_number') or None,
            disc_number=item.get('disc_number') or None,
            year=album.get('year') if isinstance(album, dict) else None,
            user_rating=None,
            path=path,
            bitrate_kbps=int(fmt['bit_rate']) if fmt.get('bit_rate') else None,
            file_size=None,
            suffix=suffix or (fmt.get('content_type') or None),
            musicbrainz_id=_external_id(item, 'musicbrainz_recordingid') or _external_id(item, 'musicbrainz_trackid'),
            album_id=str(album.get('item_id', '')) if isinstance(album, dict) else '',
            artist_ids=[str(a.get('item_id', '')) for a in artists if isinstance(a, dict)],
        )
        self.uri = item.get('uri') or f"library://track/{self.ratingKey}"
        self.sample_rate = fmt.get('sample_rate')
        self.bit_depth = fmt.get('bit_depth')
        self.codec = fmt.get('content_type')
        self.isrc = _external_id(item, 'isrc')


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class MusicAssistantClient(MediaServerBase):
    CAPABILITIES = frozenset({
        Capability.SEARCH, Capability.RECENTLY_ADDED, Capability.SCAN_TRIGGER,
        Capability.LIBRARY_STATS, Capability.PLAYLISTS,
    })

    def __init__(self, transport=None):
        """``transport`` lets tests inject ``call(command, args) -> result``
        in place of HTTP."""
        self.base_url: Optional[str] = None
        self.token: Optional[str] = None
        self.source_instance: Optional[str] = None
        self.server_version: Optional[str] = None
        self.user_name: Optional[str] = None
        self.last_fetch_failed = False
        self._connected = False
        self._transport = transport
        self._session = requests.Session()
        self._timeout = 30
        self._progress_callback = None
        self._source_folder: Optional[str] = None
        self._artist_cache: Dict[str, MusicAssistantArtist] = {}
        self._album_cache: Dict[str, MusicAssistantAlbum] = {}
        self._albums_for_artist: Dict[str, List[MusicAssistantAlbum]] = {}
        self._tracks_for_album: Dict[str, List[MusicAssistantTrack]] = {}
        self.reload_config()

    # ------------------------------------------------------------------
    # Config and hooks
    # ------------------------------------------------------------------

    def reload_config(self) -> None:
        cfg = config_manager.get('music_assistant', {}) or {}
        self.base_url = (cfg.get('base_url') or '').strip().rstrip('/') or None
        self.token = (cfg.get('token') or '').strip() or None
        self.source_instance = (cfg.get('source_instance') or '').strip() or None
        try:
            self._timeout = int(cfg.get('request_timeout_seconds') or 30)
        except (TypeError, ValueError):
            self._timeout = 30
        self._connected = False
        self._source_folder = None

    def set_progress_callback(self, callback) -> None:
        self._progress_callback = callback

    def _progress(self, message: str) -> None:
        if self._progress_callback:
            try:
                self._progress_callback(message)
            except Exception as exc:  # noqa: BLE001 - a UI callback must not stop a scan
                logger.debug("progress callback failed: %s", exc)

    def scan_hints(self) -> Dict[str, Any]:
        return {'preparing_message': 'Connecting to Music Assistant...', 'caches_listings': True}

    def clear_cache(self) -> None:
        self._artist_cache.clear()
        self._album_cache.clear()
        self._albums_for_artist.clear()
        self._tracks_for_album.clear()

    def get_cache_stats(self) -> Dict[str, int]:
        return {
            'artists_cached': len(self._artist_cache),
            'albums_cached': len(self._album_cache),
            'bulk_albums_cached': sum(len(v) for v in self._albums_for_artist.values()),
            'bulk_tracks_cached': sum(len(v) for v in self._tracks_for_album.values()),
        }

    def image_url(self, thumb: Optional[str]) -> Optional[str]:
        """``ma:<provider>:<path>`` references load through Music Assistant's
        image proxy; a remotely accessible image is its own URL."""
        if not thumb:
            return None
        if thumb.startswith(('http://', 'https://')):
            return thumb
        if not thumb.startswith('ma:') or not self.base_url:
            return None
        try:
            _, provider, path = thumb.split(':', 2)
        except ValueError:
            return None
        return f"{self.base_url}/imageproxy?path={quote(path, safe='')}&provider={quote(provider, safe='')}&size=0"

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _call(self, command: str, **args: Any) -> Any:
        """One API command. Raises MusicAssistantError on transport or
        server error; returns the command's result (may be None)."""
        if self._transport is not None:
            return self._transport(command, args)
        if not self.base_url:
            raise MusicAssistantError("Music Assistant URL is not configured")
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = f"Bearer {self.token}"
        body = {'message_id': uuid.uuid4().hex, 'command': command, 'args': args or None}
        try:
            response = self._session.post(f"{self.base_url}/api", json=body, headers=headers, timeout=self._timeout)
        except requests.RequestException as exc:
            raise MusicAssistantError(f"{command}: {exc}") from exc
        if response.status_code == 401:
            raise MusicAssistantError(f"{command}: authentication required (HTTP 401); check the token")
        if response.status_code == 403:
            raise MusicAssistantError(f"{command}: not allowed (HTTP 403); the token lacks the scope")
        if response.status_code != 200:
            raise MusicAssistantError(f"{command}: HTTP {response.status_code} {response.text[:200]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MusicAssistantError(f"{command}: non-JSON response") from exc
        if isinstance(payload, dict) and payload.get('error_code'):
            raise MusicAssistantError(f"{command}: {payload.get('error_code')} {payload.get('details') or ''}".strip())
        if isinstance(payload, dict) and 'result' in payload:
            return payload['result']
        return payload

    def _info(self) -> Dict[str, Any]:
        """``GET /info`` needs no token: version and base URL."""
        if self._transport is not None:
            return self._transport('info', {}) or {}
        response = self._session.get(f"{self.base_url}/info", timeout=min(self._timeout, 10))
        response.raise_for_status()
        return response.json() or {}

    def _page(self, command: str, **args: Any) -> List[Dict[str, Any]]:
        """Every page of a ``library_items`` style listing. A short page
        ends it; a failure anywhere marks the fetch failed so a partial
        read is never taken for a complete one."""
        items: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = self._call(command, limit=PAGE_SIZE, offset=offset, **args)
            if page is None:
                raise MusicAssistantError(f"{command}: no answer at offset {offset}")
            if isinstance(page, dict):
                page = page.get('items') or []
            items.extend(page)
            if len(page) < PAGE_SIZE:
                return items
            offset += PAGE_SIZE

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def ensure_connection(self) -> bool:
        if self._connected:
            return True
        if not self.base_url:
            logger.warning("Music Assistant: no URL configured")
            return False
        if not self.token:
            logger.warning("Music Assistant: no token configured")
            return False
        try:
            info = self._info()
            self.server_version = info.get('server_version')
            me = self._call('auth/me') or {}
            self.user_name = me.get('username') or me.get('display_name')
            self._connected = True
            logger.info("Connected to Music Assistant %s as %s", self.server_version, self.user_name or 'unknown user')
            return True
        except (MusicAssistantError, requests.RequestException, ValueError) as exc:
            logger.error("Music Assistant connection failed: %s", exc)
            self._connected = False
            return False

    def is_connected(self) -> bool:
        return self._connected

    def test_connection(self) -> Tuple[bool, str]:
        """Connect and probe the scopes the scan and sync need, so a
        read-only token fails here and not at the first playlist write."""
        self._connected = False
        if not self.ensure_connection():
            return False, "Could not connect to Music Assistant; check the URL and token."
        try:
            self._call('tasks/list')
        except MusicAssistantError as exc:
            return False, f"Connected as {self.user_name}, but the token cannot read tasks (system.read): {exc}"
        sources = self.get_sources()
        if not sources:
            return False, (f"Connected to Music Assistant {self.server_version} as {self.user_name}, but it has no "
                           "Local files or SMB music source; add one so tracks have file paths.")
        chosen = self.source_instance or sources[0]['key']
        folder = self.source_folder(chosen)
        if not folder:
            return False, (f"Connected to Music Assistant {self.server_version} as {self.user_name}, but the token "
                           "cannot read the source's folder (config.providers.read).")
        names = ', '.join(s['title'] for s in sources)
        return True, (f"Connected to Music Assistant {self.server_version} as {self.user_name}; "
                      f"source {chosen} reads {folder} (sources: {names})")

    # ------------------------------------------------------------------
    # Sources (the "library" a user picks, like a Plex section)
    # ------------------------------------------------------------------

    def get_sources(self) -> List[Dict[str, str]]:
        """Local files and SMB music sources: ``[{key, title, domain}]``."""
        try:
            providers = self._call('providers', provider_type='music') or []
        except MusicAssistantError as exc:
            logger.error("Music Assistant providers listing failed: %s", exc)
            return []
        out = []
        for p in providers:
            if p.get('domain') in FILE_SOURCE_DOMAINS and p.get('instance_id'):
                out.append({'key': p['instance_id'], 'title': p.get('name') or p['instance_id'], 'domain': p['domain']})
        return out

    def set_source_by_instance(self, instance_id: str) -> bool:
        if not instance_id:
            return False
        self.source_instance = instance_id
        self._source_folder = None
        self.clear_cache()
        config_manager.set('music_assistant.source_instance', instance_id)
        return True

    def source_folder(self, instance_id: Optional[str] = None) -> Optional[str]:
        """The folder a Local files source reads from, as Music Assistant
        sees it. Cached per connection."""
        instance = instance_id or self.source_instance
        if not instance:
            return None
        if instance == self.source_instance and self._source_folder:
            return self._source_folder
        folder = None
        try:
            folder = self._call('config/providers/get_value', instance_id=instance, key='path')
        except MusicAssistantError as exc:
            logger.debug("config/providers/get_value failed for %s: %s", instance, exc)
            try:
                answer = self._call('storage/source_folder', instance_id=instance) or {}
                folder = answer.get('path') or answer.get('folder')
            except MusicAssistantError:
                folder = None
        folder = str(folder).rstrip('/') if folder else None
        if instance == self.source_instance:
            self._source_folder = folder
        return folder

    def resolve_path(self, mapping: Optional[Dict[str, Any]]) -> Optional[str]:
        """Absolute path on the Music Assistant host for a file-source
        mapping, or None (no mapping, a cue sub-track, no folder)."""
        if not mapping or mapping.get('provider_domain') not in FILE_SOURCE_DOMAINS:
            return None
        rel = str(mapping.get('item_id') or '')
        if not rel or '::' in rel:  # cue sheet sub-track ids are "<file>::<n>"
            return None
        folder = self.source_folder(mapping.get('provider_instance'))
        if not folder:
            return None
        return posixpath.join(folder, rel.lstrip('/'))

    # ------------------------------------------------------------------
    # Library reads
    # ------------------------------------------------------------------

    def _source_args(self) -> Dict[str, Any]:
        return {'provider': self.source_instance} if self.source_instance else {}

    def get_all_artists(self) -> List[MusicAssistantArtist]:
        self.last_fetch_failed = True
        if not self.ensure_connection():
            return []
        try:
            self._progress("Fetching artists from Music Assistant...")
            items = self._page('music/artists/library_items', summary=False, **self._source_args())
            artists = [MusicAssistantArtist(i, self) for i in items]
            for a in artists:
                self._artist_cache[a.ratingKey] = a
            self.last_fetch_failed = False
            return artists
        except MusicAssistantError as exc:
            logger.error("Music Assistant artists fetch failed: %s", exc)
            return []

    def get_all_artist_ids(self) -> set:
        self.last_fetch_failed = True
        if not self.ensure_connection():
            return set()
        try:
            items = self._page('music/artists/library_items', summary=True, **self._source_args())
            self.last_fetch_failed = False
            return {str(i.get('item_id')) for i in items if i.get('item_id') is not None}
        except MusicAssistantError as exc:
            logger.error("Music Assistant artist ids fetch failed: %s", exc)
            return set()

    def get_all_album_ids(self) -> set:
        self.last_fetch_failed = True
        if not self.ensure_connection():
            return set()
        try:
            items = self._page('music/albums/library_items', summary=True, **self._source_args())
            self.last_fetch_failed = False
            return {str(i.get('item_id')) for i in items if i.get('item_id') is not None}
        except MusicAssistantError as exc:
            logger.error("Music Assistant album ids fetch failed: %s", exc)
            return set()

    def get_albums_for_artist_verified(self, artist_id: str) -> Tuple[List[MusicAssistantAlbum], bool]:
        key = str(artist_id)
        if key in self._albums_for_artist:
            return self._albums_for_artist[key], True
        if not self.ensure_connection():
            return [], False
        try:
            items = self._call('music/artists/artist_albums', item_id=key, provider_instance_id_or_domain=LIBRARY) or []
            albums = [MusicAssistantAlbum(i, self) for i in items if i.get('provider') == LIBRARY]
            for a in albums:
                self._album_cache[a.ratingKey] = a
            self._albums_for_artist[key] = albums
            return albums, True
        except MusicAssistantError as exc:
            logger.error("Music Assistant albums for artist %s failed: %s", key, exc)
            return [], False

    def get_albums_for_artist(self, artist_id: str) -> List[MusicAssistantAlbum]:
        return self.get_albums_for_artist_verified(artist_id)[0]

    def get_tracks_for_album_verified(self, album_id: str) -> Tuple[List[MusicAssistantTrack], bool]:
        key = str(album_id)
        if key in self._tracks_for_album:
            return self._tracks_for_album[key], True
        if not self.ensure_connection():
            return [], False
        try:
            items = self._call('music/albums/album_tracks', item_id=key, provider_instance_id_or_domain=LIBRARY,
                               in_library_only=True) or []
            tracks = []
            for i in items:
                track = MusicAssistantTrack(i, self)
                if track.path is None:
                    # no file on the configured source: a streaming-only
                    # track, or one from another source instance
                    continue
                tracks.append(track)
            self._tracks_for_album[key] = tracks
            return tracks, True
        except MusicAssistantError as exc:
            logger.error("Music Assistant tracks for album %s failed: %s", key, exc)
            return [], False

    def get_tracks_for_album(self, album_id: str) -> List[MusicAssistantTrack]:
        return self.get_tracks_for_album_verified(album_id)[0]

    def get_artist_by_id(self, artist_id: str) -> Optional[MusicAssistantArtist]:
        key = str(artist_id)
        if key in self._artist_cache:
            return self._artist_cache[key]
        if not key or not self.ensure_connection():
            return None
        try:
            item = self._call('music/artists/get', item_id=key, provider_instance_id_or_domain=LIBRARY)
        except MusicAssistantError as exc:
            logger.debug("Music Assistant artist %s: %s", key, exc)
            return None
        if not item:
            return None
        artist = MusicAssistantArtist(item, self)
        self._artist_cache[key] = artist
        return artist

    def get_album_by_id(self, album_id: str) -> Optional[MusicAssistantAlbum]:
        key = str(album_id)
        if key in self._album_cache:
            return self._album_cache[key]
        if not key or not self.ensure_connection():
            return None
        try:
            item = self._call('music/albums/get', item_id=key, provider_instance_id_or_domain=LIBRARY)
        except MusicAssistantError as exc:
            logger.debug("Music Assistant album %s: %s", key, exc)
            return None
        if not item:
            return None
        album = MusicAssistantAlbum(item, self)
        self._album_cache[key] = album
        return album

    def get_recently_added_albums(self, limit: int = 400) -> List[MusicAssistantAlbum]:
        if not self.ensure_connection():
            return []
        try:
            items = self._call('music/albums/library_items', limit=limit, offset=0,
                               order_by='timestamp_added_desc', summary=False, **self._source_args()) or []
            if isinstance(items, dict):
                items = items.get('items') or []
            albums = [MusicAssistantAlbum(i, self) for i in items]
            for a in albums:
                self._album_cache[a.ratingKey] = a
            return albums
        except MusicAssistantError as exc:
            logger.error("Music Assistant recently added failed: %s", exc)
            return []

    def get_library_stats(self) -> Dict[str, int]:
        if not self.ensure_connection():
            return {}
        stats: Dict[str, int] = {}
        for name, command in (('artists', 'music/artists/count'), ('albums', 'music/albums/count'),
                              ('tracks', 'music/tracks/count'), ('playlists', 'music/playlists/count')):
            try:
                stats[name] = int(self._call(command) or 0)
            except (MusicAssistantError, TypeError, ValueError) as exc:
                logger.debug("Music Assistant %s failed: %s", command, exc)
        return stats

    # ------------------------------------------------------------------
    # Scan trigger
    # ------------------------------------------------------------------

    def trigger_library_scan(self, library_name: str = "Music") -> bool:
        if not self.ensure_connection():
            return False
        try:
            self._call('music/sync', providers=[self.source_instance] if self.source_instance else None)
            logger.info("Music Assistant sync triggered for %s", self.source_instance or 'all sources')
            return True
        except MusicAssistantError as exc:
            logger.error("Music Assistant sync trigger failed: %s", exc)
            return False

    def is_library_scanning(self, library_name: str = "Music") -> bool:
        if not self.ensure_connection():
            return False
        try:
            tasks = self._call('tasks/list') or []
        except MusicAssistantError:
            return False
        for task in tasks:
            name = f"{task.get('name', '')} {task.get('task_type', '')} {task.get('type', '')}".lower()
            status = str(task.get('status') or task.get('state') or '').lower()
            if 'sync' in name and status not in TASK_DONE + TASK_FAILED:
                return True
        return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_tracks(self, title: str, artist: str, limit: int = 15) -> List[TrackInfo]:
        if not self.ensure_connection():
            return []
        query = f"{artist} {title}".strip()
        if not query:
            return []
        try:
            result = self._call('music/search', search_query=query, media_types=['track'], limit=limit,
                                library_only=True) or {}
        except MusicAssistantError as exc:
            logger.error("Music Assistant search failed: %s", exc)
            return []
        items = result.get('tracks') if isinstance(result, dict) else result
        out: List[TrackInfo] = []
        for item in items or []:
            album = item.get('album') or {}
            artists = item.get('artists') or []
            out.append(TrackInfo(
                id=str(item.get('item_id', '')),
                title=item.get('name') or '',
                artist=artists[0].get('name', '') if artists else '',
                album=album.get('name', '') if isinstance(album, dict) else '',
                duration=int(item.get('duration') or 0) * 1000,
                track_number=item.get('track_number') or None,
                year=album.get('year') if isinstance(album, dict) else None,
                rating=None,
            ))
        return out

    # ------------------------------------------------------------------
    # Playlists
    # ------------------------------------------------------------------

    def _playlist_info(self, item: Dict[str, Any]) -> PlaylistInfo:
        meta = item.get('metadata') or {}
        return PlaylistInfo(
            id=str(item.get('item_id', '')),
            title=item.get('name') or '',
            description=meta.get('description'),
            duration=0,
            leaf_count=0,
            tracks=[],
            owner=item.get('owner') or None,
        )

    def get_all_playlists(self) -> List[PlaylistInfo]:
        if not self.ensure_connection():
            return []
        try:
            items = self._page('music/playlists/library_items', summary=False)
        except MusicAssistantError as exc:
            logger.error("Music Assistant playlists fetch failed: %s", exc)
            return []
        return [self._playlist_info(i) for i in items if i.get('is_editable', True)]

    def get_playlist_by_name(self, name: str) -> Optional[PlaylistInfo]:
        wanted = (name or '').strip().lower()
        for playlist in self.get_all_playlists():
            if playlist.title.strip().lower() == wanted:
                return playlist
        return None

    def get_playlist_by_id(self, playlist_id: str) -> Optional[PlaylistInfo]:
        if not self.ensure_connection():
            return None
        try:
            item = self._call('music/playlists/get', item_id=str(playlist_id), provider_instance_id_or_domain=LIBRARY)
        except MusicAssistantError:
            return None
        return self._playlist_info(item) if item else None

    def get_playlist_tracks(self, playlist_id: str) -> List[MusicAssistantTrack]:
        if not self.ensure_connection():
            return []
        try:
            items = self._call('music/playlists/playlist_tracks', item_id=str(playlist_id),
                               provider_instance_id_or_domain=LIBRARY) or []
        except MusicAssistantError as exc:
            logger.error("Music Assistant playlist %s tracks failed: %s", playlist_id, exc)
            return []
        return [MusicAssistantTrack(i, self) for i in items]

    def get_playlist_track_ids(self, playlist_id: str) -> List[str]:
        return [t.ratingKey for t in self.get_playlist_tracks(playlist_id)]

    @staticmethod
    def _track_uris(tracks: Iterable[Any]) -> List[str]:
        uris: List[str] = []
        for track in tracks or []:
            uri = getattr(track, 'uri', None)
            if not uri:
                key = getattr(track, 'ratingKey', None) or getattr(track, 'id', None)
                uri = f"library://track/{key}" if key else None
            if uri and uri not in uris:
                uris.append(uri)
        return uris

    def _await_task(self, task: Any, timeout: float = 120.0) -> bool:
        """Playlist writes and syncs return a background task. Wait for it
        to finish; a write is only reported done once the server says so."""
        if not isinstance(task, dict):
            return True  # an older build answered synchronously
        task_id = task.get('task_id') or task.get('id')
        status = str(task.get('status') or task.get('state') or '').lower()
        if not task_id or status in TASK_DONE:
            return status not in TASK_FAILED
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                current = self._call('tasks/get', task_id=task_id) or {}
            except MusicAssistantError as exc:
                logger.warning("Music Assistant task %s lookup failed: %s", task_id, exc)
                return False
            status = str(current.get('status') or current.get('state') or '').lower()
            if status in TASK_DONE:
                return True
            if status in TASK_FAILED:
                logger.error("Music Assistant task %s %s: %s", task_id, status, current.get('error') or '')
                return False
            time.sleep(0.5)
        logger.error("Music Assistant task %s did not finish within %ss", task_id, timeout)
        return False

    def _add_tracks(self, playlist_id: str, tracks: Iterable[Any]) -> bool:
        uris = self._track_uris(tracks)
        if not uris:
            return True
        task = self._call('music/playlists/add_playlist_tracks', db_playlist_id=str(playlist_id), uris=uris)
        return self._await_task(task)

    def _remove_positions(self, playlist_id: str, positions: Iterable[int]) -> bool:
        positions = sorted({int(p) for p in positions})
        if not positions:
            return True
        task = self._call('music/playlists/remove_playlist_tracks', db_playlist_id=str(playlist_id),
                          positions_to_remove=positions)
        return self._await_task(task)

    def _positions(self, playlist_id: str) -> List[Tuple[int, str]]:
        """(position, track id) for every track currently on the playlist."""
        if not self.ensure_connection():
            return []
        items = self._call('music/playlists/playlist_tracks', item_id=str(playlist_id),
                           provider_instance_id_or_domain=LIBRARY) or []
        out = []
        for index, item in enumerate(items):
            position = item.get('position')
            out.append((int(position) if position is not None else index, str(item.get('item_id', ''))))
        return out

    def _resolve_playlist(self, name: str, playlist_id: Optional[str]) -> Optional[PlaylistInfo]:
        """The playlist by stored id when it is still there, else by name."""
        if playlist_id:
            found = self.get_playlist_by_id(playlist_id)
            if found:
                return found
        return self.get_playlist_by_name(name)

    def create_playlist(self, name: str, tracks, playlist_id: str = None) -> Union[str, bool]:
        """Create (or with ``playlist_id`` overwrite) a playlist. Returns
        the server id on success, False on failure."""
        if not self.ensure_connection():
            return False
        try:
            if playlist_id:
                existing = self.get_playlist_by_id(playlist_id)
                if existing:
                    if not self._remove_positions(playlist_id, [p for p, _ in self._positions(playlist_id)]):
                        return False
                    if existing.title != name:
                        self._call('music/playlists/update', item_id=str(playlist_id),
                                   update={'item_id': str(playlist_id), 'provider': LIBRARY, 'name': name,
                                           'media_type': 'playlist', 'provider_mappings': []})
                    return str(playlist_id) if self._add_tracks(playlist_id, tracks) else False
            created = self._call('music/playlists/create_playlist', name=name, provider_instance_or_domain=LIBRARY) or {}
            new_id = created.get('item_id') if isinstance(created, dict) else None
            if new_id is None:
                logger.error("Music Assistant create_playlist '%s' returned no id", name)
                return False
            if not self._add_tracks(new_id, tracks):
                return False
            logger.info("Created Music Assistant playlist '%s' (%s)", name, new_id)
            return str(new_id)
        except MusicAssistantError as exc:
            logger.error("Music Assistant create_playlist '%s' failed: %s", name, exc)
            return False

    def append_to_playlist(self, playlist_name: str, tracks, *, playlist_id: Optional[str] = None) -> bool:
        """Add tracks the playlist does not have yet; never removes."""
        if not self.ensure_connection():
            return False
        try:
            playlist = self._resolve_playlist(playlist_name, playlist_id)
            if not playlist:
                return bool(self.create_playlist(playlist_name, tracks))
            have = {tid for _, tid in self._positions(playlist.id)}
            new = [t for t in tracks or [] if str(getattr(t, 'ratingKey', getattr(t, 'id', ''))) not in have]
            return self._add_tracks(playlist.id, new)
        except MusicAssistantError as exc:
            logger.error("Music Assistant append_to_playlist '%s' failed: %s", playlist_name, exc)
            return False

    def reconcile_playlist(self, playlist_name: str, tracks, *, playlist_id: Optional[str] = None) -> bool:
        """Add missing, remove gone, keep the playlist object."""
        if not self.ensure_connection():
            return False
        try:
            playlist = self._resolve_playlist(playlist_name, playlist_id)
            if not playlist:
                return bool(self.create_playlist(playlist_name, tracks))
            wanted = [str(getattr(t, 'ratingKey', getattr(t, 'id', ''))) for t in tracks or []]
            current = self._positions(playlist.id)
            gone = [p for p, tid in current if tid not in wanted]
            if not self._remove_positions(playlist.id, gone):
                return False
            have = {tid for _, tid in current if tid in wanted}
            missing = [t for t in tracks or [] if str(getattr(t, 'ratingKey', getattr(t, 'id', ''))) not in have]
            return self._add_tracks(playlist.id, missing)
        except MusicAssistantError as exc:
            logger.error("Music Assistant reconcile_playlist '%s' failed: %s", playlist_name, exc)
            return False

    def update_playlist(self, playlist_name: str, tracks, *, playlist_id: Optional[str] = None) -> bool:
        """Replace the playlist's tracks with ``tracks`` in order."""
        if not self.ensure_connection():
            return False
        try:
            playlist = self._resolve_playlist(playlist_name, playlist_id)
            if not playlist:
                return bool(self.create_playlist(playlist_name, tracks))
            return bool(self.create_playlist(playlist_name, tracks, playlist_id=playlist.id))
        except MusicAssistantError as exc:
            logger.error("Music Assistant update_playlist '%s' failed: %s", playlist_name, exc)
            return False

    def rewrite_playlist_order(self, playlist_id: str, name: str, ordered_song_ids) -> bool:
        """No move primitive: remove everything and add back in order."""
        ids = [str(i) for i in (ordered_song_ids or []) if str(i)]
        if not ids or not self.ensure_connection():
            return False
        try:
            if not self._remove_positions(playlist_id, [p for p, _ in self._positions(playlist_id)]):
                return False
            uris = [f"library://track/{i}" for i in ids]
            task = self._call('music/playlists/add_playlist_tracks', db_playlist_id=str(playlist_id), uris=uris)
            return self._await_task(task)
        except MusicAssistantError as exc:
            logger.error("Music Assistant rewrite_playlist_order '%s' failed: %s", name, exc)
            return False

    def copy_playlist(self, source_name: str, target_name: str) -> bool:
        source = self.get_playlist_by_name(source_name)
        if not source:
            logger.error("Music Assistant copy_playlist: source '%s' not found", source_name)
            return False
        return bool(self.create_playlist(target_name, self.get_playlist_tracks(source.id)))

    def delete_playlist(self, playlist_id: str) -> bool:
        if not self.ensure_connection():
            return False
        try:
            self._call('music/playlists/remove', item_id=str(playlist_id))
            return True
        except MusicAssistantError as exc:
            logger.error("Music Assistant delete_playlist %s failed: %s", playlist_id, exc)
            return False

    def set_playlist_image(self, playlist_name: str, image_url: str) -> bool:
        """Music Assistant has no playlist image upload; declared absent."""
        return False
