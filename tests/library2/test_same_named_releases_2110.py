"""#2110: weezer's three self-titled albums merged into one album.

the files carry no provider or release id, only tags, so the import writer
matched every "weezer" to the first one and the album showed three track 1s.
the tagged year and folder keep them apart now, and rows merged before the
fix split back out.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.imports.album_grouping import find_existing_soulsync_album_id
from core.library2 import autolink as A
from core.library2 import release_split as RS
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture(autouse=True)
def _no_artwork_warmup(monkeypatch):
    monkeypatch.setattr(A, "_warm_new_artwork", lambda *_a, **_k: None)


@pytest.fixture
def lib2_enabled(monkeypatch, legacy_db):
    monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)
    return legacy_db


def _flac(path, **tags):
    from tests.test_album_consistency_adopt import _make_flac
    path.parent.mkdir(parents=True, exist_ok=True)
    _make_flac(path, tags)
    return str(path)


def _weezer(root, year, folder, number, title):
    return _flac(root / "Weezer" / folder / f"{number:02d}.flac",
                 artist="Weezer", albumartist="Weezer", album="Weezer",
                 title=title, date=str(year), tracknumber=str(number))


def _albums(conn, title="Weezer"):
    return sorted(
        sorted(r["title"] for r in conn.execute(
            "SELECT title FROM lib2_tracks WHERE album_id=?", (a["id"],)))
        for a in conn.execute("SELECT id FROM lib2_albums WHERE title=?", (title,)))


def _refresh(path):
    return A.link_download_into_library_v2({
        "_final_processed_path": path, "username": "standalone_refresh",
        "_download_username": "standalone_refresh"})


def test_three_self_titled_albums_stay_three(lib2_enabled, imported_conn, tmp_path):
    """#2110: same name, same artist, different year and folder: three albums."""
    for year, folder, title in [(1994, "1994 - Weezer", "My Name Is Jonas"),
                                (2001, "2001 - Weezer", "Don't Let Go"),
                                (2008, "2008 - Weezer", "Troublemaker")]:
        assert _refresh(_weezer(tmp_path, year, folder, 1, title)) is not None
    assert _albums(imported_conn) == [["Don't Let Go"], ["My Name Is Jonas"], ["Troublemaker"]]


def test_a_scan_adopted_file_brings_its_year_and_folder(lib2_enabled, imported_conn, tmp_path):
    """#2110: the media-server scan path (track_info from tags) splits too."""
    for year, folder, title in [(1994, "1994 - Weezer", "My Name Is Jonas"),
                                (2001, "2001 - Weezer", "Don't Let Go")]:
        path = _weezer(tmp_path, year, folder, 1, title)
        assert A.link_download_into_library_v2({
            "_final_processed_path": path, "username": "plex", "_scan_discovered": True,
            "track_info": {"name": title, "artist": "Weezer", "album": "Weezer",
                           "track_number": 1, "year": str(year)},
        }) is not None
    assert _albums(imported_conn) == [["Don't Let Go"], ["My Name Is Jonas"]]


def test_disc_folders_of_one_year_stay_one_album(lib2_enabled, imported_conn, tmp_path):
    _refresh(_weezer(tmp_path, 2004, "Deluxe/CD1", 1, "My Name Is Jonas"))
    _refresh(_weezer(tmp_path, 2004, "Deluxe/CD2", 1, "Mykel & Carli"))
    assert _albums(imported_conn) == [["My Name Is Jonas", "Mykel & Carli"]]


def test_a_file_in_the_same_folder_joins_whatever_its_year(lib2_enabled, imported_conn, tmp_path):
    _refresh(_weezer(tmp_path, 1994, "Weezer", 1, "My Name Is Jonas"))
    _refresh(_weezer(tmp_path, 2004, "Weezer", 2, "No One Else"))
    assert _albums(imported_conn) == [["My Name Is Jonas", "No One Else"]]


# --- the import writer's own grouping (soulsync standalone) ------------------


@pytest.fixture()
def cur():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO lib2_artists(id, name, name_key) VALUES(1,'Weezer','weezer')")
    yield cursor
    conn.close()


