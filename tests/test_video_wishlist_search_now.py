"""Wishlist manual search ('Search now' / 'Search all missing') + live-state badges.

The wishlist page had NO manual control: acquisition was purely the hourly
drain, and the page rendered wanted-vs-reality lies (dead status pills, an
invisible upgrade watch). This suite covers the three additions:

  · wishlist_manual_search_items — the gate-free row query behind 'Search now'
    (the click is the override; Sonarr semantics)
  · POST /wishlist/search + /wishlist/search-all — non-blocking dispatch,
    de-duped against active downloads and the drain's own overlap guard
  · _annotate_live_state — 'downloading' + 'upgrade_from' stamped onto page rows
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from flask import Flask

from database.video_database import VideoDatabase

ROOT = Path(__file__).resolve().parent.parent


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


def _seed_owned_movie(db, tmdb_id, title, resolution="720p"):
    mid = db.upsert_movie("plex", {"server_id": "m%s" % tmdb_id, "title": title,
                                   "tmdb_id": tmdb_id, "has_file": 1})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (mid,))
    conn.execute("INSERT INTO media_files (movie_id, relative_path, resolution) VALUES (?, ?, ?)",
                 (mid, "%s.mkv" % tmdb_id, resolution))
    conn.commit()
    conn.close()
    return mid


# ---------------------------------------------------------------------------
# DB: wishlist_manual_search_items (no gates)
# ---------------------------------------------------------------------------

def test_manual_items_bypass_release_and_status_gates(db):
    # unreleased 'monitored' movie with a far-future availability date: the
    # drain's query skips it; the manual query must return it
    db.add_movie_to_wishlist(77, "Future Film", status="monitored")
    db.set_wishlist_release_date(77, "2027-12-01")
    assert db.movie_wishlist_to_download() == []
    items = db.wishlist_manual_search_items("movie", 77)
    assert [i["tmdb_id"] for i in items] == [77]
    assert items[0]["owned"] == 0


def test_manual_items_scopes_show_season_episode(db):
    db.add_episodes_to_wishlist(500, "Show", [
        {"season_number": 1, "episode_number": 1},
        {"season_number": 1, "episode_number": 2},
        {"season_number": 2, "episode_number": 1, "air_date": "2027-01-01"},  # future
    ])
    assert len(db.wishlist_manual_search_items("show", 500)) == 3     # future included
    assert len(db.wishlist_manual_search_items("season", 500, season_number=1)) == 2
    one = db.wishlist_manual_search_items("episode", 500, season_number=2, episode_number=1)
    assert [(i["season_number"], i["episode_number"]) for i in one] == [(2, 1)]
    assert one[0]["show_tmdb_id"] == 500     # drain-compatible shape


# ---------------------------------------------------------------------------
# API: /wishlist/search (per item) + /wishlist/search-all
# ---------------------------------------------------------------------------

def _wait_for(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_search_now_runs_the_drain_seams_in_background(client, db, monkeypatch):
    import core.video.wishlist_search as ws
    from core.automation.handlers import video_process_wishlist as vpw
    db.add_movie_to_wishlist(88, "Grab Me", status="monitored")   # gate would skip it
    calls = []
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/media/movies")
    monkeypatch.setattr(vpw, "_default_search",
                        lambda it, mt: ([{"accepted": True, "resolution": "1080p",
                                          "source": "soulseek", "title": it["title"]}], None))
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda it, best, cands, mt, root: calls.append((it["tmdb_id"], mt, root)) or True)
    r = client.post("/api/video/wishlist/search", json={"scope": "movie", "tmdb_id": 88})
    out = r.get_json()
    assert out["success"] is True and out["queued"] == 1, out
    assert _wait_for(lambda: calls == [(88, "movie", "/media/movies")]), f"enqueue never ran: {calls}"
    # the batch thread's finally releases the bookkeeping AFTER the enqueue —
    # wait for it rather than racing it
    assert _wait_for(lambda: ws._inflight == set()), f"bookkeeping never released: {ws._inflight}"


def test_search_now_skips_items_already_downloading(client, db, monkeypatch):
    from core.automation.handlers import video_process_wishlist as vpw
    db.add_movie_to_wishlist(99, "Already Going")
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: {("movie", "99")})
    out = client.post("/api/video/wishlist/search",
                      json={"scope": "movie", "tmdb_id": 99}).get_json()
    assert out["queued"] == 0 and out["skipped"] == 1


def test_search_now_validates_input(client):
    assert client.post("/api/video/wishlist/search", json={}).status_code == 400
    assert client.post("/api/video/wishlist/search",
                       json={"scope": "nope", "tmdb_id": 1}).status_code == 400


def test_search_all_respects_drain_guard_and_gates(client, db, monkeypatch):
    from core.automation.handlers import video_process_wishlist as vpw
    monkeypatch.setattr(vpw, "_backfill_movie_available_dates", lambda limit=25: None)
    db.add_movie_to_wishlist(11, "Ready Movie")                       # wanted, no date → eligible
    db.add_movie_to_wishlist(12, "Future Movie", status="monitored")  # gate skips
    calls = []
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/media/x")
    monkeypatch.setattr(vpw, "_default_search", lambda it, mt: ([], None))   # empty result is fine
    # episode drain busy → refused for that kind only
    monkeypatch.setitem(vpw._running, "episode", True)
    try:
        out = client.post("/api/video/wishlist/search-all").get_json()
        assert out["success"] is True
        assert out["kinds"]["movie"] == "started"
        assert out["kinds"]["episode"] == "busy"
        assert _wait_for(lambda: not vpw._running.get("movie"))
    finally:
        vpw._running["episode"] = False
    del calls


def test_user_triggered_prepare_keeps_owned_cutoff_items(monkeypatch):
    from core.video import wishlist_search as ws
    from core.automation.handlers import video_process_wishlist as vpw

    items = [{"tmdb_id": 202, "title": "Owned Movie", "owned": 1, "owned_resolutions": "1080p"}]
    monkeypatch.setattr(vpw, "_default_active_keys", lambda media_type: set())
    monkeypatch.setattr(vpw, "annotate_upgrades", lambda items, *a, **k: [])

    background_shape = ws._prepare(items, "movie", user_initiated=False)
    user_shape = ws._prepare(items, "movie", user_initiated=True)
    try:
        assert background_shape == []
        assert user_shape == items
    finally:
        ws._finish(user_shape, "movie")



# ---------------------------------------------------------------------------
# Page annotations: downloading + upgrade_from
# ---------------------------------------------------------------------------

def test_wishlist_page_shows_downloading_and_upgrade_watch(client, db):
    # movie A: actively downloading; movie B: owned at 720p, cutoff 1080p → upgrade watch
    db.add_movie_to_wishlist(201, "Downloading Movie")
    db.add_movie_to_wishlist(202, "Upgrade Movie")
    _seed_owned_movie(db, 202, "Upgrade Movie", resolution="720p")
    # default quality profile cutoff is 1080p → the owned 720p copy is below it
    db.add_video_download({"kind": "movie", "source": "soulseek", "media_id": "201",
                           "title": "Downloading Movie", "status": "downloading",
                           "target_dir": "/x", "search_ctx": "{}"})
    out = client.get("/api/video/wishlist?kind=movie").get_json()
    by = {i["tmdb_id"]: i for i in out["items"]}
    assert by[201].get("downloading") is True
    assert by[201].get("upgrade_from") is None
    assert by[202].get("downloading") is None
    assert by[202].get("upgrade_from") == "720p"


def test_wishlist_page_episode_annotations_roll_up(client, db):
    import json as _json
    db.add_episodes_to_wishlist(600, "Rollup Show", [
        {"season_number": 1, "episode_number": 1},
        {"season_number": 1, "episode_number": 2},
    ])
    db.add_video_download({"kind": "episode", "source": "soulseek", "media_id": "600",
                           "title": "Rollup Show S01E02", "status": "queued",
                           "target_dir": "/x",
                           "search_ctx": _json.dumps({"season": 1, "episode": 2})})
    out = client.get("/api/video/wishlist?kind=show").get_json()
    show = out["items"][0]
    assert show["downloading_count"] == 1
    eps = {e["episode_number"]: e for e in show["seasons"][0]["episodes"]}
    assert eps[2].get("downloading") is True and eps[1].get("downloading") is None


# ---------------------------------------------------------------------------
# Frontend contracts (the React wishlist's video tabs)
# ---------------------------------------------------------------------------

from pathlib import Path as _P

_ROOT = _P(__file__).resolve().parent.parent
_WLD = _ROOT / "webui" / "src" / "routes" / "wishlist"
_HELPERS = (_WLD / "-video.helpers.ts").read_text(encoding="utf-8")
_API = (_WLD / "-video.api.ts").read_text(encoding="utf-8")
_ACTIONS = (_WLD / "-video.use-actions.ts").read_text(encoding="utf-8")
_PAGE = (_WLD / "-ui" / "video-wishlist.tsx").read_text(encoding="utf-8")
_SHEETS = (_WLD / "-ui" / "video-sheets.tsx").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "wishlist-premier.css").read_text(encoding="utf-8")


def test_js_status_pill_tells_the_truth():
    fn = _HELPERS.split("export function liveStatus")[1].split("export function sourceLines")[0]
    assert "it.downloading" in fn and "it.upgrade_from" in fn
    assert "monitored:" in _HELPERS and "upgrade:" in _HELPERS     # the STATUS entries
    # the movie panel and the episode row both derive from reality
    assert "liveStatus(movie)" in _SHEETS and "liveStatus(ep, show.muted)" in _SHEETS


def test_js_search_now_buttons_on_all_three_levels():
    # movie, season and episode each have their own Search, aimed at that scope
    assert "const target: VideoActionTarget = { scope: 'movie', tmdb_id: movie.tmdb_id };" in _SHEETS
    assert "scope: 'season'," in _SHEETS and "scope: 'episode'," in _SHEETS
    assert _SHEETS.count("actions.searchNow(") >= 4      # movie, show, season, episode
    assert "apiClient.post('video/wishlist/search', { json: target })" in _API


def test_js_search_all_button_wired_and_scoped():
    assert "apiClient.post('video/wishlist/search-all')" in _API
    # The button used to be HIDDEN on the YouTube tab ("it has its own drain").
    # Searching genuinely means nothing there - the video IS the release - but
    # hiding it left that tab with no bulk action at all, and once a video could
    # be waiting out a retry backoff the only override was clicking every row.
    # Same button, different verb: it searches on the TMDB tabs and downloads on
    # YouTube, through the drain's own enqueue path.
    assert "{media === 'youtube' ? 'Download all waiting' : 'Search all missing'}" in _PAGE
    assert "apiClient.post('video/wishlist/youtube/download-all')" in _API
    bulk = _ACTIONS[_ACTIONS.index("const bulk = () =>"):_ACTIONS.index("const removed = async")]
    assert bulk.index("downloadAllYoutubeWaiting()") < bulk.index("searchAllVideoMissing()")
    # shown only when the tab has something in it
    assert "{tabHasItems ? (" in _PAGE


def test_css_covers_new_states_and_touch():
    # the pill wears its state: stuck (amber), downloading (accent), done (quiet)
    for cls in (".wlp-sheet-pill.is-stuck", ".wlp-sheet-pill.is-live", ".wlp-sheet-pill.is-done"):
        assert cls in _CSS, cls
    # search buttons are always visible (no hover reveal), and the hover-only
    # remove button shows on touch
    assert "opacity: 0" not in _CSS[_CSS.index(".wlp-sheet-chip {"):_CSS.index(".wlp-sheet-chip:hover")]
    touch = _CSS[_CSS.index("@media (hover: none) {"):]
    touch = touch[:touch.index("}")]
    assert ".wlp-sheet-x" in touch and "opacity: 1" in touch


def test_search_now_reports_missing_target_before_dispatch(client, db, monkeypatch):
    from core.automation.handlers import video_process_wishlist as vpw
    db.add_movie_to_wishlist(303, "No Folder")
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "")
    out = client.post("/api/video/wishlist/search", json={"scope": "movie", "tmdb_id": 303}).get_json()
    assert out["queued"] == 0
    assert out["missing_target"] == "movie"


def test_search_all_reports_unconfigured_kinds(client, db, monkeypatch):
    from core.automation.handlers import video_process_wishlist as vpw
    monkeypatch.setattr(vpw, "_backfill_movie_available_dates", lambda limit=25: None)
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "")
    db.add_movie_to_wishlist(304, "No Movie Folder")
    db.add_episodes_to_wishlist(700, "No TV Folder", [{"season_number": 1, "episode_number": 1}])
    out = client.post("/api/video/wishlist/search-all").get_json()
    assert out["kinds"] == {"movie": "unconfigured", "episode": "unconfigured"}



def test_service_switch_exposes_extto_as_hybrid_source():
    src = (ROOT / "webui" / "static" / "video" / "video-service-status.js").read_text(encoding="utf-8")
    assert "var SOURCES = ['torrent', 'extto', 'soulseek', 'usenet'];" in src
    assert "extto: { name: 'EXT.to'" in src
    assert "extto: 'EXT.to'" in src
