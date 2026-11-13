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


# -- a preview splits its seasons too (Thibs: Frieren from search showed one) ---

def test_the_preview_season_list_splits_like_the_library():
    seasons = [{"season_number": 0, "episode_total": 3},
               {"season_number": 1, "episode_total": 38, "poster_url": "p.jpg"}]
    out, source = numbering.split_preview_seasons(seasons, FRIEREN)
    assert [(s["season_number"], s["episode_total"]) for s in out] == [(0, 3), (1, 28), (2, 10)]
    assert source == {1: 1, 2: 1}
    assert out[2]["poster_url"] == "p.jpg" and out[2]["episodes"] == []


def test_a_preview_split_never_lands_on_a_season_tmdb_has():
    """two different season 2s on one page would be worse than one lump."""
    seasons = [{"season_number": 1, "episode_total": 38}, {"season_number": 2, "episode_total": 12}]
    out, source = numbering.split_preview_seasons(seasons, FRIEREN)
    assert out == seasons and source == {}


def test_without_entries_a_preview_is_untouched():
    seasons = [{"season_number": 1, "episode_total": 59}]
    assert numbering.split_preview_seasons(seasons, []) == (seasons, {})


class _Cache:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v, ttl=0):
        self.d[k] = v


def test_a_split_preview_season_loads_its_slice_renumbered(monkeypatch):
    from core.video.enrichment.engine import VideoEnrichmentEngine
    engine = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    engine._cache = _Cache()
    engine._cache_put(("anime_split", 209867), {"source": {1: 1, 2: 1}, "rows": FRIEREN})
    fetched = []

    def raw(tv_id, n):
        fetched.append(n)
        return {"season_number": n, "episodes": _season(38)} if n == 1 else None
    monkeypatch.setattr(engine, "_tmdb_season_raw", raw)

    s2 = engine.tmdb_season(209867, 2)
    assert s2["season_number"] == 2
    assert [e["episode_number"] for e in s2["episodes"]] == list(range(1, 11))
    assert s2["episodes"][0]["title"] == "Ep 29", "TMDB's 29th episode is season 2's first"
    s1 = engine.tmdb_season(209867, 1)
    assert len(s1["episodes"]) == 28
    # a season the split didn't touch goes straight to TMDB
    assert engine.tmdb_season(209867, 0) is None and fetched[-1] == 0


def test_the_preview_detail_comes_back_split(db, monkeypatch):
    from core.video import anilist
    from core.video.enrichment.engine import VideoEnrichmentEngine
    db.set_setting("anilist_enabled", "1")
    keys = ("anilist_id", "tvdb_season", "tvdb_offset", "tmdb_season", "tmdb_offset")
    monkeypatch.setattr(db, "anime_map_entries",
                        lambda kind, **kw: [{k: e[k] for k in keys} for e in FRIEREN])
    monkeypatch.setattr(anilist, "by_ids", lambda post, ids: {
        e["anilist_id"]: {"episodes": e["episodes"], "format": e["format"] or "TV", "score": 90}
        for e in FRIEREN})
    engine = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    engine.db = db
    engine._cache = _Cache()
    d = {"tvdb_id": 424536, "seasons": [{"season_number": 1, "episode_total": 38}], "season_count": 1}
    engine._fill_preview_anime("show", 209867, d)
    assert [(s["season_number"], s["episode_total"]) for s in d["seasons"]] == [(1, 28), (2, 10)]
    assert d["season_count"] == 2
    assert engine._cache_get(("anime_split", 209867))["source"] == {1: 1, 2: 1}


# -- Nov 13 2026 (Thibs): Dandadan, Apothecary Diaries, Tokyo Revengers, Re:Zero --

def test_dandadan_splits_when_the_map_still_points_at_a_season_tmdb_folded():
    # the stale entry is the LAST one, so it was never "between two anchors"
    dandadan = [_entry(171018, 1, 1, 0, 12), _entry(185660, 2, 2, 0, 12)]
    groups, moved = numbering.anilist_regroup(1, _season(24), dandadan)
    assert len(groups[1]) == 12 and [e["episode_number"] for e in groups[2]] == list(range(1, 13))
    assert moved[13] == (2, 1) and moved[24] == (2, 12)
    # while TMDB still has its own season 2, season 1 is 12 long and nothing moves
    assert numbering.anilist_regroup(1, _season(12), dandadan)[1] == {}


