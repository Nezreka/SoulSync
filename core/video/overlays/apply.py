"""Overlay apply orchestration.

OverlayApplier ties the pieces together for ONE item, non-destructively:

  1. get the CLEAN base (from the store, or fetch the item's current poster once
     and stash it as base + first-touch backup),
  2. render the template onto that clean base (never onto a prior result → no
     stacking),
  3. push the result to the media server (best-effort), and
  4. record what we did so an unchanged re-run is a cheap skip.

All I/O (fetch the base, push to the server) is injected, so the whole flow is
testable without a live Plex/Jellyfin. The live wiring lives in service.py.
"""

from __future__ import annotations

import json

from utils.logging_config import get_logger

from .assets import sha1
from .compositor import render_overlay

logger = get_logger("video.overlays.apply")


def used_fields(definition: dict):
    """The data a template actually reads: the set of bound badge fields, and
    whether any layer uses the title logo."""
    fields, needs_logo = set(), False
    for layer in (definition or {}).get("layers") or []:
        if not isinstance(layer, dict):
            continue
        b = layer.get("binding")
        if b and b.get("field"):
            fields.add(b["field"])
        if layer.get("type") == "image" and layer.get("logo"):
            needs_logo = True
    return fields, needs_logo


# Bump when the COMPOSITOR's output changes for the same inputs (e.g. bundling
# Inter fixed invisible text). It's folded into the signature so every already-
# overlaid item re-renders once on the next apply, then goes back to skipping.
_RENDER_VERSION = 3


def values_signature(definition: dict, values: dict) -> str:
    """A stable signature of only the values a template consumes, so a quality/
    rating change re-renders while unrelated edits don't. Includes a render
    version (a compositor change invalidates every cached render exactly once)
    AND the definition's own hash — a template RESTYLE (same fields, new look)
    must re-render too, which the fields-only signature silently skipped."""
    fields, needs_logo = used_fields(definition)
    sub = {"_rv": _RENDER_VERSION,
           "_tdef": sha1(json.dumps(definition or {}, sort_keys=True, default=str).encode("utf-8"))}
    sub.update({k: (values or {}).get(k) for k in sorted(fields)})
    if needs_logo:
        sub["logo_url"] = (values or {}).get("logo_url")
    return sha1(json.dumps(sub, sort_keys=True, default=str).encode("utf-8"))


class OverlayApplier:
    """Applies/removes overlays for individual items. Inject the DB (ledger), the
    asset store, and the two I/O seams: ``fetch_base(kind, id) -> bytes`` (the
    item's current clean poster) and ``push_poster(kind, id, jpeg) -> bool``."""

    def __init__(self, db, store, *, fetch_base, push_poster, render=render_overlay):
        self.db = db
        self.store = store
        self.fetch_base = fetch_base
        self.push_poster = push_poster
        self.render = render

    def _base_for(self, kind, item_id):
        """Return (base_bytes, base_sha). Uses the stored clean base if present,
        else grabs the current poster once and stashes it as base + backup."""
        base = self.store.read_base(kind, item_id)
        if base is not None:
            return base, sha1(base)
        fresh = self.fetch_base(kind, item_id)
        if not fresh:
            return None, None
        self.store.ensure_backup(kind, item_id, fresh)   # first-touch original
        base_sha = self.store.write_base(kind, item_id, fresh)
        return fresh, base_sha

    def apply_item(self, kind, item_id, template, values, *, force=False) -> dict:
        tdef = template.get("definition") or {}
        tid = template.get("id")
        base, base_sha = self._base_for(kind, item_id)
        if base is None:
            return {"ok": False, "error": "no base artwork"}
        vsig = values_signature(tdef, values)
        prev = self.db.get_overlay_apply(kind, item_id)
        if (not force and prev and prev.get("template_id") == tid
                and prev.get("base_sha") == base_sha and prev.get("values_sig") == vsig):
            return {"ok": True, "skipped": "unchanged"}
        try:
            rendered = self.render(base, tdef, values)
        except Exception as e:
            logger.exception("overlay render failed for %s %s", kind, item_id)
            return {"ok": False, "error": "render failed: %s" % e}
        pushed = False
        try:
            pushed = bool(self.push_poster(kind, item_id, rendered))
        except Exception:
            logger.warning("overlay push failed for %s %s", kind, item_id, exc_info=True)
        self.db.record_overlay_apply(kind, item_id, tid, base_sha, vsig)
        return {"ok": True, "pushed": pushed, "bytes": len(rendered)}

    def remove_item(self, kind, item_id) -> dict:
        """Undo overlays for an item: restore the first-touch backup to the server
        (best-effort) and drop the ledger row."""
        backup = self.store.read_backup(kind, item_id)
        restored = False
        if backup:
            try:
                restored = bool(self.push_poster(kind, item_id, backup))
            except Exception:
                logger.warning("overlay restore failed for %s %s", kind, item_id, exc_info=True)
        self.db.delete_overlay_apply(kind, item_id)
        return {"ok": True, "restored": restored}


def run_apply(applier: OverlayApplier, jobs, on_progress=None, *, remove=False) -> dict:
    """Apply (or remove) a batch of jobs, reporting progress. Each job is
    {kind, item_id, template, values, title?}. One bad item never sinks the run."""
    total = len(jobs)
    applied = skipped = failed = 0
    for i, j in enumerate(jobs):
        try:
            if remove:
                res = applier.remove_item(j["kind"], j["item_id"])
            else:
                res = applier.apply_item(j["kind"], j["item_id"], j["template"], j["values"],
                                         force=j.get("force", False))
        except Exception as e:
            logger.exception("overlay batch item failed: %s", j.get("item_id"))
            res = {"ok": False, "error": str(e)}
        if not res.get("ok"):
            failed += 1
        elif res.get("skipped"):
            skipped += 1
        else:
            applied += 1
        if on_progress:
            on_progress({"done": i + 1, "total": total, "applied": applied,
                         "skipped": skipped, "failed": failed, "title": j.get("title")})
    return {"total": total, "applied": applied, "skipped": skipped, "failed": failed}
