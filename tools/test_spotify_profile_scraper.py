#!/usr/bin/env python3
"""
Spotify Profile Scraper Test Suite

Tests the spotify_profile_scraper module for fetching public playlists
from Spotify user profiles without using the API.

All tests use MOCKED data - no network requests to Spotify.

Usage:
    python tools/test_spotify_profile_scraper.py
"""

import sys
import os
import base64
import json
from unittest.mock import patch, Mock

# Add parent directory to path to import from core
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logging_config import get_logger
from core.spotify_profile_scraper import (
    fetch_profile_playlists,
    _extract_from_initial_state,
    _extract_from_html_links,
    _fetch_playlist_track_count
)

logger = get_logger("spotify_scraper_test")


# =============================================================================
# MOCK DATA
# =============================================================================

# Well-known Spotify editorial playlist IDs for realistic mock data
MOCK_PLAYLISTS = [
    {"id": "37i9dQZF1DXcBWIGoYBM5M", "name": "Today's Top Hits", "followers": 35000000},
    {"id": "37i9dQZF1DX0XUsuxWHRQd", "name": "RapCaviar", "followers": 15000000},
    {"id": "37i9dQZF1DX4JAvHpjipBk", "name": "New Music Friday", "followers": 8000000},
]


def _create_mock_initial_state(user_id: str, playlists: list) -> str:
    """Create a mock initialState JSON structure matching Spotify's format"""
    playlist_items = []
    for p in playlists:
        playlist_items.append({
            "__typename": "PlaylistResponseWrapper",
            "_uri": f"spotify:playlist:{p['id']}",
            "data": {
                "__typename": "Playlist",
                "followers": p.get("followers", 0),
                "name": p["name"],
                "uri": f"spotify:playlist:{p['id']}",
                "images": {"items": [{"sources": [{"url": f"https://example.com/{p['id']}.jpg"}]}]}
            }
        })
    
    state = {
        "entities": {
            "items": {
                f"spotify:user:{user_id}": {
                    "__typename": "User",
                    "id": user_id,
                    "name": f"Test User",
                    "publicPlaylistsV2": {"items": playlist_items, "totalCount": len(playlist_items)}
                }
            }
        }
    }
    return base64.b64encode(json.dumps(state).encode()).decode()


def _create_mock_html(user_id: str, playlists: list) -> str:
    """Create mock Spotify profile HTML page"""
    state = _create_mock_initial_state(user_id, playlists)
    return f'<html><body><script id="initialState" type="text/plain">{state}</script></body></html>'


MOCK_USER = "testuser"
MOCK_HTML = _create_mock_html(MOCK_USER, MOCK_PLAYLISTS)


def _create_mock_playlist_page(playlist_id: str, track_count: int) -> str:
    """Create mock Spotify playlist page HTML with track count in initialState"""
    state = {
        "entities": {
            "items": {
                f"spotify:playlist:{playlist_id}": {
                    "__typename": "Playlist",
                    "content": {
                        "__typename": "PlaylistItemsPage",
                        "items": [],
                        "totalCount": track_count,
                        "pagingInfo": {"nextOffset": None}
                    },
                    "name": "Test Playlist",
                    "id": playlist_id
                }
            }
        }
    }
    encoded = base64.b64encode(json.dumps(state).encode()).decode()
    return f'<html><body><script id="initialState" type="text/plain">{encoded}</script></body></html>'


# =============================================================================
# TESTS
# =============================================================================

