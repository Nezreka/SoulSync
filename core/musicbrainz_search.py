"""MusicBrainz Search Adapter — provides enhanced search tab integration.

Wraps the existing MusicBrainzClient with search methods that return the
same Track/Artist/Album dataclass format used by Deezer/iTunes/Discogs,
enabling MusicBrainz as a search tab in enhanced and global search.
Album art is fetched from Cover Art Archive (free, linked by release MBID).
"""

import threading
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


def _cover_art_url(mbid: str, scope: str = 'release') -> Optional[str]:
    """Build a Cover Art Archive URL without hitting the network.

    CAA URLs are deterministic from the MBID: the endpoint either 307-redirects
    to the image or returns 404. Previously we fired `requests.head(timeout=3)`
    per result during search — 10 results × 3s worst-case = up to 30s of
    blocking HEAD calls before a search returned. The frontend's <img> tag
    handles the 404 case via onerror fallback, so the HEAD round-trip was
    pure overhead.

    `scope` is 'release' (most specific) or 'release-group' (covers all
    editions — better hit rate).
    """
    if not mbid:
        return None
    if scope not in ('release', 'release-group'):
        scope = 'release'
    return f"{COVER_ART_ARCHIVE_URL}/{scope}/{mbid}/front-250"


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
        # Per-instance cache for "top artist MBID for this query". The
        # backend fires artists/albums/tracks searches in parallel against
        # one client instance, and albums+tracks both need the same artist
        # lookup. Without this cache, we'd fire 3 identical artist-search
        # HTTP calls (each serialized by the 1-rps rate limit = 3 wasted
        # seconds). The _Sentinel marks "we already looked and found
        # nothing" to prevent repeat no-hit lookups.
        self._artist_mbid_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._artist_mbid_lock = threading.Lock()

    def _cached_art(self, release_mbid: str, release_group_mbid: str = '') -> Optional[str]:
        """Build a Cover Art Archive URL for a release / release-group MBID.

        Prefers release-group scope when provided — better hit rate because
        it covers all editions of the same album. No network call; the
        frontend's <img onerror> fallback handles 404s.
        """
        preferred = release_group_mbid or release_mbid
        if not preferred:
            return None
        scope = 'release-group' if release_group_mbid else 'release'
        return _cover_art_url(preferred, scope=scope)

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

    def _split_structured_query(self, query: str):
        """Split 'Artist - Title' / 'Artist – Title' / 'Artist — Title' if
        a separator is present. Returns (artist_name, title) or (None, query)."""
        for sep in [' - ', ' – ', ' — ']:
            if sep in query:
                parts = query.split(sep, 1)
                return parts[0].strip(), parts[1].strip()
        return None, query

    def _resolve_top_artist(self, query: str) -> Optional[Dict[str, Any]]:
        """Return the top-scoring artist for a bare-name query, or None if
        nothing scores above threshold. Cached per instance so parallel
        album/track searches don't each refetch."""
        if not query:
            return None
        key = query.strip().lower()
        with self._artist_mbid_lock:
            if key in self._artist_mbid_cache:
                return self._artist_mbid_cache[key]
        # Do the HTTP call OUTSIDE the lock so other threads can still
        # check the cache while we wait on the network.
        raw = self._client.search_artist(query, limit=1, strict=False)
        top = None
        if raw and (raw[0].get('score', 0) or 0) >= self._MIN_SCORE:
            top = raw[0]
        with self._artist_mbid_lock:
            self._artist_mbid_cache[key] = top
        return top

    def _release_group_to_album(self, rg: Dict[str, Any], artist_name: str) -> Album:
        """Project a MusicBrainz release-group into our Album dataclass."""
        rg_mbid = rg.get('id', '')
        title = rg.get('title', '') or ''
        primary_type = rg.get('primary-type', '') or ''
        secondary_types = rg.get('secondary-types', []) or []
        album_type = _map_release_type(primary_type, secondary_types)
        release_date = rg.get('first-release-date', '') or ''
        # Release-group browse doesn't link directly to a single release,
        # so we can't get per-release track counts cheaply. Leave 0 — the
        # frontend treats it as "unknown" gracefully.
        image_url = self._cached_art(rg_mbid, rg_mbid)
        return Album(
            id=rg_mbid,
            name=title,
            artists=[artist_name] if artist_name else ['Unknown Artist'],
            release_date=release_date,
            total_tracks=0,
            album_type=album_type,
            image_url=image_url,
            external_urls={'musicbrainz': f'https://musicbrainz.org/release-group/{rg_mbid}'} if rg_mbid else {},
        )

    def search_albums(self, query: str, limit: int = 10) -> List[Album]:
        """Search MusicBrainz for releases (albums).

        Primary path: when the query looks like a bare artist name, resolve
        it to an artist MBID and BROWSE that artist's release-groups. This
        returns the artist's actual discography instead of unrelated
        releases that happen to be titled after them.

        Fallback path: when the query is structured as "Artist - Album" or
        the artist lookup fails, drop back to text search with the
        existing Lucene strategy.
        """
        try:
            artist_name, title = self._split_structured_query(query)

            # Structured "Artist - Album" query → respect user's intent;
            # text-search with both terms is more precise than browsing all
            # of that artist's discography.
            if artist_name:
                return self._search_albums_text(title, artist_name, limit)

            # Bare name query → try artist-first → browse path.
            top = self._resolve_top_artist(query)
            if top:
                mbid = top.get('id', '')
                tname = top.get('name', '') or query
                rgs = self._client.browse_artist_release_groups(
                    mbid,
                    release_types=['album', 'ep', 'single', 'compilation'],
                    limit=100,
                )
                # Sort by first-release-date desc (newest first), then by
                # primary-type priority (album > ep > single > compilation)
                # so the top of the list is a credible "what to explore."
                type_priority = {'album': 0, 'ep': 1, 'single': 2, 'compilation': 3}
                def _sort_key(rg):
                    pt = (rg.get('primary-type') or '').lower()
                    date = rg.get('first-release-date') or ''
                    return (type_priority.get(pt, 9), -int(date[:4]) if date[:4].isdigit() else 0)
                rgs.sort(key=_sort_key)
                albums = [self._release_group_to_album(rg, tname) for rg in rgs[:limit]]
                return albums

            # No artist match → text search on the whole query.
            return self._search_albums_text(query, None, limit)
        except Exception as e:
            logger.warning(f"MusicBrainz album search failed: {e}")
            return []

    def _search_albums_text(self, album_name: str, artist_name: Optional[str], limit: int) -> List[Album]:
        """Fallback text-search path for structured/fuzzy album queries."""
        try:
            results = self._client.search_release(album_name, artist_name=artist_name, limit=limit)
            # Score filter — same threshold as artists. Drops garbage
            # title-match hits from unrelated releases.
            results = [r for r in results if (r.get('score', 0) or 0) >= self._MIN_SCORE]

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

    def _recording_to_track(self, r: Dict[str, Any], fallback_artist_name: str) -> Optional[Track]:
        """Project a MusicBrainz recording into our Track dataclass. Returns
        None when the recording lacks required fields."""
        mbid = r.get('id', '')
        title = r.get('title', '')
        if not title:
            return None

        artists = _extract_artist_credit(r.get('artist-credit', []))
        if not artists and fallback_artist_name:
            artists = [fallback_artist_name]

        duration_ms = r.get('length', 0) or 0
        album_name = ''
        album_id = ''
        release_date = ''
        image_url = None
        album_type = 'single'
        total_tracks = 1

        releases = r.get('releases', []) or []
        if releases:
            rel = releases[0]
            album_name = rel.get('title', '') or ''
            album_id = rel.get('id', '') or ''
            release_date = rel.get('date', '') or ''

            rg = rel.get('release-group', {}) or {}
            primary_type = rg.get('primary-type', '') or ''
            secondary_types = rg.get('secondary-types', []) or []
            album_type = _map_release_type(primary_type, secondary_types)

            for m in rel.get('media', []) or []:
                total_tracks += m.get('track-count', 0)

            rg_mbid = rg.get('id', '') or ''
            image_url = self._cached_art(album_id, rg_mbid) if album_id else None

        return Track(
            id=mbid,
            name=title,
            artists=artists if artists else ['Unknown Artist'],
            album=album_name or title,
            duration_ms=duration_ms,
            popularity=r.get('score', 0) or 0,
            image_url=image_url,
            release_date=release_date,
            external_urls={'musicbrainz': f'https://musicbrainz.org/recording/{mbid}'} if mbid else {},
            album_type=album_type,
            total_tracks=total_tracks,
            album_id=album_id,
        )

    def search_tracks(self, query: str, limit: int = 10) -> List[Track]:
        """Search MusicBrainz for recordings (tracks).

        Same strategy as `search_albums`: bare name → artist-first → browse
        recordings; structured "Artist - Title" stays on text search so the
        user's explicit title intent is respected.
        """
        try:
            artist_name, title = self._split_structured_query(query)

            # Structured query → text search with both fields.
            if artist_name:
                return self._search_tracks_text(title, artist_name, limit)

            # Bare name → artist-first → arid: search.
            top = self._resolve_top_artist(query)
            if top:
                mbid = top.get('id', '')
                tname = top.get('name', '') or query
                # /recording?artist=<mbid> (browse) rejects inc=releases,
                # so we use the fielded Lucene search arid:<mbid> instead —
                # that returns recordings with release context inline.
                recs = self._client.search_recordings_by_artist_mbid(mbid, limit=100)
                # Browse returns recordings unsorted. Dedupe by normalized
                # title (MB has many live/compilation variants of the same
                # song), then sort by release date desc so "newest" tracks
                # surface first — matches how the other source tabs look.
                seen = set()
                deduped = []
                for r in recs:
                    key = (r.get('title') or '').lower().strip()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    deduped.append(r)

                def _track_sort_key(r):
                    rel = (r.get('releases') or [{}])[0]
                    date = (rel.get('date') or '')[:4]
                    return -int(date) if date.isdigit() else 0
                deduped.sort(key=_track_sort_key)

                tracks = []
                for r in deduped[:limit]:
                    t = self._recording_to_track(r, tname)
                    if t:
                        tracks.append(t)
                return tracks

            # No artist match → fall back to text search on whole query.
            return self._search_tracks_text(query, None, limit)
        except Exception as e:
            logger.warning(f"MusicBrainz track search failed: {e}")
            return []

    def _search_tracks_text(self, track_name: str, artist_name: Optional[str], limit: int) -> List[Track]:
        """Fallback text-search path for structured/fuzzy track queries."""
        try:
            results = self._client.search_recording(track_name, artist_name=artist_name, limit=limit)
            # Score filter matches the artist/album logic — cuts garbage
            # title collisions from unrelated recordings.
            results = [r for r in results if (r.get('score', 0) or 0) >= self._MIN_SCORE]

            tracks = []
            for r in results:
                t = self._recording_to_track(r, artist_name or '')
                if t:
                    tracks.append(t)
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
