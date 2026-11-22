"""#2239 / #2240: a release is filed under its album artist as a whole.

Ericsson, both on real MusicBrainz releases:

* #2239: 10cc's "The Ultimate Collection" (5df82a16) has disc 3 by Godley &
  Creme and Wax; "The Things We Do for Love" (9ed8d22c) mixes 10cc, Godley &
  Creme, Hotlegs and Graham Gouldman. 3.6.8 filed each performer's tracks as
  a release of that performer's, so the library held "The Ultimate
  Collection" under Godley & Creme too, and a reorganize of that row moved
  disc 3 to "Godley & Creme/Compilation/...".
* #2240: "Bubzstep" (b948eb19) is a Single credited "12th Planet, Barely
  Alive & PhaseOne". the library filed it under an artist named after the
  whole credit, as an album.
"""

from __future__ import annotations

import json
import os

import pytest

from core.library2 import autolink as A
from core.library2 import reorganize_plan

BUBZ_CREDIT = "12th Planet, BARELY ALIVE & PhaseOne"


@pytest.fixture(autouse=True)
def _no_artwork_warmup(monkeypatch):
    monkeypatch.setattr(A, "_warm_new_artwork", lambda *_args, **_kwargs: None)


@pytest.fixture
def lib2_enabled(monkeypatch, legacy_db):
    from core.settings import config_manager
    real_get = config_manager.get
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: (
        True if key == "features.library_v2" else real_get(key, default)))
    monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)
    return legacy_db


def _artist(conn, name):
    row = conn.execute("SELECT id FROM lib2_artists WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO lib2_artists(name) VALUES(?)", (name,)).lastrowid


def _album(conn, artist_id, title, **cols):
    keys = ["primary_artist_id", "title", "origin", *cols]
    album_id = conn.execute(
        f"INSERT INTO lib2_albums({', '.join(keys)}) VALUES({', '.join('?' * len(keys))})",
        (artist_id, title, "library", *cols.values())).lastrowid
    conn.execute("INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)"
                 " VALUES(?,?,'primary')", (album_id, artist_id))
    return album_id


def _track(conn, album_id, title, credits, *, number=1, disc=1, album_artist_tag=None,
           path=None):
    track_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number)"
        " VALUES(?,?,?,?)", (album_id, title, number, disc)).lastrowid
    for position, name in enumerate(credits):
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position)"
                     " VALUES(?,?,?,?)", (track_id, _artist(conn, name),
                                          "primary" if position == 0 else "featured", position))
    tags = json.dumps({"albumartist": album_artist_tag} if album_artist_tag else {})
    conn.execute("INSERT INTO lib2_track_files(track_id, path, is_primary, tags_json)"
                 " VALUES(?,?,1,?)",
                 (track_id, path or f"/music/x/{album_id}/{track_id}.flac", tags))
    return track_id


def _album_of(conn, track_id):
    return conn.execute(
        "SELECT al.id, al.title, al.album_type, ar.name AS artist FROM lib2_tracks t"
        " JOIN lib2_albums al ON al.id = t.album_id"
        " JOIN lib2_artists ar ON ar.id = al.primary_artist_id WHERE t.id=?",
        (track_id,)).fetchone()


def _repair(conn):
    from core.library2.release_artist_repair import rehome_track_lead_releases
    return rehome_track_lead_releases(conn, read_album_artist=lambda p: "")


# --- #2239: 10cc's box sets -----------------------------------------------------

ULTIMATE = "The Ultimate Collection"
THINGS = "The Things We Do for Love: The Ultimate Hits and Beyond"


def _ultimate_collection_as_368_filed_it(conn):
    """disc 1 on 10cc's row, disc 3's Godley & Creme and Wax tracks each on a
    row of the performer's own (no year: the old filing set none)"""
    tencc = _artist(conn, "10cc")
    main = _album(conn, tencc, ULTIMATE, year=2008, album_type="compilation")
    kept = _track(conn, main, "Rubber Bullets", ["10cc"], number=1, album_artist_tag="10cc")
    gc = _album(conn, _artist(conn, "Godley & Creme"), ULTIMATE, album_type="compilation")
    moved = [_track(conn, gc, title, ["Godley & Creme"], number=n, disc=3,
                    album_artist_tag="10cc")
             for n, title in ((1, "Five O’Clock in the Morning"),
                              (2, "An Englishman in New York"), (5, "Cry"))]
    wax = _album(conn, _artist(conn, "Wax"), ULTIMATE, album_type="compilation")
    moved.append(_track(conn, wax, "Right Between the Eyes", ["Wax"], number=7, disc=3,
                        album_artist_tag="10cc"))
    return main, kept, moved


