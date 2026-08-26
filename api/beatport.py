"""Beatport browse + scrape endpoints, lifted out of web_server.py.

Everything here is the SELF-CONTAINED slice of the Beatport surface: homepage
sections, genre pages, top-100s, chart extraction, release scraping and the
track-enrichment job pair — plus the in-process scrape cache that keeps those
endpoints from hammering beatport.com. None of it touches the discovery/sync
machinery; the routes that do (``/api/beatport/discovery/*``, ``/sync/*``,
``/charts*``) stay in web_server.py with the other discovery wiring, exactly
the split the discovery lift chose.

Two web_server helpers are injected via :func:`configure` rather than imported
(importing web_server from here would be circular): ``add_activity_item`` and
``add_cache_headers``. ``beatport_data_cache`` lives HERE now; web_server
imports it for the automation engine's cache-refresh dependency.
"""

from __future__ import annotations

import collections
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urljoin

from flask import Blueprint, jsonify, request

from beatport_unified_scraper import BeatportUnifiedScraper
from utils.logging_config import get_logger

logger = get_logger("api.beatport")

bp = Blueprint("beatport", __name__)

# Injected by configure() at boot — handlers only run after that.
add_activity_item = None
add_cache_headers = None
get_metadata_cache = None


def configure(*, activity_item, cache_headers, metadata_cache):
    """Hand over the web_server helpers the moved handlers call."""
    global add_activity_item, add_cache_headers, get_metadata_cache
    add_activity_item = activity_item
    add_cache_headers = cache_headers
    get_metadata_cache = metadata_cache


def create_blueprint():
    return bp


def clean_beatport_text(text):
    """Clean Beatport track/artist text for proper spacing"""
    if not text:
        return text

    import re
    # Fix common spacing issues
    text = re.sub(r'([a-z$!@#%&*])([A-Z])', r'\1 \2', text)  # Add space between lowercase/symbols and uppercase
    text = re.sub(r'([a-zA-Z]),([a-zA-Z])', r'\1, \2', text)  # Add space after comma
    text = re.sub(r'([a-zA-Z])(Mix|Remix|Extended|Version)\b', r'\1 \2', text)  # Fix mix types
    text = re.sub(r'\s+', ' ', text)  # Collapse multiple spaces
    text = text.strip()

    return text



# Beatport Data Cache
# Cache Beatport scraping data to reduce load times and avoid hammering Beatport.com
beatport_data_cache = {
    'homepage': {
        'hero_tracks': {'data': None, 'timestamp': 0, 'ttl': 86400},       # 24 hours
        'top_10_lists': {'data': None, 'timestamp': 0, 'ttl': 86400},      # 24 hours
        'top_10_releases': {'data': None, 'timestamp': 0, 'ttl': 86400},   # 24 hours
        'new_releases': {'data': None, 'timestamp': 0, 'ttl': 86400},      # 24 hours
        'hype_picks': {'data': None, 'timestamp': 0, 'ttl': 86400},       # 24 hours
        'featured_charts': {'data': None, 'timestamp': 0, 'ttl': 86400},   # 24 hours
        'dj_charts': {'data': None, 'timestamp': 0, 'ttl': 86400},         # 24 hours
    },
    'genre': {
        # Future expansion for genre-specific caching
        # 'house': {'top_10': {...}, 'releases': {...}},
        # 'techno': {'top_10': {...}, 'releases': {...}}
    },
    'cache_lock': threading.Lock(),
}



def get_cached_beatport_data(section_type, data_key, genre_slug=None):
    """
    Get Beatport data from cache if valid, otherwise return None.

    Args:
        section_type: 'homepage' or 'genre'
        data_key: specific data type (e.g., 'hero_tracks', 'top_10_lists')
        genre_slug: only used for genre section_type

    Returns:
        Cached data if valid, None if cache miss or expired
    """
    current_time = time.time()

    with beatport_data_cache['cache_lock']:
        try:
            if section_type == 'homepage':
                cache_entry = beatport_data_cache['homepage'].get(data_key)
            elif section_type == 'genre' and genre_slug:
                cache_entry = beatport_data_cache['genre'].get(genre_slug, {}).get(data_key)
            else:
                return None

            if not cache_entry:
                return None

            # Check if cache is still valid
            age = current_time - cache_entry['timestamp']
            if age < cache_entry['ttl'] and cache_entry['data'] is not None:
                logger.debug(f"Cache HIT for {section_type}/{data_key} (age: {age:.1f}s)")
                return cache_entry['data']
            else:
                logger.debug(f"⏰ Cache MISS for {section_type}/{data_key} (age: {age:.1f}s, ttl: {cache_entry['ttl']}s)")
                return None

        except Exception as e:
            logger.error(f"Cache lookup error for {section_type}/{data_key}: {e}")
            return None

def set_cached_beatport_data(section_type, data_key, data, genre_slug=None):
    """
    Store Beatport data in cache with current timestamp.

    Args:
        section_type: 'homepage' or 'genre'
        data_key: specific data type (e.g., 'hero_tracks', 'top_10_lists')
        data: the data to cache
        genre_slug: only used for genre section_type
    """
    current_time = time.time()

    with beatport_data_cache['cache_lock']:
        try:
            if section_type == 'homepage':
                if data_key in beatport_data_cache['homepage']:
                    beatport_data_cache['homepage'][data_key]['data'] = data
                    beatport_data_cache['homepage'][data_key]['timestamp'] = current_time
                    logger.info(f"Cached {section_type}/{data_key} (ttl: {beatport_data_cache['homepage'][data_key]['ttl']}s)")
            elif section_type == 'genre' and genre_slug:
                # Initialize genre cache if not exists
                if genre_slug not in beatport_data_cache['genre']:
                    beatport_data_cache['genre'][genre_slug] = {}

                # For genre caching, we need to define TTL structure (for future use)
                if data_key not in beatport_data_cache['genre'][genre_slug]:
                    beatport_data_cache['genre'][genre_slug][data_key] = {
                        'data': None, 'timestamp': 0, 'ttl': 600  # Default 10 minutes
                    }

                beatport_data_cache['genre'][genre_slug][data_key]['data'] = data
                beatport_data_cache['genre'][genre_slug][data_key]['timestamp'] = current_time
                logger.info(f"Cached {section_type}/{genre_slug}/{data_key}")

        except Exception as e:
            logger.error(f"Cache storage error for {section_type}/{data_key}: {e}")



# --- Beatport Data API ---

