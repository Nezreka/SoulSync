"""OverlayApplier — the per-item apply/remove flow, with I/O injected.

Proves the anti-stacking contract (always composite from the stored clean base,
first-touch backup taken once), idempotent skips, force, re-render on data change,
and restore-on-remove — all without a live server.
"""

from __future__ import annotations

import io

import pytest

from PIL import Image

from core.video.overlays.apply import OverlayApplier, values_signature
from core.video.overlays.assets import AssetStore
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _poster(color=(10, 10, 10)):
    b = io.BytesIO(); Image.new("RGB", (300, 450), color).save(b, format="JPEG"); return b.getvalue()


BADGE_TPL = {"id": 7, "definition": {"layers": [
    {"type": "text", "binding": {"field": "resolution"}, "anchor": "center", "x": 0.5, "y": 0.5,
     "size": 0.12, "color": "#ffffff"}]}}


class _Harness:
    def __init__(self, db, store):
        self.pushes = []          # (kind, id, bytes, delete_key)
        self.fetches = 0
        self._n = 0
        self.applier = OverlayApplier(db, store, fetch_base=self._fetch, push_poster=self._push)

    def _fetch(self, kind, item_id):
        self.fetches += 1
        return _poster((40, 40, 40))

    def _push(self, kind, item_id, jpeg, delete_key=None):
        # Simulate Plex handing back a fresh poster key on each upload.
        self.pushes.append((kind, item_id, jpeg, delete_key))
        self._n += 1
        return "upload://k%d" % self._n


def test_first_apply_stashes_base_and_backup_then_pushes(db, tmp_path):
    store = AssetStore(tmp_path / "a")
    h = _Harness(db, store)
    res = h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"})
    assert res["ok"] and res["pushed"] and res["bytes"] > 0
    assert h.fetches == 1                          # grabbed the current poster once
    assert store.has_base("movie", 5)              # …and kept it as the clean base
    assert store.read_backup("movie", 5) is not None   # first-touch backup taken
    assert len(h.pushes) == 1
    row = db.get_overlay_apply("movie", 5)
    assert row["template_id"] == 7 and row["base_sha"] and row["values_sig"]


def test_reapply_unchanged_is_skipped(db, tmp_path):
    store = AssetStore(tmp_path / "a")
    h = _Harness(db, store)
    h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"})
    res = h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"})
    assert res.get("skipped") == "unchanged"
    assert len(h.pushes) == 1                       # no second push
    assert h.fetches == 1                           # base reused from the store (not re-fetched)


def test_data_change_rerenders_from_clean_base(db, tmp_path):
    store = AssetStore(tmp_path / "a")
    h = _Harness(db, store)
    h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"})
    base_after_first = store.read_base("movie", 5)
    res = h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "1080p"})
    assert res["ok"] and "skipped" not in res       # resolution changed → re-applied
    assert len(h.pushes) == 2
    # the base we composite onto is unchanged (no stacking of the old badge)
    assert store.read_base("movie", 5) == base_after_first


def test_force_reapplies_even_when_unchanged(db, tmp_path):
    store = AssetStore(tmp_path / "a")
    h = _Harness(db, store)
    h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"})
    res = h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"}, force=True)
    assert "skipped" not in res and len(h.pushes) == 2


def test_first_apply_keeps_original_and_stores_the_new_key(db, tmp_path):
    store = AssetStore(tmp_path / "a")
    h = _Harness(db, store)
    h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"})
    assert h.pushes[0][3] is None                     # first touch: no delete → original kept in Plex
    assert db.get_overlay_apply("movie", 5)["plex_poster_key"] == "upload://k1"


def test_reapply_deletes_the_previous_overlay_before_uploading(db, tmp_path):
    store = AssetStore(tmp_path / "a")
    h = _Harness(db, store)
    h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"})     # → key k1
    h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "1080p"})     # data changed → re-apply
    assert h.pushes[1][3] == "upload://k1"            # hands the prev overlay's key to delete it
    assert db.get_overlay_apply("movie", 5)["plex_poster_key"] == "upload://k2"   # newest stored


