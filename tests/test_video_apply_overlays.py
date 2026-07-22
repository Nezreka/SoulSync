"""Daily overlay-apply automation: reads the per-scope overlay settings and
re-applies only ENABLED scopes (the guard), skipping unchanged items. Pure
handler with the DB + apply function injected."""

from __future__ import annotations

from core.automation.handlers.video_apply_overlays import auto_video_apply_overlays


class _Deps:
    def __init__(self):
        self.progress = []

    def update_progress(self, automation_id, **kw):
        self.progress.append(kw)


def _logs(deps):
    return " ".join(p.get("log_line") or "" for p in deps.progress)


class _DB:
    def __init__(self, assignments):
        self._a = assignments

    def get_overlay_assignments(self):
        return self._a


def test_applies_only_enabled_scopes():
    # movie + episode enabled; show has no template; season disabled
    db = _DB({
        "movie": {"enabled": True, "template_id": 1},
        "show": {"enabled": True, "template_id": None},
        "season": {"enabled": False, "template_id": 2},
        "episode": {"enabled": True, "template_id": 3},
    })
    called = {}

    def apply_scopes(dbx, scopes, on_progress):
        called["scopes"] = scopes
        on_progress({"done": 5, "total": 10, "applied": 2, "skipped": 3, "title": "X"})
        return {"ok": True, "total": 10, "applied": 2, "skipped": 8, "failed": 0}

    deps = _Deps()
    res = auto_video_apply_overlays({"_automation_id": "a"}, deps, db=db, apply_scopes=apply_scopes)
    assert called["scopes"] == ["movie", "episode"]        # only enabled + templated scopes
    assert res["status"] == "completed" and res["applied"] == 2 and res["skipped"] == 8
    assert "Updated 2, 8 unchanged" in _logs(deps)


def test_no_enabled_scopes_is_a_clean_noop():
    db = _DB({"movie": {"enabled": False, "template_id": 1}})
    called = {"ran": False}

    def apply_scopes(dbx, scopes, on_progress):
        called["ran"] = True
        return {"ok": True}

    deps = _Deps()
    res = auto_video_apply_overlays({"_automation_id": "a"}, deps, db=db, apply_scopes=apply_scopes)
    assert called["ran"] is False                          # never even starts a run
    assert res["status"] == "completed" and res["scopes"] == []
    assert "nothing to update" in _logs(deps)


def test_apply_in_progress_surfaces_as_error():
    db = _DB({"movie": {"enabled": True, "template_id": 1}})
    deps = _Deps()
    res = auto_video_apply_overlays(
        {"_automation_id": "a"}, deps, db=db,
        apply_scopes=lambda d, s, p: {"ok": False, "error": "an overlay run is already in progress"})
    assert res["status"] == "error" and "already in progress" in res["error"]


def test_failed_items_are_reported():
    db = _DB({"movie": {"enabled": True, "template_id": 1}})
    deps = _Deps()
    res = auto_video_apply_overlays(
        {"_automation_id": "a"}, deps, db=db,
        apply_scopes=lambda d, s, p: {"ok": True, "total": 3, "applied": 1, "skipped": 1, "failed": 1})
    assert res["status"] == "completed" and res["failed"] == 1
    assert "1 failed" in _logs(deps)
