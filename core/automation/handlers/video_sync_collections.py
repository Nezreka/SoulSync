"""Automation handler: ``video_sync_collections`` action.

Daily: resolve every ENABLED SoulSync-managed collection and sync it to the
active video server — add/remove members, set art/summary/sort/pin — skipping
collections whose resolved members + settings are unchanged since the last run.
List/franchise collections with ``wishlist_missing`` feed the members you don't
own to the wishlist.

Guards, so it never "just starts doing stuff":
  * no enabled collections → clean no-op,
  * no configured video server (or one that can't do collections) → reported, not
    a crash,
  * one bad collection never stops the rest (the batch isolates failures).

The DB + sync function are injected seams, so this is a pure function tests drive
with fakes.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.automation.deps import AutomationDeps


def _default_db():
    from api.video import get_video_db
    return get_video_db()


def _default_run(db, on_progress):
    from core.video.collections.sync import run_sync
    return run_sync(db, on_progress=on_progress)


def auto_video_sync_collections(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    db: Any = None,
    run: Optional[Callable[[Any, Callable], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Sync every enabled collection. Returns
    ``{'status': 'completed', 'synced', 'added', 'removed', 'wishlisted', ...}``."""
    automation_id = config.get("_automation_id")
    db = db if db is not None else _default_db()
    run = run or _default_run
    try:
        enabled = [d for d in (db.list_collection_definitions() or []) if d.get("enabled")]
        if not enabled:
            deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                                 log_line="No collections to sync", log_type="info")
            return {"status": "completed", "synced": 0, "_manages_own_progress": True}

        deps.update_progress(automation_id, phase="Syncing collections…", progress=5,
                             log_line="Syncing %d collection(s)…" % len(enabled), log_type="info")

        def _prog(done, total, name):
            pct = 5 + int((done / total) * 90) if total else 5
            deps.update_progress(automation_id, phase="Syncing collections…", progress=pct,
                                 log_line="%s  (%d/%d)" % (name or "…", done, total), log_type="info")

        res = run(db, _prog) or {}
        if not res.get("ok"):
            msg = res.get("error") or "collection sync failed"
            deps.update_progress(automation_id, status="error", phase="Error", log_line=msg, log_type="error")
            return {"status": "error", "error": msg, "_manages_own_progress": True}

        synced, failed = res.get("synced", 0), res.get("failed", 0)
        added, removed, wl = res.get("added", 0), res.get("removed", 0), res.get("wishlisted", 0)
        done = "Synced %d collection(s): +%d / -%d members" % (synced, added, removed)
        if wl:
            done += " · %d wishlisted" % wl
        if failed:
            done += " · %d failed" % failed
        deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                             log_line=done, log_type="success")
        return {"status": "completed", "synced": synced, "failed": failed, "added": added,
                "removed": removed, "wishlisted": wl, "_manages_own_progress": True}
    except Exception as e:  # noqa: BLE001 - one bad run must not crash the engine
        deps.update_progress(automation_id, status="error", phase="Error", log_line=str(e), log_type="error")
        return {"status": "error", "error": str(e), "_manages_own_progress": True}
