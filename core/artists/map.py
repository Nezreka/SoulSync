"""Artist Map endpoints — lifted from web_server.py.

The four route bodies (``get_artist_map_data``, ``get_artist_map_genre_list``,
``get_artist_map_genres``, ``get_artist_map_explore``) plus their cache helpers
and the artist-map cache are byte-identical to the originals. Module-level
shims for ``get_current_profile_id``, ``_get_itunes_client``, and the
``spotify_client`` proxy let the bodies resolve their original names without
modification.
"""
import json
import logging
import time

from flask import g, jsonify, request

from database.music_database import get_database
from core.metadata.registry import get_itunes_client, get_spotify_client

logger = logging.getLogger(__name__)


def get_current_profile_id() -> int:
    """Mirror of web_server.get_current_profile_id — uses Flask g."""
    try:
        return g.profile_id
    except AttributeError:
        return 1


def _get_itunes_client():
    """Mirror of web_server._get_itunes_client — delegates to registry."""
    return get_itunes_client()


class _SpotifyClientProxy:
    """Resolves the global Spotify client lazily so a Spotify re-auth that
    rebinds the cached client in core.metadata.registry is visible to the
    lifted route bodies."""

    def __getattr__(self, name):
        client = get_spotify_client()
        if client is None:
            raise AttributeError(name)
        return getattr(client, name)

    def __bool__(self):
        return get_spotify_client() is not None


spotify_client = _SpotifyClientProxy()


# Artist Map data cache — avoids re-querying 4+ tables on every request
# Keys: 'watchlist_{profile}', 'genres_{profile}', 'genre_list'
# Values: {'data': <json-ready dict>, 'ts': <timestamp>}
_artist_map_cache = {}
_ARTIST_MAP_CACHE_TTL = 300  # 5 minutes


def _artmap_cache_get(key):
    """Get cached artist map data if still fresh."""
    entry = _artist_map_cache.get(key)
    if entry and (time.time() - entry['ts']) < _ARTIST_MAP_CACHE_TTL:
        return entry['data']
    return None


def _artmap_cache_set(key, data):
    """Store artist map data in cache."""
    _artist_map_cache[key] = {'data': data, 'ts': time.time()}


def _artmap_cache_invalidate(profile_id=None):
    """Invalidate artist map cache (call after watchlist changes, scans, etc.)."""
    if profile_id:
        _artist_map_cache.pop(f'watchlist_{profile_id}', None)
        _artist_map_cache.pop(f'genres_{profile_id}', None)
    _artist_map_cache.pop('genre_list', None)


