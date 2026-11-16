"""'first listed artist' must not split a name the library holds whole (#2131).

wolf39us: Preview Rename / Organize for "Above & Beyond" planned
"Above/2015 - We Are All We Need/03 - Blue Sky Action.flac". The library
held ghost "Above" and "Beyond" rows (an old split) next to the real
"Above & Beyond" artist, and the library vote in split_credit_primary
counted the two parts as a list without ever asking whether the library
holds the whole credit as one act. The vote now only counts when the
whole credit is NOT a library artist. Stronger evidence — hard
separators, a name this download vouches for standing in the credit —
still splits, as #2028 requires.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import core.imports.paths as import_paths
import core.library_reorganize as lr
from core.imports.collab_artist import collab_primary, split_credit_primary
from core.imports.paths import build_final_path_for_track
from core.metadata import source as src_module

ABOVE_AND_BEYOND = "Above & Beyond"
GHOST_LIB = ("Above", "Beyond", ABOVE_AND_BEYOND)
CREED = "Creed, 3 Doors Down & Finger Eleven"
TEMPLATE = "$albumartist/$year - $album/$track - $title"


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)

    def get_active_media_server(self):
        return "primary"


def _lib(names):
    """Stand-in for library_has_artist. It casefolds (full Unicode);
    production uses SQLite LOWER(), which is ASCII-only — the same
    limitation the segment lookups have always had, now also on the
    whole-name lookup."""
    known = {n.casefold() for n in names}
    return lambda n: n.casefold() in known


def _whole_lib(names):
    """Stand-in for library_has_whole_artist: the whole credit counts
    as held when its name is in the set (the real lookup's alias rules
    are exercised against SQLite in
    test_whole_lookup_against_a_real_database)."""
    known = {n.casefold() for n in names}
    return lambda n, segments=(): n.casefold() in known


def _cfg(monkeypatch, tmp_path, library=()):
    config = _Config({
        "soulseek.transfer_path": str(tmp_path),
        "file_organization.enabled": True,
        "file_organization.templates": {
            "album_path": TEMPLATE, "single_path": TEMPLATE,
            "compilation_path": TEMPLATE,
        },
        "file_organization.collab_artist_mode": "first",
        "file_organization.detect_compilations": False,
    })
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
    monkeypatch.setattr(import_paths, "library_has_artist", _lib(library))
    monkeypatch.setattr(
        import_paths, "library_has_whole_artist", _whole_lib(library))
    monkeypatch.setattr(lr, "_preserve_casing_enabled", lambda: True)
    monkeypatch.setattr(lr, "_feat_in_title_enabled", lambda: False)
    return config


def _reorganize_path(tmp_path, album_artist, album, title, track_artists,
                     track_number=3, year="2015-01-16"):
    """The Library v2 preview's own context shape, planned end to end."""
    api_album = {"id": "x", "name": album, "release_date": year,
                 "total_tracks": 16, "artists": [{"name": album_artist}],
                 "record_type": "album"}
    api_track = {"name": title, "track_number": track_number, "disc_number": 1,
                 "artists": [{"name": a} for a in track_artists]}
    context = lr._build_post_process_context(
        api_album, api_track, album_artist, album, 1,
        album_artist=album_artist)
    path, _ = build_final_path_for_track(
        context, context["spotify_artist"], lr._build_album_info(context),
        ".flac", create_dirs=False)
    return os.path.relpath(path, tmp_path)


def test_split_rule_keeps_a_name_the_library_holds_whole():
    assert split_credit_primary(
        ABOVE_AND_BEYOND, [ABOVE_AND_BEYOND], _lib(GHOST_LIB)) == ""
    # the deliberate boundary: both parts but no whole row still splits,
    # exactly the #2028 library-vouches case
    assert split_credit_primary(
        ABOVE_AND_BEYOND, [ABOVE_AND_BEYOND], _lib(("Above", "Beyond"))) == "Above"


def test_reorganize_preview_keeps_above_and_beyond(monkeypatch, tmp_path):
    """The screenshot, end to end: the exact planned path stays whole."""
    _cfg(monkeypatch, tmp_path, library=GHOST_LIB)
    rel = _reorganize_path(
        tmp_path, ABOVE_AND_BEYOND, "We Are All We Need", "Blue Sky Action",
        [ABOVE_AND_BEYOND])
    assert rel == os.path.join(
        "Above & Beyond", "2015 - We Are All We Need",
        "03 - Blue Sky Action.flac")