@bp.route('/api/beatport/hero-tracks')
def get_beatport_hero_tracks():
    """Get fresh tracks from Beatport hero slideshow for the rebuild slider"""
    try:
        logger.info("Fetching Beatport hero tracks...")

        # Check cache first
        cached_data = get_cached_beatport_data('homepage', 'hero_tracks')
        if cached_data:
            logger.info("Returning cached hero tracks data")
            response = jsonify(cached_data)
            return add_cache_headers(response, 3600)  # 1 hour

        # Cache miss - scrape fresh data
        logger.info("Cache miss - scraping fresh hero tracks data...")

        # Initialize scraper
        scraper = BeatportUnifiedScraper()

        # Get tracks from hero slideshow (increased limit to capture all slides)
        tracks = scraper.scrape_new_on_beatport_hero(limit=15)

        # SMART FILTERING - Remove duplicates and invalid tracks
        valid_tracks = []
        seen_urls = set()
        filtered_reasons = collections.Counter()

        for _i, track in enumerate(tracks):
            # Extract and clean basic data
            title = track.get('title', '').strip()
            artist = track.get('artist', '').strip()
            url = track.get('url', '').strip()
            image_url = track.get('image_url', '').strip()

            # Apply text cleaning for proper spacing
            if title:
                title = clean_beatport_text(title)
            if artist:
                artist = clean_beatport_text(artist)

            # Validation filters
            is_valid = True
            skip_reasons = []

            # Filter 1: Must have title (artist can be fallback)
            if not title or title in ['No title', 'MISSING', 'Unknown Title']:
                is_valid = False
                skip_reasons.append("Missing/invalid title")

            # If no artist, use fallback based on URL or default
            if not artist or artist in ['No artist', 'MISSING', 'Unknown Artist', 'NO_ARTIST']:
                if url and '/release/' in url:
                    artist = 'Various Artists'  # Release pages often have multiple artists
                else:
                    artist = 'Unknown Artist'

            # Filter 2: Must have valid URL and image
            if not url or not image_url:
                is_valid = False
                skip_reasons.append("Missing URL or image")

            # Filter 3: URL must be a track/release page (not promotional pages)
            if url and not any(pattern in url for pattern in ['/release/', '/track/']):
                is_valid = False
                skip_reasons.append("URL is not a track/release page")

            # Filter 4: Deduplication by URL (most reliable method)
            if url in seen_urls:
                is_valid = False
                skip_reasons.append("Duplicate URL")

            if not is_valid:
                filtered_reasons.update(skip_reasons)
                continue

            # Mark URL as seen for deduplication
            seen_urls.add(url)

            # Clean up title
            title = title.replace(" t ", "'t ").replace("(Extended)DJ", "(Extended)")

            # Clean up artist names
            if 'SyrossianHappy' in artist:
                artist = 'Darius Syrossian'
            if 'Carroll,' in artist:
                artist = 'Ron Carroll'
            if artist.endswith('DJ') and ' ' not in artist[-4:]:
                artist = artist[:-2].strip()

            # Create clean track data
            track_data = {
                'title': title,
                'artist': artist,
                'url': url,
                'image_url': image_url,
                'genre': 'Electronic',  # Default genre
                'year': datetime.now().year
            }

            # Determine genre based on artist
            genre_mapping = {
                'thakzin': 'Afro House',
                'yaya': 'Tech House',
                'darius syrossian': 'Techno',
                'ron carroll': 'House',
                'dj minx': 'House',
                'durante': 'Progressive House'
            }

            for artist_key, mapped_genre in genre_mapping.items():
                if artist_key in artist.lower():
                    track_data['genre'] = mapped_genre
                    break

            valid_tracks.append(track_data)

        sample_titles = [f"{t['title']} - {t['artist']}" for t in valid_tracks[:3]]
        logger.debug(
            "Beatport smart filter summary: raw=%s valid=%s filtered=%s reasons=%s sample=%s",
            len(tracks),
            len(valid_tracks),
            len(tracks) - len(valid_tracks),
            dict(filtered_reasons),
            sample_titles,
        )

        logger.info(f"Retrieved {len(valid_tracks)} valid unique Beatport tracks (SMART FILTERING)")

        # Prepare response data
        response_data = {
            'success': True,
            'tracks': valid_tracks,
            'count': len(valid_tracks),
            'timestamp': datetime.now().isoformat()
        }

        # Cache the successful response
        set_cached_beatport_data('homepage', 'hero_tracks', response_data)

        response = jsonify(response_data)
        return add_cache_headers(response, 3600)  # 1 hour

    except Exception as e:
        logger.error(f"Error fetching Beatport tracks: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'tracks': []
        }), 500

@bp.route('/api/beatport/new-releases')
def get_beatport_new_releases():
    """Get new releases from Beatport for the rebuild slider grid"""
    try:
        logger.info("🆕 Fetching Beatport new releases...")

        # Check cache first
        cached_data = get_cached_beatport_data('homepage', 'new_releases')
        if cached_data:
            logger.info("🆕 Returning cached new releases data")
            response = jsonify(cached_data)
            return add_cache_headers(response, 3600)  # 1 hour

        # Cache miss - scrape fresh data
        logger.info("Cache miss - scraping fresh new releases data...")

        # Initialize scraper
        scraper = BeatportUnifiedScraper()

        # Get page and extract releases
        soup = scraper.get_page(scraper.base_url)
        if not soup:
            raise Exception("Could not fetch Beatport homepage")

        # Find New Releases GridSlider container by section heading
        # Use partial class match to avoid brittle hashed class names
        gridsliders = soup.select('[class*="GridSlider-style__Wrapper"]')
        releases_container = None

        for container in gridsliders:
            h2 = container.select_one('h2')
            if h2:
                title = h2.get_text(strip=True).lower()
                if 'new release' in title:
                    releases_container = container
                    logger.info(f"🆕 FOUND NEW RELEASES: '{h2.get_text(strip=True)}'")
                    break

        # Fallback: try ReleaseCard partial class match on whole page
        if releases_container:
            release_cards = releases_container.select('[class*="ReleaseCard-style__Wrapper"]')
        else:
            logger.warning("No New Releases GridSlider found, trying page-wide ReleaseCard search")
            release_cards = soup.select('[class*="ReleaseCard-style__Wrapper"]')

        releases = []

        logger.info(f"Found {len(release_cards)} release cards")

        for _i, card in enumerate(release_cards[:100]):  # Limit to 100 for 10 slides
            release_data = {}

            # Extract title from Meta section
            title_elem = card.select_one('[class*="ReleaseCard-style__Meta"] a[href*="/release/"]')
            if not title_elem:
                title_elem = card.select_one('[class*="title"], [class*="Title"], h3, h4, h5, h6')
            if title_elem:
                title_text = title_elem.get_text(strip=True)
                if title_text and len(title_text) > 2 and title_text not in ['New Releases', 'Buy', 'Play']:
                    release_data['title'] = title_text

            # Extract artist
            artist_elem = card.select_one('a[href*="/artist/"]')
            if artist_elem:
                artist_text = artist_elem.get_text(strip=True)
                if artist_text and len(artist_text) > 1:
                    release_data['artist'] = artist_text

            # Extract label
            label_elem = card.select_one('a[href*="/label/"]')
            if label_elem:
                label_text = label_elem.get_text(strip=True)
                if label_text and len(label_text) > 1:
                    release_data['label'] = label_text

            # Extract URL
            url_link = card.select_one('a[href*="/release/"]')
            if url_link:
                href = url_link.get('href')
                if href:
                    release_data['url'] = urljoin(scraper.base_url, href)

            # Extract image
            img = card.select_one('img')
            if img:
                src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                if src:
                    release_data['image_url'] = src

            # URL fallback for title
            if not release_data.get('title') and release_data.get('url'):
                url_parts = release_data['url'].split('/release/')
                if len(url_parts) > 1:
                    slug = url_parts[1].split('/')[0]
                    release_data['title'] = slug.replace('-', ' ').title()

            # Only add if we have essential data
            if release_data.get('title') and release_data.get('url'):
                # Add fallbacks for missing data
                if not release_data.get('artist'):
                    release_data['artist'] = 'Various Artists'
                if not release_data.get('label'):
                    release_data['label'] = 'Unknown Label'

                releases.append(release_data)

        logger.info(f"Successfully extracted {len(releases)} new releases")

        # Prepare response data
        response_data = {
            'success': True,
            'releases': releases,
            'count': len(releases),
            'slides': (len(releases) + 9) // 10,  # Calculate number of slides needed
            'timestamp': datetime.now().isoformat()
        }

        # Cache the successful response
        set_cached_beatport_data('homepage', 'new_releases', response_data)

        response = jsonify(response_data)
        return add_cache_headers(response, 3600)  # 1 hour

    except Exception as e:
        logger.error(f"Error fetching new releases: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'releases': []
        }), 500


