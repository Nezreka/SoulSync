"""
Spotify Public Scraper - Fetches playlist/album data from Spotify's embed endpoint
without requiring API authentication. Uses the __NEXT_DATA__ JSON embedded in the page.
"""

import re
import json
import logging
import hashlib
import requests

logger = logging.getLogger(__name__)


def parse_spotify_url(url: str) -> dict:
    """
    Parse a Spotify URL and extract the type (playlist/album) and ID.

    Supports:
        - https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
        - https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy
        - spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
        - URLs with query params (?si=...) or trailing paths

    Returns: {type: 'playlist'|'album', id: str} or None
    """
    if not url:
        return None

    url = url.strip()

    # Handle spotify: URIs
    uri_match = re.match(r'spotify:(playlist|album):([a-zA-Z0-9]+)', url)
    if uri_match:
        return {'type': uri_match.group(1), 'id': uri_match.group(2)}

    # Handle web URLs
    url_match = re.match(
        r'https?://open\.spotify\.com/(playlist|album)/([a-zA-Z0-9]+)',
        url
    )
    if url_match:
        return {'type': url_match.group(1), 'id': url_match.group(2)}

    return None


def scrape_spotify_embed(spotify_type: str, spotify_id: str) -> dict:
    """
    Scrape track data from Spotify's embed endpoint.

    Returns:
        {
            'id': str,
            'type': 'playlist' | 'album',
            'name': str,
            'subtitle': str (owner for playlists, artist for albums),
            'tracks': [
                {
                    'id': str (Spotify track ID),
                    'name': str,
                    'artists': [{'name': str}],
                    'duration_ms': int,
                    'is_explicit': bool,
                    'track_number': int
                }
            ],
            'url_hash': str
        }
    """
    embed_url = f'https://open.spotify.com/embed/{spotify_type}/{spotify_id}'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    try:
        response = requests.get(embed_url, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch Spotify embed: {e}")
        return {'error': f'Failed to fetch Spotify page: {str(e)}'}

    # Extract __NEXT_DATA__ JSON
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        response.text
    )
    if not match:
        logger.error("No __NEXT_DATA__ found in Spotify embed response")
        return {'error': 'Could not parse Spotify page. The page format may have changed.'}

    try:
        next_data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse __NEXT_DATA__ JSON: {e}")
        return {'error': 'Failed to parse Spotify data'}

    # Navigate to entity data
    try:
        entity = next_data['props']['pageProps']['state']['data']['entity']
    except (KeyError, TypeError) as e:
        logger.error(f"Unexpected embed data structure: {e}")
        return {'error': 'Unexpected Spotify data format'}

    track_list = entity.get('trackList', [])
    if not track_list:
        return {'error': 'No tracks found in this Spotify link'}

    # Parse tracks into standardized format
    tracks = []
    for i, raw_track in enumerate(track_list):
        # Extract track ID from URI (spotify:track:XXXX)
        uri = raw_track.get('uri', '')
        track_id_match = re.match(r'spotify:track:([a-zA-Z0-9]+)', uri)
        if not track_id_match:
            continue

        track_id = track_id_match.group(1)

        # Parse artists from subtitle (separated by non-breaking spaces or commas)
        subtitle = raw_track.get('subtitle', '')
        # Replace non-breaking spaces used as separators
        artist_names = [a.strip() for a in subtitle.replace('\xa0', '').split(',') if a.strip()]
        if not artist_names:
            artist_names = ['Unknown Artist']

        tracks.append({
            'id': track_id,
            'name': raw_track.get('title', 'Unknown Track'),
            'artists': [{'name': name} for name in artist_names],
            'duration_ms': raw_track.get('duration', 0),
            'is_explicit': raw_track.get('isExplicit', False),
            'track_number': i + 1
        })

    # Generate URL hash for state management
    source_url = f'https://open.spotify.com/{spotify_type}/{spotify_id}'
    url_hash = hashlib.md5(source_url.encode()).hexdigest()[:12]

    result = {
        'id': spotify_id,
        'type': entity.get('type', spotify_type),
        'name': entity.get('name', 'Unknown'),
        'subtitle': entity.get('subtitle', ''),
        'tracks': tracks,
        'url': source_url,
        'url_hash': url_hash
    }

    logger.info(f"Scraped Spotify {spotify_type}: {result['name']} ({len(tracks)} tracks)")
    return result
