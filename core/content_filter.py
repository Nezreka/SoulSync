"""kids / restricted profiles: what a profile is allowed to see and play.

two knobs live on the profile row (set by an admin):
  hide_explicit  music: explicit tracks/albums stay out of sight and can't play
  max_rating     video: G / PG / PG-13 / R, the highest rating the profile gets

admins (is_admin, or profile 1) are never restricted.

the pure part (rating_age, cap_age, title_allowed, restrictions_for) has no
flask and no db. current_restrictions() is the one request-scoped reader.

video fails closed: under a cap, a title with no rating (or one we can't
read) is hidden, kids mode hides what it can't vouch for.
music fails open: explicit NULL means "nobody told us", and most libraries
have no explicit data at all, so hiding unknowns would empty them. only a
truthy explicit flag hides a track.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("content_filter")

# us movie + us tv, by the youngest age each is meant for
_NAMED_AGES = {
    "G": 0, "PG": 8, "PG-13": 13, "R": 17, "NC-17": 18, "X": 18,
    "TV-Y": 0, "TV-G": 0, "TV-Y7": 7, "TV-Y7-FV": 7, "TV-PG": 8, "TV-14": 14, "TV-MA": 17,
    # a few common non-us words with no number in them
    "U": 0, "ALL": 0, "AL": 0, "TP": 0,
}

_UNKNOWN = {"", "NR", "UNRATED", "NOT-RATED", "NOT-RATED.", "UR", "N/A", "NA", "NONE", "UNKNOWN"}

# the profile's max_rating -> the oldest age it lets through.
# G is 7 so TV-Y7 passes, PG-13 is 14 so TV-14 passes.
_CAPS = {"G": 7, "PG": 8, "PG-13": 14, "R": 17}

MAX_RATINGS = tuple(_CAPS)


def _norm(rating: Any) -> str:
    s = str(rating).strip().upper()
    # plex writes other countries as "gb/15", "de/12"
    if "/" in s:
        s = s.rsplit("/", 1)[1]
    if ":" in s:
        s = s.rsplit(":", 1)[1]
    s = s.strip()
    if s.startswith("RATED "):
        s = s[6:]
    return re.sub(r"[\s_]+", "-", s.strip())


def rating_age(rating: Optional[str]) -> Optional[int]:
    """the youngest age a rating is meant for, or None when it's unknown.

    us movie and tv names, then any bare number ("12", "FSK 12", "12A", "16+").
    """
    if rating is None:
        return None
    s = _norm(rating)
    if s in _UNKNOWN:
        return None
    if s in _NAMED_AGES:
        return _NAMED_AGES[s]
    # "TVPG", "PG13" and friends without the dash
    squashed = s.replace("-", "")
    for name, age in _NAMED_AGES.items():
        if squashed == name.replace("-", ""):
            return age
    m = re.search(r"(\d{1,2})", s)
    if m:
        n = int(m.group(1))
        if 0 <= n <= 21:
            return n
    return None


def cap_age(max_rating: Optional[str]) -> Optional[int]:
    """the oldest rating age a max_rating lets through. None = no cap.

    a max_rating we don't recognise caps at 0: a restriction someone set
    must never read as no restriction.
    """
    if max_rating is None:
        return None
    s = _norm(max_rating)
    if not s:
        return None
    if s in _CAPS:
        return _CAPS[s]
    return 0


def title_allowed(rating: Optional[str], cap: Optional[int]) -> bool:
    """can a title with this rating be shown under this cap. unknown fails closed."""
    if cap is None:
        return True
    age = rating_age(rating)
    if age is None:
        return False
    return age <= cap


def strictest(ratings: Iterable[Optional[str]]) -> Optional[str]:
    """of several ratings for one title (two servers, say), the oldest one.
    unknowns are skipped; all unknown gives None."""
    best, best_age = None, -1
    for r in ratings:
        a = rating_age(r)
        if a is not None and a > best_age:
            best, best_age = r, a
    return best


def is_explicit(value: Any) -> bool:
    """music explicit flag. NULL / unknown is NOT explicit (see module doc)."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "explicit")
    try:
        return bool(value)
    except Exception:  # noqa: BLE001 - an odd value is not a verdict
        return False


NO_RESTRICTIONS = {"hide_explicit": False, "cap": None}


def restrictions_for(profile: Optional[dict]) -> dict:
    """{hide_explicit, cap} for a profile row. admins and missing rows get none."""
    if not profile:
        return dict(NO_RESTRICTIONS)
    if profile.get("is_admin") or profile.get("id") == 1:
        return dict(NO_RESTRICTIONS)
    return {"hide_explicit": bool(profile.get("hide_explicit")),
            "cap": cap_age(profile.get("max_rating"))}


def is_restricted(r: dict) -> bool:
    return bool(r.get("hide_explicit")) or r.get("cap") is not None


def _load_profile(pid: int) -> Optional[dict]:
    from database.music_database import get_database
    return get_database().get_profile(pid)


def current_restrictions() -> dict:
    """the current request's restrictions, read once per request and kept on g.

    no request, no profile, or an admin: none. a profile row that can't be
    read fails closed (explicit hidden, cap 0) since this is the kids guard.
    """
    from flask import g, has_request_context
    if not has_request_context():
        return dict(NO_RESTRICTIONS)
    cached = getattr(g, "_content_restrictions", None)
    if cached is not None:
        return cached
    from core.profile_context import get_current_profile_id, is_admin_request
    admin = getattr(g, "is_admin", None)
    if admin is None:
        admin = is_admin_request()
    pid = getattr(g, "profile_id", None) if hasattr(g, "profile_id") else get_current_profile_id()
    if admin or pid is None or pid == 1:
        out = dict(NO_RESTRICTIONS)
    else:
        try:
            out = restrictions_for(_load_profile(pid))
        except Exception:  # noqa: BLE001 - see docstring, kids guard fails closed
            logger.exception("content filter: profile %s unreadable, restricting", pid)
            out = {"hide_explicit": True, "cap": 0}
    g._content_restrictions = out
    return out
