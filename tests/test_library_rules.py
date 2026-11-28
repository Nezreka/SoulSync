"""#2332: secondary library rules. which albums go to the secondary folder
(holiday music, a genre, an album title, never a name), that an album is
decided once and never split, that downloads are filed there, that owned
albums move there with a preview first, and that ownership and the visibility
check see the folder."""

from __future__ import annotations

import os

import pytest

from core import audiobook_routing
from core import holiday_music as H
from core import library_rules as R
from core.library2.sql_util import on_server_sql
from database.music_database import MusicDatabase
from tests import lib2_seed as seed


class Config:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


@pytest.fixture
def folders(tmp_path, monkeypatch):
    main = tmp_path / "Transfer"
    second = tmp_path / "Holiday"
    main.mkdir()
    second.mkdir()
    monkeypatch.setattr("core.imports.paths.shared_transfer_root", lambda: str(main))
    return main, second


@pytest.fixture
def config(folders, monkeypatch):
    _, second = folders
    cfg = Config({"library.secondary_enabled": True, "library.secondary_path": str(second),
                  "library.secondary_name": "Holiday", "library.secondary_match_holiday": True})
    real = R.rules
    monkeypatch.setattr(R, "rules", lambda config=None: real(config or cfg))
    R.forget_decisions()
    H.forget_cache()
    yield cfg
    R.forget_decisions()
    H.forget_cache()


@pytest.fixture
def db(tmp_path, monkeypatch):
    database = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr(audiobook_routing, "_database", lambda: database)
    monkeypatch.setattr("database.music_database.get_database", lambda: database)
    return database


def _ctx(artist, album, album_genres=(), artist_genres=()):
    return {"artist": {"name": artist, "genres": list(artist_genres)},
            "album": {"name": album, "genres": list(album_genres),
                      "artists": [{"name": artist}]},
            "track_info": {"name": "Track", "artists": [{"name": artist}]}}


# ── the settings ────────────────────────────────────────────────────────────

def test_off_by_default():
    current = R.rules(Config())
    assert not current.enabled and not current.active
    assert current.match_holiday and current.name == "Holiday"


def test_a_folder_inside_the_main_library_is_refused(folders):
    main, _ = folders
    current = R.rules(Config({"library.secondary_enabled": True,
                              "library.secondary_path": str(main / "Holiday")}))
    assert not current.active and "inside" in current.problem


def test_no_rule_no_routing(folders):
    _, second = folders
    current = R.rules(Config({"library.secondary_enabled": True,
                              "library.secondary_path": str(second),
                              "library.secondary_match_holiday": False}))
    assert not current.active and current.problem == "no rule set"


def test_terms_split_on_bars_and_commas():
    assert R._terms("Christmas | xmas,  holiday ") == ["christmas", "xmas", "holiday"]


# ── the album decision ──────────────────────────────────────────────────────

@pytest.mark.parametrize("artist,album,genres,artist_genres", [
    ("Billie Holiday", "Lady Day: The Best of Billie Holiday", ["jazz"], ["jazz"]),
    ("Billie Holiday", "The Billie Holiday Collection", [], []),
    ("Green Day", "American Idiot", ["punk"], ["punk"]),
    ("Madonna", "Madonna", ["pop"], ["pop"]),
    ("Weezer", "Holiday", ["holiday"], []),          # an album called holiday by anyone
    ("Mike Christmas", "First Record", [], ["mike christmas"]),
    ("Mariah Carey", "Daydream", ["pop"], ["pop", "christmas"]),  # not her main genre
])
def test_names_never_route(config, artist, album, genres, artist_genres):
    hit, _ = R.album_matches(R.rules(), artist=artist, album=album, album_genres=genres,
                             artist_genres=artist_genres)
    assert not hit


