"""AniList splits a season TMDB lumped, no TVDB key needed (Thibs).

TMDB has Jujutsu Kaisen as one season of 59 and Frieren as one of 38. the
TVDB regroup splits them by air date, but only with a TVDB key. the anime id
map already says where each AniList entry starts inside the TMDB season and
which TVDB season it is, and AniList says how many episodes each has, so the
episode sync splits them from that when TVDB can't.
"""

import sqlite3

import pytest

from core.video import numbering
from core.video.enrichment.worker import VideoEnrichmentWorker
from database import video_database
from database.video_database import VideoDatabase
from tests.video.test_tvdb_regroup import TMDB_S1, FakeTMDB, _eps, _server_show


def _entry(aid, tvdb_season, tmdb_season, tmdb_offset, episodes, tvdb_offset=0, fmt="TV"):
    return {"anilist_id": aid, "tvdb_season": tvdb_season, "tvdb_offset": tvdb_offset,
            "tmdb_season": tmdb_season, "tmdb_offset": tmdb_offset,
            "episodes": episodes, "format": fmt}


# the map's real rows for JJK: season 2 still points at a TMDB season 2 that
# TMDB folded into season 1, season 3's two cours sit inside TMDB S1
JJK = [
    _entry(113415, 1, 1, 0, 24),
    _entry(145064, 2, 2, 0, 23),
    _entry(172463, 3, 1, 47, 12),
    _entry(209895, 3, 1, 59, None, tvdb_offset=12, fmt=None),
]
FRIEREN = [
    _entry(154587, 1, 1, 0, 28),
    _entry(182255, 2, 1, 28, 10),
    _entry(209939, 3, 1, 38, None),
]


def _season(n):
    return [{"episode_number": i, "title": "Ep %d" % i} for i in range(1, n + 1)]


# -- the pure split ------------------------------------------------------------

def test_jjk_splits_24_23_12_even_with_the_maps_stale_season_two():
    groups, moved = numbering.anilist_regroup(1, _season(59), JJK)
    assert [e["episode_number"] for e in groups[1]] == list(range(1, 25))
    assert [e["episode_number"] for e in groups[2]] == list(range(1, 24))
    assert [e["episode_number"] for e in groups[3]] == list(range(1, 13))
    assert moved[25] == (2, 1) and moved[47] == (2, 23) and moved[48] == (3, 1)
    assert moved[59] == (3, 12)


def test_frieren_splits_28_10():
    groups, moved = numbering.anilist_regroup(1, _season(38), FRIEREN)
    assert len(groups[1]) == 28 and [e["episode_number"] for e in groups[2]] == list(range(1, 11))
    assert moved[29] == (2, 1)


def test_counts_that_dont_add_up_leave_the_season_alone():
    wrong = [dict(e) for e in JJK]
    wrong[0]["episodes"] = 25          # 25 + 23 != 47
    groups, moved = numbering.anilist_regroup(1, _season(59), wrong)
    assert moved == {} and len(groups[1]) == 59


def test_an_unknown_count_in_the_gap_leaves_the_season_alone():
    unknown = [dict(e) for e in JJK]
    unknown[1]["episodes"] = None
    assert numbering.anilist_regroup(1, _season(59), unknown)[1] == {}


def test_a_show_tmdb_already_splits_is_left_alone():
    # one entry per TMDB season, each at offset 0: nothing to split
    split = [_entry(1, 1, 1, 0, 26), _entry(2, 2, 2, 0, 18), _entry(3, 3, 3, 0, 11)]
    assert numbering.anilist_regroup(1, _season(26), split)[1] == {}
    assert numbering.anilist_regroup(2, _season(18), split)[1] == {}


def test_movies_and_specials_never_take_episodes():
    with_movie = JJK + [_entry(131573, 0, None, 0, 1, fmt="MOVIE")]
    assert numbering.anilist_regroup(1, _season(59), with_movie)[1][59] == (3, 12)
    assert numbering.anilist_regroup(1, _season(59), [])[1] == {}


def test_a_season_that_does_not_start_at_its_own_number_is_left_alone():
    odd = [_entry(1, 2, 1, 0, 12), _entry(2, 3, 1, 12, 12)]
    assert numbering.anilist_regroup(1, _season(24), odd)[1] == {}


# -- the sync, against the real db, with no TVDB --------------------------------

@pytest.fixture
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "v.db"))


@pytest.fixture
def no_tvdb(monkeypatch):
    class _Off:
        enabled = False

        def season_episodes(self, *a):
            return []
    monkeypatch.setattr(numbering, "_default_client", lambda _db: _Off())


def _link(db, show_id, rows=JJK):
    db.save_anime_entries("show", show_id, rows)


def _sync(db, show_id):
    VideoEnrichmentWorker(db, "tmdb", FakeTMDB())._cascade_episodes(show_id, 95479, [1])


def test_sync_splits_jjk_by_anilist_without_a_tvdb_key(db, no_tvdb):
    show_id = _server_show(db, {1: 24, 2: 12})
    _link(db, show_id)
    _sync(db, show_id)
    eps = _eps(db, show_id)
    assert not [k for k in eps if k[0] == 1 and k[1] > 24], "no phantom S01E25+"
    assert eps[(2, 1)]["has_file"] == 1 and eps[(2, 23)]["has_file"] == 0
    assert eps[(3, 12)]["has_file"] == 0
    assert len(eps) == 59


