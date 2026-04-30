"""Discover Hero endpoint — lifted from web_server.py.

The function body is byte-identical to the original. The
``spotify_client`` proxy + helper shims let the body resolve its
original names; the more complex ``_get_metadata_fallback_client``
is injected via init() because it composes multiple registry helpers
that web_server.py wires together.
"""
import logging

from flask import g, jsonify

from database.music_database import get_database
from core.metadata.registry import get_primary_source, get_spotify_client

logger = logging.getLogger(__name__)


def get_current_profile_id() -> int:
    """Mirror of web_server.get_current_profile_id — uses Flask g."""
    try:
        return g.profile_id
    except AttributeError:
        return 1


def _get_active_discovery_source():
    """Mirror of web_server._get_active_discovery_source — delegates to registry."""
    return get_primary_source()


class _SpotifyClientProxy:
    """Resolves the global Spotify client lazily through core.metadata.registry."""

    def __getattr__(self, name):
        client = get_spotify_client()
        if client is None:
            raise AttributeError(name)
        return getattr(client, name)

    def __bool__(self):
        return get_spotify_client() is not None


spotify_client = _SpotifyClientProxy()


# Injected at runtime via init().
_get_metadata_fallback_client = None


def init(get_metadata_fallback_client_fn):
    """Bind web_server's _get_metadata_fallback_client helper."""
    global _get_metadata_fallback_client
    _get_metadata_fallback_client = get_metadata_fallback_client_fn


