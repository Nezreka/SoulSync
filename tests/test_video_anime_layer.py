"""the anime layer: AniList linked by id through the anime id map, TMDB and
TVDB stay the identity. only anime is touched, seasons keep their own AniList
entries, and a season's names never leak into another season's search."""

import pytest

from core.video import anilist, anime_map
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


# the shape of Fribb's anime-list-mini.json (jujutsu kaisen, trimmed)
JJK_MAP = [
    {"type": "TV", "anidb_id": 15275, "anilist_id": 113415, "mal_id": 40748,
     "imdb_id": ["tt12343534"], "themoviedb_id": {"tv": 95479}, "tvdb_id": 377543,
     "season": {"tvdb": 1, "tmdb": 1}},
    {"type": "MOVIE", "anilist_id": 131573, "mal_id": 48561, "imdb_id": ["tt14331144"],
     "themoviedb_id": {"movie": [810693]}, "tvdb_id": 377543, "season": {"tvdb": 0},
     "episode_offset": {"tvdb": 1}},
    {"type": "TV", "anilist_id": 145064, "mal_id": 51009, "themoviedb_id": {"tv": 95479},
     "tvdb_id": 377543, "season": {"tvdb": 2, "tmdb": 2}},
    {"type": "TV", "anilist_id": 0, "tvdb_id": 1},       # no anilist id: left out
]


def _media(aid, romaji, english, synonyms=(), score=85, status="FINISHED", fmt="TV"):
    return {"id": aid, "idMal": 1, "format": fmt, "status": status, "episodes": 24,
            "seasonYear": 2020, "averageScore": score, "popularity": 1000,
            "siteUrl": f"https://anilist.co/anime/{aid}",
            "title": {"romaji": romaji, "english": english, "native": "呪術廻戦"},
            "synonyms": list(synonyms), "studios": {"nodes": [{"name": "MAPPA"}]},
            "nextAiringEpisode": None, "coverImage": {"large": "c.jpg"}, "bannerImage": None}


ANILIST = {
    113415: _media(113415, "Jujutsu Kaisen", "JUJUTSU KAISEN", ["JJK", "Sorcery Fight", "咒术回战"], 84),
    145064: _media(145064, "Jujutsu Kaisen 2nd Season", "JUJUTSU KAISEN Season 2", ["JJK2"], 86),
    131573: _media(131573, "Jujutsu Kaisen 0", "JUJUTSU KAISEN 0", ["JJK 0"], 83, fmt="MOVIE"),
}


def fake_post(calls):
    def post(url, body):
        calls.append(body["variables"])
        ids = body["variables"].get("ids")
        if ids is not None:
            return {"data": {"Page": {"media": [ANILIST[i] for i in ids if i in ANILIST]}}}
        return {"data": {"Media": None}}
    return post


def _seed_map(db):
    assert anime_map.refresh(db, fetch=lambda url: JJK_MAP * 1, force=True) == 0  # < 1000: refused
    db.replace_anime_map(anime_map.parse_entries(JJK_MAP))


def test_the_map_keeps_entries_with_an_anilist_id_and_both_numberings():
    rows = anime_map.parse_entries(JJK_MAP)
    assert [r["anilist_id"] for r in rows] == [113415, 131573, 145064]
    movie = rows[1]
    assert (movie["tmdb_movie"], movie["tvdb_season"], movie["tvdb_offset"]) == (810693, 0, 1)


def test_a_short_or_failed_fetch_never_wipes_a_good_map(db):
    db.replace_anime_map(anime_map.parse_entries(JJK_MAP))
    assert anime_map.refresh(db, fetch=lambda url: [], force=True) == 0
    assert anime_map.refresh(db, fetch=lambda url: (_ for _ in ()).throw(OSError("offline")),
                             force=True) == 0
    assert db.anime_map_count() == 3


def test_a_show_is_found_by_tvdb_id_season_by_season(db):
    _seed_map(db)
    seasons = db.anime_map_entries("show", tvdb_id=377543)
    assert [(e["anilist_id"], e["tvdb_season"]) for e in seasons] == [(113415, 1), (145064, 2)]
    # the movie is the movie's
    assert [e["anilist_id"] for e in db.anime_map_entries("movie", tmdb_id=810693)] == [131573]
    # a show that isn't anime has no entries: nothing is ever touched
    assert db.anime_map_entries("show", tvdb_id=81189) == []


def test_one_request_fetches_every_season(db):
    calls = []
    facts = anilist.by_ids(fake_post(calls), [113415, 145064])
    assert set(facts) == {113415, 145064} and len(calls) == 1
    assert facts[113415]["studios"] == ["MAPPA"]