def test_the_name_hash_never_folds_another_provider_album_in(cur):
    """#2110: the blue album holds the "weezer::weezer" hash; the green album
    has its own spotify id and year and must not land on it."""
    blue = cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, origin, server_source, server_id,"
        " spotify_id, external_ids, year) VALUES(1,'Weezer','library','soulsync','nk','sp-blue',?,1994)",
        (json.dumps({"deezer": "dz-blue"}),)).lastrowid
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Weezer",
        album_source_id="sp-green", source="spotify", release_year="2001-05-15") is None
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Weezer",
        album_source_id="dz-green", source="deezer", release_year=2001) is None
    # the same release still finds its row
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Weezer",
        album_source_id="sp-blue", source="spotify", release_year=1994) == blue
    # one album under two provider ids (clean and explicit, a region) is one album
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Weezer",
        album_source_id="sp-blue-explicit", source="spotify", release_year=1994) == blue
    assert find_existing_soulsync_album_id(
        cur, name_key_id="nk", artist_id=1, album_name="Weezer",
        album_source_id="sp-blue-explicit", source="spotify") == blue


def test_an_import_with_no_ids_goes_by_year_and_folder(cur):
    """#2110: tags only, through the standalone writer: the name hash's row
    holds another year's files in another folder."""
    blue = _merged(cur, [(1, "My Name Is Jonas", "/m/Weezer/1994 - Weezer/01.flac", _w(1994))],
                   year=1994)
    cur.execute("UPDATE lib2_albums SET server_source='soulsync', server_id='nk' WHERE id=?", (blue,))
    find = lambda year, folder: find_existing_soulsync_album_id(  # noqa: E731
        cur, name_key_id="nk", artist_id=1, album_name="Weezer",
        release_year=year, file_folder=folder)
    assert find(2001, "/m/Weezer/2001 - Weezer") is None
    assert find(1994, "/m/Weezer/Somewhere Else") == blue
    assert find(2001, "/m/Weezer/1994 - Weezer") == blue
    assert find(None, "/m/Weezer/2001 - Weezer") == blue


# --- healing rows merged before the fix --------------------------------------


def _merged(cur, files, *, year=None):
    album = cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, year) VALUES(1, 'Weezer', ?)",
        (year,)).lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,1,'primary')",
                (album,))
    for number, title, path, tags in files:
        track = cur.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number) VALUES(?,?,?,1)",
            (album, title, number)).lastrowid
        cur.execute("INSERT INTO lib2_track_files(track_id, path, tags_json, file_state)"
                    " VALUES(?,?,?, 'active')", (track, path, json.dumps(tags)))
    return album


def _w(year):
    return {"album": "Weezer", "year": str(year)}


def test_a_merged_weezer_row_splits_into_its_releases(cur):
    """#2110 heal: one row holding three releases becomes three rows."""
    album = _merged(cur, [
        (1, "My Name Is Jonas", "/m/Weezer/1994 - Weezer/01.flac", _w(1994)),
        (2, "No One Else", "/m/Weezer/1994 - Weezer/02.flac", _w(1994)),
        (1, "Don't Let Go", "/m/Weezer/2001 - Weezer/01.flac", _w(2001)),
        (1, "Troublemaker", "/m/Weezer/2008 - Weezer/01.flac", _w("2008-06-03")),
    ], year=2001)
    stats = RS.split_same_named_releases(cur.connection)
    assert stats == {"albums_split": 1, "albums_made": 2}
    conn = cur.connection
    rows = {r["year"]: sorted(t["title"] for t in conn.execute(
                "SELECT title FROM lib2_tracks WHERE album_id=?", (r["id"],)))
            for r in conn.execute("SELECT id, year FROM lib2_albums WHERE title='Weezer'").fetchall()}
    assert rows == {2001: ["Don't Let Go"], 1994: ["My Name Is Jonas", "No One Else"],
                    2008: ["Troublemaker"]}
    # the row's own year kept its folder, and every new row has the artist
    assert cur.execute("SELECT year FROM lib2_albums WHERE id=?", (album,)).fetchone()[0] == 2001
    assert cur.execute("SELECT COUNT(*) FROM lib2_album_artists").fetchone()[0] == 3
    # a second pass finds nothing
    assert RS.split_same_named_releases(cur.connection) == {"albums_split": 0, "albums_made": 0}


