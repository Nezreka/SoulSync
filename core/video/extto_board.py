"""The Fresh Releases board: refreshed on a schedule, served from cache.

Fresh Releases used to be built on the render path — open the tab, wait for the
EXT.to homepage to come back through a Cloudflare challenge, then look at it.
That was already slow, and enriching each row from its own detail page (which is
where the poster, IMDb id, rating and fact list live) would have made it far
worse: every row is a separate challenge, and a bad minute on ext.to's side can
take 40 seconds per page.

So the work moved off the render path entirely. ``refresh_board`` does the whole
job — pull the homepage, enrich every row, write the result down — and the tab
just reads the last snapshot, which makes opening it instant. It runs from two
places that do the identical thing:

  * the ``video_extto_fresh_refresh`` automation, on whatever schedule the user
    sets (hourly suits the board's own turnover), and
  * the Refresh button on the tab, when the user wants it now.

CACHING IS WHAT MAKES THE HOURLY CADENCE CHEAP. The board turns over slowly:
most of what appears in one hour's pull was there the hour before, and a
release's detail page never meaningfully changes. Details are therefore keyed by
detail URL and kept in video.db, so a returning release costs nothing and each
run only pays for what is genuinely new. A first run on a cold cache is the
expensive one; every run after it is mostly free.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

from utils.logging_config import get_logger

logger = get_logger("video.extto_board")

# Where the rendered snapshot lives, so the tab can serve it without touching
# the network. Small enough for a settings row (~72 rows of scalars).
BOARD_SETTING = "extto_fresh_board"
BOARD_AT_SETTING = "extto_fresh_board_at"

# What one run is willing to spend on NEW detail pages.
#
# The real risk is WALL-CLOCK, not count: the board is ~57 unique releases, and a
# detail page costs ~2s warm, ~6s typical, and up to 40s when ext.to's Cloudflare
# is having a bad minute. A count cap does not bound that at all - 40 pages at 40s
# is still 27 minutes, which would overlap the next hourly tick. So the deadline is
# the actual guard and the count is a backstop, set high enough that a healthy run
# finishes the whole board in one go instead of dribbling it over two.
#
# Neither limit normally engages: they bite on a cold cache. In steady state only
# the genuinely new releases are fetched, and whatever a run defers is logged and
# picked up by the next one, so coverage catches up rather than being lost.
MAX_NEW_DETAILS_PER_RUN = 80
MAX_SECONDS_PER_RUN = 600

_running = False


def is_running() -> bool:
    """Whether a refresh is in flight — the automation engine's overlap guard."""
    return _running


def _rows_of(sections: dict) -> list:
    """Every row on the board, de-duplicated by detail URL.

    The same release appears under more than one period (a title posted today is
    in day AND week AND month), so enriching per-slot would fetch it three times
    for one card's worth of facts.
    """
    seen, out = set(), []
    for category in (sections or {}).values():
        for rows in (category or {}).values():
            for row in rows or []:
                url = str((row or {}).get("url") or "")
                if not url or url in seen:
                    continue
                seen.add(url)
                out.append(row)
    return out


def _attach(sections: dict, details: dict) -> dict:
    """Hang each row's detail facts on every copy of it, in every period."""
    for category in (sections or {}).values():
        for rows in (category or {}).values():
            for row in rows or []:
                got = details.get(str((row or {}).get("url") or ""))
                if got:
                    row["detail"] = got
    return sections


def load_board(db) -> dict:
    """The last snapshot, or an empty board. Never raises."""
    try:
        raw = db.get_setting(BOARD_SETTING)
        board = json.loads(raw) if raw else None
    except Exception:   # noqa: BLE001 - a corrupt snapshot must not break the tab
        logger.exception("stored EXT.to board could not be read")
        board = None
    if not isinstance(board, dict):
        return {"sections": {}, "total": 0, "fetched_at": None}
    try:
        board["fetched_at"] = db.get_setting(BOARD_AT_SETTING) or None
    except Exception:   # noqa: BLE001
        board["fetched_at"] = None
    return board