def test_a_cour_that_is_not_on_tmdb_yet_does_not_stop_the_split():
    # The Apothecary Diaries: 24 + 24 + 12 on TMDB, the map already places a
    # fourth entry at 72. 48 + 12 != 72 left all 60 in one season
    apothecary = [_entry(161645, 1, 1, 0, 24), _entry(176301, 2, 1, 24, 24),
                  _entry(195516, 3, 1, 48, 12), _entry(200927, 4, 1, 72, 12)]
    groups, moved = numbering.anilist_regroup(1, _season(60), apothecary)
    assert {k: len(v) for k, v in groups.items()} == {1: 24, 2: 24, 3: 12}
    assert moved[25] == (2, 1) and moved[49] == (3, 1) and moved[60] == (3, 12)
    # a season still airing: TMDB has 8 of the 12 so far
    groups, _moved = numbering.anilist_regroup(1, _season(56), apothecary)
    assert {k: len(v) for k, v in groups.items()} == {1: 24, 2: 24, 3: 8}


def test_a_map_offset_one_out_is_overruled_by_counts_a_later_entry_bears_out():
    # Tokyo Revengers: the map starts the third season at 36, the counts say 37,
    # and the fourth entry at 50 agrees with the counts
    revengers = [_entry(120120, 1, 1, 0, 24), _entry(142853, 2, 1, 24, 13),
                 _entry(163329, 3, 1, 36, 13), _entry(178083, 4, 1, 50, 13)]
    groups, moved = numbering.anilist_regroup(1, _season(58), revengers)
    assert {k: len(v) for k, v in groups.items()} == {1: 24, 2: 13, 3: 13, 4: 8}
    assert moved[37] == (2, 13) and moved[38] == (3, 1) and moved[51] == (4, 1)
    # Re:Zero: the map starts season 2 at 26, season 1 has 25
    rezero = [_entry(21355, 1, 1, 0, 25), _entry(108632, 2, 1, 26, 13),
              _entry(119661, 2, 1, 38, 12, tvdb_offset=13), _entry(163134, 3, 1, 50, 16),
              _entry(189046, 4, 1, 66, 19)]
    groups, moved = numbering.anilist_regroup(1, _season(85), rezero)
    assert {k: len(v) for k, v in groups.items()} == {1: 25, 2: 25, 3: 16, 4: 19}
    assert moved[26] == (2, 1) and moved[39] == (2, 14) and moved[51] == (3, 1)


def test_counts_nothing_bears_out_fall_back_to_a_map_that_places_every_entry():
    # the counts never meet the map or the end, and every entry is placed in
    # this season by the map itself: the map's offsets are the split
    shifted = [_entry(1, 1, 1, 0, 25), _entry(2, 2, 1, 26, 13), _entry(3, 3, 1, 40, 12)]
    groups, moved = numbering.anilist_regroup(1, _season(55), shifted)
    assert {k: len(v) for k, v in groups.items()} == {1: 26, 2: 14, 3: 15}
    # one entry the map places elsewhere, and nothing to bear the counts out: left alone
    shifted[1] = _entry(2, 2, 2, 0, 13)
    assert numbering.anilist_regroup(1, _season(55), shifted)[1] == {}


def test_one_entry_is_never_a_split():
    # Blue Box: one season on AniList so far
    assert numbering.anilist_regroup(1, _season(25), [_entry(173694, 1, 1, 0, 25)])[1] == {}
    assert numbering.anilist_regroup(1, [], JJK)[1] == {}


def test_a_preview_season_out_of_a_split_has_its_name_where_the_card_reads_it():
    seasons = [{"season_number": 0, "title": "Specials", "episode_total": 26},
               {"season_number": 1, "title": "Season 1", "episode_total": 38}]
    out, source = numbering.split_preview_seasons(seasons, FRIEREN)
    assert [(s["season_number"], s["title"], s["episode_total"]) for s in out] == [
        (0, "Specials", 26), (1, "Season 1", 28), (2, "Season 2", 10)]
    assert source == {1: 1, 2: 1}


# -- Nov 13 2026: getting the wider split onto existing libraries, and searches --

DANDADAN = [_entry(171018, 1, 1, 0, 12), _entry(185660, 2, 2, 0, 12)]


def _one_season_of(db, count, owned=6):
    show_id = _server_show(db, {1: owned})
    db.backfill_episodes(show_id, 1, [{"episode_number": i, "title": "E%d" % i}
                                      for i in range(1, count + 1)])
    db.mark_episodes_synced(show_id)
    return show_id