def test_a_split_keeps_each_profiles_watch_choice(cur):
    """#2110 heal: a profile that watched the merged row watches every release
    it splits into, and one that turned it off keeps it off."""
    album = _merged(cur, [
        (1, "My Name Is Jonas", "/m/Weezer/1994 - Weezer/01.flac", _w(1994)),
        (1, "Don't Let Go", "/m/Weezer/2001 - Weezer/01.flac", _w(2001)),
    ], year=2001)
    cur.execute("INSERT INTO lib2_monitor_rules(entity_type, entity_id, profile_id, monitored,"
                " provenance) VALUES('album', ?, 2, 1, 'user_explicit'),"
                " ('album', ?, 3, 0, 'user_explicit')", (album, album))
    RS.split_same_named_releases(cur.connection)
    made = [r[0] for r in cur.execute(
        "SELECT id FROM lib2_albums WHERE title='Weezer' AND id<>?", (album,))]
    assert len(made) == 1
    rules = sorted(tuple(r) for r in cur.execute(
        "SELECT profile_id, monitored, provenance FROM lib2_monitor_rules"
        " WHERE entity_type='album' AND entity_id=?", (made[0],)))
    assert rules == [(2, 1, "user_explicit"), (3, 0, "user_explicit")]


@pytest.mark.parametrize("files", [
    # disc folders of one year
    [(1, "A", "/m/W/CD1/01.flac", _w(2004)), (1, "B", "/m/W/CD2/01.flac", _w(2004))],
    # a single's file filed on the album: its own tags name another album
    [(1, "A", "/m/W/Weezer/01.flac", _w(1994)),
     (1, "B", "/m/W/Single/01.flac", {"album": "Buddy Holly", "year": "1995"})],
    # no year to tell them apart
    [(1, "A", "/m/W/One/01.flac", {"album": "Weezer"}),
     (1, "B", "/m/W/Two/01.flac", {"album": "Weezer"})],
])
def test_rows_that_are_not_proven_two_releases_stay_whole(cur, files):
    _merged(cur, files)
    assert RS.split_same_named_releases(cur.connection) == {"albums_split": 0, "albums_made": 0}
    assert cur.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 1


# --- wolf39us's library: the first heal never split it -----------------------

BLUE = ["My Name Is Jonas", "No One Else", "The World Has Turned and Left Me Here"]
GREEN = ["Don't Let Go", "Photograph", "Hash Pipe"]
RED = ["Troublemaker", "The Greatest Man That Ever Lived", "Pork and Beans"]
WHITE_IDS = {"deezer": "dz-white", "itunes": "it-white", "audiodb": "adb-white",
             "discogs": "r-white", "lastfm": "https://www.last.fm/music/Weezer/Weezer",
             "musicbrainz": "mb-white"}


def _wolf(cur, *, tagged=True):
    """one "Weezer" row holding the 1994, 2001 and 2008 albums, each in its
    own folder, matched to the 2016 one on every source."""
    files = []
    for year, names in ((2001, GREEN), (1994, BLUE), (2008, RED)):
        for number, name in enumerate(names, 1):
            files.append((number, name, f"/music/Weezer/{year} - Weezer/{number:02d} - {name}.flac",
                          _w(year) if tagged else {}))
    album = _merged(cur, files, year=2016)
    cur.execute(
        "UPDATE lib2_albums SET release_date='2016-10-28', musicbrainz_id='mb-white',"
        " external_ids=?, image_url='http://img/white.jpg', expected_track_count=10,"
        " origin='library', server_source='plex', server_id='900' WHERE id=?",
        (json.dumps(WHITE_IDS), album))
    for service in ("deezer", "musicbrainz"):
        cur.execute("INSERT INTO lib2_provider_attempts(entity_type, entity_id, service, status)"
                    " VALUES('album', ?, ?, 'matched')", (album, service))
    return album