class TestSpotifyProfileScraper:
    """All tests use mocked data"""

    def __init__(self):
        self.passed = 0
        self.failed = 0

    def run_all(self):
        print("\n" + "=" * 60)
        print("🧪 SPOTIFY PROFILE SCRAPER TESTS (mocked)")
        print("=" * 60 + "\n")

        self.test_extract_initial_state()
        self.test_extract_html_links()
        self.test_playlist_fields()
        self.test_fetch_with_playwright_mock()
        self.test_playwright_not_installed()
        self.test_track_count_fetch()

        print("\n" + "=" * 60)
        print(f"📊 RESULTS: {self.passed}/{self.passed + self.failed} passed")
        print("=" * 60)
        if self.failed == 0:
            print("🎉 All tests passed!")
        return self.failed == 0

    def _check(self, cond, name, detail=""):
        if cond:
            print(f"  ✅ {name}")
            self.passed += 1
        else:
            print(f"  ❌ {name}" + (f" - {detail}" if detail else ""))
            self.failed += 1

    def test_extract_initial_state(self):
        """Parse playlists from initialState JSON"""
        print("📦 Parse initialState JSON")
        result = _extract_from_initial_state(MOCK_HTML, MOCK_USER)
        self._check(len(result) == 3, "Extracts 3 playlists")
        self._check(result[0]['id'] == MOCK_PLAYLISTS[0]['id'], "Correct playlist ID")
        self._check(result[0]['name'] == "Today's Top Hits", "Correct playlist name")
        self._check(result[0]['source'] == 'friend_profile', "Source is friend_profile")

    def test_extract_html_links(self):
        """Fallback: parse playlist IDs from HTML links"""
        print("\n📦 Parse HTML links (fallback)")
        html = '<a href="/playlist/abc123">P1</a><a href="/playlist/def456">P2</a><a href="/playlist/abc123">P1</a>'
        result = _extract_from_html_links(html)
        self._check(len(result) == 2, "Deduplicates to 2 playlists")
        self._check(result[0]['id'] == 'abc123', "First ID correct")
        self._check(result[1]['id'] == 'def456', "Second ID correct")

    def test_playlist_fields(self):
        """Playlist dict has required fields"""
        print("\n📦 Playlist structure")
        result = _extract_from_initial_state(MOCK_HTML, MOCK_USER)
        p = result[0] if result else {}
        for f in ['id', 'name', 'owner', 'source']:
            self._check(f in p, f"Has '{f}' field")

    def test_fetch_with_playwright_mock(self):
        """fetch_profile_playlists with mocked playwright"""
        print("\n📦 fetch_profile_playlists (playwright mocked)")
        
        # Create expected result from playwright
        mock_playlists = [
            {'id': MOCK_PLAYLISTS[0]['id'], 'name': MOCK_PLAYLISTS[0]['name'], 
             'owner': MOCK_USER, 'track_count': 50, 'source': 'friend_profile'}
        ]
        
        with patch('core.spotify_profile_scraper._fetch_playlists_with_playwright', return_value=mock_playlists):
            result = fetch_profile_playlists(MOCK_USER)
        
        self._check(len(result) == 1, "Returns mocked playlist")
        self._check(result[0]['name'] == MOCK_PLAYLISTS[0]['name'], "Correct playlist name")

    def test_playwright_not_installed(self):
        """Raises RuntimeError when playwright is not installed"""
        print("\n📦 Playwright not installed handling")
        
        with patch('core.spotify_profile_scraper.sync_playwright', None):
            from core.spotify_profile_scraper import is_playwright_available
            # Need to reimport to pick up the patched value
            with patch('core.spotify_profile_scraper.is_playwright_available', return_value=False):
                try:
                    fetch_profile_playlists("any_user")
                    self._check(False, "Should raise RuntimeError")
                except RuntimeError as e:
                    self._check("Playwright is required" in str(e), "Raises correct RuntimeError")

    def test_track_count_fetch(self):
        """Fetches track count from playlist page"""
        print("\n📦 Track count fetching")
        playlist_id = "test123"
        expected_count = 42
        
        mock_html = _create_mock_playlist_page(playlist_id, expected_count)
        mock_resp = Mock(text=mock_html, raise_for_status=Mock())
        
        with patch('core.spotify_profile_scraper.requests.get', return_value=mock_resp):
            result = _fetch_playlist_track_count(playlist_id)
        
        self._check(result == expected_count, f"Returns correct track count ({expected_count})")
        
        # Test error handling - returns 0 on failure
        import requests
        with patch('core.spotify_profile_scraper.requests.get', side_effect=requests.RequestException("fail")):
            result = _fetch_playlist_track_count(playlist_id)
        self._check(result == 0, "Returns 0 on request failure")


if __name__ == "__main__":
    suite = TestSpotifyProfileScraper()
    sys.exit(0 if suite.run_all() else 1)
