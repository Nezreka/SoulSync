"""the beatport tab's browse sections, read from the page json.

each returns the list the react section renders, or None when the page had no
json for it (the route then falls back to its old html scrape). one homepage
fetch serves every homepage section (core.beatport.fetch keeps pages a few
minutes).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.beatport.fetch import fetch_next_data
from core.beatport.next_data import BASE_URL, module_charts, module_releases, top_releases

HOME = BASE_URL + '/'


def _home(scraper: Any = None):
    return fetch_next_data(HOME, scraper)


def _genre(slug: str, genre_id: Any, scraper: Any = None):
    return fetch_next_data(f"{BASE_URL}/genre/{slug}/{genre_id}", scraper)


def _nonempty(value: Optional[list]) -> Optional[list]:
    return value if value else None


def hero(scraper: Any = None, limit: int = 15) -> Optional[List[Dict[str, Any]]]:
    data = _home(scraper)
    return _nonempty(module_releases(data, 'homeLargeSlideshowOld', limit, banner_art=True)) if data else None


def new_releases(scraper: Any = None, limit: int = 40) -> Optional[List[Dict[str, Any]]]:
    data = _home(scraper)
    return _nonempty(module_releases(data, 'releaseFeature', limit)) if data else None


def hype_picks(scraper: Any = None, limit: int = 40) -> Optional[List[Dict[str, Any]]]:
    data = _home(scraper)
    return _nonempty(module_releases(data, 'hypeFeature', limit)) if data else None


def featured_charts(scraper: Any = None, limit: int = 20) -> Optional[List[Dict[str, Any]]]:
    data = _home(scraper)
    return _nonempty(module_charts(data, 'chartFeature', limit)) if data else None


def dj_charts(scraper: Any = None, limit: int = 20) -> Optional[List[Dict[str, Any]]]:
    """the homepage's small chart slideshow: artist and dj charts"""
    data = _home(scraper)
    return _nonempty(module_charts(data, 'homeSmallSlideshow', limit)) if data else None


def top10_releases(scraper: Any = None) -> Optional[List[Dict[str, Any]]]:
    data = _home(scraper)
    return _nonempty(top_releases(data)) if data else None


def genre_hero(slug: str, genre_id: Any, scraper: Any = None, limit: int = 15) -> Optional[List[Dict[str, Any]]]:
    data = _genre(slug, genre_id, scraper)
    return _nonempty(module_releases(data, 'genreLargeSlideshow', limit, banner_art=True)) if data else None


def genre_top10_releases(slug: str, genre_id: Any, scraper: Any = None) -> Optional[List[Dict[str, Any]]]:
    data = _genre(slug, genre_id, scraper)
    return _nonempty(top_releases(data)) if data else None