def _weezers(conn):
    return {r["year"]: dict(r) for r in conn.execute(
        "SELECT id, year, release_date, spotify_id, musicbrainz_id, external_ids, image_url,"
        " origin, track_count FROM lib2_albums WHERE title='Weezer' ORDER BY year")}


def _titles(conn, album_id):
    return sorted(r[0] for r in conn.execute(
        "SELECT title FROM lib2_tracks WHERE album_id=?", (album_id,)))


def test_wolfs_merged_album_becomes_three_and_drops_the_2016_match(cur):
    """#2110 follow-up: one row, three track 1s, dated 2016-10-28."""
    album = _wolf(cur)
    conn = cur.connection
    assert RS.split_same_named_releases(conn) == {"albums_split": 1, "albums_made": 2}
    rows = _weezers(conn)
    assert sorted(rows) == [1994, 2001, 2008]
    assert _titles(conn, rows[1994]["id"]) == sorted(BLUE)
    assert _titles(conn, rows[2001]["id"]) == sorted(GREEN)
    assert _titles(conn, rows[2008]["id"]) == sorted(RED)
    for row in rows.values():
        # the 2016 release is none of them: no id, date or cover of it stays
        assert (row["release_date"], row["spotify_id"], row["musicbrainz_id"],
                row["image_url"]) == (None, None, None, None)
        assert json.loads(row["external_ids"] or "{}") == {}
        assert (row["origin"], row["track_count"]) == ("library", 3)
    # the sources look the kept row up again
    assert cur.execute("SELECT COUNT(*) FROM lib2_provider_attempts WHERE entity_id=?",
                       (album,)).fetchone()[0] == 0
    # again, and again: nothing more to do
    assert RS.split_same_named_releases(conn) == {"albums_split": 0, "albums_made": 0}
    assert sorted(_weezers(conn)) == [1994, 2001, 2008]


def test_the_release_the_row_is_matched_to_keeps_the_row_and_its_ids(cur):
    """a row matched to one of its own years: that release keeps the match."""
    album = _wolf(cur)
    cur.execute("UPDATE lib2_albums SET release_date='2001-05-15', year=2001 WHERE id=?", (album,))
    RS.split_same_named_releases(cur.connection)
    rows = _weezers(cur.connection)
    assert rows[2001]["id"] == album and rows[2001]["musicbrainz_id"] == "mb-white"
    assert json.loads(rows[2001]["external_ids"])["deezer"] == "dz-white"
    assert rows[1994]["external_ids"] in (None, "{}") and rows[2008]["musicbrainz_id"] is None


def test_unread_tags_wait_and_split_on_a_later_pass(cur, monkeypatch):
    """the first heal ran once per import and marked itself done; a row whose
    tags were not read yet (or that failed any proof that day) never split.
    it runs on every start now."""
    from core.library2 import bootstrap
    album = _wolf(cur, tagged=False)
    conn = cur.connection
    monkeypatch.setattr(bootstrap, "get_state", lambda _db: {"status": "done", "finished_at": "x"})
    monkeypatch.setattr("core.library2.mirror_outbox.drain", lambda _db: None)
    assert bootstrap._split_same_named_releases(object(), conn) == {}
    assert len(_weezers(conn)) == 1
    # a scan reads the tags
    for row in conn.execute("SELECT f.id, f.path FROM lib2_track_files f").fetchall():
        year = row["path"].split("/")[3][:4]
        conn.execute("UPDATE lib2_track_files SET tags_json=? WHERE id=?",
                     (json.dumps(_w(year)), row["id"]))
    assert bootstrap._split_same_named_releases(object(), conn) == {
        "albums_split": 1, "albums_made": 2}
    assert sorted(_weezers(conn)) == [1994, 2001, 2008]
    assert bootstrap._split_same_named_releases(object(), conn) == {}
    assert album in {r["id"] for r in _weezers(conn).values()}


def test_a_refresh_and_scan_heal_stays_inside_its_albums(cur):
    wolf = _wolf(cur)
    other = _merged(cur, [(1, "A", "/m/X/1990 - Weezer/01.flac", _w(1990)),
                          (1, "B", "/m/X/1991 - Weezer/01.flac", _w(1991))])
    assert RS.split_same_named_releases(cur.connection, [wolf]) == {
        "albums_split": 1, "albums_made": 2}
    assert _titles(cur.connection, other) == ["A", "B"]
    assert RS.split_same_named_releases(cur.connection, []) == {"albums_split": 0, "albums_made": 0}


