"""The artist, album and track objects a media server client hands to the
library scan.

Jellyfin, Navidrome and the standalone library each grew a wrapper
family ("mimic the Plex object interface") with the same attribute
names, because ``database_update_worker`` and
``music_database.insert_or_update_media_*`` read plexapi's names:
``ratingKey``, ``title``, ``thumb``, ``addedAt``, ``trackNumber``,
``discNumber``, ``bitRate``, ``path`` and so on. Three copies of the
same shape, each drifting a little (Jellyfin tracks carry
``_artist_ids``, Navidrome tracks ``_artist_id``; only Navidrome and the
standalone library set ``suffix``).

These base classes own that shape once. The legacy names stay as the
real attributes, so nothing downstream changes; the canonical names
(``server_id``, ``name``, ``added_at``, ``track_number``, ...) are
read-only views on them, so new code can stop spelling plexapi. The
``*_ATTRS`` tuples are the contract the scan relies on and the shared
contract test pins every wrapper family to them.

Plex objects are plexapi's own and are not wrapped; the contract tuples
below are the subset of their attributes the scan reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


def parse_iso_date(value: Optional[str]) -> Optional[datetime]:
    """ISO-8601 to datetime, None on anything unparsable. Jellyfin and
    Navidrome both send ISO strings with a trailing Z."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


# Attribute names the scan and the insert path read from each object.
# Legacy (plexapi) spelling on purpose: these are what the consumers use.
ARTIST_ATTRS: Tuple[str, ...] = ('ratingKey', 'title', 'addedAt', 'genres', 'summary', 'thumb')
ALBUM_ATTRS: Tuple[str, ...] = ('ratingKey', 'title', 'addedAt', 'year', 'thumb', 'genres')
TRACK_ATTRS: Tuple[str, ...] = (
    'ratingKey', 'title', 'addedAt', 'duration', 'trackNumber', 'discNumber',
    'year', 'userRating', 'path', 'bitRate', 'file_size', 'suffix', 'musicBrainzId',
)
# Methods the scan calls on each object.
ARTIST_METHODS: Tuple[str, ...] = ('albums',)
ALBUM_METHODS: Tuple[str, ...] = ('artist', 'tracks')
TRACK_METHODS: Tuple[str, ...] = ('artist', 'album')


class MediaItem:
    """Common base: server id, display title, when the server added it,
    and the raw payload it was built from."""

    #: Set by each wrapper family so a consumer can tell items apart
    #: without ``__class__.__name__`` sniffing.
    server: str = ''

    def __init__(self, server_id: Any, title: str, *, client: Any = None,
                 data: Optional[Dict[str, Any]] = None,
                 added_at: Optional[datetime] = None) -> None:
        self.ratingKey = server_id if server_id is not None else ''
        self.title = title
        self.addedAt = added_at
        self._data = data if data is not None else {}
        self._client = client

    # Canonical names, read-only views on the legacy attributes.
    @property
    def server_id(self) -> Any:
        return self.ratingKey

    @property
    def name(self) -> str:
        return self.title

    @property
    def added_at(self) -> Optional[datetime]:
        return self.addedAt

    @property
    def raw(self) -> Dict[str, Any]:
        return self._data

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.ratingKey!r} {self.title!r}>"


class MediaArtist(MediaItem):
    def __init__(self, server_id: Any, title: str, *, client: Any = None,
                 data: Optional[Dict[str, Any]] = None,
                 added_at: Optional[datetime] = None,
                 genres: Optional[List[str]] = None, summary: str = '',
                 thumb: Optional[str] = None) -> None:
        super().__init__(server_id, title, client=client, data=data, added_at=added_at)
        self.genres = list(genres) if genres else []
        self.summary = summary or ''
        self.thumb = thumb

    @property
    def biography(self) -> str:
        return self.summary

    @property
    def image_url(self) -> Optional[str]:
        return self.thumb

    def albums(self) -> List['MediaAlbum']:
        """All albums by this artist. Default asks the client; a family
        that already holds the list overrides."""
        return self._client.get_albums_for_artist(self.ratingKey)

    def albums_verified(self):
        """(albums, ok): ok is False when the server gave no answer,
        which albums() folds into an empty list. The deep scan reads
        this one."""
        return self._client.get_albums_for_artist_verified(self.ratingKey)


