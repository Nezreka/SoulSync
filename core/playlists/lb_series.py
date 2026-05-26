"""ListenBrainz series detection for rolling mirrored playlists.

ListenBrainz publishes a few playlist families that get a brand new
MBID every period (week or year) — e.g. "Weekly Jams for Nezreka,
week of 2026-05-25 Mon" gets a fresh row each Monday, the previous
Monday's row rotates out of the cache after ~25 weeks. Auto-syncing
the per-period MBID is useless because the underlying ListenBrainz
playlist never updates — only the new period gets new tracks.

This module lets the auto-mirror code collapse those families into
a single rolling mirror per series. The mirror's
``source_playlist_id`` is a synthetic identifier (e.g.
``lb_weekly_jams_Nezreka``) instead of the per-period MBID, and the
refresh path resolves the synthetic id back to the latest period's
cached playlist at refresh time.

One-off playlists (user-created, collaborative, Last.fm radios) are
NOT collapsed — they have stable identifiers in their own right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class SeriesMatch:
    """A playlist whose title matches one of the rotating series."""

    series_id: str             # synthetic id, e.g. "lb_weekly_jams_Nezreka"
    canonical_name: str        # display name for the rolling mirror
    source_for_mirror: str     # "listenbrainz" or "lastfm"
    title_pattern: str         # SQL LIKE pattern for finding members
                               # (e.g. "Weekly Jams for Nezreka, week of %")


# Each series is identified by a regex + a template for the
# canonical mirror name + the source field the resulting mirror
# should sit under. ``user`` is the ListenBrainz username.
_SERIES_PATTERNS = [
    {
        "regex": re.compile(r"^Weekly Jams for (?P<user>.+?), week of "),
        "series_format": "lb_weekly_jams_{user}",
        "canonical_name": "ListenBrainz Weekly Jams",
        "source": "listenbrainz",
        "like_format": "Weekly Jams for {user}, week of %",
    },
    {
        "regex": re.compile(r"^Weekly Exploration for (?P<user>.+?), week of "),
        "series_format": "lb_weekly_exploration_{user}",
        "canonical_name": "ListenBrainz Weekly Exploration",
        "source": "listenbrainz",
        "like_format": "Weekly Exploration for {user}, week of %",
    },
    {
        "regex": re.compile(r"^Top Discoveries of (?P<year>\d{4}) for (?P<user>.+)$"),
        "series_format": "lb_top_discoveries_{user}",
        "canonical_name": "ListenBrainz Top Discoveries (latest year)",
        "source": "listenbrainz",
        # ``$`` end-anchor on the year means trailing whitespace would
        # break the LIKE — but ListenBrainz titles don't have trailing
        # whitespace; the % covers the year position.
        "like_format": "Top Discoveries of % for {user}",
    },
    {
        "regex": re.compile(r"^Top Missed Recordings of (?P<year>\d{4}) for (?P<user>.+)$"),
        "series_format": "lb_top_missed_{user}",
        "canonical_name": "ListenBrainz Top Missed Recordings (latest year)",
        "source": "listenbrainz",
        "like_format": "Top Missed Recordings of % for {user}",
    },
]


def detect_series(title: str) -> Optional[SeriesMatch]:
    """Return a ``SeriesMatch`` if ``title`` belongs to a known series,
    else ``None``.

    ``title`` is the raw playlist title as stored on the LB cache row
    (e.g. ``"Weekly Jams for Nezreka, week of 2026-05-25 Mon"``).
    """
    if not title:
        return None
    for spec in _SERIES_PATTERNS:
        m = spec["regex"].match(title)
        if not m:
            continue
        groups = m.groupdict()
        # The pattern only ever captures ``user`` (and optionally
        # ``year``); ``series_format`` / ``like_format`` reference
        # ``user`` so both interpolate cleanly with .format(**groups).
        return SeriesMatch(
            series_id=spec["series_format"].format(**groups),
            canonical_name=spec["canonical_name"],
            source_for_mirror=spec["source"],
            title_pattern=spec["like_format"].format(**groups),
        )
    return None


def list_series_synthetic_ids() -> List[str]:
    """Return all known series-id PREFIXES (e.g. ``lb_weekly_jams_``).

    Used by callers (e.g. the LB adapter's refresh path) to tell
    whether a ``source_playlist_id`` is a synthetic series id and
    needs special resolution."""
    return [
        spec["series_format"].format(user="").rstrip("_") + "_"
        for spec in _SERIES_PATTERNS
    ]


def is_series_synthetic_id(source_playlist_id: str) -> bool:
    """Cheap check: is the value one of our synthetic series ids?

    All series ids start with ``lb_`` and contain a recognizable
    series tag. MusicBrainz MBIDs are 8-4-4-4-12 hex with dashes; no
    overlap risk."""
    if not source_playlist_id or not source_playlist_id.startswith("lb_"):
        return False
    return any(
        source_playlist_id.startswith(pref) for pref in list_series_synthetic_ids()
    )
