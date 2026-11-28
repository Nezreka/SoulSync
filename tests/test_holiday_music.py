"""#2332: holiday music. what counts as holiday music (keywords, provider
genres, the hand flags that win), the season window, every generator keeping
it out of season and letting it in in season, and hand flags that survive a
rescan."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from core import holiday_music as H
from database.music_database import MusicDatabase
from tests import lib2_seed as seed

JULY = date(2026, 7, 4)
DECEMBER = date(2026, 12, 20)

XMAS = {"name": "Jingle Bell Rock", "artists": [{"name": "Bobby Helms"}],
        "album": {"name": "Rock Hits"}}
POP = {"name": "Dancing Queen", "artists": [{"name": "ABBA"}], "album": {"name": "Arrival"}}


@pytest.fixture(autouse=True)
def _fresh_caches():
    H.forget_cache()
    yield
    H.forget_cache()


@pytest.fixture
def mode(monkeypatch):
    """set the saved holiday settings for the code that reads them itself"""
    def set_mode(value="season", start="11-15", end="01-06", write_tag=False):
        saved = {"mode": value, "season_start": start, "season_end": end, "write_tag": write_tag}
        monkeypatch.setattr(H, "_saved_settings", lambda config=None: dict(saved))
        return saved
    set_mode()
    return set_mode


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.fixture
def frozen_today(monkeypatch):
    """date.today() inside core.holiday_music"""
    def freeze(day: date):
        class _Date(date):
            @classmethod
            def today(cls):
                return day
        monkeypatch.setattr(H, "date", _Date)
    return freeze


# ── detection ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,album", [
    ("Jingle Bells", ""),
    ("Silent Night", ""),
    ("Feliz Navidad", ""),
    ("Last Christmas", ""),
    ("Have Yourself a Merry Little Christmas", ""),
    ("Noël Blanc", ""),
    ("Hanukkah Song", ""),
    ("Track 4", "A Charlie Brown Christmas"),
    ("Track 1", "Xmas Party Hits"),
    ("Sleigh Ride", ""),
    ("Santa Baby", ""),
    ("Happy Holidays", ""),
])
def test_titles_that_are_holiday_music(title, album):
    assert H.detect(title, album, [])


@pytest.mark.parametrize("title,album", [
    ("Holiday", "Madonna"),            # madonna's holiday is not christmas
    ("Holiday", "American Idiot"),
    ("Santa Monica", "Everclear"),
    ("Smooth", "Santana Supernatural"),
    ("Noelle", ""),
    ("Carol", ""),
    ("Dancing Queen", "Arrival"),
])
def test_titles_that_are_not(title, album):
    assert not H.detect(title, album, [])


@pytest.mark.parametrize("genres", [
    ["Pop", "Christmas"],
    ["holiday"],
    ['["soul", "Christmas Music"]'],
    ["Xmas"],
    "Jazz, Holiday",
])
def test_provider_genres_and_moods(genres):
    assert H.detect("Some Song", "Some Album", H._genre_list(genres))


# ── false positives (the reporter's list) ─────────────────────────────────

@pytest.mark.parametrize("title,album,artist,genres", [
    ("Holiday", "Madonna", "Madonna", ["pop", "holiday"]),        # tag = the title
    ("Holiday", "American Idiot", "Green Day", ["punk", "rock"]),
    ("Holiday", "Holiday", "Anyone", ["holiday"]),               # "holiday" by any artist
    ("Holiday", "Some Record", "Weezer", []),
    ("Lover Man", "Lady Day", "Billie Holiday", ["jazz", "billie holiday"]),
    ("Strange Fruit", "The Billie Holiday Collection", "Billie Holiday", ["vocal jazz"]),
    ("Fine and Mellow", "Sings the Billie Holiday Songbook", "Someone Else", []),
    ("All I Want", "Debut", "Mike Christmas", ["mike christmas", "indie"]),  # a surname
    ("Song (feat. Mike Christmas)", "Album", "Other", []),        # a credited name
    ("Song", "Album", "Other", ["holiday inn", "billie holiday"]),
    ("Wonderwall", "Noel Gallagher's High Flying Birds", "Noel Gallagher", []),
    ("And I Am Telling You", "Dreamgirls", "Jennifer Holiday", ["jennifer holiday"]),
    ("Rudolph", "Album", "Other", []),
    ("Carol", "Album", "Other", ["carol"]),
    ("Summer Holiday", "Summer Holiday", "Cliff Richard", ["oldies"]),
    ("Holiday", "Holiday", "The Holidays", ["holiday"]),
])
def test_names_and_the_bare_word_holiday_are_not_holiday_music(title, album, artist, genres):
    assert not H.detect(title, album, genres, artist=artist)
    assert not H.Policy(mode="never").is_holiday(
        {"title": title, "album": album, "artist": artist, "genres": genres})


@pytest.mark.parametrize("title,album,artist,genres", [
    ("Song", "Album", "Bobby Helms", ["Christmas"]),
    ("Song", "Album", "Anyone", ["holiday music"]),
    ("Song", "Album", "Anyone", ["Holiday"]),                    # the tag soulsync writes
    ("Song", "Album", "Anyone", ["christmas pop"]),
    ("Track 3", "Merry Christmas", "Mariah Carey", []),
    ("Noël", "Album", "Anyone", []),
    ("The First Noel", "Album", "Anyone", []),
    ("Feliz Navidad", "Album", "José Feliciano", []),
    ("Rudolph the Red-Nosed Reindeer", "Album", "Gene Autry", []),
    ("Silent Night", "Album", "Billie Holiday", []),             # her carol still is one
    ("Track 2", "Christmas Carols", "Choir", []),
    ("Track 1", "Happy Holidays", "Andy Williams", []),
])
def test_genre_and_seasonal_phrases_are(title, album, artist, genres):
    assert H.detect(title, album, genres, artist=artist)


def test_an_artist_tagged_christmas_somewhere_is_not_all_christmas():
    # only the artist's main genre counts: mariah sings in july too
    song = {"title": "Fantasy", "album": "Daydream", "artist": "Mariah Carey",
            "artist_genres": ["pop", "r&b", "christmas"]}
    assert not H.Policy(mode="never").is_holiday(song)
    act = {"title": "Deck the Halls", "album": "Fresh Aire Christmas",
           "artist": "Mannheim Steamroller", "artist_genres": ["christmas", "new age"]}
    assert H.Policy(mode="never").is_holiday(act)
    assert H.detect("Track 1", "Album", [], artist="Mannheim Steamroller",
                    artist_genres=["Christmas"])


def test_an_album_is_decided_by_what_all_its_tracks_share():
    # one carol on a pop record doesn't make the record holiday music
    assert not H.album_says_holiday("Pop Hits 2020", ["pop"], artist="Various")
    assert H.album_says_holiday("A Very Special Christmas", [], artist="Various")
    assert H.album_says_holiday("Album", ["Christmas"], artist="Various")
    assert not H.album_says_holiday("The Billie Holiday Collection", ["jazz"],
                                    artist="Billie Holiday")


def test_ordinary_genres_are_not():
    assert not H.detect("Some Song", "Some Album", ["pop", "soul", "jazz"])


def test_every_track_shape_is_read():
    assert H.fields(XMAS)[:3] == ("Jingle Bell Rock", "Rock Hits", "Bobby Helms")
    assert H.fields({"track_name": "x", "album_name": "y", "artist_name": "z"})[:3] == ("x", "y", "z")
    assert H.fields({"title": "x", "album": "y", "artist": "z"})[:3] == ("x", "y", "z")
    rich = {"track_name": "Song", "track_data_json": '{"album": {"genres": ["Christmas"]}}'}
    assert "Christmas" in H.fields(rich)[3]


# ── hand flags win ──────────────────────────────────────────────────────────

def test_a_track_flag_beats_the_guess():
    off = {("track", H.match_key("track", "Bobby Helms", title="Jingle Bell Rock")): False}
    assert H.resolve("Jingle Bell Rock", "", "Bobby Helms", [], off) == (False, "track")
    on = {("track", H.match_key("track", "ABBA", title="Dancing Queen")): True}
    assert H.resolve("Dancing Queen", "Arrival", "ABBA", [], on) == (True, "track")


def test_track_beats_album_beats_artist():
    flags = {
        ("artist", H.match_key("artist", "Mariah Carey")): True,
        ("album", H.match_key("album", "Mariah Carey", "Butterfly")): False,
        ("track", H.match_key("track", "Mariah Carey", title="Honey")): True,
    }
    assert H.resolve("Honey", "Butterfly", "Mariah Carey", [], flags) == (True, "track")
    assert H.resolve("Fantasy", "Butterfly", "Mariah Carey", [], flags) == (False, "album")
    assert H.resolve("Hero", "Music Box", "Mariah Carey", [], flags) == (True, "artist")


def test_name_keys_ignore_case_and_punctuation():
    assert H.match_key("track", "Guns N' Roses", title="Sweet Child O' Mine") == \
        H.match_key("track", "guns n roses", title="sweet child o mine")


# ── the season ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("day,inside", [
    (date(2026, 11, 14), False),
    (date(2026, 11, 15), True),
    (date(2026, 12, 25), True),
    (date(2027, 1, 6), True),
    (date(2027, 1, 7), False),
    (date(2026, 7, 4), False),
])
def test_the_default_season_runs_over_new_year(day, inside):
    assert H.in_window(day) is inside


def test_a_season_inside_one_year():
    assert H.in_window(date(2026, 12, 10), "12-01", "12-26")
    assert not H.in_window(date(2026, 11, 30), "12-01", "12-26")
    assert not H.in_window(date(2027, 1, 2), "12-01", "12-26")


def test_bad_dates_fall_back():
    assert H.parse_day("13-40", "11-15") == (11, 15)
    assert H.parse_day(None, "01-06") == (1, 6)


def test_settings_with_none_saved_are_the_defaults():
    class Empty:
        def get(self, key, default=None):
            return default
    assert H.settings(Empty()) == {"mode": "season", "season_start": "11-15",
                                   "season_end": "01-06", "write_tag": False}


@pytest.mark.parametrize("mode_name,day,keeps", [
    ("season", JULY, False), ("season", DECEMBER, True),
    ("always", JULY, True), ("never", DECEMBER, False),
])
def test_the_policy(mode_name, day, keeps):
    p = H.Policy(mode=mode_name, today=day)
    assert p.keep(XMAS) is keeps
    assert p.keep(POP)
    assert p.filter([XMAS, POP]) == ([XMAS, POP] if keeps else [POP])


def test_the_holiday_mix_shows_in_season_only():
    assert H.Policy(today=DECEMBER).shows_holiday_mix
    assert not H.Policy(today=JULY).shows_holiday_mix
    assert H.Policy(mode="always", today=DECEMBER).shows_holiday_mix
    assert not H.Policy(mode="always", today=JULY).shows_holiday_mix
    assert not H.Policy(mode="never", today=DECEMBER).shows_holiday_mix


# ── hand flags in the database survive a rescan ─────────────────────────────

def test_a_flag_survives_a_rescan(db):
    conn = db._get_connection()
    tid = seed.track(conn, "ABBA", "Arrival", "Dancing Queen")
    conn.commit()
    H.set_override(db, "track", True, artist="ABBA", title="Dancing Queen")
    assert H.status(db, artist="ABBA", album="Arrival", title="Dancing Queen")["holiday"]
    assert [r["id"] for r in H.library_tracks(db, use_cache=False)] == [tid]

    # a rescan that drops the rows and makes them again, under new ids
    conn.execute("DELETE FROM lib2_track_files")
    conn.execute("DELETE FROM lib2_tracks")
    conn.execute("DELETE FROM lib2_albums")
    conn.execute("DELETE FROM lib2_artists")
    new_tid = seed.track(conn, "ABBA", "Arrival", "Dancing Queen")
    conn.commit()
    conn.close()
    H.forget_cache()
    state = H.status(db, artist="ABBA", album="Arrival", title="Dancing Queen")
    assert state["holiday"] and state["override"] is True and state["decided_by"] == "track"
    assert [r["id"] for r in H.library_tracks(db, use_cache=False)] == [new_tid]


def test_automatic_takes_the_flag_away(db):
    H.set_override(db, "artist", True, artist="ABBA")
    assert H.status(db, artist="ABBA")["override"] is True
    H.set_override(db, "artist", None, artist="ABBA")
    assert H.status(db, artist="ABBA")["override"] is None
    assert H.list_overrides(db) == []


def test_a_flag_off_takes_a_christmas_song_out(db):
    conn = db._get_connection()
    seed.track(conn, "Bobby Helms", "Rock Hits", "Jingle Bell Rock")
    keep = seed.track(conn, "Bing Crosby", "Merry Christmas", "White Christmas")
    seed.track(conn, "ABBA", "Arrival", "Dancing Queen")
    conn.commit()
    conn.close()
    assert len(H.library_tracks(db, use_cache=False)) == 2
    H.set_override(db, "track", False, artist="Bobby Helms", title="Jingle Bell Rock")
    assert [r["id"] for r in H.library_tracks(db, use_cache=False)] == [keep]


def test_set_override_needs_names(db):
    with pytest.raises(ValueError):
        H.set_override(db, "track", True, artist="ABBA", title="")
    with pytest.raises(ValueError):
        H.set_override(db, "album", True, artist="", album="X")


def test_the_migration_made_the_table(db):
    conn = db._get_connection()
    assert conn.execute("SELECT name FROM schema_migrations WHERE name='holiday_overrides_v1'").fetchone()


# ── the library filter ──────────────────────────────────────────────────────

def test_the_library_filters_holiday_albums(db, monkeypatch):
    conn = db._get_connection()
    seed.track(conn, "Bing Crosby", "Merry Christmas", "White Christmas")
    seed.track(conn, "Bing Crosby", "Merry Christmas", "Silent Night")
    # one bonus christmas song doesn't make an album festive
    seed.track(conn, "ABBA", "Arrival", "Dancing Queen")
    seed.track(conn, "ABBA", "Arrival", "Money Money Money")
    seed.track(conn, "ABBA", "Arrival", "Happy New Year Christmas Bonus")
    conn.commit()
    conn.close()
    monkeypatch.setattr(H, "_database", lambda database=None: db)
    from core.library2 import queries as Q

    def titles(holiday):
        c = db._get_connection()
        try:
            albums, _total = Q.list_albums(c, holiday=holiday)
        finally:
            c.close()
        return sorted(a["title"] for a in albums)

    assert titles("only") == ["Merry Christmas"]
    assert titles("hide") == ["Arrival"]
    assert titles("") == ["Arrival", "Merry Christmas"]


# ── the tag ─────────────────────────────────────────────────────────────────

class _Cfg:
    def __init__(self, write_tag):
        self.write_tag = write_tag

    def get(self, key, default=None):
        return self.write_tag if key == "holiday_music.write_tag" else default


def test_the_tag_is_off_by_default(db):
    assert H.tag_genres(["Pop"], {"title": "Jingle Bells"}, config=_Cfg(False), database=db) == ["Pop"]


def test_the_tag_goes_on_holiday_music_once(db):
    cfg = _Cfg(True)
    assert H.tag_genres(["Pop"], {"title": "Jingle Bells"}, config=cfg, database=db) == ["Pop", "Holiday"]
    assert H.tag_genres("Pop", {"title": "Jingle Bells"}, config=cfg, database=db) == "Pop, Holiday"
    assert H.tag_genres(["Pop", "Holiday"], {"title": "Jingle Bells"}, config=cfg,
                        database=db) == ["Pop", "Holiday"]
    assert H.tag_genres(["Pop"], {"title": "Dancing Queen"}, config=cfg, database=db) == ["Pop"]
    # the artist's christmas genre counts
    assert H.tag_genres([], {"title": "Song", "genres": ["Christmas"]}, config=cfg,
                        database=db) == ["Holiday"]


def test_the_tag_follows_a_hand_flag(db):
    H.set_override(db, "album", True, artist="ABBA", album="Arrival")
    assert H.tag_genres(["Pop"], {"title": "Dancing Queen", "album": "Arrival", "artist": "ABBA"},
                        config=_Cfg(True), database=db) == ["Pop", "Holiday"]


def test_retag_keeps_the_holiday_genre(db, mode):
    """the retag projection (what Write Tags compares and writes) carries it"""
    mode("season", write_tag=True)
    conn = seed.row_conn(db.database_path)
    tid = seed.track(conn, "Bing Crosby", "Merry Christmas", "White Christmas",
                     album_cols={"genres": '["Pop"]'})
    conn.commit()
    from core.library2.retag import _db_data_for_row, _track_rows
    try:
        data = _db_data_for_row(conn, _track_rows(conn, [tid])[0])
    finally:
        conn.close()
    assert "Holiday" in data["genres"]


# ── every generator ─────────────────────────────────────────────────────────

def test_daily_mixes_and_flow(frozen_today, mode):
    from core.personalized import daily_mixes, for_you, taste_draw
    assert not daily_mixes.in_season(XMAS, JULY)
    assert daily_mixes.in_season(XMAS, DECEMBER)
    assert daily_mixes.in_season(POP, JULY)
    row = {"title": "Last Christmas", "album": "Music from the Edge of Heaven"}
    assert not for_you.in_season(row, datetime(2026, 7, 4))
    assert for_you.in_season(row, datetime(2026, 12, 4))
    junk = {"artist_name": "Wham!", "track_name": "Last Christmas", "album_name": "x",
            "duration_ms": 260000}
    assert taste_draw.is_junk(junk, JULY)
    assert not taste_draw.is_junk(junk, DECEMBER)


def test_the_user_dates_reach_the_mixes(mode):
    from core.personalized import daily_mixes
    mode("season", start="12-20", end="12-26")
    assert not daily_mixes.in_season(XMAS, date(2026, 12, 1))
    assert daily_mixes.in_season(XMAS, date(2026, 12, 24))
    mode("always")
    assert daily_mixes.in_season(XMAS, JULY)
    mode("never")
    assert not daily_mixes.in_season(XMAS, DECEMBER)


def _manager(db, kind, rows):
    from core.personalized.manager import PersonalizedPlaylistManager
    from core.personalized.specs import PlaylistKindRegistry, PlaylistKindSpec
    from core.personalized.types import PlaylistConfig, Track
    registry = PlaylistKindRegistry()
    registry.register(PlaylistKindSpec(
        kind=kind, name_template=kind, description="", default_config=PlaylistConfig(limit=50),
        generator=lambda deps, variant, config: [Track.from_dict(r) for r in rows],
        requires_variant=kind == "seasonal_mix"))
    return PersonalizedPlaylistManager(db, None, registry=registry)


_ROWS = [{"track_name": "Jingle Bell Rock", "artist_name": "Bobby Helms", "album_name": "Hits"},
         {"track_name": "Dancing Queen", "artist_name": "ABBA", "album_name": "Arrival"}]


@pytest.mark.parametrize("kind", ["genre_playlist", "daily_mix", "hidden_gems", "discovery_shuffle",
                                  "popular_picks", "time_machine", "listening_mix"])
def test_every_personalized_playlist(db, mode, frozen_today, kind):
    variant = "1" if kind in ("daily_mix",) else ""
    frozen_today(JULY)
    record = _manager(db, kind, _ROWS).refresh_playlist(kind, variant)
    assert record.track_count == 1
    frozen_today(DECEMBER)
    record = _manager(db, kind, _ROWS).refresh_playlist(kind, variant)
    assert record.track_count == 2


def test_the_holiday_kinds_keep_their_songs(db, mode, frozen_today):
    frozen_today(JULY)
    assert _manager(db, "holiday_mix", _ROWS).refresh_playlist("holiday_mix").track_count == 2
    assert _manager(db, "seasonal_mix", _ROWS).refresh_playlist(
        "seasonal_mix", "christmas").track_count == 2
    assert _manager(db, "seasonal_mix", _ROWS).refresh_playlist(
        "seasonal_mix", "summer").track_count == 1


def test_genre_and_decade_playlists_before_the_limit(db, mode, frozen_today):
    from core.personalized_playlists import PersonalizedPlaylistsService
    svc = object.__new__(PersonalizedPlaylistsService)
    svc.database = db
    rows = [dict(r) for r in _ROWS]
    frozen_today(JULY)
    out = svc._apply_diversity_filter(rows, max_per_album=5, max_per_artist=5, limit=1)
    assert [r["track_name"] for r in out] == ["Dancing Queen"]
    frozen_today(DECEMBER)
    out = svc._apply_diversity_filter(rows, max_per_album=5, max_per_artist=5, limit=2)
    assert len(out) == 2


def test_discover_routes_drop_them_from_track_lists(frozen_today):
    from core.discovery.blocked import WORKS, BlockedArtists, hide_blocked
    blocked = BlockedArtists()
    payload = {"tracks": [XMAS, POP], "mixes": [{"tracks": [XMAS]}, {"tracks": [POP]}],
               "albums": [{"name": "Merry Christmas", "artist": "Bing Crosby"}]}
    spec = {"tracks": WORKS, "mixes[].tracks": WORKS, "albums": WORKS}
    hide_blocked(payload, spec, blocked, H.Policy(today=JULY))
    assert payload["tracks"] == [POP]
    assert payload["mixes"] == [{"tracks": [POP]}]   # an all-christmas mix goes
    assert len(payload["albums"]) == 1                # album lists are yours
    payload = {"tracks": [XMAS, POP]}
    hide_blocked(payload, {"tracks": WORKS}, blocked, H.Policy(today=DECEMBER))
    assert payload["tracks"] == [XMAS, POP]


def test_radio(db, mode, frozen_today):
    from core.radio.selection import RadioCollector
    rows = [{"id": 1, "title": "Silent Night", "album": "x", "artist": "a"},
            {"id": 2, "title": "Dancing Queen", "album": "Arrival", "artist": "ABBA"}]
    c = RadioCollector(10, holiday=H.Policy(today=JULY))
    c.collect(rows)
    assert [r["id"] for r in c.tracks] == [2]
    c = RadioCollector(10, holiday=H.Policy(today=DECEMBER))
    c.collect(rows)
    assert [r["id"] for r in c.tracks] == [1, 2]


def test_library_radio_off_an_everyday_song(db, mode, frozen_today):
    conn = db._get_connection()
    seed_id = seed.track(conn, "ABBA", "Arrival", "Dancing Queen")
    seed.track(conn, "ABBA", "Christmas Hits", "Little Drummer Boy")
    seed.track(conn, "ABBA", "Voulez-Vous", "Chiquitita")
    conn.commit()
    conn.close()
    frozen_today(JULY)
    titles = {t["title"] for t in db.get_radio_tracks(seed_id, limit=10)["tracks"]}
    assert titles == {"Chiquitita"}
    frozen_today(DECEMBER)
    titles = {t["title"] for t in db.get_radio_tracks(seed_id, limit=10)["tracks"]}
    assert titles == {"Chiquitita", "Little Drummer Boy"}
    frozen_today(JULY)
    titles = {t["title"] for t in db.get_library_radio_tracks(limit=10)["tracks"]}
    assert "Little Drummer Boy" not in titles


def test_radio_off_a_christmas_song_plays_christmas(db, mode, frozen_today):
    conn = db._get_connection()
    seed_id = seed.track(conn, "ABBA", "Christmas Hits", "Little Drummer Boy")
    seed.track(conn, "ABBA", "More Christmas", "Silent Night")
    conn.commit()
    conn.close()
    frozen_today(JULY)
    titles = {t["title"] for t in db.get_radio_tracks(seed_id, limit=10)["tracks"]}
    assert "Silent Night" in titles


def test_lastfm_radio(mode, frozen_today):
    from core.lastfm_fallback import _in_season
    rows = [{"name": "Last Christmas", "artist": "Wham!"}, {"name": "Freedom", "artist": "Wham!"}]
    frozen_today(JULY)
    assert _in_season(rows, "George Michael", "Faith") == [rows[1]]
    assert _in_season(rows, "Mariah Carey", "All I Want for Christmas Is You") == rows
    frozen_today(DECEMBER)
    assert _in_season(rows, "George Michael", "Faith") == rows


def test_mix_recipes(db, mode, frozen_today, monkeypatch):
    import random

    from core.personalized import recipes
    lib = [{"name": "Jingle Bells", "artists": [{"name": "A"}], "album": {"name": "x"}},
           {"name": "Song", "artists": [{"name": "B"}], "album": {"name": "y"}}]
    monkeypatch.setattr(recipes, "library_pool", lambda *a, **k: list(lib))
    monkeypatch.setattr(recipes, "discovery_pool", lambda *a, **k: [])
    monkeypatch.setattr(recipes, "trending_pool", lambda *a, **k: [])
    seen = {}

    def build_mix(pools, variant, skip_artist=None):
        seen["library"] = [t["name"] for t in pools["library"]]
        return [], []
    monkeypatch.setattr(recipes, "build_mix", build_mix)
    frozen_today(JULY)
    recipes._build_variant(db, recipes.Recipe(name="x"), 1, [], set(), [], random.Random(1), None)
    assert seen["library"] == ["Song"]
    recipes._build_variant(db, recipes.Recipe(name="x", genres=["Christmas"]), 1, [], set(), [],
                           random.Random(1), None)
    assert seen["library"] == ["Jingle Bells", "Song"]


def test_player_rows(frozen_today, mode):
    from core.discovery.blocked import BlockedArtists
    from core.player.discovery import _card, _without_blocked
    cards = [_card("daily-1", "Daily Mix 1", "", "made", [XMAS, POP]),
             _card("holiday", "Holiday", "", "made", [XMAS]),
             _card("seasonal-christmas", "Christmas", "", "fresh", [XMAS]),
             _card("mood-x", "Mood", "", "made", [XMAS])]
    out = _without_blocked(cards, BlockedArtists(), H.Policy(today=JULY))
    assert {c["key"]: c["track_count"] for c in out} == {
        "daily-1": 1, "holiday": 1, "seasonal-christmas": 1}


def test_player_music_channels(db, mode, frozen_today):
    from core.player.music_channels import read
    conn = db._get_connection()
    for i in range(22):  # a channel needs 20 tracks
        seed.track(conn, "Bing Crosby", "Merry Christmas", f"Carol {i}", duration=200000,
                   album_cols={"genres": '["Christmas"]'})
        seed.track(conn, "ABBA", "Arrival", f"Song {i}", duration=200000,
                   album_cols={"genres": '["Pop"]'})
    conn.commit()
    frozen_today(JULY)
    names = {c["name"] for c in read(conn)}
    assert "Christmas" not in names and "Pop" in names
    frozen_today(DECEMBER)
    names = {c["name"] for c in read(conn)}
    conn.close()
    assert "Christmas" in names


def test_the_holiday_mix(db, mode, frozen_today):
    from core.personalized.generators.holiday_mix import generate
    from core.personalized.types import PlaylistConfig
    conn = db._get_connection()
    seed.track(conn, "Bing Crosby", "Merry Christmas", "White Christmas")
    seed.track(conn, "ABBA", "Arrival", "Dancing Queen")
    conn.commit()
    conn.close()
    deps = {"database": db}
    frozen_today(DECEMBER)
    assert [t.track_name for t in generate(deps, "", PlaylistConfig())] == ["White Christmas"]
    frozen_today(JULY)
    assert generate(deps, "", PlaylistConfig()) == []
    mode("never")
    frozen_today(DECEMBER)
    assert generate(deps, "", PlaylistConfig()) == []


def test_subsonic_shuffle_and_chat_use_the_same_policy(frozen_today, mode):
    frozen_today(JULY)
    songs = [{"title": "Silent Night", "album": "x", "artist": "a"},
             {"title": "Song", "album": "y", "artist": "b", "genre": "Pop"}]
    assert H.filter_tracks(songs) == [songs[1]]
