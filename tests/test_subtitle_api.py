"""Phase 3 API: subtitle overrides CRUD, manual search, manual download.

End-to-end through the Flask test client with a tmp-backed video.db, following
the tests/test_video_api.py seam pattern. The provider registry, file
resolution, and scoring are stubbed; video files are real temp files.
"""

from __future__ import annotations

import os

import pytest

from core.video.subtitles.providers.base import (
    SubtitleCandidate,
    SubtitleProvider,
)


@pytest.fixture()
def client(tmp_path):
    import api.video as videoapi
    from api.video import subtitles as submod
    from database.video_database import VideoDatabase
    from flask import Flask, g
    videoapi._video_db = VideoDatabase(
        database_path=str(tmp_path / "video_library.db"))
    submod._clear_search_cache_for_tests()
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        g.is_admin = True
        g.can_download = True

    app.register_blueprint(videoapi.create_video_blueprint(),
                           url_prefix="/api/video")
    return app.test_client()


@pytest.fixture()
def client_non_admin(tmp_path):
    import api.video as videoapi
    from api.video import subtitles as submod
    from database.video_database import VideoDatabase
    from flask import Flask, g
    videoapi._video_db = VideoDatabase(
        database_path=str(tmp_path / "video_library.db"))
    submod._clear_search_cache_for_tests()
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        g.is_admin = False
        g.can_download = False

    app.register_blueprint(videoapi.create_video_blueprint(),
                           url_prefix="/api/video")
    return app.test_client()


def _db():
    import api.video as videoapi
    return videoapi._video_db


def _movie(db, movie_id=42):
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO movies (id, title, tmdb_id) VALUES (?, 'M', 603)",
                     (movie_id,))
        conn.commit()
    finally:
        conn.close()


class ScriptedProvider(SubtitleProvider):
    id = "scripted"
    display_name = "Scripted"

    def __init__(self, candidates=(), texts=None):
        self._candidates = list(candidates)
        self._texts = dict(texts or {})

    @property
    def needs_key(self):
        return False

    def is_configured(self, get_setting):
        return True

    def search(self, query):
        return list(self._candidates)

    def download(self, candidate):
        return self._texts.get(candidate.download_ref)


def _cand(ref, score_unused=None, **kw):
    kw.setdefault("download_count", 100)
    return SubtitleCandidate(provider_id="scripted", language="en", hi=False,
                             forced=False, title="Movie.2024.1080p.WEB-GROUP",
                             download_ref=ref, **kw)


def _stub_search(monkeypatch, tmp_path, provider, scores, filename="M.mkv", db=None):
    """Stub registry + scoring + file resolution for the search endpoint."""
    import core.video.subtitles.providers as pkg
    monkeypatch.setattr(pkg, "get_providers", lambda: {"scripted": provider})
    import core.video.subtitles.scoring as scoring
    monkeypatch.setattr(scoring, "score_candidate",
                        lambda c, q: float(scores[c.download_ref]))
    if db is not None:
        # the search endpoint reads the provider chain from the organization
        # settings, not from a passed-in dict
        from core.video import organization
        organization.save(db, {"subtitle_provider_order": '["scripted"]'})
    path = str(tmp_path / filename)
    with open(path, "wb") as f:
        f.write(os.urandom(200_000))

    def _fake_resolve(db, row):
        return path, {"tmdb_id": 603}

    monkeypatch.setattr("core.video.subtitles.worker._resolve_file", _fake_resolve)
    return path


# ── overrides CRUD ────────────────────────────────────────────────────────

def test_overrides_put_get_delete_roundtrip(client):
    _movie(_db())
    r = client.put("/api/video/subtitles/overrides/movie/42",
                   json={"languages": ["EN", "es"]})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "languages": ["en", "es"]}

    r = client.get("/api/video/subtitles/overrides/movie/42")
    body = r.get_json()
    assert body == {"languages": ["en", "es"], "source": "override",
                    "global_languages": ["en"]}

    r = client.delete("/api/video/subtitles/overrides/movie/42")
    assert r.get_json() == {"ok": True}
    body = client.get("/api/video/subtitles/overrides/movie/42").get_json()
    assert body["source"] == "global" and body["languages"] == ["en"]


def test_overrides_reject_bad_kind_and_languages(client):
    _movie(_db())
    assert client.put("/api/video/subtitles/overrides/episode/42",
                      json={"languages": ["en"]}).status_code == 400
    assert client.get("/api/video/subtitles/overrides/episode/42").status_code == 400
    for bad in ([], ["e"], ["engl"], ["en", ""], "en", None, [42]):
        r = client.put("/api/video/subtitles/overrides/movie/42",
                       json={"languages": bad})
        assert r.status_code == 400, bad


def test_overrides_404_on_missing_title(client):
    assert client.get("/api/video/subtitles/overrides/movie/4242").status_code == 404
    assert client.put("/api/video/subtitles/overrides/show/4242",
                      json={"languages": ["en"]}).status_code == 404
    assert client.delete("/api/video/subtitles/overrides/movie/4242").status_code == 404


