"""Custom Formats (arr-parity P3) — scored release-name matchers.

Radarr's sharpest ranking tool: patterns over the release NAME add (or
subtract) score, with per-profile overrides and an optional hard floor.
Applied inside the shared ranker (_evaluate_hits), so drain, RSS, manual
search and requery all judge identically.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

import core.video.custom_formats as cf
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


# ---------------------------------------------------------------------------
# Matching (pure)
# ---------------------------------------------------------------------------

def test_substring_and_regex_terms():
    fmts = [{"id": 1, "name": "HEVC", "include": ["/x265|hevc/"], "exclude": [], "score": 20},
            {"id": 2, "name": "Bad group", "include": ["-YIFY"], "exclude": [], "score": -100}]
    assert [f["name"] for f in cf.matching_formats("Movie.2026.1080p.x265-GRP", fmts)] == ["HEVC"]
    assert [f["name"] for f in cf.matching_formats("Movie.2026.HEVC.WEB", fmts)] == ["HEVC"]
    assert [f["name"] for f in cf.matching_formats("Movie.2026.1080p-YIFY", fmts)] == ["Bad group"]


def test_all_includes_must_hit_and_excludes_veto():
    fmts = [{"id": 1, "name": "Web HDR", "include": ["WEB", "HDR"], "exclude": ["HDTV"], "score": 5}]
    assert cf.matching_formats("Show.S01E01.WEB.HDR.x265", fmts)
    assert not cf.matching_formats("Show.S01E01.WEB.x265", fmts)          # missing HDR
    assert not cf.matching_formats("Show.S01E01.WEB.HDR.HDTV", fmts)      # exclude veto


def test_broken_regex_matches_nothing():
    fmts = [{"id": 1, "name": "Broken", "include": ["/[unclosed/"], "exclude": [], "score": 50}]
    assert cf.matching_formats("anything at all [unclosed", fmts) == []


def test_scores_sum_with_per_profile_overrides():
    fmts = [{"id": 1, "name": "A", "include": ["web"], "exclude": [], "score": 10},
            {"id": 2, "name": "B", "include": ["hdr"], "exclude": [], "score": 5}]
    total, names = cf.format_score("Movie WEB HDR", fmts, {"format_scores": {"2": -50}})
    assert total == -40 and names == ["A", "B"]
    total2, _ = cf.format_score("Movie WEB HDR", fmts, {})
    assert total2 == 15


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def test_store_crud_and_validation(db):
    assert cf.save_format(db, {"name": "", "include": ["x"]}) is None          # needs a name
    assert cf.save_format(db, {"name": "X", "include": []}) is None            # needs a term
    f = cf.save_format(db, {"name": "Freeleech", "include": ["freeleech"], "score": 25})
    assert f["id"] == 1
    f2 = cf.save_format(db, {"id": 1, "name": "Freeleech!", "include": ["freeleech"], "score": 30})
    assert f2["id"] == 1
    rows = cf.load_formats(db)
    assert len(rows) == 1 and rows[0]["name"] == "Freeleech!" and rows[0]["score"] == 30
    assert cf.delete_format(db, 1) is True
    assert cf.load_formats(db) == []


# ---------------------------------------------------------------------------
# Ranker integration (the one seam every path shares)
# ---------------------------------------------------------------------------

def _hits():
    return [
        {"title": "Heat 1995 1080p WEB x264-GOOD", "filename": "Heat 1995 1080p WEB x264-GOOD",
         "size_bytes": 4_000_000_000, "username": "u1"},
        {"title": "Heat 1995 1080p WEB x264-YIFY", "filename": "Heat 1995 1080p WEB x264-YIFY",
         "size_bytes": 4_000_000_000, "username": "u2"},
    ]


def test_format_scores_reorder_the_ranking(db, client):
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load as load_profile
    cf.save_format(db, {"name": "Trash group", "include": ["-YIFY"], "score": -200})
    out = _evaluate_hits(_hits(), load_profile(db), "movie", None, None,
                         blocked=frozenset(), blocked_users=frozenset(), want_title="Heat", want_year=1995)
    assert [r["title"].split("-")[-1] for r in out] == ["GOOD", "YIFY"]
    yify = out[1]
    assert yify["format_score"] == -200 and yify["formats"] == ["Trash group"]
    assert yify["accepted"] is True     # buried, not rejected


def test_min_format_score_hard_rejects(db, client):
    from api.video.downloads import _evaluate_hits
    from core.video.quality_profile import load as load_profile
    cf.save_format(db, {"name": "Trusted", "include": ["-GOOD"], "score": 50})
    profile = dict(load_profile(db))
    profile["min_format_score"] = 10
    out = _evaluate_hits(_hits(), profile, "movie", None, None,
                         blocked=frozenset(), blocked_users=frozenset(), want_title="Heat", want_year=1995)
    by = {r["title"].split("-")[-1]: r for r in out}
    assert by["GOOD"]["accepted"] is True
    assert by["YIFY"]["accepted"] is False
    assert "below your minimum" in str(by["YIFY"]["rejected"])


def test_profile_normalize_carries_format_fields():
    from core.video.quality_profile import normalize
    p = normalize({"format_scores": {"3": "25", "bad": "x"}, "min_format_score": "15"})
    assert p["format_scores"] == {"3": 25}
    assert p["min_format_score"] == 15


# ---------------------------------------------------------------------------
# API + UI contracts
# ---------------------------------------------------------------------------

def test_formats_api_crud(client):
    created = client.post("/api/video/downloads/quality/formats",
                          json={"name": "HDR", "include": ["/hdr|dv/"], "score": 15}).get_json()
    assert created["success"] and created["id"] == 1
    assert client.get("/api/video/downloads/quality/formats").get_json()["formats"][0]["name"] == "HDR"
    assert client.post("/api/video/downloads/quality/formats", json={"name": ""}).status_code == 400
    assert client.delete("/api/video/downloads/quality/formats/1").get_json()["success"]
    assert client.delete("/api/video/downloads/quality/formats/9").status_code == 404


def test_settings_ui_has_the_formats_editor():
    assert 'id="vq-format-rows"' in _INDEX and "data-vq-format-add" in _INDEX
    assert 'id="vq-min-format-score"' in _INDEX
    assert "QUALITY_URL + '/formats'" in _SETTINGS_JS
    assert "format_scores" in _SETTINGS_JS       # per-profile override column
    assert "renderFormats()" in _SETTINGS_JS