def _d(year, disc=None):
    tags = _w(year)
    if disc:
        tags["disc_number"] = disc
    return tags


@pytest.mark.parametrize("files", [
    # two discs in disc folders, the bonus disc tagged with its own year and
    # no disc number: the same positions twice, still one album
    [(1, "A", "/m/W/Weezer (Deluxe)/CD1/01.flac", _d(1994)),
     (2, "B", "/m/W/Weezer (Deluxe)/CD1/02.flac", _d(1994)),
     (1, "C", "/m/W/Weezer (Deluxe)/CD2/01.flac", _d(2004)),
     (2, "D", "/m/W/Weezer (Deluxe)/CD2/02.flac", _d(2004))],
    [(1, "A", "/m/W/Weezer/Disc 1/01.flac", _d(1994)),
     (1, "C", "/m/W/Weezer/Disc 2 - Dusty Gems/01.flac", _d(2004))],
    # a deluxe whose bonus tracks sit in a subfolder of the album's own
    [(1, "A", "/m/W/Weezer (Deluxe)/01.flac", _d(1994)),
     (1, "C", "/m/W/Weezer (Deluxe)/Bonus/01.flac", _d(2004))],
    # folders differ, the tags say one year
    [(1, "A", "/m/W/One/01.flac", _d(1994)), (1, "C", "/m/W/Two/01.flac", _d(1994))],
    # a second copy of the album's own song, filed elsewhere under another
    # year (a reissue's stray file): a duplicate, not another album
    [(1, "All For You", "/m/B/2011 Stronger/01.flac", _d(2011)),
     (4, "Rust", "/m/B/2011 Stronger/04.flac", _d(2011)),
     (4, "Rust", "/m/Loose/04. Rust.flac", _d(2000))],
    # two years in two folders whose numbers fit together: one album
    [(1, "A", "/m/W/Part One/01.flac", _d(1994)), (2, "C", "/m/W/Part Two/02.flac", _d(1995))],
])
def test_multi_disc_deluxe_and_duplicates_are_left_whole(cur, files):
    album = _merged(cur, files, year=1994)
    cur.execute("UPDATE lib2_albums SET release_date='2016-10-28', external_ids=? WHERE id=?",
                (json.dumps(WHITE_IDS), album))
    assert RS.plan_for_album(cur.connection, album) is None
    assert RS.split_same_named_releases(cur.connection) == {"albums_split": 0, "albums_made": 0}
    assert cur.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0] == 1
    assert json.loads(cur.execute("SELECT external_ids FROM lib2_albums").fetchone()[0]) == WHITE_IDS


def test_a_deluxe_with_disc_folders_beside_another_release_still_splits(cur):
    album = _merged(cur, [
        (1, "A", "/m/W/2004 - Weezer/CD1/01.flac", _d(2004)),
        (1, "B", "/m/W/2004 - Weezer/CD2/01.flac", _d(2004)),
        (1, "C", "/m/W/2001 - Weezer/01.flac", _d(2001)),
    ], year=2004)
    RS.split_same_named_releases(cur.connection)
    rows = _weezers(cur.connection)
    assert _titles(cur.connection, rows[2004]["id"]) == ["A", "B"] and rows[2004]["id"] == album
    assert _titles(cur.connection, rows[2001]["id"]) == ["C"]


def test_a_file_in_a_disc_folder_of_the_album_joins_it_whatever_its_year(cur):
    album = _merged(cur, [(1, "A", "/m/W/Weezer (Deluxe)/CD1/01.flac", _d(1994))], year=1994)
    conn = cur.connection
    assert RS.is_other_release_by_year(conn, album, 2004, "/m/W/Weezer (Deluxe)/CD2") is False
    assert RS.is_other_release_by_year(conn, album, 2004, "/m/W/Weezer (Deluxe)") is False
    assert RS.is_other_release_by_year(conn, album, 2004, "/m/W/2004 - Weezer") is True