def test_remove_hands_the_overlay_key_so_it_gets_deleted(db, tmp_path):
    store = AssetStore(tmp_path / "a")
    h = _Harness(db, store)
    h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"})
    h.applier.remove_item("movie", 5)
    assert h.pushes[-1][3] == "upload://k1"            # restore push drops the overlay upload
    assert db.get_overlay_apply("movie", 5) is None    # ledger cleared


def test_no_base_art_is_an_error(db, tmp_path):
    store = AssetStore(tmp_path / "a")
    applier = OverlayApplier(db, store, fetch_base=lambda k, i: None,
                             push_poster=lambda k, i, b, dk=None: "upload://x")
    res = applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"})
    assert res["ok"] is False and "base" in res["error"]


def test_remove_restores_backup_and_clears_ledger(db, tmp_path):
    store = AssetStore(tmp_path / "a")
    h = _Harness(db, store)
    h.applier.apply_item("movie", 5, BADGE_TPL, {"resolution": "2160p"})
    backup = store.read_backup("movie", 5)
    res = h.applier.remove_item("movie", 5)
    assert res["ok"] and res["restored"]
    assert h.pushes[-1] == ("movie", 5, backup, "upload://k1")   # original back + drops the overlay
    assert db.get_overlay_apply("movie", 5) is None


def test_run_apply_batch_reports_and_survives_a_bad_item(db, tmp_path):
    from core.video.overlays.apply import run_apply
    store = AssetStore(tmp_path / "a")
    h = _Harness(db, store)
    progress = []
    jobs = [
        {"kind": "movie", "item_id": 1, "template": BADGE_TPL, "values": {"resolution": "2160p"}, "title": "A"},
        {"kind": "movie", "item_id": 2, "template": {"id": 7, "definition": None}, "values": {}, "title": "B"},
        {"kind": "movie", "item_id": 3, "template": BADGE_TPL, "values": {"resolution": "1080p"}, "title": "C"},
    ]
    summary = run_apply(h.applier, jobs, on_progress=progress.append)
    assert summary["total"] == 3 and summary["applied"] == 3   # all three composite fine
    assert progress[-1]["done"] == 3


def test_values_signature_only_tracks_used_fields():
    tpl = {"layers": [{"type": "text", "binding": {"field": "resolution"}}]}
    a = values_signature(tpl, {"resolution": "2160p", "imdb": 8.4})
    b = values_signature(tpl, {"resolution": "2160p", "imdb": 1.0})   # unused field changed
    c = values_signature(tpl, {"resolution": "1080p"})               # used field changed
    assert a == b and a != c


def test_render_version_invalidates_every_cached_render(monkeypatch):
    import core.video.overlays.apply as ap
    tpl = {"layers": [{"type": "text", "binding": {"field": "resolution"}}]}
    vals = {"resolution": "2160p"}
    before = values_signature(tpl, vals)
    monkeypatch.setattr(ap, "_RENDER_VERSION", ap._RENDER_VERSION + 1)  # a compositor change
    after = values_signature(tpl, vals)
    assert before != after   # same inputs, new signature → item re-renders once


def test_template_restyle_changes_signature_but_value_noise_does_not():
    # A restyle (same consumed fields, different look) must re-render; an edit
    # to a value the template doesn't consume must still skip.
    from core.video.overlays.apply import values_signature
    d1 = {"layers": [{"type": "text", "field": "resolution", "color": "#fff"}]}
    d2 = {"layers": [{"type": "text", "field": "resolution", "color": "#f00"}]}   # restyle
    v = {"resolution": "2160p", "title": "A"}
    assert values_signature(d1, v) != values_signature(d2, v)
    assert values_signature(d1, v) == values_signature(d1, dict(v, title="B"))
