"""Plex media cleanup — reclaim the space overlay re-uploads accumulate.

Plex keeps every uploaded poster (even byte-identical ones) in its metadata
bundles, so re-applying overlays over time bloats the server. Plex's own
maintenance operations reclaim it, all via the API (no filesystem access):

  * Empty Trash   — drop items flagged as removed,
  * Clean Bundles — remove stale bundle packages, incl. old overlay posters,
  * Optimize DB   — defragment the library database.

This mirrors the safe, API-only path of Kometa's ImageMaid. Plex-only (Jellyfin
manages its own images); the server is an injected seam so it's unit-testable.
"""

from __future__ import annotations

import threading

from utils.logging_config import get_logger

logger = get_logger("video.overlays.cleanup")

# (step key, LibrarySection/Library method name, human label)
_STEPS = (
    ("empty_trash", "emptyTrash", "Emptying trash"),
    ("clean_bundles", "cleanBundles", "Cleaning bundles"),
    ("optimize", "optimize", "Optimizing database"),
)


def _default_server():
    """The active video server's plexapi PlexServer, or None (cleanup is Plex-only)."""
    try:
        from core.video.sources import get_active_video_source
        src = get_active_video_source()
    except Exception:
        return None
    return getattr(src, "_server", None) if src else None


def run_cleanup(*, server=None, on_step=None) -> dict:
    """Run Plex's maintenance ops in order. Best-effort per step (one failing
    doesn't abort the rest). Returns {ok, done:[...], failed:[...]}."""
    server = server if server is not None else _default_server()
    if server is None:
        return {"ok": False, "error": "Cleanup needs a Plex server (none active).", "done": [], "failed": []}
    lib = getattr(server, "library", None)
    if lib is None:
        return {"ok": False, "error": "Plex server has no library API.", "done": [], "failed": []}
    done, failed = [], []
    for key, method, label in _STEPS:
        fn = getattr(lib, method, None)
        if on_step:
            on_step(key, label)
        if fn is None:
            failed.append(key)
            continue
        try:
            fn()
            done.append(key)
        except Exception:
            logger.warning("Plex cleanup step '%s' failed", key, exc_info=True)
            failed.append(key)
    return {"ok": bool(done) and not failed, "done": done, "failed": failed}


# ── single background job with status (for the manual "Clean up now" button) ──
_JOB = {"running": False, "phase": "idle", "step": None, "done": [], "failed": [], "error": None}
_lock = threading.Lock()


def status() -> dict:
    return dict(_JOB)


def start_cleanup() -> bool:
    """Kick a background cleanup; False if one's already running."""
    with _lock:
        if _JOB["running"]:
            return False
        _JOB.update(running=True, phase="running", step=None, done=[], failed=[], error=None)
    threading.Thread(target=_run, daemon=True).start()
    return True


def _run():
    try:
        res = run_cleanup(on_step=lambda key, label: _JOB.update(step=key))
        _JOB.update(done=res.get("done", []), failed=res.get("failed", []),
                    phase="done" if res.get("ok") else "error", error=res.get("error"))
    except Exception as e:
        logger.exception("Plex cleanup run failed")
        _JOB.update(phase="error", error=str(e))
    finally:
        _JOB["running"] = False
