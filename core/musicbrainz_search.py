"""MusicBrainz Search Adapter — provides enhanced search tab integration.

Wraps the existing MusicBrainzClient with search methods that return the
same Track/Artist/Album dataclass format used by Deezer/iTunes/Discogs,
enabling MusicBrainz as a search tab in enhanced and global search.
Album art is fetched from Cover Art Archive (free, linked by release MBID).
"""

import requests
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("musicbrainz_search")

COVER_ART_ARCHIVE_URL = "https://coverartarchive.org"


@dataclass
class Track:
    id: str
    name: str
    artists: List[str]
    album: str
    duration_ms: int
    popularity: int
    preview_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None
    image_url: Optional[str] = None
    release_date: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    album_type: Optional[str] = None
    total_tracks: Optional[int] = None
    album_id: Optional[str] = None


@dataclass
class Artist:
    id: str
    name: str
    popularity: int
    genres: List[str]
    followers: int
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None


@dataclass
class Album:
    id: str
    name: str
    artists: List[str]
    release_date: str
    total_tracks: int
    album_type: str
    image_url: Optional[str] = None
    external_urls: Optional[Dict[str, str]] = None


def _get_cover_art_url(release_mbid: str) -> Optional[str]:
    """Fetch album art URL from Cover Art Archive. Returns None if not available."""
    try:
        # CAA redirects to the actual image URL — just get the front image URL
        url = f"{COVER_ART_ARCHIVE_URL}/release/{release_mbid}/front-250"
        resp = requests.head(url, timeout=3, allow_redirects=True)
        if resp.status_code == 200:
            return resp.url  # The redirect target is the actual image
        return None
    except Exception:
        return None


def _get_release_group_art(release_group_mbid: str) -> Optional[str]:
    """Fetch album art from release group (covers all editions)."""
    try:
        url = f"{COVER_ART_ARCHIVE_URL}/release-group/{release_group_mbid}/front-250"
        resp = requests.head(url, timeout=3, allow_redirects=True)
        if resp.status_code == 200:
            return resp.url
        return None
    except Exception:
        return None


def _extract_artist_credit(artist_credit) -> List[str]:
    """Extract artist names from MusicBrainz artist-credit array."""
    if not artist_credit:
        return []
    names = []
    for credit in artist_credit:
        if isinstance(credit, dict) and 'artist' in credit:
            names.append(credit['artist'].get('name', ''))
        elif isinstance(credit, dict) and 'name' in credit:
            names.append(credit['name'])
    return [n for n in names if n]


def _map_release_type(primary_type: str, secondary_types: List[str] = None) -> str:
    """Map MusicBrainz release group type to standard album_type."""
    pt = (primary_type or '').lower()
    if pt == 'album':
        return 'album'
    elif pt == 'single':
        return 'single'
    elif pt == 'ep':
        return 'ep'
    elif pt == 'compilation' or 'compilation' in (secondary_types or []):
        return 'compilation'
    return 'album'


