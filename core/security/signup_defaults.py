"""what a profile made by sign-in starts with.

sign-in that makes profiles (sign in with plex today) is an open invite to
everyone the admin shares with, so a new profile starts small: finding and
asking for things, never the machinery (sync, automations, import,
downloads, tools). the admin picks the side it starts on (music, movies &
tv, or both) and an optional request limit; everything else is widened per
person in profile management.

nothing set = music only and no limit, exactly what sign-up made before
these settings existed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("security.signup_defaults")

# the music pages a new profile opens. help and issues are always shown
SIGNUP_PAGES = ['discover', 'search', 'library', 'artist-detail', 'wishlist', 'stats', 'help', 'issues']
SIGNUP_HOME_PAGE = 'discover'

# the video pages a new profile opens when it starts with movies & tv.
# detail pages are always reachable and requests follows download rights,
# so neither is listed. no wishlist or downloads: a member asks, it doesn't grab
SIGNUP_VIDEO_PAGES = ['video-discover', 'video-search', 'video-library']
SIGNUP_VIDEO_HOME_PAGE = 'video-discover'

SIDES = ('music', 'video', 'both')

# the same bounds the profile editor holds a request limit to
REQUEST_LIMIT_MAX = 1000
REQUEST_LIMIT_DAYS_DEFAULT = 7
REQUEST_LIMIT_DAYS_MAX = 365


@dataclass(frozen=True)
class SignupAccess:
    # None = the shipped default (music for a non-admin), stored as NULL
    allowed_sides: Optional[str] = None
    allowed_pages: List[str] = field(default_factory=lambda: list(SIGNUP_PAGES))
    home_page: str = SIGNUP_HOME_PAGE
    request_limit: int = 0
    request_limit_days: int = REQUEST_LIMIT_DAYS_DEFAULT


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def signup_access(get: Optional[Callable[[str, Any], Any]] = None) -> SignupAccess:
    """the access a new profile gets, from the security.signup_* settings.
    `get` is config_manager.get; without one, the shipped defaults"""
    if get is None:
        return SignupAccess()
    sides = get('security.signup_sides', 'music')
    if sides not in SIDES:
        sides = 'music'
    pages: List[str] = []
    if sides in ('music', 'both'):
        pages += SIGNUP_PAGES
    else:
        pages += ['help', 'issues']
    if sides in ('video', 'both'):
        pages += SIGNUP_VIDEO_PAGES
    limit = _int(get('security.signup_request_limit', 0), 0, 0, REQUEST_LIMIT_MAX)
    days = _int(get('security.signup_request_limit_days', REQUEST_LIMIT_DAYS_DEFAULT),
                REQUEST_LIMIT_DAYS_DEFAULT, 1, REQUEST_LIMIT_DAYS_MAX)
    return SignupAccess(
        allowed_sides=None if sides == 'music' else sides,
        allowed_pages=pages,
        home_page=SIGNUP_HOME_PAGE if sides != 'video' else SIGNUP_VIDEO_HOME_PAGE,
        request_limit=limit,
        request_limit_days=days,
    )


def create_signup_profile(db, name: str, access: SignupAccess, *, can_download: bool) -> Optional[int]:
    """one profile with the sign-up access, or None. the limit is written
    only when there is one, so no limit leaves the row as it always was"""
    pid = db.create_profile(name, can_download=can_download, allowed_pages=list(access.allowed_pages),
                            home_page=access.home_page, allowed_sides=access.allowed_sides)
    if pid and access.request_limit > 0:
        if not db.update_profile(pid, request_limit=access.request_limit,
                                 request_limit_days=access.request_limit_days):
            logger.warning("Sign-up: profile %s made, but its request limit wasn't saved", pid)
    return pid


__all__ = ['SIGNUP_PAGES', 'SIGNUP_HOME_PAGE', 'SIGNUP_VIDEO_PAGES', 'SIGNUP_VIDEO_HOME_PAGE', 'SIDES',
           'SignupAccess', 'signup_access', 'create_signup_profile']
