"""TMDB lumps some anime into one long season, TVDB splits it.

a user's Jujutsu Kaisen (oct 23 2026): TMDB has one season of 59, TVDB, plex,
the scene and the subtitle sites have 24/23/12. the episode sync filed TMDB's
S01E25-59 as missing rows next to the server's real S02/S03, so the wishlist
hunted S01E59 (nobody names it that, it's S03E12) and subtitle sites had
nothing for it. boulder's own library had the same 35 phantom rows.

the sync now files an episode TVDB's same season doesn't have under the TVDB
episode that aired that day, and retires the row it left behind.
"""

import datetime as dt

import pytest

from core.video import numbering
from core.video.enrichment.worker import VideoEnrichmentWorker
from database.video_database import VideoDatabase

NOW = dt.datetime(2026, 10, 25, 12, 0, 0)


def _weekly(start, n):
    d = dt.date.fromisoformat(start)
    return [(d + dt.timedelta(weeks=i)).isoformat() for i in range(n)]


# TVDB's split (the real shape: S3 opens with a double bill on 2026-01-09)
S1 = _weekly("2020-10-03", 24)
S2 = _weekly("2023-07-06", 23)
S3 = ["2026-01-09"] + _weekly("2026-01-09", 11)
TVDB_JJK = {
    0: [],
    1: list(enumerate(S1, 1)),
    2: list(enumerate(S2, 1)),
    3: list(enumerate(S3, 1)),
}
# TMDB's one season of 59, same air dates in order
TMDB_S1 = [{"episode_number": i, "title": "Ep %d" % i, "air_date": d}
           for i, d in enumerate(S1 + S2 + S3, 1)]


class FakeTVDB:
    enabled = True

    def __init__(self, seasons):
        self.seasons = seasons
        self.calls = []

    def season_episodes(self, series_id, season):
        self.calls.append(season)
        return [{"episode_number": e, "air_date": d} for e, d in self.seasons.get(season, [])]


class FakeTMDB:
    def season_episodes(self, tv_id, season):
        return {"episodes": [dict(e) for e in TMDB_S1]} if season == 1 else None


@pytest.fixture
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


@pytest.fixture
def tvdb(monkeypatch):
    fake = FakeTVDB(TVDB_JJK)
    monkeypatch.setattr(numbering, "_default_client", lambda _db: fake)
    return fake


def _server_show(db, layout):
    """a server show with files at {season: episode count}."""
    seasons = [{"season_number": s, "episodes": [
        {"server_id": "p%d-%d" % (s, e), "season_number": s, "episode_number": e,
         "title": "Episode %d" % e,
         "file": {"relative_path": "/tv/jjk/s%02de%02d.mkv" % (s, e), "size_bytes": 1}}
        for e in range(1, n + 1)]} for s, n in layout.items()]
    show_id = db.upsert_show_tree("plex", {"server_id": "jjk", "title": "JUJUTSU KAISEN",
                                           "seasons": seasons})
    conn = db._get_connection()
    try:
        conn.execute("UPDATE shows SET tmdb_id=95479, tvdb_id=377543 WHERE id=?", (show_id,))
        conn.commit()
    finally:
        conn.close()
    return show_id


def _eps(db, show_id):
    conn = db._get_connection()
    try:
        return {(r["season_number"], r["episode_number"]): dict(r) for r in conn.execute(
            "SELECT season_number, episode_number, server_id, has_file, air_date "
            "FROM episodes WHERE show_id=? AND season_number>0", (show_id,))}
    finally:
        conn.close()


def _sync(db, show_id):
    VideoEnrichmentWorker(db, "tmdb", FakeTMDB())._cascade_episodes(show_id, 95479, [1])


# -- the pure split ------------------------------------------------------------

def test_overflow_moves_to_the_tvdb_season_that_aired_it():
    groups, moved = numbering.tvdb_regroup(1, TMDB_S1, TVDB_JJK)
    assert [e["episode_number"] for e in groups[1]] == list(range(1, 25))
    assert [e["episode_number"] for e in groups[2]] == list(range(1, 24))
    assert [e["episode_number"] for e in groups[3]] == list(range(1, 13))
    assert moved[25] == (2, 1) and moved[59] == (3, 12)
    # the double bill keeps its order
    assert moved[48] == (3, 1) and moved[49] == (3, 2)


def test_a_season_tvdb_agrees_with_stays_put():
    groups, moved = numbering.tvdb_regroup(1, TMDB_S1[:24], TVDB_JJK)
    assert moved == {} and len(groups[1]) == 24