class MusicBrainzSearchClient:
    """Search adapter for MusicBrainz — compatible with enhanced search tab system."""

    def __init__(self):
        from core.musicbrainz_client import MusicBrainzClient
        # Client defaults to the project URL as its User-Agent contact,
        # which is what MusicBrainz wants. Version stays generic ("2") —
        # the exact UI minor version would add noise to every request.
        self._client = MusicBrainzClient("SoulSync", "2")
        self._art_cache: Dict[str, Optional[str]] = {}  # mbid -> url

    def _cached_art(self, release_mbid: str, release_group_mbid: str = '') -> Optional[str]:
        """Get cover art with caching. Tries release first, then release group."""
        if release_mbid in self._art_cache:
            return self._art_cache[release_mbid]

        url = _get_cover_art_url(release_mbid)
        if not url and release_group_mbid:
            url = _get_release_group_art(release_group_mbid)
        self._art_cache[release_mbid] = url
        return url

    # Score threshold for user-facing search results. MusicBrainz returns a
    # Lucene score 0-100 on every match; exact name/alias hits score 100,
    # partial/typo matches trend lower, and tribute bands / random
    # lookalikes score 40-65. 80 is the cutoff that keeps the true artist
    # and close variants while dropping unrelated noise.
    _MIN_SCORE = 80

    def search_artists(self, query: str, limit: int = 10) -> List[Artist]:
        """Search MusicBrainz for artists by name.

        Uses a bare Lucene query (no field prefix) so MusicBrainz searches
        the alias, artist, AND sortname indexes together — much better
        recall than strict `artist:"..."` phrase matching. Results are
        filtered by score (>= 80) to drop tribute bands and unrelated
        lookalikes.
        """
        try:
            raw = self._client.search_artist(query, limit=limit, strict=False)
            artists = []
            for a in raw:
                score = a.get('score', 0) or 0
                if score < self._MIN_SCORE:
                    continue

                mbid = a.get('id', '')
                name = a.get('name', '')
                if not mbid or not name:
                    continue

                # Genres from MB tags (user-applied categorical labels). Each
                # tag has {name, count}; keep the top-weighted ones.
                tags = a.get('tags', []) or []
                genres = [t.get('name') for t in tags if t.get('name')][:5]

                external_urls = {
                    'musicbrainz': f'https://musicbrainz.org/artist/{mbid}'
                }

                artists.append(Artist(
                    id=mbid,
                    name=name,
                    popularity=score,  # Reuse score as popularity (0-100)
                    genres=genres,
                    followers=0,  # MusicBrainz doesn't track followers
                    image_url=None,  # MB doesn't store artist images directly
                    external_urls=external_urls,
                ))
            return artists
        except Exception as e:
            logger.warning(f"MusicBrainz artist search failed: {e}")
            return []

    def search_albums(self, query: str, limit: int = 10) -> List[Album]:
        """Search MusicBrainz for releases (albums)."""
        try:
            # Try to split "Artist Album" for better matching
            artist_name = None
            album_name = query
            for sep in [' - ', ' – ', ' — ']:
                if sep in query:
                    parts = query.split(sep, 1)
                    artist_name = parts[0].strip()
                    album_name = parts[1].strip()
                    break

            results = self._client.search_release(album_name, artist_name=artist_name, limit=limit)

            # If no separator, try word-boundary splitting
            if not results and not artist_name:
                words = query.split()
                for i in range(1, len(words)):
                    possible_artist = ' '.join(words[:i])
                    possible_album = ' '.join(words[i:])
                    if len(possible_album) >= 2:
                        results = self._client.search_release(possible_album, artist_name=possible_artist, limit=limit)
                        if results:
                            break

            albums = []
            for r in results:
                mbid = r.get('id', '')
                title = r.get('title', '')
                if not title:
                    continue

                artists = _extract_artist_credit(r.get('artist-credit', []))
                release_date = r.get('date', '') or ''

                # Track count from media
                total_tracks = 0
                media = r.get('media', [])
                for m in media:
                    total_tracks += m.get('track-count', 0)

                # Release type
                rg = r.get('release-group', {})
                primary_type = rg.get('primary-type', '') or ''
                secondary_types = rg.get('secondary-types', []) or []
                album_type = _map_release_type(primary_type, secondary_types)

                # Cover art (non-blocking — skip if slow)
                rg_mbid = rg.get('id', '')
                image_url = self._cached_art(mbid, rg_mbid)

                external_urls = {'musicbrainz': f'https://musicbrainz.org/release/{mbid}'} if mbid else {}

                albums.append(Album(
                    id=mbid,
                    name=title,
                    artists=artists if artists else ['Unknown Artist'],
                    release_date=release_date,
                    total_tracks=total_tracks,
                    album_type=album_type,
                    image_url=image_url,
                    external_urls=external_urls,
                ))
            # Deduplicate: keep best version of each title+artist combo
            # (prefer ones with release dates and cover art)
            seen = {}
            deduped = []
            for album in albums:
                key = (album.name.lower().strip(), ', '.join(album.artists).lower().strip())
                if key not in seen:
                    seen[key] = album
                    deduped.append(album)
                else:
                    existing = seen[key]
                    # Prefer: has date > no date, has art > no art
                    better = False
                    if not existing.release_date and album.release_date:
                        better = True
                    elif not existing.image_url and album.image_url:
                        better = True
                    if better:
                        deduped[deduped.index(existing)] = album
                        seen[key] = album
            return deduped
        except Exception as e:
            logger.warning(f"MusicBrainz album search failed: {e}")
            return []

    def search_tracks(self, query: str, limit: int = 10) -> List[Track]:
        """Search MusicBrainz for recordings (tracks)."""
        try:
            # Try to split "Artist - Title" for better matching
            artist_name = None
            track_name = query
            for sep in [' - ', ' – ', ' — ']:
                if sep in query:
                    parts = query.split(sep, 1)
                    artist_name = parts[0].strip()
                    track_name = parts[1].strip()
                    break

            results = self._client.search_recording(track_name, artist_name=artist_name, limit=limit)

            # If no separator found or structured search failed, try the full query
            # as both a recording search and an artist+recording combined search
            if not results and not artist_name:
                # Try each word split as potential artist/title boundary
                words = query.split()
                for i in range(1, len(words)):
                    possible_artist = ' '.join(words[:i])
                    possible_track = ' '.join(words[i:])
                    if len(possible_track) >= 2:
                        results = self._client.search_recording(possible_track, artist_name=possible_artist, limit=limit)
                        if results:
                            break
            tracks = []
            for r in results:
                mbid = r.get('id', '')
                title = r.get('title', '')
                if not title:
                    continue

                artists = _extract_artist_credit(r.get('artist-credit', []))
                duration_ms = r.get('length', 0) or 0

                # Get album from first release
                album_name = ''
                album_id = ''
                release_date = ''
                image_url = None
                album_type = 'single'
                total_tracks = 1
                track_number = None

                releases = r.get('releases', [])
                if releases:
                    rel = releases[0]
                    album_name = rel.get('title', '')
                    album_id = rel.get('id', '')
                    release_date = rel.get('date', '') or ''

                    rg = rel.get('release-group', {})
                    primary_type = rg.get('primary-type', '') or ''
                    secondary_types = rg.get('secondary-types', []) or []
                    album_type = _map_release_type(primary_type, secondary_types)

                    media = rel.get('media', [])
                    for m in media:
                        total_tracks += m.get('track-count', 0)
                        # Find track number
                        for t in m.get('tracks', []):
                            if t.get('id') == mbid or t.get('recording', {}).get('id') == mbid:
                                try:
                                    track_number = int(t.get('number', t.get('position', 0)))
                                except (ValueError, TypeError):
                                    pass

                    # Cover art
                    rg_mbid = rg.get('id', '')
                    image_url = self._cached_art(album_id, rg_mbid) if album_id else None

                external_urls = {'musicbrainz': f'https://musicbrainz.org/recording/{mbid}'} if mbid else {}

                tracks.append(Track(
                    id=mbid,
                    name=title,
                    artists=artists if artists else ['Unknown Artist'],
                    album=album_name or title,
                    duration_ms=duration_ms,
                    popularity=r.get('score', 0),
                    image_url=image_url,
                    release_date=release_date,
                    external_urls=external_urls,
                    track_number=track_number,
                    album_type=album_type,
                    total_tracks=total_tracks,
                    album_id=album_id,
                ))
            return tracks
        except Exception as e:
            logger.warning(f"MusicBrainz track search failed: {e}")
            return []

    def get_album(self, release_mbid: str) -> Optional[Dict[str, Any]]:
        """Get full album details with track listing for download modal."""
        try:
            release = self._client.get_release(release_mbid, includes=['recordings', 'artist-credits', 'release-groups'])
            if not release:
                return None

            title = release.get('title', '')
            artists_raw = _extract_artist_credit(release.get('artist-credit', []))
            release_date = release.get('date', '') or ''

            rg = release.get('release-group', {})
            primary_type = rg.get('primary-type', '') or ''
            secondary_types = rg.get('secondary-types', []) or []
            album_type = _map_release_type(primary_type, secondary_types)

            # Cover art
            rg_mbid = rg.get('id', '')
            image_url = self._cached_art(release_mbid, rg_mbid)

            # Build tracks from media
            tracks = []
            total_tracks = 0
            media_list = release.get('media', [])
            for media_idx, media in enumerate(media_list):
                disc_number = media.get('position', media_idx + 1)
                for track in media.get('tracks', []):
                    total_tracks += 1
                    recording = track.get('recording', {})
                    track_artists = _extract_artist_credit(recording.get('artist-credit', []))
                    if not track_artists:
                        track_artists = artists_raw

                    try:
                        track_num = int(track.get('number', track.get('position', total_tracks)))
                    except (ValueError, TypeError):
                        track_num = total_tracks

                    tracks.append({
                        'id': recording.get('id', track.get('id', '')),
                        'name': recording.get('title', track.get('title', '')),
                        'artists': [{'name': a} for a in track_artists],
                        'duration_ms': recording.get('length', 0) or track.get('length', 0) or 0,
                        'track_number': track_num,
                        'disc_number': disc_number,
                    })

            images = [{'url': image_url, 'height': 250, 'width': 250}] if image_url else []

            return {
                'id': release_mbid,
                'name': title,
                'artists': [{'name': a, 'id': ''} for a in (artists_raw or ['Unknown Artist'])],
                'release_date': release_date,
                'total_tracks': total_tracks,
                'album_type': album_type,
                'images': images,
                'tracks': tracks,
                'external_urls': {'musicbrainz': f'https://musicbrainz.org/release/{release_mbid}'},
            }
        except Exception as e:
            logger.error(f"MusicBrainz album detail failed for {release_mbid}: {e}")
            return None

    def get_artist_albums(self, artist_mbid: str, album_type: str = 'album,single') -> List:
        """Get artist's releases for discography view."""
        try:
            artist = self._client.get_artist(artist_mbid, includes=['release-groups'])
            if not artist or 'release-groups' not in artist:
                return []

            albums = []
            for rg in artist.get('release-groups', []):
                primary_type = rg.get('primary-type', '') or ''
                rg_type = _map_release_type(primary_type, rg.get('secondary-types', []))

                rg_mbid = rg.get('id', '')
                image_url = self._cached_art(rg_mbid, rg_mbid)

                albums.append(Album(
                    id=rg_mbid,
                    name=rg.get('title', ''),
                    artists=[artist.get('name', 'Unknown Artist')],
                    release_date=rg.get('first-release-date', '') or '',
                    total_tracks=0,
                    album_type=rg_type,
                    image_url=image_url,
                    external_urls={'musicbrainz': f'https://musicbrainz.org/release-group/{rg_mbid}'},
                ))
            return albums
        except Exception as e:
            logger.warning(f"MusicBrainz artist albums failed: {e}")
            return []
