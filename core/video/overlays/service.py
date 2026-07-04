"""Overlay apply — live service glue.

Wires the tested OverlayApplier to the real world: fetching an item's current
poster (as the clean base) and pushing the composited result back to Plex/
Jellyfin, plus a single background job with progress. The applier + batch runner
are unit-tested; this module is the thin, live-only seam (proven against a real
server, like the Poster Manager push).
"""

from __future__ import annotations

import threading

from utils.logging_config import get_logger

from .apply import OverlayApplier, run_apply
from .assets import AssetStore

logger = get_logger("video.overlays.service")


def fetch_poster_bytes(db, kind: str, item_id: int) -> bytes | None:
    """The item's current poster bytes — direct for an http art URL, else via the
    Plex/Jellyfin token (mirrors the poster proxy's fetch)."""
    ref = db.get_art_ref(kind, item_id, "poster")
    if not ref or not ref.get("poster_url"):
        return None
    import requests
    path = ref["poster_url"]
    try:
        if path.startswith("http://") or path.startswith("https://"):
            r = requests.get(path, timeout=20)
            return r.content if r.status_code == 200 else None
        from core.video.sources import video_jellyfin_config, video_plex_config
        source = ref.get("server_source")
        if source == "plex":
            cfg = video_plex_config()
            base, token = cfg.get("base_url"), cfg.get("token")
            if not base or not token:
                return None
            r = requests.get(base.rstrip("/") + path, params={"X-Plex-Token": token}, timeout=20)
        elif source == "jellyfin":
            cfg = video_jellyfin_config()
            base, key = cfg.get("base_url"), cfg.get("api_key")
            if not base:
                return None
            url = base.rstrip("/") + f"/Items/{ref['server_id']}/Images/Primary"
            r = requests.get(url, params=({"api_key": key} if key else {}), timeout=20)
        else:
            return None
        return r.content if r.status_code == 200 else None
    except Exception:
        logger.warning("fetch_poster_bytes failed for %s %s", kind, item_id, exc_info=True)
        return None


def _fetch_external(url):
    """Fetch an http(s) image → bytes (used for clean external/TMDB posters)."""
    if not url:
        return None
    import requests
    try:
        r = requests.get(url, timeout=20)
        return r.content if r.status_code == 200 else None
    except Exception:
        logger.warning("fetch external poster failed: %s", url, exc_info=True)
        return None


def _fetch_tmdb_poster(db, kind: str, tmdb_id) -> bytes | None:
    """The clean TMDB poster for a title — the canonical 'original', untouched by
    whatever a media-server tool (e.g. Kometa) burned onto the local copy."""
    try:
        from core.video.enrichment.engine import get_video_enrichment_engine
        posters = get_video_enrichment_engine().poster_options(kind, tmdb_id) or []
    except Exception:
        logger.warning("tmdb poster lookup failed for %s %s", kind, tmdb_id, exc_info=True)
        return None
    return _fetch_external(posters[0].get("full")) if posters else None


def fetch_clean_base(db, kind: str, item_id: int, *, external=None, tmdb=None, server=None) -> bytes | None:
    """Resolve a CLEAN base poster to composite overlays onto, preferring sources
    that can't carry another tool's burned-in overlays:

      1. an external poster URL on the item — a deliberate choice (Poster Manager /
         enrichment), and clean by construction,
      2. the TMDB original by tmdb_id — bypasses a media-server tool's local burn-in,
      3. the current server poster — last resort (may carry foreign overlays).
    """
    external = external or _fetch_external
    tmdb = tmdb or (lambda t: _fetch_tmdb_poster(db, kind, t))
    server = server or (lambda: fetch_poster_bytes(db, kind, item_id))
    ref = db.get_art_ref(kind, item_id, "poster")
    url = (ref or {}).get("poster_url")
    if url and (str(url).startswith("http://") or str(url).startswith("https://")):
        b = external(url)
        if b:
            return b
    tid = db.item_tmdb_id(kind, item_id)
    if tid:
        b = tmdb(tid)
        if b:
            return b
    return server()


def preview_thumbnail(db, definition: dict) -> bytes | None:
    """Render a template onto a RANDOM real library title's clean TMDB poster with
    that title's real data (representative fallbacks fill any gaps), so a gallery
    card shows the overlay accurately. None if no suitable title → caller falls
    back to the neutral poster."""
    from .compositor import _THUMB_SAMPLE, render_overlay
    pick = db.random_overlay_preview_item()
    if not pick or not pick.get("tmdb_id"):
        return None
    try:
        from core.video.enrichment.engine import get_video_enrichment_engine
        posters = get_video_enrichment_engine().poster_options(pick["kind"], pick["tmdb_id"]) or []
    except Exception:
        posters = []
    if not posters:
        return None
    base = _fetch_external(posters[0].get("thumb") or posters[0].get("full"))
    if not base:
        return None
    sample = dict(_THUMB_SAMPLE)
    for k, v in (db.overlay_sample_data(pick["kind"], pick["id"]) or {}).items():
        if v not in (None, ""):
            sample[k] = v          # real values win; representative defaults fill gaps
    try:
        return render_overlay(base, definition or {}, sample)
    except Exception:
        logger.warning("preview_thumbnail render failed", exc_info=True)
        return None