def get_discover_hero():
    """Get featured similar artists for hero slideshow"""
    try:
        database = get_database()

        # Determine active source
        active_source = _get_active_discovery_source()
        logger.info(f"Discover hero using source: {active_source}")

        # Import fallback client for non-Spotify lookups
        itunes_client = _get_metadata_fallback_client()

        # Get top similar artists (excluding watchlist, cycled by last_featured)
        # Fetch more than needed since strict source filtering may drop many
        pid = get_current_profile_id()
        logger.info(f"[Discover Hero] Profile ID: {pid}, Active source: {active_source}")
        similar_artists = database.get_top_similar_artists(limit=200, profile_id=pid, require_source=active_source)

        # FALLBACK: If no similar artists exist, use watchlist artists for Hero section
        if not similar_artists:
            logger.warning("[Discover Hero] No similar artists found, falling back to watchlist artists")
            watchlist_artists = database.get_watchlist_artists(profile_id=pid)

            if not watchlist_artists:
                return jsonify({"success": True, "artists": [], "source": active_source})

            # Convert watchlist artists to hero format
            import random
            shuffled_watchlist = list(watchlist_artists)
            random.shuffle(shuffled_watchlist)

            hero_artists = []
            for artist in shuffled_watchlist[:10]:
                if active_source == 'spotify':
                    artist_id = artist.spotify_artist_id
                elif active_source == 'deezer':
                    artist_id = getattr(artist, 'deezer_artist_id', None) or artist.itunes_artist_id
                else:
                    artist_id = artist.itunes_artist_id
                if not artist_id:
                    continue

                artist_data = {
                    "spotify_artist_id": artist.spotify_artist_id,
                    "itunes_artist_id": artist.itunes_artist_id,
                    "artist_id": artist_id,
                    "artist_name": artist.artist_name,
                    "occurrence_count": 1,
                    "similarity_rank": 1,
                    "source": active_source,
                    "is_watchlist": True
                }

                # Use cached image from watchlist — no API call needed
                if hasattr(artist, 'image_url') and artist.image_url:
                    artist_data['image_url'] = artist.image_url

                hero_artists.append(artist_data)

            logger.warning(f"[Discover Hero] Returning {len(hero_artists)} watchlist artists as fallback")
            return jsonify({"success": True, "artists": hero_artists, "source": active_source, "fallback": "watchlist"})

        # Artists are already filtered by source in SQL — no post-filter needed
        valid_artists = list(similar_artists)

        # FALLBACK: If no valid artists for fallback source, try to resolve IDs on-the-fly
        if active_source in ('itunes', 'deezer') and not valid_artists:
            logger.warning(f"[{active_source} Fallback] No artists with {active_source} IDs found, attempting on-the-fly resolution for {len(similar_artists)} artists")
            resolved_count = 0
            for artist in similar_artists:
                existing_id = getattr(artist, f'similar_artist_{active_source}_id', None) or (artist.similar_artist_itunes_id if active_source == 'itunes' else None)
                if existing_id:
                    valid_artists.append(artist)
                    continue
                # Try to resolve ID by name
                try:
                    search_results = itunes_client.search_artists(artist.similar_artist_name, limit=1)
                    if search_results and len(search_results) > 0:
                        resolved_id = search_results[0].id
                        # Cache the resolved ID for future use
                        if active_source == 'deezer':
                            database.update_similar_artist_deezer_id(artist.id, resolved_id)
                            artist.similar_artist_deezer_id = resolved_id
                        else:
                            database.update_similar_artist_itunes_id(artist.id, resolved_id)
                            artist.similar_artist_itunes_id = resolved_id
                        valid_artists.append(artist)
                        resolved_count += 1
                        logger.info(f"  [Resolved] {artist.similar_artist_name} -> {active_source} ID: {resolved_id}")
                except Exception as resolve_err:
                    logger.error(f"  [Failed] Could not resolve {active_source} ID for {artist.similar_artist_name}: {resolve_err}")
                # Stop after 10 successful resolutions to avoid rate limiting
                if len(valid_artists) >= 10:
                    break
            logger.warning(f"[{active_source} Fallback] Resolved {resolved_count} artists with IDs")

        logger.info(f"[Discover Hero] Found {len(valid_artists)} valid artists for source: {active_source}")

        # Filter out blacklisted artists
        blacklisted = database.get_discovery_blacklist_names()
        if blacklisted:
            valid_artists = [a for a in valid_artists if a.similar_artist_name.lower() not in blacklisted]

        # Take top 10 (already ordered by least-recently-featured, then quality)
        similar_artists = valid_artists[:10]

        # Convert to JSON format — use cached metadata, only fetch from API if missing
        hero_artists = []
        for artist in similar_artists:
            # Use the ID for the active source, falling back to the other if needed
            if active_source == 'spotify':
                artist_id = artist.similar_artist_spotify_id or artist.similar_artist_itunes_id
            elif active_source == 'deezer':
                artist_id = getattr(artist, 'similar_artist_deezer_id', None) or artist.similar_artist_itunes_id or artist.similar_artist_spotify_id
            else:
                artist_id = artist.similar_artist_itunes_id or artist.similar_artist_spotify_id

            artist_data = {
                "spotify_artist_id": artist.similar_artist_spotify_id,
                "itunes_artist_id": artist.similar_artist_itunes_id,
                "artist_id": artist_id,
                "artist_name": artist.similar_artist_name,
                "occurrence_count": artist.occurrence_count,
                "similarity_rank": artist.similarity_rank,
                "source": active_source
            }

            # Use cached metadata if available
            if artist.image_url:
                artist_data['image_url'] = artist.image_url
                artist_data['genres'] = artist.genres or []
                artist_data['popularity'] = artist.popularity or 0
            else:
                # No cached metadata — fetch from API and cache for next time
                try:
                    if active_source == 'spotify' and artist.similar_artist_spotify_id:
                        if spotify_client and spotify_client.is_authenticated():
                            sp_artist = spotify_client.get_artist(artist.similar_artist_spotify_id)
                            if sp_artist and sp_artist.get('images'):
                                artist_data['artist_name'] = sp_artist.get('name', artist.similar_artist_name)
                                artist_data['image_url'] = sp_artist['images'][0]['url'] if sp_artist['images'] else None
                                artist_data['genres'] = sp_artist.get('genres', [])
                                artist_data['popularity'] = sp_artist.get('popularity', 0)
                                # Cache it
                                database.update_similar_artist_metadata(
                                    artist.id, artist_data.get('image_url'),
                                    artist_data.get('genres'), artist_data.get('popularity')
                                )
                    elif active_source in ('itunes', 'deezer'):
                        fb_artist_id = getattr(artist, 'similar_artist_deezer_id', None) if active_source == 'deezer' else None
                        fb_artist_id = fb_artist_id or artist.similar_artist_itunes_id
                        if fb_artist_id:
                            fb_artist_data = itunes_client.get_artist(fb_artist_id)
                            if fb_artist_data:
                                artist_data['artist_name'] = fb_artist_data.get('name', artist.similar_artist_name)
                                artist_data['image_url'] = fb_artist_data.get('images', [{}])[0].get('url') if fb_artist_data.get('images') else None
                                artist_data['genres'] = fb_artist_data.get('genres', [])
                                artist_data['popularity'] = fb_artist_data.get('popularity', 0)
                                # Cache it
                                database.update_similar_artist_metadata(
                                    artist.id, artist_data.get('image_url'),
                                    artist_data.get('genres'), artist_data.get('popularity')
                                )
                except Exception as img_err:
                    logger.error(f"Could not fetch artist image: {img_err}")

            hero_artists.append(artist_data)

        # Mark these artists as featured so they cycle to the back of the queue
        featured_names = [a["artist_name"] for a in hero_artists]
        database.mark_artists_featured(featured_names)

        return jsonify({"success": True, "artists": hero_artists, "source": active_source})

    except Exception as e:
        logger.error(f"Error getting discover hero: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
