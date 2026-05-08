#!/usr/bin/env python3

"""
Personalized Playlists Service - Creates Spotify-quality personalized playlists
from user's library and discovery pool
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter
import random
import json
from utils.logging_config import get_logger

logger = get_logger("personalized_playlists")

class PersonalizedPlaylistsService:
    """Service for generating personalized playlists from library and discovery pool"""

    # Genre consolidation mapping - maps specific Spotify genres to broad parent categories
    GENRE_MAPPING = {
        'Electronic/Dance': [
            'house', 'techno', 'trance', 'edm', 'electro', 'dubstep', 'drum and bass',
            'breakbeat', 'jungle', 'dnb', 'bass', 'garage', 'uk garage', 'future bass',
            'trap', 'hardstyle', 'hardcore', 'rave', 'dance', 'electronic', 'electronica',
            'synth', 'downtempo', 'chillwave', 'vaporwave', 'synthwave', 'idm', 'glitch'
        ],
        'Hip Hop/Rap': [
            'hip hop', 'rap', 'trap', 'drill', 'grime', 'boom bap', 'underground hip hop',
            'conscious hip hop', 'gangsta rap', 'southern hip hop', 'east coast', 'west coast',
            'crunk', 'hyphy', 'cloud rap', 'emo rap', 'mumble rap'
        ],
        'Rock': [
            'rock', 'alternative rock', 'indie rock', 'garage rock', 'post-punk', 'punk',
            'hard rock', 'psychedelic rock', 'progressive rock', 'art rock', 'glam rock',
            'blues rock', 'southern rock', 'surf rock', 'rockabilly', 'grunge', 'shoegaze',
            'noise rock', 'post-rock', 'math rock', 'emo', 'screamo'
        ],
        'Pop': [
            'pop', 'dance pop', 'electropop', 'synth pop', 'indie pop', 'chamber pop',
            'art pop', 'baroque pop', 'dream pop', 'power pop', 'bubblegum pop', 'k-pop',
            'j-pop', 'hyperpop', 'pop rock', 'teen pop'
        ],
        'R&B/Soul': [
            'r&b', 'soul', 'neo soul', 'contemporary r&b', 'alternative r&b', 'funk',
            'disco', 'motown', 'northern soul', 'quiet storm', 'new jack swing'
        ],
        'Jazz': [
            'jazz', 'bebop', 'cool jazz', 'hard bop', 'modal jazz', 'free jazz',
            'fusion', 'jazz fusion', 'smooth jazz', 'contemporary jazz', 'latin jazz',
            'afro-cuban jazz', 'swing', 'big band', 'ragtime', 'dixieland'
        ],
        'Classical': [
            'classical', 'baroque', 'romantic', 'contemporary classical', 'minimalism',
            'opera', 'orchestral', 'chamber music', 'choral', 'renaissance', 'medieval'
        ],
        'Metal': [
            'metal', 'heavy metal', 'thrash metal', 'death metal', 'black metal',
            'doom metal', 'power metal', 'progressive metal', 'metalcore', 'deathcore',
            'djent', 'nu metal', 'industrial metal', 'symphonic metal', 'gothic metal'
        ],
        'Country': [
            'country', 'bluegrass', 'americana', 'outlaw country', 'country rock',
            'alt-country', 'contemporary country', 'traditional country', 'honky tonk',
            'western', 'nashville sound'
        ],
        'Folk/Indie': [
            'folk', 'indie folk', 'folk rock', 'freak folk', 'anti-folk', 'singer-songwriter',
            'acoustic', 'indie', 'lo-fi', 'bedroom pop', 'slowcore', 'sadcore'
        ],
        'Latin': [
            'latin', 'reggaeton', 'salsa', 'bachata', 'merengue', 'cumbia', 'banda',
            'regional mexican', 'mariachi', 'ranchera', 'corrido', 'latin pop',
            'latin trap', 'urbano latino', 'bossa nova', 'samba', 'tango'
        ],
        'Reggae/Dancehall': [
            'reggae', 'dancehall', 'dub', 'roots reggae', 'ska', 'rocksteady',
            'lovers rock', 'reggae fusion'
        ],
        'World': [
            'afrobeat', 'afropop', 'african', 'world', 'worldbeat', 'ethnic',
            'traditional', 'folk music', 'celtic', 'klezmer', 'flamenco', 'fado',
            'indian classical', 'raga', 'qawwali', 'k-indie', 'j-indie'
        ],
        'Alternative': [
            'alternative', 'experimental', 'avant-garde', 'noise', 'ambient',
            'industrial', 'new wave', 'no wave', 'gothic', 'darkwave', 'coldwave',
            'witch house', 'trip hop', 'downtempo'
        ],
        'Blues': [
            'blues', 'delta blues', 'chicago blues', 'electric blues', 'blues rock',
            'rhythm and blues', 'soul blues', 'gospel blues'
        ],
        'Funk/Disco': [
            'funk', 'disco', 'p-funk', 'boogie', 'electro-funk', 'g-funk'
        ]
    }

    def __init__(self, database, spotify_client=None):
        self.database = database
        self.spotify_client = spotify_client

    def _get_active_source(self) -> str:
        """Determine which music source is active — delegates to centralized metadata_service."""
        from core.metadata_service import get_primary_source
        return get_primary_source()

    # Standard column set returned by every discovery_pool selector.
    # Callers can request additional columns via the `extra_columns` parameter
    # of `_select_discovery_tracks` (e.g. `release_date`, `artist_genres`).
    _STANDARD_DISCOVERY_COLUMNS: Tuple[str, ...] = (
        'spotify_track_id',
        'itunes_track_id',
        'deezer_track_id',
        'track_name',
        'artist_name',
        'album_name',
        'album_cover_url',
        'duration_ms',
        'popularity',
        'track_data_json',
        'source',
    )

    def _select_discovery_tracks(
        self,
        *,
        source: str,
        extra_where: str = "",
        extra_params: tuple = (),
        order_by: str = "RANDOM()",
        fetch_limit: int,
        extra_columns: tuple = (),
    ) -> List[Dict]:
        """
        Shared selector for discovery_pool playlist methods.

        Builds and runs a SELECT against `discovery_pool` with a baked-in
        ID-validity gate so callers cannot accidentally return rows with no
        usable source IDs (which would fail downstream when the user clicks
        download).

        The WHERE clause always includes:
            source = ?
            AND (spotify_track_id IS NOT NULL OR itunes_track_id IS NOT NULL OR deezer_track_id IS NOT NULL)
            AND LOWER(artist_name) NOT IN
                (SELECT LOWER(artist_name) FROM discovery_artist_blacklist)

        The ID gate is mandatory and not opt-out by design — if a future
        method needs to skip it, that's a design discussion, not a flag.

        Callers compose additional filters via `extra_where` (a SQL fragment
        beginning with "AND ...") and `extra_params` (positional bindings for
        any `?` placeholders inside `extra_where`).

        Diversity filtering is the caller's responsibility — apply
        `_apply_diversity_filter` to the returned list if needed.

        Args:
            source: discovery_pool.source value to filter on.
            extra_where: optional SQL fragment appended to the WHERE clause.
                Must start with "AND " if non-empty.
            extra_params: positional bindings for `?` placeholders in
                `extra_where`.
            order_by: ORDER BY expression, used as-is (e.g. "RANDOM()",
                "popularity DESC, RANDOM()").
            fetch_limit: LIMIT applied to the query. Callers that intend to
                run a diversity filter should over-fetch (e.g. `limit * 3`).
            extra_columns: additional columns to SELECT beyond
                `_STANDARD_DISCOVERY_COLUMNS` (e.g. `('release_date',)`).

        Returns:
            List of track dicts via `_build_track_dict`. Returns `[]` on any
            error (logged at error level).
        """
        try:
            columns = self._STANDARD_DISCOVERY_COLUMNS + tuple(extra_columns)
            select_cols = ",\n                        ".join(columns)

            query = f"""
                SELECT
                        {select_cols}
                FROM discovery_pool
                WHERE source = ?
                  AND (spotify_track_id IS NOT NULL OR itunes_track_id IS NOT NULL OR deezer_track_id IS NOT NULL)
                  AND LOWER(artist_name) NOT IN (SELECT LOWER(artist_name) FROM discovery_artist_blacklist)
                  {extra_where}
                ORDER BY {order_by}
                LIMIT ?
            """

            params = (source,) + tuple(extra_params) + (fetch_limit,)

            with self.database._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()

            return [self._build_track_dict(row, source) for row in rows]

        except Exception as e:
            logger.error(f"Error in _select_discovery_tracks (source={source}): {e}")
            return []

    def _apply_diversity_filter(
        self,
        tracks: List[Dict],
        *,
        max_per_album: int,
        max_per_artist: int,
        limit: int,
    ) -> List[Dict]:
        """
        Apply per-album / per-artist diversity caps to a track list.

        Iterates `tracks` in order, accepting each only if its album count
        is still under `max_per_album` AND its artist count is still under
        `max_per_artist`. Stops once `limit` tracks have been accepted.

        Returns a new list, already trimmed to at most `limit` items.
        """
        tracks_by_album: Dict[str, int] = {}
        tracks_by_artist: Dict[str, int] = {}
        diverse_tracks: List[Dict] = []

        for track in tracks:
            album = track['album_name']
            artist = track['artist_name']

            album_count = tracks_by_album.get(album, 0)
            artist_count = tracks_by_artist.get(artist, 0)

            if album_count < max_per_album and artist_count < max_per_artist:
                diverse_tracks.append(track)
                tracks_by_album[album] = album_count + 1
                tracks_by_artist[artist] = artist_count + 1

                if len(diverse_tracks) >= limit:
                    break

        return diverse_tracks

    def _compute_adaptive_diversity_limits(
        self,
        tracks: List[Dict],
        *,
        relaxed: bool = False,
    ) -> Tuple[int, int]:
        """
        Pick (max_per_album, max_per_artist) caps based on artist variety.

        Mirrors the step-functions previously inlined in the decade and
        genre playlist methods. With more unique artists we can be strict;
        with fewer we relax to still hit the requested track count.

        When `relaxed=True` (used for genre playlists), the moderate and
        low bands use looser caps and an extra "very limited" tier kicks
        in below 5 unique artists.

        Args:
            tracks: candidate track list to inspect.
            relaxed: True to apply the looser genre-playlist limits.

        Returns:
            Tuple of (max_per_album, max_per_artist).
        """
        unique_artists = len(set(t['artist_name'] for t in tracks))

        if relaxed:
            # Genre playlist tiers
            if unique_artists >= 20:
                return 3, 5
            if unique_artists >= 10:
                return 4, 10
            if unique_artists >= 5:
                return 6, 15
            return 8, 25

        # Decade-style strict tiers
        if unique_artists >= 20:
            return 3, 5
        if unique_artists >= 10:
            return 4, 8
        return 5, 12

    def _build_track_dict(self, row, source: str) -> Dict:
        """Build a standardized track dictionary from a database row.

        If the row carries the optional `artist_genres` column (selected via
        `_select_discovery_tracks(extra_columns=('artist_genres',))`), the raw
        JSON string is passed through under `_artist_genres_raw` so callers
        can run Python-side genre matching without re-querying the row.
        """
        # Convert sqlite3.Row to dict if needed (Row objects don't support .get())
        if hasattr(row, 'keys'):
            row = dict(row)

        track_data = row.get('track_data_json')
        if isinstance(track_data, str):
            try:
                track_data = json.loads(track_data)
            except:
                track_data = None

        result = {
            'track_id': row.get('spotify_track_id') or row.get('itunes_track_id') or row.get('deezer_track_id'),
            'spotify_track_id': row.get('spotify_track_id'),
            'itunes_track_id': row.get('itunes_track_id'),
            'deezer_track_id': row.get('deezer_track_id'),
            'track_name': row.get('track_name', 'Unknown'),
            'artist_name': row.get('artist_name', 'Unknown'),
            'album_name': row.get('album_name', 'Unknown'),
            'album_cover_url': row.get('album_cover_url'),
            'duration_ms': row.get('duration_ms', 0),
            'popularity': row.get('popularity', 0),
            'track_data_json': track_data,
            'source': source,
        }

        # Pass through optional extra columns under underscore-prefixed keys
        # so callers that requested them via `extra_columns` can use them
        # without having to re-query.
        if 'artist_genres' in row:
            result['_artist_genres_raw'] = row.get('artist_genres')
        if 'release_date' in row:
            result['_release_date'] = row.get('release_date')

        return result

    @staticmethod
    def get_parent_genre(spotify_genre: str) -> str:
        """
        Map a specific Spotify genre to its parent category.
        Returns the parent genre or 'Other' if no match found.
        """
        spotify_genre_lower = spotify_genre.lower()

        for parent_genre, keywords in PersonalizedPlaylistsService.GENRE_MAPPING.items():
            for keyword in keywords:
                if keyword in spotify_genre_lower:
                    return parent_genre

        return 'Other'

    def get_decade_playlist(self, decade: int, limit: int = 100, source: str = None) -> List[Dict]:
        """
        Get tracks from a specific decade from discovery pool with diversity filtering.

        Args:
            decade: Decade year (e.g., 2020 for 2020s, 2010 for 2010s)
            limit: Maximum tracks to return
            source: Optional source filter ('spotify' or 'itunes'), auto-detects if not provided
        """
        start_year = decade
        end_year = decade + 9
        active_source = source or self._get_active_source()

        # Over-fetch 10x for diversity filtering headroom.
        all_tracks = self._select_discovery_tracks(
            source=active_source,
            extra_where=(
                "AND release_date IS NOT NULL "
                "AND CAST(SUBSTR(release_date, 1, 4) AS INTEGER) BETWEEN ? AND ?"
            ),
            extra_params=(start_year, end_year),
            order_by="RANDOM()",
            fetch_limit=limit * 10,
            extra_columns=('release_date',),
        )

        if not all_tracks:
            logger.warning(f"No tracks found for {decade}s")
            return []

        random.shuffle(all_tracks)

        max_per_album, max_per_artist = self._compute_adaptive_diversity_limits(all_tracks)
        unique_artists = len(set(t['artist_name'] for t in all_tracks))
        logger.info(
            f"{decade}s has {unique_artists} unique artists - using limits: "
            f"{max_per_album} per album, {max_per_artist} per artist"
        )

        diverse_tracks = self._apply_diversity_filter(
            all_tracks,
            max_per_album=max_per_album,
            max_per_artist=max_per_artist,
            limit=limit,
        )

        logger.info(f"Found {len(diverse_tracks)} tracks from {decade}s in discovery pool (adaptive diversity)")
        return diverse_tracks

    def get_available_genres(self, source: str = None) -> List[Dict]:
        """
        Get list of consolidated parent genres with track counts from discovery pool.
        Uses cached artist genres from database (populated during discovery scan).
        Consolidates specific Spotify genres into broader parent categories.
        """
        try:
            # Determine active source if not specified
            active_source = source or self._get_active_source()

            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Get all tracks with genres from discovery pool, filtered by source
                cursor.execute("""
                    SELECT artist_genres
                    FROM discovery_pool
                    WHERE artist_genres IS NOT NULL AND source = ?
                """, (active_source,))
                rows = cursor.fetchall()

                if not rows:
                    logger.warning(f"No genres found in discovery pool for source {active_source}")
                    return []

                # Count tracks per PARENT genre (consolidated)
                parent_genre_track_count = {}  # {parent_genre: count}

                for row in rows:
                    try:
                        artist_genres_json = row[0]
                        if artist_genres_json:
                            genres = json.loads(artist_genres_json)
                            # Map each Spotify genre to parent and count tracks
                            mapped_parents = set()  # Use set to avoid double-counting per track
                            for genre in genres:
                                parent_genre = self.get_parent_genre(genre)
                                mapped_parents.add(parent_genre)

                            # Add this track to all parent genres
                            for parent_genre in mapped_parents:
                                parent_genre_track_count[parent_genre] = parent_genre_track_count.get(parent_genre, 0) + 1
                    except Exception as e:
                        logger.debug(f"Error parsing genres JSON: {e}")
                        continue

                # Filter genres with at least 10 tracks and sort by count
                # Exclude 'Other' category
                available_genres = [
                    {'name': genre, 'track_count': count}
                    for genre, count in parent_genre_track_count.items()
                    if count >= 10 and genre != 'Other'
                ]
                available_genres.sort(key=lambda x: x['track_count'], reverse=True)

                logger.info(f"Found {len(available_genres)} consolidated genres with 10+ tracks")
                return available_genres[:20]  # Top 20 parent genres

        except Exception as e:
            logger.error(f"Error getting available genres: {e}")
            return []

    def _genre_matches(self, artist_genres_json: Optional[str], search_keywords: List[str]) -> bool:
        """Return True if any artist genre in the JSON-encoded column matches any keyword."""
        if not artist_genres_json:
            return False
        try:
            genres = json.loads(artist_genres_json)
        except Exception:
            return False
        for artist_genre in genres:
            artist_genre_lower = artist_genre.lower()
            for keyword in search_keywords:
                if keyword in artist_genre_lower:
                    return True
        return False

    def get_genre_playlist(self, genre: str, limit: int = 50, source: str = None) -> List[Dict]:
        """
        Get tracks from a specific genre with diversity filtering.
        Uses cached artist genres from database (populated during discovery scan).
        Supports both parent genres (e.g., "Electronic/Dance") and specific genres (e.g., "house").

        The genre keyword match runs Python-side over the JSON-encoded artist_genres
        column, so this method overfetches via the shared selector then filters.
        """
        active_source = source or self._get_active_source()

        # Build keyword list: parent genre expands to all child keywords;
        # specific genre uses its own name for partial matching.
        if genre in self.GENRE_MAPPING:
            search_keywords = [k.lower() for k in self.GENRE_MAPPING[genre]]
            logger.info(f"Matching parent genre '{genre}' with {len(search_keywords)} child keywords")
        else:
            search_keywords = [genre.lower()]
            logger.info(f"Matching specific genre '{genre}' with partial matching")

        # Pull every source row with non-null artist_genres through the shared
        # selector (gets the ID gate + blacklist filter for free). Cap at a high
        # bound — the Python keyword filter narrows the result drastically.
        candidate_tracks = self._select_discovery_tracks(
            source=active_source,
            extra_where="AND artist_genres IS NOT NULL",
            order_by="RANDOM()",
            fetch_limit=1_000_000,
            extra_columns=('artist_genres',),
        )

        if not candidate_tracks:
            logger.warning(f"No tracks with genre data found for source: {active_source}")
            return []

        # `_build_track_dict` stashes the raw `artist_genres` column under
        # `_artist_genres_raw` (since we requested it via `extra_columns`),
        # so the keyword match can run without re-querying.
        matching_tracks = [
            track for track in candidate_tracks
            if self._genre_matches(track.get('_artist_genres_raw'), search_keywords)
        ]

        if not matching_tracks:
            logger.warning(f"No tracks found for genre: {genre}")
            return []

        random.shuffle(matching_tracks)

        # Cap candidate set at 10x limit for diversity filtering headroom.
        all_tracks = matching_tracks[:limit * 10] if len(matching_tracks) > limit * 10 else matching_tracks

        max_per_album, max_per_artist = self._compute_adaptive_diversity_limits(all_tracks, relaxed=True)
        unique_artists = len(set(t['artist_name'] for t in all_tracks))
        logger.info(
            f"Genre '{genre}' has {unique_artists} artists, {len(all_tracks)} total tracks - "
            f"limits: {max_per_album}/album, {max_per_artist}/artist"
        )

        random.shuffle(all_tracks)
        diverse_tracks = self._apply_diversity_filter(
            all_tracks,
            max_per_album=max_per_album,
            max_per_artist=max_per_artist,
            limit=limit,
        )

        logger.info(f"Found {len(diverse_tracks)} tracks for genre '{genre}'")
        return diverse_tracks

    # ========================================
    # DISCOVERY POOL PLAYLISTS
    # ========================================

    def get_popular_picks(self, limit: int = 50) -> List[Dict]:
        """Get high popularity tracks from discovery pool with diversity (max 2 per album, 3 per artist)."""
        active_source = self._get_active_source()

        # Over-fetch 3x so the diversity filter has room to spread albums/artists.
        all_tracks = self._select_discovery_tracks(
            source=active_source,
            extra_where="AND popularity >= 60",
            order_by="popularity DESC, RANDOM()",
            fetch_limit=limit * 3,
        )

        diverse_tracks = self._apply_diversity_filter(
            all_tracks,
            max_per_album=2,
            max_per_artist=3,
            limit=limit,
        )

        logger.info(f"Popular Picks ({active_source}): selected {len(diverse_tracks)} tracks with diversity")
        return diverse_tracks

    def get_hidden_gems(self, limit: int = 50) -> List[Dict]:
        """Get low-popularity (underground/indie) tracks from discovery pool."""
        active_source = self._get_active_source()
        tracks = self._select_discovery_tracks(
            source=active_source,
            extra_where="AND popularity < 40",
            order_by="RANDOM()",
            fetch_limit=limit,
        )
        logger.info(f"Hidden Gems ({active_source}): selected {len(tracks)} tracks")
        return tracks

    def get_discovery_shuffle(self, limit: int = 50) -> List[Dict]:
        """
        Get random tracks from discovery pool - pure exploration.

        Different every time you call it!
        """
        active_source = self._get_active_source()
        tracks = self._select_discovery_tracks(
            source=active_source,
            order_by="RANDOM()",
            fetch_limit=limit,
        )
        logger.info(f"Discovery Shuffle ({active_source}): selected {len(tracks)} tracks")
        return tracks

    # ========================================
    # DAILY MIX (HYBRID PLAYLISTS)
    # ========================================

    def get_top_genres_from_library(self, limit: int = 5) -> List[Tuple[str, int]]:
        """
        Get top genres from user's library by track count.

        Returns: List of (genre_name, track_count) tuples
        """
        try:
            # Get all genres from library tracks
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                # Try to get genres from tracks or albums
                cursor.execute("PRAGMA table_info(tracks)")
                columns = [row['name'] for row in cursor.fetchall()]

                if 'genres' in columns:
                    # Get genres directly from tracks
                    cursor.execute("""
                        SELECT genres FROM tracks WHERE genres IS NOT NULL
                    """)
                    rows = cursor.fetchall()

                    # Parse genres (assuming JSON array or comma-separated)
                    all_genres = []
                    for row in rows:
                        genres_str = row['genres']
                        if genres_str:
                            # Try JSON parse first
                            try:
                                import json
                                genres = json.loads(genres_str)
                                all_genres.extend(genres)
                            except:
                                # Fallback to comma-separated
                                genres = [g.strip() for g in genres_str.split(',')]
                                all_genres.extend(genres)

                    # Count genres
                    genre_counts = Counter(all_genres)
                    return genre_counts.most_common(limit)
                else:
                    # Fallback: use artist names as "genres"
                    logger.warning("No genres column - using top artists as categories")
                    cursor.execute("""
                        SELECT ar.name, COUNT(*) as count
                        FROM tracks t
                        LEFT JOIN artists ar ON t.artist_id = ar.id
                        WHERE ar.name IS NOT NULL
                        GROUP BY ar.name
                        ORDER BY count DESC
                        LIMIT ?
                    """, (limit,))

                    rows = cursor.fetchall()
                    return [(row['name'], row['count']) for row in rows]

        except Exception as e:
            logger.error(f"Error getting top genres: {e}")
            return []

    def create_daily_mix(self, genre_or_artist: str, mix_number: int = 1) -> Dict[str, Any]:
        """
        Create a Daily Mix playlist - hybrid of library + discovery pool.

        Strategy:
        - 50% tracks from user's library matching genre/artist
        - 50% tracks from discovery pool matching genre/artist

        Args:
            genre_or_artist: Genre name or artist name to base mix on
            mix_number: Mix number (1, 2, 3, etc.)

        Returns:
            Dict with playlist metadata and tracks
        """
        try:
            logger.info(f"Creating Daily Mix #{mix_number} for: {genre_or_artist}")

            mix_size = 50
            library_portion = mix_size // 2  # 25 tracks
            discovery_portion = mix_size - library_portion  # 25 tracks

            # Get tracks from library
            library_tracks = self._get_library_tracks_by_category(genre_or_artist, library_portion)

            # Get tracks from discovery pool
            discovery_tracks = self._get_discovery_tracks_by_category(genre_or_artist, discovery_portion)

            # Combine and shuffle
            all_tracks = library_tracks + discovery_tracks
            random.shuffle(all_tracks)

            return {
                'mix_number': mix_number,
                'name': f"Daily Mix {mix_number}",
                'description': f"{genre_or_artist} mix",
                'category': genre_or_artist,
                'track_count': len(all_tracks),
                'tracks': all_tracks
            }

        except Exception as e:
            logger.error(f"Error creating daily mix: {e}")
            return {
                'mix_number': mix_number,
                'name': f"Daily Mix {mix_number}",
                'description': 'Mix',
                'category': genre_or_artist,
                'track_count': 0,
                'tracks': []
            }

    def _get_library_tracks_by_category(self, category: str, limit: int) -> List[Dict]:
        """
        Get tracks from library matching genre or artist

        NOTE: This requires library tracks to have Spotify metadata which may not be available.
        Returns empty list if schema incompatible.
        """
        try:
            logger.warning("Library tracks by category requires Spotify-linked library - returning empty")
            return []

        except Exception as e:
            logger.error(f"Error getting library tracks by category: {e}")
            return []

    def _get_discovery_tracks_by_category(self, category: str, limit: int) -> List[Dict]:
        """Get tracks from discovery pool matching genre or artist"""
        # Determine active source
        active_source = self._get_active_source()

        try:
            with self.database._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT
                        spotify_track_id,
                        itunes_track_id,
                        track_name,
                        artist_name,
                        album_name,
                        album_cover_url,
                        duration_ms,
                        popularity,
                        track_data_json,
                        source
                    FROM discovery_pool
                    WHERE (artist_name LIKE ? OR track_name LIKE ?) AND source = ?
                      AND LOWER(artist_name) NOT IN (SELECT LOWER(artist_name) FROM discovery_artist_blacklist)
                    ORDER BY RANDOM()
                    LIMIT ?
                """, (f'%{category}%', f'%{category}%', active_source, limit))

                rows = cursor.fetchall()
                return [self._build_track_dict(row, active_source) for row in rows]

        except Exception as e:
            logger.error(f"Error getting discovery tracks by category: {e}")
            return []

    def get_all_daily_mixes(self, max_mixes: int = 4) -> List[Dict]:
        """
        Generate multiple Daily Mix playlists based on top genres/artists.

        Args:
            max_mixes: Maximum number of mixes to generate (default: 4)

        Returns:
            List of daily mix dictionaries
        """
        try:
            # Get top categories (genres or artists)
            top_categories = self.get_top_genres_from_library(limit=max_mixes)

            if not top_categories:
                logger.warning("No categories found for Daily Mixes")
                return []

            daily_mixes = []
            for i, (category, _count) in enumerate(top_categories, 1):
                mix = self.create_daily_mix(category, mix_number=i)
                if mix['track_count'] > 0:
                    daily_mixes.append(mix)

            logger.info(f"Created {len(daily_mixes)} Daily Mixes")
            return daily_mixes

        except Exception as e:
            logger.error(f"Error getting all daily mixes: {e}")
            return []

    # ========================================
    # BUILD A PLAYLIST (CUSTOM GENERATOR)
    # ========================================

    def build_custom_playlist(self, seed_artist_ids: List[str], playlist_size: int = 50) -> Dict[str, Any]:
        """
        Build a custom playlist from seed artists.

        Process:
        1. Get similar artists for each seed artist (max 25 total)
        2. Get albums from those similar artists
        3. Select 20 random albums
        4. Build playlist from tracks in those albums (max 50 tracks)

        Args:
            seed_artist_ids: List of 1-5 artist IDs (Spotify or iTunes)
            playlist_size: Maximum tracks in final playlist (default: 50)

        Returns:
            Dict with playlist metadata and tracks
        """
        try:
            if not seed_artist_ids or len(seed_artist_ids) > 5:
                logger.error(f"Invalid seed artists count: {len(seed_artist_ids)}")
                return {'tracks': [], 'error': 'Must provide 1-5 seed artists'}

            active_source = self._get_active_source()
            use_spotify = (active_source == 'spotify') and self.spotify_client and self.spotify_client.sp
            logger.info(f"Building custom playlist from {len(seed_artist_ids)} seed artists (source: {active_source})")

            # Step 1: Get similar artists for each seed
            all_similar_artists = []
            seen_artist_ids = set(seed_artist_ids)

            for seed_artist_id in seed_artist_ids:
                try:
                    # Try database first (cached from MusicMap/watchlist scans)
                    db_results = []
                    with self.database._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT similar_artist_spotify_id, similar_artist_name
                            FROM similar_artists
                            WHERE source_artist_id = ?
                            ORDER BY similarity_rank ASC
                            LIMIT 10
                        """, (seed_artist_id,))
                        db_results = cursor.fetchall()

                    if db_results:
                        for row in db_results:
                            artist_id = row['similar_artist_spotify_id']
                            artist_name = row['similar_artist_name']
                            if artist_id and artist_id not in seen_artist_ids:
                                all_similar_artists.append({'id': artist_id, 'name': artist_name})
                                seen_artist_ids.add(artist_id)
                                if len(all_similar_artists) >= 25:
                                    break
                    elif self.spotify_client and self.spotify_client.sp:
                        # Fallback: fetch related artists from Spotify API (no Deezer/iTunes equivalent)
                        logger.info(f"No cached similar artists for {seed_artist_id}, trying Spotify related artists API")
                        try:
                            related = self.spotify_client.sp.artist_related_artists(seed_artist_id)
                            if related and 'artists' in related:
                                for artist in related['artists'][:10]:
                                    artist_id = artist['id']
                                    if artist_id not in seen_artist_ids:
                                        all_similar_artists.append({'id': artist_id, 'name': artist['name']})
                                        seen_artist_ids.add(artist_id)
                                        if len(all_similar_artists) >= 25:
                                            break
                        except Exception as e2:
                            logger.warning(f"Spotify related artists fallback failed for {seed_artist_id}: {e2}")

                    if len(all_similar_artists) >= 25:
                        break

                except Exception as e:
                    logger.warning(f"Error getting similar artists for {seed_artist_id}: {e}")
                    continue

            logger.info(f"Found {len(all_similar_artists)} similar artists")

            # Always include seed artists alongside similar artists
            # so the playlist has tracks from both the selected and discovered artists
            artists_for_albums = [{'id': sid, 'name': '', 'is_seed': True} for sid in seed_artist_ids]
            for sa in all_similar_artists[:22]:  # Cap similar to leave room for seeds
                artists_for_albums.append({**sa, 'is_seed': False})

            # Step 2: Get albums from seed + similar artists
            all_albums = []
            if use_spotify:
                for artist in artists_for_albums:
                    try:
                        albums = self.spotify_client.get_artist_albums(
                            artist['id'],
                            album_type='album,single',
                            limit=10
                        )
                        if albums:
                            all_albums.extend(albums)
                        import time
                        time.sleep(0.3)
                    except Exception as e:
                        logger.warning(f"Error getting albums for {artist.get('name', artist['id'])}: {e}")
                        continue
            else:
                from core.metadata_service import get_primary_client
                itunes = get_primary_client()
                for artist in artists_for_albums:
                    try:
                        albums = itunes.get_artist_albums(artist['id'], limit=10)
                        if albums:
                            all_albums.extend(albums)
                        import time
                        time.sleep(0.3)
                    except Exception as e:
                        logger.warning(f"Error getting albums for {artist.get('name', artist['id'])}: {e}")
                        continue

            logger.info(f"Found {len(all_albums)} total albums")

            if not all_albums:
                return {'tracks': [], 'error': 'No albums found for the selected artists'}

            # Step 3: Select 20 random albums
            random.shuffle(all_albums)
            selected_albums = all_albums[:20]

            logger.info(f"Selected {len(selected_albums)} random albums")

            # Step 4: Build playlist from tracks in those albums
            all_tracks = []
            if use_spotify:
                for album in selected_albums:
                    try:
                        album_data = self.spotify_client.get_album(album.id)
                        if album_data and 'tracks' in album_data:
                            tracks = album_data['tracks'].get('items', [])
                            for track in tracks:
                                if track['id']:
                                    all_tracks.append({
                                        'spotify_track_id': track['id'],
                                        'track_name': track['name'],
                                        'artist_name': ', '.join([a['name'] for a in track.get('artists', [])]),
                                        'album_name': album_data.get('name', 'Unknown'),
                                        'album_cover_url': album_data.get('images', [{}])[0].get('url') if album_data.get('images') else None,
                                        'duration_ms': track.get('duration_ms', 0),
                                        'popularity': album_data.get('popularity', 0),
                                        'id': track['id'],
                                        'name': track['name'],
                                        'artists': [a['name'] for a in track.get('artists', [])],
                                        'album': {
                                            'name': album_data.get('name', 'Unknown'),
                                            'images': album_data.get('images', [])
                                        }
                                    })
                        import time
                        time.sleep(0.3)
                    except Exception as e:
                        logger.warning(f"Error getting tracks from album: {e}")
                        continue
            else:
                from core.metadata_service import get_primary_client
                itunes = get_primary_client()
                for album in selected_albums:
                    try:
                        album_data = itunes.get_album(album.id, include_tracks=True)
                        if album_data and 'tracks' in album_data:
                            tracks = album_data['tracks'].get('items', [])
                            album_name = album_data.get('name', 'Unknown')
                            album_images = album_data.get('images', [])
                            album_cover = album_images[0].get('url') if album_images else None
                            for track in tracks:
                                track_id = track.get('id', '')
                                if track_id:
                                    # iTunes artists are [{'name': '...'}] dicts
                                    track_artists = track.get('artists', [])
                                    artist_names = [a['name'] for a in track_artists] if isinstance(track_artists, list) and track_artists and isinstance(track_artists[0], dict) else (track_artists if isinstance(track_artists, list) else [])
                                    all_tracks.append({
                                        'spotify_track_id': track_id,
                                        'track_name': track.get('name', ''),
                                        'artist_name': ', '.join(artist_names) if artist_names else 'Unknown',
                                        'album_name': album_name,
                                        'album_cover_url': album_cover,
                                        'duration_ms': track.get('duration_ms', 0),
                                        'popularity': 0,
                                        'id': track_id,
                                        'name': track.get('name', ''),
                                        'artists': artist_names,
                                        'album': {
                                            'name': album_name,
                                            'images': album_images
                                        }
                                    })
                        import time
                        time.sleep(0.3)
                    except Exception as e:
                        logger.warning(f"Error getting tracks from album: {e}")
                        continue

            logger.info(f"Collected {len(all_tracks)} total tracks")

            if not all_tracks:
                return {'tracks': [], 'error': 'No tracks found'}

            # Shuffle and limit to playlist_size
            random.shuffle(all_tracks)
            final_tracks = all_tracks[:playlist_size]

            logger.info(f"Built custom playlist with {len(final_tracks)} tracks")

            return {
                'name': 'Custom Playlist',
                'description': f'Built from {len(seed_artist_ids)} seed artists',
                'track_count': len(final_tracks),
                'tracks': final_tracks,
                'metadata': {
                    'total_tracks': len(final_tracks),
                    'similar_artists_count': len(all_similar_artists),
                    'albums_count': len(selected_albums)
                }
            }

        except Exception as e:
            logger.error(f"Error building custom playlist: {e}")
            import traceback
            traceback.print_exc()
            return {'tracks': [], 'error': str(e)}


# Singleton instance
_personalized_playlists_instance = None

def get_personalized_playlists_service(database, spotify_client=None):
    """Get the global personalized playlists service instance"""
    global _personalized_playlists_instance
    if _personalized_playlists_instance is None:
        _personalized_playlists_instance = PersonalizedPlaylistsService(database, spotify_client)
    return _personalized_playlists_instance