def test_10cc_box_set_split_per_performer_goes_back_together(imported_conn):
    main, kept, moved = _ultimate_collection_as_368_filed_it(imported_conn)

    stats = _repair(imported_conn)

    assert stats["albums_merged"] == 2
    for track_id in (kept, *moved):
        row = _album_of(imported_conn, track_id)
        assert (row["id"], row["artist"]) == (main, "10cc")
    # the performers keep their own track credits
    credited = {r[0] for r in imported_conn.execute(
        "SELECT ar.name FROM lib2_track_artists ta JOIN lib2_artists ar ON ar.id=ta.artist_id"
        " WHERE ta.track_id IN (?,?)", (moved[0], moved[-1]))}
    assert credited == {"Godley & Creme", "Wax"}


def test_things_we_do_for_love_split_over_four_performers_goes_back(imported_conn):
    tencc = _artist(imported_conn, "10cc")
    main = _album(imported_conn, tencc, THINGS, year=2022, album_type="compilation")
    _track(imported_conn, main, "The Things We Do for Love", ["10cc"], number=1,
           album_artist_tag="10cc")
    others = []
    for performer, number, title in (("Godley & Creme", 15, "Wedding Bells"),
                                     ("Hotlegs", 17, "Neanderthal Man"),
                                     ("Graham Gouldman", 18, "Look Through Any Window")):
        row = _album(imported_conn, _artist(imported_conn, performer), THINGS,
                     album_type="compilation")
        others.append(_track(imported_conn, row, title, [performer], number=number,
                             album_artist_tag="10cc"))

    _repair(imported_conn)

    assert {_album_of(imported_conn, t)["id"] for t in others} == {main}


def test_a_reorganize_of_the_box_set_keeps_disc_3_under_10cc(imported_conn, monkeypatch, tmp_path):
    """after the merge, the plan files disc 3 where the import put it"""
    from core.imports import paths

    class _Cfg:
        values = {
            "file_organization.templates": {
                "album_path": "$albumartist/$albumtype/$albumartist - $album ($year)/$disc-$track - $title",
                "compilation_path": "$albumartist/$albumtype/$albumartist - $album ($year)/$disc-$track - $title",
            },
            "file_organization.enabled": True,
            "file_organization.collab_artist_mode": "first",
            "soulseek.transfer_path": str(tmp_path),
        }

        def get(self, key, default=None):
            return self.values.get(key, default)

        def get_active_media_server(self):
            return "primary"

    monkeypatch.setattr(paths, "_get_config_manager", lambda: _Cfg())
    monkeypatch.setattr(paths, "_get_album_tracks_for_source", lambda *a: None)
    main, _kept, moved = _ultimate_collection_as_368_filed_it(imported_conn)
    _repair(imported_conn)

    plan = reorganize_plan.plan_album_reorganize(
        imported_conn, main, build_final_path_fn=paths.build_final_path_for_track,
        transfer_dir=str(tmp_path), resolve_file_path_fn=lambda p: p)
    by_track = {t["track_id"]: t["new_path"].replace(os.sep, "/") for t in plan["tracks"]}
    folder = "10cc/Compilation/10cc - The Ultimate Collection (2008)/"
    assert all(path.startswith(folder) for path in by_track.values()), by_track
    assert by_track[moved[0]].startswith(folder + "03-01")


def test_a_box_set_import_files_every_disc_under_10cc(lib2_enabled, imported_conn, monkeypatch):
    """the import's shape: the release is 10cc's, disc 3's track is Godley &
    Creme's, the file's album artist tag says 10cc"""
    monkeypatch.setattr("core.imports.track_number.read_embedded_album_artist",
                        lambda path: "10cc")
    album = {"name": ULTIMATE, "album_type": "compilation", "total_tracks": 38,
             "release_date": "2008-03-26", "artists": [{"name": "10cc"}]}
    ids = []
    for number, disc, title, performer in ((1, 1, "Rubber Bullets", "10cc"),
                                           (1, 3, "Five O’Clock in the Morning", "Godley & Creme"),
                                           (7, 3, "Right Between the Eyes", "Wax")):
        ids.append(A.link_download_into_library_v2({
            "_final_processed_path": f"/music/10cc/Compilation/{disc}-{number:02d} {title}.flac",
            "username": "import",
            "spotify_artist": {"name": "10cc"},
            "spotify_album": album,
            "track_info": {"name": title, "artists": [{"name": performer}],
                           "track_number": number, "disc_number": disc,
                           "provider": "musicbrainz"},
            "_embedded_id_tags": {},
        }))
    rows = {imported_conn.execute(
        "SELECT al.id, ar.name FROM lib2_track_files tf JOIN lib2_tracks t ON t.id=tf.track_id"
        " JOIN lib2_albums al ON al.id=t.album_id JOIN lib2_artists ar"
        " ON ar.id=al.primary_artist_id WHERE tf.id=?", (fid,)).fetchone()[:] for fid in ids}
    assert len(rows) == 1 and next(iter(rows))[1] == "10cc"