def test_overrides_show_kind(client):
    db = _db()
    conn = db._get_connection()
    try:
        conn.execute("INSERT INTO shows (id, title) VALUES (11, 'S')")
        conn.commit()
    finally:
        conn.close()
    assert client.put("/api/video/subtitles/overrides/show/11",
                      json={"languages": ["de"]}).status_code == 200
    body = client.get("/api/video/subtitles/overrides/show/11").get_json()
    assert body["languages"] == ["de"] and body["source"] == "override"


# ── search ────────────────────────────────────────────────────────────────

def test_region_subtag_roundtrip_override_search_download(client, tmp_path, monkeypatch):
    # pt-br (offered by the manual-search modal's LANG_NAMES and the override
    # input's frontend regex) round-trips through override PUT, search, and
    # manual download — the backend validator used to 400 all three.
    _movie(_db())
    r = client.put("/api/video/subtitles/overrides/movie/42",
                   json={"languages": ["pt-BR"]})
    assert r.status_code == 200
    assert r.get_json()["languages"] == ["pt-br"]
    body = client.get("/api/video/subtitles/overrides/movie/42").get_json()
    assert body["languages"] == ["pt-br"] and body["source"] == "override"

    provider = ScriptedProvider(
        [SubtitleCandidate(provider_id="scripted", language="pt-br", hi=False,
                           forced=False, title="Movie.2024.1080p.WEB-GROUP",
                           download_ref="r1", download_count=100)],
        {"r1": "srt-pt-br"})
    _stub_search(monkeypatch, tmp_path, provider, {"r1": 90.0}, db=_db())
    r = client.get("/api/video/subtitles/search/movie/42?lang=pt-br")
    assert r.status_code == 200
    cands = r.get_json()["candidates"]
    assert cands and cands[0]["language"] == "pt-br"
    cid = cands[0]["candidate_id"]

    r = client.post("/api/video/subtitles/manual-download",
                    json={"kind": "movie", "item_id": 42, "lang": "pt-br",
                          "candidate_id": cid})
    assert r.status_code == 200
    assert r.get_json()["path"].endswith("M.pt-br.srt")
    row = _db().subtitle_get_for_video("movie", 42)[0]
    assert row["language"] == "pt-br" and row["user_set"] == 1


def test_search_returns_scored_candidates(client, tmp_path, monkeypatch):
    _movie(_db())
    provider = ScriptedProvider([_cand("r1"), _cand("r2")], {"r1": "a", "r2": "b"})
    _stub_search(monkeypatch, tmp_path, provider, {"r1": 90.0, "r2": 70.0}, db=_db())
    r = client.get("/api/video/subtitles/search/movie/42?lang=en")
    assert r.status_code == 200
    cands = r.get_json()["candidates"]
    assert [c["score"] for c in cands] == [90.0, 70.0]     # score desc
    first = cands[0]
    assert first["provider"] == "scripted"
    assert first["title"] == "Movie.2024.1080p.WEB-GROUP"
    assert first["hash_match"] is False and first["download_count"] == 100
    assert first["language"] == "en" and first["hi"] is False \
        and first["forced"] is False
    assert first["candidate_id"] and len({c["candidate_id"] for c in cands}) == 2


def test_search_rejects_bad_input(client):
    _movie(_db())
    assert client.get("/api/video/subtitles/search/show/42").status_code == 400
    assert client.get("/api/video/subtitles/search/movie/42?lang=e").status_code == 400
    assert client.get("/api/video/subtitles/search/movie/4242").status_code == 404


# ── manual download ─────────────────────────────────────────────────────

def test_manual_download_happy_path(client, tmp_path, monkeypatch):
    db = _db()
    _movie(db)
    provider = ScriptedProvider([_cand("r1")], {"r1": "picked-srt"})
    path = _stub_search(monkeypatch, tmp_path, provider, {"r1": 90.0}, db=_db())
    cid = client.get("/api/video/subtitles/search/movie/42?lang=en"
                     ).get_json()["candidates"][0]["candidate_id"]

    r = client.post("/api/video/subtitles/manual-download",
                    json={"kind": "movie", "item_id": 42, "lang": "en",
                          "candidate_id": cid})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["path"].endswith("M.en.srt")
    with open(body["path"]) as f:
        assert f.read() == "picked-srt"
    # explicit user pick: 'have' + user_set=1 → protected from the upgrade loop
    row = db.subtitle_get_for_video("movie", 42)[0]
    assert row["status"] == "have" and row["user_set"] == 1
    hist = db.subtitle_get_history("movie", 42)
    assert hist[0]["outcome"] == "downloaded" and hist[0]["score"] == 90.0
    # explicit user action: does NOT burn the automated daily quota
    assert db.subtitle_quota_used("scripted") == 0
    assert len(db.subtitle_get_upgradable(50)) == 0


def test_manual_download_rejects_bad_input(client):
    assert client.post("/api/video/subtitles/manual-download",
                       json={}).status_code == 400
    assert client.post("/api/video/subtitles/manual-download",
                       json={"kind": "show", "item_id": 1, "lang": "en",
                             "candidate_id": "x"}).status_code == 400
    assert client.post("/api/video/subtitles/manual-download",
                       json={"kind": "movie", "item_id": "42", "lang": "en",
                             "candidate_id": "x"}).status_code == 400
    assert client.post("/api/video/subtitles/manual-download",
                       json={"kind": "movie", "item_id": 42, "lang": "e",
                             "candidate_id": "x"}).status_code == 400


