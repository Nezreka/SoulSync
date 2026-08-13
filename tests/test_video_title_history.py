"""Per-title acquisition history on detail pages (arr-parity P9).

The permanent archive already recorded every grab/import/upgrade/failure;
only the global History modal could see it. The detail page now shows THIS
title's timeline, matched under both identities a title can be grabbed as
(tmdb preview before it was owned, library id after).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_DETAIL_JS = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        yield app.test_client(), db
    finally:
        videoapi._video_db = None


def _archive(db, **kw):
    row = {"kind": "movie", "title": "Heat", "release_title": "Heat 1995 1080p",
           "source": "torrent", "outcome": "completed", "media_source": "tmdb",
           "media_id": "603", "size_bytes": 4_000_000_000, "quality_label": "1080p"}
    row.update(kw)
    conn = db._get_connection()
    cols = ", ".join(row)
    conn.execute("INSERT INTO video_download_history (%s) VALUES (%s)"
                 % (cols, ", ".join("?" for _ in row)), tuple(row.values()))
    conn.commit()
    conn.close()


def test_history_matches_both_identities(client):
    c, db = client
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Heat", "tmdb_id": 603})
    _archive(db)                                                    # grabbed as tmdb preview
    _archive(db, media_source="library", media_id=str(mid),
             release_title="Heat 1995 2160p", outcome="completed")  # upgraded from the library page
    _archive(db, media_id="99999")                                  # someone else's history
    out = c.get("/api/video/detail/movie/%d/history" % mid).get_json()
    rels = [h["release_title"] for h in out["history"]]
    assert rels == ["Heat 1995 2160p", "Heat 1995 1080p"]           # newest first, both identities


def test_show_history_includes_episode_grabs(client):
    c, db = client
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Severance", "tmdb_id": 95396,
                                 "seasons": [{"season_number": 1, "episodes": [{"episode_number": 1}]}]})
    sid = db.query_library("shows")["items"][0]["id"]
    _archive(db, kind="episode", title="Severance", media_id="95396",
             season_number=1, episode_number=7, release_title="Severance S01E07 WEB")
    out = c.get("/api/video/detail/show/%d/history" % sid).get_json()
    if "history" not in out:
        # Went red on CI (2026-07-28) with KeyError: 'history' and has never
        # reproduced locally. The endpoint only omits the key on its 404 branch,
        # which means show_detail() did not find the show that query_library()
        # just handed us the id for — the video-db family signature: the test's
        # own handle sees the row, the endpoint's does not.
        #
        # Self-describing rather than another 45-minute re-roll: the next red
        # run says WHICH of those it is instead of costing a repro attempt.
        import api.video as videoapi
        direct = db.show_detail(sid)
        raise AssertionError(
            "history endpoint answered %r\n"
            "  library id       : %r\n"
            "  show_detail(id)  : %s\n"
            "  query_library    : %r\n"
            "  test handle      : id=%s path=%s\n"
            "  api.video global : id=%s path=%s\n"
            "  same handle      : %s"
            % (out, sid,
               "FOUND on this handle" if direct else "NOT found on this handle either",
               db.query_library("shows")["items"],
               hex(id(db)), db.database_path,
               hex(id(videoapi._video_db)) if videoapi._video_db else None,
               getattr(videoapi._video_db, "database_path", None),
               videoapi._video_db is db)
        )
    assert len(out["history"]) == 1
    assert out["history"][0]["episode_number"] == 7


def test_unknown_title_404s_and_bad_kind_400s(client):
    c, _db = client
    assert c.get("/api/video/detail/movie/999/history").status_code == 404
    assert c.get("/api/video/detail/nope/1/history").status_code == 400


def test_detail_page_renders_the_section():
    assert "data-vd-history-section" in _INDEX
    assert _INDEX.count("Acquisition History") == 2        # movie + show layouts
    assert "loadTitleHistory('movie', id)" in _DETAIL_JS
    assert "loadTitleHistory('show', id)" in _DETAIL_JS
    assert "'[data-vd-history-section]'" in _DETAIL_JS     # reset with the other extras
