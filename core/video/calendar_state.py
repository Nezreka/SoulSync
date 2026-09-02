"""Where a calendar episode stands, in one word.

The calendar knew two things about an episode: the date it airs and whether a
file exists. That is enough to draw a grid and not enough to act on. An episode
that aired on Tuesday and is still missing looks identical to one that aired on
Tuesday and is deliberately unmonitored, and both look identical to one that is
downloading right now.

So this maps an episode onto the same vocabulary the detail page's acquisition
panel uses, plus the two states only a calendar needs: ``unaired`` (the date is
in the future, so nothing is wrong) and ``missing`` (it aired, nothing has it,
and nothing is looking for it — the one state that always wants a human).

Pure: no DB, no network. The caller supplies today's date and the set of
in-flight download keys.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

OWNED = "owned"
DOWNLOADING = "downloading"
QUEUED = "queued"
FAILED = "failed"
WANTED = "wanted"
IGNORED = "ignored"
UNAIRED = "unaired"
MISSING = "missing"

# Read in this order. Reality beats intent: a file on disk is owned whatever the
# wishlist thinks, and a live transfer outranks the row that asked for it.
_ORDER = (OWNED, DOWNLOADING, QUEUED, FAILED, WANTED, IGNORED, UNAIRED, MISSING)

# The states worth a badge. `unaired` is the ordinary case for most of a
# calendar week and drawing attention to it would make the view useless.
_NOTABLE = frozenset({OWNED, DOWNLOADING, QUEUED, FAILED, WANTED, IGNORED, MISSING})

# What "needs action" means: it aired, you don't have it, and nothing is
# currently trying to get it. `wanted` is excluded on purpose — the drain has it
# in hand. `ignored` is excluded because you already answered this question.
_NEEDS_ACTION = frozenset({MISSING, FAILED})


def episode_key(ep: Dict[str, Any]) -> tuple:
    """The in-flight identity for one calendar row, matching the shape
    ``active_download_keys`` produces so the two can be compared directly."""
    return ("episode", str(ep.get("show_tmdb_id")),
            int(ep.get("season_number") or 0), int(ep.get("episode_number") or 0))


def season_key(ep: Dict[str, Any]) -> tuple:
    """A season-pack grab covers its episodes, so a row has to check for one."""
    return ("season", str(ep.get("show_tmdb_id")), int(ep.get("season_number") or 0))


def acquisition_state(ep: Dict[str, Any], *, today: str,
                      active: Optional[Dict[tuple, str]] = None) -> str:
    """One of the module's state constants for this calendar row.

    ``active`` maps an in-flight download key to its status, so a season pack
    being fetched marks every episode it covers rather than leaving them looking
    abandoned.
    """
    if ep.get("has_file"):
        return OWNED

    live = active or {}
    status = live.get(episode_key(ep)) or live.get(season_key(ep))
    if status:
        # 'importing' is still in flight from the user's point of view - the
        # bytes are here but the episode is not yet theirs.
        return QUEUED if str(status).lower() == "queued" else DOWNLOADING

    wl = str(ep.get("wishlist_status") or "").strip().lower()
    if wl == "failed":
        return FAILED
    if wl and wl != "downloaded":
        return WANTED

    # Unmonitored is a decision, not a gap, so it is never "missing".
    if not ep.get("monitored"):
        return IGNORED

    air = str(ep.get("air_date") or "")
    if air and air > str(today):
        return UNAIRED
    return MISSING


def needs_action(state: str) -> bool:
    """Whether this state is one a person still has to do something about."""
    return state in _NEEDS_ACTION


def is_notable(state: str) -> bool:
    """Whether the state earns a badge on the card."""
    return state in _NOTABLE


def summarize(states: Iterable[str]) -> list:
    """``[{"state", "count"}]`` over a window, in reading order, zeroes dropped.

    A LIST, not a dict, and that is the whole point: Flask serialises dicts with
    their keys sorted, so a carefully ordered mapping arrives at the browser
    alphabetised and the header strip reshuffles itself as counts change. Order
    is part of the contract here, so it rides in a shape that preserves it.
    """
    seen: Dict[str, int] = {}
    for s in states or ():
        seen[s] = seen.get(s, 0) + 1
    return [{"state": k, "count": seen[k]} for k in _ORDER if seen.get(k)]


__all__ = ["acquisition_state", "needs_action", "is_notable", "summarize",
           "episode_key", "season_key",
           "OWNED", "DOWNLOADING", "QUEUED", "FAILED", "WANTED", "IGNORED",
           "UNAIRED", "MISSING"]