def test_manual_download_unknown_candidate_404(client, tmp_path, monkeypatch):
    _movie(_db())
    provider = ScriptedProvider([_cand("r1")], {"r1": "srt"})
    _stub_search(monkeypatch, tmp_path, provider, {"r1": 90.0}, db=_db())
    r = client.post("/api/video/subtitles/manual-download",
                    json={"kind": "movie", "item_id": 42, "lang": "en",
                          "candidate_id": "nope"})
    assert r.status_code == 404


def test_manual_download_expired_candidate_404(client, tmp_path, monkeypatch):
    from api.video import subtitles as submod
    _movie(_db())
    provider = ScriptedProvider([_cand("r1")], {"r1": "srt"})
    _stub_search(monkeypatch, tmp_path, provider, {"r1": 90.0}, db=_db())
    cid = client.get("/api/video/subtitles/search/movie/42?lang=en"
                     ).get_json()["candidates"][0]["candidate_id"]
    submod._search_cache[cid]["expires"] = 0  # force expiry
    r = client.post("/api/video/subtitles/manual-download",
                    json={"kind": "movie", "item_id": 42, "lang": "en",
                          "candidate_id": cid})
    assert r.status_code == 404


def test_manual_download_candidate_mismatch_400(client, tmp_path, monkeypatch):
    _movie(_db(), movie_id=42)
    _movie(_db(), movie_id=43)
    provider = ScriptedProvider([_cand("r1")], {"r1": "srt"})
    _stub_search(monkeypatch, tmp_path, provider, {"r1": 90.0}, db=_db())
    cid = client.get("/api/video/subtitles/search/movie/42?lang=en"
                     ).get_json()["candidates"][0]["candidate_id"]
    # candidate was searched for movie 42 — not valid for 43
    r = client.post("/api/video/subtitles/manual-download",
                    json={"kind": "movie", "item_id": 43, "lang": "en",
                          "candidate_id": cid})
    assert r.status_code == 400


def test_manual_download_no_file_400(client, tmp_path, monkeypatch):
    import core.video.subtitles.providers as pkg
    from core.video import organization
    db = _db()
    _movie(db)
    organization.save(db, {"subtitle_provider_order": '["scripted"]'})
    provider = ScriptedProvider([_cand("r1")], {"r1": "srt"})
    monkeypatch.setattr(pkg, "get_providers", lambda: {"scripted": provider})
    import core.video.subtitles.scoring as scoring
    monkeypatch.setattr(scoring, "score_candidate", lambda c, q: 90.0)
    # no _resolve_file stub → movie 42 has no media_files row → no file
    cid = client.get("/api/video/subtitles/search/movie/42?lang=en"
                     ).get_json()["candidates"][0]["candidate_id"]
    r = client.post("/api/video/subtitles/manual-download",
                    json={"kind": "movie", "item_id": 42, "lang": "en",
                          "candidate_id": cid})
    assert r.status_code == 400


def test_manual_download_provider_failure_502(client, tmp_path, monkeypatch):
    _movie(_db())
    provider = ScriptedProvider([_cand("r1")], {})   # download → None
    _stub_search(monkeypatch, tmp_path, provider, {"r1": 90.0}, db=_db())
    cid = client.get("/api/video/subtitles/search/movie/42?lang=en"
                     ).get_json()["candidates"][0]["candidate_id"]
    r = client.post("/api/video/subtitles/manual-download",
                    json={"kind": "movie", "item_id": 42, "lang": "en",
                          "candidate_id": cid})
    assert r.status_code == 502


# ── auth ────────────────────────────────────────────────────────────────

def test_override_writes_are_admin_only(client_non_admin):
    _movie(_db())
    assert client_non_admin.put(
        "/api/video/subtitles/overrides/movie/42",
        json={"languages": ["en"]}).status_code == 403
    assert client_non_admin.delete(
        "/api/video/subtitles/overrides/movie/42").status_code == 403
    # reads stay open
    assert client_non_admin.get(
        "/api/video/subtitles/overrides/movie/42").status_code == 200
    assert client_non_admin.get(
        "/api/video/subtitles/search/movie/42").status_code == 200


def test_manual_download_needs_can_download(client, client_non_admin, tmp_path,
                                            monkeypatch):
    _movie(_db())
    provider = ScriptedProvider([_cand("r1")], {"r1": "srt"})
    _stub_search(monkeypatch, tmp_path, provider, {"r1": 90.0}, db=_db())
    cid = client.get("/api/video/subtitles/search/movie/42?lang=en"
                     ).get_json()["candidates"][0]["candidate_id"]
    r = client_non_admin.post("/api/video/subtitles/manual-download",
                              json={"kind": "movie", "item_id": 42, "lang": "en",
                                    "candidate_id": cid})
    assert r.status_code == 403
