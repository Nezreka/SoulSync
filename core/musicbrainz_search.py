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


def _extract_title_hint(query: str, artist_name: str) -> Optional[str]:
    """If `query` starts with `artist_name` followed by more words, return
    the trailing portion. Used to pick out the album/track title the user
    typed after the artist name (e.g. "The Beatles Abbey Road" → "Abbey
    Road"). Returns None when the query is just the artist name.

    Case-insensitive prefix match on whitespace-normalized versions of
    both strings, so "the beatles   abbey road" → "abbey road" and
    "The Beatles" → None.
    """
    if not query or not artist_name:
        return None
    q_norm = ' '.join(query.split()).lower()
    a_norm = ' '.join(artist_name.split()).lower()
    if q_norm == a_norm:
        return None
    # Require a word boundary between the artist name and the trailing bit.
    if q_norm.startswith(a_norm + ' '):
        return query[len(artist_name):].strip() or None
    return None


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
            # Fetch extra so dedup below has enough to pick from. For
            # common names (Michael Jackson, John Williams, etc.) MB returns
            # many same-named people; without a larger pool, capping at
            # `limit` before dedup can leave us with fewer results than
            # requested.
            raw = self._client.search_artist(query, limit=max(limit * 3, 10), strict=False)

            # Dedupe by normalized name. MusicBrainz has many different
            # people with the same canonical name (7 entries for "Michael
            # Jackson" — the singer + poet + photographer + didgeridoo
            # player + ...), all scoring 80+ on exact-name match. Rendered
            # as identical cards since the fallback image lookup hits the
            # same fallback-source result for each. Keep the highest-
            # scoring entry per normalized name so the user sees one card
            # per distinct artist.
            seen = {}
            for a in raw:
                score = a.get('score', 0) or 0
                if score < self._MIN_SCORE:
                    continue
                mbid = a.get('id', '')
                name = a.get('name', '')
                if not mbid or not name:
                    continue
                key = name.lower().strip()
                if key not in seen or (seen[key].get('score', 0) or 0) < score:
                    seen[key] = a

            # Sort the survivors score-descending and cap at the caller's
            # limit. `seen` only holds top-per-name, so ordering is stable.
            top = sorted(seen.values(), key=lambda r: -(r.get('score', 0) or 0))[:limit]

            artists = []
            for a in top:
                mbid = a.get('id', '')
                name = a.get('name', '')

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
                    popularity=a.get('score', 0) or 0,  # Reuse score as popularity (0-100)
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

    # Secondary-type tags on MB release-groups that indicate NOT a studio
    # release. Used by both the album browse (filter out) and the track
    # browse (prefer studio release for album context).
    _NON_STUDIO_SECONDARY_TYPES = {
        'Live', 'Compilation', 'Soundtrack', 'Remix', 'Demo',
        'Mixtape/Street', 'Interview', 'Audiobook', 'Audio drama',
    }

    def _release_preference_key(self, rel: Dict[str, Any]):
        """Sort key: studio releases first, then by date ASC.

        Recordings in MB often have 10+ releases (studio album, live, best-of,
        reissues, anniversary editions). The first one in the API response is
        arbitrary — it's often a recent live bootleg because MB users add new
        live recordings all the time. Re-sorting before `_recording_to_track`
        reads the first release means tracks show their canonical studio
        album, not a random live compilation.
        """
        rg = rel.get('release-group') or {}
        secs = set(rg.get('secondary-types') or [])
        is_studio = 0 if not (secs & self._NON_STUDIO_SECONDARY_TYPES) else 1
        date = (rel.get('date') or '')[:4]
        year = int(date) if date.isdigit() else 9999
        return (is_studio, year)

    def _has_studio_release(self, recording: Dict[str, Any]) -> bool:
        """True when at least one of the recording's releases is on a
        release-group with no non-studio secondary type."""
        for rel in (recording.get('releases') or []):
            rg = rel.get('release-group') or {}
            secs = set(rg.get('secondary-types') or [])
            if not (secs & self._NON_STUDIO_SECONDARY_TYPES):
                return True
        return False

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
                # If the query has words beyond the artist name (e.g. "The
                # Beatles Abbey Road"), extract the leftover as a title hint.
                # We'll use it below to narrow browse results to the specific
                # album the user typed rather than dumping the full back
                # catalogue. kettui flagged the regression — bare-name browse
                # was burying a specific-album query inside a discography list.
                title_hint = _extract_title_hint(query, tname)
                rgs = self._client.browse_artist_release_groups(
                    mbid,
                    # 'compilation' is a SECONDARY type, not a primary type
                    # — including it in the OR filter causes MB to return
                    # only 82 matches instead of the actual 1076 because
                    # the filter silently breaks. Actual compilations
                    # (primary-type=Album with secondary-types=[Compilation])
                    # are handled by the studio-preference filter below.
                    release_types=['album', 'ep', 'single'],
                    limit=100,
                )

                # Prefer studio releases — MusicBrainz tags live bootlegs
                # and best-of compilations with secondary-types. For mega-
                # artists like Metallica, 83 of 100 browse results are live
                # broadcast bootlegs; the 12 studio albums are buried. A
                # release-group with no secondary-types (or an explicit
                # studio-only type) is the "original studio" shape users
                # expect to see first.
                def _is_studio(rg):
                    secs = set((rg.get('secondary-types') or []))
                    return not (secs & {'Live', 'Compilation', 'Soundtrack',
                                         'Remix', 'Demo', 'Mixtape/Street',
                                         'Interview', 'Audiobook', 'Audio drama'})
                studio = [rg for rg in rgs if _is_studio(rg)]
                # If filtering leaves us empty (niche live-only artist),
                # fall back to the unfiltered list — better than no results.
                rgs = studio or rgs

                # Narrow to the title-hint if the user gave one ("The Beatles
                # Abbey Road" → filter to RGs whose title contains "abbey
                # road"). If no RG matches, fall back to text-search so the
                # user finds the specific album instead of either seeing the
                # full discography or getting zero results. (kettui flagged
                # this regression — artist-first alone was burying specific-
                # album queries inside the unfiltered discography list.)
                if title_hint:
                    hint_lower = title_hint.lower()
                    matched = [rg for rg in rgs if hint_lower in (rg.get('title') or '').lower()]
                    if matched:
                        rgs = matched
                    else:
                        fallback = self._search_albums_text(title_hint, tname, limit)
                        if fallback:
                            return fallback
                        # Text-search also missed — fall through and show the
                        # full (unfiltered) discography rather than nothing.

                # Sort by primary-type priority first (album > ep > single >
                # compilation), then chronologically ASC — the standard way
                # discographies are listed ("their debut was X, then Y, then Z").
                type_priority = {'album': 0, 'ep': 1, 'single': 2, 'compilation': 3}
                def _sort_key(rg):
                    pt = (rg.get('primary-type') or '').lower()
                    date = rg.get('first-release-date') or ''
                    year = int(date[:4]) if date[:4].isdigit() else 9999
                    return (type_priority.get(pt, 9), year)
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
        # Initialized to 0 and summed from the release's media track-counts.
        # Previously initialized to 1, which made every track-with-release
        # report one more than the album actually has (kettui caught this).
        total_tracks = 0

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

        # Tracks with no release info are standalone recordings — give them
        # total_tracks=1 (the track itself). Keeps the old shape for that
        # edge case but fixes the off-by-one for every normal case.
        if not releases:
            total_tracks = 1

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

                # Re-order each recording's releases to prefer studio over
                # live/compilation. Without this, the first release (which
                # the adapter uses for album info + date) is often a random
                # live bootleg — Metallica has 10+ live versions of "One"
                # ranked ahead of the studio release. Mutates in place so
                # `_recording_to_track` sees the preferred release first.
                for r in recs:
                    rels = r.get('releases') or []
                    if not rels:
                        continue
                    rels.sort(key=self._release_preference_key)
                    r['releases'] = rels

                # Prefer recordings that have at least one studio release.
                # Falls back to the full set if the artist is live-only.
                studio = [r for r in recs if self._has_studio_release(r)]
                recs = studio or recs

                # Dedupe by normalized title (MB has many versions of the
                # same song — live, remaster, re-recording, etc.). Because
                # we sorted releases above, `_recording_to_track` will pick
                # the studio release for album info on the first keeper.
                seen = set()
                deduped = []
                for r in recs:
                    key = (r.get('title') or '').lower().strip()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    deduped.append(r)

                # Sort by studio-release year ASC so classic tracks surface
                # first. For a user typing "metallica", this means "Seek
                # and Destroy" (1983) before "Atlas, Rise!" (2016) — which
                # matches how most discography views order by release.
                def _track_sort_key(r):
                    rels = r.get('releases') or []
                    for rel in rels:
                        date = (rel.get('date') or '')[:4]
                        if date.isdigit():
                            return int(date)
                    return 9999
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

    def _pick_representative_release(self, releases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Pick the best release out of a release-group's editions.

        Release-groups often contain 5-20+ releases (original, reissues,
        remasters, regional editions, bonus-track editions). We want a
        single canonical version to show the user as 'the album.' Prefer:
        1. Official releases (not promo/bootleg)
        2. Earliest date (the original)
        3. Any release with media (skip entries that are just stubs)
        """
        if not releases:
            return None

        def _key(r):
            status = (r.get('status') or '').lower()
            status_rank = 0 if status == 'official' else 1  # Official first
            has_media = 0 if r.get('media') else 1  # Real tracklists first
            date = (r.get('date') or '9999-99-99')[:10]
            return (has_media, status_rank, date)

        return sorted(releases, key=_key)[0]

    def get_album(self, album_mbid: str) -> Optional[Dict[str, Any]]:
        """Get full album details with track listing for download modal.

        The MBID passed in could be either:
        - A release-group MBID (from `search_albums` browse path — the
          common case now that bare-name searches route artist-first →
          browse), or
        - A release MBID (from the text-search fallback path).

        Try release-group first since that's the majority; if it 404s,
        fall back to direct release lookup. Release-group resolution adds
        one extra API call (~1s at the 1-rps rate limit) to pick a
        representative release and then fetch its tracklist.
        """
        try:
            # Path A: release-group MBID (new browse-based search default)
            rg = self._client.get_release_group(
                album_mbid, includes=['releases', 'artist-credits']
            )
            if rg:
                releases = rg.get('releases') or []
                rep = self._pick_representative_release(releases)
                if rep and rep.get('id'):
                    album = self._render_release_as_album(
                        rep['id'],
                        rg_fallback=rg,
                    )
                    if album:
                        # Keep the release-group MBID as the canonical
                        # Album.id so downstream code can re-fetch with
                        # the same URL.
                        album['id'] = album_mbid
                        album['external_urls'] = {
                            'musicbrainz': f'https://musicbrainz.org/release-group/{album_mbid}'
                        }
                        return album

            # Path B: release MBID (text-search fallback path)
            return self._render_release_as_album(album_mbid)
        except Exception as e:
            logger.error(f"MusicBrainz album detail failed for {album_mbid}: {e}")
            return None

    def _render_release_as_album(self, release_mbid: str,
                                  rg_fallback: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Fetch a specific release and project it to the album-detail dict
        shape the download modal expects. `rg_fallback` supplies release-group
        metadata (type, artist credits) when resolving from a release-group
        whose releases may be lightly populated."""
        release = self._client.get_release(
            release_mbid, includes=['recordings', 'artist-credits', 'release-groups']
        )
        if not release:
            return None

        title = release.get('title', '')
        artists_raw = _extract_artist_credit(release.get('artist-credit', []))
        if not artists_raw and rg_fallback:
            artists_raw = _extract_artist_credit(rg_fallback.get('artist-credit', []))
        release_date = release.get('date', '') or ''
        if not release_date and rg_fallback:
            release_date = rg_fallback.get('first-release-date', '') or ''

        rg = release.get('release-group', rg_fallback or {}) or {}
        primary_type = rg.get('primary-type', '') or ''
        secondary_types = rg.get('secondary-types', []) or []
        album_type = _map_release_type(primary_type, secondary_types)

        rg_mbid = rg.get('id', '')
        image_url = self._cached_art(release_mbid, rg_mbid)

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
