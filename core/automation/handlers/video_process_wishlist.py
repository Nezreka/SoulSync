"""Automation handlers: ``video_process_movie_wishlist`` / ``video_process_episode_wishlist``.

The Soulseek counterpart of the YouTube wishlist drain — the piece that finally makes the
people/airing scans pay off. For wished, RELEASED movies (and aired episodes) it searches
Soulseek, picks the best release per the quality profile, and enqueues the download; the
existing ``download_monitor`` finishes + organises + archives it, exactly like a manual grab.

Shape mirrors the YouTube drain (Boulder: "same standard"): it processes the WHOLE eligible
wishlist (no total cap), but the slow part — each item needs a ~20s blocking Soulseek search
— runs only a FEW at a time (``max_concurrent``). A ``guard`` keeps the next hourly tick from
overlapping a run that's still working, so it can't pile up.

Movies are gated on ``status='wanted'`` (released; skips 'monitored'/unreleased). Episodes
are all-wished (the airing scan only adds aired ones). Items already downloading are skipped
so re-runs never double-grab. The pick is the top ACCEPTED release — the ranker already
encodes the quality profile's accept/reject/score, so no extra rules here.

Shared automation side (may import ``core.video`` / ``api.video``); owns its own progress.
The search + enqueue are injected seams, so selection/pick/record are pure + unit-tested.
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.automation.deps import AutomationDeps
from utils.logging_config import get_logger

logger = get_logger("automation.video_process_wishlist")


# ── pure helpers ──────────────────────────────────────────────────────────────
def acceptable_candidates(candidates: List[Dict[str, Any]],
                          min_rank: int = 0) -> List[Dict[str, Any]]:
    """Every release worth trying, best first. ``_evaluate_hits`` already sorts
    best-first (accepted, score, availability), so this just filters.

    ``min_rank`` > 0 = an UPGRADE pick for an owned item: only releases with a
    resolution STRICTLY better than the current copy qualify (a same-quality
    re-grab would just import_fail as 'not an upgrade' — the old re-download
    loop). Unknown-resolution releases can't prove they're better, so they
    don't qualify either."""
    from core.video.quality_eval import resolution_rank
    out = []
    for c in candidates or []:
        if not c.get("accepted"):
            continue
        if min_rank and resolution_rank(c.get("resolution")) <= min_rank:
            continue
        out.append(c)
    return out


def pick_best(candidates: List[Dict[str, Any]], min_rank: int = 0) -> Optional[Dict[str, Any]]:
    """The single best acceptable release, or None. Kept as the one-shot form of
    :func:`acceptable_candidates` for callers that only ever grab once."""
    top = acceptable_candidates(candidates, min_rank)
    return top[0] if top else None


# How many releases one drain pass will offer the download client for a single
# item before giving up on this run. The top pick being refused (already in the
# client, a magnet it won't parse, a dead tracker) used to abandon the item
# entirely and re-offer the SAME release an hour later, forever — a live row hit
# 133 identical refusals that way. Walking a few keeps one bad release from
# blocking an item without turning a stuck item into an indexer hammer.
MAX_GRAB_ATTEMPTS = 3
MIN_SEASON_PACK_EPISODES = 3
SOURCE_REFUSAL_LIMIT = 2
SOURCE_COOLDOWN_SECONDS = 6 * 60 * 60
_SOURCE_REFUSALS: Dict[tuple, int] = {}
_SOURCE_COOLDOWNS: Dict[tuple, float] = {}
_SOURCE_COOLDOWN_LOCK = threading.Lock()


def display_name(item: Dict[str, Any], media_type: str) -> str:
    """The human name for an item in logs and progress lines. Movie rows carry
    ``title``; episode rows carry ``show_title`` — reading only ``title`` is why
    every episode grab-refusal in the log said "refused for None"."""
    name = item.get("title") or item.get("show_title") or "?"
    if media_type == "episode":
        season = int(item.get("season_number") or 0)
        episode = item.get("episode_number")
        if episode is None:
            return "%s S%02d" % (name, season)
        return "%s S%02dE%02d" % (name, season, int(episode or 0))
    return str(name)


def enqueue_outcome(result: Any) -> Dict[str, Any]:
    """Normalise whatever the enqueue seam returned to ``{ok, error}``.

    The seam used to answer a bare bool and several callers/tests still do, so
    both shapes are accepted — but a bool cannot say WHY, and 'why' is the whole
    point: a client refusing a release is not the same event as the quality
    profile rejecting one, and conflating them is what made a stuck item report
    "none met your quality profile" about releases it had actually accepted."""
    if isinstance(result, dict):
        return {"ok": bool(result.get("ok")), "error": result.get("error")}
    return {"ok": bool(result), "error": None}


def _candidate_source(cand: Dict[str, Any]) -> str:
    src = str((cand or {}).get("source") or "soulseek").strip().lower()
    return "torrent" if src == "extto" else (src or "soulseek")


def source_refusal_key(item: Dict[str, Any], media_type: str, cand: Dict[str, Any]) -> tuple:
    """The temporary cooldown identity: this wanted item on this transport."""
    return item_key(item, media_type), _candidate_source(cand)


def source_cooldown_remaining(item: Dict[str, Any], media_type: str, cand: Dict[str, Any],
                              *, now: Optional[float] = None) -> int:
    now = time.time() if now is None else float(now)
    key = source_refusal_key(item, media_type, cand)
    with _SOURCE_COOLDOWN_LOCK:
        until = float(_SOURCE_COOLDOWNS.get(key) or 0)
        if until <= now:
            _SOURCE_COOLDOWNS.pop(key, None)
            return 0
        return int(round(until - now))


