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
from typing import Any

MAX_ATTEMPTS = 6   # total tries (candidate hops + requeries) before giving up


def next_query(ctx: dict, tried: Any) -> str | None:
    """The next alternate slskd query to try for a search context, or None when
    exhausted. Movie: 'Title Year' then 'Title'. TV keeps the SxxExx/Sxx identity but
    offers a couple of numbering variants."""
    ctx = ctx or {}
    triedset = set(tried or [])
    scope = str(ctx.get("scope") or "movie").lower()
    title = str(ctx.get("title") or "").strip()
    cands = []
    if scope == "movie":
        if ctx.get("year"):
            cands.append(("%s %s" % (title, ctx["year"])).strip())
        cands.append(title)
    elif scope == "episode" and ctx.get("season") is not None and ctx.get("episode") is not None:
        s, e = int(ctx["season"]), int(ctx["episode"])
        cands.append("%s S%02dE%02d" % (title, s, e))
        cands.append("%s %dx%02d" % (title, s, e))
    elif scope == "season" and ctx.get("season") is not None:
        s = int(ctx["season"])
        cands.append("%s S%02d" % (title, s))
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


def plan_retry(row: dict, max_attempts: int = MAX_ATTEMPTS) -> dict:
    """Decide what to do for a failed download row. Returns one of:
      {action: 'candidate', candidate: {...}, rest: [...]}  — try the next stored hit
      {action: 'requery', query: str, ctx: {...}}           — re-search a new query
      {action: 'fail', reason: str}                         — genuinely out of options
    Pure: reads the row's JSON columns (candidates / tried_files / search_ctx / tried_queries)."""
    if int(row.get("attempts") or 0) >= max_attempts:
        return {"action": "fail", "reason": "retry budget reached"}
    tried_files = set(_loads(row.get("tried_files"), []))
    fresh = [c for c in _loads(row.get("candidates"), []) if c.get("filename") not in tried_files]
    if fresh:
        return {"action": "candidate", "candidate": fresh[0], "rest": fresh[1:]}
    ctx = _loads(row.get("search_ctx"), {})
    q = next_query(ctx, _loads(row.get("tried_queries"), []))
    if q:
        return {"action": "requery", "query": q, "ctx": ctx}
    return {"action": "fail", "reason": "no candidates or queries left"}


def merge_candidates(new_accepted: Any, tried_files: Any) -> list:
    """Turn fresh accepted search results into candidate dicts, dropping anything
    already attempted (so a requery never re-tries the same failing release)."""
    seen = set(tried_files or [])
    out = []
    for r in (new_accepted or []):
        fn = r.get("filename")
        if not fn or fn in seen:
            continue
        seen.add(fn)
        out.append({"username": r.get("username"), "filename": fn, "size_bytes": r.get("size_bytes"),
                    "quality_label": r.get("quality_label"), "release_title": r.get("title") or r.get("release_title")})
    return out


__all__ = ["MAX_ATTEMPTS", "next_query", "plan_retry", "merge_candidates"]
