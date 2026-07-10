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


def test_generate_chart_collection_collages_via_list_fetcher(db, tmp_path):
    # The 'Generate collage' regression: a chart/franchise collection must
    # resolve its owned members through the list fetcher — not fall back to the
    # gradient because membership couldn't resolve.
    _seed(db)                                            # movies 1..5, no tmdb ids
    conn = db._get_connection()
    conn.execute("UPDATE movies SET tmdb_id = 1000 + id")
    conn.commit(); conn.close()
    cid = db.create_collection_definition(
        "Top Rated 250", kind="list", media_type="movie",
        definition={"source": "tmdb_chart", "chart": "top_movies", "limit": 250})
    d = db.get_collection_definition(cid)
    fetched = []

    def fetch(media_type, item_id):
        fetched.append(item_id)
        return _jpeg((90, 40, 40))

    url = poster_gen.generate_for_definition(
        db, d, fetch=fetch,
        list_fetcher=lambda s, ref: [{"tmdb_id": 1001}, {"tmdb_id": 1002}],
        root=tmp_path / "gen")
    assert url is not None
    assert sorted(fetched) == [1, 2]                     # collaged from owned members


def test_generate_with_preresolved_owned_skips_resolve(db, tmp_path):
    _seed(db, n=1)
    d = _definition(db)
    owned = [{"id": 1, "server_id": "srv1", "title": "M1", "rating": 9.0}]
    url = poster_gen.generate_for_definition(
        _BoomProxy(db), d, owned=owned, fetch=lambda mt, i: _jpeg((50, 50, 50)),
        root=tmp_path / "gen")
    assert url is not None


class _BoomProxy:
    """DB proxy that forbids member resolution (only poster_url update allowed)."""

    def __init__(self, db):
        self._db = db

    def update_collection_definition(self, *a, **k):
        return self._db.update_collection_definition(*a, **k)

    def __getattr__(self, name):
        raise AssertionError(f"unexpected db call {name} — owned= should skip resolve")


# ── context art: real TMDB artwork beats a collage where the subject has one ─
class _CtxEngine:
    def collection_poster(self, cid):
        return "https://img.tmdb/collection-%s.jpg" % cid if int(cid) in (119, 131292) else None

    def person_photo(self, name):
        return "https://img.tmdb/nolan.jpg" if name == "Christopher Nolan" else None

    def company_logo(self, name):
        return "https://img.tmdb/hallmark-logo.png" if name.startswith("Hallmark") else None


def _http(url):
    class R:
        status_code = 200
        content = ("ART:" + url).encode()
    return R()


def test_context_art_franchise_verbatim_and_director_overlay():
    fr = {"kind": "list", "definition": {"source": "tmdb_collection", "collection_id": 119}}
    art = poster_gen._context_art(fr, engine=_CtxEngine(), http_get=_http)
    assert art == (b"ART:https://img.tmdb/collection-119.jpg", "verbatim")

    uni = {"kind": "list", "definition": {"source": "tmdb_union", "collections": [999, 131292]}}
    art = poster_gen._context_art(uni, engine=_CtxEngine(), http_get=_http)
    assert art[0].endswith(b"collection-131292.jpg")                    # first WITH art wins

    di = {"kind": "smart", "definition": {"rules": [
        {"field": "director", "op": "is", "value": "Christopher Nolan"}]}}
    art = poster_gen._context_art(di, engine=_CtxEngine(), http_get=_http)
    assert art == (b"ART:https://img.tmdb/nolan.jpg", "title")          # name gets burned in


def test_context_art_studio_logo_from_variant_list():
    # Brand-grouped studio rule (in-list of variants) → the studio's logo card.
    st = {"kind": "smart", "definition": {"rules": [
        {"field": "studio", "op": "in", "value": ["Hallmark Channel", "Hallmark Media"]}]}}
    art = poster_gen._context_art(st, engine=_CtxEngine(), http_get=_http)
    assert art == (b"ART:https://img.tmdb/hallmark-logo.png", "logo")


def test_context_art_none_for_charts_and_multi_rule():
    eng = _CtxEngine()
    assert poster_gen._context_art(
        {"kind": "list", "definition": {"source": "tmdb_chart", "chart": "top_movies"}},
        engine=eng, http_get=_http) is None
    assert poster_gen._context_art(
        {"kind": "smart", "definition": {"rules": [
            {"field": "director", "op": "is", "value": "X"},
            {"field": "year", "op": "gte", "value": 2000}]}},
        engine=eng, http_get=_http) is None


def test_logo_poster_light_card_for_dark_logo_and_reverse():
    # Dark logo → light card so it stays visible; light logo → the gradient.
    dark = io.BytesIO()
    Image.new("RGBA", (400, 160), (20, 20, 24, 255)).save(dark, format="PNG")
    data = poster_gen.render_logo_poster(dark.getvalue(), "A24")
    img = Image.open(io.BytesIO(data)).convert("RGB")
    assert img.size == (1000, 1500)
    assert sum(img.getpixel((30, 30))) > 550                 # corner is light

    light = io.BytesIO()
    Image.new("RGBA", (400, 160), (240, 240, 245, 255)).save(light, format="PNG")
    data = poster_gen.render_logo_poster(light.getvalue(), "A24")
    img = Image.open(io.BytesIO(data)).convert("RGB")
    assert sum(img.getpixel((30, 30))) < 300                 # corner is dark gradient
    assert poster_gen.render_logo_poster(b"not a png", "X") is None


