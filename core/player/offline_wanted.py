"""what a device should add and remove: its wishes (sent from the web or
the TV) and smart rules, worked out against the library and this
profile's watch state.

    wanted(db, profile_id, device_id) -> {add: [...], remove: [...]}
    watch_state(db, profile_id, keys)  -> {"type:id": (watched, position_s)}

a show rule keeps the next ``keep_next`` unwatched episodes after the last
one watched, and with delete_watched drops the watched ones. a podcast
rule keeps the newest ``keep_next`` episodes. a playlist rule keeps every
song you can play and, following, drops the ones that left it. something
a wish or rule still wants is never removed for another.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set, Tuple

from core.player import offline_catalog as C
from core.player import offline_store as S
from core.player.playable import Refused
from utils.logging_config import get_logger

logger = get_logger("player.offline")

# what the device holds: anything but a failed download
HELD = ("downloaded", "downloading", "queued", "paused")


# ── watch state ──────────────────────────────────────────────────────────

def _progress_id(item_type: str, item_id: str) -> str:
    """the row the players keep: a podcast episode's id without its podcast"""
    if item_type == "podcast_episode":
        return item_id.partition(":")[2] or item_id
    return item_id


def watch_state(db: Any, profile_id: int, keys: Iterable[Tuple[str, str]]) -> Dict[str, Tuple[bool, float]]:
    """{"type:id": (watched, position seconds)} from the player's own rows
    and, for movies and episodes, the profile's video watch state"""
    by: Dict[str, List[str]] = {}
    for t, i in keys:
        by.setdefault(t, []).append(str(i))
    out: Dict[str, Tuple[bool, float]] = {}
    for t, ids in by.items():
        pids = {_progress_id(t, i): i for i in ids}
        rows = []
        plist = list(pids)
        for n in range(0, len(plist), 500):
            chunk = plist[n:n + 500]
            rows += S._read(
                db, "SELECT item_id, position_seconds, finished FROM watch_progress "  # noqa: S608
                    f"WHERE profile_id = ? AND item_type = ? AND item_id IN ({','.join('?' * len(chunk))})",
                (int(profile_id), t, *chunk))
        for r in rows:
            orig = pids.get(r[0])
            if orig is not None:
                out[C.key(t, orig)] = (bool(r[2]), float(r[1] or 0))
    for t in ("movie", "episode"):
        ids = [int(i) for i in by.get(t, []) if str(i).isdigit()]
        for i in _video_watched(t, ids):
            k = C.key(t, i)
            out[k] = (True, out.get(k, (False, 0.0))[1])
    return out


def _video_watched(kind: str, ids: List[int]) -> Set[int]:
    if not ids:
        return set()
    try:
        from api.video import get_video_db
        from core.video.watch_scope import current_watcher
        rows_for = current_watcher().rows
        db = get_video_db()
        conn = db._get_connection()
        try:
            seen: Set[int] = set()
            for n in range(0, len(ids), 500):
                chunk = ids[n:n + 500]
                marks = ",".join("?" * len(chunk))
                if rows_for is None:
                    table = "movies" if kind == "movie" else "episodes"
                    seen.update(r[0] for r in conn.execute(
                        f"SELECT id FROM {table} WHERE id IN ({marks}) AND COALESCE(play_count, 0) > 0",  # noqa: S608
                        chunk))
                else:
                    seen.update(r[0] for r in conn.execute(
                        f"SELECT item_id FROM video_watch_state WHERE profile_id = ? AND kind = ? "  # noqa: S608
                        f"AND item_id IN ({marks}) AND play_count > 0", (int(rows_for), kind, *chunk)))
            return seen
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - unknown watch state is unwatched
        logger.debug("offline: video watch state unreadable", exc_info=True)
        return set()


# ── wanted ───────────────────────────────────────────────────────────────

def _add_entry(item: Dict[str, Any], quality: str, reason: str) -> Dict[str, Any]:
    return {"item_type": item["item_type"], "item_id": item["item_id"], "quality": quality, "reason": reason,
            "title": item.get("title"), "subtitle": item.get("subtitle"), "artwork": item.get("artwork"),
            "group": item.get("group")}


def _in_group(held_item: Dict[str, Any], gtype: str, gid: str) -> bool:
    g = held_item.get("group") or {}
    if g.get("type") == gtype and str(g.get("id")) == gid:
        return True
    if gtype == "show":
        return str(g.get("show_id") or "") == gid or (g.get("type") == "season"
                                                       and str(g.get("id", "")).partition(":")[0] == gid)
    return False


def _expand(gtype: str, gid: str, count: int = C.PODCAST_DEFAULT) -> List[Dict[str, Any]]:
    try:
        return C.expand(gtype, gid, count=count)[1]
    except Refused:
        return []
    except Exception:  # noqa: BLE001 - one broken group never stops the rest
        logger.warning("offline: couldn't expand %s %s", gtype, gid, exc_info=True)
        return []


