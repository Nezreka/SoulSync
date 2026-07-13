"""Sync a resolved collection to the active media server (Plex Collection /
Jellyfin BoxSet).

SoulSync resolves membership itself and pushes an explicit member list, so this
is server-agnostic: it drives a duck-typed ``source`` (see the collection methods
on ``PlexVideoSource`` / ``JellyfinVideoSource`` in ``core/video/sources.py``).
The source is INJECTED, so the whole diff/sync-mode/ledger/adopt orchestration is
unit-testable with a fake in-memory server.

Behavior:
  * Resolve the definition to its owned member ``server_id`` set.
  * Skip (ledger-gated) when the resolved members + settings are unchanged and the
    collection still exists on the server.
  * First sync: adopt an existing same-name collection if present, else create one.
  * sync_mode 'sync' adds missing AND removes stale members; 'append' only adds.
  * Never touches a collection we don't manage beyond the one matched by name/ledger.
  * Set poster / summary / sort / pin (best-effort per server).

Returns a per-definition result dict; a resolve error or server failure is
reported, never raised, so a batch can skip one and carry on.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from core.video.collections.poster_gen import is_generated_ref, read_poster
from core.video.collections.resolver import ResolvedCollection, resolve_collection
from utils.logging_config import get_logger

logger = get_logger("video.collections.sync")

# Bump to force every collection to re-sync once (e.g. after a push-logic change).
# v2: ranked lists/charts (IMDb Top 250…) now push in rank order instead of the
# server's default release-date sort.
_SYNC_VERSION = 2

# Remote lists/charts whose OWN order is meaningful — the collection should present in that
# rank on the server (unless the user picks an explicit sort), not by release date.
_RANKED_LIST_SOURCES = {"imdb_chart", "imdb_list", "tmdb_chart", "tmdb_list",
                        "trakt_list", "mdblist_list"}


def _is_ranked_list(definition: Dict[str, Any]) -> bool:
    return ((definition or {}).get("definition") or {}).get("source") in _RANKED_LIST_SOURCES


# The collection DB defaults sort_order to "release", so a ranked list/chart created with
# defaults ends up release-date-sorted on the server — the wrong order for a chart (IMDb Top
# 250 became a 1926→ chronological list). Treat "release" as "unset" for ranked sources so the
# list's own rank wins by default; an explicit alpha / rating / custom choice is still honoured.
_RANK_DEFAULT_SORTS = {None, "", "rank", "release"}


def _ranked_default(definition: Dict[str, Any]) -> bool:
    """True when a ranked list should drive the server order — it IS a ranked list and the user
    hasn't picked a non-default sort (alpha / rating / custom)."""
    return ((definition or {}).get("sort_order") in _RANK_DEFAULT_SORTS
            and _is_ranked_list(definition))