class MediaAlbum(MediaItem):
    def __init__(self, server_id: Any, title: str, *, client: Any = None,
                 data: Optional[Dict[str, Any]] = None,
                 added_at: Optional[datetime] = None,
                 year: Optional[int] = None, thumb: Optional[str] = None,
                 genres: Optional[List[str]] = None,
                 artist_id: Any = '') -> None:
        super().__init__(server_id, title, client=client, data=data, added_at=added_at)
        self.year = year
        self.thumb = thumb
        self.genres = list(genres) if genres else []
        self._artist_id = artist_id or ''

    @property
    def artist_id(self) -> Any:
        return self._artist_id

    @property
    def image_url(self) -> Optional[str]:
        return self.thumb

    def artist(self) -> Optional[MediaArtist]:
        if self._artist_id:
            return self._client.get_artist_by_id(self._artist_id)
        return None

    def tracks(self) -> List['MediaTrack']:
        return self._client.get_tracks_for_album(self.ratingKey)

    def tracks_verified(self):
        """(tracks, ok): ok is False when the server gave no answer."""
        return self._client.get_tracks_for_album_verified(self.ratingKey)


class MediaTrack(MediaItem):
    """``duration`` is milliseconds, ``bitRate`` kilobits per second,
    ``file_size`` bytes; each family converts from its wire units."""

    def __init__(self, server_id: Any, title: str, *, client: Any = None,
                 data: Optional[Dict[str, Any]] = None,
                 added_at: Optional[datetime] = None,
                 duration_ms: int = 0, track_number: Optional[int] = None,
                 disc_number: Optional[int] = None, year: Optional[int] = None,
                 user_rating: Optional[float] = None, path: Optional[str] = None,
                 bitrate_kbps: Optional[int] = None, file_size: Optional[int] = None,
                 suffix: Optional[str] = None, musicbrainz_id: Optional[str] = None,
                 album_id: Any = '', artist_ids: Optional[List[Any]] = None) -> None:
        super().__init__(server_id, title, client=client, data=data, added_at=added_at)
        self.duration = duration_ms or 0
        self.trackNumber = track_number
        self.discNumber = disc_number
        self.year = year
        self.userRating = user_rating
        self.path = path
        self.bitRate = bitrate_kbps
        self.file_size = file_size
        self.suffix = suffix
        self.musicBrainzId = musicbrainz_id
        self._album_id = album_id or ''
        self._artist_ids = [a for a in (artist_ids or []) if a]
        # Single-artist families read this one; keep both in step.
        self._artist_id = self._artist_ids[0] if self._artist_ids else ''

    @property
    def duration_ms(self) -> int:
        return self.duration

    @property
    def track_number(self) -> Optional[int]:
        return self.trackNumber

    @property
    def disc_number(self) -> Optional[int]:
        return self.discNumber

    @property
    def user_rating(self) -> Optional[float]:
        return self.userRating

    @property
    def bitrate_kbps(self) -> Optional[int]:
        return self.bitRate

    @property
    def musicbrainz_id(self) -> Optional[str]:
        return self.musicBrainzId

    @property
    def album_id(self) -> Any:
        return self._album_id

    @property
    def artist_ids(self) -> List[Any]:
        return list(self._artist_ids)

    def artist(self) -> Optional[MediaArtist]:
        """The primary artist."""
        if self._artist_id:
            return self._client.get_artist_by_id(self._artist_id)
        return None

    def album(self) -> Optional[MediaAlbum]:
        if self._album_id:
            return self._client.get_album_by_id(self._album_id)
        return None
