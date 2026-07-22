"""Continue Watching P1 — per-episode watch state ingestion (schema v45).

The scan now carries per-episode play_count / last_viewed_at / view_offset_ms
(Plex viewCount/lastViewedAt/viewOffset; Jellyfin UserData) and movies gain a
resume offset. Watch state is server truth: every upsert takes the fresh
values (unlike added_at, which keeps the earliest). The detail payloads
expose watched/progress per episode and raw state on movies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video.db"))


def _ep(e, *, plays=0, offset=0, viewed=None):
    return {"server_id": "e%d" % e, "season_number": 1, "episode_number": e,
            "title": "E%d" % e, "air_date": "2026-07-%02d" % e,
            "play_count": plays, "last_viewed_at": viewed, "view_offset_ms": offset,
            "file": {"relative_path": "/tv/s1e%d.mkv" % e, "size_bytes": 100,
                     "resolution": "1080p"}}


def _tree(eps):
    return {"server_id": "sh", "title": "Show",
            "seasons": [{"season_number": 1, "episodes": eps}]}


class TestSchema:
    def test_new_columns_exist(self, db):
        conn = db._get_connection()
        ep_cols = {r["name"] for r in conn.execute("PRAGMA table_info(episodes)")}
        mv_cols = {r["name"] for r in conn.execute("PRAGMA table_info(movies)")}
        conn.close()
        assert {"play_count", "last_viewed_at", "view_offset_ms"} <= ep_cols
        assert "view_offset_ms" in mv_cols

    def test_columns_ride_the_migration_list(self):
        src = (_ROOT / "database" / "video_database.py").read_text(encoding="utf-8", errors="replace")
        for entry in ('("episodes", "play_count", "INTEGER")',
                      '("episodes", "last_viewed_at", "TEXT")',
                      '("episodes", "view_offset_ms", "INTEGER")',
                      '("movies", "view_offset_ms", "INTEGER")'):
            assert entry in src, f"missing migration {entry}"


class TestEpisodeIngest:
    def test_watch_state_round_trips_to_the_payload(self, db):
        show_id = db.upsert_show_tree("plex", _tree([
            _ep(1, plays=2, viewed="2026-07-16 21:00:00"),
            _ep(2, plays=0, offset=743_000),               # in progress
            _ep(3),                                        # untouched
        ]))
        eps = db.show_detail(show_id)["seasons"][0]["episodes"]
        assert eps[0]["watched"] is True and eps[0]["last_viewed_at"] == "2026-07-16 21:00:00"
        assert eps[1]["watched"] is False and eps[1]["view_offset_ms"] == 743_000
        assert eps[2]["watched"] is False and eps[2]["view_offset_ms"] == 0

    def test_rescan_always_takes_fresh_server_truth(self, db):
        # watched on the server, then marked UNwatched there — the rescan must
        # follow (watch state never sticks to a stale value)
        show_id = db.upsert_show_tree("plex", _tree([_ep(1, plays=1, offset=0)]))
        db.upsert_show_tree("plex", _tree([_ep(1, plays=0, offset=0)]))
        eps = db.show_detail(show_id)["seasons"][0]["episodes"]
        assert eps[0]["watched"] is False

    def test_enrichment_missing_rows_have_clean_state(self, db):
        show_id = db.upsert_show_tree("plex", _tree([_ep(1)]))
        db.backfill_episodes(show_id, 1, [{"episode_number": 9, "title": "E9",
                                           "air_date": "2026-09-01"}])
        eps = db.show_detail(show_id)["seasons"][0]["episodes"]
        e9 = [e for e in eps if e["episode_number"] == 9][0]
        assert e9["watched"] is False and e9["view_offset_ms"] == 0


class TestMoviePayload:
    def test_movie_exposes_raw_watch_state(self, db):
        mid = db.upsert_movie("plex", {
            "server_id": "m1", "title": "Film", "year": 2020, "tmdb_id": 7,
            "play_count": 1, "last_viewed_at": "2026-06-02 20:00:00",
            "view_offset_ms": 0,
            "file": {"relative_path": "/mov/f.mkv", "size_bytes": 10,
                     "resolution": "2160p"}})
        d = db.movie_detail(mid)
        assert d["watched"] is True
        assert d["play_count"] == 1
        assert d["last_viewed_at"] == "2026-06-02 20:00:00"
        assert d["view_offset_ms"] == 0

    def test_in_progress_movie_carries_offset(self, db):
        mid = db.upsert_movie("plex", {
            "server_id": "m2", "title": "Half Watched", "year": 2021, "tmdb_id": 8,
            "play_count": 0, "view_offset_ms": 3_600_000,
            "file": {"relative_path": "/mov/h.mkv", "size_bytes": 10,
                     "resolution": "1080p"}})
        d = db.movie_detail(mid)
        assert d["watched"] is False and d["view_offset_ms"] == 3_600_000


class TestNextUp:
    """show_next_up — the Netflix 'Next Up' slot behind the hero CTA."""

    def test_fresh_show_has_no_continue_story(self, db):
        show_id = db.upsert_show_tree("plex", _tree([_ep(1), _ep(2)]))
        assert db.show_next_up(show_id) is None
        assert db.show_detail(show_id)["next_up"] is None

    def test_in_progress_episode_wins_as_resume(self, db):
        show_id = db.upsert_show_tree("plex", _tree([
            _ep(1, plays=1, viewed="2026-07-10 21:00:00"),
            _ep(2, offset=600_000, viewed="2026-07-16 21:00:00"),
            _ep(3),
        ]))
        nu = db.show_next_up(show_id)
        assert (nu["season_number"], nu["episode_number"]) == (1, 2)
        assert nu["resume"] is True and nu["view_offset_ms"] == 600_000
        assert nu["server_id"] == "e2"

    def test_falls_to_first_unwatched_owned(self, db):
        show_id = db.upsert_show_tree("plex", _tree([
            _ep(1, plays=1), _ep(2, plays=1), _ep(3), _ep(4)]))
        nu = db.show_next_up(show_id)
        assert (nu["episode_number"], nu["resume"]) == (3, False)

    def test_unowned_episodes_never_suggested(self, db):
        # E2 exists only as a schedule FACT (no file) — next up must skip it.
        eps = [_ep(1, plays=1), _ep(3)]
        e2 = _ep(2)
        del e2["file"]
        show_id = db.upsert_show_tree("plex", _tree([eps[0], e2, eps[1]]))
        assert db.show_next_up(show_id)["episode_number"] == 3

    def test_everything_watched_means_none(self, db):
        show_id = db.upsert_show_tree("plex", _tree([_ep(1, plays=1), _ep(2, plays=2)]))
        assert db.show_next_up(show_id) is None


class TestExtrasEpisodeLink:
    def test_server_tile_gains_next_up_deep_link(self, db, monkeypatch):
        from core.video.enrichment.engine import VideoEnrichmentEngine
        show_id = db.upsert_show_tree("plex", _tree([
            _ep(1, plays=1), _ep(2)]))
        eng = VideoEnrichmentEngine(db, clients={})
        calls = []

        def fake_link(kind, item_id, episode_sid=None):
            calls.append(episode_sid)
            return {"server": "Plex",
                    "url": "https://plex/" + (str(episode_sid) if episode_sid else "show")}
        monkeypatch.setattr(eng, "_server_watch_link", fake_link)
        out = eng.item_extras("show", show_id)
        srv = out["server"]
        assert srv["url"] == "https://plex/show"          # title link intact
        assert srv["episode_url"] == "https://plex/e2"    # next-up deep link
        assert srv["next_up"] == {"season": 1, "episode": 2, "resume": False}
        assert calls == [None, "e2"]

    def test_no_next_up_leaves_plain_server_tile(self, db, monkeypatch):
        from core.video.enrichment.engine import VideoEnrichmentEngine
        show_id = db.upsert_show_tree("plex", _tree([_ep(1)]))   # nothing watched
        eng = VideoEnrichmentEngine(db, clients={})
        monkeypatch.setattr(eng, "_server_watch_link",
                            lambda kind, item_id, episode_sid=None: {"server": "Plex", "url": "u"})
        srv = eng.item_extras("show", show_id)["server"]
        assert "episode_url" not in srv and "next_up" not in srv


def test_source_maps_carry_the_fields():
    src = (_ROOT / "core" / "video" / "sources.py").read_text(encoding="utf-8", errors="replace")
    # plex episode + movie
    assert src.count('"view_offset_ms": int(getattr(') == 2
    assert 'int(getattr(ep, "viewCount", 0) or 0)' in src
    # jellyfin ticks → ms in both maps
    assert src.count('("PlaybackPositionTicks") or 0) // 10_000') == 2