def test_presents_album_still_whole(monkeypatch, tmp_path):
    """Pin of pre-existing behavior (the screenshot's second row was
    already right): the library vote never reaches two for this credit,
    with or without the guard."""
    _cfg(monkeypatch, tmp_path, library=GHOST_LIB)
    rel = _reorganize_path(
        tmp_path, "Above & Beyond presents OceanLab",
        "Sirens of the Sea Remixed", "Miracle (Above & Beyond club mix)",
        ["Above & Beyond presents OceanLab"], track_number=8, year="2009-01-01")
    assert rel.split(os.sep)[0] == "Above & Beyond presents OceanLab"


def test_duo_and_lead_band_names_with_ghost_rows_stay_whole():
    for name, parts in (
        ("Simon & Garfunkel", ("Simon", "Garfunkel")),
        ("Tom Petty & The Heartbreakers", ("Tom Petty", "The Heartbreakers")),
    ):
        lib = _lib((*parts, name))
        assert split_credit_primary(name, [name], lib) == "", name
        assert collab_primary(
            name, [{"name": name}], [{"name": name}],
            known_names=[name], library_has=lib) == "", name


def test_creed_still_splits_with_a_legacy_combined_row():
    """Known-name evidence is stronger than the guard: a track naming one
    of the parts splits even when an old import left the combined row."""
    lib = _lib(("Creed", "3 Doors Down", CREED))
    assert collab_primary(
        CREED, [{"name": CREED}], [{"name": "3 Doors Down"}],
        known_names=["3 Doors Down"], library_has=lib) == "Creed"


def test_semicolon_credit_still_splits_with_its_whole_row_present():
    whole = "Johannes Brahms; Joshua Bell"
    lib = _lib((whole, "Johannes Brahms"))
    assert collab_primary(
        whole, [{"name": whole}], [{"name": whole}],
        known_names=[whole], library_has=lib) == "Johannes Brahms"


def test_creed_library_vote_stays_combined_with_a_legacy_combined_row():
    """The plan's accepted price, pinned: the library vote alone cannot
    tell a real act row ("Above & Beyond") from an artifact row a
    pre-#2028 combined import left ("Creed, 3 Doors Down & Finger
    Eleven"). With the combined row present and nothing stronger naming
    a part, the credit stays whole; #2028's split survives on the
    stronger evidence (previous test) and on libraries with no combined
    row (the #2028 suite)."""
    lib = _lib(("Creed", "3 Doors Down", CREED))
    assert collab_primary(
        CREED, [{"name": CREED}], [{"name": CREED}],
        known_names=[CREED], library_has=lib) == ""


def test_guard_also_covers_the_multi_segment_lead():
    """Same trade-off, one level up: with a row for the whole 3-name
    credit, the guard keeps it whole instead of filing under the
    library-known duo prefix the vote would have returned; without the
    whole row the duo lead still wins."""
    credit = "Daryl Hall & John Oates & Guest"
    duo = "Daryl Hall & John Oates"
    lib = _lib(("Daryl Hall", "John Oates", duo, credit))
    assert split_credit_primary(credit, [], lib) == ""
    lib_no_whole = _lib(("Daryl Hall", "John Oates", duo))
    assert split_credit_primary(credit, [], lib_no_whole) == duo


def test_double_spaced_credit_still_matches_its_library_row():
    assert split_credit_primary(
        "Above &  Beyond", [ABOVE_AND_BEYOND], _lib(GHOST_LIB)) == ""


def test_no_library_and_a_failing_whole_lookup():
    # no library at all: the vote never runs, the credit stays whole
    assert split_credit_primary(ABOVE_AND_BEYOND, [ABOVE_AND_BEYOND], None) == ""

    def flaky(name):
        if " ".join(name.split()) == ABOVE_AND_BEYOND:
            raise RuntimeError("db gone")
        return name.casefold() in ("above", "beyond")

    # segment lookups succeed, the whole-name lookup fails: in_library
    # swallows it as a miss, so the vote still splits (today's behavior)
    assert split_credit_primary(
        ABOVE_AND_BEYOND, [ABOVE_AND_BEYOND], flaky) == "Above"

    def raising_whole(name, segments=()):
        raise RuntimeError("db gone")

    # same fail-open when the dedicated whole lookup is the one raising
    assert split_credit_primary(
        ABOVE_AND_BEYOND, [ABOVE_AND_BEYOND], _lib(GHOST_LIB),
        whole_library_has=raising_whole) == "Above"