def test_a_show_whose_second_season_tmdb_folded_is_queued(db):
    # no entry starts inside season 1, so the first rule never saw Dandadan
    show_id = _one_season_of(db, 24)
    _link(db, show_id, DANDADAN)
    assert db.requeue_lumped_anime(show_id) == 1 and _synced(db, show_id) == 0


def test_a_show_tmdb_really_gives_two_seasons_is_not_queued(db):
    show_id = _one_season_of(db, 12)
    db.backfill_episodes(show_id, 2, [{"episode_number": i} for i in range(1, 13)])
    _link(db, show_id, DANDADAN)
    assert db.requeue_lumped_anime(show_id) == 0 and _synced(db, show_id) == 1
    # and one season of exactly its own length has nothing folded into it
    other = db.upsert_show_tree("plex", {"server_id": "y", "title": "Other", "seasons": []})
    db.backfill_episodes(other, 1, [{"episode_number": i} for i in range(1, 13)])
    db.mark_episodes_synced(other)
    _link(db, other, DANDADAN)
    assert db.requeue_lumped_anime(other) == 0


def test_v68_queues_the_shows_the_old_split_passed_over(tmp_path):
    path = tmp_path / "old.db"
    db = VideoDatabase(database_path=str(path))
    folded = _one_season_of(db, 24)
    _link(db, folded, DANDADAN)
    plain = db.upsert_show_tree("plex", {"server_id": "x", "title": "Arrow", "seasons": []})
    db.mark_episodes_synced(plain)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 67")
    conn.commit()
    conn.close()
    video_database._initialized_paths.discard(str(path.resolve()))   # a restart
    db = VideoDatabase(database_path=str(path))
    assert _synced(db, folded) == 0 and _synced(db, plain) == 1


def test_a_season_numbered_on_from_the_last_one_is_also_searched_by_its_own_count(db):
    # TMDB runs Naruto Shippuden S19 from E394: "S19E397" finds nothing, the
    # release is S19E04 (Thibs)
    show_id = db.upsert_show_tree("plex", {"server_id": "n", "title": "Naruto", "seasons": []})
    conn = db._get_connection()
    conn.execute("UPDATE shows SET tmdb_id=31910 WHERE id=?", (show_id,))
    conn.commit()
    conn.close()
    db.backfill_episodes(show_id, 19, [{"episode_number": n} for n in range(394, 414)])
    db.backfill_episodes(show_id, 1, [{"episode_number": n} for n in range(1, 33)])

    def item(season, episode):
        return {"show_tmdb_id": 31910, "season_number": season, "episode_number": episode}

    assert numbering.season_relative(item(19, 397), db) == (19, 4)
    assert numbering.season_relative(item(19, 394), db) == (19, 1)
    # a season numbered from 1 is left as it is, and so is an episode nobody knows
    assert numbering.season_relative(item(1, 12), db) is None
    assert numbering.season_relative(item(19, 500), db) is None
    assert numbering.season_relative(item(0, 3), db) is None

    class _NoTvdb:
        enabled = False

        def season_episodes(self, *a):
            return []
    out = numbering.annotate([item(19, 397), item(1, 12)], db=db, client=_NoTvdb())
    assert (out[0]["alt_season"], out[0]["alt_episode"]) == (19, 4)
    assert "alt_exclusive" not in out[0] and "alt_season" not in out[1]


def test_a_split_season_asked_for_after_the_cache_emptied_still_loads(monkeypatch):
    # the second Frieren card came up with no episodes: the split only lived
    # in the cache next to the detail
    from core.video.enrichment.engine import VideoEnrichmentEngine
    engine = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    engine._cache = _Cache()
    built = []

    def detail(kind, tv_id):
        built.append(tv_id)
        if tv_id == 209867:
            engine._cache_put(("anime_split", tv_id), {"source": {1: 1, 2: 1}, "rows": FRIEREN})
        return {}
    monkeypatch.setattr(engine, "tmdb_detail", detail)
    monkeypatch.setattr(engine, "_tmdb_season_raw", lambda tv_id, n: (
        {"season_number": n, "episodes": _season(38)} if n == 1 else None))

    season = engine.tmdb_season(209867, 2)
    assert [e["episode_number"] for e in season["episodes"]] == list(range(1, 11))
    assert built == [209867]
    # a show with nothing to split is looked at once, not on every season opened
    assert engine.tmdb_season(1396, 2) is None and engine.tmdb_season(1396, 3) is None
    assert built == [209867, 1396]