# --- and nothing puts them back together -------------------------------------


def _split_wolf(cur):
    _wolf(cur)
    RS.split_same_named_releases(cur.connection)
    return {year: row["id"] for year, row in _weezers(cur.connection).items()}


def test_a_scan_files_a_new_track_on_its_own_years_album(cur):
    ids = _split_wolf(cur)
    conn = cur.connection
    verdict = {year: RS.is_other_release_by_year(conn, album, 2001, "/music/Weezer/2001 - Weezer")
               for year, album in ids.items()}
    assert verdict == {1994: True, 2001: False, 2008: True}


def test_a_source_cannot_put_one_release_on_two_of_them(cur):
    """the enrichment workers match an album by its name: every "weezer" row
    got the first "weezer" the source listed, the 2016 one."""
    from core.library2.provider_writes import write_provider_enrichment
    ids = _split_wolf(cur)
    conn = cur.connection

    def match(year, provider_id, release_date):
        write_provider_enrichment(conn, entity_type="album", entity_id=ids[year], service="deezer",
                                  provider_id=provider_id, payload={"label": "Geffen"},
                                  backfill={"release_date": release_date, "year": release_date[:4]})
        row = conn.execute("SELECT external_ids, release_date FROM lib2_albums WHERE id=?",
                           (ids[year],)).fetchone()
        return json.loads(row["external_ids"] or "{}").get("deezer"), row["release_date"]

    for year in ids:
        assert match(year, "dz-white", "2016-10-28") == (None, None)
    assert match(2001, "dz-green", "2001-05-15") == ("dz-green", "2001-05-15")
    assert match(2008, "dz-green", "2001-05-15") == (None, None)
    # a source that sends no date still can't reuse a sibling's release
    write_provider_enrichment(conn, entity_type="album", entity_id=ids[1994], service="deezer",
                              provider_id="dz-green")
    assert RS._provider_ids(conn.execute(
        "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_albums WHERE id=?",
        (ids[1994],)).fetchone()) == {}


def test_an_album_with_no_same_named_sibling_is_matched_as_before(cur):
    """one owned album whose tags say 2004 takes the 1994 release a source finds."""
    from core.library2.provider_writes import write_provider_enrichment
    album = _merged(cur, [(1, "A", "/m/W/Weezer/01.flac", _w(2004))], year=2004)
    write_provider_enrichment(cur.connection, entity_type="album", entity_id=album,
                              service="deezer", provider_id="dz-blue",
                              backfill={"release_date": "1994-05-10"})
    assert json.loads(cur.execute("SELECT external_ids FROM lib2_albums WHERE id=?",
                                  (album,)).fetchone()[0]) == {"deezer": "dz-blue"}


def _index(conn):
    from core.library2.discography import _existing_release_index
    return _existing_release_index(conn, 1)


def test_a_discography_refresh_gives_each_its_own_years_release(cur):
    """the refresh matched a provider release to the library by name: all six
    "weezer"s landed on the first row."""
    from types import SimpleNamespace
    from core.library2.discography import _match_existing, _names_listed_several_times
    ids = _split_wolf(cur)
    index = _index(cur.connection)
    listed = [SimpleNamespace(title="Weezer", album_type="album", release_date=f"{y}-01-01", year=y)
              for y in (1994, 2001, 2008, 2016, 2019)]
    several = _names_listed_several_times(listed)
    assert several == {("weezer", "release")}
    found = {}
    for release in listed:
        row = _match_existing(index, title="Weezer", album_type="album",
                              provider_id=f"mb-{release.year}", source="musicbrainz",
                              release_date=release.release_date, year=release.year, several=True)
        found[release.year] = row and row["id"]
    assert found == {1994: ids[1994], 2001: ids[2001], 2008: ids[2008], 2016: None, 2019: None}
    # the library alone says the name is several releases, whatever the source lists
    assert _match_existing(index, title="Weezer", album_type="album", provider_id="x",
                           source="deezer", release_date="2016-04-01", year=2016) is None