# --- #2240: Bubzstep --------------------------------------------------------------

def test_bubzstep_filed_under_its_whole_credit_goes_to_12th_planet(imported_conn):
    """3.6.8's shape: an artist named after the whole credit holds the single.
    12th Planet and PhaseOne are library artists already"""
    _album(imported_conn, _artist(imported_conn, "12th Planet"), "Some Other Release")
    _artist(imported_conn, "PhaseOne")
    combined = _artist(imported_conn, BUBZ_CREDIT)
    album = _album(imported_conn, combined, "Bubzstep", album_type="album")
    track = _track(imported_conn, album, "Bubzstep", [BUBZ_CREDIT],
                   album_artist_tag=BUBZ_CREDIT)

    _repair(imported_conn)

    row = _album_of(imported_conn, track)
    assert row["artist"] == "12th Planet"
    assert row["album_type"] == "single"


def test_bubzstep_filed_under_phaseone_with_a_whole_credit_tag(imported_conn):
    """the #2142 repair's own shape with 'all artists combined': the tag is
    the whole credit, the row sits under PhaseOne. it goes to 12th Planet,
    never to a new artist named after the credit"""
    phase = _artist(imported_conn, "PhaseOne")
    _artist(imported_conn, "12th Planet")
    album = _album(imported_conn, phase, "Bubzstep", album_type="single")
    track = _track(imported_conn, album, "Bubzstep", ["PhaseOne", "12th Planet", "BARELY ALIVE"],
                   album_artist_tag=BUBZ_CREDIT)

    _repair(imported_conn)

    assert _album_of(imported_conn, track)["artist"] == "12th Planet"
    assert imported_conn.execute("SELECT 1 FROM lib2_artists WHERE name=?",
                                 (BUBZ_CREDIT,)).fetchone() is None


def test_a_band_named_like_a_list_stays_whole(imported_conn):
    """Earth, Wind & Fire is one act: nothing in the library says its parts
    are artists, so its album stays where it is"""
    ewf = _artist(imported_conn, "Earth, Wind & Fire")
    album = _album(imported_conn, ewf, "September", album_type="single")
    track = _track(imported_conn, album, "September", ["Earth, Wind & Fire"],
                   album_artist_tag="Earth, Wind & Fire")
    _repair(imported_conn)
    assert _album_of(imported_conn, track)["artist"] == "Earth, Wind & Fire"


def test_a_locked_combined_credit_album_stays(imported_conn):
    _artist(imported_conn, "12th Planet")
    _artist(imported_conn, "PhaseOne")
    album = _album(imported_conn, _artist(imported_conn, BUBZ_CREDIT), "Bubzstep",
                   album_type="album")
    track = _track(imported_conn, album, "Bubzstep", [BUBZ_CREDIT],
                   album_artist_tag=BUBZ_CREDIT)
    from core.library2.locks import LIB2_ENTITY_LOCKS_DDL
    imported_conn.execute(LIB2_ENTITY_LOCKS_DDL)
    imported_conn.execute("INSERT INTO lib2_entity_locks(entity_type, entity_id, scopes)"
                          " VALUES('album', ?, 'tags')", (album,))
    _repair(imported_conn)
    assert _album_of(imported_conn, track)["artist"] == BUBZ_CREDIT


def _itunes_bubzstep(path):
    """the itunes shape: the credit is one artist string, every album 'album'"""
    return {
        "_final_processed_path": path,
        "username": "import",
        "spotify_artist": {"name": BUBZ_CREDIT},
        "spotify_album": {"name": "Bubzstep", "album_type": "album", "total_tracks": 1,
                          "artists": [{"name": BUBZ_CREDIT}]},
        "track_info": {"name": "Bubzstep", "artists": [{"name": BUBZ_CREDIT}],
                       "provider": "itunes"},
        "_embedded_id_tags": {},
    }


@pytest.mark.parametrize("tag", [BUBZ_CREDIT, "12th Planet"])
def test_bubzstep_import_files_one_single_under_12th_planet(lib2_enabled, imported_conn,
                                                           monkeypatch, tag):
    """whichever collab mode tagged it, the library files it under the first
    credited artist, as a single"""
    _album(imported_conn, _artist(imported_conn, "12th Planet"), "Some Other Release")
    _artist(imported_conn, "PhaseOne")
    imported_conn.commit()
    monkeypatch.setattr("core.imports.track_number.read_embedded_album_artist",
                        lambda path: tag)
    file_id = A.link_download_into_library_v2(
        _itunes_bubzstep("/music/12th Planet/Bubzstep/01 Bubzstep.flac"))
    row = imported_conn.execute(
        "SELECT ar.name, al.album_type FROM lib2_track_files tf"
        " JOIN lib2_tracks t ON t.id=tf.track_id JOIN lib2_albums al ON al.id=t.album_id"
        " JOIN lib2_artists ar ON ar.id=al.primary_artist_id WHERE tf.id=?",
        (file_id,)).fetchone()
    assert (row[0], row[1]) == ("12th Planet", "single")