@pytest.mark.parametrize("artist,album,genres,artist_genres", [
    ("Mariah Carey", "Merry Christmas", [], ["pop"]),
    ("Various", "Now That's What I Call Xmas", [], []),
    ("Pentatonix", "That's Christmas to Me", [], []),
    ("Someone", "Winter Songs", ["Christmas"], []),
    ("Mannheim Steamroller", "Fresh Aire 2", [], ["Christmas", "new age"]),
])
def test_holiday_albums_route(config, artist, album, genres, artist_genres):
    hit, why = R.album_matches(R.rules(), artist=artist, album=album, album_genres=genres,
                               artist_genres=artist_genres)
    assert hit and why == "holiday"


def test_genre_and_album_title_rules(config):
    config.values.update({"library.secondary_match_holiday": False,
                          "library.secondary_genres": "children|kids",
                          "library.secondary_album_titles": "lullaby"})
    current = R.rules()
    assert R.album_matches(current, artist="X", album="Y", album_genres=["Children's Music"]) == (True, "genre")
    assert R.album_matches(current, artist="X", album="Bedtime Lullaby Songs") == (True, "album title")
    # whole words: "lullabye" is not "lullaby"
    assert not R.album_matches(current, artist="X", album="Lullabye")[0]
    # holiday is off: a christmas album stays
    assert not R.album_matches(current, artist="X", album="Merry Christmas")[0]


def test_hand_flags_decide(config, db):
    H.set_override(db, "album", True, artist="Bing Crosby", album="Songs I Love")
    H.set_override(db, "album", False, artist="Someone", album="Christmas Island Surf")
    overrides = H.load_overrides(db)
    current = R.rules()
    assert R.album_matches(current, artist="Bing Crosby", album="Songs I Love",
                           overrides=overrides)[0]
    assert not R.album_matches(current, artist="Someone", album="Christmas Island Surf",
                               overrides=overrides)[0]


# ── downloads ───────────────────────────────────────────────────────────────

def test_a_holiday_album_download_goes_to_the_secondary_folder(config, db, folders):
    from core.imports.paths import transfer_root_for_context
    main, second = folders
    assert transfer_root_for_context(_ctx("Mariah Carey", "Merry Christmas")) == str(second)
    assert transfer_root_for_context(_ctx("Billie Holiday", "Lady in Satin")) == str(main)
    assert transfer_root_for_context(_ctx("Green Day", "Holiday")) == str(main)


def test_off_means_the_main_folder(config, db, folders):
    from core.imports.paths import transfer_root_for_context
    main, _ = folders
    config.values["library.secondary_enabled"] = False
    assert transfer_root_for_context(_ctx("Mariah Carey", "Merry Christmas")) == str(main)


def test_one_album_is_decided_once(config, db, folders):
    _, second = folders
    first = R.secondary_root_for_context(_ctx("Someone", "Winter Songs", ["Christmas"]), db)
    # a later track of the same album whose context lost its genres
    later = R.secondary_root_for_context(_ctx("Someone", "Winter Songs"), db)
    assert first == later == str(second)


def test_an_album_already_owned_stays_where_it_is(config, db, folders):
    main, second = folders
    with db._get_connection() as conn:
        seed.track(conn, "Mariah Carey", "Merry Christmas", "All I Want",
                   path=str(main / "Mariah Carey" / "Merry Christmas" / "01.flac"))
        seed.track(conn, "Someone", "Pop Record", "Song",
                   path=str(second / "Someone" / "Pop Record" / "01.flac"))
        conn.commit()
    # its other tracks join it in the main folder: it is never split
    assert R.secondary_root_for_context(_ctx("Mariah Carey", "Merry Christmas"), db) is None
    # an album someone put in the secondary folder keeps getting its tracks there
    assert R.secondary_root_for_context(_ctx("Someone", "Pop Record"), db) == str(second)


def test_library_genres_count(config, db, folders):
    _, second = folders
    with db._get_connection() as conn:
        seed.album(conn, "Someone", "Winter", genres='["Christmas"]')
        conn.commit()
    assert R.secondary_root_for_context(_ctx("Someone", "Winter"), db) == str(second)


