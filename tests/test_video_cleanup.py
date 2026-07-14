"""Plex media cleanup — runs Plex's maintenance ops (empty trash, clean bundles,
optimize) to reclaim overlay-upload bloat. Engine + automation handler, both with
the server / cleanup fn injected so no live Plex is needed."""

from __future__ import annotations

from core.video.overlays import cleanup as C
from core.automation.handlers.video_clean_plex_images import auto_video_clean_plex_images


class _FakeLib:
    def __init__(self, fail=()):
        self.calls = []
        self._fail = set(fail)

    def _do(self, name):
        self.calls.append(name)
        if name in self._fail:
            raise RuntimeError("boom")

    def emptyTrash(self): self._do("empty_trash")
    def cleanBundles(self): self._do("clean_bundles")
    def optimize(self): self._do("optimize")


class _FakeServer:
    def __init__(self, lib): self.library = lib


# ── engine ────────────────────────────────────────────────────────────────────
def test_run_cleanup_runs_all_ops_in_order():
    lib = _FakeLib()
    steps = []
    res = C.run_cleanup(server=_FakeServer(lib), on_step=lambda k, l: steps.append(k))
    assert res["ok"] is True
    assert res["done"] == ["empty_trash", "clean_bundles", "optimize"] and res["failed"] == []
    assert lib.calls == ["empty_trash", "clean_bundles", "optimize"]   # order matters (bundles before optimize)
    assert steps == ["empty_trash", "clean_bundles", "optimize"]


def test_run_cleanup_one_failing_step_doesnt_abort_the_rest():
    lib = _FakeLib(fail=("clean_bundles",))
    res = C.run_cleanup(server=_FakeServer(lib))
    assert lib.calls == ["empty_trash", "clean_bundles", "optimize"]   # optimize still ran
    assert res["done"] == ["empty_trash", "optimize"] and res["failed"] == ["clean_bundles"]
    assert res["ok"] is False                                          # not clean when a step failed


def test_run_cleanup_no_plex_server_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(C, "_default_server", lambda: None)
    res = C.run_cleanup()
    assert res["ok"] is False and "Plex" in res["error"] and res["done"] == []


# ── automation handler ─────────────────────────────────────────────────────────
class _Deps:
    def __init__(self): self.progress = []
    def update_progress(self, automation_id, **kw): self.progress.append(kw)


def _logs(d): return " ".join(p.get("log_line") or "" for p in d.progress)


def test_handler_reports_cleaned_ops():
    deps = _Deps()
    res = auto_video_clean_plex_images(
        {"_automation_id": "a"}, deps,
        cleanup=lambda on_step: {"ok": True, "done": ["empty_trash", "clean_bundles", "optimize"], "failed": []})
    assert res["status"] == "completed" and res["done"] == ["empty_trash", "clean_bundles", "optimize"]
    assert "Cleaned:" in _logs(deps)


def test_handler_no_server_is_a_clean_skip():
    deps = _Deps()
    res = auto_video_clean_plex_images(
        {"_automation_id": "a"}, deps,
        cleanup=lambda on_step: {"ok": False, "done": [], "failed": [], "error": "Cleanup needs a Plex server (none active)."})
    assert res["status"] == "completed" and res.get("skipped") is True
    assert "needs a Plex server" in _logs(deps)


def test_handler_reports_partial_failure():
    deps = _Deps()
    res = auto_video_clean_plex_images(
        {"_automation_id": "a"}, deps,
        cleanup=lambda on_step: {"ok": False, "done": ["empty_trash"], "failed": ["optimize"]})
    assert res["status"] == "completed" and res["failed"] == ["optimize"]
    assert "failed: optimize" in _logs(deps)