def wanted(db: Any, profile_id: int, device_id: str, *, allowed: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    pid = int(profile_id)
    held = {C.key(i["item_type"], i["item_id"]): i for i in S.items(db, pid, device_id) if i["state"] in HELD}
    adds: Dict[str, Dict[str, Any]] = {}
    keep: Set[str] = set()
    removes: Dict[str, Dict[str, Any]] = {}

    def want(item: Dict[str, Any], quality: str, reason: str) -> None:
        k = C.key(item["item_type"], item["item_id"])
        keep.add(k)
        if k not in held and k not in adds:
            adds[k] = _add_entry(item, quality, reason)

    def drop(k: str, reason: str) -> None:
        if k in held and k not in removes:
            i = held[k]
            removes[k] = {"item_type": i["item_type"], "item_id": i["item_id"], "reason": reason}

    protect: Set[str] = set()
    wish_rows = S.wishes(db, pid, device_id)
    for w in wish_rows:
        if w["action"] != "add":
            continue
        items = _expand(w["type"], w["target_id"])
        keys = [C.key(i["item_type"], i["item_id"]) for i in items]
        if w["done"]:
            # done: nothing more to add; what it brought isn't dropped by a
            # playlist moving on. gone once none of it is left
            if not any(k in held for k in keys):
                S.drop_wish(db, w["id"])
            protect.update(keys)
            continue
        # an add is one errand: done once everything in it is on the device,
        # so a later remove of one episode isn't undone by it
        if all(held.get(k, {}).get("state") == "downloaded" for k in keys):
            S.wish_done(db, w["id"])
            protect.update(keys)
            continue
        for item in items:
            want(item, w["quality"], "wish")

    for r in S.rules(db, pid, device_id):
        if r["type"] == "show":
            eps = _expand("show", r["target_id"])
            state = watch_state(db, pid, [(e["item_type"], e["item_id"]) for e in eps])
            seen = [n for n, e in enumerate(eps) if state.get(C.key("episode", e["item_id"]), (False, 0))[0]]
            after = (seen[-1] + 1) if seen else 0
            nxt = [e for e in eps[after:] if not state.get(C.key("episode", e["item_id"]), (False, 0))[0]]
            for e in nxt[:int(r["keep_next"])]:
                want(e, r["quality"], "rule")
            if r["delete_watched"]:
                mine = [i for i in held.values() if i["item_type"] == "episode" and _in_group(i, "show", r["target_id"])]
                done = watch_state(db, pid, [(i["item_type"], i["item_id"]) for i in mine])
                for i in mine:
                    k = C.key(i["item_type"], i["item_id"])
                    if done.get(k, (False, 0))[0]:
                        drop(k, "watched")
        elif r["type"] == "podcast":
            for e in _expand("podcast", r["target_id"], count=int(r["keep_next"])):
                want(e, r["quality"], "rule")
            if r["delete_watched"]:
                mine = [i for i in held.values() if i["item_type"] == "podcast_episode"
                        and i["item_id"].partition(":")[0] == r["target_id"]]
                done = watch_state(db, pid, [(i["item_type"], i["item_id"]) for i in mine])
                for i in mine:
                    k = C.key(i["item_type"], i["item_id"])
                    if done.get(k, (False, 0))[0]:
                        drop(k, "watched")
        elif r["type"] == "playlist":
            tracks = _expand("playlist", r["target_id"])
            for t in tracks:
                want(t, r["quality"], "rule")
            now = [t["item_id"] for t in tracks]
            if r["follow"] and r.get("members_json"):
                import json
                try:
                    before = json.loads(r["members_json"]) or []
                except (TypeError, ValueError):
                    before = []
                for tid in set(before) - set(now):
                    drop(C.key("track", str(tid)), "rule")
            if tracks or not r.get("members_json"):
                S.set_members(db, r["id"], now)

    for w in wish_rows:
        if w["action"] != "remove":
            continue
        if w["type"] in C.ITEM_KINDS:
            targets = {C.key(w["type"], w["target_id"])}
        else:
            targets = {C.key(i["item_type"], i["item_id"]) for i in _expand(w["type"], w["target_id"])}
            targets |= {k for k, i in held.items() if _in_group(i, w["type"], w["target_id"])}
        live = [k for k in targets if k in held]
        if not live:
            S.drop_wish(db, w["id"])
            continue
        for k in live:
            drop(k, "wish")

    # an explicit remove (or a watched one) wins over what a done add
    # brought; a playlist moving on doesn't
    remove = [v for k, v in removes.items()
              if k not in keep and not (v["reason"] == "rule" and k in protect)]
    return {"add": list(adds.values()) if allowed else [], "remove": remove}