def test_own_library_profiles_keep_their_folder(config, db, folders, monkeypatch, tmp_path):
    from core.imports import paths
    own = str(tmp_path / "own")
    monkeypatch.setattr(paths, "import_owner_id", lambda context: 2)
    monkeypatch.setattr(paths, "library_root_for_profile", lambda pid, **kw: own if pid == 2 else None)
    assert paths.transfer_root_for_context(_ctx("Mariah Carey", "Merry Christmas")) == own


def test_a_broken_rule_files_in_the_main_folder(config, folders, monkeypatch):
    from core.imports.paths import transfer_root_for_context
    main, _ = folders

    def boom(*a, **k):
        raise RuntimeError("no")
    monkeypatch.setattr(R, "decide_album", boom)
    assert transfer_root_for_context(_ctx("Mariah Carey", "Merry Christmas")) == str(main)


# ── albums already owned ────────────────────────────────────────────────────

def _album_on_disk(conn, folder, artist, album, titles, **album_cols):
    os.makedirs(folder, exist_ok=True)
    ids = []
    for i, title in enumerate(titles, 1):
        path = os.path.join(folder, f"{i:02d} - {title}.flac")
        with open(path, "wb") as fh:
            fh.write(b"x")
        ids.append(seed.track(conn, artist, album, title, path=path,
                              album_cols=album_cols or None))
    with open(os.path.join(folder, "cover.jpg"), "wb") as fh:
        fh.write(b"jpg")
    return ids


def test_preview_then_move(config, db, folders, monkeypatch):
    main, second = folders
    scans = []
    monkeypatch.setattr(R, "request_media_scan", lambda reason: scans.append(reason) or True)
    xmas = main / "Mariah Carey" / "Merry Christmas"
    pop = main / "Mariah Carey" / "Daydream"
    with db._get_connection() as conn:
        _album_on_disk(conn, str(xmas), "Mariah Carey", "Merry Christmas", ["All I Want", "Silent Night"])
        _album_on_disk(conn, str(pop), "Mariah Carey", "Daydream", ["Fantasy"])
        conn.commit()

    preview = R.move_owned(apply=False, database=db)
    assert [(i["title"], i["action"]) for i in preview["items"]] == [("Merry Christmas", "move")]
    assert xmas.is_dir() and not scans  # nothing touched

    done = R.move_owned(apply=True, database=db)
    assert done["moved"] == 1 and done["failed"] == 0
    moved = second / "Mariah Carey" / "Merry Christmas"
    assert (moved / "cover.jpg").is_file() and not xmas.exists()
    assert pop.is_dir()  # the artist folder still holds daydream
    with db._get_connection() as conn:
        paths = [r[0] for r in conn.execute(
            "SELECT f.path FROM lib2_track_files f JOIN lib2_tracks t ON t.id = f.track_id"
            " JOIN lib2_albums al ON al.id = t.album_id WHERE al.title = 'Merry Christmas'")]
    assert paths and all(p.startswith(str(moved)) for p in paths)
    assert scans


def test_loose_files_in_an_artist_folder_are_not_moved(config, db, folders):
    main, _ = folders
    with db._get_connection() as conn:
        _album_on_disk(conn, str(main / "Bing"), "Bing", "White Christmas", ["White Christmas"])
        _album_on_disk(conn, str(main / "Bing"), "Bing", "Swinging", ["Swing"])
        conn.commit()
    items = R.plan_move(db)["items"]
    assert [(i["title"], i["action"]) for i in items] == [("White Christmas", "shared folder")]
    assert R.move_owned(apply=True, database=db)["moved"] == 0