def test_slash_credit_still_splits_with_its_whole_row_present():
    whole = "Artist A / Artist B"
    lib = _lib((whole, "Artist A"))
    assert split_credit_primary(whole, [whole], lib) == "Artist A"


def test_itunes_fallback_is_consulted_and_its_answer_used(monkeypatch, tmp_path):
    """What this pins (the resolver is mocked, so its real-world answer
    is not what's tested): with the guard holding the name whole, the
    single-artist iTunes fallback — reachable whenever collab_primary
    returns "" — is consulted once for the artist id, and the folder
    follows its answer."""
    _cfg(monkeypatch, tmp_path, library=GHOST_LIB)
    client = MagicMock()
    client.resolve_primary_artist.return_value = ABOVE_AND_BEYOND
    monkeypatch.setattr(import_paths, "_get_itunes_client", lambda: client)
    context = {
        "artist": {"name": ABOVE_AND_BEYOND, "id": "123456"},
        "album": {"name": "We Are All We Need", "id": "a1",
                  "release_date": "2015-01-16", "total_tracks": 16,
                  "album_type": "album",
                  "artists": [{"name": ABOVE_AND_BEYOND}]},
        "track_info": {"name": "Blue Sky Action", "id": "t1",
                       "track_number": 3, "disc_number": 1,
                       "artists": [{"name": ABOVE_AND_BEYOND}]},
        "original_search_result": {
            "title": "Blue Sky Action", "clean_title": "Blue Sky Action",
            "clean_album": "We Are All We Need",
            "artists": [{"name": ABOVE_AND_BEYOND}]},
        "source": "itunes", "is_album_download": True,
    }
    path, _ = build_final_path_for_track(
        context, {"name": ABOVE_AND_BEYOND, "id": "123456"},
        {"is_album": True, "album_name": "We Are All We Need",
         "track_number": 3, "disc_number": 1, "album_type": "album"},
        ".flac", create_dirs=False)
    assert os.path.relpath(path, tmp_path).split(os.sep)[0] == ABOVE_AND_BEYOND
    client.resolve_primary_artist.assert_called_once_with("123456")


def test_the_whole_lookup_decides_the_vote():
    """The vote splits exactly when the whole lookup says the library
    does not hold the credit as one act, and not otherwise."""
    assert split_credit_primary(
        ABOVE_AND_BEYOND, [ABOVE_AND_BEYOND], _lib(GHOST_LIB),
        whole_library_has=_whole_lib(("Above", "Beyond"))) == "Above"
    assert split_credit_primary(
        ABOVE_AND_BEYOND, [ABOVE_AND_BEYOND], _lib(GHOST_LIB),
        whole_library_has=_whole_lib(GHOST_LIB)) == ""


def test_reorganize_when_the_whole_lookup_misses_still_splits(monkeypatch, tmp_path):
    """The ghost-split shape end to end: the only whole row is an alias
    of its lead part, so the whole lookup answers False (its SQLite
    rules are pinned in the next test) and the preview still splits."""
    _cfg(monkeypatch, tmp_path, library=GHOST_LIB)
    monkeypatch.setattr(import_paths, "library_has_whole_artist",
                        lambda n, segments=(): False)
    rel = _reorganize_path(
        tmp_path, ABOVE_AND_BEYOND, "We Are All We Need", "Blue Sky Action",
        [ABOVE_AND_BEYOND])
    assert rel.split(os.sep)[0] == "Above"


