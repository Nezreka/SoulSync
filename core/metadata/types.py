"""Canonical typed dataclasses for metadata across all providers.

The metadata pipeline historically grew organically: each new provider
(Spotify → iTunes → Deezer → Tidal → Qobuz → MusicBrainz → AudioDB →
Discogs → Hydrabase) returns its own response shape, and consumer code
defensively extracts every field via fallback chains:

    _extract_lookup_value(album_data, 'id', 'album_id', 'collectionId',
                          'release_id', default=album_id)

That pattern works but is brittle: each new provider adds more keys to
chase, each consumer re-runs the same defensive logic, and there's no
contract about what shape any given consumer can trust.

This module is the canonical contract. Every provider produces these
types via a single ``from_<provider>_dict()`` classmethod. Every
consumer accepts these types and trusts the fields. Field names are
provider-neutral (``release_date`` not ``releaseDate``,
``image_url`` not ``artworkUrl100``).

This is the foundation PR. It only DEFINES the contract and provides
the converters; no consumer is migrated in this PR. Future PRs each
migrate one consumer to accept ``Album`` / ``Track`` / ``Artist``
instead of raw dicts.

The ``Album`` / ``Track`` / ``Artist`` symbols also re-export from
``core.itunes_client`` for backward compatibility — existing callers
don't need to change anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers shared by converters
# ---------------------------------------------------------------------------


def _str(value: Any, default: str = '') -> str:
    """Coerce to non-None str, never None."""
    if value is None:
        return default
    return str(value)


def _int(value: Any, default: int = 0) -> int:
    """Coerce to int, default on parse failure."""
    if value is None or value == '':
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _strip_discogs_disambiguation(name: str) -> str:
    """Discogs appends ``(N)`` to artist names when there are multiple
    artists with the same name. Strip so cross-provider matches work."""
    return re.sub(r'\s*\(\d+\)$', '', name or '').strip()


def _itunes_artwork(url: Optional[str]) -> Optional[str]:
    """iTunes serves cover art at any size by template substitution.
    Always upgrade ``100x100bb`` → ``3000x3000bb`` for highest quality."""
    if not url:
        return None
    return url.replace('100x100bb', '3000x3000bb')


# ---------------------------------------------------------------------------
# Album
# ---------------------------------------------------------------------------


@dataclass
class Album:
    """Provider-neutral album.

    Required fields are guaranteed to be set by every converter. Optional
    fields are explicit ``Optional[...]`` so consumers know they may be
    None / empty. Source-specific raw IDs that don't fit the typed schema
    can be stashed in ``external_ids`` (provider name → id string).
    """

    id: str                                      # Source-native id, always set
    name: str                                    # Album title, always set
    artists: List[str]                           # Display names, may be ['Unknown Artist']
    release_date: str                            # ISO 'YYYY' or 'YYYY-MM-DD' or '' when unknown
    total_tracks: int                            # 0 when unknown
    album_type: str                              # 'album' / 'single' / 'ep' / 'compilation'

    # Optional but commonly populated
    image_url: Optional[str] = None              # Highest-quality cover URL
    artist_id: Optional[str] = None              # Primary artist's source-native id
    genres: List[str] = field(default_factory=list)
    label: Optional[str] = None                  # Record label / publisher
    barcode: Optional[str] = None                # UPC/EAN — Discogs/MusicBrainz only

    # Source provenance
    source: str = ''                             # 'spotify' / 'itunes' / etc — set by converter
    external_ids: Dict[str, str] = field(default_factory=dict)
    external_urls: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Per-source converters. Each one is the SINGLE source of truth for
    # how that provider's response maps to the canonical Album. Adding
    # a new provider = adding one more converter here. Consumer code
    # never needs to know any provider's wire shape.
    # ------------------------------------------------------------------

    @classmethod
    def from_spotify_dict(cls, raw: Dict[str, Any]) -> 'Album':
        """Spotify Web API ``/albums/{id}`` response shape."""
        artists_raw = raw.get('artists') or []
        artist_names = [_str(a.get('name')) for a in artists_raw
                        if isinstance(a, dict) and a.get('name')]
        primary_artist_id = ''
        if artists_raw and isinstance(artists_raw[0], dict):
            primary_artist_id = _str(artists_raw[0].get('id'))

        images = raw.get('images') or []
        image_url = None
        if images and isinstance(images[0], dict):
            image_url = _str(images[0].get('url')) or None

        external_ids = {}
        if raw.get('id'):
            external_ids['spotify'] = _str(raw['id'])
        upc = (raw.get('external_ids') or {}).get('upc')
        if upc:
            external_ids['upc'] = _str(upc)

        external_urls = {}
        sp_url = (raw.get('external_urls') or {}).get('spotify')
        if sp_url:
            external_urls['spotify'] = _str(sp_url)

        return cls(
            id=_str(raw.get('id')),
            name=_str(raw.get('name')),
            artists=artist_names or ['Unknown Artist'],
            release_date=_str(raw.get('release_date')),
            total_tracks=_int(raw.get('total_tracks')),
            album_type=_str(raw.get('album_type'), default='album'),
            image_url=image_url,
            artist_id=primary_artist_id or None,
            genres=list(raw.get('genres') or []),
            label=_str(raw.get('label')) or None,
            barcode=external_ids.get('upc'),
            source='spotify',
            external_ids=external_ids,
            external_urls=external_urls,
        )

    @classmethod
    def from_itunes_dict(cls, raw: Dict[str, Any]) -> 'Album':
        """iTunes Search API album response shape (`collectionType=Album`)."""
        track_count = _int(raw.get('trackCount'))

        # iTunes doesn't tag album type; infer from track count + collectionType.
        collection_type = _str(raw.get('collectionType'), default='Album')
        if 'compilation' in collection_type.lower():
            album_type = 'compilation'
        elif track_count <= 3:
            album_type = 'single'
        elif track_count <= 6:
            album_type = 'ep'
        else:
            album_type = 'album'

        artist_id = _str(raw.get('artistId')) or None
        external_ids = {}
        if raw.get('collectionId'):
            external_ids['itunes'] = _str(raw['collectionId'])
        if artist_id:
            external_ids['itunes_artist'] = artist_id

        external_urls = {}
        if raw.get('collectionViewUrl'):
            external_urls['itunes'] = _str(raw['collectionViewUrl'])

        # Strip iTunes "(Single)" / "(EP)" / "(Deluxe)" suffixes from name
        # the same way the existing _clean_itunes_album_name helper does.
        name = _str(raw.get('collectionName'))
        name = re.sub(r'\s*[-(]\s*(Single|EP)\s*[)]?$', '', name, flags=re.IGNORECASE).strip()

        release_date = _str(raw.get('releaseDate'))
        if release_date and 'T' in release_date:
            release_date = release_date.split('T', 1)[0]

        primary_genre = _str(raw.get('primaryGenreName'))
        return cls(
            id=_str(raw.get('collectionId')),
            name=name,
            artists=[_str(raw.get('artistName'), default='Unknown Artist')],
            release_date=release_date,
            total_tracks=track_count,
            album_type=album_type,
            image_url=_itunes_artwork(raw.get('artworkUrl100')),
            artist_id=artist_id,
            genres=[primary_genre] if primary_genre else [],
            source='itunes',
            external_ids=external_ids,
            external_urls=external_urls,
        )

    @classmethod
    def from_deezer_dict(cls, raw: Dict[str, Any]) -> 'Album':
        """Deezer API ``/album/{id}`` response shape."""
        artist = raw.get('artist') or {}
        artist_name = _str(artist.get('name'), default='Unknown Artist') if isinstance(artist, dict) else _str(artist) or 'Unknown Artist'
        artist_id = _str(artist.get('id')) if isinstance(artist, dict) else ''

        # Deezer cover URLs come in size suffixes (cover_xl, cover_big,
        # cover_medium, cover_small). Prefer xl.
        image_url = (
            _str(raw.get('cover_xl'))
            or _str(raw.get('cover_big'))
            or _str(raw.get('cover_medium'))
            or _str(raw.get('cover'))
            or None
        )

        record_type = _str(raw.get('record_type'), default='album').lower()
        album_type = {'single': 'single', 'ep': 'ep'}.get(record_type, 'album')

        external_ids = {}
        if raw.get('id'):
            external_ids['deezer'] = _str(raw['id'])
        if raw.get('upc'):
            external_ids['upc'] = _str(raw['upc'])

        external_urls = {}
        if raw.get('link'):
            external_urls['deezer'] = _str(raw['link'])

        return cls(
            id=_str(raw.get('id')),
            name=_str(raw.get('title')),
            artists=[artist_name],
            release_date=_str(raw.get('release_date')),
            total_tracks=_int(raw.get('nb_tracks')),
            album_type=album_type,
            image_url=image_url,
            artist_id=artist_id or None,
            genres=[g.get('name', '') for g in (raw.get('genres', {}) or {}).get('data', [])
                    if isinstance(g, dict) and g.get('name')],
            label=_str(raw.get('label')) or None,
            barcode=external_ids.get('upc'),
            source='deezer',
            external_ids=external_ids,
            external_urls=external_urls,
        )

    @classmethod
    def from_discogs_dict(cls, raw: Dict[str, Any]) -> 'Album':
        """Discogs API ``/releases/{id}`` response shape."""
        artists_raw = raw.get('artists') or []
        artist_names = []
        primary_artist_id = ''
        for a in artists_raw:
            if not isinstance(a, dict):
                continue
            name = _strip_discogs_disambiguation(_str(a.get('name')))
            if name:
                artist_names.append(name)
            if not primary_artist_id and a.get('id'):
                primary_artist_id = _str(a['id'])

        images = raw.get('images') or []
        image_url = None
        if images and isinstance(images[0], dict):
            image_url = _str(images[0].get('uri') or images[0].get('uri150')) or None

        # Discogs `tracklist` is the source of total_tracks.
        tracklist = raw.get('tracklist') or []
        total_tracks = sum(1 for t in tracklist if isinstance(t, dict)
                           and t.get('type_') == 'track')
        if not total_tracks:
            total_tracks = len(tracklist)

        labels = raw.get('labels') or []
        label_name = ''
        if labels and isinstance(labels[0], dict):
            label_name = _str(labels[0].get('name'))

        external_ids = {}
        if raw.get('id'):
            external_ids['discogs'] = _str(raw['id'])
        # Discogs `identifiers` array can include barcode entries
        for ident in raw.get('identifiers', []) or []:
            if isinstance(ident, dict) and ident.get('type', '').lower() == 'barcode':
                bc = _str(ident.get('value')).strip()
                if bc:
                    external_ids['barcode'] = bc
                    break

        external_urls = {}
        if raw.get('uri'):
            external_urls['discogs'] = _str(raw['uri'])

        year = raw.get('year')
        release_date = str(year) if year and _int(year) > 0 else ''

        return cls(
            id=_str(raw.get('id')),
            name=_str(raw.get('title')),
            artists=artist_names or ['Unknown Artist'],
            release_date=release_date,
            total_tracks=total_tracks,
            album_type='album',  # Discogs doesn't tag this; default to album
            image_url=image_url,
            artist_id=primary_artist_id or None,
            genres=list(raw.get('genres') or []) + list(raw.get('styles') or []),
            label=label_name or None,
            barcode=external_ids.get('barcode'),
            source='discogs',
            external_ids=external_ids,
            external_urls=external_urls,
        )

    @classmethod
    def from_musicbrainz_dict(cls, raw: Dict[str, Any]) -> 'Album':
        """MusicBrainz ``/release/{mbid}`` response shape (release, not release-group)."""
        artist_credit = raw.get('artist-credit') or []
        artist_names = []
        primary_artist_id = ''
        for credit in artist_credit:
            if isinstance(credit, dict) and 'artist' in credit:
                name = _str(credit['artist'].get('name'))
                if name:
                    artist_names.append(name)
                if not primary_artist_id and credit['artist'].get('id'):
                    primary_artist_id = _str(credit['artist']['id'])

        # Total tracks: sum across media (MB stores per-disc).
        media = raw.get('media') or []
        total_tracks = sum(_int(m.get('track-count')) for m in media if isinstance(m, dict))

        external_ids = {}
        if raw.get('id'):
            external_ids['musicbrainz'] = _str(raw['id'])
        if raw.get('barcode'):
            external_ids['barcode'] = _str(raw['barcode'])

        # MB `release-group` carries the album-level type (album/single/ep)
        rg = raw.get('release-group') or {}
        primary_type = _str(rg.get('primary-type'), default='Album').lower()
        album_type = {'single': 'single', 'ep': 'ep'}.get(primary_type, 'album')
        if rg.get('id'):
            external_ids['musicbrainz_release_group'] = _str(rg['id'])

        labels = raw.get('label-info') or []
        label_name = ''
        if labels and isinstance(labels[0], dict):
            lbl = labels[0].get('label') or {}
            label_name = _str(lbl.get('name'))

        return cls(
            id=_str(raw.get('id')),
            name=_str(raw.get('title')),
            artists=artist_names or ['Unknown Artist'],
            release_date=_str(raw.get('date')),
            total_tracks=total_tracks,
            album_type=album_type,
            image_url=None,  # MB doesn't serve cover art directly; CAA is separate
            artist_id=primary_artist_id or None,
            genres=[],  # MB has tags but they're noisy; consumer can fetch separately
            label=label_name or None,
            barcode=external_ids.get('barcode'),
            source='musicbrainz',
            external_ids=external_ids,
            external_urls={},
        )

    @classmethod
    def from_hydrabase_dict(cls, raw: Dict[str, Any]) -> 'Album':
        """Hydrabase metadata service response shape."""
        artists_raw = raw.get('artists') or []
        if isinstance(artists_raw, str):
            artist_names = [artists_raw]
        else:
            artist_names = []
            for a in artists_raw:
                if isinstance(a, dict):
                    name = _str(a.get('name'))
                else:
                    name = _str(a)
                if name:
                    artist_names.append(name)

        external_ids = {}
        if raw.get('id'):
            external_ids['hydrabase'] = _str(raw['id'])
        if raw.get('soul_id'):
            external_ids['soul'] = _str(raw['soul_id'])

        return cls(
            id=_str(raw.get('id')),
            name=_str(raw.get('name') or raw.get('title')),
            artists=artist_names or ['Unknown Artist'],
            release_date=_str(raw.get('release_date')),
            total_tracks=_int(raw.get('total_tracks')),
            album_type=_str(raw.get('album_type'), default='album'),
            image_url=_str(raw.get('image_url') or raw.get('thumb_url')) or None,
            artist_id=_str(raw.get('artist_id')) or None,
            source='hydrabase',
            external_ids=external_ids,
        )

    # ------------------------------------------------------------------
    # Consumer-side helpers
    # ------------------------------------------------------------------

    def to_context_dict(self) -> Dict[str, Any]:
        """Return the canonical dict shape SoulSync's import / download
        pipelines expect. This is the bridge between typed metadata and
        the existing dict-passing internal API. Future PRs migrate
        consumers off this dict shape and onto the typed Album directly,
        at which point this helper becomes unnecessary."""
        primary_artist = self.artists[0] if self.artists else 'Unknown Artist'
        artists_dicts = [{'name': name, 'id': self.artist_id if i == 0 else ''}
                         for i, name in enumerate(self.artists)]
        images = [{'url': self.image_url}] if self.image_url else []

        return {
            'id': self.id,
            'name': self.name,
            'artist': primary_artist,
            'artist_name': primary_artist,
            'artist_id': self.artist_id or '',
            'artists': artists_dicts,
            'image_url': self.image_url,
            'images': images,
            'release_date': self.release_date,
            'album_type': self.album_type,
            'total_tracks': self.total_tracks,
            'source': self.source,
            'genres': list(self.genres),
            'label': self.label or '',
            'barcode': self.barcode or '',
            'external_ids': dict(self.external_ids),
            'external_urls': dict(self.external_urls),
        }


# ---------------------------------------------------------------------------
# Track and Artist — kept lighter for now. Future PRs flesh these out
# in the same per-source-converter pattern as Album.
# ---------------------------------------------------------------------------


@dataclass
class Track:
    """Provider-neutral track. Required fields are always populated by
    every provider's converter; optional fields may be None."""

    id: str
    name: str
    artists: List[str]
    album: str
    duration_ms: int

    # Optional
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    image_url: Optional[str] = None
    release_date: Optional[str] = None
    album_type: Optional[str] = None
    total_tracks: Optional[int] = None
    preview_url: Optional[str] = None
    isrc: Optional[str] = None
    popularity: int = 0  # Spotify-only; 0 elsewhere

    # Source provenance
    source: str = ''
    external_ids: Dict[str, str] = field(default_factory=dict)
    external_urls: Dict[str, str] = field(default_factory=dict)


@dataclass
class Artist:
    """Provider-neutral artist."""

    id: str
    name: str

    # Optional
    image_url: Optional[str] = None
    genres: List[str] = field(default_factory=list)
    popularity: int = 0  # Spotify-only; 0 elsewhere
    followers: int = 0   # Spotify-only; 0 elsewhere

    # Source provenance
    source: str = ''
    external_ids: Dict[str, str] = field(default_factory=dict)
    external_urls: Dict[str, str] = field(default_factory=dict)


__all__ = ['Album', 'Track', 'Artist']