def test_an_existing_destination_is_never_overwritten(config, db, folders):
    main, second = folders
    with db._get_connection() as conn:
        _album_on_disk(conn, str(main / "A" / "Christmas"), "A", "Christmas", ["Carol"])
        conn.commit()
    (second / "A" / "Christmas").mkdir(parents=True)
    assert R.plan_move(db)["items"][0]["action"] == "exists"


def test_mostly_holiday_albums_count_when_owned(config, db, folders):
    main, _ = folders
    with db._get_connection() as conn:
        _album_on_disk(conn, str(main / "B" / "Winter Record"), "B", "Winter Record",
                       ["Jingle Bells", "Silent Night", "Snowfall"])
        conn.commit()
    items = R.plan_move(db)["items"]
    assert [i["title"] for i in items] == ["Winter Record"]


def test_move_needs_the_rules_on(config, db):
    config.values["library.secondary_enabled"] = False
    assert R.move_owned(apply=True, database=db)["active"] is False


# ── ownership and visibility ────────────────────────────────────────────────

def test_ownership_sees_the_secondary_folder(config, db, folders):
    _, second = folders
    with db._get_connection() as conn:
        seed.track(conn, "Mariah Carey", "Merry Christmas", "All I Want",
                   path=str(second / "Mariah Carey" / "Merry Christmas" / "01.flac"))
        seed.track(conn, "Queen", "Jazz", "Mustapha", path="/elsewhere/q.flac")
        conn.commit()
        for kind, alias, table in (("track", "t", "lib2_tracks"), ("album", "t", "lib2_albums")):
            sql, params = on_server_sql(kind, alias, "navidrome")
            rows = conn.execute(f"SELECT {alias}.id FROM {table} {alias} WHERE {sql}", params).fetchall()
            assert len(rows) == 1, kind
    # off: back to the server's word only
    config.values["library.secondary_enabled"] = False
    sql, params = on_server_sql("track", "t", "navidrome")
    assert len(params) == 2


def test_the_server_not_seeing_the_folder_is_a_warning(config, db, folders, monkeypatch):
    _, second = folders
    notes = []
    monkeypatch.setattr("core.profile_notify.notify_profile",
                        lambda pid, msg, kind="info", link=None: notes.append((kind, msg)))
    R._last_check.clear()
    folder = second / "Mariah Carey" / "Merry Christmas"
    folder.mkdir(parents=True)
    (folder / "01.flac").write_bytes(b"x")
    with db._get_connection() as conn:
        track_id = seed.track(conn, "Mariah Carey", "Merry Christmas", "All I Want",
                              path=str(folder / "01.flac"))
        conn.commit()
    result = R.visibility_check(db, server="navidrome")
    assert result["hidden"] and result["files_on_disk"] == 1
    monkeypatch.setattr(R, "visibility_check", lambda database=None, server=None: result)
    R.after_scan(db)
    R.after_scan(db)
    assert len(notes) == 1 and notes[0][0] == "warning"  # once, not every scan
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_tracks SET server_source = 'navidrome' WHERE id = ?", (track_id,))
        conn.commit()
    monkeypatch.undo()
    assert not R.visibility_check(db, server="navidrome")["hidden"]


def test_an_empty_folder_or_standalone_is_not_a_warning(config, db):
    assert not R.visibility_check(db, server="navidrome")["hidden"]
    assert not R.visibility_check(db, server="soulsync")["checked"]


def test_the_folder_is_a_shared_library_root(config, folders):
    from core.library2.library_roots import SHARED, desired_roots, normalize_prefix
    main, second = folders
    roots = desired_roots(str(main), [], [R.secondary_root()])
    assert roots == {normalize_prefix(main): SHARED, normalize_prefix(second): SHARED}


def test_the_folder_joins_the_library_folders_once(config, folders):
    _, second = folders
    config.values["library.music_paths"] = ["/archive"]
    assert R.ensure_library_folder(config)
    assert not R.ensure_library_folder(config)
    assert config.values["library.music_paths"] == ["/archive", str(second)]