def test_a_title_search_only_takes_an_exact_match():
    def post(url, body):
        return {"data": {"Media": _media(1, "The Bear Anime", "The Bear: The Animation")}}
    assert anilist.by_title(post, "The Bear", 2022) is None
    assert anilist.by_title(post, "The Bear Anime", 2020)["anilist_id"] == 1
    # the year has to agree within one when both know it
    assert anilist.by_title(post, "The Bear Anime", 2015) is None


def test_the_worker_links_a_show_by_id_and_keeps_every_season(db):
    from core.video.enrichment import backfill
    _seed_map(db)
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Jujutsu Kaisen",
                                       "tmdb_id": 95479, "tvdb_id": 377543, "seasons": []})
    db.set_setting("anilist_enabled", "1")
    calls = []
    worker = backfill.AniListWorker(db)
    worker._map_checked = 1.0       # the map is seeded
    orig = backfill._http_post_json
    backfill._http_post_json = fake_post(calls)
    try:
        assert worker.process_one()
    finally:
        backfill._http_post_json = orig
    entries = db.anime_entries("show", sid)
    assert [(e["anilist_id"], e["tvdb_season"]) for e in entries] == [(113415, 1), (145064, 2)]
    conn = db._get_connection()
    try:
        score = conn.execute("SELECT anilist_score FROM shows WHERE id=?", (sid,)).fetchone()[0]
    finally:
        conn.close()
    assert score == 84        # season one stands for the show
    # the detail page's summary
    summary = db.anime_summary("show", sid)
    assert summary["main"]["anilist_id"] == 113415
    assert [e["tvdb_season"] for e in summary["entries"]] == [1, 2]


def test_a_season_gets_its_own_names_never_another_seasons(db):
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Jujutsu Kaisen",
                                       "tmdb_id": 95479, "tvdb_id": 377543, "seasons": []})
    rows = [{**e, **anilist.media_row(ANILIST[e["anilist_id"]])}
            for e in anime_map.parse_entries(JJK_MAP) if e["format"] == "TV"]
    db.save_anime_entries("show", sid, rows)
    s1 = db.anime_titles_for("show", 95479, season=1)
    s2 = db.anime_titles_for("show", 95479, season=2)
    assert "Sorcery Fight" in s1 and "JJK2" not in s1
    assert "JJK2" in s2 and "Jujutsu Kaisen" in s2 and "Sorcery Fight" not in s2
    # no kana/kanji names: no release is titled in them
    assert not any("咒" in n for n in s1)


def test_the_release_search_takes_the_seasons_anime_names(db, monkeypatch):
    from api import video as videoapi
    from core.automation.handlers import video_process_wishlist as vpw
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Jujutsu Kaisen",
                                       "tmdb_id": 95479, "tvdb_id": 377543, "seasons": []})
    db.upsert_show_tree("plex", {"server_id": "s2", "title": "Breaking Bad",
                                 "tmdb_id": 1396, "tvdb_id": 81189, "seasons": []})
    rows = [{**e, **anilist.media_row(ANILIST[e["anilist_id"]])}
            for e in anime_map.parse_entries(JJK_MAP) if e["format"] == "TV"]
    db.save_anime_entries("show", sid, rows)
    monkeypatch.setattr(videoapi, "_video_db", db)
    titles = vpw._acceptable_titles("Jujutsu Kaisen", "show", 95479, 2)
    assert titles[0] == "Jujutsu Kaisen"
    assert "JJK2" in titles and "Sorcery Fight" not in titles
    # not anime: nothing added
    assert vpw._acceptable_titles("Breaking Bad", "show", 1396, 1) == ["Breaking Bad"]


def test_v61_turns_anilist_on_once_and_looks_at_every_show_again(tmp_path):
    # it was off by default only because the old title search could score a
    # live-action show; by id it only ever touches anime
    import sqlite3

    from database import video_database
    path = tmp_path / "old.db"
    db = VideoDatabase(database_path=str(path))
    sid = db.upsert_show_tree("plex", {"server_id": "s1", "title": "Arrow", "seasons": []})
    db.set_setting("anilist_enabled", "0")
    conn = sqlite3.connect(path)
    conn.execute("UPDATE shows SET anilist_status='not_found' WHERE id=?", (sid,))
    conn.execute("PRAGMA user_version = 60")
    conn.commit()
    conn.close()
    video_database._initialized_paths.discard(str(path.resolve()))   # a restart
    db = VideoDatabase(database_path=str(path))
    assert db.get_setting("anilist_enabled") == "1"
    conn = db._get_connection()
    try:
        assert conn.execute("SELECT anilist_status FROM shows WHERE id=?", (sid,)).fetchone()[0] is None
    finally:
        conn.close()
    # and only once: switching it off afterwards sticks
    db.set_setting("anilist_enabled", "0")
    video_database._initialized_paths.discard(str(path.resolve()))
    db = VideoDatabase(database_path=str(path))
    assert db.get_setting("anilist_enabled") == "0"