@bp.route('/api/beatport/featured-charts')
def get_beatport_featured_charts():
    """Get featured charts from Beatport for the charts slider grid using GridSlider approach"""
    try:
        logger.info("Fetching Beatport featured charts...")

        # Check cache first
        cached_data = get_cached_beatport_data('homepage', 'featured_charts')
        if cached_data:
            logger.info("Returning cached featured charts data")
            response = jsonify(cached_data)
            return add_cache_headers(response, 3600)  # 1 hour

        # Cache miss - scrape fresh data
        logger.info("Cache miss - scraping fresh featured charts data...")

        # Initialize scraper
        scraper = BeatportUnifiedScraper()

        # Get page and extract charts
        soup = scraper.get_page(scraper.base_url)
        if not soup:
            raise Exception("Could not fetch Beatport homepage")

        # Find Featured Charts GridSlider container (like New Releases)
        gridsliders = soup.select('[class*="GridSlider-style__Wrapper"]')
        featured_container = None

        logger.debug(f"Checking {len(gridsliders)} GridSlider containers for featured charts...")

        for container in gridsliders:
            h2 = container.select_one('h2')
            if h2:
                title = h2.get_text(strip=True).lower()
                logger.debug(f"Found section: '{h2.get_text(strip=True)}'")

                if 'featured' in title and 'chart' in title:
                    featured_container = container
                    logger.debug(f"FOUND FEATURED CHARTS: '{h2.get_text(strip=True)}'")
                    break

        if not featured_container:
            logger.warning("No Featured Charts GridSlider container found")
            return jsonify({
                'success': False,
                'error': 'Featured Charts section not found',
                'charts': []
            })

        # Extract charts from the container using chart links
        charts = []
        chart_links = featured_container.select('a[href*="/chart/"]')

        logger.debug(f"Found {len(chart_links)} chart links in Featured Charts section")

        for _i, link in enumerate(chart_links[:100]):  # Limit to 100 for 10 slides
            chart_data = {}

            # Extract chart name from link text or nearby elements
            name_elem = link.select_one('h3, h4, h5, h6, [class*="title"], [class*="Title"], [class*="name"], [class*="Name"]')
            if name_elem:
                name_text = name_elem.get_text(strip=True)
            else:
                name_text = link.get_text(strip=True)

            if name_text and len(name_text) > 2 and name_text.lower() not in ['featured charts', 'buy', 'play']:
                chart_data['name'] = name_text

                # Extract creator using the specific CSS class pattern from chart cards
                creator = 'Beatport'  # Default

                # Look for the ChartCard Name class that contains the creator
                creator_elem = link.select_one('[class*="ChartCard-style__Name"]')
                if creator_elem:
                    creator_text = creator_elem.get_text(strip=True)
                    if creator_text and len(creator_text) > 1 and creator_text.lower() not in ['by', 'chart', 'featured', 'beatport']:
                        creator = creator_text
                    elif creator_text.lower() == 'beatport':
                        creator = 'Beatport'
                else:
                    # Fallback: look for other creator indicators
                    parent = link.parent
                    if parent:
                        fallback_selectors = [
                            '[class*="artist"]', '[class*="Artist"]',
                            '[class*="creator"]', '[class*="Creator"]',
                            '[class*="author"]', '[class*="Author"]'
                        ]

                        for selector in fallback_selectors:
                            fallback_elem = parent.select_one(selector)
                            if fallback_elem:
                                fallback_text = fallback_elem.get_text(strip=True)
                                if fallback_text and len(fallback_text) > 1 and fallback_text.lower() not in ['by', 'chart', 'featured']:
                                    creator = fallback_text
                                    break

                chart_data['creator'] = creator

                # Extract URL
                href = link.get('href', '')
                if href:
                    if href.startswith('/'):
                        chart_data['url'] = f"https://www.beatport.com{href}"
                    else:
                        chart_data['url'] = href

                # Extract image
                img_elem = link.select_one('img') or (link.parent.select_one('img') if link.parent else None)
                if img_elem:
                    src = img_elem.get('src', '') or img_elem.get('data-src', '')
                    if src:
                        if src.startswith('//'):
                            src = f"https:{src}"
                        elif src.startswith('/'):
                            src = f"https://www.beatport.com{src}"
                        chart_data['image'] = src

                # Only add if we have meaningful data
                if 'name' in chart_data and 'url' in chart_data:
                    charts.append(chart_data)
                    logger.debug(f"Chart {len(charts)}: {chart_data['name']} by {chart_data['creator']}")

        logger.info(f"Successfully extracted {len(charts)} featured charts")

        # Prepare response data
        response_data = {
            'success': True,
            'charts': charts,
            'count': len(charts),
            'slides': (len(charts) + 9) // 10,  # Calculate number of slides needed
            'timestamp': datetime.now().isoformat()
        }

        # Cache the successful response
        set_cached_beatport_data('homepage', 'featured_charts', response_data)

        response = jsonify(response_data)
        return add_cache_headers(response, 3600)  # 1 hour

    except Exception as e:
        logger.error(f"Error fetching featured charts: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'charts': []
        }), 500


@bp.route('/api/beatport/dj-charts')
def get_beatport_dj_charts():
    """Get DJ charts from Beatport for the DJ charts slider using Carousel approach"""
    try:
        logger.info("Fetching Beatport DJ charts...")

        # Check cache first
        cached_data = get_cached_beatport_data('homepage', 'dj_charts')
        if cached_data:
            logger.info("Returning cached DJ charts data")
            response = jsonify(cached_data)
            return add_cache_headers(response, 3600)  # 1 hour

        # Cache miss - scrape fresh data
        logger.info("Cache miss - scraping fresh DJ charts data...")

        # Initialize scraper
        scraper = BeatportUnifiedScraper()

        # Get page and extract charts
        soup = scraper.get_page(scraper.base_url)
        if not soup:
            raise Exception("Could not fetch Beatport homepage")

        # Find all Carousel containers
        carousels = soup.select('[class*="Carousel-style__Wrapper"]')
        dj_container = None

        logger.debug(f"Checking {len(carousels)} Carousel containers for DJ charts...")

        # Based on test results, DJ charts are in the second carousel (index 1) with ~9 chart links
        for i, container in enumerate(carousels):
            chart_links = container.select('a[href*="/chart/"]')
            logger.debug(f"Carousel {i+1}: {len(chart_links)} chart links")

            # DJ charts container typically has 8-12 chart links (not 99+ like featured charts)
            if 5 <= len(chart_links) <= 15:
                dj_container = container
                logger.info(f"FOUND DJ CHARTS: Carousel {i+1} with {len(chart_links)} charts")
                break

        if not dj_container:
            logger.warning("No DJ Charts Carousel container found")
            return jsonify({
                'success': False,
                'error': 'DJ Charts section not found',
                'charts': []
            })

        # Extract charts from the container using chart links
        charts = []
        chart_links = dj_container.select('a[href*="/chart/"]')

        logger.info(f"Found {len(chart_links)} DJ chart links")

        for _i, link in enumerate(chart_links):
            chart_data = {}

            # Extract chart name from link text or nearby elements
            name_elem = link.select_one('h3, h4, h5, h6, [class*="title"], [class*="Title"], [class*="name"], [class*="Name"]')
            if name_elem:
                name_text = name_elem.get_text(strip=True)
            else:
                name_text = link.get_text(strip=True)

            if name_text and len(name_text) > 2:
                chart_data['name'] = name_text

                # Extract creator - for DJ charts, the chart name might be the artist name
                creator = name_text  # Use chart name as creator for DJ charts

                # Look for additional creator info in parent elements
                parent = link.parent
                if parent:
                    creator_selectors = [
                        '[class*="artist"]', '[class*="Artist"]',
                        '[class*="creator"]', '[class*="Creator"]',
                        '[class*="author"]', '[class*="Author"]'
                    ]

                    for selector in creator_selectors:
                        creator_elem = parent.select_one(selector)
                        if creator_elem:
                            creator_text = creator_elem.get_text(strip=True)
                            if creator_text and len(creator_text) > 1 and creator_text != name_text:
                                creator = creator_text
                                break

                chart_data['creator'] = creator

                # Extract URL
                href = link.get('href', '')
                if href:
                    if href.startswith('/'):
                        chart_data['url'] = f"https://www.beatport.com{href}"
                    else:
                        chart_data['url'] = href

                # Extract image
                img_elem = link.select_one('img') or (link.parent.select_one('img') if link.parent else None)
                if img_elem:
                    src = img_elem.get('src', '') or img_elem.get('data-src', '')
                    if src:
                        if src.startswith('//'):
                            src = f"https:{src}"
                        elif src.startswith('/'):
                            src = f"https://www.beatport.com{src}"
                        chart_data['image'] = src

                # Only add if we have meaningful data
                if 'name' in chart_data and 'url' in chart_data:
                    charts.append(chart_data)
                    logger.info(f"DJ Chart {len(charts)}: {chart_data['name']} by {chart_data['creator']}")

        logger.info(f"Successfully extracted {len(charts)} DJ charts")

        # Prepare response data
        response_data = {
            'success': True,
            'charts': charts,
            'count': len(charts),
            'slides': max(1, (len(charts) + 2) // 3),  # 3 cards per slide
            'timestamp': datetime.now().isoformat()
        }

        # Cache the successful response
        set_cached_beatport_data('homepage', 'dj_charts', response_data)

        response = jsonify(response_data)
        return add_cache_headers(response, 3600)  # 1 hour

    except Exception as e:
        logger.error(f"Error fetching DJ charts: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'charts': []
        }), 500



