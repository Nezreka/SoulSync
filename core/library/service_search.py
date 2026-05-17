"""Library manual-match service search — lifted from web_server.py.

Both function bodies are byte-identical to the originals. Enrichment
worker handles are injected at runtime via init() because the workers
are constructed after this module is imported.
"""
import logging

logger = logging.getLogger(__name__)

# Injected at runtime via init() — these workers are constructed in
# web_server.py and bound here once they exist.
spotify_enrichment_worker = None
itunes_enrichment_worker = None
mb_worker = None
lastfm_worker = None
genius_worker = None
tidal_enrichment_worker = None
qobuz_enrichment_worker = None
discogs_worker = None
audiodb_worker = None
amazon_worker = None


def init(
    spotify_worker=None,
    itunes_worker=None,
    musicbrainz_worker=None,
    lastfm_worker_obj=None,
    genius_worker_obj=None,
    tidal_worker=None,
    qobuz_worker=None,
    discogs_worker_obj=None,
    audiodb_worker_obj=None,
    amazon_worker_obj=None,
):
    """Bind enrichment worker handles so the lifted bodies can use them."""
    global spotify_enrichment_worker, itunes_enrichment_worker, mb_worker
    global lastfm_worker, genius_worker, tidal_enrichment_worker
    global qobuz_enrichment_worker, discogs_worker, audiodb_worker, amazon_worker
    spotify_enrichment_worker = spotify_worker
    itunes_enrichment_worker = itunes_worker
    mb_worker = musicbrainz_worker
    lastfm_worker = lastfm_worker_obj
    genius_worker = genius_worker_obj
    tidal_enrichment_worker = tidal_worker
    qobuz_enrichment_worker = qobuz_worker
    discogs_worker = discogs_worker_obj
    audiodb_worker = audiodb_worker_obj
    amazon_worker = amazon_worker_obj


def _detect_provider(items, client):
    """Detect actual provider from result IDs. Spotify IDs are alphanumeric;
    iTunes/Deezer IDs are purely numeric. If the results have numeric IDs,
    they came from the fallback source, not Spotify."""
    if items and str(items[0].id).isdigit():
        return client._fallback_source
    return 'spotify'