def test_bubzstep_musicbrainz_shape_is_a_single_under_12th_planet(lib2_enabled, imported_conn,
                                                                 monkeypatch):
    monkeypatch.setattr("core.imports.track_number.read_embedded_album_artist",
                        lambda path: "12th Planet, Barely Alive & PhaseOne")
    credit = [{"name": "12th Planet"}, {"name": "Barely Alive"}, {"name": "PhaseOne"}]
    file_id = A.link_download_into_library_v2({
        "_final_processed_path": "/music/12th Planet/Bubzstep/01 Bubzstep.flac",
        "username": "import",
        "spotify_artist": {"name": "12th Planet"},
        "spotify_album": {"name": "Bubzstep", "album_type": "single", "total_tracks": 1,
                          "artists": credit},
        "track_info": {"name": "Bubzstep", "artists": credit, "provider": "musicbrainz"},
        "_embedded_id_tags": {},
    })
    row = imported_conn.execute(
        "SELECT ar.name, al.album_type FROM lib2_track_files tf"
        " JOIN lib2_tracks t ON t.id=tf.track_id JOIN lib2_albums al ON al.id=t.album_id"
        " JOIN lib2_artists ar ON ar.id=al.primary_artist_id WHERE tf.id=?",
        (file_id,)).fetchone()
    assert (row[0], row[1]) == ("12th Planet", "single")


def test_a_whole_credit_row_whose_files_name_someone_else_stays(imported_conn):
    """the files tag another artist entirely: not this pass's call"""
    _artist(imported_conn, "12th Planet")
    _artist(imported_conn, "PhaseOne")
    album = _album(imported_conn, _artist(imported_conn, BUBZ_CREDIT), "Bubzstep",
                   album_type="album")
    track = _track(imported_conn, album, "Bubzstep", ["Somebody Else"],
                   album_artist_tag="Somebody Else")
    _repair(imported_conn)
    assert _album_of(imported_conn, track)["artist"] == BUBZ_CREDIT


def test_a_shape_read_kind_stays_open_to_a_source(lib2_enabled, imported_conn, monkeypatch):
    """the single kind came off the release's shape, so enrichment that
    does say a kind can still set it (album_type_known stays 0)"""
    _album(imported_conn, _artist(imported_conn, "12th Planet"), "Some Other Release")
    _artist(imported_conn, "PhaseOne")
    imported_conn.commit()
    monkeypatch.setattr("core.imports.track_number.read_embedded_album_artist",
                        lambda path: "12th Planet")
    file_id = A.link_download_into_library_v2(
        _itunes_bubzstep("/music/12th Planet/Bubzstep/01 Bubzstep.flac"))
    row = imported_conn.execute(
        "SELECT al.album_type, al.album_type_known FROM lib2_track_files tf"
        " JOIN lib2_tracks t ON t.id=tf.track_id JOIN lib2_albums al ON al.id=t.album_id"
        " WHERE tf.id=?", (file_id,)).fetchone()
    assert (row[0], row[1]) == ("single", 0)


def test_a_scanned_file_tagged_with_the_whole_credit_files_under_12th_planet(
        lib2_enabled, imported_conn, monkeypatch):
    """a media server scan adopts a file by its album artist tag"""
    _album(imported_conn, _artist(imported_conn, "12th Planet"), "Some Other Release")
    _artist(imported_conn, "PhaseOne")
    imported_conn.commit()
    monkeypatch.setattr("core.imports.track_number.read_embedded_album_artist",
                        lambda path: BUBZ_CREDIT)
    file_id = A.link_download_into_library_v2({
        "_final_processed_path": "/music/x/Bubzstep.flac", "username": "plex",
        "_scan_discovered": True,
        "track_info": {"name": "Bubzstep", "artist": BUBZ_CREDIT, "album": "Bubzstep"}})
    row = imported_conn.execute(
        "SELECT ar.name FROM lib2_track_files tf JOIN lib2_tracks t ON t.id=tf.track_id"
        " JOIN lib2_albums al ON al.id=t.album_id"
        " JOIN lib2_artists ar ON ar.id=al.primary_artist_id WHERE tf.id=?",
        (file_id,)).fetchone()
    assert row[0] == "12th Planet"