def members_signature(definition: Dict[str, Any], server_ids) -> str:
    """Signature of everything a sync would push: the member set + the settings
    that affect the server object. Unchanged signature → skip the sync. ``server_ids``
    is passed in ORDER (rank order for ranked lists) so a re-rank changes the signature."""
    so = (definition or {}).get("sort_order")
    ids = [str(i) for i in server_ids]
    sub = {
        "_v": _SYNC_VERSION,
        "ids": sorted(set(ids)),
        "name": (definition or {}).get("name"),
        "summary": (definition or {}).get("summary"),
        "sort": so,
        "sync_mode": (definition or {}).get("sync_mode"),
        "pinned": bool((definition or {}).get("pinned")),
        "poster": (definition or {}).get("poster_url"),
        "mode": (definition or {}).get("collection_mode"),
        # ORDER is captured whenever it drives the server object: an explicit custom order, or a
        # ranked list/chart (so re-ranking the source re-syncs + re-orders, not only add/remove).
        "order": ((((definition or {}).get("definition") or {}).get("order")) if so == "custom"
                  else (ids if _ranked_default(definition) else None)),
    }
    return hashlib.sha1(json.dumps(sub, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _dedup(seq) -> List[str]:
    seen, out = set(), []
    for x in seq:
        s = str(x)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ── seasonal windows ─────────────────────────────────────────────────────────
_MD_RE = None   # compiled lazily (module import stays cheap)


def _parse_md(s) -> tuple | None:
    """'MM-DD' → (month, day), or None when absent/invalid."""
    global _MD_RE
    if not s or not isinstance(s, str):
        return None
    if _MD_RE is None:
        import re
        _MD_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
    m = _MD_RE.match(s.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def in_season(definition, today=None) -> bool:
    """Whether a definition's seasonal window covers today (inclusive). No/
    invalid window → always in season. Windows may wrap the year end
    ('12-26' → '01-08'). ``today`` injectable for tests."""
    start = _parse_md((definition or {}).get("window_start"))
    end = _parse_md((definition or {}).get("window_end"))
    if not start or not end:
        return True
    if today is None:
        import datetime
        today = datetime.date.today()
    md = (today.month, today.day)
    if start <= end:
        return start <= md <= end
    return md >= start or md <= end   # wraps the year end


def sync_collection(db, definition: Dict[str, Any], *, source,
                    list_fetcher: Optional[Callable] = None, force: bool = False,
                    poster_generator: Optional[Callable] = None,
                    today=None) -> Dict[str, Any]:
    """Sync one collection definition (a full row from ``get_collection_definition``)
    to ``source``. Returns a result dict with ``ok`` and, on success,
    ``server_id``/``added``/``removed``/``total``/``missing`` (or ``skipped``).
    ``poster_generator(definition, owned) -> poster_url|None`` (injected by the
    live entry points) gives a poster-less collection generated collage art
    before its first push — art is default-on, never a manual chore.
    Seasonal windows: out-of-window, the sync REMOVES our server collection
    (ledger-verified — never a name-matched or foreign object) so seasonal
    shelves appear for the holiday and disappear after it."""
    did = definition.get("id")
    name = (definition.get("name") or "").strip() or "Untitled collection"
    media_type = definition.get("media_type") or "movie"
    kind = media_type   # 'movie' | 'show'
    sync_mode = (definition.get("sync_mode") or "sync").lower()

    if not in_season(definition, today=today):
        removed_server = False
        prev = db.get_collection_sync(did) if did is not None else None
        if (prev and prev.get("server_source") == source.server_name
                and prev.get("server_id")):
            try:
                r = source.delete_collection(str(prev["server_id"]))
                removed_server = bool(r.get("ok"))
            except Exception:   # noqa: BLE001 - off-season removal is best-effort
                logger.debug("out-of-season removal failed for %s", did, exc_info=True)
            if removed_server:
                db.delete_collection_sync(did)
        return {"ok": True, "skipped": "out_of_season", "definition_id": did,
                "name": name, "removed_server": removed_server}

    res: ResolvedCollection = resolve_collection(db, definition, list_fetcher=list_fetcher)
    if not res.ok:
        logger.warning("Collection %s (%s) resolve failed: %s", did, name, res.error)
        return {"ok": False, "definition_id": did, "name": name, "error": res.error}

    # ACQUISITION LIST (Radarr import-list mode): resolve + report missing (the
    # caller wishlists them) but never create/push a server collection. A
    # previously-synced server object is removed once — flipping an existing
    # collection to acquire-only shouldn't leave a stale shelf behind.
    if (definition.get("definition") or {}).get("acquire_only"):
        prev = db.get_collection_sync(did) if did is not None else None
        if (prev and prev.get("server_source") == source.server_name
                and prev.get("server_id")):
            try:
                if source.delete_collection(str(prev["server_id"])).get("ok"):
                    db.delete_collection_sync(did)
            except Exception:   # noqa: BLE001 - stale-shelf removal is best-effort
                logger.debug("acquire-only server removal failed for %s", did, exc_info=True)
        return {"ok": True, "acquire_only": True, "definition_id": did, "name": name,
                "total": len(set(res.server_ids)), "missing": res.missing}

    # Default-on artwork — BEFORE the signature, so the very first sync pushes
    # the art with no signature churn. Best-effort: a failed render just leaves
    # the poster empty and tries again next sync. Adopted collections opt out
    # (keep_server_art): their existing server poster is the user's choice.
    if (poster_generator and did is not None and not definition.get("poster_url")
            and not (definition.get("definition") or {}).get("keep_server_art")):
        try:
            url = poster_generator(definition, res.owned)
        except Exception:   # noqa: BLE001 - art is a nicety, never fail the sync
            logger.debug("auto poster generation failed for %s", did, exc_info=True)
            url = None
        if url:
            definition = dict(definition, poster_url=url)

    desired = _dedup(res.server_ids)      # rank order for ranked lists (resolver preserves it)
    desired_set = set(desired)
    sig = members_signature(definition, desired)   # ORDER matters for ranked lists

    prev = db.get_collection_sync(did) if did is not None else None
    prev_server_id = None
    if prev and prev.get("server_source") == source.server_name and prev.get("server_id"):
        prev_server_id = str(prev.get("server_id"))

    # Confirm the previously-synced collection still exists on the server.
    collection_id: Optional[str] = None
    if prev_server_id is not None:
        try:
            if source.collection_member_ids(prev_server_id) is not None:
                collection_id = prev_server_id
        except Exception:   # noqa: BLE001 - treat a probe error as "gone", re-adopt/create
            collection_id = None

    if not force and collection_id and prev and prev.get("members_sig") == sig:
        return {"ok": True, "skipped": "unchanged", "definition_id": did, "name": name,
                "server_id": collection_id, "total": len(desired_set), "missing": res.missing}

    added = removed = 0
    do_diff = False

    if collection_id is None:
        try:
            found = source.find_collection(kind, name)
        except Exception as e:   # noqa: BLE001
            return {"ok": False, "definition_id": did, "name": name, "error": f"find failed: {e}"}
        if found:
            collection_id = str(found)
            do_diff = True
        elif desired:
            cr = source.create_collection(kind, name, list(desired))
            if not cr.get("ok"):
                return {"ok": False, "definition_id": did, "name": name,
                        "error": cr.get("error") or "create failed"}
            collection_id = str(cr.get("server_id"))
            added = len(desired)
        else:
            # No members yet and nothing to adopt — don't create an empty collection.
            if did is not None:
                db.record_collection_sync(did, server_source=source.server_name,
                                          server_id=None, members_sig=sig, member_count=0)
            return {"ok": True, "definition_id": did, "name": name, "server_id": None,
                    "total": 0, "added": 0, "removed": 0, "empty": True, "missing": res.missing}
    else:
        do_diff = True

    if do_diff:
        try:
            current = set(source.collection_member_ids(collection_id) or [])
        except Exception as e:   # noqa: BLE001
            return {"ok": False, "definition_id": did, "name": name, "error": f"member read failed: {e}"}
        to_add = [i for i in desired if i not in current]
        to_remove = [i for i in current if i not in desired_set] if sync_mode == "sync" else []
        if to_add:
            r = source.collection_add(collection_id, to_add)
            if not r.get("ok"):
                return {"ok": False, "definition_id": did, "name": name,
                        "error": r.get("error") or "add failed"}
            added = len(to_add)
        if to_remove:
            r = source.collection_remove(collection_id, to_remove)
            if not r.get("ok"):
                return {"ok": False, "definition_id": did, "name": name,
                        "error": r.get("error") or "remove failed"}
            removed = len(to_remove)

    # Metadata (best-effort — a failure here doesn't fail the member sync).
    try:
        # A generated poster's poster_url is OUR serve route — the media server
        # can't fetch that relative URL, so push the file bytes instead.
        poster_url = definition.get("poster_url")
        poster_bytes = None
        if is_generated_ref(poster_url):
            poster_bytes = read_poster(did)
            poster_url = None
        # A ranked list/chart with no explicit sort → present it in RANK order (custom),
        # not the server's default release-date sort. An explicit sort choice is honoured.
        ranked_default = _ranked_default(definition)
        effective_sort = "custom" if ranked_default else definition.get("sort_order")
        source.set_collection_meta(
            collection_id,
            poster_url=poster_url,
            poster_bytes=poster_bytes,
            summary=definition.get("summary"),
            sort=effective_sort,
            pinned=bool(definition.get("pinned")),
            mode=definition.get("collection_mode"),
        )
    except Exception:   # noqa: BLE001
        logger.debug("set_collection_meta failed for %s", collection_id, exc_info=True)

    # Member ORDER — best-effort, Plex only (Jellyfin BoxSets have no reorder API). Runs after
    # the member diff so every ordered member exists. Two ordered cases:
    #   • sort_order == 'custom' → the definition's stored order (e.g. the MCU in timeline order)
    #   • a ranked list/chart with no explicit sort → the list's rank (res.owned is already in it)
    if hasattr(source, "collection_reorder"):
        ordered = None
        if definition.get("sort_order") == "custom":
            order = (definition.get("definition") or {}).get("order") or []
            ordered = _ordered_server_ids(order, res.owned)
        elif _ranked_default(definition):
            ordered = _dedup(res.server_ids)      # rank order, deduped
        if ordered:
            try:
                source.collection_reorder(collection_id, ordered)
            except Exception:   # noqa: BLE001 - ordering is presentation, never fail the sync
                logger.debug("collection_reorder failed for %s", collection_id, exc_info=True)

    if did is not None:
        db.record_collection_sync(did, server_source=source.server_name, server_id=collection_id,
                                  members_sig=sig, member_count=len(desired_set))

    return {"ok": True, "definition_id": did, "name": name, "server_id": collection_id,
            "total": len(desired_set), "added": added, "removed": removed, "missing": res.missing}


def _ordered_server_ids(order_tmdb_ids, owned) -> List[str]:
    """Map a custom tmdb-id order onto the owned members' server ids; members
    not in the order list follow after, in resolve order."""
    by_tmdb = {}
    rest = []
    for m in owned or []:
        sid = m.get("server_id")
        if not sid:
            continue
        tid = m.get("tmdb_id")
        if tid is not None:
            by_tmdb[int(tid)] = str(sid)
        rest.append(str(sid))
    head = []
    for tid in order_tmdb_ids or []:
        try:
            sid = by_tmdb.get(int(tid))
        except (TypeError, ValueError):
            continue
        if sid and sid not in head:
            head.append(sid)
    return head + [s for s in rest if s not in set(head)]


def wishlist_missing_movies(db, definition: Dict[str, Any], missing) -> int:
    """For a 'list' MOVIE collection with wishlist_missing on, add the members the
    user doesn't own to the wishlist (idempotent upsert). Franchise/list only —
    smart collections have no 'missing'. Returns how many were added. No-ops
    safely if the db doesn't expose the wishlist method (unit-test fakes)."""
    if not (definition.get("wishlist_missing") and definition.get("kind") == "list"
            and (definition.get("media_type") or "movie") == "movie"):
        return 0
    add = getattr(db, "add_movie_to_wishlist", None)
    if not callable(add):
        return 0
    n = 0
    for m in missing or []:
        tid = m.get("tmdb_id")
        if not tid:
            continue
        try:
            if add(int(tid), m.get("title") or "Untitled", year=m.get("year"),
                   poster_url=m.get("poster_url"), status="wanted"):
                n += 1
        except Exception:   # noqa: BLE001 - one bad wishlist row shouldn't stop the sync
            logger.debug("wishlist add failed for tmdb %s", tid, exc_info=True)
    return n


# Watchlist semantics for TV: a missing MOVIE is a one-shot get (wishlist), a
# missing SHOW is an ongoing thing you FOLLOW (watchlist) — the airing
# automation then wishes its new episodes. Ended shows are skipped: following
# them is meaningless (nothing will air) and the nightly watchlist-prune would
# just remove them again; the missing browser stays the manual route for those.
_TERMINAL_STATUS = {"ended", "canceled", "cancelled", "completed"}
_SHOW_WATCHLIST_CAP = 10      # status lookups per sync run — the rest next pass


def watchlist_missing_shows(db, definition: Dict[str, Any], missing, *,
                            engine=None, cap: int = _SHOW_WATCHLIST_CAP) -> int:
    """For a 'list' SHOW collection with the acquire-missing toggle on, FOLLOW
    the missing shows (idempotent; 'mute' tombstones respected — a muted show is
    never re-followed by automation). Returns shows followed."""
    if not (definition.get("wishlist_missing") and definition.get("kind") == "list"
            and (definition.get("media_type") or "movie") == "show"):
        return 0
    add = getattr(db, "add_to_watchlist", None)
    states_fn = getattr(db, "watchlist_states", None)
    if not callable(add):
        return 0
    if engine is None:
        try:
            from core.video.enrichment.engine import get_video_enrichment_engine
            engine = get_video_enrichment_engine()
        except Exception:   # noqa: BLE001 - no engine → can't status-check; do nothing
            return 0
    if engine is None:
        return 0
    states = states_fn("show") if callable(states_fn) else {}

    added = 0
    checked = 0
    for m in missing or []:
        tid = m.get("tmdb_id")
        if not tid or int(tid) in states:      # follows AND mutes both skip
            continue
        if checked >= cap:
            logger.info("show watchlist cap reached (%d) — remaining missing shows "
                        "follow on the next sync", cap)
            break
        checked += 1
        try:
            detail = engine.tmdb_full_detail("show", int(tid)) or {}
            status = str(detail.get("status") or "").strip().lower()
            if status in _TERMINAL_STATUS:
                continue                        # nothing will air — manual-get territory
            if add("show", int(tid), m.get("title") or "Untitled",
                   poster_url=m.get("poster_url")):
                added += 1
        except Exception:   # noqa: BLE001 - one bad show shouldn't stop the sync
            logger.debug("show watchlist add failed for tmdb %s", tid, exc_info=True)
    return added


def wishlist_missing_members(db, definition: Dict[str, Any], missing) -> int:
    """Feed a list collection's missing members to acquisition — movies to the
    WISHLIST (one-shot gets), shows to the WATCHLIST (follows)."""
    if (definition.get("media_type") or "movie") == "show":
        return watchlist_missing_shows(db, definition, missing)
    return wishlist_missing_movies(db, definition, missing)


def sync_all_collections(db, *, source, list_fetcher: Optional[Callable] = None,
                         force: bool = False, on_progress: Optional[Callable] = None,
                         poster_generator: Optional[Callable] = None) -> Dict[str, Any]:
    """Sync every enabled collection definition. Aggregates per-definition results;
    one failing definition never stops the rest. ``on_progress(done, total, name)``
    is called after each. After a successful sync, a list collection with
    wishlist_missing feeds its unowned members to the wishlist."""
    defs = [d for d in db.list_collection_definitions() if d.get("enabled")]
    total = len(defs)
    results: List[Dict[str, Any]] = []
    wishlisted = 0
    for i, light in enumerate(defs):
        full = db.get_collection_definition(light["id"])
        if not full:
            continue
        try:
            r = sync_collection(db, full, source=source, list_fetcher=list_fetcher,
                                force=force, poster_generator=poster_generator)
            if r.get("ok"):
                wishlisted += wishlist_missing_members(db, full, r.get("missing"))
        except Exception as e:   # noqa: BLE001 - never let one collection kill the batch
            logger.exception("Collection sync crashed for %s", light.get("name"))
            r = {"ok": False, "definition_id": light["id"], "name": light.get("name"), "error": str(e)}
        results.append(r)
        if on_progress:
            try:
                on_progress(i + 1, total, full.get("name"))
            except Exception:   # noqa: BLE001
                pass
    ok = [r for r in results if r.get("ok")]
    return {
        "ok": True,
        "total": total,
        "synced": len(ok),
        "failed": len(results) - len(ok),
        "added": sum(r.get("added", 0) for r in ok),
        "removed": sum(r.get("removed", 0) for r in ok),
        "wishlisted": wishlisted,
        "results": results,
    }


def get_collection_source():
    """The active video server's collection surface, or None when no server is
    configured / the adapter doesn't support collections. Never raises."""
    try:
        from core.video.sources import get_active_video_source
        src = get_active_video_source()
    except Exception:   # noqa: BLE001 - a config/connection hiccup means "no server"
        logger.debug("get_active_video_source failed", exc_info=True)
        return None
    if src is None or not hasattr(src, "create_collection"):
        return None
    return src


def _default_fetcher(db, list_fetcher):
    if list_fetcher is not None:
        return list_fetcher
    try:
        from core.video.collections.list_sources import build_list_fetcher
        return build_list_fetcher(db)
    except Exception:   # noqa: BLE001 - no fetcher → franchise owned still syncs
        logger.debug("could not build list fetcher", exc_info=True)
        return None


def _default_poster_generator(db):
    """The live poster generator for default-on art (skips the re-resolve —
    sync already has the owned members)."""
    def gen(definition, owned):
        from core.video.collections.poster_gen import generate_for_definition
        return generate_for_definition(db, definition, owned=owned)
    return gen


def run_sync(db, *, force: bool = False, list_fetcher: Optional[Callable] = None,
             on_progress: Optional[Callable] = None) -> Dict[str, Any]:
    """Shared entry point for the 'Sync all' action and the daily automation:
    resolve the active server and sync every enabled collection (with the real
    list fetcher + wishlist tie-in by default)."""
    src = get_collection_source()
    if src is None:
        return {"ok": False, "error": "No video server configured (or it can't do collections)"}
    return sync_all_collections(db, source=src, list_fetcher=_default_fetcher(db, list_fetcher),
                                force=force, on_progress=on_progress,
                                poster_generator=_default_poster_generator(db))


def sync_one_now(db, definition_id, *, force: bool = False) -> Dict[str, Any]:
    """Sync a single collection now (the studio's 'Sync now' button): active server
    + real list fetcher + wishlist tie-in."""
    src = get_collection_source()
    if src is None:
        return {"ok": False, "error": "No video server configured for collections"}
    c = db.get_collection_definition(definition_id)
    if not c:
        return {"ok": False, "error": "not found"}
    r = sync_collection(db, c, source=src, list_fetcher=_default_fetcher(db, None), force=force,
                        poster_generator=_default_poster_generator(db))
    if r.get("ok"):
        r["wishlisted"] = wishlist_missing_members(db, c, r.get("missing"))
    return r


__all__ = ["sync_collection", "sync_all_collections", "members_signature",
           "wishlist_missing_movies", "watchlist_missing_shows", "wishlist_missing_members",
           "run_sync", "sync_one_now", "get_collection_source", "in_season"]
