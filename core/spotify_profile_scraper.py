"""
Spotify Profile Scraper - Fetches public playlists from Spotify user profiles.

This module scrapes the public Spotify profile page to extract playlist information,
bypassing API restrictions for users who don't have API access or want to sync
playlists from friends' profiles.

Requires: playwright (headless browser) for full JS-rendered page scraping.
Install with: pip install playwright && python -m playwright install chromium
"""

import requests
import base64
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from utils.logging_config import get_logger
from config.settings import config_manager

logger = get_logger("spotify_profile_scraper")

# Import playwright - required for this module
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
    logger.warning("Playwright not installed - friend playlist scraping will not work")
    logger.warning("Install with: pip install playwright && python -m playwright install chromium")

# User agent for HTTP requests (fetching individual playlist details)
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def is_playwright_available() -> bool:
    """Check if playwright is installed and available."""
    return sync_playwright is not None


def fetch_profile_playlists(user_id: str) -> List[Dict[str, Any]]:
    """
    Fetch public playlists from a Spotify user's profile page using playwright.
    
    Args:
        user_id: The Spotify user ID (e.g., "12166842163")
        
    Returns:
        List of playlist dictionaries with id, name, owner, track_count, etc.
        
    Raises:
        RuntimeError: If playwright is not installed
    """
    if not is_playwright_available():
        raise RuntimeError(
            "Playwright is required for friend playlist scraping. "
            "Install with: pip install playwright && python -m playwright install chromium"
        )
    
    return _fetch_playlists_with_playwright(user_id)


def _fetch_playlists_with_playwright(user_id: str) -> List[Dict[str, Any]]:
    """
    Fetch all playlists using playwright headless browser.
    
    This renders the /playlists page with JavaScript to get the complete list.
    """
    playlists_url = f"https://open.spotify.com/user/{user_id}/playlists"
    logger.info(f"Playwright: Fetching playlists from {playlists_url}")
    
    playlists = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Use default Chrome user agent (custom UA causes Spotify to redirect)
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Navigate to playlists page
            page.goto(playlists_url, wait_until="networkidle", timeout=30000)
            
            # Wait for page to fully render (no specific selector needed)
            page.wait_for_timeout(5000)
            
            # Extract playlist links from rendered page
            playlist_links = page.query_selector_all('a[href*="/playlist/"]')
            
            seen_ids = set()
            for link in playlist_links:
                href = link.get_attribute('href')
                if href and '/playlist/' in href:
                    # Extract playlist ID from href
                    match = re.search(r'/playlist/([a-zA-Z0-9]+)', href)
                    if match:
                        playlist_id = match.group(1)
                        if playlist_id not in seen_ids:
                            seen_ids.add(playlist_id)
                            
                            # Get playlist name from link text or parent
                            name = link.inner_text().strip() or None
                            
                            playlists.append({
                                'id': playlist_id,
                                'name': name,
                                'owner': user_id,
                                'source': 'friend_profile',
                                'source_user_id': user_id
                            })
            
            logger.info(f"Playwright: Found {len(playlists)} playlist links")
            
        finally:
            browser.close()
    
    # Fetch details (name, track count) for each playlist
    for playlist in playlists:
        track_count, fetched_name = _fetch_playlist_details(playlist['id'])
        playlist['track_count'] = track_count
        if not playlist.get('name') and fetched_name:
            playlist['name'] = fetched_name
        
        # Skip playlists with no name (likely deleted/inaccessible)
        if not playlist.get('name'):
            logger.debug(f"Skipping playlist with no name: {playlist['id']}")
    
    # Filter out playlists with no name
    playlists = [p for p in playlists if p.get('name')]
    
    return playlists


