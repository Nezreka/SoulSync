"""Seam tests for the video BACKFILL workers (artwork / subtitles / no-key
YouTube extras). The network fetch is stubbed so the loop/queue/record/status
logic is tested without hitting fanart.tv / OpenSubtitles / RYD / SponsorBlock.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from database.video_database import VideoDatabase
from core.video.enrichment.backfill import (
    RydWorker, SponsorBlockWorker, FanartWorker, OpenSubtitlesWorker, TraktWorker, TVmazeWorker,
    AniListWorker, DeArrowWorker, WikidataWorker, VideoBackfillWorker, _RateLimited, _Unauthorized,
    build_backfill_workers,
)


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


# ── DB seams ──────────────────────────────────────────────────────────────────
def test_youtube_enrich_queue_apply_breakdown(db):
    db.cache_channel_videos("UC1", [{"youtube_id": "a", "title": "A"},
                                    {"youtube_id": "b", "title": "B"}])
    assert db.youtube_enrich_breakdown("ryd_status")["video"]["pending"] == 2
    nxt = db.youtube_enrich_next("ryd_status")
    assert nxt["youtube_id"] in ("a", "b") and nxt["kind"] == "video"

    db.apply_youtube_votes("a", 100, 5, "ok")
    db.apply_youtube_votes("b", None, None, "not_found")
    bd = db.youtube_enrich_breakdown("ryd_status")["video"]
    assert bd == {"matched": 1, "not_found": 1, "errors": 0, "pending": 0}
    # likes/dislikes merge onto the cached catalog on read
    vids = {v["youtube_id"]: v for v in db.get_channel_videos("UC1")}
    assert vids["a"]["like_count"] == 100 and vids["a"]["dislike_count"] == 5


def test_youtube_enrich_shared_video_counted_once(db):
    # Same video cached under two channels/playlists → one enrichment unit.
    db.cache_channel_videos("UC1", [{"youtube_id": "x", "title": "X"}])
    db.cache_channel_videos("PLfoo", [{"youtube_id": "x", "title": "X"}])
    assert db.youtube_enrich_breakdown("sb_status")["video"]["pending"] == 1


def test_youtube_segments_stored_and_read(db):
    db.cache_channel_videos("UC1", [{"youtube_id": "v", "title": "V"}])
    segs = [{"category": "sponsor", "start_sec": 10.0, "end_sec": 20.0, "votes": 3, "uuid": "u1"}]
    db.apply_youtube_segments("v", segs, "ok")
    got = db.youtube_video_segments("v")
    assert got == [{"category": "sponsor", "start_sec": 10.0, "end_sec": 20.0, "votes": 3}]


def test_backfill_next_mark_breakdown(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Fight Club", "year": 1999})
    with db.connect() as c:
        c.execute("UPDATE movies SET tmdb_id=550, imdb_id='tt0137523' WHERE id=?", (mid,))
        c.commit()
    nxt = db.backfill_next("fanart")
    assert nxt["kind"] == "movie" and nxt["tmdb_id"] == 550

    db.backfill_mark("fanart", "movie", mid, "ok",
                     columns={"logo_url": "http://x/l.png", "bogus_col": "nope"})
    with db.connect() as c:
        r = c.execute("SELECT logo_url, fanart_status FROM movies WHERE id=?", (mid,)).fetchone()
    assert r["logo_url"] == "http://x/l.png" and r["fanart_status"] == "ok"   # whitelist drops bogus_col
    assert db.backfill_breakdown("fanart")["movie"]["matched"] == 1


def test_backfill_mark_never_clobbers(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "M"})
    with db.connect() as c:
        c.execute("UPDATE movies SET tmdb_id=1, logo_url='SERVER_LOGO' WHERE id=?", (mid,))
        c.commit()
    db.backfill_mark("fanart", "movie", mid, "ok", columns={"logo_url": "FANART_LOGO"})
    with db.connect() as c:
        r = c.execute("SELECT logo_url FROM movies WHERE id=?", (mid,)).fetchone()
    assert r["logo_url"] == "SERVER_LOGO"   # gap-fill only


def test_backfill_show_requires_tvdb_id(db):
    with db.connect() as c:
        c.execute("INSERT INTO shows (title, year) VALUES ('S', 2008)")
        sid = c.execute("SELECT id FROM shows WHERE title='S'").fetchone()["id"]
        c.commit()
    # No tvdb_id → fanart has nothing to key on, so it's not queued.
    assert db.backfill_next("fanart") is None
    with db.connect() as c:
        c.execute("UPDATE shows SET tvdb_id=81189 WHERE id=?", (sid,))
        c.commit()
    assert db.backfill_next("fanart")["kind"] == "show"


# ── worker loop seams (stubbed fetch) ─────────────────────────────────────────
def _seed_video(db):
    db.cache_channel_videos("UC1", [{"youtube_id": "v", "title": "V"}])


def test_ryd_worker_records_each_outcome(db):
    _seed_video(db)
    w = RydWorker(db)
    w.fetch = lambda item: {"likes": 9, "dislikes": 2}
    assert w.process_one() is True
    assert db.youtube_enrich_breakdown("ryd_status")["video"]["matched"] == 1
    assert w.stats["matched"] == 1


def test_ryd_worker_empty_marks_not_found(db):
    _seed_video(db)
    w = RydWorker(db)
    w.fetch = lambda item: None
    w.process_one()
    assert db.youtube_enrich_breakdown("ryd_status")["video"]["not_found"] == 1


def test_worker_call_error_records_error_not_notfound(db):
    _seed_video(db)
    w = SponsorBlockWorker(db)

    def boom(item):
        raise RuntimeError("network")
    w.fetch = boom
    w.process_one()
    bd = db.youtube_enrich_breakdown("sb_status")["video"]
    assert bd["errors"] == 1 and bd["not_found"] == 0
    assert w.stats["errors"] == 1


def test_rate_limit_sets_cooldown_and_pauses_pending(db):
    _seed_video(db)
    w = SponsorBlockWorker(db)

    def limited(item):
        raise _RateLimited(30)
    w.fetch = limited
    assert w.process_one() is False           # not consumed; will retry
    assert w._cooldown_until > 0
    assert w.get_stats()["cooldown"] is True


def test_unauthorized_pauses_worker(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "M"})
    with db.connect() as c:
        c.execute("UPDATE movies SET tmdb_id=1 WHERE id=?", (mid,))
        c.commit()
    db.set_setting("fanart_api_key", "KEY")
    w = FanartWorker(db)

    def denied(item):
        raise _Unauthorized()
    w.fetch = denied
    w.process_one()
    assert w.paused is True and "key" in (w.note or "").lower()


def test_key_gated_workers_disabled_without_key(db):
    assert FanartWorker(db).enabled is False
    assert OpenSubtitlesWorker(db).enabled is False
    db.set_setting("fanart_api_key", "KEY")
    assert FanartWorker(db).enabled is True


def test_nokey_workers_enabled_by_default_and_toggle(db):
    assert RydWorker(db).enabled is True
    db.set_setting("ryd_enabled", "0")
    assert RydWorker(db).enabled is False


def test_get_stats_shape_matches_matcher_worker(db):
    _seed_video(db)
    stats = RydWorker(db).get_stats()
    assert set(stats) == {"enabled", "needs_key", "running", "paused", "idle", "current_item",
                          "note", "cooldown", "stats", "progress", "breakdown"}
    assert set(stats["stats"]) == {"matched", "not_found", "errors", "pending"}


def test_build_backfill_workers_set(db):
    assert set(build_backfill_workers(db)) == {
        "ryd", "sponsorblock", "fanart", "opensubtitles",
        "trakt", "tvmaze", "anilist", "dearrow", "wikidata"}


# ── Wikidata (no-key, official-website lookup by imdb id) ─────────────────────
def test_wikidata_queue_keyed_on_imdb(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Fight Club", "year": 1999})
    assert db.backfill_next("wikidata") is None              # no imdb id → not queued
    with db.connect() as c:
        c.execute("UPDATE movies SET imdb_id='tt0137523' WHERE id=?", (mid,))
        c.commit()
    assert db.backfill_next("wikidata")["kind"] == "movie"


def test_wikidata_worker_records_url(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "M"})
    with db.connect() as c:
        c.execute("UPDATE movies SET imdb_id='tt1' WHERE id=?", (mid,))
        c.commit()
    w = WikidataWorker(db)
    assert w.enabled is True
    w.fetch = lambda item: {"wikidata_url": "https://example.com"}
    assert w.process_one() is True
    with db.connect() as c:
        r = c.execute("SELECT wikidata_url, wikidata_status FROM movies WHERE id=?", (mid,)).fetchone()
    assert r["wikidata_url"] == "https://example.com" and r["wikidata_status"] == "ok"


def test_wikidata_fetch_two_step_lookup(db, monkeypatch):
    import core.video.enrichment.backfill as bf

    def fake(url, params=None, headers=None, timeout=12):
        if (params or {}).get("action") == "query":
            return {"query": {"search": [{"title": "Q190050"}]}}
        return {"entities": {"Q190050": {"claims": {"P856": [
            {"mainsnak": {"datavalue": {"value": "https://officialsite.example"}}}]}}}}

    monkeypatch.setattr(bf, "_http_get_json", fake)
    out = WikidataWorker(db).fetch({"kind": "movie", "imdb_id": "tt0137523"})
    assert out == {"wikidata_url": "https://officialsite.example"}


# ── DeArrow (no-key, YouTube crowd titles) ────────────────────────────────────
def test_dearrow_queue_and_apply(db):
    db.cache_channel_videos("UC1", [{"youtube_id": "v", "title": "clickbait TITLE!!!"}])
    nxt = db.youtube_enrich_next("dearrow_status")
    assert nxt and nxt["youtube_id"] == "v" and nxt["kind"] == "video"
    db.apply_youtube_dearrow("v", "A calm, accurate title", "ok")
    assert db.youtube_video_dearrow_title("v") == "A calm, accurate title"
    assert db.youtube_enrich_breakdown("dearrow_status")["video"]["matched"] == 1


def test_dearrow_worker_records_title(db):
    db.cache_channel_videos("UC1", [{"youtube_id": "v", "title": "X"}])
    w = DeArrowWorker(db)
    assert w.enabled is True
    w.fetch = lambda item: {"title": "Better Crowd Title"}
    assert w.process_one() is True
    assert db.youtube_video_dearrow_title("v") == "Better Crowd Title"


def test_dearrow_fetch_parses_branding(db, monkeypatch):
    import core.video.enrichment.backfill as bf
    branding = {"titles": [
        {"title": "Original", "original": True},
        {"title": "Crowd Title", "original": False},
    ]}
    monkeypatch.setattr(bf, "_http_get_json",
                        lambda url, params=None, headers=None, timeout=12: branding)
    out = DeArrowWorker(db).fetch({"youtube_id": "v"})
    assert out == {"title": "Crowd Title"}


# ── Trakt (community rating backfill, keyed on imdb id) ────────────────────────
def test_trakt_queue_keyed_on_imdb(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "Fight Club", "year": 1999})
    assert db.backfill_next("trakt") is None              # no imdb id → not queued
    with db.connect() as c:
        c.execute("UPDATE movies SET imdb_id='tt0137523' WHERE id=?", (mid,))
        c.commit()
    nxt = db.backfill_next("trakt")
    assert nxt["kind"] == "movie" and nxt["imdb_id"] == "tt0137523"


def test_trakt_worker_records_rating(db):
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "M"})
    with db.connect() as c:
        c.execute("UPDATE movies SET imdb_id='tt1' WHERE id=?", (mid,))
        c.commit()
    w = TraktWorker(db)
    w.fetch = lambda item: {"trakt_rating": 8.2, "trakt_votes": 1234}
    assert w.process_one() is True
    with db.connect() as c:
        r = c.execute("SELECT trakt_rating, trakt_votes, trakt_status FROM movies WHERE id=?", (mid,)).fetchone()
    assert r["trakt_rating"] == 8.2 and r["trakt_votes"] == 1234 and r["trakt_status"] == "ok"
    assert db.backfill_breakdown("trakt")["movie"]["matched"] == 1


def test_trakt_fetch_parses_summary(db, monkeypatch):
    db.set_setting("trakt_api_key", "client-id")
    import core.video.enrichment.backfill as bf
    monkeypatch.setattr(bf, "_http_get_json",
                        lambda url, params=None, headers=None, timeout=12: {"rating": 8.234, "votes": 5000})
    w = TraktWorker(db)
    out = w.fetch({"kind": "movie", "imdb_id": "tt0137523"})
    assert out == {"trakt_rating": 8.2, "trakt_votes": 5000}   # rounded to 1dp


def test_trakt_fetch_needs_imdb_and_key(db, monkeypatch):
    import core.video.enrichment.backfill as bf
    monkeypatch.setattr(bf, "_http_get_json", lambda *a, **k: {"rating": 9})
    w = TraktWorker(db)
    assert w.fetch({"kind": "movie", "imdb_id": "tt1"}) is None   # no key configured
    db.set_setting("trakt_api_key", "k")
    assert w.fetch({"kind": "movie", "imdb_id": "550"}) is None   # not a tt-id


# ── TVmaze (no-key, TV-only community rating) ─────────────────────────────────
def test_tvmaze_is_show_only_and_enabled_by_default(db):
    w = TVmazeWorker(db)
    assert w.enabled is True                              # keyless → on by default
    db.set_setting("tvmaze_enabled", "0")
    assert w.enabled is False
    # No movie entry in the backfill map → movies are never queued for tvmaze.
    mid = db.upsert_movie("plex", {"server_id": "m1", "title": "M"})
    with db.connect() as c:
        c.execute("UPDATE movies SET imdb_id='tt1' WHERE id=?", (mid,))
        c.commit()
    assert db.backfill_next("tvmaze") is None


def test_tvmaze_worker_records_rating(db):
    with db.connect() as c:
        c.execute("INSERT INTO shows (title, year) VALUES ('S', 2008)")
        sid = c.execute("SELECT id FROM shows WHERE title='S'").fetchone()["id"]
        c.execute("UPDATE shows SET imdb_id='tt0903747' WHERE id=?", (sid,))
        c.commit()
    nxt = db.backfill_next("tvmaze")
    assert nxt["kind"] == "show"
    w = TVmazeWorker(db)
    w.fetch = lambda item: {"tvmaze_rating": 9.3}
    assert w.process_one() is True
    with db.connect() as c:
        r = c.execute("SELECT tvmaze_rating, tvmaze_status FROM shows WHERE id=?", (sid,)).fetchone()
    assert r["tvmaze_rating"] == 9.3 and r["tvmaze_status"] == "ok"
    assert db.backfill_breakdown("tvmaze")["show"]["matched"] == 1


def test_tvmaze_fetch_parses_lookup(db, monkeypatch):
    import core.video.enrichment.backfill as bf
    monkeypatch.setattr(bf, "_http_get_json",
                        lambda url, params=None, headers=None, timeout=12: {"rating": {"average": 9.34}})
    out = TVmazeWorker(db).fetch({"kind": "show", "imdb_id": "tt0903747"})
    assert out == {"tvmaze_rating": 9.3}


# ── AniList (no-key GraphQL, anime score, opt-in + title-match guard) ──────────
def test_anilist_off_by_default(db):
    w = AniListWorker(db)
    assert w.enabled is False                            # opt-in (anime-niche)
    db.set_setting("anilist_enabled", "1")
    assert w.enabled is True


def test_anilist_fetch_matches_title_and_scores(db, monkeypatch):
    import core.video.enrichment.backfill as bf
    payload = {"data": {"Media": {"averageScore": 85,
                                   "title": {"romaji": "Cowboy Bebop", "english": "Cowboy Bebop"}}}}
    monkeypatch.setattr(bf, "_http_post_json", lambda url, body, headers=None, timeout=12: payload)
    out = AniListWorker(db).fetch({"kind": "show", "title": "Cowboy Bebop"})
    assert out == {"anilist_score": 85}


def test_anilist_rejects_title_mismatch(db, monkeypatch):
    # AniList returns SOME anime for a non-anime title → the guard must reject it.
    import core.video.enrichment.backfill as bf
    payload = {"data": {"Media": {"averageScore": 90,
                                   "title": {"romaji": "Naruto", "english": "Naruto"}}}}
    monkeypatch.setattr(bf, "_http_post_json", lambda url, body, headers=None, timeout=12: payload)
    assert AniListWorker(db).fetch({"kind": "show", "title": "The Office"}) is None


def test_anilist_worker_records_score(db):
    with db.connect() as c:
        c.execute("INSERT INTO shows (title, year) VALUES ('Bebop', 1998)")
        sid = c.execute("SELECT id FROM shows WHERE title='Bebop'").fetchone()["id"]
        c.commit()
    w = AniListWorker(db)
    w.fetch = lambda item: {"anilist_score": 87}
    assert w.process_one() is True
    with db.connect() as c:
        r = c.execute("SELECT anilist_score, anilist_status FROM shows WHERE id=?", (sid,)).fetchone()
    assert r["anilist_score"] == 87 and r["anilist_status"] == "ok"


def test_backfill_module_imports_nothing_from_music():
    path = Path(__file__).resolve().parent.parent / "core" / "video" / "enrichment" / "backfill.py"
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            assert "music" not in s.lower(), f"music import leaked: {s!r}"
