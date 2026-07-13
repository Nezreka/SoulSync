"""When is a movie actually DOWNLOADABLE — i.e. home/digital-released, not just in cinemas?

Radarr's "minimum availability = released": the wishlist drain must not chase a film that's
only in theatres (there's no real WEB-DL/BluRay yet, so it can only match a wrong/fake copy).
This computes an "available date" from TMDB's per-country release-date data: the earliest
digital / physical / TV date when TMDB has one, otherwise the earliest theatrical date plus a
typical home-release window. Pure + stdlib-only, so it's unit-tested in isolation.
"""

from __future__ import annotations

import datetime
from typing import Any, List, Optional

# TMDB release types: 1 Premiere · 2 Theatrical (limited) · 3 Theatrical · 4 Digital ·
# 5 Physical · 6 TV. Digital/Physical/TV = a copy exists to download.
_HOME_TYPES = frozenset({4, 5, 6})
_THEATRICAL_TYPES = frozenset({1, 2, 3})
DEFAULT_THEATRICAL_TO_HOME_DAYS = 90   # the usual cinema→home gap when TMDB has no digital date


def _dates_by_type(results: Any) -> dict:
    """{type:int -> [YYYY-MM-DD, ...]} flattened across every country in a TMDB
    /movie/{id}/release_dates ``results`` list."""
    out: dict = {}
    for country in (results or []):
        if not isinstance(country, dict):
            continue
        for rd in (country.get("release_dates") or []):
            if not isinstance(rd, dict):
                continue
            t, d = rd.get("type"), str(rd.get("release_date") or "")[:10]
            if isinstance(t, int) and len(d) == 10:
                out.setdefault(t, []).append(d)
    return out


def available_date(results: Any, *, delay_days: int = DEFAULT_THEATRICAL_TO_HOME_DAYS) -> Optional[str]:
    """The date a downloadable copy is expected as ``YYYY-MM-DD``:

    1. the earliest DIGITAL / PHYSICAL / TV release TMDB knows about, else
    2. the earliest THEATRICAL release + ``delay_days`` (estimated home window), else
    3. ``None`` — unknown (the drain then falls back to the release-year check).
    """
    by = _dates_by_type(results)
    home = sorted(d for t in _HOME_TYPES for d in by.get(t, []))
    if home:
        return home[0]
    theatrical = sorted(d for t in _THEATRICAL_TYPES for d in by.get(t, []))
    if theatrical:
        try:
            base = datetime.date.fromisoformat(theatrical[0])
        except ValueError:
            return None
        return (base + datetime.timedelta(days=max(0, int(delay_days)))).isoformat()
    return None


__all__ = ["available_date", "DEFAULT_THEATRICAL_TO_HOME_DAYS"]
