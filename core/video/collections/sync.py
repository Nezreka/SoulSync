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

from core.video.collections.resolver import ResolvedCollection, resolve_collection
from utils.logging_config import get_logger

logger = get_logger("video.collections.sync")

# Bump to force every collection to re-sync once (e.g. after a push-logic change).
_SYNC_VERSION = 1


def members_signature(definition: Dict[str, Any], server_ids) -> str:
    """Signature of everything a sync would push: the member set + the settings
    that affect the server object. Unchanged signature → skip the sync."""
    sub = {
        "_v": _SYNC_VERSION,
        "ids": sorted(str(i) for i in set(server_ids)),
        "name": (definition or {}).get("name"),
        "summary": (definition or {}).get("summary"),
        "sort": (definition or {}).get("sort_order"),
        "sync_mode": (definition or {}).get("sync_mode"),
        "pinned": bool((definition or {}).get("pinned")),
        "poster": (definition or {}).get("poster_url"),
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


def sync_collection(db, definition: Dict[str, Any], *, source,
                    list_fetcher: Optional[Callable] = None, force: bool = False) -> Dict[str, Any]:
    """Sync one collection definition (a full row from ``get_collection_definition``)
    to ``source``. Returns a result dict with ``ok`` and, on success,
    ``server_id``/``added``/``removed``/``total``/``missing`` (or ``skipped``)."""
    did = definition.get("id")
    name = (definition.get("name") or "").strip() or "Untitled collection"
    media_type = definition.get("media_type") or "movie"
    kind = media_type   # 'movie' | 'show'
    sync_mode = (definition.get("sync_mode") or "sync").lower()

    res: ResolvedCollection = resolve_collection(db, definition, list_fetcher=list_fetcher)
    if not res.ok:
        logger.warning("Collection %s (%s) resolve failed: %s", did, name, res.error)
        return {"ok": False, "definition_id": did, "name": name, "error": res.error}

    desired = _dedup(res.server_ids)
    desired_set = set(desired)
    sig = members_signature(definition, desired_set)

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
        source.set_collection_meta(
            collection_id,
            poster_url=definition.get("poster_url"),
            summary=definition.get("summary"),
            sort=definition.get("sort_order"),
            pinned=bool(definition.get("pinned")),
        )
    except Exception:   # noqa: BLE001
        logger.debug("set_collection_meta failed for %s", collection_id, exc_info=True)

    if did is not None:
        db.record_collection_sync(did, server_source=source.server_name, server_id=collection_id,
                                  members_sig=sig, member_count=len(desired_set))

    return {"ok": True, "definition_id": did, "name": name, "server_id": collection_id,
            "total": len(desired_set), "added": added, "removed": removed, "missing": res.missing}


def sync_all_collections(db, *, source, list_fetcher: Optional[Callable] = None,
                         force: bool = False, on_progress: Optional[Callable] = None) -> Dict[str, Any]:
    """Sync every enabled collection definition. Aggregates per-definition results;
    one failing definition never stops the rest. ``on_progress(done, total, name)``
    is called after each."""
    defs = [d for d in db.list_collection_definitions() if d.get("enabled")]
    total = len(defs)
    results: List[Dict[str, Any]] = []
    for i, light in enumerate(defs):
        full = db.get_collection_definition(light["id"])
        if not full:
            continue
        try:
            r = sync_collection(db, full, source=source, list_fetcher=list_fetcher, force=force)
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
        "results": results,
    }


def get_collection_source():
    """The active video server's collection surface, or None when no server is
    configured / the adapter doesn't support collections."""
    from core.video.sources import get_active_video_source
    src = get_active_video_source()
    if src is None or not hasattr(src, "create_collection"):
        return None
    return src


def run_sync(db, *, force: bool = False, list_fetcher: Optional[Callable] = None,
             on_progress: Optional[Callable] = None) -> Dict[str, Any]:
    """Shared entry point for the 'Sync now' action and the daily automation:
    resolve the active server and sync every enabled collection."""
    src = get_collection_source()
    if src is None:
        return {"ok": False, "error": "No video server configured (or it can't do collections)"}
    return sync_all_collections(db, source=src, list_fetcher=list_fetcher,
                                force=force, on_progress=on_progress)


__all__ = ["sync_collection", "sync_all_collections", "members_signature",
           "run_sync", "get_collection_source"]