# ================================= #
# BEATPORT API ENDPOINTS            #
# ================================= #

@bp.route('/api/beatport/genres', methods=['GET'])
def get_beatport_genres():
    """Get current Beatport genres with images dynamically scraped from homepage"""
    try:
        logger.info("API request for Beatport genres")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        include_images = request.args.get('include_images', 'false').lower() == 'true'

        # Discover genres dynamically
        if include_images:
            logger.info("Including genre images in response (slower)")
            genres = scraper.discover_genres_with_images(include_images=True)
        else:
            logger.info("Returning genres without images (faster)")
            genres = scraper.discover_genres_from_homepage()

        logger.info(f"Successfully discovered {len(genres)} Beatport genres")

        return jsonify({
            "success": True,
            "genres": genres,
            "count": len(genres),
            "includes_images": include_images
        })

    except Exception as e:
        logger.error(f"Error fetching Beatport genres: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "genres": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/tracks', methods=['GET'])
def get_beatport_genre_tracks(genre_slug, genre_id):
    """Get tracks for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre tracks (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        limit = int(request.args.get('limit', '100'))
        enrich = request.args.get('enrich', 'true').lower() != 'false'

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Scrape tracks for this genre
        tracks = scraper.scrape_genre_charts(genre, limit=limit, enrich=enrich)

        logger.info(f"Successfully scraped {len(tracks)} tracks for {genre_slug}")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "genre": genre,
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching tracks for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/chart/extract', methods=['POST'])
def extract_beatport_chart_tracks():
    """Extract tracks from a specific Beatport chart URL"""
    try:
        data = request.get_json()
        chart_url = data.get('chart_url')
        chart_name = data.get('chart_name', 'Unknown Chart')
        limit = int(data.get('limit', 100))

        if not chart_url:
            return jsonify({
                "success": False,
                "error": "chart_url is required",
                "tracks": [],
                "count": 0
            }), 400

        enrich = data.get('enrich', True)

        logger.info(f"API request to extract tracks from chart: {chart_name}")
        logger.info(f"Chart URL: {chart_url}")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        if enrich:
            # Full extraction + enrichment (legacy synchronous path)
            tracks = scraper.extract_tracks_from_chart(chart_url, chart_name, limit)
        else:
            # Extract raw track list only (no per-track enrichment)
            soup = scraper.get_page(chart_url)
            tracks = []
            if soup:
                tracks = scraper.extract_tracks_from_chart_table(soup, chart_name, limit)
                if len(tracks) < 10:
                    general_tracks = scraper.extract_tracks_from_page(soup, f"New Chart: {chart_name}", limit)
                    if len(general_tracks) > len(tracks):
                        tracks = general_tracks
                if len(tracks) < 10:
                    table_tracks = scraper.extract_tracks_from_table_format(soup, chart_name, limit)
                    if len(table_tracks) > len(tracks):
                        tracks = table_tracks

        logger.info(f"Successfully extracted {len(tracks)} tracks from chart: {chart_name}")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "chart_name": chart_name,
            "chart_url": chart_url,
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error extracting tracks from chart: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/top-10', methods=['GET'])
def get_beatport_genre_top_10(genre_slug, genre_id):
    """Get top 10 tracks for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre top 10 tracks (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Scrape top 10 tracks for this genre
        tracks = scraper.scrape_genre_top_10(genre)

        logger.info(f"Successfully scraped {len(tracks)} top 10 tracks for {genre_slug}")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "genre": genre,
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching top 10 tracks for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/releases-top-10', methods=['GET'])
def get_beatport_genre_releases_top_10(genre_slug, genre_id):
    """Get top 10 releases for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre top 10 releases (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Scrape top 10 releases for this genre
        releases = scraper.scrape_genre_releases(genre, limit=10)

        logger.info(f"Successfully scraped {len(releases)} top 10 releases for {genre_slug}")

        return jsonify({
            "success": True,
            "tracks": releases,
            "genre": genre,
            "count": len(releases)
        })

    except Exception as e:
        logger.error(f"Error fetching top 10 releases for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/releases-top-100', methods=['GET'])
def get_beatport_genre_releases_top_100(genre_slug, genre_id):
    """Get top 100 releases for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre top 100 releases (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        limit = int(request.args.get('limit', '100'))

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Scrape top releases for this genre
        releases = scraper.scrape_genre_releases(genre, limit=limit)

        logger.info(f"Successfully scraped {len(releases)} top 100 releases for {genre_slug}")

        return jsonify({
            "success": True,
            "tracks": releases,
            "genre": genre,
            "count": len(releases)
        })

    except Exception as e:
        logger.error(f"Error fetching top 100 releases for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/staff-picks', methods=['GET'])
def get_beatport_genre_staff_picks(genre_slug, genre_id):
    """Get staff picks for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre staff picks (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        limit = int(request.args.get('limit', '50'))

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Scrape staff picks for this genre
        tracks = scraper.scrape_genre_staff_picks(genre, limit=limit)

        logger.info(f"Successfully scraped {len(tracks)} staff picks for {genre_slug}")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "genre": genre,
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching staff picks for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/hype-top-10', methods=['GET'])
def get_beatport_genre_hype_top_10(genre_slug, genre_id):
    """Get hype top 10 tracks for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre hype top 10 (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Scrape hype top 10 for this genre
        tracks = scraper.scrape_genre_hype_top_10(genre)

        logger.info(f"Successfully scraped {len(tracks)} hype top 10 tracks for {genre_slug}")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "genre": genre,
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching hype top 10 for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/hype-top-100', methods=['GET'])
def get_beatport_genre_hype_top_100(genre_slug, genre_id):
    """Get hype top 100 tracks for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre hype top 100 (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Scrape hype top 100 for this genre
        tracks = scraper.scrape_genre_hype_charts(genre, limit=100)

        logger.info(f"Successfully scraped {len(tracks)} hype top 100 tracks for {genre_slug}")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "genre": genre,
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching hype top 100 for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/hype-picks', methods=['GET'])
def get_beatport_genre_hype_picks(genre_slug, genre_id):
    """Get hype picks for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre hype picks (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        limit = int(request.args.get('limit', '50'))

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Scrape hype picks for this genre
        tracks = scraper.scrape_genre_hype_picks(genre, limit=limit)

        logger.info(f"Successfully scraped {len(tracks)} hype picks for {genre_slug}")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "genre": genre,
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching hype picks for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/latest-releases', methods=['GET'])
def get_beatport_genre_latest_releases(genre_slug, genre_id):
    """Get latest releases for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre latest releases (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        limit = int(request.args.get('limit', '50'))

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Scrape latest releases for this genre
        tracks = scraper.scrape_genre_latest_releases(genre, limit=limit)

        logger.info(f"Successfully scraped {len(tracks)} latest releases for {genre_slug}")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "genre": genre,
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching latest releases for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/new-charts', methods=['GET'])
def get_beatport_genre_new_charts(genre_slug, genre_id):
    """Get new charts for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre new charts (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        limit = int(request.args.get('limit', '50'))

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Scrape new charts for this genre
        tracks = scraper.scrape_genre_new_charts(genre, limit=limit)

        logger.info(f"Successfully scraped {len(tracks)} new charts for {genre_slug}")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "genre": genre,
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching new charts for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/hero', methods=['GET'])
def get_beatport_genre_hero(genre_slug, genre_id):
    """Get hero slider data for a specific Beatport genre with 1-hour caching"""
    try:
        logger.info(f"API request for {genre_slug} genre hero slider (ID: {genre_id})")

        # Check cache first (1-hour TTL like other genre data)
        cache_key = f"hero_{genre_slug}_{genre_id}"
        cached_data = get_cached_beatport_data('genre', cache_key, genre_slug)

        if cached_data:
            logger.info(f"Returning cached hero data for {genre_slug}")
            return jsonify({
                "success": True,
                "releases": cached_data,
                "count": len(cached_data),
                "genre_slug": genre_slug,
                "genre_id": genre_id,
                "cached": True,
                "cache_timestamp": time.time()
            })

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Scrape hero slider data
        hero_releases = scraper.scrape_genre_hero_slider(genre_slug, genre_id)

        if hero_releases:
            # Cache the data (1-hour TTL)
            set_cached_beatport_data('genre', cache_key, hero_releases, genre_slug)

            logger.info(f"Successfully scraped and cached {len(hero_releases)} hero releases for {genre_slug}")

            return jsonify({
                "success": True,
                "releases": hero_releases,
                "count": len(hero_releases),
                "genre_slug": genre_slug,
                "genre_id": genre_id,
                "cached": False,
                "scrape_timestamp": time.time()
            })
        else:
            logger.info(f"No hero releases found for {genre_slug}")
            return jsonify({
                "success": False,
                "releases": [],
                "count": 0,
                "genre_slug": genre_slug,
                "genre_id": genre_id,
                "message": "No hero releases found"
            })

    except Exception as e:
        logger.error(f"Error fetching hero data for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "releases": [],
            "count": 0,
            "genre_slug": genre_slug,
            "genre_id": genre_id
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/top-10-lists', methods=['GET'])
def get_beatport_genre_top10_lists(genre_slug, genre_id):
    """Get Top 10 lists (Beatport + Hype) for a specific genre with 1-hour caching"""
    try:
        logger.info(f"API request for {genre_slug} Top 10 lists (ID: {genre_id})")

        # Check cache first (1-hour TTL)
        cached_data = get_cached_beatport_data('genre', 'top_10_lists', genre_slug)
        if cached_data:
            logger.info(f"Returning cached Top 10 lists for {genre_slug}")
            cached_data['success'] = True
            cached_data['cached'] = True
            return jsonify(cached_data)

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Scrape Top 10 lists from genre page
        top10_data = scraper.scrape_genre_top10_tracks(genre_slug, genre_id)

        if not top10_data['beatport_top10'] and not top10_data['hype_top10']:
            return jsonify({
                "success": False,
                "error": "No Top 10 tracks found for this genre",
                "beatport_top10": [],
                "hype_top10": [],
                "beatport_count": 0,
                "hype_count": 0,
                "has_hype_section": False,
                "genre_slug": genre_slug,
                "genre_id": genre_id,
                "cached": False
            })

        # Prepare response data
        response_data = {
            "beatport_top10": top10_data['beatport_top10'],
            "hype_top10": top10_data['hype_top10'],
            "beatport_count": len(top10_data['beatport_top10']),
            "hype_count": len(top10_data['hype_top10']),
            "has_hype_section": top10_data['has_hype_section'],
            "total_tracks": top10_data['total_tracks'],
            "genre_slug": genre_slug,
            "genre_id": genre_id,
            "cached": False,
            "cache_ttl": 3600  # 1 hour
        }

        # Cache the data (1-hour TTL)
        set_cached_beatport_data('genre', 'top_10_lists', response_data, genre_slug)

        logger.info(f"Successfully fetched {response_data['beatport_count']} Beatport + {response_data['hype_count']} Hype Top 10 tracks for {genre_slug}")

        response_data['success'] = True
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Error fetching Top 10 lists for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "beatport_top10": [],
            "hype_top10": [],
            "beatport_count": 0,
            "hype_count": 0,
            "has_hype_section": False,
            "genre_slug": genre_slug,
            "genre_id": genre_id,
            "cached": False
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/top-10-releases', methods=['GET'])
def get_beatport_genre_top10_releases(genre_slug, genre_id):
    """Get Top 10 releases for a specific genre using .partial-artwork elements with 1-hour caching"""
    try:
        logger.info(f"API request for {genre_slug} Top 10 releases (ID: {genre_id})")

        # Check cache first (1-hour TTL)
        cached_data = get_cached_beatport_data('genre', 'top_10_releases', genre_slug)
        if cached_data:
            logger.info(f"Returning cached Top 10 releases for {genre_slug}")
            cached_data['success'] = True
            cached_data['cached'] = True
            return jsonify(cached_data)

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Scrape Top 10 releases from genre page
        releases = scraper.scrape_genre_top10_releases(genre_slug, genre_id)

        if not releases:
            return jsonify({
                "success": False,
                "error": "No Top 10 releases found for this genre",
                "releases": [],
                "releases_count": 0,
                "genre_slug": genre_slug,
                "genre_id": genre_id,
                "cached": False
            })

        # Prepare response data
        response_data = {
            "releases": releases,
            "releases_count": len(releases),
            "genre_slug": genre_slug,
            "genre_id": genre_id,
            "cached": False,
            "cache_ttl": 3600  # 1 hour
        }

        # Cache the data (1-hour TTL)
        set_cached_beatport_data('genre', 'top_10_releases', response_data, genre_slug)

        logger.info(f"Successfully fetched {response_data['releases_count']} Top 10 releases for {genre_slug}")

        response_data['success'] = True
        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Error fetching Top 10 releases for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "releases": [],
            "releases_count": 0,
            "genre_slug": genre_slug,
            "genre_id": genre_id,
            "cached": False
        }), 500

@bp.route('/api/beatport/genre/<genre_slug>/<genre_id>/sections', methods=['GET'])
def get_beatport_genre_sections(genre_slug, genre_id):
    """Discover all available sections for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre sections discovery (ID: {genre_id})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Create genre dict for scraper
        genre = {
            'name': genre_slug.replace('-', ' ').title(),
            'slug': genre_slug,
            'id': genre_id
        }

        # Discover sections for this genre
        sections = scraper.discover_genre_page_sections(genre)

        logger.info(f"Successfully discovered sections for {genre_slug}")

        return jsonify({
            "success": True,
            "sections": sections,
            "genre": genre
        })

    except Exception as e:
        logger.error(f"Error discovering sections for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "sections": {}
        }), 500

@bp.route('/api/beatport/top-100', methods=['GET'])
def get_beatport_top_100():
    """Get Beatport Top 100 tracks"""
    try:
        logger.info("API request for Beatport Top 100")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        limit = int(request.args.get('limit', '100'))
        enrich = request.args.get('enrich', 'true').lower() != 'false'

        # Scrape Top 100
        tracks = scraper.scrape_top_100(limit=limit, enrich=enrich)

        logger.info(f"Successfully scraped {len(tracks)} tracks from Beatport Top 100")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "chart_name": "Beatport Top 100",
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching Beatport Top 100: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/genre-image/<genre_slug>/<genre_id>', methods=['GET'])
def get_beatport_genre_image(genre_slug, genre_id):
    """Get image for a specific Beatport genre"""
    try:
        logger.info(f"API request for {genre_slug} genre image")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Construct genre URL
        genre_url = f"{scraper.base_url}/genre/{genre_slug}/{genre_id}"

        # Get genre image
        image_url = scraper.get_genre_image(genre_url)

        if image_url:
            logger.info(f"Found image for {genre_slug}")
            return jsonify({
                "success": True,
                "image_url": image_url,
                "genre_slug": genre_slug,
                "genre_id": genre_id
            })
        else:
            logger.info(f"No image found for {genre_slug}")
            return jsonify({
                "success": False,
                "image_url": None,
                "genre_slug": genre_slug,
                "genre_id": genre_id
            })

    except Exception as e:
        logger.error(f"Error fetching image for {genre_slug}: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "image_url": None
        }), 500

@bp.route('/api/beatport/hype-top-100', methods=['GET'])
def get_beatport_hype_top_100():
    """Get Beatport Hype Top 100 - Improved with fixed URL"""
    try:
        logger.info("API request for Beatport Hype Top 100")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        limit = int(request.args.get('limit', '100'))
        enrich = request.args.get('enrich', 'true').lower() != 'false'

        # Scrape Hype Top 100 using improved method
        tracks = scraper.scrape_hype_top_100(limit=limit, enrich=enrich)

        logger.info(f"Successfully scraped {len(tracks)} tracks from Beatport Hype Top 100")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "chart_name": "Beatport Hype Top 100",
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching Beatport Hype Top 100: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/top-100-releases', methods=['GET'])
def get_beatport_top_100_releases():
    """Get Beatport Top 100 Releases - New endpoint"""
    try:
        logger.info("API request for Beatport Top 100 Releases")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        limit = int(request.args.get('limit', '100'))

        # Scrape Top 100 Releases using new method
        tracks = scraper.scrape_top_100_releases(limit=limit)

        logger.info(f"Successfully scraped {len(tracks)} tracks from Beatport Top 100 Releases")

        return jsonify({
            "success": True,
            "tracks": tracks,
            "chart_name": "Top 100 New Releases",
            "count": len(tracks)
        })

    except Exception as e:
        logger.error(f"Error fetching Beatport Top 100 Releases: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "count": 0
        }), 500

@bp.route('/api/beatport/homepage/new-releases', methods=['GET'])
def get_beatport_homepage_new_releases():
    """Get Beatport New Releases from homepage section"""
    try:
        limit = int(request.args.get('limit', 40))
        logger.info(f"🆕 API request for Beatport homepage New Releases (limit: {limit})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get new releases from homepage
        new_releases = scraper.scrape_new_releases(limit=limit)

        logger.info(f"Successfully extracted {len(new_releases)} new releases from homepage")

        return jsonify({
            "success": True,
            "tracks": new_releases,
            "track_count": len(new_releases),
            "source": "beatport_homepage_new_releases"
        })

    except Exception as e:
        logger.error(f"Error getting Beatport homepage new releases: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "track_count": 0
        }), 500

@bp.route('/api/beatport/homepage/hype-picks', methods=['GET'])
def get_beatport_homepage_hype_picks():
    """Get Beatport Hype Picks from homepage section"""
    try:
        limit = int(request.args.get('limit', 40))
        logger.info(f"API request for Beatport homepage Hype Picks (limit: {limit})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get hype picks from homepage
        hype_picks = scraper.scrape_hype_picks_homepage(limit=limit)

        logger.info(f"Successfully extracted {len(hype_picks)} hype picks from homepage")

        return jsonify({
            "success": True,
            "tracks": hype_picks,
            "track_count": len(hype_picks),
            "source": "beatport_homepage_hype_picks"
        })

    except Exception as e:
        logger.error(f"Error getting Beatport homepage hype picks: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "track_count": 0
        }), 500

@bp.route('/api/beatport/homepage/top-10-releases', methods=['GET'])
def get_beatport_homepage_top_10_releases():
    """Get Beatport Top 10 Releases from homepage section"""
    try:
        limit = int(request.args.get('limit', 10))
        logger.info(f"API request for Beatport homepage Top 10 Releases (limit: {limit})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get top 10 releases from homepage
        top_10_releases = scraper.scrape_top_10_releases_homepage(limit=limit)

        logger.info(f"Successfully extracted {len(top_10_releases)} top 10 releases from homepage")

        return jsonify({
            "success": True,
            "tracks": top_10_releases,
            "track_count": len(top_10_releases),
            "source": "beatport_homepage_top_10_releases"
        })

    except Exception as e:
        logger.error(f"Error getting Beatport homepage top 10 releases: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "track_count": 0
        }), 500

@bp.route('/api/beatport/homepage/top-10-lists', methods=['GET'])
def get_beatport_homepage_top10_lists():
    """Get Beatport Top 10 Lists from homepage - both Beatport Top 10 and Hype Top 10"""
    try:
        logger.info("API request for Beatport homepage Top 10 Lists")

        # Check cache first
        cached_data = get_cached_beatport_data('homepage', 'top_10_lists')
        if cached_data:
            logger.info("Returning cached top 10 lists data")
            response = jsonify(cached_data)
            return add_cache_headers(response, 3600)  # 1 hour

        # Cache miss - scrape fresh data
        logger.info("Cache miss - scraping fresh top 10 lists data...")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get top 10 lists from homepage
        top10_lists = scraper.scrape_homepage_top10_lists()

        logger.info(f"Successfully extracted Beatport Top 10: {len(top10_lists['beatport_top10'])}, Hype Top 10: {len(top10_lists['hype_top10'])}")

        # Prepare response data
        response_data = {
            "success": True,
            "beatport_top10": top10_lists["beatport_top10"],
            "hype_top10": top10_lists["hype_top10"],
            "beatport_count": len(top10_lists["beatport_top10"]),
            "hype_count": len(top10_lists["hype_top10"]),
            "source": "beatport_homepage_top10_lists"
        }

        # Cache the successful response
        set_cached_beatport_data('homepage', 'top_10_lists', response_data)

        response = jsonify(response_data)
        return add_cache_headers(response, 3600)  # 1 hour

    except Exception as e:
        logger.error(f"Error getting Beatport homepage top 10 lists: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "beatport_top10": [],
            "hype_top10": [],
            "beatport_count": 0,
            "hype_count": 0
        }), 500

@bp.route('/api/beatport/homepage/top-10-releases-cards', methods=['GET'])
def get_beatport_homepage_top10_releases_cards():
    """Get Beatport Top 10 Releases CARDS from homepage (not individual tracks)"""
    try:
        logger.info("API request for Beatport homepage Top 10 Releases CARDS")

        # Check cache first
        cached_data = get_cached_beatport_data('homepage', 'top_10_releases')
        if cached_data:
            logger.info("Returning cached top 10 releases data")
            response = jsonify(cached_data)
            return add_cache_headers(response, 3600)  # 1 hour

        # Cache miss - scrape fresh data
        logger.info("Cache miss - scraping fresh top 10 releases data...")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get top 10 releases from homepage
        top10_releases = scraper.scrape_homepage_top10_releases()

        logger.info(f"API extracted {len(top10_releases)} Top 10 Release Cards")

        # Debug: Log first release if any
        if top10_releases:
            logger.info(f"First release: {top10_releases[0].get('title', 'No title')} by {top10_releases[0].get('artist', 'No artist')}")
        else:
            logger.warning("No releases found by scraper")

        # Prepare response data
        response_data = {
            "success": True,
            "releases": top10_releases,
            "releases_count": len(top10_releases),
            "source": "beatport_homepage_top10_releases_cards"
        }

        # Cache the successful response
        set_cached_beatport_data('homepage', 'top_10_releases', response_data)

        response = jsonify(response_data)
        return add_cache_headers(response, 3600)  # 1 hour

    except Exception as e:
        logger.error(f"Error getting Beatport homepage Top 10 Releases cards: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "releases": [],
            "releases_count": 0
        }), 500

@bp.route('/api/beatport/scrape-releases', methods=['POST'])
def scrape_beatport_releases():
    """General scraper endpoint - takes release URLs and returns tracks"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "No JSON data provided",
                "tracks": [],
                "track_count": 0
            }), 400

        release_urls = data.get('release_urls', [])
        source_name = data.get('source_name', 'General Release Scraper')

        if not release_urls:
            return jsonify({
                "success": False,
                "error": "No release URLs provided",
                "tracks": [],
                "track_count": 0
            }), 400

        logger.info(f"API request to scrape {len(release_urls)} release URLs with source: {source_name}")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Use our new general scraper function
        tracks = scraper.scrape_multiple_releases(release_urls, source_name)

        logger.info(f"Successfully extracted {len(tracks)} tracks from {len(release_urls)} releases")

        # Apply text cleaning to track data
        cleaned_tracks = []
        for track in tracks:
            cleaned_track = track.copy()
            if 'title' in cleaned_track:
                cleaned_track['title'] = clean_beatport_text(cleaned_track['title'])
            if 'artist' in cleaned_track:
                cleaned_track['artist'] = clean_beatport_text(cleaned_track['artist'])
            if 'label' in cleaned_track:
                cleaned_track['label'] = clean_beatport_text(cleaned_track['label'])
            cleaned_tracks.append(cleaned_track)

        return jsonify({
            "success": True,
            "tracks": cleaned_tracks,
            "track_count": len(cleaned_tracks),
            "source": source_name,
            "release_urls_processed": len(release_urls)
        })

    except Exception as e:
        logger.error(f"Error scraping releases: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "track_count": 0
        }), 500

@bp.route('/api/beatport/release-metadata', methods=['POST'])
def get_beatport_release_metadata():
    """Fetch structured release metadata for direct download modal (skip discovery)"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        release_url = data.get('release_url', '')
        if not release_url:
            return jsonify({"success": False, "error": "No release_url provided"}), 400

        logger.info(f"API request for release metadata: {release_url}")

        scraper = BeatportUnifiedScraper()
        result = scraper.get_release_metadata(release_url)

        if not result.get('success'):
            return jsonify(result), 404

        # Apply text cleaning
        album = result['album']
        artist = result['artist']
        album['name'] = clean_beatport_text(album['name'])
        artist['name'] = clean_beatport_text(artist['name'])

        for track in result['tracks']:
            track['name'] = clean_beatport_text(track['name'])
            for a in track.get('artists', []):
                a['name'] = clean_beatport_text(a['name'])
            # Update the embedded album name too
            track['album']['name'] = album['name']

        logger.info(f"Release metadata: {album['name']} by {artist['name']} ({len(result['tracks'])} tracks)")

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error getting release metadata: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500

# Active enrichment tasks — progress tracked here, polled by frontend
_enrichment_tasks = {}  # enrichment_id -> {completed, total, current_track, done, tracks, error}
_enrichment_tasks_lock = threading.Lock()

@bp.route('/api/beatport/enrich-tracks', methods=['POST'])
def enrich_beatport_tracks():
    """Start Beatport track enrichment. Returns immediately; poll /enrich-progress for updates."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        tracks = data.get('tracks', [])
        if not tracks:
            return jsonify({"success": False, "error": "No tracks provided"}), 400

        enrichment_id = data.get('enrichment_id', str(uuid.uuid4()))

        logger.info(f"Enriching {len(tracks)} Beatport tracks with per-track metadata (id: {enrichment_id})")

        # --- Check enrichment cache (fast, do before spawning background) ---
        cached_results = {}
        uncached_tracks = []
        uncached_indices = []

        mcache = get_metadata_cache()

        for i, track in enumerate(tracks):
            url = track.get('url') or track.get('track_url') or ''
            if url:
                cached = mcache.get_entity('beatport', 'track', url)
                if cached:
                    cached_results[i] = cached
                    continue
            uncached_tracks.append(track)
            uncached_indices.append(i)

        cache_hits = len(cached_results)
        cache_misses = len(uncached_tracks)
        logger.info(f"Enrichment cache: {cache_hits} hits, {cache_misses} misses")

        # All cached — return immediately (no background task needed)
        if cache_misses == 0:
            merged = [None] * len(tracks)
            for idx, d in cached_results.items():
                merged[idx] = d
            for i in range(len(merged)):
                if merged[i] is None:
                    merged[i] = tracks[i]
            return jsonify({"success": True, "tracks": merged})

        # --- Initialize progress tracker and start background task ---
        with _enrichment_tasks_lock:
            _enrichment_tasks[enrichment_id] = {
                'completed': cache_hits,
                'total': len(tracks),
                'current_track': f'{cache_hits} tracks (cached)' if cache_hits > 0 else '',
                'done': False,
                'tracks': None,
                'error': None,
            }

        def _run_enrichment():
            try:
                def on_progress(completed, total, track_name):
                    new_completed = cache_hits + completed
                    with _enrichment_tasks_lock:
                        task = _enrichment_tasks.get(enrichment_id)
                        if task:
                            task['completed'] = new_completed
                            task['current_track'] = track_name
                        else:
                            logger.warning(f"on_progress: task {enrichment_id} not found in _enrichment_tasks!")

                scraper = BeatportUnifiedScraper()
                newly_enriched = scraper.enrich_chart_tracks(uncached_tracks, progress_callback=on_progress)

                # Clean and cache
                for track in newly_enriched:
                    if track.get('title'):
                        track['title'] = clean_beatport_text(track['title'])
                    if track.get('artist'):
                        track['artist'] = clean_beatport_text(track['artist'])
                    if track.get('release_name'):
                        track['release_name'] = clean_beatport_text(track['release_name'])
                    if track.get('label'):
                        track['label'] = clean_beatport_text(track['label'])
                    url = track.get('url') or track.get('track_url') or ''
                    if url:
                        mcache.store_entity('beatport', 'track', url, track)

                # Merge in original order
                merged = [None] * len(tracks)
                for idx, d in cached_results.items():
                    merged[idx] = d
                for j, idx in enumerate(uncached_indices):
                    if j < len(newly_enriched):
                        merged[idx] = newly_enriched[j]
                for i in range(len(merged)):
                    if merged[i] is None:
                        merged[i] = tracks[i]

                logger.info(f"Enriched {len(merged)} tracks ({cache_hits} cached, {cache_misses} scraped)")

                with _enrichment_tasks_lock:
                    task = _enrichment_tasks.get(enrichment_id)
                    if task:
                        task['done'] = True
                        task['tracks'] = merged
                        task['completed'] = len(tracks)

            except Exception as e:
                logger.error(f"Error enriching tracks: {e}")
                import traceback
                logger.error(f"Full traceback: {traceback.format_exc()}")
                with _enrichment_tasks_lock:
                    task = _enrichment_tasks.get(enrichment_id)
                    if task:
                        task['done'] = True
                        task['error'] = str(e)
                        task['tracks'] = tracks  # Return originals as fallback

        threading.Thread(target=_run_enrichment, daemon=True).start()

        return jsonify({"success": True, "enrichment_id": enrichment_id, "async": True})

    except Exception as e:
        logger.error(f"Error starting enrichment: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/beatport/enrich-progress/<enrichment_id>', methods=['GET'])
def get_enrichment_progress(enrichment_id):
    """Poll enrichment progress. Returns current state; includes tracks when done."""
    with _enrichment_tasks_lock:
        task = _enrichment_tasks.get(enrichment_id)
        if not task:
            return jsonify({"success": False, "error": "Unknown enrichment ID"}), 404

        result = {
            'success': True,
            'completed': task['completed'],
            'total': task['total'],
            'current_track': task['current_track'],
            'done': task['done'],
        }
        if task['done']:
            result['tracks'] = task['tracks']
            result['error'] = task['error']
            # Clean up — task is finished
            del _enrichment_tasks[enrichment_id]

        resp = jsonify(result)
        resp.headers['Cache-Control'] = 'no-store'
        return resp

@bp.route('/api/beatport/homepage/featured-charts', methods=['GET'])
def get_beatport_homepage_featured_charts():
    """Get Beatport Featured Charts from homepage section"""
    try:
        limit = int(request.args.get('limit', 20))
        logger.info(f"API request for Beatport homepage Featured Charts (limit: {limit})")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get featured charts from homepage
        featured_charts = scraper.scrape_featured_charts(limit=limit)

        logger.info(f"Successfully extracted {len(featured_charts)} featured charts from homepage")

        return jsonify({
            "success": True,
            "tracks": featured_charts,
            "track_count": len(featured_charts),
            "source": "beatport_homepage_featured_charts"
        })

    except Exception as e:
        logger.error(f"Error getting Beatport homepage featured charts: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "tracks": [],
            "track_count": 0
        }), 500

@bp.route('/api/beatport/chart-sections', methods=['GET'])
def get_beatport_chart_sections():
    """Get dynamically discovered Beatport chart sections"""
    try:
        logger.info("API request for Beatport chart sections discovery")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Discover chart sections dynamically
        chart_sections = scraper.discover_chart_sections()

        logger.info("Successfully discovered chart sections")

        return jsonify({
            "success": True,
            "chart_sections": chart_sections,
            "summary": chart_sections.get('summary', {})
        })

    except Exception as e:
        logger.error(f"Error discovering Beatport chart sections: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "chart_sections": {},
            "summary": {}
        }), 500

@bp.route('/api/beatport/dj-charts-improved', methods=['GET'])
def get_beatport_dj_charts_improved():
    """Get Beatport DJ Charts using improved method"""
    try:
        logger.info("API request for Beatport DJ Charts (improved)")

        # Initialize the Beatport scraper
        scraper = BeatportUnifiedScraper()

        # Get query parameters
        limit = int(request.args.get('limit', '20'))

        # Scrape DJ Charts using improved method
        charts = scraper.scrape_dj_charts(limit=limit)

        logger.info(f"Successfully scraped {len(charts)} DJ charts")

        return jsonify({
            "success": True,
            "charts": charts,
            "chart_name": "Beatport DJ Charts",
            "count": len(charts)
        })

    except Exception as e:
        logger.error(f"Error fetching Beatport DJ Charts: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "charts": [],
            "count": 0
        }), 500


@bp.route('/api/beatport/hype-picks')
def get_beatport_hype_picks():
    """Get Beatport Hype Picks for the rebuild slider grid (EXACT same pattern as new-releases)"""
    try:
        logger.info("Fetching Beatport hype picks...")

        # Check cache first
        cached_data = get_cached_beatport_data('homepage', 'hype_picks')
        if cached_data:
            logger.info("Returning cached hype picks data")
            response = jsonify(cached_data)
            return add_cache_headers(response, 3600)  # 1 hour

        # Cache miss - scrape fresh data
        logger.info("Cache miss - scraping fresh hype picks data...")

        # Initialize scraper
        scraper = BeatportUnifiedScraper()

        # Get page and extract releases
        soup = scraper.get_page(scraper.base_url)
        if not soup:
            raise Exception("Could not fetch Beatport homepage")

        # Extract hype pick cards using data-testid selector (equivalent to new-releases CSS selector)
        hype_pick_cards = soup.select('[data-testid="hype-picks"]')
        releases = []

        logger.info(f"Found {len(hype_pick_cards)} hype pick cards")

        for _i, card in enumerate(hype_pick_cards[:100]):  # Limit to 100 for 10 slides (same as new-releases)
            release_data = {}

            # Extract title (exact same logic as new-releases)
            title_elem = card.select_one('[class*="title"], [class*="Title"], h1, h2, h3, h4, h5, h6')
            if title_elem:
                title_text = title_elem.get_text(strip=True)
                if title_text and len(title_text) > 2 and title_text not in ['Hype Picks', 'Buy', 'Play']:
                    release_data['title'] = title_text

            # Extract artist (exact same logic as new-releases)
            artist_elem = card.select_one('[class*="artist"], [class*="Artist"], a[href*="/artist/"]')
            if artist_elem:
                artist_text = artist_elem.get_text(strip=True)
                if artist_text and len(artist_text) > 1:
                    release_data['artist'] = artist_text

            # Extract label (exact same logic as new-releases)
            label_elem = card.select_one('[class*="label"], [class*="Label"], a[href*="/label/"]')
            if label_elem:
                label_text = label_elem.get_text(strip=True)
                if label_text and len(label_text) > 1:
                    release_data['label'] = label_text

            # Extract URL (exact same logic as new-releases)
            url_link = card.select_one('a[href*="/release/"]')
            if url_link:
                href = url_link.get('href')
                if href:
                    release_data['url'] = urljoin(scraper.base_url, href)

            # Extract image (exact same logic as new-releases)
            img = card.select_one('img')
            if img:
                src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                if src:
                    release_data['image_url'] = src

            # URL fallback for title (exact same logic as new-releases)
            if not release_data.get('title') and release_data.get('url'):
                url_parts = release_data['url'].split('/release/')
                if len(url_parts) > 1:
                    slug = url_parts[1].split('/')[0]
                    release_data['title'] = slug.replace('-', ' ').title()

            # Only add if we have essential data (exact same logic as new-releases)
            if release_data.get('title') and release_data.get('url'):
                # Add fallbacks for missing data (exact same logic as new-releases)
                if not release_data.get('artist'):
                    release_data['artist'] = 'Various Artists'
                if not release_data.get('label'):
                    release_data['label'] = 'Unknown Label'

                releases.append(release_data)

        logger.info(f"Successfully extracted {len(releases)} hype picks")

        # Prepare response data
        response_data = {
            'success': True,
            'releases': releases,
            'count': len(releases),
            'slides': (len(releases) + 9) // 10,  # Calculate number of slides needed (same as new-releases)
            'timestamp': datetime.now().isoformat()
        }

        # Cache the successful response
        set_cached_beatport_data('homepage', 'hype_picks', response_data)

        response = jsonify(response_data)
        return add_cache_headers(response, 3600)  # 1 hour

    except Exception as e:
        logger.error(f"Error getting Beatport hype picks: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'releases': [],
            'count': 0
        }), 500