def test_one_album_of_a_name_still_takes_its_listing_whatever_the_year(cur):
    from core.library2.discography import _match_existing
    album = _merged(cur, [(1, "A", "/m/W/Weezer/01.flac", _w(2004))], year=2004)
    row = _match_existing(_index(cur.connection), title="Weezer", album_type="album",
                          provider_id="sp-blue", source="spotify",
                          release_date="1994-05-10", year=1994)
    assert row["id"] == album


def _listing(cur, year, deezer):
    return cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, year, release_date, origin, monitored,"
        " external_ids, expected_track_count) VALUES(1,'Weezer',?,?, 'discography', 0, ?, 3)",
        (year, f"{year}-01-01", json.dumps({"deezer": deezer}))).lastrowid


def _fold(cur):
    from core.library2.dedup_repair import _fold_albums_within_artist
    stats = {"albums_folded": 0, "album_review": 0}
    for album in cur.execute("SELECT id FROM lib2_albums WHERE origin='discography'").fetchall():
        cur.execute("INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)"
                    " VALUES(?,1,'primary')", (album[0],))
    _fold_albums_within_artist(cur.connection, cur.connection.cursor(), 1, stats)
    return stats["albums_folded"]


def test_the_duplicate_fold_takes_a_listing_by_year_and_leaves_2016_alone(cur):
    """the title-twin fold put any same-named fileless listing on the first
    album with files: that is how a "weezer" row came to be the 2016 one."""
    ids = _split_wolf(cur)
    white = _listing(cur, 2016, "dz-white")
    red = _listing(cur, 2008, "dz-red")
    assert _fold(cur) == 1
    rows = _weezers(cur.connection)
    assert rows[2016]["id"] == white and rows[2016]["origin"] == "discography"
    assert cur.execute("SELECT 1 FROM lib2_albums WHERE id=?", (red,)).fetchone() is None
    assert json.loads(rows[2008]["external_ids"]) == {"deezer": "dz-red"}
    assert rows[2008]["id"] == ids[2008]
    assert json.loads(rows[1994]["external_ids"] or "{}") == {}


def test_one_owned_album_still_takes_its_listing_of_another_year(cur):
    album = _merged(cur, [(n, t, f"/m/W/Weezer/{n:02d}.flac", _w(2004))
                          for n, t in enumerate(BLUE, 1)], year=2004)
    _listing(cur, 1994, "dz-blue")
    assert _fold(cur) == 1
    assert json.loads(cur.execute("SELECT external_ids FROM lib2_albums WHERE id=?",
                                  (album,)).fetchone()[0]) == {"deezer": "dz-blue"}


def test_a_split_album_takes_the_listing_the_source_already_made_for_its_year(cur):
    """a refresh that ran before the split listed 2001 on its own: the split
    album becomes that release instead of sitting beside "0 of 3"."""
    _wolf(cur)
    green = _listing(cur, 2001, "dz-green")
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,1,'primary')",
                (green,))
    white = _listing(cur, 2016, "dz-white-2")
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,1,'primary')",
                (white,))
    RS.split_same_named_releases(cur.connection)
    rows = _weezers(cur.connection)
    assert sorted(rows) == [1994, 2001, 2008, 2016]
    assert json.loads(rows[2001]["external_ids"]) == {"deezer": "dz-green"}
    assert rows[2001]["release_date"] == "2001-01-01" and rows[2001]["origin"] == "library"
    assert _titles(cur.connection, rows[2001]["id"]) == sorted(GREEN)
    assert rows[2016]["id"] == white


def test_a_search_does_not_match_another_years_release(cur):
    """the per-album search matches by name too."""
    from core.library2.native_enrich import enrich_native_entity_for_service
    ids = _split_wolf(cur)
    white = [{"id": "dz-white", "name": "Weezer", "artist": "Weezer",
              "extra": "Weezer · 2016-04-01", "provider": "deezer"}]
    result = enrich_native_entity_for_service(
        cur.connection, "album", ids[1994], "deezer", searcher=lambda *_a: white)
    assert result["success"] is False and result["reason"] == "ambiguous"
    assert json.loads(cur.execute("SELECT external_ids FROM lib2_albums WHERE id=?",
                                  (ids[1994],)).fetchone()[0] or "{}") == {}
