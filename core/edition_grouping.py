"""Edition grouping for SoulSync issue #1450 (one edition per album).

Shared by the Watchlist scanner (``core/watchlist_scanner.py``) and the
Download Discography API (``api/artist_detail.py``) so both paths group and
pick editions with identical logic.

Design notes
------------
* :func:`edition_group_key` strips ONLY known edition qualifiers
  (deluxe, expanded, remastered, anniversary, ...). It deliberately does
  NOT reuse the catch-all bracket strip from
  ``_normalize_album_for_match`` in the watchlist scanner — that strip
  removes *any* parenthesized text, which would fuse genuinely different
  releases that differ only by a real parenthetical subtitle
  (e.g. "Greatest Hits" vs "Greatest Hits (Live)").
* :func:`reduce_edition_group` picks one edition per group under the
  ``watchlist.edition_preference`` setting. The ``one_complete`` mode
  excludes editions with more than 4.5x the track count of the smallest
  edition in the group — this kills the "75-track Nevermind box set"
  case (the album plus four full concerts) while keeping genuine
  expanded editions (2x was verified to degenerate: it excludes the
  40-track deluxe too).
"""

from __future__ import annotations

import re

# Valid values for the ``watchlist.edition_preference`` setting.
EDITION_PREFERENCE_ALL = "all"
EDITION_PREFERENCE_ONE_STANDARD = "one_standard"
EDITION_PREFERENCE_ONE_COMPLETE = "one_complete"
EDITION_PREFERENCE_VALUES = (
    EDITION_PREFERENCE_ALL,
    EDITION_PREFERENCE_ONE_STANDARD,
    EDITION_PREFERENCE_ONE_COMPLETE,
)

# Box-set guard: an edition with more than this multiple of the smallest
# edition's track count is treated as a box set / concert compilation and
# is never picked as "most complete". Verified against real groups:
#   Nevermind [13,40,70,75] -> 40 (70/75-track concert boxes excluded)
#   In Utero  [12,43,60,72] -> 43
#   Aerosmith [8,33]        -> 33 (4.1x expanded edition kept)
# NOTE: the original spec said 2x, but that degenerates — with Nevermind,
# 2x of smallest (26) excludes the 40-track deluxe too, so one_complete
# would pick the 13-track standard, identical to one_standard.
_BOX_SET_TRACK_MULTIPLE = 4.5

# Longest-first: patterns are applied in order, then the whole pass loops
# to a fixpoint so stacked qualifiers ("30th Anniversary Super Deluxe")
# all come off.
_EDITION_QUALIFIER_PATTERNS = [
    r"\b\d+(?:st|nd|rd|th)\s+anniversary\s+super\s+deluxe(?:\s+edition)?\b",
    r"\b\d+(?:st|nd|rd|th)\s+anniversary(?:\s+edition)?\b",
    r"\banniversary(?:\s+edition)?\b",
    r"\bsuper\s+deluxe(?:\s+edition)?\b",
    r"\bdeluxe(?:\s+edition)?\b",
    r"\bexpanded(?:\s+edition)?\b",
    r"\bremaster(?:ed)?(?:\s+\d{4})?\b",
    r"\bspecial\s+edition\b",
    r"\bcollector'?s\s+edition\b",
    r"\bdefinitive(?:\s+edition)?\b",
    r"\blimited\s+edition\b",
    r"\bplatinum\s+edition\b",
    r"\btour\s+edition\b",
    r"\bbonus\s+tracks?(?:\s+edition|\s+version)?\b",
    r"\bbonus\s+(?:edition|version)\b",
    r"\bextended(?:\s+edition|\s+version)?\b",
    r"\bre-?issue(?:d)?\b",
    r"\bclean(?:\s+version)?\b",
    r"\bexplicit\b",
    r"\bedition\b",
]
_EDITION_QUALIFIER_RES = [
    re.compile(p, re.IGNORECASE) for p in _EDITION_QUALIFIER_PATTERNS
]
_EMPTY_BRACKETS_RE = re.compile(r"\(\s*\)|\[\s*\]|\{\s*\}")


def edition_group_key(album_name: str) -> str:
    """Return a canonical grouping key for one-edition-per-album logic.

    Only known edition qualifiers are stripped; real subtitles are kept,
    so "Greatest Hits" and "Greatest Hits (Live)" (or "Album" and
    "Album - Live in Berlin") get different keys and never fuse.
    """
    if not album_name:
        return ""
    key = album_name
    prev = None
    while prev != key:
        prev = key
        for rx in _EDITION_QUALIFIER_RES:
            key = rx.sub(" ", key)
        key = _EMPTY_BRACKETS_RE.sub(" ", key)
    key = re.sub(r"\s+", " ", key).strip()
    # Drop a trailing bare dash left behind by qualifier removal
    # ("Album - Remastered 2011" -> "Album -" -> "Album").
    key = re.sub(r"\s*-\s*$", "", key).strip()
    key = key.lower()
    if not key:
        # The whole title was qualifiers (e.g. an album literally named
        # "Deluxe"): fall back to the plain normalized title so unrelated
        # qualifier-named releases don't all fuse on an empty key.
        key = re.sub(r"\s+", " ", album_name).strip().lower()
    return key


def _track_count(item) -> int:
    for k in ("track_count", "total_tracks", "tracks", "num_tracks"):
        try:
            v = item.get(k) if isinstance(item, dict) else getattr(item, k, None)
        except Exception:
            v = None
        if v is not None:
            try:
                return max(0, int(v))
            except (TypeError, ValueError):
                continue
    return 0


def _is_explicit(item) -> bool:
    for k in ("explicit", "is_explicit"):
        try:
            v = item.get(k) if isinstance(item, dict) else getattr(item, k, None)
        except Exception:
            v = None
        if v:
            return True
    return False


def reduce_edition_group(releases, preference: str, prefer_explicit: bool = True):
    """Reduce one edition group to the release(s) to keep.

    ``releases``: list of mappings (or objects) carrying at least a track
    count (``track_count`` / ``total_tracks`` / ``tracks``) and optionally
    an ``explicit`` flag. Provider order is preserved and used as the
    final tie-break (stable sort), so the first-listed edition wins ties.

    Returns a list: the full group unchanged when ``preference`` is
    ``"all"`` (or invalid), otherwise a single-element list with the
    picked edition.
    """
    releases = list(releases)
    if preference not in (EDITION_PREFERENCE_ONE_STANDARD, EDITION_PREFERENCE_ONE_COMPLETE):
        return releases
    if len(releases) <= 1:
        return releases

    def sort_key(item):
        n = _track_count(item)
        # Explicit editions sort first when preferred, so they win ties.
        explicit_rank = 0 if (prefer_explicit and _is_explicit(item)) else 1
        return (n, explicit_rank)

    if preference == EDITION_PREFERENCE_ONE_STANDARD:
        return [min(releases, key=sort_key)]

    # one_complete: most tracks, but never a box set.
    smallest = min(_track_count(r) for r in releases)
    candidates = [
        r for r in releases if _track_count(r) <= _BOX_SET_TRACK_MULTIPLE * smallest
    ]
    if not candidates:  # smallest is 0 for every edition; fall back to all
        candidates = releases
    # max() returns the first maximal element: provider order wins ties
    # after the explicit tie-break below.
    if prefer_explicit:
        best_count = max(_track_count(r) for r in candidates)
        top = [r for r in candidates if _track_count(r) == best_count]
        explicit_top = [r for r in top if _is_explicit(r)]
        return [(explicit_top or top)[0]]
    return [max(candidates, key=_track_count)]
