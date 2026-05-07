"""Playlist explorer build-tree route.

`playlist_explorer_build_tree(deps)` is the body of the
`POST /api/playlist-explorer/build-tree` route. It builds a discovery
tree from a mirrored playlist and streams the result as NDJSON
(one JSON object per artist line + a final 'complete' line).

Works with Spotify (preferred), iTunes, or Deezer as the metadata
source. Uses and populates the metadata cache to avoid redundant API
calls per discography fetch.

Two operating modes:
- `albums`: only show releases that overlap with the playlist's tracks.
- `discographies`: show the full discography of every artist in the
  playlist, with `in_playlist` flag on the matching releases.

Per-artist flow inside the streaming generator:
1. Resolve discography via `_fetch_artist_discography` (cache → fall
   through to live API search).
2. Tag each release with `in_playlist` based on title-similarity match
   against the playlist's track/album names.
3. Apply mode filter, sort by in-playlist-first then year DESC.
4. Yield one JSON line per artist.

The route returns Flask's streaming `Response` wrapper around the NDJSON
generator. Early-exit cases (bad request, playlist not found, top-level
exception) yield via Flask's standard `jsonify(...), status` shape.

Lifted verbatim from web_server.py. Wide dependency surface (Flask
`request` + `Response`, Spotify client, multiple metadata helpers,
DB access, metadata cache) all injected via `PlaylistExplorerDeps`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class PlaylistExplorerDeps:
    """Bundle of cross-cutting deps the playlist explorer needs."""
    request: Any  # flask.request proxy
    flask_response: Any  # flask.Response constructor
    flask_jsonify: Any  # flask.jsonify
    spotify_client: Any
    get_database: Callable[[], Any]
    get_active_discovery_source: Callable[[], str]
    get_metadata_fallback_client: Callable[[], Any]
    get_metadata_fallback_source: Callable[[], str]
    get_metadata_cache: Callable[[], Any]


def playlist_explorer_build_tree(deps: PlaylistExplorerDeps):
    """Build a discovery tree from a mirrored playlist.
    Streams NDJSON: one line per artist with their albums.
    Works with Spotify, iTunes, or Deezer as the metadata source.
    Uses and populates the metadata cache to avoid redundant API calls."""
    try:
        data = deps.request.get_json()
        if not data:
            return deps.flask_jsonify({"success": False, "error": "No data provided"}), 400

        playlist_id = data.get('playlist_id')
        mode = data.get('mode', 'albums')  # 'albums' or 'discographies'

        if not playlist_id:
            return deps.flask_jsonify({"success": False, "error": "playlist_id is required"}), 400
        if mode not in ('albums', 'discographies'):
            return deps.flask_jsonify({"success": False, "error": "mode must be 'albums' or 'discographies'"}), 400

        database = deps.get_database()
        playlist = database.get_mirrored_playlist(playlist_id)
        if not playlist:
            return deps.flask_jsonify({"success": False, "error": "Playlist not found"}), 404

        tracks = database.get_mirrored_playlist_tracks(playlist_id)
        if not tracks:
            return deps.flask_jsonify({"success": False, "error": "Playlist has no tracks"}), 400

        # Determine active metadata source — respect user's configured primary
        source_name = deps.get_active_discovery_source()
        if source_name == 'spotify' and deps.spotify_client and deps.spotify_client.is_spotify_authenticated():
            active_client = deps.spotify_client
        else:
            active_client = deps.get_metadata_fallback_client()
            source_name = deps.get_metadata_fallback_source()

        cache = deps.get_metadata_cache()

        # Parse extra_data and group tracks by artist using discovered data
        artist_groups = {}
        for t in tracks:
            extra = {}
            if t.get('extra_data'):
                try:
                    extra = json.loads(t['extra_data']) if isinstance(t['extra_data'], str) else t['extra_data']
                except (json.JSONDecodeError, TypeError):
                    pass

            # Only use discovery data if it matches the active metadata source
            is_discovered = extra.get('discovered', False)
            provider = (extra.get('provider') or '').lower()
            source_matches = provider == source_name or (provider in ('itunes', 'apple') and source_name == 'itunes')

            matched = extra.get('matched_data', {}) if (is_discovered and source_matches) else {}
            artists_list = matched.get('artists', [])
            primary_artist = artists_list[0] if artists_list else None
            # Artists can be dicts {"name": "X", "id": "Y"} or plain strings "X"
            if isinstance(primary_artist, dict):
                artist_name = primary_artist.get('name') or (t.get('artist_name') or '').strip()
                artist_id = primary_artist.get('id') or None
            elif isinstance(primary_artist, str):
                artist_name = primary_artist or (t.get('artist_name') or '').strip()
                artist_id = None
            else:
                artist_name = (t.get('artist_name') or '').strip()
                artist_id = None

            if not artist_name:
                continue

            key = artist_name.lower()
            if key not in artist_groups:
                artist_groups[key] = {
                    'name': artist_name,
                    'artist_id': artist_id,  # Pre-resolved from discovery
                    'tracks': [],
                    'album_names': set(),
                    'discovered': extra.get('discovered', False),
                }
            # If we get an artist_id from a later track but didn't have one before, fill it in
            if artist_id and not artist_groups[key].get('artist_id'):
                artist_groups[key]['artist_id'] = artist_id

            artist_groups[key]['tracks'].append(t.get('track_name', ''))
            # Get album name from discovered data or playlist field
            album_name = ''
            album_data = matched.get('album')
            if isinstance(album_data, dict) and album_data.get('name'):
                album_name = album_data['name']
            elif (t.get('album_name') or '').strip():
                album_name = t['album_name'].strip()
            if album_name:
                artist_groups[key]['album_names'].add(album_name)

        def _normalize_for_match(title):
            import re
            return re.sub(r'\s*[\(\[][^)\]]*[\)\]]', '', title).strip().lower()

        def _fetch_artist_discography(artist_name, known_artist_id=None):
            """Fetch discography using the active client. Checks cache first, stores results after.
            If known_artist_id is provided (from discovery cache), skips the name search."""
            # Check cache for this artist's discography
            cache_key = f"explorer_disco_{artist_name.lower().strip()}"
            cached = cache.get_entity(source_name, 'artist_discography', cache_key) if cache else None
            if cached and isinstance(cached, dict) and cached.get('albums'):
                logger.debug(f"Explorer: cache hit for '{artist_name}' discography")
                return cached

            artist_id = known_artist_id
            artist_image = None

            if artist_id:
                # Already have the ID from discovery — just fetch the artist image
                try:
                    artist_info = active_client.get_artist(artist_id)
                    if artist_info:
                        if isinstance(artist_info, dict):
                            images = artist_info.get('images') or []
                            artist_image = images[0].get('url') if images else None
                        elif hasattr(artist_info, 'image_url'):
                            artist_image = artist_info.image_url
                except Exception as e:
                    logger.debug("artist image resolve: %s", e)
            else:
                # No pre-resolved ID — search by name
                try:
                    search_results = active_client.search_artists(artist_name, limit=5)
                except Exception as e:
                    return {'success': False, 'error': f'Search failed: {e}'}

                if not search_results:
                    return {'success': False, 'error': f'"{artist_name}" not found'}

                # Find best match (exact first, then fuzzy)
                best = None
                for a in search_results:
                    if a.name.lower().strip() == artist_name.lower().strip():
                        best = a
                        break
                if not best:
                    best = search_results[0]

                artist_id = best.id
                artist_image = best.image_url if hasattr(best, 'image_url') else None

            # Fetch albums
            try:
                # skip_cache only supported by spotify_client — other clients don't cache this call
                _skip = {'skip_cache': True} if hasattr(active_client, 'sp') else {}
                all_albums = active_client.get_artist_albums(artist_id, album_type='album,single', **_skip)
            except Exception as e:
                return {'success': False, 'error': f'Album fetch failed: {e}'}

            if not all_albums:
                return {'success': False, 'error': 'No albums found'}

            # Check which albums the user already owns
            owned_titles = set()
            try:
                db = deps.get_database()
                with db._get_connection() as conn:
                    cursor = conn.cursor()
                    # Find all artists in DB matching this name
                    cursor.execute("SELECT id FROM artists WHERE LOWER(name) = LOWER(?)", (artist_name,))
                    artist_rows = cursor.fetchall()
                    for ar in artist_rows:
                        cursor.execute("SELECT title FROM albums WHERE artist_id = ?", (ar['id'],))
                        for alb_row in cursor.fetchall():
                            owned_titles.add((alb_row['title'] or '').strip().lower())
            except Exception as e:
                logger.debug("owned-titles lookup: %s", e)

            # Build release list
            releases = []
            for album in all_albums:
                # Skip albums where this artist isn't primary
                if hasattr(album, 'artist_ids') and album.artist_ids and album.artist_ids[0] != artist_id:
                    continue
                releases.append({
                    'title': album.name,
                    'year': album.release_date[:4] if album.release_date else None,
                    'image_url': album.image_url,
                    'spotify_id': album.id,
                    'track_count': album.total_tracks,
                    'album_type': (album.album_type or 'album').lower(),
                    'owned': (album.name or '').strip().lower() in owned_titles,
                })

            result = {
                'success': True,
                'name': artist_name,  # Required for metadata cache validation
                'albums': releases,
                'artist_image': artist_image,
                'artist_id': artist_id,
                'artist_name': artist_name,
            }

            # Store in cache
            if cache and releases:
                try:
                    cache.store_entity(source_name, 'artist_discography', cache_key, result)
                except Exception as e:
                    logger.debug("cache discography write: %s", e)

            return result

        def generate():
            yield json.dumps({
                "type": "meta",
                "playlist_name": playlist.get('name', 'Unknown Playlist'),
                "playlist_image": playlist.get('image_url', ''),
                "total_artists": len(artist_groups),
                "total_tracks": len(tracks),
                "source": source_name,
            }) + '\n'

            total_albums = 0

            for idx, (_key, group) in enumerate(artist_groups.items()):
                artist_name = group['name']
                playlist_track_names = group['tracks']
                playlist_album_names = group['album_names']

                try:
                    disco = _fetch_artist_discography(artist_name, group.get('artist_id'))

                    if not disco.get('success'):
                        yield json.dumps({
                            "type": "artist",
                            "name": artist_name,
                            "artist_id": None,
                            "image_url": None,
                            "playlist_tracks": playlist_track_names,
                            "albums": [],
                            "error": disco.get('error', 'Not found'),
                        }) + '\n'
                        time.sleep(0.1)
                        continue

                    # Tag each release with in_playlist flag
                    # If no album names available, fall back to matching track names against single titles
                    match_names = playlist_album_names
                    if not match_names:
                        match_names = set(playlist_track_names)

                    all_releases = []
                    for release in disco.get('albums', []):
                        r = dict(release)
                        norm_title = _normalize_for_match(r['title'])
                        r['in_playlist'] = any(
                            _normalize_for_match(a) == norm_title or
                            norm_title in _normalize_for_match(a) or
                            _normalize_for_match(a) in norm_title
                            for a in match_names
                        )
                        all_releases.append(r)

                    # Filter based on mode
                    if mode == 'albums':
                        filtered = [r for r in all_releases if r['in_playlist']]
                    else:
                        filtered = all_releases

                    filtered.sort(key=lambda r: (not r.get('in_playlist', False), -(int(r.get('year') or 0))))
                    total_albums += len(filtered)

                    yield json.dumps({
                        "type": "artist",
                        "name": disco.get('artist_name', artist_name),
                        "artist_id": disco.get('artist_id'),
                        "image_url": disco.get('artist_image'),
                        "playlist_tracks": playlist_track_names,
                        "albums": filtered,
                    }) + '\n'

                except Exception as e:
                    logger.error(f"Explorer: error processing artist '{artist_name}': {e}")
                    yield json.dumps({
                        "type": "artist",
                        "name": artist_name,
                        "artist_id": None,
                        "image_url": None,
                        "playlist_tracks": playlist_track_names,
                        "albums": [],
                        "error": str(e),
                    }) + '\n'

                # Rate limit protection between artists
                if idx < len(artist_groups) - 1:
                    time.sleep(0.2)

            deps.get_database().mark_mirrored_playlist_explored(playlist_id)
            yield json.dumps({"type": "complete", "total_artists": len(artist_groups), "total_albums": total_albums}) + '\n'

        return deps.flask_response(generate(), mimetype='application/x-ndjson', headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        })

    except Exception as e:
        logger.error(f"Playlist Explorer build-tree error: {e}")
        import traceback
        traceback.print_exc()
        return deps.flask_jsonify({"success": False, "error": str(e)}), 500


