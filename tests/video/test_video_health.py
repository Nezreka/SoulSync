"""Video health surface — Sonarr's health-check idea, cheap + local only.

collect(db) aggregates: unreachable library roots (error), low disk vs the
min-free floor (warning), a missing recycle override folder (warning),
errored maintenance runs (warning), in-flight downloads with no monitor
thread (warning), and an always-visible YouTube readiness tile. No network
probes — server connectivity surfaces through the flows that use it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from core.video.health import collect
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_DASH_JS = (_ROOT / "webui" / "static" / "video" / "video-dashboard.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def youtube_health_defaults(monkeypatch):
    monkeypatch.setattr("core.ytdlp_update.installed_version", lambda importer=None: "2026.08.11")
    monkeypatch.setattr("core.settings.config_manager.get", lambda key, default="": default)
    monkeypatch.setattr("core.video.disk_guard.free_gb", lambda path: 100.0)


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _yt_check(h):
    return next(c for c in h["checks"] if c["id"] == "youtube_health")


def test_healthy_system_reports_youtube_ready(db, tmp_path):
    (tmp_path / "Movies").mkdir()
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    h = collect(db)
    assert h["status"] == "ok"
    c = _yt_check(h)
    assert c["status"] == "ok"
    assert "yt-dlp 2026.08.11" in c["detail"]
    assert "cookies: none" in c["detail"]
    assert "temp:" in c["detail"]
    assert "output: not configured" in c["detail"]
    assert "recent failures: 0" in c["detail"]


def test_unreachable_root_is_an_error(db, tmp_path):
    db.set_setting("movies_path", str(tmp_path / "not-mounted"))
    h = collect(db)
    assert h["status"] == "error"
    c = h["checks"][0]
    assert c["id"] == "movies_path" and "unreachable" in c["detail"]


def test_unset_roots_are_not_noise(db):
    h = collect(db)
    assert h["status"] == "ok"
    assert [c["id"] for c in h["checks"]] == ["youtube_health"]


def test_low_disk_under_the_floor_warns(db, tmp_path, monkeypatch):
    (tmp_path / "Movies").mkdir()
    db.set_setting("movies_path", str(tmp_path / "Movies"))
    from core.video import organization
    organization.save(db, {**organization.load(db), "min_free_disk_gb": 10})
    monkeypatch.setattr("core.video.disk_guard.free_gb", lambda p: 3.2)
    h = collect(db)
    assert h["status"] == "warning"
    assert "3.2 GB free" in h["checks"][0]["detail"]
    assert "grabs are paused" in h["checks"][0]["detail"]


def test_missing_recycle_override_warns(db, tmp_path):
    from core.video import organization
    organization.save(db, {**organization.load(db),
                           "recycle_path": str(tmp_path / "gone-trash")})
    h = collect(db)
    assert any(c["id"] == "recycle_path" and c["status"] == "warning" for c in h["checks"])


def test_inflight_downloads_without_monitor_warn(db, monkeypatch):
    conn = db._get_connection()
    conn.execute("INSERT INTO video_downloads (kind, title, status, source) "
                 "VALUES ('movie','Heat','downloading','slskd')")
    conn.commit(); conn.close()
    import core.video.download_monitor as mon
    monkeypatch.setattr(mon, "_started", False)
    h = collect(db)
    assert any(c["id"] == "monitor" for c in h["checks"])
    # youtube rows don't count — they have their own worker pool
    conn = db._get_connection()
    conn.execute("UPDATE video_downloads SET source='youtube'")
    conn.commit(); conn.close()
    assert not any(c["id"] == "monitor" for c in collect(db)["checks"])


def test_youtube_missing_cookie_file_warns(db, monkeypatch, tmp_path):
    missing = tmp_path / "gone-cookies.txt"

    def _get(key, default=""):
        if key == "youtube.cookies_browser":
            return "custom"
        if key == "youtube.cookies_file":
            return str(missing)
        return default

    monkeypatch.setattr("core.settings.config_manager.get", _get)
    h = collect(db)
    assert h["status"] == "warning"
    c = _yt_check(h)
    assert c["status"] == "warning"
    assert "cookies: file missing" in c["detail"]


def test_youtube_recent_blocked_failures_warn(db):
    conn = db._get_connection()
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, error, completed_at)
        VALUES ('video','youtube','abc123','One','failed','HTTP Error 403: Forbidden',datetime('now'))""")
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, error, completed_at)
        VALUES ('video','youtube','def456','Two','failed','Sign in to confirm you are not a bot',datetime('now'))""")
    conn.commit(); conn.close()
    h = collect(db)
    c = _yt_check(h)
    assert h["status"] == "warning" and c["status"] == "warning"
    assert "2 recent YouTube failure(s)" in c["detail"]
    # The tile now names the failure and its fix rather than counting a bucket.
    assert "mostly blocked" in c["detail"]
    assert "yt-dlp" in c["detail"]


def test_a_failure_the_operator_must_fix_is_an_error_not_a_warning(db):
    """A full disk or a missing ffmpeg will not clear on its own, so it must not
    wear the same colour as a couple of timeouts. Whoever is looking at the tile
    needs to know which of the two they are in."""
    conn = db._get_connection()
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, error, completed_at)
        VALUES ('video','youtube','a','One','failed','Connection reset by peer',datetime('now'))""")
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, error, completed_at)
        VALUES ('video','youtube','b','Two','failed','ERROR: [Errno 28] No space left on device',datetime('now'))""")
    conn.commit(); conn.close()
    c = _yt_check(collect(db))
    assert c["status"] == "error"
    # ...and the ONE standing problem is named, not drowned by the ordinary one.
    assert "mostly disk" in c["detail"]
    assert "disk space" in c["detail"].lower()


def test_a_success_after_the_failures_clears_the_alarm(db):
    """Boulder's C: drive filled on the 31st, produced 18 'no space left on device'
    rows, and was cleared the next day - and the tile went on telling him to free
    space he had already freed, because the failures were still inside its 3-day
    window. A light that stays red after the fault is fixed is a light you learn
    to ignore."""
    conn = db._get_connection()
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, error, completed_at)
        VALUES ('video','youtube','a','One','failed','[Errno 28] No space left on device',
                datetime('now','-2 days'))""")
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, completed_at)
        VALUES ('video','youtube','b','Two','completed', datetime('now','-1 hours'))""")
    conn.commit(); conn.close()
    c = _yt_check(collect(db))
    assert "downloads working again" in c["detail"]
    assert c["status"] != "error", "a cleared fault must not keep shouting"


def test_a_success_BEFORE_the_failures_does_not_clear_them(db):
    """Order is the whole point: a success from last week says nothing about a
    disk that filled this morning."""
    conn = db._get_connection()
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, completed_at)
        VALUES ('video','youtube','b','Two','completed', datetime('now','-2 days'))""")
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, error, completed_at)
        VALUES ('video','youtube','a','One','failed','[Errno 28] No space left on device',
                datetime('now','-1 hours'))""")
    conn.commit(); conn.close()
    c = _yt_check(collect(db))
    assert c["status"] == "error"
    assert "downloads working again" not in c["detail"]


def test_recovered_is_false_when_there_was_nothing_to_recover_from(db):
    """"Recovered" has to mean "a failure happened and then stopped". A run of
    plain successes is healthy, not recovered - and the health tile only asks
    after it has already found failures, so the distinction has to live in the
    method rather than in its caller."""
    conn = db._get_connection()
    conn.execute("""INSERT INTO video_download_history
        (kind, source, media_id, title, outcome, completed_at)
        VALUES ('video','youtube','b','Two','completed', datetime('now','-1 hours'))""")
    conn.commit(); conn.close()
    assert db.youtube_download_recovered(days=3) is False
    # ...and with nothing at all in the table either.


def test_ordinary_failures_stay_a_warning(db):
    conn = db._get_connection()
    for i in range(3):
        conn.execute("""INSERT INTO video_download_history
            (kind, source, media_id, title, outcome, error, completed_at)
            VALUES ('video','youtube',?,'X','failed','Connection reset by peer',datetime('now'))""",
                     (str(i),))
    conn.commit(); conn.close()
    c = _yt_check(collect(db))
    assert c["status"] == "warning"


def test_endpoint_and_dashboard_strip(db, tmp_path):
    import api.video as videoapi
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    try:
        r = app.test_client().get("/api/video/health")
        body = r.get_json()
        assert r.status_code == 200 and body["status"] in ("ok", "warning", "error")
        assert any(c["id"] == "youtube_health" for c in body["checks"])
    finally:
        videoapi._video_db = None
    assert "data-vdash-health" in _INDEX
    assert "function loadHealth" in _DASH_JS and "loadHealth();" in _DASH_JS
    assert "ok: '✓'" in _DASH_JS


def test_hidden_strip_takes_no_space():
    """The strip's class rule sets display:flex, which overrides the [hidden]
    attribute's UA display:none — without an explicit [hidden] rule an empty
    healthy strip leaves a visible gap + margin above the header."""
    css = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")
    assert ".vdash-health[hidden] { display: none; }" in css
    assert ".vdash-health-chip--ok" in css


# ── per-source health, read off the receipts each search already leaves ───────
def _snap(db, tmdb, sources, days_ago=0):
    """One wishlist row carrying a per-source search snapshot.

    days_ago rides as a datetime MODIFIER, not as the whole time string: bound
    as the latter, sqlite returns NULL and the row then falls out of the window
    for being null rather than for being old, which makes the staleness test
    pass without testing staleness.
    """
    import json as _json
    conn = db._get_connection()
    conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, status, "
                 "last_search_at, search_snapshot) "
                 "VALUES ('movie', ?, 'X', 'wanted', datetime('now', ?), ?)",
                 (tmdb, "-%d days" % int(days_ago),
                  _json.dumps({"chain": list(sources), "sources": sources})))
    conn.commit()
    stored = conn.execute("SELECT last_search_at FROM video_wishlist WHERE tmdb_id=?",
                          (tmdb,)).fetchone()[0]
    conn.close()
    assert stored, "the fixture wrote a NULL timestamp; the window test would be hollow"


def _src_check(h, src):
    return next((c for c in h["checks"] if c["id"] == "source_" + src), None)


def test_a_source_that_cannot_run_is_an_error_not_an_empty_result(db):
    """The distinction the whole check exists for. A closed slskd port used to
    read as 'nothing on Soulseek has what you want' - for weeks."""
    for i in range(3):
        _snap(db, 100 + i, {"soulseek": {"ran": False, "results": 0, "accepted": 0,
                                         "reason": "slskd is unreachable"}})
    c = _src_check(collect(db), "soulseek")
    assert c and c["status"] == "error"
    assert "couldn't run" in c["detail"] and "slskd is unreachable" in c["detail"]


def test_a_source_that_ran_and_found_nothing_is_only_a_warning(db):
    """It is working. Worth saying - an indexer set returning nothing for a week
    is misconfigured more often than unlucky - but it is not broken."""
    for i in range(3):
        _snap(db, 200 + i, {"torrent": {"ran": True, "results": 0, "accepted": 0}})
    c = _src_check(collect(db), "torrent")
    assert c and c["status"] == "warning"
    assert "no releases at all" in c["detail"]


def test_a_healthy_source_says_what_it_actually_delivered(db):
    for i in range(2):
        _snap(db, 300 + i, {"torrent": {"ran": True, "results": 7, "accepted": 2}})
    c = _src_check(collect(db), "torrent")
    assert c and c["status"] == "ok"
    assert "14 releases" in c["detail"] and "4 passed" in c["detail"]


def test_a_source_that_ran_only_sometimes_is_flagged_as_partial(db):
    _snap(db, 400, {"usenet": {"ran": True, "results": 3, "accepted": 1}})
    _snap(db, 401, {"usenet": {"ran": False, "results": 0, "accepted": 0,
                               "reason": "no usenet client configured"}})
    c = _src_check(collect(db), "usenet")
    assert c and c["status"] == "warning"
    assert "ran 1 of the last 2" in c["detail"]


def test_an_untried_source_is_not_reported_at_all(db):
    """A green tile for a source nobody has used would be a claim we cannot make."""
    _snap(db, 500, {"torrent": {"ran": True, "results": 1, "accepted": 1}})
    h = collect(db)
    assert _src_check(h, "torrent") is not None
    assert _src_check(h, "soulseek") is None
    assert _src_check(h, "usenet") is None


def test_stale_snapshots_fall_out_of_the_window(db):
    """A source that was down a month ago is not down now."""
    _snap(db, 600, {"soulseek": {"ran": False, "results": 0, "reason": "down"}},
          days_ago=30)
    assert _src_check(collect(db), "soulseek") is None


def test_unreadable_snapshots_never_break_health(db):
    conn = db._get_connection()
    conn.execute("INSERT INTO video_wishlist (kind, tmdb_id, title, status, "
                 "last_search_at, search_snapshot) "
                 "VALUES ('movie', 700, 'X', 'wanted', datetime('now'), 'not json')")
    conn.commit(); conn.close()
    assert isinstance(collect(db)["checks"], list)
