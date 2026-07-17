"""Series types + multi-episode files (arr-parity P8).

Sonarr knows three kinds of series and SoulSync treated them all as standard:
daily shows release by AIR DATE, anime by ABSOLUTE number — an SxxExx query
simply never finds those. And a multi-episode file (S01E01E02) used to be
rejected as "wrong episode". Covers: parsing spans + absolute tokens, the
scope gate, per-type query building (slskd + Prowlarr + the retry ladder),
the shows.series_type column + absolute-number derivation, the drain's
search context, the importer's span/date acceptance + span naming, the
admin gate on the new route (and the P2 quality-profile route it fixed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from core.video.release_parse import has_absolute_episode, parse_release
from core.video.quality_eval import evaluate_release
from core.video.quality_profile import default_profile
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


def _eval(name, **kw):
    return evaluate_release(parse_release(name), default_profile(), scope="episode", **kw)


# ── parsing: multi-episode spans ──────────────────────────────────────────────

@pytest.mark.parametrize("name,season,ep,end", [
    ("Show.S01E01E02.1080p.WEB-DL", 1, 1, 2),
    ("Show S01E01-E03 720p HDTV", 1, 1, 3),
    ("Show.S02E05-06.x265", 2, 5, 6),
    ("Show.S01E01.E02.WEB", 1, 1, 2),
    ("Show S01E01E02E03 1080p", 1, 1, 3),
])
def test_parse_multi_episode_spans(name, season, ep, end):
    p = parse_release(name)
    assert (p["season"], p["episode"], p["episode_end"]) == (season, ep, end)
    assert not p["is_season_pack"]


def test_span_never_swallows_a_resolution_or_nonsense():
    # 'S01E01-1080p' is a SINGLE episode; a backwards span degrades to its start.
    p = parse_release("Show.S01E01-1080p.WEB")
    assert (p["episode"], p["episode_end"]) == (1, None)
    p2 = parse_release("Show.S01E05-04.WEB")
    assert (p2["episode"], p2["episode_end"]) == (5, None)
    p3 = parse_release("Show.S03E07.1080p.WEB")        # plain single unchanged
    assert (p3["season"], p3["episode"], p3["episode_end"]) == (3, 7, None)


# ── parsing: absolute numbers (anime) ─────────────────────────────────────────

@pytest.mark.parametrize("name,n,hit", [
    ("[SubsPlease] One Piece - 1071 (1080p) [ABCD].mkv", 1071, True),
    ("One.Piece.E1071.1080p.WEB", 1071, True),
    ("[Group] Naruto - 0523v2 [720p]", 523, True),
    ("[SubsPlease] One Piece - 1072 (1080p)", 1071, False),
    # audio-channel digits live AFTER the quality boundary — never a match
    ("Show.1080p.DDP5.1.WEB", 1, False),
    ("Show - 12 (1080p)", None, False),
    ("Show - 12 (1080p)", 0, False),
])
def test_has_absolute_episode(name, n, hit):
    assert has_absolute_episode(name, n) is hit


# ── the scope gate ────────────────────────────────────────────────────────────

def test_gate_multi_episode_file_satisfies_any_episode_it_spans():
    ok = _eval("Show.S01E01E02.1080p.WEB", want_season=1, want_episode=2)
    assert ok["accepted"]
    out = _eval("Show.S01E01E02.1080p.WEB", want_season=1, want_episode=4)
    assert not out["accepted"] and "Wrong episode" in out["rejected"]
    wrong_season = _eval("Show.S02E01E02.1080p.WEB", want_season=1, want_episode=1)
    assert not wrong_season["accepted"]


def test_gate_anime_absolute_number_is_an_episode_identity():
    ok = _eval("[SubsPlease] One Piece - 1071 (1080p)",
               want_season=20, want_episode=45, want_absolute=1071)
    assert ok["accepted"]
    miss = _eval("[SubsPlease] One Piece - 1072 (1080p)",
                 want_season=20, want_episode=45, want_absolute=1071)
    assert not miss["accepted"] and "Not a single episode" in miss["rejected"]
    # without want_absolute nothing changes for standard shows
    std = _eval("[SubsPlease] One Piece - 1071 (1080p)", want_season=20, want_episode=45)
    assert not std["accepted"]


def test_gate_s00_specials_flow_through():
    ok = _eval("Show.S00E05.1080p.WEB", want_season=0, want_episode=5)
    assert ok["accepted"]
    wrong = _eval("Show.S00E05.1080p.WEB", want_season=0, want_episode=6)
    assert not wrong["accepted"]


# ── query building per series type ────────────────────────────────────────────

def test_build_query_speaks_the_series_type():
    from core.video.slskd_search import build_query
    std = build_query("episode", "Breaking Bad", season=1, episode=2)
    assert std == "Breaking Bad S01E02"
    daily = build_query("episode", "The Daily Show", season=30, episode=88,
                        air_date="2026-07-08", series_type="daily")
    assert daily == "The Daily Show 2026.07.08"
    anime = build_query("episode", "One Piece", season=20, episode=45,
                        absolute=1071, series_type="anime")
    assert anime == "One Piece 1071"
    # anime with no derivable absolute falls back to SxxExx
    fallback = build_query("episode", "One Piece", season=20, episode=45,
                           series_type="anime")
    assert fallback == "One Piece S20E45"


def test_prowlarr_strategies_add_the_typed_text_query():
    from core.video.prowlarr_search import build_strategies
    strat = build_strategies("episode", "The Daily Show", season=30, episode=88,
                             air_date="2026-07-08", series_type="daily")
    queries = [q for (t, q, _x) in strat if t == "search"]
    assert "The Daily Show 2026.07.08" in queries
    assert "The Daily Show S30E88" in queries          # standard text query kept


def test_retry_ladder_front_loads_the_series_type_identity():
    from core.video.retry import next_query
    daily_ctx = {"scope": "episode", "title": "The Daily Show", "season": 30,
                 "episode": 88, "air_date": "2026-07-08", "series_type": "daily"}
    assert next_query(daily_ctx, []) == "The Daily Show 2026.07.08"
    anime_ctx = {"scope": "episode", "title": "One Piece", "season": 20,
                 "episode": 45, "absolute": 1071, "series_type": "anime"}
    assert next_query(anime_ctx, []) == "One Piece 1071"
    # standard shows lead with SxxExx exactly as before
    std_ctx = {"scope": "episode", "title": "Breaking Bad", "season": 1, "episode": 2}
    assert next_query(std_ctx, []) == "Breaking Bad S01E02"
    # the ladder still falls back to SxxExx for a daily show
    assert "S30E88" in next_query(daily_ctx, ["The Daily Show 2026.07.08",
                                              "The Daily Show 2026 07 08"])


# ── DB: series_type column + absolute numbers ─────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _seed_show(db, tmdb_id=100, eps_per_season=(3, 3)):
    seasons = []
    for si, n in enumerate(eps_per_season, start=1):
        seasons.append({"season_number": si, "episodes": [
            {"episode_number": e, "title": "E%d" % e} for e in range(1, n + 1)]})
    # specials must never count toward absolute numbering
    seasons.append({"season_number": 0, "episodes": [
        {"episode_number": 1, "title": "Special"}]})
    return db.upsert_show_tree("plex", {"server_id": "s%d" % tmdb_id, "title": "Anime",
                                        "tmdb_id": tmdb_id, "seasons": seasons})


def test_series_type_round_trip_and_validation(db):
    sid = _seed_show(db)
    assert db.set_show_series_type(sid, "anime") is True
    assert db.show_detail(sid)["series_type"] == "anime"
    assert db.set_show_series_type(sid, "standard") is True
    assert db.show_detail(sid)["series_type"] == "standard"   # stored as NULL
    assert db.set_show_series_type(sid, "weekly") is False
    assert db.set_show_series_type(999999, "anime") is False


def test_episode_absolute_number_spans_seasons_and_skips_specials(db):
    _seed_show(db, tmdb_id=100, eps_per_season=(3, 3))
    assert db.episode_absolute_number(100, 1, 1) == 1
    assert db.episode_absolute_number(100, 1, 3) == 3
    assert db.episode_absolute_number(100, 2, 1) == 4         # crosses the season line
    assert db.episode_absolute_number(100, 2, 3) == 6
    assert db.episode_absolute_number(100, 0, 1) is None      # specials have no absolute
    assert db.episode_absolute_number(100, 3, 1) is None      # unknown episode → None
    assert db.episode_absolute_number(999, 1, 1) is None      # unknown show → None


def test_absolute_numbers_survive_a_multi_server_mirror(db):
    # the same show scanned from two servers must not double-count episodes
    _seed_show(db, tmdb_id=100, eps_per_season=(3, 3))
    db.upsert_show_tree("jellyfin", {"server_id": "j100", "title": "Anime",
                                     "tmdb_id": 100, "seasons": [
        {"season_number": 1, "episodes": [
            {"episode_number": e, "title": "E%d" % e} for e in (1, 2, 3)]},
        {"season_number": 2, "episodes": [
            {"episode_number": e, "title": "E%d" % e} for e in (1, 2, 3)]}]})
    assert db.episode_absolute_number(100, 2, 1) == 4


def test_wishlist_queries_carry_series_type(db):
    sid = _seed_show(db, tmdb_id=200)
    db.set_show_series_type(sid, "daily")
    db.add_episodes_to_wishlist(200, "Anime", [
        {"season_number": 1, "episode_number": 1, "air_date": "2026-07-01"}])
    items = db.episode_wishlist_to_download()
    assert items and items[0]["series_type"] == "daily"
    manual = db.wishlist_manual_search_items("episode", 200, 1, 1)
    assert manual and manual[0]["series_type"] == "daily"


# ── the drain's search context ────────────────────────────────────────────────

def test_search_context_resolves_the_absolute_number(tmp_path):
    import api.video as videoapi
    from core.automation.handlers.video_process_wishlist import search_context
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    videoapi._video_db = db
    try:
        sid = _seed_show(db, tmdb_id=300, eps_per_season=(3, 3))
        db.set_show_series_type(sid, "anime")
        item = {"show_title": "Anime", "show_tmdb_id": 300, "season_number": 2,
                "episode_number": 1, "air_date": "2026-07-01", "series_type": "anime"}
        ctx = search_context(item, "episode")
        assert ctx["series_type"] == "anime" and ctx["absolute"] == 4
        # a daily show carries its type; standard items carry neither key
        ctx2 = search_context({**item, "series_type": "daily"}, "episode")
        assert ctx2["series_type"] == "daily" and "absolute" not in ctx2
        ctx3 = search_context({**item, "series_type": None}, "episode")
        assert "series_type" not in ctx3 and "absolute" not in ctx3
    finally:
        videoapi._video_db = None


# ── importer: spans + date-named dailies ──────────────────────────────────────

def _episode_dl(release, season=1, episode=1, air_date=None):
    ctx = {"scope": "episode", "title": "Show", "season": season, "episode": episode,
           "episode_title": "Ep"}
    if air_date:
        ctx["air_date"] = air_date
    return {"kind": "show", "title": "Show", "source": "soulseek",
            "release_title": release, "size_bytes": 1_000_000_000,
            "target_dir": "/lib/tv", "search_ctx": json.dumps(ctx)}


def test_import_accepts_a_multi_episode_file_and_names_the_span():
    from core.video import importer
    dl = _episode_dl("Show.S01E01E02.1080p.WEB-DL", season=1, episode=2)
    p = importer.plan_import(dl, "/dl/x/show.s01e01e02.mkv", list_dir=lambda d: [])
    assert p["action"] == "import"
    assert "S01E01-E02" in p["dest"]["filename"]
    out = importer.plan_import(_episode_dl("Show.S01E01E02.WEB", season=1, episode=4),
                               "/dl/x/show.mkv", list_dir=lambda d: [])
    assert out["action"] == "reject"


def test_import_accepts_a_date_named_daily_file():
    from core.video import importer
    dl = _episode_dl("Show.2026.07.08.Guest.1080p.WEB", season=30, episode=88,
                     air_date="2026-07-08")
    p = importer.plan_import(dl, "/dl/x/show.2026.07.08.mkv", list_dir=lambda d: [])
    assert p["action"] == "import"
    # a date-named release with an SxxExx that contradicts the wanted episode
    # still rejects — the date only RESCUES, it never overrides real numbering
    contradiction = _episode_dl("Show.S05E01.2026.07.08.WEB", season=30, episode=88,
                                air_date="2026-07-09")
    assert importer.plan_import(contradiction, "/dl/x/show.mkv",
                                list_dir=lambda d: [])["action"] == "reject"


def test_existing_span_file_is_found_for_a_covered_episode():
    from core.video.importer import _existing_match
    hit = _existing_match("episode", "/lib/tv/Show/Season 01",
                          {"season": 1, "episode": 2},
                          lambda d: ["Show - S01E01-E02 - Pilot 1080p WEB-DL.mkv"])
    assert hit == "Show - S01E01-E02 - Pilot 1080p WEB-DL.mkv"


def test_airdate_template_token():
    from core.video import organization
    settings = {**organization.DEFAULTS,
                "episode_template": "$series/Season $season/$series - $airdate - $episodetitle"}
    dest = organization.render_path("episode", "/tv", {
        "series": "The Daily Show", "season": 30, "episode": 88,
        "episode_title": "Guest", "air_date": "2026-07-08"}, settings, ".mkv")
    assert dest["filename"] == "The Daily Show - 2026-07-08 - Guest.mkv"


# ── API + gate ────────────────────────────────────────────────────────────────

def _client(tmp_path, *, is_admin):
    import api.video as videoapi
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        from flask import g
        g.is_admin = is_admin
        g.can_download = True
        g.allowed_sides = "both"

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), videoapi


def test_series_type_endpoint_round_trip(tmp_path):
    client, videoapi = _client(tmp_path, is_admin=True)
    try:
        sid = _seed_show(videoapi._video_db, tmdb_id=400)
        r = client.put("/api/video/detail/show/%d/series-type" % sid,
                       json={"series_type": "anime"})
        assert r.status_code == 200
        assert videoapi._video_db.show_detail(sid)["series_type"] == "anime"
        assert client.put("/api/video/detail/show/%d/series-type" % sid,
                          json={"series_type": "weekly"}).status_code == 400
        assert client.put("/api/video/detail/show/999999/series-type",
                          json={"series_type": "daily"}).status_code == 404
    finally:
        videoapi._video_db = None


def test_series_type_and_quality_profile_are_admin_only(tmp_path):
    # includes the P2 gap this phase closed: the quality-profile PUT was open
    client, videoapi = _client(tmp_path, is_admin=False)
    try:
        for url, body in [("/api/video/detail/show/1/series-type", {"series_type": "anime"}),
                          ("/api/video/detail/show/1/quality-profile", {"profile_id": 1})]:
            r = client.put(url, json=body)
            assert r.status_code == 403, "%s must be admin-only" % url
    finally:
        videoapi._video_db = None


# ── UI wiring ─────────────────────────────────────────────────────────────────

def test_manage_panel_wiring():
    js = (_ROOT / "webui" / "static" / "video" / "video-manage-panel.js").read_text(encoding="utf-8")
    assert "data-vmg-series-type" in js
    assert "setSeriesType" in js and "/series-type" in js
