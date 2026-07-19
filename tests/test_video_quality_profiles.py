"""Per-title quality profiles + follow-time monitor policies (arr-parity P2).

Radarr/Sonarr assign a profile PER title; SoulSync had one global blob. Named
profiles now live beside it ('quality_profiles', Default stays id 0 at the
classic key so every old reader keeps working), titles carry an assignment
(movies/shows/video_wishlist.quality_profile_id), the drain/RSS/manual seams
judge each item under ITS profile, the download row records the profile it was
grabbed under, and deleting a profile degrades to Default instead of wedging.

Monitor policies: following a SHOW can wish a back-catalog slice at add time
(all / first_season / latest_season / pilot) — aired episodes only, best-effort
(the follow itself never fails on a TMDB hiccup).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

import core.video.quality_profile as qp
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent
_MANAGE_JS = (_ROOT / "webui" / "static" / "video" / "video-manage-panel.js").read_text(encoding="utf-8")
_SETTINGS_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")
_WLBTN_JS = (_ROOT / "webui" / "static" / "video" / "video-watchlist-btn.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "video" / "video-side.css").read_text(encoding="utf-8")


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


def _named_4k(db, name="4K"):
    return qp.save_named(db, None, name, {"cutoff_resolution": "2160p"})


# ---------------------------------------------------------------------------
# Profile store
# ---------------------------------------------------------------------------

def test_default_is_always_id_zero_and_undeletable(db):
    profs = qp.list_profiles(db)
    assert profs[0]["id"] == 0 and profs[0]["name"] == "Default"
    assert qp.delete_named(db, 0) is False


def test_named_profiles_create_update_delete(db):
    entry = _named_4k(db)
    assert entry["id"] >= 1
    assert entry["profile"]["cutoff_resolution"] == "2160p"
    qp.save_named(db, entry["id"], "4K Remux", {"cutoff_resolution": ""})
    profs = {p["id"]: p for p in qp.list_profiles(db)}
    assert profs[entry["id"]]["name"] == "4K Remux"
    assert profs[entry["id"]]["profile"]["cutoff_resolution"] == ""
    assert qp.delete_named(db, entry["id"]) is True
    assert entry["id"] not in {p["id"] for p in qp.list_profiles(db)}


def test_dangling_assignment_degrades_to_default(db):
    entry = _named_4k(db)
    qp.delete_named(db, entry["id"])
    assert qp.profile_by_id(db, entry["id"]) == qp.load(db)
    assert qp.load_for_item(db, {"quality_profile_id": entry["id"]}) == qp.load(db)


def test_load_for_item_honors_the_item_assignment(db):
    entry = _named_4k(db)
    assert qp.load_for_item(db, {"quality_profile_id": entry["id"]})["cutoff_resolution"] == "2160p"
    assert qp.load_for_item(db, {})["cutoff_resolution"] == qp.load(db)["cutoff_resolution"]


# ---------------------------------------------------------------------------
# Per-title assignment + query annotation
# ---------------------------------------------------------------------------

def test_assignment_stamps_title_and_wishlist_rows(db):
    entry = _named_4k(db)
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "tmdb_id": 693134})
    db.add_movie_to_wishlist(693134, "Dune")
    assert db.set_title_quality_profile("movie", mid, entry["id"]) is True
    assert db.quality_profile_id_for("movie", library_id=mid) == entry["id"]
    assert db.quality_profile_id_for("movie", tmdb_id=693134) == entry["id"]
    items = db.movie_wishlist_to_download()
    assert items and items[0]["quality_profile_id"] == entry["id"]
    # clearing goes back to Default everywhere
    assert db.set_title_quality_profile("movie", mid, 0) is True
    assert db.quality_profile_id_for("movie", library_id=mid) is None


def test_manual_search_items_carry_the_assignment(db):
    entry = _named_4k(db)
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Severance", "tmdb_id": 95396,
                                 "seasons": [{"season_number": 1, "episodes": [{"episode_number": 1}]}]})
    sid = db.query_library("shows")["items"][0]["id"]
    db.set_title_quality_profile("show", sid, entry["id"])
    db.add_episodes_to_wishlist(95396, "Severance", [{"season_number": 1, "episode_number": 1}])
    items = db.wishlist_manual_search_items("episode", 95396, season_number=1, episode_number=1)
    assert items and items[0]["quality_profile_id"] == entry["id"]


def test_download_record_carries_the_grab_profile(db):
    from core.automation.handlers.video_process_wishlist import build_download_record
    rec = build_download_record(
        {"tmdb_id": 1, "title": "Heat", "year": 1995, "quality_profile_id": 7},
        {"source": "soulseek", "username": "u", "filename": "f", "size_bytes": 1},
        [], media_type="movie", target_dir="/m", query="q")
    assert rec["quality_profile_id"] == 7
    did = db.add_video_download({**rec, "status": "downloading"})
    row = db.get_video_download(did)
    assert row["quality_profile_id"] == 7


def test_annotate_upgrades_judges_each_item_under_its_own_cutoff():
    from core.automation.handlers.video_process_wishlist import annotate_upgrades
    items = [
        {"tmdb_id": 1, "owned": 1, "owned_resolutions": "1080p", "quality_profile_id": None},
        {"tmdb_id": 2, "owned": 1, "owned_resolutions": "1080p", "quality_profile_id": 9},
    ]
    # global cutoff 1080p (met → dropped); profile 9's cutoff 2160p (below → kept)
    from core.video.quality_eval import resolution_rank
    cutoff_1080 = resolution_rank("1080p")

    def per_item(it):
        return resolution_rank("2160p") if it.get("quality_profile_id") == 9 else cutoff_1080

    out = annotate_upgrades(items, cutoff_1080, cutoff_for=per_item)
    assert [it["tmdb_id"] for it in out] == [2]
    assert out[0]["_min_rank"] == resolution_rank("1080p")


# ---------------------------------------------------------------------------
# API: profiles CRUD + per-title assignment + detail payload
# ---------------------------------------------------------------------------

def test_profiles_api_crud(client):
    out = client.get("/api/video/downloads/quality/profiles").get_json()
    assert out["profiles"][0]["id"] == 0
    created = client.post("/api/video/downloads/quality/profiles",
                          json={"name": "4K", "profile": {"cutoff_resolution": "2160p"}}).get_json()
    assert created["success"] and created["id"] >= 1
    assert client.delete("/api/video/downloads/quality/profiles/%d" % created["id"]).get_json()["success"]
    assert client.delete("/api/video/downloads/quality/profiles/0").status_code == 404


def test_assignment_endpoint_and_detail_payload(client, db):
    entry = _named_4k(db)
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Dune", "tmdb_id": 693134})
    r = client.put("/api/video/detail/movie/%d/quality-profile" % mid,
                   json={"profile_id": entry["id"]})
    assert r.get_json()["success"] is True
    detail = client.get("/api/video/detail/movie/%d" % mid).get_json()
    assert detail["quality_profile_id"] == entry["id"]
    assert client.put("/api/video/detail/movie/999999/quality-profile",
                      json={"profile_id": entry["id"]}).status_code == 404


# ---------------------------------------------------------------------------
# Monitor policies
# ---------------------------------------------------------------------------

class _FakeEngine:
    def __init__(self):
        self.detail = {"seasons": [{"season_number": 0}, {"season_number": 1}, {"season_number": 2}]}
        self.seasons = {
            1: {"episodes": [{"episode_number": 1, "title": "Pilot", "air_date": "2025-01-01"},
                             {"episode_number": 2, "title": "Two", "air_date": "2025-01-08"}]},
            2: {"episodes": [{"episode_number": 1, "title": "S2E1", "air_date": "2026-01-01"},
                             {"episode_number": 2, "title": "Future", "air_date": "2099-01-01"}]},
        }

    def tmdb_detail(self, kind, tmdb_id):
        return self.detail

    def tmdb_season(self, tmdb_id, sn):
        return self.seasons.get(sn)


def test_policy_expansion_covers_the_matrix():
    from core.video.monitor_policy import episodes_for_policy
    eng = _FakeEngine()
    today = "2026-07-16"
    assert episodes_for_policy(eng, 1, "future", today) == []
    all_eps = episodes_for_policy(eng, 1, "all", today)
    assert [(e["season_number"], e["episode_number"]) for e in all_eps] == [(1, 1), (1, 2), (2, 1)]
    assert [(e["season_number"], e["episode_number"])
            for e in episodes_for_policy(eng, 1, "first_season", today)] == [(1, 1), (1, 2)]
    assert [(e["season_number"], e["episode_number"])
            for e in episodes_for_policy(eng, 1, "latest_season", today)] == [(2, 1)]   # future ep excluded
    assert [(e["season_number"], e["episode_number"])
            for e in episodes_for_policy(eng, 1, "pilot", today)] == [(1, 1)]


def test_policy_engine_failure_degrades_to_empty():
    from core.video.monitor_policy import episodes_for_policy

    class _Boom:
        def tmdb_detail(self, *a):
            raise RuntimeError("tmdb down")
    assert episodes_for_policy(_Boom(), 1, "all", "2026-07-16") == []


def test_follow_with_policy_wishes_the_back_catalog(client, db, monkeypatch):
    import core.video.enrichment.engine as eng_mod
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _FakeEngine())
    out = client.post("/api/video/watchlist/add",
                      json={"kind": "show", "tmdb_id": 42, "title": "Poker Face",
                            "monitor": "first_season"}).get_json()
    assert out["success"] is True and out["wished"] == 2
    assert db.wishlist_counts().get("episode") == 2
    # default follow stays exactly as before
    out2 = client.post("/api/video/watchlist/add",
                       json={"kind": "show", "tmdb_id": 43, "title": "Slow Horses"}).get_json()
    assert out2["success"] is True and out2["wished"] == 0


# ---------------------------------------------------------------------------
# Frontend contracts
# ---------------------------------------------------------------------------

def test_manage_panel_has_the_profile_picker():
    assert "data-vmg-quality-profile" in _MANAGE_JS
    assert "loadQualityProfiles(d)" in _MANAGE_JS
    assert "/quality-profile'" in _MANAGE_JS       # the PUT assign call


def test_settings_editor_grew_the_profile_bar():
    assert "data-vq-profile-select" in _SETTINGS_JS
    assert "QUALITY_URL + '/profiles'" in _SETTINGS_JS
    assert "showConfirmDialog" in _SETTINGS_JS     # delete is confirm-gated (house rule)


def test_follow_menu_is_shows_only_with_future_default():
    assert "followMenu(b)" in _WLBTN_JS
    assert "kind === 'show'" in _WLBTN_JS
    fm = _WLBTN_JS.split("var MONITOR_OPTIONS")[1]
    assert fm.index("'future'") < fm.index("'all'")     # default listed first
    assert "body.monitor = monitor" in _WLBTN_JS
    assert ".vwl-menu" in _CSS