def get_artist_map_data():
    """Get watchlist artists + their similar artists for the force-directed artist map."""
    try:
        database = get_database()
        profile_id = get_current_profile_id()

        cached = _artmap_cache_get(f'watchlist_{profile_id}')
        if cached:
            return jsonify(cached)

        # Get all watchlist artists
        conn = database._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, artist_name, spotify_artist_id, itunes_artist_id, deezer_artist_id,
                   discogs_artist_id, image_url
            FROM watchlist_artists WHERE profile_id = ?
        """, (profile_id,))
        watchlist_rows = cursor.fetchall()

        nodes = []  # {id, name, image_url, type: 'watchlist'|'similar', genres, size}
        edges = []  # {source, target, weight}
        seen_names = {}  # normalized_name → node index

        def _norm(name):
            return (name or '').lower().strip()

        # Add watchlist artists as anchor nodes
        for wa in watchlist_rows:
            w = dict(wa)
            norm = _norm(w['artist_name'])
            if norm in seen_names:
                continue
            idx = len(nodes)
            seen_names[norm] = idx
            # Get image — prefer HTTP URLs
            img = w.get('image_url', '') or ''
            if img and not img.startswith('http'):
                img = ''
            nodes.append({
                'id': idx,
                'name': w['artist_name'],
                'image_url': img,
                'type': 'watchlist',
                'genres': [],
                'spotify_id': w.get('spotify_artist_id') or '',
                'itunes_id': w.get('itunes_artist_id') or '',
                'deezer_id': w.get('deezer_artist_id') or '',
                'discogs_id': w.get('discogs_artist_id') or '',
                'source_db_id': str(w['id']),
            })

        # Get all similar artists for all watchlist artists
        watchlist_ids = [dict(wa)['spotify_artist_id'] or dict(wa)['itunes_artist_id'] or str(dict(wa)['id']) for wa in watchlist_rows]
        if watchlist_ids:
            placeholders = ','.join(['?'] * len(watchlist_ids))
            cursor.execute(f"""
                SELECT source_artist_id, similar_artist_name, similar_artist_spotify_id,
                       similar_artist_itunes_id, similar_artist_deezer_id,
                       similarity_rank, occurrence_count, image_url, genres, popularity
                FROM similar_artists
                WHERE profile_id = ? AND source_artist_id IN ({placeholders})
                ORDER BY similarity_rank ASC
            """, [profile_id] + watchlist_ids)

            for row in cursor.fetchall():
                r = dict(row)
                sim_norm = _norm(r['similar_artist_name'])

                # Find or create similar artist node
                if sim_norm not in seen_names:
                    idx = len(nodes)
                    seen_names[sim_norm] = idx
                    img = r.get('image_url', '') or ''
                    if img and not img.startswith('http'):
                        img = ''
                    genres = []
                    if r.get('genres'):
                        try:
                            genres = json.loads(r['genres'])
                        except Exception:
                            pass
                    nodes.append({
                        'id': idx,
                        'name': r['similar_artist_name'],
                        'image_url': img,
                        'type': 'similar',
                        'genres': genres,
                        'spotify_id': r.get('similar_artist_spotify_id') or '',
                        'itunes_id': r.get('similar_artist_itunes_id') or '',
                        'deezer_id': r.get('similar_artist_deezer_id') or '',
                        'rank': r.get('similarity_rank', 5),
                        'occurrence': r.get('occurrence_count', 1),
                        'popularity': r.get('popularity', 0),
                    })

                sim_idx = seen_names[sim_norm]

                # Find the watchlist node that sourced this similar artist
                source_norm = None
                for wa in watchlist_rows:
                    w = dict(wa)
                    sid = w.get('spotify_artist_id') or w.get('itunes_artist_id') or str(w['id'])
                    if sid == r['source_artist_id']:
                        source_norm = _norm(w['artist_name'])
                        break

                if source_norm and source_norm in seen_names:
                    source_idx = seen_names[source_norm]
                    # Weight: inverse of rank (rank 1 = strongest connection)
                    weight = max(1, 11 - (r.get('similarity_rank', 5)))
                    edges.append({
                        'source': source_idx,
                        'target': sim_idx,
                        'weight': weight,
                    })

        # Also check if any similar artists ARE watchlist artists (cross-links)
        # These create extra connections between watchlist nodes
        for i, node in enumerate(nodes):
            if node['type'] == 'similar':
                # Check if this similar artist is also a watchlist artist
                for j, wnode in enumerate(nodes):
                    if wnode['type'] == 'watchlist' and i != j:
                        if _norm(node['name']) == _norm(wnode['name']):
                            # Merge: upgrade the similar node to watchlist
                            node['type'] = 'watchlist'
                            break

        # ── Backfill from metadata cache: batch-lookup all node names across all sources ──
        # Single query to get ALL cached artist entries matching ANY node name
        try:
            all_names = list(set(_norm(n['name']) for n in nodes if n.get('name')))
            if all_names:
                # Build case-insensitive IN clause via temp matching
                # Lightweight query — no raw_json (can be huge)
                cursor.execute("""
                    SELECT entity_id, source, name, image_url, genres, popularity
                    FROM metadata_cache_entities
                    WHERE entity_type = 'artist'
                """)
                cache_rows = cursor.fetchall()

                # Index cache by normalized name → {source: {id, image_url, genres}}
                cache_by_name = {}
                for cr in cache_rows:
                    cn = _norm(cr['name'] or '')
                    if cn not in cache_by_name:
                        cache_by_name[cn] = {}
                    source = cr['source']
                    genres = []
                    if cr['genres']:
                        try:
                            genres = json.loads(cr['genres']) if isinstance(cr['genres'], str) else []
                        except Exception:
                            pass
                    cache_by_name[cn][source] = {
                        'id': cr['entity_id'],
                        'image_url': cr['image_url'] or '',
                        'genres': genres,
                    }

                # Apply cache data to nodes
                source_id_map = {'spotify': 'spotify_id', 'itunes': 'itunes_id', 'deezer': 'deezer_id', 'discogs': 'discogs_id'}
                for n in nodes:
                    nn = _norm(n['name'])
                    cached = cache_by_name.get(nn)
                    if not cached:
                        continue
                    for source, field in source_id_map.items():
                        if not n.get(field) and source in cached:
                            n[field] = cached[source]['id']
                    # Backfill image if missing or local path
                    if not n.get('image_url') or not n['image_url'].startswith('http'):
                        for source in ('spotify', 'deezer', 'itunes'):
                            if source in cached and cached[source].get('image_url', '').startswith('http'):
                                n['image_url'] = cached[source]['image_url']
                                break
                    # Backfill genres if missing
                    if not n.get('genres') or len(n.get('genres', [])) == 0:
                        for source in ('spotify', 'deezer', 'itunes', 'discogs'):
                            if source in cached and cached[source].get('genres'):
                                n['genres'] = cached[source]['genres'][:5]
                                break
                # Deezer direct URL fallback
                for n in nodes:
                    if not n.get('image_url') or not n['image_url'].startswith('http'):
                        if n.get('deezer_id'):
                            n['image_url'] = f"https://api.deezer.com/artist/{n['deezer_id']}/image?size=big"

                # Album art fallback (iTunes artists have no artist images)
                _album_art = {}
                try:
                    cursor.execute("""
                        SELECT artist_name, image_url FROM metadata_cache_entities
                        WHERE entity_type = 'album' AND image_url LIKE 'http%'
                          AND artist_name IS NOT NULL AND artist_name != ''
                    """)
                    for r in cursor.fetchall():
                        an = _norm(r['artist_name'])
                        if an and an not in _album_art:
                            _album_art[an] = r['image_url']
                except Exception:
                    pass
                for n in nodes:
                    if not n.get('image_url') or not n['image_url'].startswith('http'):
                        nn = _norm(n['name'])
                        if nn in _album_art:
                            n['image_url'] = _album_art[nn]

        except Exception as cache_err:
            logger.debug(f"Artist map cache backfill error: {cache_err}")

        result = {
            'success': True,
            'nodes': nodes,
            'edges': edges,
            'watchlist_count': sum(1 for n in nodes if n['type'] == 'watchlist'),
            'similar_count': sum(1 for n in nodes if n['type'] == 'similar'),
        }
        _artmap_cache_set(f'watchlist_{profile_id}', result)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting artist map data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


def get_artist_map_genre_list():
    """Lightweight endpoint — just genre names + counts for the picker. No node data."""
    try:
        cached = _artmap_cache_get('genre_list')
        if cached:
            return jsonify(cached)

        database = get_database()
        conn = database._get_connection()
        cursor = conn.cursor()

        # Fast query: just count artists per genre from cache
        genre_counts = {}
        cursor.execute("""
            SELECT genres FROM metadata_cache_entities
            WHERE entity_type = 'artist' AND genres IS NOT NULL AND genres != '' AND genres != '[]'
        """)
        for r in cursor.fetchall():
            try:
                for g in json.loads(r['genres']):
                    if g and isinstance(g, str):
                        gl = g.lower().strip()
                        genre_counts[gl] = genre_counts.get(gl, 0) + 1
            except Exception:
                pass

        # Sort by count descending
        sorted_genres = sorted(genre_counts.items(), key=lambda x: -x[1])

        result = {
            'success': True,
            'genres': [{'name': g, 'count': c} for g, c in sorted_genres],
            'total': len(sorted_genres)
        }
        _artmap_cache_set('genre_list', result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def get_artist_map_genres():
    """Get ALL artists from every data source, grouped by genre for the genre map."""
    try:
        database = get_database()
        profile_id = get_current_profile_id()

        cached = _artmap_cache_get(f'genres_{profile_id}')
        if cached:
            return jsonify(cached)

        conn = database._get_connection()
        cursor = conn.cursor()

        artists_by_name = {}  # normalized_name → {name, image, genres[], sources, ids}

        def _norm(n):
            return (n or '').lower().strip()

        def _add(name, image_url=None, genres=None, spotify_id=None, itunes_id=None, deezer_id=None, discogs_id=None, source='unknown', popularity=0):
            n = _norm(name)
            if not n or len(n) < 2:
                return
            if n not in artists_by_name:
                artists_by_name[n] = {
                    'name': name, 'image_url': '', 'genres': set(),
                    'spotify_id': '', 'itunes_id': '', 'deezer_id': '', 'discogs_id': '',
                    'sources': set(), 'popularity': 0
                }
            a = artists_by_name[n]
            if image_url and image_url.startswith('http') and not a['image_url']:
                a['image_url'] = image_url
            if genres:
                for g in (genres if isinstance(genres, list) else []):
                    if g and isinstance(g, str):
                        a['genres'].add(g.lower().strip())
            if spotify_id and not a['spotify_id']:
                a['spotify_id'] = str(spotify_id)
            if itunes_id and not a['itunes_id']:
                a['itunes_id'] = str(itunes_id)
            if deezer_id and not a['deezer_id']:
                a['deezer_id'] = str(deezer_id)
            if discogs_id and not a['discogs_id']:
                a['discogs_id'] = str(discogs_id)
            if popularity > a['popularity']:
                a['popularity'] = popularity
            a['sources'].add(source)

        # 1. Metadata cache — biggest source
        cursor.execute("""
            SELECT name, entity_id, source, image_url, genres, popularity
            FROM metadata_cache_entities WHERE entity_type = 'artist'
        """)
        for r in cursor.fetchall():
            genres = []
            if r['genres']:
                try:
                    genres = json.loads(r['genres']) if isinstance(r['genres'], str) else []
                except Exception:
                    pass
            src_map = {'spotify': 'spotify_id', 'itunes': 'itunes_id', 'deezer': 'deezer_id', 'discogs': 'discogs_id'}
            kwargs = {src_map.get(r['source'], 'spotify_id'): r['entity_id']}
            _add(r['name'], image_url=r['image_url'], genres=genres, source='cache', popularity=r['popularity'] or 0, **kwargs)

        # 2. Similar artists
        cursor.execute("""
            SELECT similar_artist_name, similar_artist_spotify_id, similar_artist_itunes_id,
                   similar_artist_deezer_id, image_url, genres, popularity
            FROM similar_artists WHERE profile_id = ?
        """, (profile_id,))
        for r in cursor.fetchall():
            genres = []
            if r['genres']:
                try:
                    genres = json.loads(r['genres']) if isinstance(r['genres'], str) else []
                except Exception:
                    pass
            _add(r['similar_artist_name'], image_url=r['image_url'], genres=genres,
                 spotify_id=r['similar_artist_spotify_id'], itunes_id=r['similar_artist_itunes_id'],
                 deezer_id=r['similar_artist_deezer_id'], source='similar', popularity=r['popularity'] or 0)

        # 3. Watchlist artists
        cursor.execute("""
            SELECT artist_name, spotify_artist_id, itunes_artist_id, deezer_artist_id,
                   discogs_artist_id, image_url
            FROM watchlist_artists WHERE profile_id = ?
        """, (profile_id,))
        for r in cursor.fetchall():
            _add(r['artist_name'], image_url=r['image_url'],
                 spotify_id=r['spotify_artist_id'], itunes_id=r['itunes_artist_id'],
                 deezer_id=r['deezer_artist_id'], discogs_id=r['discogs_artist_id'], source='watchlist')

        # 4. Library artists
        cursor.execute("SELECT name, thumb_url, genres FROM artists")
        for r in cursor.fetchall():
            genres = []
            if r['genres']:
                try:
                    genres = json.loads(r['genres']) if isinstance(r['genres'], str) else []
                except Exception:
                    pass
            img = r['thumb_url'] if r['thumb_url'] and r['thumb_url'].startswith('http') else None
            _add(r['name'], image_url=img, genres=genres, source='library')

        # Filter: only include artists that have at least one genre
        genre_artists = {k: v for k, v in artists_by_name.items() if v['genres']}

        # Build genre → artists map
        genre_map = {}  # genre_name → [artist_keys]
        for key, a in genre_artists.items():
            for g in a['genres']:
                if g not in genre_map:
                    genre_map[g] = []
                genre_map[g].append(key)

        # Sort genres by artist count, take top genres
        sorted_genres = sorted(genre_map.items(), key=lambda x: -len(x[1]))

        # Build nodes
        nodes = []
        node_idx = {}
        for key, a in genre_artists.items():
            idx = len(nodes)
            node_idx[key] = idx
            nodes.append({
                'id': idx,
                'name': a['name'],
                'image_url': a['image_url'],
                'genres': list(a['genres'])[:5],
                'spotify_id': a['spotify_id'],
                'itunes_id': a['itunes_id'],
                'deezer_id': a['deezer_id'],
                'discogs_id': a['discogs_id'],
                'popularity': a['popularity'],
                'type': 'watchlist' if 'watchlist' in a['sources'] else 'similar',
            })

        # Build genre clusters — allow artists in multiple genres
        top_genres = sorted_genres[:40]

        # Sort genres by co-occurrence so related genres are adjacent in the list.
        # This makes the spiral layout place related genres near each other.
        if len(top_genres) > 2:
            genre_sets = {g: set(keys) for g, keys in top_genres}
            ordered = [top_genres[0][0]]  # Start with biggest genre
            remaining = {g for g, _ in top_genres[1:]}
            while remaining:
                last = ordered[-1]
                last_set = genre_sets.get(last, set())
                # Find most similar remaining genre (highest artist overlap)
                best = None
                best_overlap = -1
                for g in remaining:
                    overlap = len(last_set & genre_sets.get(g, set()))
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best = g
                ordered.append(best)
                remaining.remove(best)
            # Rebuild top_genres in the ordered sequence
            genre_dict = dict(top_genres)
            top_genres = [(g, genre_dict[g]) for g in ordered if g in genre_dict]

        genres_out = []
        for genre, artist_keys in top_genres:
            genres_out.append({
                'name': genre,
                'count': len(artist_keys),
                'artist_ids': [node_idx[k] for k in artist_keys if k in node_idx],
            })

        # Image cleanup + multi-source fallback
        # Build two lookups: name→image_url AND name→deezer_entity_id
        _img_cache = {}
        _deezer_id_cache = {}
        _album_art_cache = {}  # artist_name → album image (iTunes fallback)
        try:
            # Artist images + Deezer IDs
            cursor.execute("""
                SELECT name, entity_id, source, image_url FROM metadata_cache_entities
                WHERE entity_type = 'artist'
                  AND ((image_url IS NOT NULL AND image_url != '' AND image_url LIKE 'http%')
                       OR source = 'deezer')
            """)
            for r in cursor.fetchall():
                nn = (r['name'] or '').lower().strip()
                if not nn:
                    continue
                if r['image_url'] and r['image_url'].startswith('http') and nn not in _img_cache:
                    _img_cache[nn] = r['image_url']
                if r['source'] == 'deezer' and r['entity_id'] and nn not in _deezer_id_cache:
                    _deezer_id_cache[nn] = r['entity_id']

            # Album art by artist name (for iTunes artists with no artist image)
            cursor.execute("""
                SELECT artist_name, image_url FROM metadata_cache_entities
                WHERE entity_type = 'album'
                  AND image_url IS NOT NULL AND image_url != '' AND image_url LIKE 'http%'
                  AND artist_name IS NOT NULL AND artist_name != ''
            """)
            for r in cursor.fetchall():
                nn = (r['artist_name'] or '').lower().strip()
                if nn and nn not in _album_art_cache:
                    _album_art_cache[nn] = r['image_url']
        except Exception:
            pass

        for n in nodes:
            img = n.get('image_url', '')
            if img in ('None', 'null', '') or (img and not img.startswith('http')):
                n['image_url'] = ''
            nn = n['name'].lower().strip()
            if not n['image_url']:
                # Try cache image by name
                n['image_url'] = _img_cache.get(nn, '')
            if not n['image_url'] and n.get('deezer_id'):
                n['image_url'] = f"https://api.deezer.com/artist/{n['deezer_id']}/image?size=big"
            if not n['image_url']:
                # Try Deezer ID from cache by name
                did = _deezer_id_cache.get(nn)
                if did:
                    n['deezer_id'] = did
                    n['image_url'] = f"https://api.deezer.com/artist/{did}/image?size=big"
            if not n['image_url']:
                # Try album art by artist name (iTunes artists have no artist images)
                n['image_url'] = _album_art_cache.get(nn, '')

        _img_count = sum(1 for n in nodes if n.get('image_url'))
        _deezer_count = sum(1 for n in nodes if n.get('image_url', '').startswith('https://api.deezer'))
        _none_count = sum(1 for n in nodes if not n.get('image_url'))
        logger.info(f"[Genre Map] {len(nodes)} artists, {len(sorted_genres)} genres")
        logger.warning(f"[Genre Map] Images: {_img_count} have URLs, {_deezer_count} Deezer fallback, {_none_count} missing")
        if _none_count > 0:
            samples = [n['name'] for n in nodes if not n.get('image_url')][:5]
            logger.warning(f"[Genre Map] Missing image samples: {samples}")

        result = {
            'success': True,
            'nodes': nodes,
            'genres': genres_out,
            'total_artists': len(nodes),
            'total_genres': len(sorted_genres),
        }
        _artmap_cache_set(f'genres_{profile_id}', result)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting genre map data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


def get_artist_map_explore():
    """Build an exploration map outward from a single artist."""
    try:
        artist_name = request.args.get('name', '').strip()
        artist_id = request.args.get('id', '').strip()

        if not artist_name and not artist_id:
            return jsonify({"success": False, "error": "Provide artist name or id"}), 400

        database = get_database()
        profile_id = get_current_profile_id()
        conn = database._get_connection()
        cursor = conn.cursor()

        def _norm(n):
            return (n or '').lower().strip()

        nodes = []
        edges = []
        seen = {}  # norm_name → node index

        # Find the center artist
        center_name = artist_name
        center_image = ''
        center_ids = {'spotify_id': '', 'itunes_id': '', 'deezer_id': '', 'discogs_id': ''}
        center_genres = []

        # Search metadata cache for the center artist
        if artist_id:
            cursor.execute("""
                SELECT name, entity_id, source, image_url, genres FROM metadata_cache_entities
                WHERE entity_type = 'artist' AND entity_id = ? LIMIT 1
            """, (artist_id,))
        else:
            cursor.execute("""
                SELECT name, entity_id, source, image_url, genres FROM metadata_cache_entities
                WHERE entity_type = 'artist' AND name = ? COLLATE NOCASE LIMIT 1
            """, (artist_name,))

        row = cursor.fetchone()
        artist_found = False
        if row:
            artist_found = True
            center_name = row['name']
            if row['image_url'] and row['image_url'].startswith('http'):
                center_image = row['image_url']
            src_map = {'spotify': 'spotify_id', 'itunes': 'itunes_id', 'deezer': 'deezer_id', 'discogs': 'discogs_id'}
            k = src_map.get(row['source'], 'spotify_id')
            center_ids[k] = row['entity_id']
            if row['genres']:
                try:
                    center_genres = json.loads(row['genres']) if isinstance(row['genres'], str) else []
                except Exception:
                    pass

        # Check watchlist + library if not in cache
        if not artist_found and not artist_id:
            cursor.execute("SELECT artist_name, image_url, spotify_artist_id, itunes_artist_id, deezer_artist_id, discogs_artist_id FROM watchlist_artists WHERE artist_name = ? COLLATE NOCASE LIMIT 1", (artist_name,))
            wr = cursor.fetchone()
            if wr:
                artist_found = True
                center_name = wr['artist_name']
                if wr['image_url'] and str(wr['image_url']).startswith('http'):
                    center_image = wr['image_url']
                for k, col in [('spotify_id', 'spotify_artist_id'), ('itunes_id', 'itunes_artist_id'), ('deezer_id', 'deezer_artist_id'), ('discogs_id', 'discogs_artist_id')]:
                    if wr[col]:
                        center_ids[k] = str(wr[col])
            else:
                cursor.execute("SELECT name, thumb_url FROM artists WHERE name = ? COLLATE NOCASE LIMIT 1", (artist_name,))
                lr = cursor.fetchone()
                if lr:
                    artist_found = True
                    center_name = lr['name']
                    if lr['thumb_url'] and str(lr['thumb_url']).startswith('http'):
                        center_image = lr['thumb_url']

        # If not found locally, validate via metadata API search
        if not artist_found and not artist_id:
            try:
                api_match = None
                if spotify_client and spotify_client.is_spotify_authenticated():
                    results = spotify_client.search_artists(artist_name, limit=1)
                    if results and len(results) > 0:
                        sa = results[0]
                        if sa.name.lower().strip() == artist_name.lower().strip() or \
                           artist_name.lower().strip() in sa.name.lower().strip():
                            api_match = sa
                            center_name = sa.name
                            center_ids['spotify_id'] = sa.id
                            center_image = sa.image_url if hasattr(sa, 'image_url') else ''
                            center_genres = sa.genres if hasattr(sa, 'genres') else []
                            artist_found = True
                if not artist_found:
                    ic = _get_itunes_client()
                    results = ic.search_artists(artist_name, limit=1)
                    if results and len(results) > 0:
                        ia = results[0]
                        if ia.name.lower().strip() == artist_name.lower().strip() or \
                           artist_name.lower().strip() in ia.name.lower().strip():
                            center_name = ia.name
                            center_ids['itunes_id'] = str(ia.id)
                            center_image = ia.image_url if hasattr(ia, 'image_url') else ''
                            artist_found = True
            except Exception as e:
                logger.debug(f"[Artist Explorer] API validation failed for '{artist_name}': {e}")

        if not artist_found:
            return jsonify({"success": False, "error": f"Artist '{artist_name}' not found"}), 404

        # Also check cache for other source IDs
        cursor.execute("""
            SELECT entity_id, source, image_url, genres FROM metadata_cache_entities
            WHERE entity_type = 'artist' AND name = ? COLLATE NOCASE
        """, (center_name,))
        for r in cursor.fetchall():
            src_map = {'spotify': 'spotify_id', 'itunes': 'itunes_id', 'deezer': 'deezer_id', 'discogs': 'discogs_id'}
            k = src_map.get(r['source'], 'spotify_id')
            if not center_ids.get(k):
                center_ids[k] = r['entity_id']
            if r['image_url'] and r['image_url'].startswith('http') and not center_image:
                center_image = r['image_url']
            if r['genres'] and not center_genres:
                try:
                    center_genres = json.loads(r['genres']) if isinstance(r['genres'], str) else []
                except Exception:
                    pass

        # Add center node
        center_idx = 0
        seen[_norm(center_name)] = center_idx
        nodes.append({
            'id': 0, 'name': center_name, 'image_url': center_image,
            'type': 'center', 'genres': center_genres[:5],
            **center_ids, 'ring': 0
        })

        # Ring 1: Direct similar artists from similar_artists table
        # Search by all known IDs
        id_values = [v for v in center_ids.values() if v]
        ring1_artists = []
        if id_values:
            placeholders = ','.join(['?'] * len(id_values))
            cursor.execute(f"""
                SELECT DISTINCT similar_artist_name, similar_artist_spotify_id,
                       similar_artist_itunes_id, similar_artist_deezer_id,
                       image_url, genres, popularity, similarity_rank
                FROM similar_artists
                WHERE source_artist_id IN ({placeholders}) AND profile_id = ?
                ORDER BY similarity_rank ASC
            """, id_values + [profile_id])
            ring1_artists = cursor.fetchall()

        # Also search by name (the center artist might be a watchlist source)
        cursor.execute("""
            SELECT DISTINCT sa.similar_artist_name, sa.similar_artist_spotify_id,
                   sa.similar_artist_itunes_id, sa.similar_artist_deezer_id,
                   sa.image_url, sa.genres, sa.popularity, sa.similarity_rank
            FROM similar_artists sa
            JOIN watchlist_artists wa ON sa.source_artist_id = COALESCE(wa.spotify_artist_id, wa.itunes_artist_id, CAST(wa.id AS TEXT))
            WHERE wa.artist_name = ? COLLATE NOCASE AND sa.profile_id = ?
            ORDER BY sa.similarity_rank ASC
        """, (center_name, profile_id))
        ring1_artists.extend(cursor.fetchall())

        # If no similar artists in DB, fetch from MusicMap on-the-fly
        if not ring1_artists:
            try:
                logger.debug(f"[Artist Explorer] No stored similar artists for '{center_name}', fetching from MusicMap...")
                from core.watchlist_scanner import WatchlistScanner
                scanner = WatchlistScanner(spotify_client=spotify_client) if spotify_client else None
                if scanner:
                    similar = scanner._fetch_similar_artists_from_musicmap(center_name, limit=15)
                    if similar:
                        source_artist_id = center_ids.get('spotify_id') or center_ids.get('itunes_id') or center_name
                        # Store in DB for future use
                        for rank, sa in enumerate(similar, 1):
                            try:
                                database.add_or_update_similar_artist(
                                    source_artist_id=source_artist_id,
                                    similar_artist_name=sa['name'],
                                    similar_artist_spotify_id=sa.get('spotify_id'),
                                    similar_artist_itunes_id=sa.get('itunes_id'),
                                    similarity_rank=rank,
                                    profile_id=profile_id,
                                    image_url=sa.get('image_url'),
                                    genres=sa.get('genres'),
                                    popularity=sa.get('popularity', 0),
                                    similar_artist_deezer_id=sa.get('deezer_id')
                                )
                            except Exception:
                                pass
                        # Re-query from DB to get consistent format
                        if id_values:
                            placeholders = ','.join(['?'] * len(id_values))
                            cursor.execute(f"""
                                SELECT DISTINCT similar_artist_name, similar_artist_spotify_id,
                                       similar_artist_itunes_id, similar_artist_deezer_id,
                                       image_url, genres, popularity, similarity_rank
                                FROM similar_artists
                                WHERE source_artist_id IN ({placeholders}) AND profile_id = ?
                                ORDER BY similarity_rank ASC
                            """, id_values + [profile_id])
                            ring1_artists = cursor.fetchall()
                        if not ring1_artists:
                            # Fallback: query by name-based source ID
                            cursor.execute("""
                                SELECT DISTINCT similar_artist_name, similar_artist_spotify_id,
                                       similar_artist_itunes_id, similar_artist_deezer_id,
                                       image_url, genres, popularity, similarity_rank
                                FROM similar_artists
                                WHERE source_artist_id = ? AND profile_id = ?
                                ORDER BY similarity_rank ASC
                            """, (source_artist_id, profile_id))
                            ring1_artists = cursor.fetchall()
                        logger.debug(f"[Artist Explorer] Fetched {len(ring1_artists)} similar artists from MusicMap for '{center_name}'")
                        _artmap_cache_invalidate(profile_id)  # New similar artists added
            except Exception as e:
                logger.debug(f"[Artist Explorer] MusicMap fetch failed for '{center_name}': {e}")

        # Deduplicate ring 1
        for r in ring1_artists:
            nn = _norm(r['similar_artist_name'])
            if nn in seen:
                continue
            idx = len(nodes)
            seen[nn] = idx
            genres = []
            if r['genres']:
                try:
                    genres = json.loads(r['genres']) if isinstance(r['genres'], str) else []
                except Exception:
                    pass
            img = r['image_url'] if r['image_url'] and r['image_url'].startswith('http') else ''
            nodes.append({
                'id': idx, 'name': r['similar_artist_name'], 'image_url': img,
                'type': 'ring1', 'genres': genres[:5],
                'spotify_id': r['similar_artist_spotify_id'] or '',
                'itunes_id': r['similar_artist_itunes_id'] or '',
                'deezer_id': r['similar_artist_deezer_id'] or '',
                'discogs_id': '',
                'popularity': r['popularity'] or 0,
                'rank': r['similarity_rank'] or 5,
                'ring': 1,
            })
            weight = max(1, 11 - (r['similarity_rank'] or 5))
            edges.append({'source': center_idx, 'target': idx, 'weight': weight})

        # Ring 2: Similar artists of ring 1 artists (from similar_artists table)
        ring1_ids = []
        for n in nodes[1:]:  # skip center
            for sid in [n.get('spotify_id'), n.get('itunes_id')]:
                if sid:
                    ring1_ids.append(sid)

        if ring1_ids:
            placeholders = ','.join(['?'] * len(ring1_ids))
            cursor.execute(f"""
                SELECT DISTINCT source_artist_id, similar_artist_name,
                       similar_artist_spotify_id, similar_artist_itunes_id,
                       similar_artist_deezer_id, image_url, genres, popularity, similarity_rank
                FROM similar_artists
                WHERE source_artist_id IN ({placeholders}) AND profile_id = ?
                ORDER BY similarity_rank ASC
            """, ring1_ids + [profile_id])

            for r in cursor.fetchall():
                nn = _norm(r['similar_artist_name'])
                if nn in seen:
                    # Create edge to existing node if not center
                    existing_idx = seen[nn]
                    # Find the ring1 node that sourced this
                    source_norm = None
                    for n in nodes[1:]:
                        for sid in [n.get('spotify_id'), n.get('itunes_id')]:
                            if sid == r['source_artist_id']:
                                source_norm = _norm(n['name'])
                                break
                        if source_norm:
                            break
                    if source_norm and source_norm in seen and existing_idx != seen[source_norm]:
                        edges.append({'source': seen[source_norm], 'target': existing_idx, 'weight': 3})
                    continue

                idx = len(nodes)
                if idx >= 500:  # Cap at 500 nodes for performance
                    break
                seen[nn] = idx
                genres = []
                if r['genres']:
                    try:
                        genres = json.loads(r['genres']) if isinstance(r['genres'], str) else []
                    except Exception:
                        pass
                img = r['image_url'] if r['image_url'] and r['image_url'].startswith('http') else ''
                nodes.append({
                    'id': idx, 'name': r['similar_artist_name'], 'image_url': img,
                    'type': 'ring2', 'genres': genres[:5],
                    'spotify_id': r['similar_artist_spotify_id'] or '',
                    'itunes_id': r['similar_artist_itunes_id'] or '',
                    'deezer_id': r['similar_artist_deezer_id'] or '',
                    'discogs_id': '',
                    'popularity': r['popularity'] or 0,
                    'rank': r['similarity_rank'] or 5,
                    'ring': 2,
                })
                # Find the ring1 source
                for n in nodes[1:]:
                    for sid in [n.get('spotify_id'), n.get('itunes_id')]:
                        if sid == r['source_artist_id']:
                            edges.append({'source': n['id'], 'target': idx, 'weight': max(1, 11 - (r['similarity_rank'] or 5))})
                            break

        # Backfill images/genres from ALL cache sources + Deezer fallback
        for n in nodes:
            # Clean up string "None" stored as image URL
            if n['image_url'] in ('None', 'null', ''):
                n['image_url'] = ''
            if n['image_url'] and n['genres']:
                continue
            # Check all cache entries for this artist (multiple sources)
            cursor.execute("""
                SELECT entity_id, source, image_url, genres FROM metadata_cache_entities
                WHERE entity_type = 'artist' AND name = ? COLLATE NOCASE
            """, (n['name'],))
            for cr in cursor.fetchall():
                if not n['image_url'] and cr['image_url'] and cr['image_url'].startswith('http'):
                    n['image_url'] = cr['image_url']
                if not n['genres'] and cr['genres']:
                    try:
                        n['genres'] = json.loads(cr['genres'])[:5] if isinstance(cr['genres'], str) else []
                    except Exception:
                        pass
                # Harvest missing IDs from cache
                src_map = {'spotify': 'spotify_id', 'itunes': 'itunes_id', 'deezer': 'deezer_id', 'discogs': 'discogs_id'}
                k = src_map.get(cr['source'])
                if k and not n.get(k):
                    n[k] = cr['entity_id']

            # Deezer image fallback — construct URL directly from ID
            if not n['image_url'] and n.get('deezer_id'):
                n['image_url'] = f"https://api.deezer.com/artist/{n['deezer_id']}/image?size=big"

            # Spotify image fallback — try API if authenticated
            if not n['image_url'] and n.get('spotify_id'):
                try:
                    if spotify_client and spotify_client.is_spotify_authenticated():
                        from core.api_call_tracker import api_call_tracker
                        api_call_tracker.record_call('spotify', endpoint='artist')
                        artist_data = spotify_client.sp.artist(n['spotify_id'])
                        if artist_data and artist_data.get('images'):
                            n['image_url'] = artist_data['images'][0]['url']
                            if not n['genres'] and artist_data.get('genres'):
                                n['genres'] = artist_data['genres'][:5]
                except Exception:
                    pass

            # Album art fallback (iTunes artists have no artist images)
            if not n['image_url']:
                cursor.execute("""
                    SELECT image_url FROM metadata_cache_entities
                    WHERE entity_type = 'album' AND image_url LIKE 'http%'
                      AND artist_name = ? COLLATE NOCASE LIMIT 1
                """, (n['name'],))
                alb = cursor.fetchone()
                if alb:
                    n['image_url'] = alb['image_url']

        logger.info(f"[Artist Explorer] Center: {center_name}, Ring 1: {sum(1 for n in nodes if n.get('ring')==1)}, Ring 2: {sum(1 for n in nodes if n.get('ring')==2)}, Edges: {len(edges)}")

        return jsonify({
            'success': True,
            'nodes': nodes,
            'edges': edges,
            'center': center_name,
        })
    except Exception as e:
        logger.error(f"Error getting artist explorer data: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500