def test_regenerate_all_respects_user_posters(db, tmp_path, monkeypatch):
    _seed(db)
    gen_id = db.create_collection_definition(
        "Action", media_type="movie", poster_url="/api/video/collections/1/poster?v=old11111",
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]})
    db.create_collection_definition(                          # hand-set URL — untouched
        "Custom", media_type="movie", poster_url="https://img.example/mine.jpg",
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]})
    bare_id = db.create_collection_definition(                # no poster — included
        "Bare", media_type="movie",
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]})

    regenerated = []
    monkeypatch.setattr(poster_gen, "generate_for_definition",
                        lambda dbb, d, **kw: regenerated.append(d["id"]) or "/api/x")
    n = poster_gen.regenerate_all(db)
    assert n == 2 and sorted(regenerated) == sorted([gen_id, bare_id])

    # Busy-guard: second kick while running is refused.
    poster_gen._JOB["running"] = True
    assert poster_gen.kick_regenerate_all(db)["ok"] is False
    poster_gen._JOB["running"] = False


def test_kick_regenerate_is_a_live_job(db, monkeypatch):
    import time
    _seed(db)
    db.create_collection_definition(
        "Action", media_type="movie",
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]})
    db.create_collection_definition(
        "Drama", media_type="movie", poster_url="/api/video/collections/9/poster?v=aa1122bb",
        definition={"rules": [{"field": "genre", "op": "in", "value": ["Action"]}]})
    monkeypatch.setattr(poster_gen, "generate_for_definition",
                        lambda dbb, d, **kw: "/api/x")
    events = []
    poster_gen.set_artwork_progress_emitter(lambda name, payload: events.append((name, payload)))
    try:
        r = poster_gen.kick_regenerate_all(db)
        assert r == {"ok": True, "total": 2}
        for _ in range(100):
            s = poster_gen.artwork_status()
            if not s["running"] and s["phase"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert s["phase"] == "done" and s["rendered"] == 2 and s["failed"] == 0
        assert events[0][0] == "collections:artwork"
        assert events[0][1]["phase"] == "starting" and events[-1][1]["phase"] == "done"
    finally:
        poster_gen.set_artwork_progress_emitter(None)
        poster_gen._JOB["running"] = False


def test_generate_auto_prefers_context_and_collage_mode_forces(db, tmp_path, monkeypatch):
    _seed(db, n=2)
    d = _definition(db)
    real_jpeg = _jpeg((25, 90, 25), size=(780, 1170))
    monkeypatch.setattr(poster_gen, "_context_art", lambda definition, **kw: (real_jpeg, "verbatim"))

    url = poster_gen.generate_for_definition(db, d, root=tmp_path / "gen")
    assert url is not None
    # Verbatim context art — stored byte-for-byte, no collage pipeline.
    assert poster_gen.read_poster(d["id"], tmp_path / "gen") == real_jpeg

    fetched = []
    url = poster_gen.generate_for_definition(
        db, db.get_collection_definition(d["id"]), mode="collage",
        fetch=lambda mt, i: fetched.append(i) or _jpeg((60, 60, 60)),
        root=tmp_path / "gen")
    assert url is not None and fetched                     # collage mode ignored context
    assert poster_gen.read_poster(d["id"], tmp_path / "gen") != real_jpeg


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


def test_sync_generates_missing_poster_by_default(db, tmp_path, monkeypatch):
    # Default-on art: a poster-less collection gets generated art on sync via the
    # injected generator, pushed as bytes in the same pass (no signature churn).
    from core.video.collections.sync import sync_collection
    import core.video.collections.sync as sync_mod
    _seed(db)
    d = _definition(db)                                   # no poster_url
    gen_calls = []

    def generator(definition, owned):
        gen_calls.append((definition["id"], len(owned)))
        return f"/api/video/collections/{definition['id']}/poster?v=abc12345"

    monkeypatch.setattr(sync_mod, "read_poster", lambda did: b"generated-jpeg-bytes")
    src = _FakeSource()
    r = sync_collection(db, d, source=src, poster_generator=generator)
    assert r["ok"]
    assert gen_calls == [(d["id"], 5)]                    # got the resolved members
    assert src.meta["poster_bytes"] == b"generated-jpeg-bytes"
    assert src.meta["poster_url"] is None
    # Definition that already HAS a poster → generator not called.
    db.update_collection_definition(d["id"], poster_url="https://img.example/x.jpg")
    sync_collection(db, db.get_collection_definition(d["id"]), source=_FakeSource(),
                    poster_generator=generator)
    assert len(gen_calls) == 1


def test_run_sync_wires_the_default_generator(db, tmp_path, monkeypatch):
    import core.video.collections.poster_gen as pg
    from core.video.collections.sync import run_sync
    _seed(db)
    _definition(db)
    called = []
    monkeypatch.setattr(pg, "generate_for_definition",
                        lambda dbb, definition, **kw: called.append(definition["id"]) or None)
    monkeypatch.setattr("core.video.collections.sync.get_collection_source",
                        lambda: _FakeSource())
    r = run_sync(db)
    assert r["ok"] and r["synced"] == 1
    assert len(called) == 1                               # default-on generator ran


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
