"""Auto-retry logic for video downloads (the music-style depth).

When a grabbed release fails (transfer error / peer cancel / never lands), the engine
shouldn't just give up — it should try the NEXT-best candidate from the same search,
and when those run out, RE-SEARCH with a different query (e.g. a movie without its
year) and try those. This module is the pure decision engine; the monitor performs
the I/O (start the download / run the requery).

Pure (json + stdlib only); unit-tested. Isolated — no music imports.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_ATTEMPTS = 6   # total tries (candidate hops + requeries) before giving up


def title_variants(ctx: dict) -> list:
    """Every name worth searching for one title, best first.

    A wishlist title is a DATABASE title — "Insomnia (2024)", "The Voice (AU)",
    "Gabby's Dollhouse (2021)". Scene releases are not named that way: they use
    ``Insomnia.2024`` and ``The.Voice.AU``, so a query built from the raw title
    cannot match, and every variant in the ladder below inherited the same dead
    parenthetical. On the live install six such shows had burnt 5,071 fruitless
    searches between them without a single grab.

    Order matters more than length: the retry budget is a handful of attempts, so
    the primary title comes first (never regress a search that already works),
    then the scene form with the brackets flattened, then without them at all,
    then TMDB's aliases. For an ordinary title every variant collapses onto the
    primary and is deduped away, so nothing changes for the shows that were fine.
    Pure."""
    out: list = []
    seen = set()

    def add(value):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)

    primary = ctx.get("title")
    add(primary)
    if primary:
        add(re.sub(r"[()\[\]]", " ", str(primary)))          # Insomnia 2024
        add(re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", str(primary)))   # Insomnia
    for alias in (ctx.get("titles") or []):
        add(alias)
    return out


def next_query(ctx: dict, tried: Any) -> str | None:
    """The next alternate slskd query to try for a search context, or None when
    exhausted. Movie: 'Title Year' then 'Title'. TV keeps the SxxExx/Sxx identity but
    offers a couple of numbering variants, then — when the episode has an air date —
    DATE-form queries (Sonarr's daily-series naming: 'Title 2026 07 08'), since daily
    shows (late night / soaps / House Hunters) release by date, not SxxExx. The ranker
    accepts a date match as the episode identity, so these are safe to try last."""
    ctx = ctx or {}
    triedset = set(tried or [])
    scope = str(ctx.get("scope") or "movie").lower()
    names = title_variants(ctx)
    title = names[0] if names else ""
    cands = []
    if scope == "movie":
        if ctx.get("year"):
            cands.append(("%s %s" % (title, ctx["year"])).strip())
        cands.extend(names)
    elif scope == "episode" and ctx.get("season") is not None and ctx.get("episode") is not None:
        s, e = int(ctx["season"]), int(ctx["episode"])
        stype = str(ctx.get("series_type") or "").lower()
        ad = str(ctx.get("air_date") or "")[:10]
        # Series type (P8) front-loads the identity the scene actually uses:
        # dailies lead with the air date, anime with the absolute number. The
        # SxxExx variants stay in the ladder as fallbacks either way.
        if stype == "daily" and len(ad) == 10:
            cands.append("%s %s" % (title, ad.replace("-", ".")))   # Title 2026.07.08
            cands.append("%s %s" % (title, ad.replace("-", " ")))   # Title 2026 07 08
        if stype == "anime" and ctx.get("absolute"):
            cands.append("%s %s" % (title, ctx["absolute"]))        # Title 1071
        for name in names:
            cands.append("%s S%02dE%02d" % (name, s, e))
        cands.append("%s %dx%02d" % (title, s, e))
        if len(ad) == 10 and stype != "daily":
            cands.append("%s %s" % (title, ad.replace("-", " ")))   # Title 2026 07 08
            cands.append("%s %s" % (title, ad.replace("-", ".")))   # Title 2026.07.08
    elif scope == "season" and ctx.get("season") is not None:
        s = int(ctx["season"])
        for name in names:
            cands.append("%s S%02d" % (name, s))
        cands.append("%s Season %d" % (title, s))
    else:
        cands.append(title)
    for q in cands:
        if q and q not in triedset:
            return q
    return None


def _loads(s, default):
    try:
        v = json.loads(s) if s else default
        return v if v is not None else default
    except (ValueError, TypeError):
        return default


def plan_retry(row: dict, max_attempts: int = MAX_ATTEMPTS, blocked=frozenset(),
               blocked_users=frozenset()) -> dict:
    """Decide what to do for a failed download row. Returns one of:
      {action: 'candidate', candidate: {...}, rest: [...]}  — try the next stored hit
      {action: 'requery', query: str, ctx: {...}}           — re-search a new query
      {action: 'fail', reason: str}                         — genuinely out of options
    Pure: reads the row's JSON columns (candidates / tried_files / search_ctx / tried_queries).

    ``blocked`` = {(username, filename)} (per-release) and ``blocked_users`` = usernames
    blocked source-wide. Both compose with tried_files, not folded into it: tried is
    per-row ("this attempt sequence"), the blocklists are global + permanent — so a
    candidate STORED before the block still gets dropped here on retry."""
    if int(row.get("attempts") or 0) >= max_attempts:
        return {"action": "fail", "reason": "retry budget reached"}
    tried_files = set(_loads(row.get("tried_files"), []))
    fresh = [c for c in _loads(row.get("candidates"), [])
             if c.get("filename") not in tried_files
             and c.get("username") not in blocked_users
             and (c.get("username"), c.get("filename")) not in blocked]
    if fresh:
        return {"action": "candidate", "candidate": fresh[0], "rest": fresh[1:]}
    ctx = _loads(row.get("search_ctx"), {})
    q = next_query(ctx, _loads(row.get("tried_queries"), []))
    if q:
        return {"action": "requery", "query": q, "ctx": ctx}
    return {"action": "fail", "reason": "no candidates or queries left"}


def merge_candidates(new_accepted: Any, tried_files: Any, blocked=frozenset(),
                     blocked_users=frozenset()) -> list:
    """Turn fresh accepted search results into candidate dicts, dropping anything
    already attempted (so a requery never re-tries the same failing release) and
    anything on the release blocklist (a specific file OR a source-wide uploader)."""
    seen = set(tried_files or [])
    out = []
    for r in (new_accepted or []):
        fn = r.get("filename")
        if not fn or fn in seen or r.get("username") in blocked_users \
                or (r.get("username"), fn) in blocked:
            continue
        seen.add(fn)
        out.append({"username": r.get("username"), "filename": fn, "size_bytes": r.get("size_bytes"),
                    "quality_label": r.get("quality_label"), "release_title": r.get("title") or r.get("release_title")})
    return out


def exhausted_reason(stats: Any, tried_files: Any = None) -> str:
    """Why a download gave up, in the words of what actually happened.

    Every exhausted retry recorded the same sentence - "No working release found
    after retries". On Boulder's install that is 1,495 of 1,507 Soulseek failures
    and 437 of 443 torrent ones: nearly two thousand rows, no diagnosis in any of
    them. "Nothing was posted", "six were posted and your profile refused them
    all" and "four peers were tried and none sent the file" are three different
    problems with three different answers, and they all read identically.

    ``stats`` = {searches, hits, accepted, refusals} accumulated by the requery
    loop; ``refusals`` are the per-candidate verdicts, so the same evidence
    summariser the wishlist row uses can name the rule that refused the best of
    them. Pure.
    """
    st = stats if isinstance(stats, dict) else {}
    searches = int(st.get("searches") or 0)
    hits = int(st.get("hits") or 0)
    tried = len(_loads(tried_files, []) if not isinstance(tried_files, list) else tried_files)

    if searches and not hits:
        return ("No results from %d search%s" % (searches, "" if searches == 1 else "es"))

    if hits and not int(st.get("accepted") or 0):
        try:
            from core.video.wishlist_evidence import refusal_line, summarize_search
            line = refusal_line(summarize_search(st.get("refusals") or []))
        except Exception:   # noqa: BLE001 - a diagnosis must never break the failure path
            line = None
        if line:
            # The evidence line already opens with its own count for the
            # "they were all some other title" case; don't say it twice.
            return line if line[:1].isdigit() else (
                "%d result%s, none accepted — %s" % (hits, "" if hits == 1 else "s", line))
        return "%d result%s, none matched your quality profile" % (hits, "" if hits == 1 else "s")

    if tried:
        return ("Tried %d release%s, none completed — the sources went away or never sent the file"
                % (tried, "" if tried == 1 else "s"))

    return "No working release found after retries"


__all__ = ["MAX_ATTEMPTS", "next_query", "plan_retry", "merge_candidates",
           "exhausted_reason"]
