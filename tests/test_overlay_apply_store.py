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


def test_thumb_cache_write_read_and_stale_cleanup(tmp_path):
    s = AssetStore(tmp_path / "a")
    assert s.read_thumb(5, "h1") is None
    s.write_thumb(5, "h1", b"IMG1")
    assert s.read_thumb(5, "h1") == b"IMG1"
    s.write_thumb(5, "h2", b"IMG2")                    # new def-hash replaces the old
    assert s.read_thumb(5, "h2") == b"IMG2" and s.read_thumb(5, "h1") is None
    s.write_thumb(6, "h1", b"OTHER")                   # a different template is untouched
    s.clear_thumb(5)
    assert s.read_thumb(5, "h2") is None and s.read_thumb(6, "h1") == b"OTHER"


def test_get_or_render_thumb_caches_and_reuses(db, tmp_path, monkeypatch):
    import core.video.overlays.service as svc
    store = AssetStore(tmp_path / "a")
    renders = []
    monkeypatch.setattr(svc, "preview_thumbnail", lambda d, defn: (renders.append(1), b"RENDERED")[1])
    definition = {"layers": []}
    assert svc.get_or_render_thumb(db, 7, definition, store) == b"RENDERED" and len(renders) == 1
    assert svc.get_or_render_thumb(db, 7, definition, store) == b"RENDERED" and len(renders) == 1   # cache hit
    svc.get_or_render_thumb(db, 7, {"layers": [{"type": "text"}]}, store)                            # def changed → re-render
    assert len(renders) == 2


def test_clear_removes_item(tmp_path):
    s = AssetStore(tmp_path / "assets")
    s.write_base("movie", 1, b"x"); s.ensure_backup("movie", 1, b"y")
    s.clear("movie", 1)
    assert s.has_base("movie", 1) is False and s.read_backup("movie", 1) is None


def test_keyed_by_id_not_path(tmp_path):
    s = AssetStore(tmp_path / "assets")
    s.write_base("movie", 1, b"a"); s.write_base("movie", 2, b"b")
    assert s.read_base("movie", 1) == b"a" and s.read_base("movie", 2) == b"b"


def test_fetch_clean_base_prefers_external_then_tmdb_then_server():
    """Clean-base resolution must never inherit a media-server tool's burn-in
    (Kometa): an external poster URL wins; else the TMDB original; else, only as a
    last resort, the current server poster."""
    from core.video.overlays.service import fetch_clean_base

    class _DB:
        def __init__(self, url, tid):
            self._url, self._tid = url, tid
        def get_art_ref(self, k, i, a):
            return {"poster_url": self._url}
        def item_tmdb_id(self, k, i):
            return self._tid

    seen = []
    ext = lambda u: (seen.append("ext"), b"EXT")[1] if u else None      # noqa: E731
    tmdb = lambda t: (seen.append("tmdb"), b"TMDB")[1]                   # noqa: E731
    server = lambda: (seen.append("server"), b"SRV")[1]                 # noqa: E731

    seen[:] = []
    assert fetch_clean_base(_DB("https://img/x.jpg", 9), "movie", 1, external=ext, tmdb=tmdb, server=server) == b"EXT"
    assert seen == ["ext"]                                              # external short-circuits

    seen[:] = []                                                       # server-path url → skip external, use TMDB
    assert fetch_clean_base(_DB("/library/metadata/9/thumb", 9), "movie", 1, external=ext, tmdb=tmdb, server=server) == b"TMDB"
    assert seen == ["tmdb"]

    seen[:] = []                                                       # no tmdb match → server (last resort)
    assert fetch_clean_base(_DB("/library/x", None), "movie", 1, external=ext, tmdb=tmdb, server=server) == b"SRV"
    assert seen == ["server"]

    seen[:] = []                                                       # external fails → fall through to TMDB
    assert fetch_clean_base(_DB("https://img/x.jpg", 9), "movie", 1, external=lambda u: None, tmdb=tmdb, server=server) == b"TMDB"


def test_reset_item_poster_pushes_clean_and_clears_our_state(tmp_path, db, monkeypatch):
    """Reset re-pushes the clean poster and drops our overlay ledger + base so a
    later apply starts fresh (the un-Kometa path)."""
    import core.video.overlays.service as svc
    store = AssetStore(tmp_path / "a")
    store.write_base("movie", 5, b"stale-base")
    db.record_overlay_apply("movie", 5, template_id=1, base_sha="x", values_sig="y")
    pushes = []
    monkeypatch.setattr(svc, "fetch_clean_base", lambda d, k, i: b"CLEAN")
    monkeypatch.setattr(svc, "push_poster_bytes", lambda d, k, i, b: (pushes.append(b), True)[1])
    res = svc.reset_item_poster(db, "movie", 5, store)
    assert res["ok"] and res["pushed"] and pushes == [b"CLEAN"]
    assert db.get_overlay_apply("movie", 5) is None     # ledger cleared
    assert store.has_base("movie", 5) is False           # base cleared → next apply re-fetches clean


def test_random_overlay_preview_item(db):
    assert db.random_overlay_preview_item() is None            # empty library
    db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 603, "title": "The Matrix",
                             "poster_url": "/p.jpg", "file": {"relative_path": "m.mkv", "size_bytes": 5}})
    db.upsert_movie("plex", {"server_id": "m2", "title": "No TMDB", "poster_url": "/q.jpg",
                             "file": {"relative_path": "n.mkv", "size_bytes": 5}})   # no tmdb_id → excluded
    pick = db.random_overlay_preview_item()
    assert pick and pick["kind"] == "movie" and pick["tmdb_id"] == 603 and pick["title"] == "The Matrix"


def test_preview_thumbnail_renders_on_a_random_title(db, monkeypatch):
    import io
    from PIL import Image
    import core.video.overlays.service as svc
    db.upsert_movie("plex", {"server_id": "m1", "tmdb_id": 603, "title": "The Matrix", "poster_url": "/p.jpg",
                             "file": {"relative_path": "m.mkv", "size_bytes": 5, "resolution": "2160p"}})

    class _Eng:
        def poster_options(self, k, t):
            return [{"thumb": "http://x/t.jpg", "full": "http://x/f.jpg"}]
    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine", lambda: _Eng())
    poster = io.BytesIO(); Image.new("RGB", (200, 300), (10, 10, 10)).save(poster, format="JPEG")
    monkeypatch.setattr(svc, "_fetch_external", lambda u: poster.getvalue())

    definition = {"layers": [{"type": "text", "binding": {"field": "resolution"}, "anchor": "top-right",
                              "x": 0.95, "y": 0.05, "size": 0.08, "color": "#fff",
                              "bg": {"enabled": True, "color": "#000", "opacity": 1, "radius": 0.02, "padX": 0.03, "padY": 0.02}}]}
    data = svc.preview_thumbnail(db, definition)
    assert data and Image.open(io.BytesIO(data)).format == "JPEG"

    monkeypatch.setattr(db, "random_overlay_preview_item", lambda: None)   # no title → caller uses neutral
    assert svc.preview_thumbnail(db, definition) is None


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
