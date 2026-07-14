"""Automation handler: ``video_apply_overlays`` action.

Keeps the library's burned-in overlays current, on a daily schedule, the SMART
way: it reads the user's per-scope overlay settings (the movie/show/season/
episode assignments) and applies ONLY the scopes they've enabled. Within each
scope the applier skips every item whose template, base art, and consumed data
are unchanged since the last run — so a nightly pass re-renders just what
actually changed (a new rating, a swapped poster, an edited template), not the
whole library.

Guards, so it never "just starts doing stuff":
  * nothing runs unless a scope is explicitly enabled with a template (the Apply
    dialog's settings) — an unconfigured library is a clean no-op,
  * episodes (tens of thousands of items) only run when their assignment is
    enabled, same as the manual path,
  * a singleton lock (shared with the manual Apply job) means the automation and
    a manual run can't overlap.

The DB + apply function are injected seams, so the handler is a pure function
tests drive with fakes.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.automation.deps import AutomationDeps

_SCOPES = ("movie", "show", "season", "episode")


def _default_db():
    from api.video import get_video_db
    return get_video_db()


def _default_apply(db, scopes, on_progress):
    from core.video.overlays import service
    return service.apply_scopes_sync(db, scopes, on_progress=on_progress)


def auto_video_apply_overlays(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    db: Any = None,
    apply_scopes: Optional[Callable[[Any, list, Callable], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Re-apply overlays for every ENABLED scope, skipping unchanged items.

    Returns ``{'status': 'completed', 'applied', 'skipped', 'failed', 'scopes', ...}``."""
    automation_id = config.get("_automation_id")
    db = db if db is not None else _default_db()
    apply_scopes = apply_scopes or _default_apply
    try:
        assigns = db.get_overlay_assignments() or {}
        scopes = [s for s in _SCOPES
                  if (assigns.get(s) or {}).get("enabled") and (assigns.get(s) or {}).get("template_id")]
        if not scopes:
            deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                                 log_line="No overlay scopes enabled — nothing to update", log_type="info")
            return {"status": "completed", "applied": 0, "skipped": 0, "failed": 0,
                    "scopes": [], "_manages_own_progress": True}

        deps.update_progress(automation_id, phase="Updating overlays…", progress=5,
                             log_line="Applying overlays for: " + ", ".join(scopes), log_type="info")

        def _prog(p):
            total = p.get("total") or 0
            pct = 5 + int((p.get("done", 0) / total) * 90) if total else 5
            deps.update_progress(
                automation_id, phase="Updating overlays…", progress=pct,
                log_line="%s  (%d/%d · %d updated, %d unchanged)" % (
                    p.get("title") or "…", p.get("done", 0), total,
                    p.get("applied", 0), p.get("skipped", 0)),
                log_type="info")

        res = apply_scopes(db, scopes, _prog) or {}
        if not res.get("ok"):
            msg = res.get("error") or "overlay apply failed"
            deps.update_progress(automation_id, status="error", phase="Error", log_line=msg, log_type="error")
            return {"status": "error", "error": msg, "_manages_own_progress": True}

        applied, skipped, failed = res.get("applied", 0), res.get("skipped", 0), res.get("failed", 0)
        done = "Updated %d, %d unchanged" % (applied, skipped) + (" · %d failed" % failed if failed else "")
        deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                             log_line=done, log_type="success")
        return {"status": "completed", "applied": applied, "skipped": skipped, "failed": failed,
                "scopes": scopes, "_manages_own_progress": True}
    except Exception as e:  # noqa: BLE001 - one bad run must not crash the engine
        deps.update_progress(automation_id, status="error", phase="Error", log_line=str(e), log_type="error")
        return {"status": "error", "error": str(e), "_manages_own_progress": True}