def test_whole_lookup_against_a_real_database(monkeypatch, tmp_path):
    """library_has_whole_artist over real SQLite rows:
    - a canonical whole row counts;
    - an alias of one of the credit's parts (the ghost split's
      tombstone for a combined row it resolved as a list) does not;
    - an alias of the same act under another spelling (a §40 alias,
      whose canonical name is not a part) counts;
    - a self-canonical row (canonical_artist_id = id) counts;
    - an alias of a later part, an alias whose target row is gone, and
      an alias whose target is a part spelled with an accent the credit
      lacks all behave like the first-part tombstone."""
    import sqlite3

    import database.music_database as music_db
    from core.imports.collab_artist import (
        library_has_artist, library_has_whole_artist)

    db_path = tmp_path / "music.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE lib2_artists ("
                 "id INTEGER PRIMARY KEY, name TEXT, canonical_artist_id INTEGER)")
    lead = conn.execute("INSERT INTO lib2_artists(name) VALUES('Above')").lastrowid
    conn.execute("INSERT INTO lib2_artists(name) VALUES('Beyond')")
    conn.execute(
        "INSERT INTO lib2_artists(name, canonical_artist_id) VALUES(?, ?)",
        (ABOVE_AND_BEYOND, lead))
    conn.execute("INSERT INTO lib2_artists(name) VALUES('Simon')")
    conn.execute("INSERT INTO lib2_artists(name) VALUES('Garfunkel')")
    spelled = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Simon and Garfunkel')").lastrowid
    conn.execute(
        "INSERT INTO lib2_artists(name, canonical_artist_id) VALUES(?, ?)",
        ("Simon & Garfunkel", spelled))
    hall = conn.execute("INSERT INTO lib2_artists(name) VALUES('Hall & Oates')").lastrowid
    conn.execute("UPDATE lib2_artists SET canonical_artist_id=? WHERE id=?",
                 (hall, hall))
    conn.execute("INSERT INTO lib2_artists(name) VALUES('Duo One')")
    duo_two = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Duo Two')").lastrowid
    conn.execute(
        "INSERT INTO lib2_artists(name, canonical_artist_id) VALUES(?, ?)",
        ("Duo One & Duo Two", duo_two))
    conn.execute(
        "INSERT INTO lib2_artists(name, canonical_artist_id) VALUES(?, ?)",
        ("Dangling Duo", 999999))
    beyonce = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Beyoncé')").lastrowid
    conn.execute("INSERT INTO lib2_artists(name) VALUES('Jay-Z')")
    conn.execute(
        "INSERT INTO lib2_artists(name, canonical_artist_id) VALUES(?, ?)",
        ("Beyonce & Jay-Z", beyonce))
    conn.commit()
    conn.close()

    class _Db:
        def _get_connection(self):
            return sqlite3.connect(db_path)

    monkeypatch.setattr(music_db, "get_database", lambda: _Db())
    assert library_has_artist(ABOVE_AND_BEYOND) is True
    assert library_has_whole_artist(
        ABOVE_AND_BEYOND, ["Above", "Beyond"]) is False
    assert library_has_whole_artist(
        "Simon & Garfunkel", ["Simon", "Garfunkel"]) is True
    assert library_has_whole_artist("Above", ["Above"]) is True
    assert library_has_whole_artist("Hall & Oates", ["Hall", "Oates"]) is True
    assert library_has_whole_artist(
        "Duo One & Duo Two", ["Duo One", "Duo Two"]) is False
    assert library_has_whole_artist(
        "Dangling Duo", ["Dangling", "Duo"]) is False
    assert library_has_whole_artist(
        "Beyonce & Jay-Z", ["Beyonce", "Jay-Z"]) is False
    assert library_has_whole_artist("Nobody", []) is False


def test_album_artist_tag_keeps_the_whole_name():
    values = {"file_organization.collab_artist_mode": "first",
              "metadata_enhancement.embed_album_art": False}
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    context = {
        "original_search_result": {
            "title": "Blue Sky Action", "artists": [{"name": ABOVE_AND_BEYOND}]},
        "spotify_album": {"id": "x", "name": "We Are All We Need",
                          "artists": [{"name": ABOVE_AND_BEYOND}]},
        "source": "deezer",
    }
    with patch.object(src_module, "get_config_manager", return_value=cfg), \
            patch.object(src_module, "library_has_artist", _lib(GHOST_LIB)), \
            patch.object(src_module, "library_has_whole_artist", _whole_lib(GHOST_LIB)):
        meta = src_module.extract_source_metadata(
            context, {"name": ABOVE_AND_BEYOND},
            {"album_name": "We Are All We Need"})
    assert meta["album_artist"] == ABOVE_AND_BEYOND


def test_web_server_copy_agrees(monkeypatch):
    import web_server

    monkeypatch.setattr(web_server, "config_manager",
                        _Config({"file_organization.collab_artist_mode": "first"}))
    monkeypatch.setattr(web_server, "library_has_artist", _lib(GHOST_LIB))
    monkeypatch.setattr(
        web_server, "library_has_whole_artist", _whole_lib(GHOST_LIB))
    ctx = {"artist": ABOVE_AND_BEYOND, "albumartist": ABOVE_AND_BEYOND,
           "album": "We Are All We Need", "title": "Blue Sky Action",
           "track_number": 3, "disc_number": 1, "year": "2015",
           "_artists_list": [{"name": ABOVE_AND_BEYOND}],
           "_track_artists_list": [{"name": ABOVE_AND_BEYOND}]}
    assert web_server._apply_path_template("$albumartist/$album", ctx) == \
        "Above & Beyond/We Are All We Need"
