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
# the show has no TMDB id, so nothing can wish for or search this episode until
# it is matched. Counting it as missing put a "Wishlist 16" button on the page
# that could only ever answer "nothing new to add".
UNMATCHED = "unmatched"

# Read in this order. Reality beats intent: a file on disk is owned whatever the
# wishlist thinks, and a live transfer outranks the row that asked for it.
_ORDER = (OWNED, DOWNLOADING, QUEUED, FAILED, WANTED, IGNORED, UNAIRED, MISSING, UNMATCHED)

# The states worth a badge. `unaired` is the ordinary case for most of a
# calendar week and drawing attention to it would make the view useless.
_NOTABLE = frozenset({OWNED, DOWNLOADING, QUEUED, FAILED, WANTED, IGNORED, MISSING, UNMATCHED})

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
    hit = live.get(episode_key(ep)) or live.get(season_key(ep))
    # an entry is the download's status, or the live_index record carrying it
    status = hit.get("status") if isinstance(hit, dict) else hit
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

    # Unmatched comes before the air date: an unmatched show's upcoming episode
    # won't be grabbed on air either, so "Auto" would be a promise nothing keeps.
    if not ep.get("show_tmdb_id"):
        return UNMATCHED

    air = str(ep.get("air_date") or "")
    if air and air > str(today):
        return UNAIRED
    return MISSING


def live_index(rows: Iterable[Dict[str, Any]]) -> Dict[tuple, Dict[str, Any]]:
    """In-flight download rows keyed the way ``acquisition_state`` reads them,
    each carrying what a card shows: status, percent and seconds left. The first
    row for a key wins (rows come oldest first, so that is the one running)."""
    from core.automation.handlers.video_process_wishlist import active_download_keys
    out: Dict[tuple, Dict[str, Any]] = {}
    for row in rows or ():
        for key in active_download_keys([row]):
            if key in out:
                continue
            try:
                pct = max(0, min(100, int(round(float(row.get("progress") or 0)))))
            except (TypeError, ValueError):
                pct = 0
            out[key] = {"status": row.get("status"), "progress": pct,
                        "eta_seconds": row.get("eta_seconds")}
    return out


def live_for(ep: Dict[str, Any], active: Optional[Dict[tuple, Any]]) -> Optional[Dict[str, Any]]:
    """The in-flight record covering this episode (its own grab or a season pack)."""
    live = active or {}
    hit = live.get(episode_key(ep)) or live.get(season_key(ep))
    return hit if isinstance(hit, dict) else None


def movie_state(m: Dict[str, Any], *, active: Optional[Dict[tuple, Any]] = None) -> str:
    """Where a calendar movie stands. Every movie on the calendar is on the
    wishlist, so the floor is ``wanted``, never ``missing``."""
    if m.get("owned"):
        return OWNED
    hit = (active or {}).get(("movie", str(m.get("tmdb_id"))))
    status = hit.get("status") if isinstance(hit, dict) else hit
    if status:
        return QUEUED if str(status).lower() == "queued" else DOWNLOADING
    if str(m.get("status") or "").strip().lower() == "failed":
        return FAILED
    return WANTED


def collapse_copies(eps):
    """One row per episode. A Plex library can list one series as several
    items (Boulder's had "90 Day Fiancé: The Other Way" fifteen times), each
    a show row with its own copy of every episode, so the calendar drew the
    same episode once per copy. Copies are the same show (by TMDB id, or by
    title when unmatched), season, episode and air date. The kept copy is the
    one that has the file, else one on the wishlist, else the first."""
    def rank(e):
        return (0 if e.get("has_file") else 1, 0 if e.get("wishlist_status") else 1)

    order, best = [], {}
    for e in eps or ():
        ident = e.get("show_tmdb_id") or "t:" + str(e.get("show_title") or "").strip().lower()
        key = (ident, e.get("season_number"), e.get("episode_number"), e.get("air_date"))
        if key not in best:
            order.append(key)
            best[key] = e
        elif rank(e) < rank(best[key]):
            best[key] = e
    return [best[k] for k in order]


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
           "live_index", "live_for", "movie_state", "collapse_copies",
           "episode_key", "season_key",
           "OWNED", "DOWNLOADING", "QUEUED", "FAILED", "WANTED", "IGNORED",
           "UNAIRED", "MISSING", "UNMATCHED"]
