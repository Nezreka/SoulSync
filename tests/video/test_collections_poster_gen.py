"""Generated collection posters: pure collage rendering, generation against a
temp DB with injected fetches, the sync engine's bytes-not-URL push, and the
serve/generate API seam."""

from __future__ import annotations

import io

import pytest
from flask import Flask
from PIL import Image

from core.video.collections import poster_gen
from database.video_database import VideoDatabase


def _jpeg(color, size=(342, 513)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _px(data: bytes, x, y):
    return Image.open(io.BytesIO(data)).convert("RGB").getpixel((x, y))


def _close(a, b, tol=28):
    return all(abs(int(x) - int(y)) <= tol for x, y in zip(a, b))


# ── render_collage layouts (pure) ────────────────────────────────────────────
def test_collage_four_is_2x2_grid():
    data = poster_gen.render_collage(
        [_jpeg((200, 30, 30)), _jpeg((30, 200, 30)), _jpeg((30, 30, 200)), _jpeg((200, 200, 30))],
        "Action")
    img = Image.open(io.BytesIO(data))
    assert img.size == (1000, 1500)
    # Quadrant sample points (upper area, above the title gradient).
    assert _close(_px(data, 250, 200), (200, 30, 30), tol=60)
    assert _close(_px(data, 750, 200), (30, 200, 30), tol=60)


def test_collage_two_split_and_one_full_bleed():
    two = poster_gen.render_collage([_jpeg((200, 30, 30)), _jpeg((30, 30, 200))], "Duo")
    assert _close(_px(two, 250, 300), (200, 30, 30), tol=60)
    assert _close(_px(two, 750, 300), (30, 30, 200), tol=60)
    one = poster_gen.render_collage([_jpeg((30, 200, 30))], "Solo")
    assert _close(_px(one, 250, 300), (30, 200, 30), tol=60)
    assert _close(_px(one, 750, 300), (30, 200, 30), tol=60)


def test_collage_zero_members_renders_gradient_fallback():
    data = poster_gen.render_collage([], "Empty Pack")
    img = Image.open(io.BytesIO(data))
    assert img.size == (1000, 1500)
    # Deterministic per name — same input, same art.
    assert data == poster_gen.render_collage([], "Empty Pack")


def test_collage_survives_bad_image_bytes_and_long_titles():
    data = poster_gen.render_collage(
        [b"not a jpeg", _jpeg((90, 90, 90))],
        "The Extraordinarily Long Collection Name That Must Wrap Or Shrink")
    assert Image.open(io.BytesIO(data)).size == (1000, 1500)


# ── generation against a temp DB ─────────────────────────────────────────────
@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed(db, n=5):
    conn = db._get_connection()
    try:
        for mid in range(1, n + 1):
            conn.execute(
                "INSERT INTO movies (id, server_source, server_id, title, year, rating, has_file) "
                "VALUES (?,?,?,?,?,?,1)",
                (mid, "plex", f"srv{mid}", f"M{mid}", 2000, 9.0 - mid))
            conn.execute("INSERT OR IGNORE INTO genres (name) VALUES ('Action')")
            gid = conn.execute("SELECT id FROM genres WHERE name='Action'").fetchone()[0]
            conn.execute("INSERT INTO movie_genres (movie_id, genre_id) VALUES (?,?)", (mid, gid))
        conn.commit()
    finally:
        conn.close()


def _definition(db):
    cid = db.create_collection_definition(
        "Action", media_type="movie",
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]})
    return db.get_collection_definition(cid)


def test_generate_writes_file_and_updates_poster_url(db, tmp_path):
    _seed(db)
    d = _definition(db)
    fetched = []

    def fetch(media_type, item_id):
        fetched.append((media_type, item_id))
        return _jpeg((120, 60, 60))

    url = poster_gen.generate_for_definition(db, d, fetch=fetch, root=tmp_path / "gen")
    assert url and url.startswith(f"/api/video/collections/{d['id']}/poster?v=")
    assert poster_gen.poster_path(d["id"], tmp_path / "gen").is_file()
    # Highest-rated members fetched, capped at 4.
    assert [i for _, i in fetched] == [1, 2, 3, 4]
    assert db.get_collection_definition(d["id"])["poster_url"] == url
    assert poster_gen.is_generated_ref(url)