def test_unplaceable_overflow_keeps_its_tmdb_number():
    future = TMDB_S1 + [{"episode_number": 60, "air_date": "2026-04-03"}]
    groups, moved = numbering.tvdb_regroup(1, future, TVDB_JJK)
    assert 60 not in moved and groups[1][-1]["episode_number"] == 60


def test_tvdb_asked_past_the_season_only_when_it_overflows(db, tvdb):
    numbering.regroup_season(db, 377543, 1, TMDB_S1[:24])
    assert tvdb.calls == [1]
    numbering.regroup_season(db, 377543, 1, TMDB_S1)
    assert tvdb.calls == [1, 2, 3, 4]       # S1 from the cache, walks until TVDB runs out


def test_no_tvdb_id_changes_nothing(db, tvdb):
    groups, moved = numbering.regroup_season(db, None, 1, TMDB_S1)
    assert moved == {} and len(groups[1]) == 59 and tvdb.calls == []


# -- the sync, against the real db ---------------------------------------------

def test_sync_files_jjk_the_way_the_server_does(db, tvdb):
    show_id = _server_show(db, {1: 24, 2: 23, 3: 11})
    _sync(db, show_id)
    eps = _eps(db, show_id)
    assert not [k for k in eps if k[0] == 1 and k[1] > 24], "no phantom S01E25+"
    assert eps[(2, 1)]["has_file"] == 1 and eps[(2, 1)]["air_date"] == S2[0]
    # the one the server doesn't have is missing at the number releases carry
    assert eps[(3, 12)]["has_file"] == 0 and eps[(3, 12)]["air_date"] == S3[-1]
    assert len(eps) == 59


def test_resync_retires_rows_the_old_sync_left(db, tvdb, monkeypatch):
    show_id = _server_show(db, {1: 24, 2: 23, 3: 11})
    db.backfill_episodes(show_id, 1, TMDB_S1)              # what the old sync wrote
    assert (1, 59) in _eps(db, show_id)
    db.add_episodes_to_wishlist(95479, "JUJUTSU KAISEN", [
        {"season_number": 1, "episode_number": 59, "air_date": S3[-1]}])
    _sync(db, show_id)
    eps = _eps(db, show_id)
    assert not [k for k in eps if k[0] == 1 and k[1] > 24]
    assert len(eps) == 59
    conn = db._get_connection()
    try:
        wished = [tuple(r) for r in conn.execute(
            "SELECT season_number, episode_number FROM video_wishlist WHERE kind='episode'")]
    finally:
        conn.close()
    assert wished == [(3, 12)]


def test_a_server_numbering_it_tmdbs_way_is_left_alone(db, tvdb):
    show_id = _server_show(db, {1: 59})
    _sync(db, show_id)
    eps = _eps(db, show_id)
    assert len(eps) == 59 and all(k[0] == 1 for k in eps)


def test_v56_requeues_shows_with_the_phantom_shape(tmp_path):
    path = str(tmp_path / "old.db")
    db = VideoDatabase(database_path=path)
    show_id = _server_show(db, {1: 24, 2: 23, 3: 11})
    clean = db.upsert_show_tree("plex", {"server_id": "ok", "title": "Fine", "seasons": [
        {"season_number": 1, "episodes": [{"server_id": "f1", "season_number": 1,
                                           "episode_number": 1}]}]})
    db.backfill_episodes(show_id, 1, TMDB_S1)
    conn = db._get_connection()
    try:
        conn.execute("UPDATE shows SET tmdb_id=COALESCE(tmdb_id, 1), tvdb_id=COALESCE(tvdb_id, 2), "
                     "episodes_synced=1")
        conn.execute("PRAGMA user_version = 55")
        conn.commit()
    finally:
        conn.close()
    conn = db._get_connection()
    try:
        VideoDatabase._run_data_migrations(conn, 55)
        conn.commit()
        flags = dict(conn.execute("SELECT id, episodes_synced FROM shows").fetchall())
    finally:
        conn.close()
    assert flags[show_id] == 0 and flags[clean] == 1


def test_tvdb_worker_leaves_the_episode_sync_queue_alone(db):
    # the tvdb client has season_episodes too (a list, for gap-fill). the
    # tvdb worker used to take shows off tmdb's queue, choke and mark them
    # synced with nothing pulled, which ate half the v56 re-queue live.
    show_id = _server_show(db, {1: 24})
    worker = VideoEnrichmentWorker(db, "tvdb", FakeTVDB(TVDB_JJK))
    assert worker._sync_episodes_once() is False
    assert db.episode_sync_next()["id"] == show_id
