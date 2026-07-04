"""Overlay apply — the base/backup asset store + the assignment/ledger DB layer.

The asset store keeps the clean base + first-touch backup so re-runs never stack
overlays; the DB tracks which template applies to each scope and what we last
burned onto each item.
"""

from __future__ import annotations

import pytest

from core.video.overlays.assets import AssetStore, sha1
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


# ── asset store ───────────────────────────────────────────────────────────────
def test_base_write_read_and_hash(tmp_path):
    s = AssetStore(tmp_path / "assets")
    assert s.has_base("movie", 5) is False
    h = s.write_base("movie", 5, b"clean-poster")
    assert h == sha1(b"clean-poster")
    assert s.has_base("movie", 5) is True
    assert s.read_base("movie", 5) == b"clean-poster"
    # overwriting the base is allowed (new source art)
    s.write_base("movie", 5, b"newer")
    assert s.read_base("movie", 5) == b"newer"


def test_backup_is_first_touch_only(tmp_path):
    s = AssetStore(tmp_path / "assets")
    assert s.ensure_backup("show", 9, b"original-art") is True     # first touch → written
    assert s.ensure_backup("show", 9, b"different") is False       # never overwritten
    assert s.read_backup("show", 9) == b"original-art"


def test_clear_removes_item(tmp_path):
    s = AssetStore(tmp_path / "assets")
    s.write_base("movie", 1, b"x"); s.ensure_backup("movie", 1, b"y")
    s.clear("movie", 1)
    assert s.has_base("movie", 1) is False and s.read_backup("movie", 1) is None


def test_keyed_by_id_not_path(tmp_path):
    s = AssetStore(tmp_path / "assets")
    s.write_base("movie", 1, b"a"); s.write_base("movie", 2, b"b")
    assert s.read_base("movie", 1) == b"a" and s.read_base("movie", 2) == b"b"


def test_upload_is_content_addressed_and_readable(tmp_path):
    s = AssetStore(tmp_path / "assets")
    n1 = s.save_upload(b"logo-bytes", "png")
    n2 = s.save_upload(b"logo-bytes", "png")       # identical → same name (dedup)
    assert n1 == n2 and n1.endswith(".png")
    assert s.read_upload(n1) == b"logo-bytes"
    assert s.read_upload("nope.png") is None
    assert s.read_upload("../../etc/passwd") is None   # traversal guarded to basename


def test_compositor_asset_loader_reads_uploads(tmp_path, monkeypatch):
    import io
    from PIL import Image
    from core.video.overlays import assets as assets_mod
    from core.video.overlays.compositor import render_overlay
    store = AssetStore(tmp_path / "assets")
    logo = io.BytesIO(); Image.new("RGBA", (60, 30), (0, 0, 255, 255)).save(logo, format="PNG")
    name = store.save_upload(logo.getvalue(), "png")
    monkeypatch.setattr(assets_mod.AssetStore, "default", classmethod(lambda cls: store))
    base = io.BytesIO(); Image.new("RGB", (200, 300), (0, 0, 0)).save(base, format="JPEG")
    definition = {"layers": [{"type": "image", "src": "asset://" + name, "anchor": "center",
                              "x": 0.5, "y": 0.5, "w": 0.5, "opacity": 1}]}
    out = render_overlay(base.getvalue(), definition, {})   # default loader resolves asset://
    img = Image.open(io.BytesIO(out)).convert("RGB")
    assert img.getpixel((100, 150))[2] > 200               # blue upload painted centre


# ── assignment ────────────────────────────────────────────────────────────────
def test_assignment_roundtrip(db):
    tid = db.create_overlay_template("My overlay")
    assert db.get_overlay_assignments() == {}
    assert db.set_overlay_assignment("movie", tid, True) is True
    a = db.get_overlay_assignments()
    assert a["movie"]["template_id"] == tid and a["movie"]["enabled"] is True
    assert a["movie"]["template_name"] == "My overlay"
    # upsert flips enabled without duplicating
    db.set_overlay_assignment("movie", tid, False)
    assert db.get_overlay_assignments()["movie"]["enabled"] is False
    assert db.set_overlay_assignment("bogus", tid, True) is False


# ── ledger ────────────────────────────────────────────────────────────────────
def test_apply_ledger_upsert_and_delete(db):
    assert db.get_overlay_apply("movie", 5) is None
    db.record_overlay_apply("movie", 5, template_id=1, base_sha="abc", values_sig="v1")
    row = db.get_overlay_apply("movie", 5)
    assert row["template_id"] == 1 and row["base_sha"] == "abc" and row["values_sig"] == "v1"
    # re-apply updates in place (one row per item)
    db.record_overlay_apply("movie", 5, template_id=2, base_sha="def", values_sig="v2")
    row = db.get_overlay_apply("movie", 5)
    assert row["template_id"] == 2 and row["values_sig"] == "v2"
    assert db.overlay_applied_count() == 1
    assert db.overlay_applied_count(template_id=2) == 1
    assert db.overlay_applied_count(template_id=99) == 0
    assert db.delete_overlay_apply("movie", 5) is True
    assert db.get_overlay_apply("movie", 5) is None