def _fetch_playlists_with_http(user_id: str) -> List[Dict[str, Any]]:
    """
    Fetch playlists using simple HTTP request (limited to ~10 playlists).
    
    Fallback method when playwright is not available.
    """
    profile_url = f"https://open.spotify.com/user/{user_id}"
    
    logger.info(f"HTTP: Fetching playlists from {profile_url}")
    
    try:
        headers = {"User-Agent": USER_AGENT}
        
        response = requests.get(profile_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        html_content = response.text
        
        # Try to extract from initialState JSON first (most reliable)
        playlists = _extract_from_initial_state(html_content, user_id)
        
        if playlists:
            logger.info(f"HTTP: Extracted {len(playlists)} playlists from initialState")
            return playlists
        
        # Fallback: Extract playlist IDs from href links
        playlists = _extract_from_html_links(html_content)
        
        if playlists:
            logger.info(f"HTTP: Extracted {len(playlists)} playlists from HTML links")
            return playlists
        
        logger.warning(f"HTTP: No playlists found for user {user_id}")
        return []
        
    except requests.RequestException as e:
        logger.error(f"Failed to fetch profile page for {user_id}: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching profile for {user_id}: {e}")
        return []


def _extract_from_initial_state(html_content: str, user_id: str) -> List[Dict[str, Any]]:
    """
    Extract playlist data from the base64-encoded initialState script tag.
    
    The initialState contains structured JSON with full playlist metadata.
    """
    try:
        # Find the initialState script tag
        match = re.search(r'<script id="initialState" type="text/plain">([^<]+)</script>', html_content)
        
        if not match:
            logger.debug("initialState script tag not found")
            return []
        
        # Decode base64
        encoded_data = match.group(1)
        decoded_bytes = base64.b64decode(encoded_data)
        state_json = json.loads(decoded_bytes.decode('utf-8'))
        
        # Navigate to the playlists data
        entities = state_json.get('entities', {})
        items = entities.get('items', {})
        
        # The user data is stored with key like "spotify:user:12166842163"
        user_key = f"spotify:user:{user_id}"
        user_data = items.get(user_key, {})
        
        if not user_data:
            logger.debug(f"No user data found for key {user_key}")
            return []
        
        public_playlists = user_data.get('publicPlaylistsV2', {})
        playlist_items = public_playlists.get('items', [])
        
        playlists = []
        for item in playlist_items:
            data = item.get('data', {})
            
            # Skip playlists that are NotFound (deleted, private, or inaccessible)
            if data.get('__typename') == 'NotFound':
                logger.debug(f"Skipping NotFound playlist: {item.get('_uri')}")
                continue
            
            uri = item.get('_uri', '') or data.get('uri', '')
            
            # Extract playlist ID from URI (spotify:playlist:XXXXX)
            playlist_id = uri.split(':')[-1] if uri else None
            
            if not playlist_id:
                continue
            
            # Extract image URL from nested structure
            image_url = None
            images = data.get('images', {})
            image_items = images.get('items', [])
            if image_items:
                sources = image_items[0].get('sources', [])
                if sources:
                    image_url = sources[0].get('url')
            
            # Fetch track count (and name if missing) from playlist page
            playlist_name = data.get('name')
            track_count, fetched_name = _fetch_playlist_details(playlist_id)
            
            # Use fetched name if original is missing
            if not playlist_name and fetched_name:
                playlist_name = fetched_name
            
            # Skip if still no valid name (likely deleted/inaccessible)
            if not playlist_name:
                logger.debug(f"Skipping playlist with no name: {playlist_id}")
                continue
            
            playlist_info = {
                'id': playlist_id,
                'name': playlist_name,
                'owner': user_id,
                'owner_display_name': user_data.get('name', user_id),
                'image_url': image_url,
                'followers': data.get('followers', 0),
                'track_count': track_count,
                'source': 'friend_profile',
                'source_user_id': user_id
            }
            
            playlists.append(playlist_info)
        
        return playlists
        
    except (json.JSONDecodeError, ValueError) as e:
        logger.debug(f"Failed to parse initialState JSON: {e}")
        return []
    except Exception as e:
        logger.debug(f"Error extracting from initialState: {e}")
        return []


def _fetch_playlist_details(playlist_id: str) -> tuple:
    """
    Fetch track count and name for a playlist by scraping its page.
    
    Args:
        playlist_id: Spotify playlist ID
        
    Returns:
        Tuple of (track_count, name) - defaults to (0, None) if unable to fetch
    """
    try:
        playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
        headers = {"User-Agent": USER_AGENT}
        
        response = requests.get(playlist_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Find initialState
        match = re.search(r'<script id="initialState" type="text/plain">([^<]+)</script>', response.text)
        if not match:
            return (0, None)
        
        decoded = base64.b64decode(match.group(1)).decode('utf-8')
        data = json.loads(decoded)
        
        # Get playlist data
        items = data.get('entities', {}).get('items', {})
        playlist_key = f"spotify:playlist:{playlist_id}"
        playlist_data = items.get(playlist_key, {})
        
        # Extract totalCount from content
        content = playlist_data.get('content', {})
        track_count = content.get('totalCount', 0)
        
        # Extract name
        name = playlist_data.get('name')
        
        logger.debug(f"Playlist {playlist_id}: '{name}' with {track_count} tracks")
        return (track_count, name)
        
    except Exception as e:
        logger.debug(f"Could not fetch details for {playlist_id}: {e}")
        return (0, None)


# Keep the old function for backward compatibility with tests
def _fetch_playlist_track_count(playlist_id: str) -> int:
    """Fetch track count only (wrapper for backward compatibility)."""
    track_count, _ = _fetch_playlist_details(playlist_id)
    return track_count


def _extract_from_html_links(html_content: str) -> List[Dict[str, Any]]:
    """
    Fallback method: Extract playlist IDs from href links in the HTML.
    
    Looks for patterns like href="/playlist/4xJiUcKvrFEhhfhthMeOx7"
    """
    try:
        # Find all playlist links
        pattern = r'href="/playlist/([a-zA-Z0-9]+)"'
        matches = re.findall(pattern, html_content)
        
        # Deduplicate while preserving order
        seen = set()
        unique_ids = []
        for playlist_id in matches:
            if playlist_id not in seen:
                seen.add(playlist_id)
                unique_ids.append(playlist_id)
        
        playlists = []
        for playlist_id in unique_ids:
            playlist_info = {
                'id': playlist_id,
                'name': f'Playlist {playlist_id[:8]}...',  # Placeholder name
                'owner': 'Unknown',
                'image_url': None,
                'followers': 0,
                'source': 'friend_profile'
            }
            playlists.append(playlist_info)
        
        return playlists
        
    except Exception as e:
        logger.debug(f"Error extracting from HTML links: {e}")
        return []


def get_all_friend_playlists() -> List[Dict[str, Any]]:
    """
    Fetch playlists from all configured friend profiles.
    
    Reads the friend_profiles list from config and fetches playlists from each.
    
    Returns:
        Combined list of playlists from all friend profiles
    """
    spotify_config = config_manager.get_spotify_config()
    friend_profiles = spotify_config.get('friend_profiles', [])
    
    if not friend_profiles:
        logger.debug("No friend profiles configured")
        return []
    
    logger.info(f"Fetching playlists from {len(friend_profiles)} friend profile(s)")
    
    all_playlists = []
    for user_id in friend_profiles:
        try:
            playlists = fetch_profile_playlists(user_id)
            all_playlists.extend(playlists)
        except Exception as e:
            logger.error(f"Failed to fetch playlists for friend {user_id}: {e}")
            continue
    
    logger.info(f"Total friend playlists fetched: {len(all_playlists)}")
    return all_playlists


# For standalone testing
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        test_user_id = sys.argv[1]
    else:
        test_user_id = "12166842163"
    
    print(f"Testing profile scraper for user: {test_user_id}")
    print("-" * 50)
    
    playlists = fetch_profile_playlists(test_user_id)
    
    print(f"Found {len(playlists)} playlists:")
    for i, p in enumerate(playlists, 1):
        print(f"{i}. {p['name']} (ID: {p['id']})")
        if p.get('followers'):
            print(f"   Followers: {p['followers']}")