def test_generate_survives_failing_fetch_with_gradient(db, tmp_path):
    _seed(db, n=2)
    d = _definition(db)
    url = poster_gen.generate_for_definition(
        db, d, fetch=lambda mt, i: None, root=tmp_path / "gen")
    assert url is not None                       # gradient fallback, never art-less
    assert poster_gen.read_poster(d["id"], tmp_path / "gen")


def test_generate_batch_counts_successes(db, tmp_path):
    _seed(db)
    d = _definition(db)
    n = poster_gen.generate_for_definitions(
        db, [d["id"], 99999], fetch=lambda mt, i: _jpeg((60, 60, 60)), root=tmp_path / "gen")
    assert n == 1                                 # unknown id skipped, not fatal


# ── sync pushes generated poster BYTES, never our relative route ────────────
class _FakeSource:
    server_name = "plex"

    def __init__(self):
        self.meta = None

    def find_collection(self, kind, name):
        return None

    def create_collection(self, kind, name, ids):
        return {"ok": True, "server_id": "col1"}

    def collection_member_ids(self, cid):
        return []

    def collection_add(self, cid, ids):
        return {"ok": True}

    def collection_remove(self, cid, ids):
        return {"ok": True}

    def set_collection_meta(self, cid, **kw):
        self.meta = kw
        return {"ok": True}


def test_sync_pushes_generated_poster_bytes(db, tmp_path, monkeypatch):
    from core.video.collections.sync import sync_collection
    _seed(db)
    d = _definition(db)
    poster_gen.generate_for_definition(db, d, fetch=lambda mt, i: _jpeg((80, 80, 80)),
                                       root=tmp_path / "gen")
    d = db.get_collection_definition(d["id"])     # now carries the generated ref
    monkeypatch.setattr(poster_gen, "posters_root", lambda: tmp_path / "gen")
    # sync.py imported read_poster by name — patch its default-root lookup too.
    import core.video.collections.sync as sync_mod
    monkeypatch.setattr(sync_mod, "read_poster",
                        lambda did: poster_gen.read_poster(did, tmp_path / "gen"))

    src = _FakeSource()
    r = sync_collection(db, d, source=src)
    assert r["ok"]
    assert src.meta["poster_url"] is None         # our route never reaches the server
    assert isinstance(src.meta["poster_bytes"], bytes) and len(src.meta["poster_bytes"]) > 1000


def test_sync_passes_external_poster_url_through(db):
    from core.video.collections.sync import sync_collection
    _seed(db)
    cid = db.create_collection_definition(
        "Action", media_type="movie", poster_url="https://img.example/poster.jpg",
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]})
    src = _FakeSource()
    assert sync_collection(db, db.get_collection_definition(cid), source=src)["ok"]
    assert src.meta["poster_url"] == "https://img.example/poster.jpg"
    assert src.meta["poster_bytes"] is None


# ── API seam ─────────────────────────────────────────────────────────────────
def _make_client(tmp_path):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi._video_db


def test_poster_api_generate_then_serve(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_DATABASE_PATH", str(tmp_path / "video_library.db"))
    client, vdb = _make_client(tmp_path)
    _seed(vdb)
    d = _definition(vdb)

    # Nothing generated yet → 404.
    assert client.get(f"/api/video/collections/{d['id']}/poster").status_code == 404

    # No server configured → member fetches fail → gradient fallback still lands.
    r = client.post(f"/api/video/collections/{d['id']}/poster/generate").get_json()
    assert r["ok"] and r["poster_url"].startswith(f"/api/video/collections/{d['id']}/poster?v=")

    resp = client.get(f"/api/video/collections/{d['id']}/poster")
    assert resp.status_code == 200 and resp.content_type == "image/jpeg"
    assert Image.open(io.BytesIO(resp.data)).size == (1000, 1500)

    assert client.post("/api/video/collections/99999/poster/generate").status_code == 404
