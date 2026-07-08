"""Automation handler: ``video_clean_plex_images`` action.

Reclaims the Plex space that overlay re-uploads accumulate. Plex keeps every
uploaded poster in its bundles, so this runs Plex's own maintenance ops (Empty
Trash → Clean Bundles → Optimize DB) — the API-only path, mirroring Kometa's
ImageMaid. Best scheduled AFTER the overlay-apply run, never concurrently.

The cleanup fn is injected, so the handler is a pure function tests drive with a
fake.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.automation.deps import AutomationDeps

_LABELS = {"empty_trash": "Emptying trash", "clean_bundles": "Cleaning bundles", "optimize": "Optimizing database"}


def _default_cleanup(on_step):
    from core.video.overlays import cleanup
    return cleanup.run_cleanup(on_step=on_step)


def auto_video_clean_plex_images(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    cleanup: Optional[Callable[[Callable], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run Plex's maintenance ops. Returns {'status', 'done', 'failed', ...}."""
    automation_id = config.get("_automation_id")
    cleanup = cleanup or _default_cleanup
    try:
        deps.update_progress(automation_id, phase="Cleaning up Plex images…", progress=5,
                             log_line="Reclaiming space from accumulated overlay posters", log_type="info")

        def _step(key, label):
            deps.update_progress(automation_id, phase=_LABELS.get(key, "Cleaning up…"), progress=40,
                                 log_line=label + "…", log_type="info")

        res = cleanup(_step) or {}
        if not res.get("done") and res.get("error"):
            deps.update_progress(automation_id, status="finished", progress=100, phase="Skipped",
                                 log_line=res["error"], log_type="info")
            return {"status": "completed", "done": [], "failed": [], "skipped": True, "_manages_own_progress": True}
        done, failed = res.get("done", []), res.get("failed", [])
        msg = "Cleaned: " + (", ".join(done) or "nothing") + (" · failed: " + ", ".join(failed) if failed else "")
        deps.update_progress(automation_id, status="finished", progress=100, phase="Complete",
                             log_line=msg, log_type="success" if not failed else "warning")
        return {"status": "completed", "done": done, "failed": failed, "_manages_own_progress": True}
    except Exception as e:  # noqa: BLE001
        deps.update_progress(automation_id, status="error", phase="Error", log_line=str(e), log_type="error")
        return {"status": "error", "error": str(e), "_manages_own_progress": True}