def note_source_refusal(item: Dict[str, Any], media_type: str, cand: Dict[str, Any],
                        *, now: Optional[float] = None,
                        limit: int = SOURCE_REFUSAL_LIMIT,
                        cooldown_seconds: int = SOURCE_COOLDOWN_SECONDS) -> None:
    """After repeated client refusals, pause this item/source briefly.

    This is intentionally about the download client refusing an accepted release,
    not the quality profile rejecting a result. It keeps a bad torrent client/indexer
    handoff from being offered the same item every drain while other sources can
    still be tried on later passes.
    """
    now = time.time() if now is None else float(now)
    key = source_refusal_key(item, media_type, cand)
    with _SOURCE_COOLDOWN_LOCK:
        strikes = int(_SOURCE_REFUSALS.get(key) or 0) + 1
        _SOURCE_REFUSALS[key] = strikes
        if strikes >= max(1, int(limit or 1)):
            _SOURCE_COOLDOWNS[key] = now + max(1, int(cooldown_seconds or SOURCE_COOLDOWN_SECONDS))


def clear_source_refusal(item: Dict[str, Any], media_type: str, cand: Dict[str, Any]) -> None:
    key = source_refusal_key(item, media_type, cand)
    with _SOURCE_COOLDOWN_LOCK:
        _SOURCE_REFUSALS.pop(key, None)
        _SOURCE_COOLDOWNS.pop(key, None)


def reset_source_cooldowns() -> None:
    """Test/support hook: clear temporary in-process refusal memory."""
    with _SOURCE_COOLDOWN_LOCK:
        _SOURCE_REFUSALS.clear()
        _SOURCE_COOLDOWNS.clear()


def annotate_upgrades(items: List[Dict[str, Any]], cutoff_rank: int,
                      cutoff_for: Optional[Callable[[Dict[str, Any]], int]] = None) -> List[Dict[str, Any]]:
    """Upgrade-until-cutoff eligibility over the wishlist rows (pure).

    Unowned items pass through untouched. Owned items (the queries annotate
    ``owned`` + ``owned_resolutions``) are judged against the cutoff:
      · already meet it       → skipped (their row should be gone; the Wishlist
                                Audit job sweeps stragglers)
      · below it              → kept, carrying ``_min_rank`` = the current
                                copy's rank so only strictly-better wins
      · resolution unreadable → skipped (can't prove an upgrade; the audit job
                                surfaces these)
    An empty cutoff ('always chase the best') means owned items are never
    'done' — they stay upgrade-eligible forever.

    ``cutoff_for`` (P2, per-title profiles): when given, each owned item is
    judged against ITS OWN profile's cutoff instead of the global
    ``cutoff_rank``. Still pure — the callable is injected."""
    from core.video.quality_eval import resolution_rank
    out = []
    for it in items or []:
        if not it.get("owned"):
            out.append(it)
            continue
        rks = [resolution_rank(r) for r in str(it.get("owned_resolutions") or "").split(",")
               if r.strip()]
        cur = max(rks, default=0)
        if cur == 0:
            continue
        eff_cutoff = cutoff_for(it) if cutoff_for is not None else cutoff_rank
        if eff_cutoff and cur >= eff_cutoff:
            continue
        it = dict(it)
        it["_min_rank"] = cur
        out.append(it)
    return out


def _cutoff_rank_for_item(item: Dict[str, Any]) -> int:
    """The cutoff rank under the item's OWN profile (per-title, P2)."""
    from api.video import get_video_db
    from core.video.quality_eval import resolution_rank
    from core.video.quality_profile import load_for_item
    return resolution_rank((load_for_item(get_video_db(), item) or {}).get("cutoff_resolution"))


def _default_cutoff_rank() -> int:
    """The profile cutoff as a resolution rank (0 = no cutoff set)."""
    from api.video import get_video_db
    from core.video.quality_eval import resolution_rank
    from core.video.quality_profile import load as load_profile
    return resolution_rank((load_profile(get_video_db()) or {}).get("cutoff_resolution"))


# Scene names end in the release group: "Show.S01E01.1080p.WEB-DL-NTb". Take the
# tail after the last '-', minus any container extension. Deliberately blunt —
# a name we cannot read the group from must never be treated as "group ''",
# because an allow-list would then reject every unparsable release.
_GROUP_RE = re.compile(r"-([A-Za-z0-9_.]{2,})$")


def release_group(name: str) -> str:
    text = re.sub(r"\.(mkv|mp4|avi|ts|m2ts|wmv|mov)$", "", str(name or "").strip(), flags=re.I)
    m = _GROUP_RE.search(text)
    return m.group(1).strip() if m else ""


def title_overrides_for_item(item: Dict[str, Any], media_type: str) -> Dict[str, Any]:
    """This title's per-title acquisition overrides, read once per item.

    Cached on the item the way _source_snapshot is: the drain hands the same
    dict to search() and record_outcome(), so one db read covers the run.
    """
    cached = item.get("_overrides")
    if cached is not None:
        return cached
    blank = {"preferred_sources": [], "release_group_allow": [],
             "release_group_block": [], "pack_preference": "auto"}
    try:
        from api.video import get_video_db
        kind = "movie" if media_type == "movie" else "show"
        tmdb = item.get("tmdb_id") if media_type == "movie" else item.get("show_tmdb_id")
        out = get_video_db().title_overrides_for(
            kind, tmdb_id=tmdb, library_id=item.get("library_id")) or blank
    except Exception:   # noqa: BLE001 - an override lookup must never stop a grab
        logger.exception("title_overrides_for_item failed")
        out = blank
    item["_overrides"] = out
    return out


def chain_for(chain: List[str], overrides: Dict[str, Any]) -> List[str]:
    """The download chain narrowed to this title's preferred sources, in the
    USER's order. An empty preference, or one naming nothing the global config
    offers, follows the global chain — never an empty chain, which would search
    nothing at all and look like 'no releases exist'."""
    want = [str(s).strip().lower() for s in (overrides or {}).get("preferred_sources") or []]
    if not want:
        return list(chain)
    picked = [s for s in want if s in chain]
    return picked or list(chain)