def refresh_board(db, *, timeout: int = 25, flaresolverr: Optional[str] = None,
                  log: Optional[Callable[[str], None]] = None,
                  max_new_details: int = MAX_NEW_DETAILS_PER_RUN,
                  max_seconds: int = MAX_SECONDS_PER_RUN,
                  progress: Optional[Callable[[int, str], None]] = None) -> dict:
    """Pull the board, enrich it from cache-or-network, store the snapshot.

    Identical work whether the automation or the Refresh button calls it. Returns
    a summary; never raises, because both callers report rather than crash.
    """
    global _running
    if _running:
        return {"ok": False, "status": "skipped", "reason": "already_running"}

    from core.video.extto_detail import fetch_detail
    from core.video.extto_fresh import extto_fresh_releases

    say = log or (lambda _m: None)
    tick = progress or (lambda _p, _m: None)
    _running = True
    try:
        tick(10, "Pulling the EXT.to board…")
        board = extto_fresh_releases(timeout=max(timeout, 30), flaresolverr=flaresolverr)
        if not board.get("configured"):
            say("EXT.to needs FlareSolverr — set flaresolverr.url on Settings.")
            return {"ok": False, "status": "skipped", "reason": "not_configured"}
        if board.get("error"):
            say("EXT.to board failed: %s" % board["error"])
            return {"ok": False, "status": "failed", "error": board["error"]}

        sections = board.get("sections") or {}
        rows = _rows_of(sections)
        say("%d release(s) on the board" % len(rows))

        details, hits, fetched, failed, skipped = {}, 0, 0, 0, 0
        deadline = time.monotonic() + max(1, max_seconds)
        out_of_time = False
        for i, row in enumerate(rows):
            url = str(row.get("url") or "")
            cached = _cached_detail(db, url)
            if cached is not None:
                details[url] = cached
                hits += 1
                continue
            if fetched >= max_new_details or time.monotonic() >= deadline:
                out_of_time = out_of_time or time.monotonic() >= deadline
                skipped += 1
                continue
            tick(10 + int(80 * (i + 1) / max(1, len(rows))),
                 "Matching %d/%d…" % (i + 1, len(rows)))
            res = fetch_detail(url, timeout=timeout, flaresolverr=flaresolverr)
            if res.get("ok"):
                details[url] = res["detail"]
                _store_detail(db, url, res["detail"])
                fetched += 1
            else:
                failed += 1
                logger.info("EXT.to detail skipped for %s: %s", url, res.get("error"))

        # A Cloudflare-lite or truncated homepage PARSES CLEANLY to zero rows and
        # reports no error - seen live. Storing that would blank the tab until the
        # next good run, so an empty pull leaves the last good board alone.
        if not rows and (load_board(db).get("sections") or {}):
            say("EXT.to returned an empty board - keeping the last good one")
            return {"ok": False, "status": "failed", "error": "EXT.to returned an empty board"}

        _attach(sections, details)
        stored = {"sections": sections, "total": board.get("total") or len(rows),
                  "source": board.get("source") or "EXT.to"}
        _store_board(db, stored)

        # Say what was left out rather than reporting a partial board as complete.
        if skipped:
            say("%d release(s) left for the next run (%s)"
                % (skipped, ("ran out of time - EXT.to is slow right now" if out_of_time
                             else "cap of %d new lookups per run" % max_new_details)))
        if failed:
            say("%d release(s) had no detail page this time — their cards stay plain" % failed)
        say("%d from cache, %d newly matched" % (hits, fetched))
        tick(100, "Complete")
        return {"ok": True, "status": "completed", "rows": len(rows), "cached": hits,
                "fetched": fetched, "failed": failed, "skipped": skipped}
    except Exception as exc:   # noqa: BLE001 - both callers report, neither crashes
        logger.exception("EXT.to board refresh failed")
        return {"ok": False, "status": "failed", "error": str(exc)}
    finally:
        _running = False


# ── persistence seams ────────────────────────────────────────────────────────
def _cached_detail(db, url: str):
    """A cached match, but only if the CURRENT parser produced it.

    Without the version check a parser improvement never reaches anything already
    matched: the refresh hands back the old parse and never revisits the page. A
    stale stamp reads as a miss, so that release is re-fetched once and re-stored.
    """
    from core.video.extto_detail import PARSE_VERSION
    try:
        got = db.extto_detail_cached(url)
    except Exception:   # noqa: BLE001 - a cache miss is always survivable
        logger.debug("EXT.to detail cache read failed for %s", url, exc_info=True)
        return None
    if isinstance(got, dict) and got.get("v") == PARSE_VERSION:
        return got
    return None


def _store_detail(db, url: str, detail: dict) -> None:
    try:
        db.extto_detail_store(url, detail)
    except Exception:   # noqa: BLE001 - failing to remember costs a refetch, nothing more
        logger.debug("EXT.to detail cache write failed for %s", url, exc_info=True)


def _store_board(db, board: dict) -> None:
    from datetime import datetime
    try:
        db.set_setting(BOARD_SETTING, json.dumps(board))
        db.set_setting(BOARD_AT_SETTING, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:   # noqa: BLE001
        logger.exception("EXT.to board snapshot could not be stored")


__all__ = ["refresh_board", "load_board", "is_running", "BOARD_SETTING",
           "BOARD_AT_SETTING", "MAX_NEW_DETAILS_PER_RUN", "MAX_SECONDS_PER_RUN"]