def _search_service(service, entity_type, query):
    """Search a service and return normalized results."""
    import requests as req_lib

    if service == 'spotify':
        if not spotify_enrichment_worker or not spotify_enrichment_worker.client:
            raise ValueError("Spotify worker not initialized")
        client = spotify_enrichment_worker.client
        if entity_type == 'artist':
            items = client.search_artists(query, limit=8)
            # Detect actual provider from result IDs — Spotify IDs are alphanumeric,
            # iTunes/Deezer IDs are purely numeric. Prevents storing wrong IDs.
            provider = _detect_provider(items, client)
            return [{'id': a.id, 'name': a.name, 'image': a.image_url, 'extra': ', '.join(a.genres[:3]) if a.genres else '', 'provider': provider} for a in items]
        elif entity_type == 'album':
            items = client.search_albums(query, limit=8)
            provider = _detect_provider(items, client)
            return [{'id': a.id, 'name': a.name, 'image': a.image_url, 'extra': f"{', '.join(a.artists)} · {a.release_date or ''}", 'provider': provider} for a in items]
        elif entity_type == 'track':
            items = client.search_tracks(query, limit=8)
            provider = _detect_provider(items, client)
            return [{'id': t.id, 'name': t.name, 'image': t.image_url, 'extra': f"{', '.join(t.artists)} · {t.album or ''}", 'provider': provider} for t in items]

    elif service == 'itunes':
        if not itunes_enrichment_worker or not itunes_enrichment_worker.client:
            raise ValueError("iTunes worker not initialized")
        client = itunes_enrichment_worker.client
        if entity_type == 'artist':
            items = client.search_artists(query, limit=8)
            return [{'id': a.id, 'name': a.name, 'image': a.image_url, 'extra': ', '.join(a.genres[:3]) if a.genres else ''} for a in items]
        elif entity_type == 'album':
            items = client.search_albums(query, limit=8)
            return [{'id': a.id, 'name': a.name, 'image': a.image_url, 'extra': f"{', '.join(a.artists)} · {a.release_date or ''}"} for a in items]
        elif entity_type == 'track':
            items = client.search_tracks(query, limit=8)
            return [{'id': t.id, 'name': t.name, 'image': t.image_url, 'extra': f"{', '.join(t.artists)} · {t.album or ''}"} for t in items]

    elif service == 'musicbrainz':
        if not mb_worker or not mb_worker.mb_service:
            raise ValueError("MusicBrainz worker not initialized")
        mb_client = mb_worker.mb_service.mb_client
        if entity_type == 'artist':
            items = mb_client.search_artist(query, limit=8)
            return [{'id': a['id'], 'name': a.get('name', ''), 'image': None,
                      'extra': f"Score: {a.get('score', '')} · {a.get('disambiguation', '') or a.get('country', '')}"} for a in items]
        elif entity_type == 'album':
            items = mb_client.search_release(query, limit=8)
            results = []
            for r in items:
                artists = ', '.join(ac.get('name', '') for ac in r.get('artist-credit', []) if isinstance(ac, dict))
                # Cover Art Archive provides album art by release MBID
                cover_url = f"https://coverartarchive.org/release/{r['id']}/front-250" if r.get('id') else None
                results.append({'id': r['id'], 'name': r.get('title', ''), 'image': cover_url,
                                'extra': f"{artists} · {r.get('date', '')} · Score: {r.get('score', '')}"})
            return results
        elif entity_type == 'track':
            items = mb_client.search_recording(query, limit=8)
            results = []
            for r in items:
                artists = ', '.join(ac.get('name', '') for ac in r.get('artist-credit', []) if isinstance(ac, dict))
                results.append({'id': r['id'], 'name': r.get('title', ''), 'image': None,
                                'extra': f"{artists} · Score: {r.get('score', '')}"})
            return results

    elif service == 'deezer':
        # Deezer client only returns single results, so hit the API directly for multiple
        type_map = {'artist': 'artist', 'album': 'album', 'track': 'track'}
        deezer_type = type_map.get(entity_type, 'track')
        try:
            resp = req_lib.get(f'https://api.deezer.com/search/{deezer_type}', params={'q': query, 'limit': 8}, timeout=10)
            data = resp.json().get('data', [])
        except Exception:
            data = []
        results = []
        for item in data:
            if entity_type == 'artist':
                results.append({'id': str(item.get('id', '')), 'name': item.get('name', ''),
                                'image': item.get('picture_medium'), 'extra': f"{item.get('nb_fan', 0)} fans"})
            elif entity_type == 'album':
                artist_name = item.get('artist', {}).get('name', '') if isinstance(item.get('artist'), dict) else ''
                results.append({'id': str(item.get('id', '')), 'name': item.get('title', ''),
                                'image': item.get('cover_medium'), 'extra': artist_name})
            elif entity_type == 'track':
                artist_name = item.get('artist', {}).get('name', '') if isinstance(item.get('artist'), dict) else ''
                album_name = item.get('album', {}).get('title', '') if isinstance(item.get('album'), dict) else ''
                results.append({'id': str(item.get('id', '')), 'name': item.get('title', ''),
                                'image': item.get('album', {}).get('cover_medium') if isinstance(item.get('album'), dict) else None,
                                'extra': f"{artist_name} · {album_name}"})
        return results

    elif service == 'lastfm':
        if not lastfm_worker or not lastfm_worker.client:
            raise ValueError("Last.fm worker not initialized")
        client = lastfm_worker.client
        if entity_type == 'artist':
            result = client.search_artist(query)
            if result:
                image = client.get_best_image(result.get('image', []))
                return [{'id': result.get('url', ''), 'name': result.get('name', ''),
                         'image': image, 'extra': f"{result.get('listeners', '0')} listeners"}]
        elif entity_type == 'album':
            result = client.search_album(query, '')
            if result:
                image = client.get_best_image(result.get('image', []))
                return [{'id': result.get('url', ''), 'name': result.get('name', ''),
                         'image': image, 'extra': result.get('artist', '')}]
        elif entity_type == 'track':
            # search_track takes separate artist/track params
            parts = query.split(' - ', 1) if ' - ' in query else ['', query]
            result = client.search_track(parts[0], parts[1])
            if result:
                artist_name = result.get('artist', '')
                return [{'id': result.get('url', ''), 'name': result.get('name', ''),
                         'image': None, 'extra': f"{artist_name} · {result.get('listeners', '0')} listeners"}]
        return []

    elif service == 'genius':
        if not genius_worker or not genius_worker.client:
            raise ValueError("Genius worker not initialized")
        client = genius_worker.client
        if entity_type == 'artist':
            artists = client.search_artists(query, limit=8)
            return [{'id': str(a.get('id', '')), 'name': a.get('name', ''),
                     'image': a.get('image_url'), 'extra': a.get('url', '')} for a in artists]
        elif entity_type == 'track':
            # Search with broader results for manual matching
            hits = client.search(f"{query}", per_page=10)
            results = []
            seen_ids = set()
            for hit in hits:
                r = hit.get('result', {})
                rid = r.get('id')
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    results.append({'id': str(rid), 'name': r.get('title', ''),
                                    'image': r.get('song_art_image_url'), 'extra': r.get('artist_names', '')})
            return results
        return []

    elif service == 'tidal':
        if not tidal_enrichment_worker or not tidal_enrichment_worker.client:
            raise ValueError("Tidal worker not initialized")
        client = tidal_enrichment_worker.client
        if entity_type == 'artist':
            result = client.search_artist(query)
            if result:
                thumb = result.get('picture', '')
                if isinstance(thumb, list) and thumb:
                    thumb = thumb[0].get('url', '') if isinstance(thumb[0], dict) else str(thumb[0])
                return [{'id': str(result.get('id', '')), 'name': result.get('name', ''),
                         'image': thumb if isinstance(thumb, str) else None, 'extra': ''}]
        elif entity_type == 'album':
            result = client.search_album('', query)
            if result:
                return [{'id': str(result.get('id', '')), 'name': result.get('title', ''),
                         'image': None, 'extra': result.get('artist', {}).get('name', '') if isinstance(result.get('artist'), dict) else ''}]
        elif entity_type == 'track':
            result = client.search_track('', query)
            if result:
                artist_name = result.get('artist', {}).get('name', '') if isinstance(result.get('artist'), dict) else ''
                return [{'id': str(result.get('id', '')), 'name': result.get('title', ''),
                         'image': None, 'extra': artist_name}]
        return []

    elif service == 'qobuz':
        if not qobuz_enrichment_worker or not qobuz_enrichment_worker.client:
            raise ValueError("Qobuz worker not initialized")
        client = qobuz_enrichment_worker.client
        if entity_type == 'artist':
            result = client.search_artist(query)
            if result:
                image = result.get('image', {})
                thumb = image.get('large', image.get('medium', '')) if isinstance(image, dict) else ''
                return [{'id': str(result.get('id', '')), 'name': result.get('name', ''),
                         'image': thumb, 'extra': ''}]
        elif entity_type == 'album':
            result = client.search_album('', query)
            if result:
                artist_name = result.get('artist', {}).get('name', '') if isinstance(result.get('artist'), dict) else ''
                image = result.get('image', {})
                thumb = image.get('large', image.get('medium', '')) if isinstance(image, dict) else ''
                return [{'id': str(result.get('id', '')), 'name': result.get('title', ''),
                         'image': thumb, 'extra': artist_name}]
        elif entity_type == 'track':
            result = client.search_track('', query)
            if result:
                artist_name = result.get('performer', {}).get('name', '') if isinstance(result.get('performer'), dict) else ''
                if not artist_name:
                    artist_name = result.get('artist', {}).get('name', '') if isinstance(result.get('artist'), dict) else ''
                return [{'id': str(result.get('id', '')), 'name': result.get('title', ''),
                         'image': None, 'extra': artist_name}]
        return []

    elif service == 'discogs':
        if not discogs_worker or not discogs_worker.client:
            raise ValueError("Discogs worker not initialized")
        client = discogs_worker.client
        if entity_type == 'artist':
            items = client.search_artists(query, limit=8)
            return [{'id': str(a.id), 'name': a.name, 'image': a.image_url,
                     'extra': ', '.join(a.genres[:3]) if a.genres else ''} for a in items]
        elif entity_type == 'album':
            items = client.search_albums(query, limit=8)
            return [{'id': str(a.id), 'name': a.name, 'image': a.image_url,
                     'extra': f"{', '.join(a.artists)} · {a.release_date or ''}"} for a in items]
        elif entity_type == 'track':
            items = client.search_tracks(query, limit=8)
            return [{'id': str(t.id), 'name': t.name, 'image': t.image_url,
                     'extra': f"{', '.join(t.artists)} · {t.album or ''}"} for t in items]
        return []

    elif service == 'audiodb':
        if not audiodb_worker or not audiodb_worker.client:
            raise ValueError("AudioDB worker not initialized")
        client = audiodb_worker.client
        result = None
        if entity_type == 'artist':
            result = client.search_artist(query)
        elif entity_type == 'album':
            # AudioDB album search needs artist + album, try query as-is
            parts = query.split(' - ', 1) if ' - ' in query else [query, '']
            result = client.search_album(parts[0], parts[1] if len(parts) > 1 else query)
        elif entity_type == 'track':
            parts = query.split(' - ', 1) if ' - ' in query else [query, '']
            result = client.search_track(parts[0], parts[1] if len(parts) > 1 else query)
        if result:
            if entity_type == 'artist':
                return [{'id': str(result.get('idArtist', '')), 'name': result.get('strArtist', ''),
                         'image': result.get('strArtistThumb'), 'extra': result.get('strGenre', '')}]
            elif entity_type == 'album':
                return [{'id': str(result.get('idAlbum', '')), 'name': result.get('strAlbum', ''),
                         'image': result.get('strAlbumThumb'), 'extra': f"{result.get('strArtist', '')} · {result.get('intYearReleased', '')}"}]
            elif entity_type == 'track':
                return [{'id': str(result.get('idTrack', '')), 'name': result.get('strTrack', ''),
                         'image': None, 'extra': f"{result.get('strArtist', '')} · {result.get('strAlbum', '')}"}]
        return []

    elif service == 'amazon':
        if not amazon_worker or not amazon_worker.client:
            raise ValueError("Amazon worker not initialized")
        client = amazon_worker.client
        if entity_type == 'artist':
            items = client.search_artists(query, limit=8)
            return [{'id': str(a.id), 'name': a.name, 'image': a.image_url,
                     'extra': ', '.join(a.genres[:3]) if a.genres else ''} for a in items]
        elif entity_type == 'album':
            items = client.search_albums(query, limit=8)
            return [{'id': str(a.id), 'name': a.name, 'image': a.image_url,
                     'extra': f"{', '.join(a.artists)} · {a.release_date or ''}"} for a in items]
        elif entity_type == 'track':
            items = client.search_tracks(query, limit=8)
            return [{'id': str(t.id), 'name': t.name, 'image': t.image_url,
                     'extra': f"{', '.join(t.artists)} · {t.album or ''}"} for t in items]
        return []

    return []