def apply_group_overrides(candidates: List[Dict[str, Any]],
                          overrides: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Mark releases this title refuses as rejected, with the reason.

    NOT a silent drop: the drain reports 'hits, none accepted' with each
    release's rejection note, and "you blocked that group" is exactly the sort
    of thing a user needs told. Dropping them instead reads as 'no releases
    exist', which is the lie the refusal work already fixed once.
    """
    allow = [str(g).strip().lower() for g in (overrides or {}).get("release_group_allow") or []]
    block = [str(g).strip().lower() for g in (overrides or {}).get("release_group_block") or []]
    if not allow and not block:
        return candidates or []
    for c in candidates or []:
        group = release_group(c.get("filename") or c.get("title") or "")
        low = group.lower()
        if block and low and low in block:
            c["accepted"] = False
            c["rejected"] = "release group %s is blocked for this title" % group
        elif allow and low not in allow:
            c["accepted"] = False
            c["rejected"] = ("release group %s is not on this title's allow list" % group
                             if group else "release group unknown; this title allows only %s"
                             % ", ".join(sorted(allow)))
    return candidates or []


def item_key(item: Dict[str, Any], media_type: str) -> tuple:
    """Stable identity for de-duping a wished item against active downloads."""
    if media_type == "movie":
        return ("movie", str(item.get("tmdb_id")))
    if item.get("episode_number") is None and item.get("season_number") is not None:
        return ("season", str(item.get("show_tmdb_id")), int(item.get("season_number") or 0))
    return ("episode", str(item.get("show_tmdb_id")),
            int(item.get("season_number") or 0), int(item.get("episode_number") or 0))


def active_download_keys(active: Iterable[Dict[str, Any]]) -> set:
    """Identity keys for the movie/episode downloads already in flight, so we don't
    re-grab them. Episodes read season/episode out of the row's ``search_ctx``."""
    keys = set()
    for d in active or []:
        kind = str(d.get("kind") or "").lower()
        if kind == "movie":
            keys.add(("movie", str(d.get("media_id"))))
        elif kind in ("episode", "show", "season"):
            ctx = d.get("search_ctx")
            if isinstance(ctx, str):
                try:
                    ctx = json.loads(ctx)
                except (ValueError, TypeError):
                    ctx = {}
            ctx = ctx if isinstance(ctx, dict) else {}
            season = int(ctx.get("season") or 0)
            episode = ctx.get("episode")
            if episode is None:
                keys.add(("season", str(d.get("media_id")), season))
            else:
                keys.add(("episode", str(d.get("media_id")), season, int(episode or 0)))
    return keys


def _packable_episode(item: Dict[str, Any]) -> bool:
    """Whether an episode row is safe to fold into an automatic season-pack try."""
    if item.get("owned") or item.get("_min_rank"):
        return False
    if item.get("episode_number") is None or item.get("season_number") is None:
        return False
    if str(item.get("series_type") or "").strip().lower() in ("daily", "anime"):
        return False
    return bool(item.get("show_tmdb_id") and item.get("show_title"))


def season_pack_requests(items: List[Dict[str, Any]], active: Iterable,
                         min_missing: int = MIN_SEASON_PACK_EPISODES) -> List[Dict[str, Any]]:
    """Synthetic season-search rows for shows with many missing episodes.

    The ordinary drain receives one wishlist row per episode. When several rows
    belong to the same show season, searching each SxxExx first burns indexer
    budget and misses the release shape Sonarr prefers: one season pack. This
    groups only straightforward missing standard episodes; upgrades, daily/anime
    numbering, and seasons with an in-flight member stay on the per-episode path.
    """
    try:
        floor = max(2, int(min_missing or MIN_SEASON_PACK_EPISODES))
    except (TypeError, ValueError):
        floor = MIN_SEASON_PACK_EPISODES
    active_set = set(active or set())
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for it in items or []:
        if not _packable_episode(it):
            continue
        key = (str(it.get("show_tmdb_id")), int(it.get("season_number") or 0))
        groups.setdefault(key, []).append(it)

    out: List[Dict[str, Any]] = []
    for (show_id, season), rows in groups.items():
        if len(rows) < floor:
            continue
        if ("season", show_id, season) in active_set:
            continue
        if any(("episode", show_id, season, int(r.get("episode_number") or 0)) in active_set
               for r in rows):
            continue
        first = rows[0]
        pack = dict(first)
        pack["episode_number"] = None
        pack["episode_title"] = None
        pack["_pack_members"] = [
            (int(r.get("season_number") or 0), int(r.get("episode_number") or 0)) for r in rows]
        out.append(pack)
    return out


def _acceptable_titles(primary: Any, kind: str, tmdb_id: Any) -> List[str]:
    """[primary title, *TMDB alternative titles] — deduped, primary first. The alias
    set the release-title gate matches against (so a release named by a known aka still
    matches). Best-effort: just the primary when TMDB is unavailable."""
    aliases: List[str] = []
    if tmdb_id:
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            aliases = get_video_enrichment_engine().alt_titles_for(kind, tmdb_id) or []
        except Exception:   # noqa: BLE001 - a matching assist must never break a grab
            aliases = []
    out, seen = [], set()
    for t in [primary, *aliases]:
        t = str(t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def search_context(item: Dict[str, Any], media_type: str) -> Dict[str, Any]:
    """The ``search_ctx`` the download row carries (drives the monitor's requery).
    Carries ``titles`` — the primary title plus TMDB aliases — so both the initial pick
    and every retry gate releases against the full alias set."""
    if media_type == "movie":
        ctx = {"scope": "movie", "title": item.get("title"), "year": item.get("year")}
        tmdb_id, kind = item.get("tmdb_id"), "movie"
    else:
        scope = "season" if item.get("episode_number") is None and item.get("season_number") is not None else "episode"
        ctx = {"scope": scope, "title": item.get("show_title"),
               "season": item.get("season_number"),
               "episode": item.get("episode_number"),
               "year": (str(item.get("air_date") or "")[:4] or None),
               # full air date — daily series (Daily Show / Kimmel / soaps) release by
               # DATE, not SxxExx; the ranker + retry queries key off this.
               "air_date": (str(item.get("air_date") or "")[:10] or None)}
        if scope == "season":
            ctx.pop("episode", None)
            ctx.pop("air_date", None)
        # Series type (P8): daily/anime shows QUERY differently. Anime also carries
        # the wanted ABSOLUTE episode number (scene anime is numbered 'Show - 1071',
        # no season) — derived from the library's episode list, best-effort.
        stype = str(item.get("series_type") or "").strip().lower()
        if stype in ("daily", "anime"):
            ctx["series_type"] = stype
        if stype == "anime":
            try:
                from api.video import get_video_db
                ctx["absolute"] = get_video_db().episode_absolute_number(
                    item.get("show_tmdb_id"), item.get("season_number"),
                    item.get("episode_number"))
            except Exception:   # noqa: BLE001 - a numbering assist must never break a grab
                ctx["absolute"] = None
        tmdb_id, kind = item.get("show_tmdb_id"), "show"
    titles = _acceptable_titles(ctx["title"], kind, tmdb_id)
    if len(titles) > 1:
        ctx["titles"] = titles
    # External ids for the indexer search. Prowlarr's structured tvsearch/movie
    # queries take tvdbid/imdbid/tmdbid and route each to the indexers that can
    # resolve it — the id-aware path Sonarr/Radarr rely on so they never depend on
    # a title matching scene spelling. The plumbing accepted these from day one;
    # nothing ever filled them, so every automated search was free text.
    ids = {"tmdb_id": tmdb_id, "imdb_id": item.get("imdb_id")}
    if media_type != "movie":
        ids["tvdb_id"] = item.get("tvdb_id")
    ctx.update({k: v for k, v in ids.items() if v})
    return ctx


def build_download_record(item: Dict[str, Any], best: Dict[str, Any], candidates: List[Dict[str, Any]],
                          *, media_type: str, target_dir: str, query: Any) -> Dict[str, Any]:
    """The ``add_video_download`` row for a chosen release — identical shape to a manual
    grab, so the monitor finishes it the same way (other accepted hits become the retry
    pool)."""
    ctx = search_context(item, media_type)
    # stash the chosen source's peer stats so the drawer can show its availability snapshot
    # (free slot / queue depth / speed at grab time). Retry ignores the extra key.
    peer = {k: best.get(k) for k in ("slots", "queue", "speed", "availability") if best.get(k) is not None}
    if peer:
        ctx = {**ctx, "peer": peer}
    if item.get("_user_initiated"):
        ctx = {**ctx, "user_initiated": True, "import_policy": "user_replace"}
    media_id = str(item.get("tmdb_id") if media_type == "movie" else item.get("show_tmdb_id"))
    source = str(best.get("source") or "soulseek").lower()
    transport_source = "torrent" if source == "extto" else source
    row_kind = "show" if media_type == "episode" and ctx.get("scope") == "season" else media_type
    common = {
        "kind": row_kind, "title": ctx["title"],
        "release_title": best.get("title") or best.get("filename"),
        "size_bytes": int(best.get("size_bytes") or 0), "quality_label": best.get("quality_label"),
        "target_dir": target_dir, "status": "downloading",
        "media_id": media_id, "media_source": "tmdb", "year": ctx.get("year"),
        "poster_url": item.get("poster_url"), "search_ctx": json.dumps(ctx), "attempts": 0,
        # the profile this grab was judged under — the monitor's cutoff/requery
        # decisions stay consistent even if the title is reassigned mid-flight
        "quality_profile_id": item.get("quality_profile_id"),
    }
    if transport_source == "soulseek":
        rest = [c for c in (candidates or []) if c.get("filename") != best.get("filename")]
        return {**common, "source": "soulseek", "username": best.get("username"),
                "filename": best.get("filename"), "candidates": json.dumps(rest),
                "tried_queries": json.dumps([query] if query else []),
                "tried_files": json.dumps([best.get("filename")])}
    # torrent / usenet — tracked by the client ref the grab returned; no Soulseek requery pool.
    return {**common, "source": transport_source, "username": best.get("username"),   # indexer (display)
            # keep the indexer ID too, not just its name. the seeding sweep needs a
            # stable key to look up that tracker's own seed goal.
            "indexer_id": best.get("indexer_id"),
            "filename": best.get("title") or best.get("filename"), "client_ref": best.get("_client_ref"),
            "candidates": json.dumps([]), "tried_queries": json.dumps([]), "tried_files": json.dumps([])}


# ── production seams ──────────────────────────────────────────────────────────
def _default_fetch_items(media_type: str) -> List[Dict[str, Any]]:
    from api.video import get_video_db
    db = get_video_db()
    return db.movie_wishlist_to_download() if media_type == "movie" else db.episode_wishlist_to_download()


def _backfill_movie_available_dates(limit: int = 25) -> None:
    """Resolve the DOWNLOADABLE (home/digital) date for wished movies that don't have one yet —
    TMDB digital/physical, or theatrical + a home-release window (Radarr's 'minimum availability
    = released'). This is what lets the drain SKIP a film that's still only in cinemas instead of
    grabbing a wrong/fake copy. Bounded per run + engine-cached; best-effort. A past-date sentinel
    is stored when TMDB knows nothing, so it isn't re-queried forever (the year check still guards)."""
    try:
        from api.video import get_video_db
        from core.video.enrichment.engine import get_video_enrichment_engine
        db = get_video_db()
        # One-time reset: an earlier version anchored the estimate on TMDB PREMIERE dates
        # (festival screenings months before release), so previously-derived dates are wrong.
        # Wipe them once and re-derive with the wide-theatrical logic.
        if db.get_setting("avail_dates_logic") != "v2":
            db.clear_wishlist_movie_release_dates()
            db.set_setting("avail_dates_logic", "v2")
        need = db.wishlist_movies_missing_release_date(limit)
        if not need:
            return
        eng = get_video_enrichment_engine()
        for tmdb_id in need:
            try:
                db.set_wishlist_release_date(tmdb_id, eng.movie_available_date(tmdb_id) or "1970-01-01")
            except Exception:   # noqa: BLE001 - one lookup failing shouldn't stall the rest
                logger.debug("available-date backfill failed for %s", tmdb_id, exc_info=True)
    except Exception:   # noqa: BLE001 - the backfill is an assist; never block the drain
        logger.debug("movie available-date backfill failed", exc_info=True)


def _default_active_keys(media_type: str) -> set:
    from api.video import get_video_db
    return active_download_keys(get_video_db().get_active_video_downloads())


def _default_target_dir(media_type: str) -> str:
    from api.video import get_video_db
    db = get_video_db()
    if media_type == "movie":
        return db.get_setting("movies_path") or db.get_setting("transfer_path") or ""
    return db.get_setting("tv_path") or ""


def _extto_hits_for_context(ctx: Dict[str, Any]) -> tuple:
    from core.video.extto_search import extto_search
    from core.video.slskd_search import build_query
    query = build_query(ctx["scope"], ctx["title"], year=ctx.get("year"),
                        season=ctx.get("season"), episode=ctx.get("episode"),
                        air_date=ctx.get("air_date"), absolute=ctx.get("absolute"),
                        series_type=ctx.get("series_type"))
    eres = extto_search(query, limit=30, timeout=35, max_candidates=2)
    if not eres.get("configured"):
        return None, "EXT.to requires FlareSolverr"
    if eres.get("error"):
        return None, eres["error"]
    return eres.get("hits") or [], None


def _search_one_source(source: str, item: Dict[str, Any], media_type: str):
    """Search ONE configured source → (ranked candidates tagged with source, error).

    ``torrent`` is the torrent discovery lane: Prowlarr torrent indexers plus EXT.to
    when FlareSolverr is configured. EXT.to can still be listed explicitly in a
    hybrid chain, but normal torrent users should get it automatically."""
    from api.video import get_video_db
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load_for_item
    ctx = search_context(item, media_type)
    profile = load_for_item(get_video_db(), item)   # per-title profile (P2)
    source_notes: List[str] = []
    if source == "soulseek":
        from core.video.download_monitor import _search_for_retry
        from core.video.slskd_search import build_query
        query = build_query(ctx["scope"], ctx["title"], year=ctx.get("year"),
                            season=ctx.get("season"), episode=ctx.get("episode"),
                            air_date=ctx.get("air_date"), absolute=ctx.get("absolute"),
                            series_type=ctx.get("series_type"))
        res = _search_for_retry(query) or {}
        if res.get("started") is False:
            return None, res.get("error")
        hits = res.get("hits") or []
    elif source in ("torrent", "usenet"):
        from core.video.prowlarr_search import prowlarr_search
        pres = prowlarr_search(ctx["scope"], ctx["title"], year=ctx.get("year"),
                               season=ctx.get("season"), episode=ctx.get("episode"), source=source,
                               air_date=ctx.get("air_date"), absolute=ctx.get("absolute"),
                               series_type=ctx.get("series_type"),
                               imdb_id=ctx.get("imdb_id"), tmdb_id=ctx.get("tmdb_id"),
                               tvdb_id=ctx.get("tvdb_id"))
        prowlarr_err = None
        if not pres.get("configured"):
            prowlarr_err = "Prowlarr not configured"
        elif pres.get("error"):
            prowlarr_err = pres["error"]
        hits = [] if prowlarr_err else list(pres.get("hits") or [])
        if source != "torrent":
            # usenet is Prowlarr-only; EXT.to is torrents.
            if prowlarr_err:
                return None, prowlarr_err
        else:
            # EXT.to is the OTHER HALF of the torrent lane, so a Prowlarr that
            # can't run must not take it down with it. Returning early on
            # "not configured" meant a user running FlareSolverr without Prowlarr
            # got nothing from the torrent lane while EXT.to sat there able to
            # answer — the opposite of "normal torrent users get it
            # automatically". The lane has only genuinely failed to search when
            # NEITHER half could run.
            ehits, eerr = _extto_hits_for_context(ctx)
            if ehits is None:
                if prowlarr_err:
                    return None, "; ".join(
                        x for x in (prowlarr_err, eerr or "EXT.to search didn't run") if x)
                source_notes.append("EXT.to skipped — %s" % (eerr or "search didn't run"))
            else:
                if prowlarr_err:
                    # It ran on one leg — say so, same as any other degradation.
                    source_notes.append("Prowlarr skipped — %s" % prowlarr_err)
                hits = hits + list(ehits or [])
    elif source == "extto":
        hits, err = _extto_hits_for_context(ctx)
        if hits is None:
            return None, err
    else:
        return None, "unsupported source %r" % source
    cands = _evaluate_hits(hits, profile, ctx["scope"], ctx.get("season"), ctx.get("episode"),
                           want_year=ctx.get("year"),
                           want_title=ctx.get("titles") or ctx.get("title"),
                           want_date=ctx.get("air_date"), want_absolute=ctx.get("absolute"))
    for c in cands:
        if source == "torrent" and str(c.get("indexer_id") or "").lower() == "extto":
            c["source"] = "extto"
        else:
            c["source"] = source
    return cands, "; ".join(source_notes) or None


# How many individual releases a snapshot keeps per source. Enough to answer
# "why didn't this download" without unbounded growth: a busy search returns 180
# hits and storing them all, per wishlist row, per hour, would be a database of
# torrent names. Ten is enough to see a pattern.
AUDIT_SAMPLE_LIMIT = 10


def audit_samples(cands, limit: int = AUDIT_SAMPLE_LIMIT) -> List[Dict[str, Any]]:
    """The individual releases behind a source's verdict, best-first, bounded.

    The summary line ("15 results, none were this release") could not answer WHY
    any single release lost - twice in one day that meant searching Boulder's
    live Prowlarr by hand to find out. These are the receipts that make the
    stored evidence enough on its own. Pure.
    """
    out: List[Dict[str, Any]] = []
    for c in (cands or [])[:max(0, int(limit))]:
        if not isinstance(c, dict):
            continue
        out.append({
            "title": str(c.get("title") or c.get("filename") or "")[:200],
            "accepted": bool(c.get("accepted")),
            # The rule that turned it down, verbatim, so the drawer can group by it.
            "rejected": (str(c.get("rejected"))[:200] if c.get("rejected") else None),
            "quality": str(c.get("quality_label") or "")[:60] or None,
            "source": str(c.get("source") or "")[:20] or None,
            "seeders": c.get("seeders"),
        })
    return out


def source_outcome(cands, err=None) -> Dict[str, Any]:
    """One source's result for the last search, as a storable fact.

    ``cands is None`` means the source could not SEARCH (slskd down, Prowlarr
    unconfigured) — which is not evidence about the release and must never read
    as "found nothing". Everything else is a real answer: how many releases came
    back, how many the profile accepted, and the rule that turned down the best
    of the rest. Pure."""
    if cands is None:
        return {"ran": False, "results": 0, "accepted": 0, "rejected": 0,
                "reason": err or "search didn't run", "samples": []}
    accepted = sum(1 for c in cands if c.get("accepted"))
    rejected = [c for c in cands if not c.get("accepted")]
    reason = None
    if rejected:
        try:
            from core.video.wishlist_evidence import refusal_line, summarize_search
            reason = refusal_line(summarize_search(rejected))
        except Exception:   # noqa: BLE001 - a snapshot must never break a search
            reason = None
    return {"ran": True, "results": len(cands), "accepted": accepted,
            "rejected": len(rejected), "reason": reason,
            "samples": audit_samples(cands)}


def _default_search(item: Dict[str, Any], media_type: str):
    """Ranked candidates for a wished item, honoring the download mode/order. In hybrid mode the
    sources are tried IN ORDER — the first that yields an ACCEPTED release wins (mirrors the
    music per-item quality-fallback). Returns [] for a real empty result across all sources, or
    **None** (with the error) if no source's search could even run.

    When SOME source in the chain couldn't run (e.g. torrent is first but Prowlarr
    isn't configured), that skip rides back in the error slot alongside the surviving
    results — silent degradation to a weaker source misled a whole run once ('why
    can't it find what's plainly on TPB?'), so the run log must say it every time."""
    from core.video import download_config
    from api.video import get_video_db
    cfg = download_config.load(get_video_db())
    mode = str(cfg.get("download_mode") or "soulseek")
    chain = (cfg.get("hybrid_order") or ["soulseek"]) if mode == "hybrid" else [mode]
    chain = chain_for(chain, title_overrides_for_item(item, media_type))
    skips: List[str] = []
    fallback = None      # hits that didn't pass the profile — kept so the caller can say 'rejected'
    # Per-source receipt for this search, read back by the recorder. Attached to
    # the item the way annotate_upgrades attaches _min_rank: the drain hands the
    # same dict to search() and record_outcome(), and each item is its own.
    snapshot: Dict[str, Any] = {"chain": list(chain), "sources": {}}
    item["_source_snapshot"] = snapshot
    overrides = title_overrides_for_item(item, media_type)
    for src in chain:
        cands, err = _search_one_source(src, item, media_type)
        cands = apply_group_overrides(cands, overrides) if cands is not None else None
        snapshot["sources"][src] = source_outcome(cands, err)
        if cands is None:
            skips.append("%s skipped — %s" % (src, err or "search didn't run"))
            continue
        if any(c.get("accepted") for c in cands):
            return cands, None                       # first source with a usable release wins
        if cands:
            fallback = cands
    note = "; ".join(skips) or None
    if fallback is not None:
        return fallback, note                        # → 'rejected' (hits, none accepted)
    if len(skips) == len(chain):
        return None, note                            # → 'search didn't run' (nothing ran at all)
    return [], note                                  # → 'source empty' (+ any skip note)


def _pack_preference_for(item: Dict[str, Any]) -> str:
    """This show's season-pack preference: auto | prefer | never."""
    return str(title_overrides_for_item(item, "episode").get("pack_preference") or "auto")


def _default_enqueue(item: Dict[str, Any], best: Dict[str, Any], candidates: List[Dict[str, Any]],
                     media_type: str, target_dir: str) -> Dict[str, Any]:
    """Start the slskd transfer + write the download row (exactly like the manual flow),
    then ensure the monitor is running.

    Returns ``{ok, error}``. The error matters: the caller uses it to tell the
    user (and the wishlist row) apart a client that refused this release from a
    search that found nothing — they used to read identically."""
    from api.video import get_video_db
    from core.video import disk_guard, organization
    from core.video.download_monitor import ensure_started
    from core.video.slskd_search import build_query
    name = display_name(item, media_type)
    room = disk_guard.check_room(target_dir, organization.load(get_video_db()))
    if not room["ok"]:
        msg = disk_guard.shortfall_message(room, target_dir)
        logger.warning("disk guard: %s — skipping grab of %s", msg, name)
        return {"ok": False, "error": msg}
    source = str(best.get("source") or "soulseek").lower()
    transport_source = "torrent" if source == "extto" else source
    if transport_source == "soulseek":
        from core.video.slskd_download import start_download
        started = start_download(best.get("username"), best.get("filename"), best.get("size_bytes") or 0)
        if not started.get("ok"):
            return {"ok": False, "error": started.get("error") or "Soulseek refused the transfer"}
    else:
        # torrent / usenet — hand off to the shared client; carry the returned ref into the row.
        from core.video.client_grab import grab
        res = grab(transport_source, best.get("download_url"),
                   fallback_magnet=best.get("magnet_uri"))
        if not res.get("ok"):
            logger.warning("video hybrid: %s grab refused for %s: %s", source, name, res.get("error"))
            return {"ok": False, "error": res.get("error") or "the download client refused it"}
        best = {**best, "_client_ref": res["ref"]}
    ctx = search_context(item, media_type)
    query = build_query(ctx["scope"], ctx["title"], year=ctx.get("year"),
                        season=ctx.get("season"), episode=ctx.get("episode"))
    get_video_db().add_video_download(
        build_download_record(item, best, candidates, media_type=media_type,
                              target_dir=target_dir, query=query))
    ensure_started(get_video_db)
    return {"ok": True, "error": None}


# ── guard: keep an in-progress drain from overlapping the next tick ───────────
_running: Dict[str, bool] = {"movie": False, "episode": False}


def _wishlist_db_key(item: Dict[str, Any], media_type: str) -> tuple:
    tmdb = item.get('tmdb_id') or item.get('show_tmdb_id')
    kind = 'movie' if media_type == 'movie' else 'episode'
    return kind, tmdb, item.get('season_number'), item.get('episode_number')


def _default_record_outcome(item: Dict[str, Any], media_type: str, grabbed: bool,
                            refusal: Optional[Dict[str, Any]] = None) -> None:
    """Persist the drain search outcome on the wishlist row (#liveleak-failing-hub):
    a grab resets search_attempts, a fruitless search increments it. Callers skip
    this entirely when the search never ran (slskd down) — that's not evidence
    the release doesn't exist. Best-effort: a db hiccup never breaks the drain.

    ``refusal`` is the receipt from :func:`core.video.wishlist_evidence.
    summarize_refusals` — the best release that WAS found and the rule that turned
    it down. Without it a row on its fortieth fruitless search is indistinguishable
    from one waiting for next week's episode."""
    try:
        from api.video import get_video_db
        from core.video.wishlist_evidence import refusal_line
        kind, tmdb, season, episode = _wishlist_db_key(item, media_type)
        if not tmdb:
            return
        get_video_db().record_wishlist_search_outcome(
            kind, tmdb, grabbed,
            season_number=season,
            episode_number=episode,
            refusal=refusal_line(refusal),
            refusal_quality=(refusal or {}).get('quality_label'),
            snapshot=item.get("_source_snapshot"))
    except Exception:   # noqa: BLE001 - visibility must never break acquisition
        logger.debug("record_wishlist_search_outcome failed", exc_info=True)


def _default_record_note(item: Dict[str, Any], media_type: str, note: str,
                         refusal_quality: Optional[str] = None) -> None:
    """Persist a non-fruitless reason on the wishlist row. Used when a release
    passed matching/profile gates but the download/import side could not land it."""
    try:
        from api.video import get_video_db
        kind, tmdb, season, episode = _wishlist_db_key(item, media_type)
        if not tmdb:
            return
        get_video_db().record_wishlist_search_note(
            kind, tmdb, note, season_number=season, episode_number=episode,
            refusal_quality=refusal_quality)
    except Exception:   # noqa: BLE001 - visibility must never break acquisition
        logger.debug("record_wishlist_search_note failed", exc_info=True)


def is_running(media_type: str) -> bool:
    return bool(_running.get(media_type))


def auto_video_process_wishlist(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    media_type: str = "movie",
    fetch_items: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
    active_keys: Optional[Callable[[str], Iterable]] = None,
    target_dir: Optional[Callable[[str], str]] = None,
    search: Optional[Callable[[Dict[str, Any], str], List[Dict[str, Any]]]] = None,
    enqueue: Optional[Callable[..., bool]] = None,
    record_outcome: Optional[Callable[[Dict[str, Any], str, bool], None]] = None,
    record_note: Optional[Callable[[Dict[str, Any], str, str, Optional[str]], None]] = None,
) -> Dict[str, Any]:
    """Auto-grab the wished movies (or episodes): search Soulseek, pick the best release,
    enqueue. Processes the whole eligible wishlist, a few searches at a time.

    Returns ``{'status': 'completed', 'searched': int, 'grabbed': int, ...}``."""
    fetch_items = fetch_items or _default_fetch_items
    active_keys = active_keys or _default_active_keys
    target_dir = target_dir or _default_target_dir
    search = search or _default_search
    enqueue = enqueue or _default_enqueue
    record_outcome = record_outcome or _default_record_outcome
    record_note = record_note or _default_record_note
    automation_id = config.get('_automation_id')
    concurrency = max(1, int(config.get('max_concurrent', 3) or 3))
    label = 'movie' if media_type == 'movie' else 'episode'

    _running[media_type] = True
    try:
        root = target_dir(media_type)
        if not root:
            where = 'Movie' if media_type == 'movie' else 'TV'
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='%s library folder not set — skipping (Settings → Downloads)' % where,
                                 log_type='info')
            return {'status': 'completed', 'searched': 0, 'grabbed': 0,
                    'skipped': 'no_folder', '_manages_own_progress': True}

        deps.update_progress(automation_id, phase='Checking the wishlist…', progress=5,
                             log_line='Looking for wished %ss to grab' % label, log_type='info')
        if media_type == 'movie':
            _backfill_movie_available_dates()   # resolve downloadable dates so the gate can skip cinema-only films
        items = fetch_items(media_type) or []
        # Upgrade-until-cutoff: owned rows are judged against the profile cutoff
        # (skip when met; strictly-better-only when below). Only loaded when an
        # owned row is actually present — the common all-new case stays DB-free.
        if any(it.get("owned") for it in items):
            try:
                cutoff_rank = _default_cutoff_rank()
            except Exception:   # noqa: BLE001 - no profile → treat as no cutoff
                cutoff_rank = 0
            # per-title profiles: judge each owned item against ITS profile's
            # cutoff when any assignment exists (the common no-assignment case
            # stays on the single global read)
            per_item = _cutoff_rank_for_item if any(
                it.get("quality_profile_id") for it in items) else None
            items = annotate_upgrades(items, cutoff_rank, cutoff_for=per_item)
        active = set(active_keys(media_type) or set())
        todo = [it for it in items if item_key(it, media_type) not in active]
        if media_type == "episode":
            # Global setting, overridable per show: 'never' keeps that show on the
            # per-episode path even when packs are on, 'prefer' reaches for a pack
            # even when they are globally off.
            pack_on = config.get("season_pack_first", True) is not False
            packable = [it for it in todo
                        if _pack_preference_for(it) == "prefer"
                        or (pack_on and _pack_preference_for(it) != "never")]
        else:
            packable = []
        if packable:
            packs = season_pack_requests(packable, active, config.get("season_pack_min_missing", MIN_SEASON_PACK_EPISODES))
            covered = {("episode", str(p.get("show_tmdb_id")), int(s), int(e))
                       for p in packs for s, e in (p.get("_pack_members") or [])}
            todo = packs + [it for it in todo if item_key(it, media_type) not in covered]
        if not todo:
            deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                                 log_line='Nothing new to grab (%d already in flight)' % len(active),
                                 log_type='info')
            return {'status': 'completed', 'searched': 0, 'grabbed': 0, '_manages_own_progress': True}

        grabbed = [0]
        searched = [0]
        noresults = [0]    # search came back empty (the source had nothing)
        rejected = [0]     # source had hits, but none passed the quality profile
        notrun = [0]       # the search never ran (slskd didn't accept it)
        refused = [0]      # releases passed the profile; the download client said no
        total = len(todo)
        lock = threading.Lock()

        def _one(it):
            found = search(it, media_type)
            # the seam returns (candidates, error); tolerate a bare list/None too (test fakes)
            cands, err = found if isinstance(found, tuple) else (found, None)
            didnt_run = cands is None       # slskd not configured / errored / rate-limited
            cands = cands or []
            # Offer the client more than one release. Abandoning the item the
            # moment its top pick is refused is what let a single bad release
            # block an item indefinitely — the next-best one is usually fine.
            usable_all = acceptable_candidates(cands, it.get("_min_rank") or 0)
            if it.get("_pack_members"):
                usable_all = [c for c in usable_all if str(c.get("source") or "").lower() not in ("", "soulseek")]
            cooled = [(c, source_cooldown_remaining(it, media_type, c)) for c in usable_all]
            usable = [c for c, remaining in cooled if remaining <= 0]
            cooldown_wait = max([remaining for _c, remaining in cooled], default=0)
            ok, refusal = False, None
            for cand in usable[:MAX_GRAB_ATTEMPTS]:
                out = enqueue_outcome(enqueue(it, cand, cands, media_type, root))
                if out["ok"]:
                    clear_source_refusal(it, media_type, cand)
                    ok = True
                    break
                note_source_refusal(it, media_type, cand)
                refusal = out["error"] or refusal
            name = display_name(it, media_type)
            # tell apart: grabbed / search-didn't-run / source-empty / hits-but-all-rejected.
            # `err` alongside RESULTS is a non-fatal note (a chain source was skipped,
            # e.g. 'torrent skipped — Prowlarr not configured') — always show it, or a
            # mis-configured first source silently degrades every search.
            if ok:
                msg, lt = "Grabbed '%s'" % name, 'success'
            elif didnt_run:
                msg = ("Search didn't run for '%s' — %s" % (name, err)) if err \
                    else ("Search didn't run for '%s' — slskd not responding?" % name)
                lt = 'warning'
            elif usable_all and not usable:
                hours = max(1, int((cooldown_wait + 3599) // 3600))
                msg = ("Found release(s) for '%s', but that source is cooling down "
                       "after repeated client refusals — retrying in about %dh" % (name, hours))
                record_note(it, media_type, "Source cooldown after repeated client refusals")
                lt = 'warning'
            elif usable:
                # Releases PASSED the profile and the download client turned them
                # down. Saying "none met your quality profile" here (which is what
                # this branch used to fall through to) points the user at the one
                # setting that is not the problem, and leaves the real reason —
                # sitting right there in the client's own error — unsaid.
                reason = refusal or "no reason given"
                msg = ("Found %d release(s) for '%s' but the download client "
                       "refused %s — %s" % (len(usable), name,
                                            "them" if len(usable) > 1 else "it",
                                            reason))
                record_note(it, media_type, "Download client refused: " + str(reason),
                            usable[0].get("quality_label"))
                lt = 'warning'
            elif not cands:
                msg, lt = "No search results for '%s'" % name, 'info'
                if err:
                    msg, lt = msg + " · " + str(err), 'warning'
            elif it.get('_min_rank'):
                msg = ("%d result(s) for '%s', none better than your current copy — "
                       "still watching for an upgrade" % (len(cands), name))
                lt = 'info'
            else:
                why = (cands[0].get('rejected') or 'none met your quality profile')
                msg, lt = "%d result(s) for '%s', none accepted — %s" % (len(cands), name, why), 'info'
                if err:
                    msg, lt = msg + " · " + str(err), 'warning'
            # Failing visibility (#liveleak-failing-hub): record the outcome on
            # the wishlist row — but only when the search actually RAN, and only
            # when its verdict says something about whether the release EXISTS.
            # A search that never ran (slskd down / rate-limited) says nothing;
            # neither does a client refusing a release we did find, and counting
            # that as a fruitless search is how a row reached 133 "attempts"
            # while its release was available the whole time.
            if not didnt_run and not (usable and not ok):
                # Keep the receipt. Every candidate was just judged and is about to
                # be discarded; the best one refused for a reason about
                # AVAILABILITY (not "wrong season", which is only search noise) is
                # what turns "why is this stuck" into a decision the user can make.
                from core.video.wishlist_evidence import summarize_search
                record_outcome(it, media_type, ok, summarize_search(
                    cands, noun="movie" if media_type == "movie" else "episode"))
            with lock:
                searched[0] += 1
                if ok:
                    grabbed[0] += 1
                elif didnt_run:
                    notrun[0] += 1
                elif usable_all:
                    refused[0] += 1
                elif not cands:
                    noresults[0] += 1
                else:
                    rejected[0] += 1
                deps.update_progress(
                    automation_id, phase='Searching + grabbing…',
                    progress=10 + int(85 * searched[0] / max(total, 1)),
                    log_line=msg, log_type=lt)

        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            list(ex.map(_one, todo))

        # Headline with the WHY breakdown: it's the difference between "the source has
        # nothing" (noresults) and "it has stuff but your quality profile rejects it" (rejected).
        tail = []
        if notrun[0]:
            tail.append("%d search(es) didn't run (slskd?)" % notrun[0])
        if noresults[0]:
            tail.append('%d had no results' % noresults[0])
        if rejected[0]:
            tail.append('%d rejected on quality' % rejected[0])
        if refused[0]:
            tail.append('%d refused by the download client' % refused[0])
        breakdown = (' · ' + ', '.join(tail)) if tail else ''
        done = ('Grabbed %d %s(s) of %d searched%s' % (grabbed[0], label, searched[0], breakdown)) if grabbed[0] \
            else ('Searched %d %s(s), grabbed 0%s' % (searched[0], label, breakdown))
        deps.update_progress(automation_id, status='finished', progress=100, phase='Complete',
                             log_line=done, log_type='success' if grabbed[0] else 'info')
        return {'status': 'completed', 'searched': searched[0], 'grabbed': grabbed[0],
                'noresults': noresults[0], 'rejected': rejected[0], 'notrun': notrun[0],
                'refused': refused[0], '_manages_own_progress': True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status='error', phase='Error', log_line=str(e), log_type='error')
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}
    finally:
        _running[media_type] = False
