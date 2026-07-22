"""Calendar movie lane — wishlisted movies' release dates on the calendar.

Two typed events per movie: 'cinema' (theatrical, from the add-time detail_json)
and 'available' (home availability, the drain-backfilled release_date column).
Same-day collision collapses to 'available' (a streaming film never sees a
cinema); the 1970 backfill sentinel is never an event. The ICS feed carries the
movie events (whole-day) and upgrades episodes with a real TVDB air time to
timed events; ?movies=0 opts the feed out of the movie lane.
"""

from __future__ import annotations

import pytest
from flask import Flask

from database.video_database import VideoDatabase


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


def _wish(db, tmdb_id, title, *, theatrical=None, available=None, **kw):
    detail = {"release_date": theatrical} if theatrical else None
    assert db.add_movie_to_wishlist(tmdb_id, title, detail_json=detail, **kw)
    if available:
        db.set_wishlist_release_date(tmdb_id, available)


# ---------------------------------------------------------------------------
# DB: calendar_movie_releases
# ---------------------------------------------------------------------------

def test_movie_release_events_typed_and_windowed(db):
    _wish(db, 1, "Cinema Only", theatrical="2026-07-20")
    _wish(db, 2, "Home Later", theatrical="2026-05-01", available="2026-07-22")
    _wish(db, 3, "Way Out", theatrical="2026-09-09")
    evs = db.calendar_movie_releases("2026-07-19", "2026-07-25")
    assert [(e["tmdb_id"], e["type"], e["date"]) for e in evs] == [
        (1, "cinema", "2026-07-20"),
        (2, "available", "2026-07-22"),
    ]
    assert evs[0]["title"] == "Cinema Only" and evs[0]["owned"] is False


def test_same_day_collision_collapses_to_available(db):
    _wish(db, 7, "Streaming Film", theatrical="2026-07-21", available="2026-07-21")
    evs = db.calendar_movie_releases("2026-07-19", "2026-07-25")
    assert [(e["type"], e["date"]) for e in evs] == [("available", "2026-07-21")]


def test_sentinel_and_malformed_dates_never_become_events(db):
    _wish(db, 8, "No Date Known", available="1970-01-01")
    _wish(db, 9, "Garbage Detail")
    conn = db._get_connection()
    conn.execute("UPDATE video_wishlist SET detail_json='not json' WHERE tmdb_id=9")
    conn.commit()
    conn.close()
    assert db.calendar_movie_releases("1969-01-01", "2027-12-31") == []


# ---------------------------------------------------------------------------
# API: /calendar payload + /calendar.ics feed
# ---------------------------------------------------------------------------

def test_calendar_payload_carries_movie_events(client, db):
    from datetime import date, timedelta
    d = (date.today() + timedelta(days=2)).isoformat()
    _wish(db, 11, "Payload Movie", available=d)
    out = client.get("/api/video/calendar?days=7").get_json()
    assert [(m["tmdb_id"], m["type"], m["date"]) for m in out["movies"]] == [(11, "available", d)]


def test_ics_movie_events_and_optout(client, db):
    from datetime import date, timedelta
    d = (date.today() + timedelta(days=2)).isoformat()
    _wish(db, 12, "Feed Movie", year=2026, available=d)
    body = client.get("/api/video/calendar.ics").get_data(as_text=True)
    assert "UID:ss-movie-12-available@soulsync" in body
    assert "SUMMARY:Feed Movie (2026) — Home Release" in body
    assert "DTSTART;VALUE=DATE:" + d.replace("-", "") in body
    body2 = client.get("/api/video/calendar.ics?movies=0").get_data(as_text=True)
    assert "ss-movie-" not in body2


def test_ics_episodes_with_real_airtime_become_timed_events(client, db):
    from datetime import date, timedelta
    d = (date.today() + timedelta(days=1)).isoformat()
    db.upsert_show_tree("plex", {
        "server_id": "sT", "title": "Timed Show", "tmdb_id": 501,
        "seasons": [{"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "E1", "air_date": d}]}]})
    db.upsert_show_tree("plex", {
        "server_id": "sU", "title": "Streamer Show", "tmdb_id": 502,
        "seasons": [{"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "E1", "air_date": d}]}]})
    conn = db._get_connection()
    conn.execute("UPDATE shows SET airs_time='21:00' WHERE tmdb_id=501")
    conn.execute("UPDATE shows SET airs_time='00:00' WHERE tmdb_id=502")  # placeholder
    conn.commit()
    conn.close()
    body = client.get("/api/video/calendar.ics").get_data(as_text=True)
    day = d.replace("-", "")
    assert "DTSTART:%sT210000" % day in body           # real time → timed event
    assert "DTSTART;VALUE=DATE:%s" % day in body       # placeholder stays whole-day


# ---------------------------------------------------------------------------
# Frontend contracts (video-calendar.js + index.html)
# ---------------------------------------------------------------------------

from pathlib import Path as _P

_ROOT = _P(__file__).resolve().parent.parent
_CAL_JS = (_ROOT / "webui" / "static" / "video" / "video-calendar.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")


def test_js_renders_movie_rail_with_typed_chips():
    assert "vcal-rail--movies" in _CAL_JS
    assert "'🎬 In Cinemas'" in _CAL_JS and "'🏠 Home Release'" in _CAL_JS
    assert "data-cal-movie" in _CAL_JS
    # owned movie opens the library detail; unowned the tmdb preview
    assert "'/video-detail/library/movie/' + m.library_id" in _CAL_JS
    assert "'/video-detail/tmdb/movie/' + m.tmdb_id" in _CAL_JS


def test_js_movie_type_filter_persisted_and_click_delegated():
    assert "data-video-cal-movietype" in _CAL_JS and "vcalMovieTypes" in _CAL_JS
    assert "data-video-cal-movietype" in _INDEX
    assert "'[data-cal-ep],[data-cal-movie]'" in _CAL_JS   # grid click delegation covers movies


def test_agenda_view_exists_and_rerenders_across_boundary():
    assert 'data-video-cal-view="agenda"' in _INDEX
    assert "function renderAgenda" in _CAL_JS
    assert "vcal-view--agenda" in _CAL_JS
    assert "if (state.data && (wasAgenda || v === 'agenda')) render();" in _CAL_JS


def test_ical_subscribe_button_and_modal():
    assert "data-video-cal-ical" in _INDEX
    assert "/api/video/calendar.ics?scope=" in _CAL_JS
    assert "function openIcsModal" in _CAL_JS


def test_empty_state_counts_movies_too():
    assert "eps.length > 0 || movies.length > 0" in _CAL_JS