def push_poster_bytes(db, kind: str, item_id: int, jpeg: bytes) -> bool:
    """Push composited art to the server for an item (best-effort)."""
    from core.video.sources import set_video_poster
    tgt = db.poster_set_target(kind, item_id)
    if not tgt or not tgt.get("server_id"):
        return False
    try:
        return bool(set_video_poster(tgt["server_id"], image_bytes=jpeg, kind=kind).get("ok"))
    except Exception:
        logger.warning("push_poster_bytes failed for %s %s", kind, item_id, exc_info=True)
        return False


class OverlayApplyService:
    def __init__(self, db):
        self.db = db
        self.store = AssetStore.default()

    def applier(self) -> OverlayApplier:
        return OverlayApplier(
            self.db, self.store,
            fetch_base=lambda k, i: fetch_clean_base(self.db, k, i),
            push_poster=lambda k, i, b: push_poster_bytes(self.db, k, i, b))

    def build_jobs(self, scopes, force=False) -> list:
        assigns = self.db.get_overlay_assignments()
        jobs = []
        for scope in scopes:
            a = assigns.get(scope) or {}
            if not a.get("enabled") or not a.get("template_id"):
                continue
            tpl = self.db.get_overlay_template(a["template_id"])
            if not tpl:
                continue
            for it in self.db.overlay_scope_items(scope):
                jobs.append({"kind": scope, "item_id": it["id"], "template": tpl,
                             "values": self.db.overlay_sample_data(scope, it["id"]) or {},
                             "title": it.get("title"), "force": force})
        return jobs

    def build_remove_jobs(self, scopes) -> list:
        jobs = []
        for scope in scopes:
            for it in self.db.overlay_scope_items(scope):
                jobs.append({"kind": scope, "item_id": it["id"], "title": it.get("title")})
        return jobs


# ── single background job with progress ───────────────────────────────────────
_JOB = {"running": False, "phase": "idle", "mode": None,
        "done": 0, "total": 0, "applied": 0, "skipped": 0, "failed": 0, "title": None, "error": None}
_lock = threading.Lock()


def _reset(mode):
    _JOB.update(running=True, phase="starting", mode=mode, done=0, total=0,
                applied=0, skipped=0, failed=0, title=None, error=None)


def reset_item_poster(db, kind, item_id, store=None) -> dict:
    """Restore a title's CLEAN poster (TMDB original / the item's chosen art),
    push it to the server — wiping another tool's burned-in overlays (Kometa) —
    and drop our own overlay ledger + base so a later apply starts fresh."""
    store = store or AssetStore.default()
    clean = fetch_clean_base(db, kind, item_id)
    pushed = bool(clean) and push_poster_bytes(db, kind, item_id, clean)
    db.delete_overlay_apply(kind, item_id)
    store.clear(kind, item_id)
    return {"ok": bool(clean), "pushed": pushed}


def start(db, scopes, *, force=False, remove=False, reset=False) -> bool:
    with _lock:
        if _JOB["running"]:
            return False
        _reset("reset" if reset else ("remove" if remove else "apply"))
    threading.Thread(target=_run, args=(db, scopes, force, remove, reset), daemon=True).start()
    return True


def _run(db, scopes, force, remove, reset=False):
    try:
        svc = OverlayApplyService(db)
        if reset:
            items = [(scope, it) for scope in scopes for it in db.overlay_scope_items(scope)]
            _JOB.update(total=len(items), phase="running")
            applied = failed = 0
            for idx, (scope, it) in enumerate(items):
                try:
                    ok = reset_item_poster(db, scope, it["id"], svc.store).get("ok")
                except Exception:
                    logger.exception("overlay reset failed for %s %s", scope, it.get("id"))
                    ok = False
                applied += 1 if ok else 0
                failed += 0 if ok else 1
                _JOB.update(done=idx + 1, applied=applied, failed=failed, title=it.get("title"))
            _JOB["phase"] = "done"
            return
        jobs = svc.build_remove_jobs(scopes) if remove else svc.build_jobs(scopes, force=force)
        _JOB.update(total=len(jobs), phase="running")
        run_apply(svc.applier(), jobs, on_progress=lambda p: _JOB.update(p), remove=remove)
        _JOB["phase"] = "done"
    except Exception as e:
        logger.exception("overlay apply run failed")
        _JOB.update(phase="error", error=str(e))
    finally:
        _JOB["running"] = False


def status() -> dict:
    return dict(_JOB)
