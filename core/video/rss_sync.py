"""RSS-speed grabbing — Sonarr's RSS sync, via Prowlarr (video side).

The wishlist drain acquires by SEARCHING each item hourly. This module closes
the reaction-time gap from the other direction: every few minutes it pulls the
indexers' LATEST releases (the Newznab empty-query "RSS" form — one aggregate
Prowlarr call, no per-item searches) and matches them against the wishlist.
A wanted episode posted to an indexer lands minutes later instead of at the
next drain tick — the single biggest speed difference vs Sonarr/Radarr.

Everything acquisition-shaped is the drain's own seams, so behavior can't
drift: the same GATED wishlist queries (released/aired only — RSS must not
hunt unreleased titles), the same upgrade-until-cutoff annotation (owned items
accept strictly-better only), the same ranker (``_evaluate_hits`` — quality
profile + blocklist + scope validation), the same ``pick_best`` and the same
``_default_enqueue`` (disk guard included). Active-key dedupe prevents double
grabs against in-flight downloads and the drain alike.

Soulseek is untouched — there is no feed to poll on a P2P network; slskd
acquisition stays the drain's job.
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("video.rss_sync")

_running = False
_lock = threading.Lock()

# One aggregate pull covers both kinds; indexers that don't support empty-query
# (RSS) requests simply contribute nothing.
_FETCH_LIMIT = 200


def is_running() -> bool:
    return _running


def fetch_recent_releases(limit: int = _FETCH_LIMIT) -> Optional[List[Dict[str, Any]]]:
    """The indexers' latest releases (movies + TV categories), projected into
    the shared hit shape. None = Prowlarr not configured; [] = nothing/errors."""
    from core.video.prowlarr_search import _MOVIE_CATS, _TV_CATS, _client, _project
    client = _client()
    if not client.is_configured():
        return None
    try:
        results = client._search_sync("", _MOVIE_CATS + _TV_CATS, [], limit)
    except Exception as e:   # noqa: BLE001 - a feed hiccup is a skipped tick, not a crash
        logger.warning("rss: recent-releases fetch failed: %s", e)
        return []
    hits: Dict[str, Dict[str, Any]] = {}
    for r in results:
        url = getattr(r, "magnet_uri", None) or getattr(r, "download_url", None)
        if not url:
            continue
        proto = str(getattr(r, "protocol", "") or "torrent")
        keyv = getattr(r, "guid", None) or url
        if keyv in hits:
            continue
        hits[keyv] = _project(r, url, proto)
    return list(hits.values())


# Words too common to distinguish a title — a release containing only these is
# not a match. "The Oval" vs "…The Mummy" both share 'the'; without this every
# wishlist title matched every release (the RSS flood Boulder's logs caught).
_STOPWORDS = frozenset({
    "the", "and", "a", "an", "of", "to", "in", "on", "for", "with", "at",
    "by", "from", "his", "her", "its", "is", "are", "be",
})


def _tokens(text: Any) -> List[str]:
    """Word tokens of a title/release name: lowercased, punctuation split out
    (so apostrophes/dots don't fuse or hide words). Length >= 2 kept."""
    return [w for w in re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split()
            if len(w) >= 2]


def _significant(tokens: List[str]) -> set:
    """The distinguishing words of a title — stopwords + 1-char noise dropped.
    Falls back to the raw tokens when a title is ALL stopwords (rare)."""
    sig = {w for w in tokens if len(w) >= 3 and w not in _STOPWORDS}
    return sig or set(tokens)


def _prescreen(hits: List[Dict[str, Any]], titles: List[str]) -> List[Dict[str, Any]]:
    """Cheap recall shield before the real ranker runs: keep a hit only when a
    wanted title's DISTINGUISHING words ALL appear as whole words in the release
    name. WORD-level (not substring — 'all' must not match 'Cornwall') and
    stopword-aware ('the' alone never qualifies). ``_evaluate_hits`` still does
    the authoritative title/scope/quality gate; this just stops the feed's
    every-release-matches-every-title flood from reaching it."""
    wanted = [s for s in (_significant(_tokens(t)) for t in titles) if s]
    if not wanted:
        return []
    out = []
    for h in hits:
        htoks = set(_tokens(h.get("title")))
        if any(sig <= htoks for sig in wanted):
            out.append(h)
    return out


def rss_pass(*, fetch: Optional[Callable[[], Optional[List[Dict[str, Any]]]]] = None,
             log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """One RSS tick: recent releases vs the eligible wishlist. Returns
    {status, grabbed, matched_items, releases, skipped?}."""
    global _running
    with _lock:
        if _running:
            return {"status": "skipped", "reason": "already_running"}
        _running = True
    try:
        return _rss_pass_inner(fetch=fetch, log=log or (lambda m: None))
    finally:
        with _lock:
            _running = False


def _rss_pass_inner(*, fetch, log) -> Dict[str, Any]:
    from api.video import get_video_db
    from core.automation.handlers import video_process_wishlist as vpw

    hits = (fetch or fetch_recent_releases)()
    if hits is None:
        return {"status": "skipped", "reason": "prowlarr_not_configured",
                "grabbed": 0, "releases": 0}
    if not hits:
        return {"status": "completed", "grabbed": 0, "releases": 0, "matched_items": 0}

    db = get_video_db()
    # Respect the download mode: RSS only ever grabs from indexers, so a user
    # whose chain has no torrent/usenet must never receive one from here.
    from core.video import download_config
    cfg = download_config.load(db)
    mode = str(cfg.get("download_mode") or "soulseek")
    chain = (cfg.get("hybrid_order") or ["soulseek"]) if mode == "hybrid" else [mode]
    allowed = {"torrent", "usenet"} & set(chain)
    if not allowed:
        return {"status": "skipped", "reason": "no_indexer_source_enabled",
                "grabbed": 0, "releases": len(hits)}
    hits = [h for h in hits if str(h.get("protocol") or "torrent") in allowed]
    grabbed = 0
    matched = 0
    active = set(vpw._default_active_keys("movie") or set())   # one row set covers both kinds
    try:
        cutoff = vpw._default_cutoff_rank()
    except Exception:   # noqa: BLE001 - no profile → no cutoff
        cutoff = 0

    for media_type, items in (("movie", db.movie_wishlist_to_download()),
                              ("episode", db.episode_wishlist_to_download())):
        if any(it.get("owned") for it in items):
            per_item = vpw._cutoff_rank_for_item if any(
                it.get("quality_profile_id") for it in items) else None
            items = vpw.annotate_upgrades(items, cutoff, cutoff_for=per_item)
        target = vpw._default_target_dir(media_type)
        if not target:
            continue
        for item in items:
            if vpw.item_key(item, media_type) in active:
                continue
            titles = [item.get("title") or item.get("show_title")]
            pool = _prescreen(hits, titles)
            if not pool:
                continue
            cands = _rank(pool, item, media_type)
            if not cands:
                continue
            matched += 1
            min_rank = int(item.get("_min_rank") or 0)
            best = vpw.pick_best(cands, min_rank)
            if not best:
                # A namesake was in the feed but nothing qualified. Say WHY so a
                # quiet '0 grabbed' is provable, not a mystery (Boulder).
                log("RSS skip: %s — %s" % (_item_label(item, media_type),
                                           _skip_reason(cands, min_rank, item)))
                continue
            if vpw._default_enqueue(item, best, cands, media_type, target):
                grabbed += 1
                active.add(vpw.item_key(item, media_type))
                log("RSS grab: %s (%s)" % (best.get("title"), media_type))
            else:
                log("RSS skip: %s — a release qualified but the grab was refused "
                    "(disk guard or client error)" % _item_label(item, media_type))

    return {"status": "completed", "grabbed": grabbed,
            "matched_items": matched, "releases": len(hits)}


def _item_label(item: Dict[str, Any], media_type: str) -> str:
    if media_type == "movie":
        y = item.get("year")
        return "%s%s" % (item.get("title") or "?", " (%s)" % y if y else "")
    return "%s S%02dE%02d" % (item.get("show_title") or "?",
                              int(item.get("season_number") or 0),
                              int(item.get("episode_number") or 0))


def _reason_text(rej: Any) -> str:
    """A candidate's ``rejected`` field is a string or a list of reasons —
    normalize to a short phrase."""
    if isinstance(rej, (list, tuple)):
        rej = "; ".join(str(r) for r in rej if r)
    return str(rej or "").strip()


def _skip_reason(cands: List[Dict[str, Any]], min_rank: int, item: Dict[str, Any]) -> str:
    """Explain why a matched item grabbed nothing. Two cases: an accepted
    release existed but wasn't a strict-enough UPGRADE for an owned copy, or
    nothing was accepted at all (surface the ranker's own reject reason)."""
    accepted = [c for c in cands if c.get("accepted")]
    if accepted and min_rank:
        best_res = (accepted[0].get("resolution") or "?")
        have = item.get("owned_resolutions") or "current copy"
        return ("best new release is %s — not better than your %s (upgrade-only)"
                % (best_res, have))
    if not accepted:
        # the top-ranked (best) rejected candidate carries the most relevant why
        why = _reason_text(cands[0].get("rejected")) if cands else ""
        n = len(cands)
        base = why or "didn't pass the quality/scope filter"
        return "%d release(s) matched by name but none accepted (%s)" % (n, base)
    return "no release qualified"


def _rank(pool: List[Dict[str, Any]], item: Dict[str, Any], media_type: str) -> List[Dict[str, Any]]:
    """The drain's ranker over a pre-fetched release pool (no network). Tags each
    accepted candidate with its protocol as the grab source."""
    from api.video import get_video_db
    from api.video.downloads import _evaluate_hits
    from core.automation.handlers.video_process_wishlist import search_context
    from core.video.quality_profile import load_for_item
    ctx = search_context(item, media_type)
    cands = _evaluate_hits(pool, load_for_item(get_video_db(), item), ctx["scope"],
                           ctx.get("season"), ctx.get("episode"),
                           want_year=ctx.get("year"),
                           want_title=ctx.get("titles") or ctx.get("title"),
                           want_date=ctx.get("air_date"), want_absolute=ctx.get("absolute"))
    for c in cands:
        c["source"] = "usenet" if str(c.get("protocol") or "") == "usenet" else "torrent"
    # Full ranked list, not just accepted: rejected candidates ride along into
    # the download row as the auto-retry ladder, exactly like the drain.
    return cands