def test_without_anilist_entries_the_season_stays_as_tmdb_has_it(db, no_tvdb):
    show_id = _server_show(db, {1: 24})
    _sync(db, show_id)
    assert (1, 59) in _eps(db, show_id)


def test_a_server_numbering_it_tmdbs_way_is_still_left_alone(db, no_tvdb):
    show_id = _server_show(db, {1: 36})
    _link(db, show_id)
    _sync(db, show_id)
    eps = _eps(db, show_id)
    assert len(eps) == 59 and all(k[0] == 1 for k in eps)


def test_tvdb_still_goes_first_when_it_can_split(db, monkeypatch):
    from tests.video.test_tvdb_regroup import TVDB_JJK, FakeTVDB
    fake = FakeTVDB(TVDB_JJK)
    monkeypatch.setattr(numbering, "_default_client", lambda _db: fake)
    calls = []
    real = numbering.anilist_regroup
    monkeypatch.setattr(numbering, "anilist_regroup",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    show_id = _server_show(db, {1: 24, 2: 23, 3: 11})
    _link(db, show_id)
    _sync(db, show_id)
    assert calls == [] and (3, 12) in _eps(db, show_id)


# -- getting the split onto libraries AniList already linked --------------------

def _lumped(db):
    show_id = _server_show(db, {1: 24})
    db.backfill_episodes(show_id, 1, TMDB_S1)       # the old sync's one season of 59
    db.mark_episodes_synced(show_id)
    return show_id


def _synced(db, show_id):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT episodes_synced FROM shows WHERE id=?", (show_id,)).fetchone()[0]
    finally:
        conn.close()


def test_linking_a_lumped_show_queues_its_episode_sync(db):
    show_id = _lumped(db)
    _link(db, show_id)
    assert db.requeue_lumped_anime(show_id) == 1 and _synced(db, show_id) == 0


def test_a_show_already_split_is_not_queued_again(db, no_tvdb):
    show_id = _server_show(db, {1: 24, 2: 12})
    _link(db, show_id)
    _sync(db, show_id)
    db.mark_episodes_synced(show_id)
    assert db.requeue_lumped_anime(show_id) == 0 and _synced(db, show_id) == 1


def test_the_anilist_worker_queues_the_split_when_it_links_a_show(db, monkeypatch):
    from core.video.enrichment import backfill
    show_id = _lumped(db)
    worker = backfill.AniListWorker(db)
    worker.record_ok({"kind": "show", "id": show_id}, {"rows": JJK})
    assert _synced(db, show_id) == 0


def test_v64_queues_the_lumped_shows_anilist_already_linked(tmp_path):
    path = tmp_path / "old.db"
    db = VideoDatabase(database_path=str(path))
    lumped = _lumped(db)
    _link(db, lumped)
    plain = db.upsert_show_tree("plex", {"server_id": "x", "title": "Arrow", "seasons": []})
    db.mark_episodes_synced(plain)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 63")
    conn.commit()
    conn.close()
    video_database._initialized_paths.discard(str(path.resolve()))   # a restart
    db = VideoDatabase(database_path=str(path))
    assert _synced(db, lumped) == 0 and _synced(db, plain) == 1


# -- a search preview gets the anime layer (Frieren, not in the library) --------

def test_a_preview_the_map_knows_gets_its_anilist_entries(db, monkeypatch):
    from core.video import anilist
    from core.video.enrichment.engine import VideoEnrichmentEngine
    from tests.test_video_anime_layer import ANILIST, JJK_MAP
    from core.video import anime_map
    db.replace_anime_map(anime_map.parse_entries(JJK_MAP))
    db.set_setting("anilist_enabled", "1")
    asked = []
    monkeypatch.setattr(anilist, "by_ids", lambda post, ids: asked.append(ids) or {
        i: anilist.media_row(ANILIST[i]) for i in ids if i in ANILIST})
    engine = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    engine.db = db
    d = {"tvdb_id": 377543}
    engine._fill_preview_anime("show", 95479, d)
    assert asked == [[113415, 145064]]
    assert d["anime"]["main"]["anilist_id"] == 113415 and d["anilist_score"] == 84
    assert [e["tvdb_season"] for e in d["anime"]["entries"]] == [1, 2]

    # not anime, or anilist switched off: nothing asked, nothing added
    other = {}
    engine._fill_preview_anime("show", 1396, other)
    db.set_setting("anilist_enabled", "0")
    engine._fill_preview_anime("show", 95479, other)
    assert "anime" not in other and len(asked) == 1


def test_anilist_down_never_breaks_a_preview(db, monkeypatch):
    from core.video import anilist, anime_map
    from core.video.enrichment.engine import VideoEnrichmentEngine
    from tests.test_video_anime_layer import JJK_MAP
    db.replace_anime_map(anime_map.parse_entries(JJK_MAP))
    db.set_setting("anilist_enabled", "1")

    def down(post, ids):
        raise OSError("503")
    monkeypatch.setattr(anilist, "by_ids", down)
    engine = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    engine.db = db
    d = {"tvdb_id": 377543}
    engine._fill_preview_anime("show", 95479, d)
    assert "anime" not in d
