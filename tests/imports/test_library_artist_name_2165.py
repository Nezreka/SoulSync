"""#2165 (nstrelow): a download is filed and tagged under the library's name.

his library files Machine Gun Kelly as "mgk", musicbrainz says mgk, and
deezer, the download source, still says "Machine Gun Kelly". the library
already knows it's one artist: the importer attaches the file to the mgk row
by deezer id. only the folder and the tags used deezer's name, so every new
release landed in a second "Machine Gun Kelly/" folder.
"""

from __future__ import annotations

import json

import pytest

import core.imports.library_names as names_mod
import core.imports.paths as import_paths
from core.imports.library_names import library_artist_name, swap_credit
from core.imports.paths import build_final_path_for_track
from database.music_database import MusicDatabase

MGK_DZ = "1135797"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    conn = db._get_connection()
    rows = [
        # id, name, mbid, external ids
        (1, "mgk", "mbid-mgk", {"deezer": MGK_DZ, "spotify": "sp-mgk"}),
        (2, "Camila Cabello", None, {"deezer": "dz-camila"}),
        (3, "Nirvana", "mbid-us", {"deezer": "dz-us"}),
        (4, "Nirvana", "mbid-uk", {"deezer": "dz-uk"}),
    ]
    for artist_id, name, mbid, ids in rows:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, name_key, musicbrainz_id, spotify_id, external_ids) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (artist_id, name, name.casefold(), mbid, ids.get("spotify"), json.dumps(ids)))
    conn.commit()
    conn.close()
    monkeypatch.setattr(names_mod, "_get_db", lambda: db)
    names_mod.clear_cache()
    yield db
    names_mod.clear_cache()


def _pin(db, artist_id, name):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO lib2_metadata_overrides (entity_type, entity_id, field_name, value_json) "
        "VALUES ('artist', ?, 'name', ?)", (artist_id, json.dumps(name)))
    conn.commit()
    conn.close()
    names_mod.clear_cache()


class _Config:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


@pytest.fixture()
def template(monkeypatch, tmp_path):
    def use(album_path="$albumartist/$albumartist - $album/$track - $title", **extra):
        config = _Config({
            "soulseek.transfer_path": str(tmp_path / "T"),
            "file_organization.enabled": True,
            "file_organization.templates": {
                "album_path": album_path,
                "single_path": extra.get("single_path", "$artist/$artist - $title/$title"),
            },
            "file_organization.collab_artist_mode": extra.get("collab", "first"),
            "file_organization.auto_disambiguation": False,
        })
        monkeypatch.setattr(import_paths, "_get_config_manager", lambda: config)
        monkeypatch.setattr(import_paths, "_get_album_tracks_for_source", lambda *a: None)
        return tmp_path / "T"
    return use


def _context(artist, artist_id, album, *, source="deezer", track_artists=None, album_artists=None):
    track_artists = track_artists or [{"name": artist, "id": artist_id}]
    return {
        "artist": {"name": artist, "id": artist_id},
        "album": {"name": album, "id": "alb", "release_date": "2024-08-09", "total_tracks": 12,
                  "total_discs": 1, "total_discs_declared": True, "album_type": "album",
                  "artists": album_artists or [{"name": artist, "id": artist_id}]},
        "track_info": {"name": "Song", "id": "t", "track_number": 1, "disc_number": 1,
                       "artists": track_artists},
        "original_search_result": {"title": "Song", "clean_title": "Song", "clean_album": album,
                                   "clean_artist": artist, "artists": track_artists},
        "source": source, "is_album_download": True,
    }


def _destination(context, album="Lost Americana"):
    path, _ = build_final_path_for_track(
        context, context["artist"],
        {"is_album": True, "album_name": album, "track_number": 1, "disc_number": 1},
        ".flac", create_dirs=False)
    return path


# ── the bug ──────────────────────────────────────────────────────────────────

def test_a_deezer_download_lands_in_the_library_folder(db, template):
    root = template()
    path = _destination(_context("Machine Gun Kelly", MGK_DZ, "Lost Americana"))
    assert path == str(root / "mgk" / "mgk - Lost Americana" / "01 - Song.flac")


def test_a_single_lands_in_the_library_folder(db, template):
    root = template(single_path="$artist/$artist - $title/$title")
    ctx = _context("Machine Gun Kelly", MGK_DZ, "Song")
    path, _ = build_final_path_for_track(
        ctx, ctx["artist"], {"is_album": False, "album_name": "Song"}, ".flac",
        create_dirs=False)
    assert path == str(root / "mgk" / "mgk - Song" / "Song.flac")


def test_every_artist_token_follows(db, template):
    root = template("$albumartistletter/$albumartist/$artist - $album/$track - $title")
    path = _destination(_context("Machine Gun Kelly", MGK_DZ, "Lost Americana"))
    assert path == str(root / "M" / "mgk" / "mgk - Lost Americana" / "01 - Song.flac")


def test_a_spotify_id_or_mbid_is_evidence_too(db, template):
    root = template()
    sp = _context("Machine Gun Kelly", "sp-mgk", "Hotel Diablo", source="spotify")
    assert _destination(sp, "Hotel Diablo").startswith(str(root / "mgk" / "mgk - Hotel Diablo"))
    mb = _context("Machine Gun Kelly", "", "Hotel Diablo", source="musicbrainz")
    mb["artist"]["musicbrainz_id"] = "mbid-mgk"
    assert _destination(mb, "Hotel Diablo").startswith(str(root / "mgk" / "mgk - Hotel Diablo"))


def test_a_name_alone_is_never_evidence(db, template):
    root = template()
    # no id anywhere: deezer's name stays, nothing guessed from "sounds alike"
    ctx = _context("Machine Gun Kelly", "", "Lost Americana")
    assert _destination(ctx).startswith(str(root / "Machine Gun Kelly"))
    # an id the library doesn't hold is not evidence either
    other = _context("Machine Gun Kelly", "999", "Lost Americana")
    assert _destination(other).startswith(str(root / "Machine Gun Kelly"))


def test_an_unrelated_artist_keeps_its_name(db, template):
    root = template()
    assert _destination(_context("Camila Cabello", "dz-camila", "Familia"), "Familia") == \
        str(root / "Camila Cabello" / "Camila Cabello - Familia" / "01 - Song.flac")


def test_only_the_matching_credit_is_swapped_in_a_collab_folder(db, template):
    root = template(collab="all")
    ctx = _context(
        "Machine Gun Kelly", MGK_DZ, "Collab",
        track_artists=[{"name": "Machine Gun Kelly", "id": MGK_DZ},
                       {"name": "Camila Cabello", "id": "dz-camila"}],
        album_artists=[{"name": "Machine Gun Kelly & Camila Cabello", "id": ""}])
    ctx["artist"] = {"name": "Machine Gun Kelly & Camila Cabello", "id": ""}
    assert _destination(ctx, "Collab").startswith(
        str(root / "mgk & Camila Cabello" / "mgk & Camila Cabello - Collab"))


def test_first_listed_collab_folder_uses_the_library_name(db, template):
    root = template(collab="first")
    ctx = _context(
        "Machine Gun Kelly", MGK_DZ, "Collab",
        track_artists=[{"name": "Machine Gun Kelly", "id": MGK_DZ},
                       {"name": "Camila Cabello", "id": "dz-camila"}],
        album_artists=[{"name": "Machine Gun Kelly", "id": MGK_DZ},
                       {"name": "Camila Cabello", "id": "dz-camila"}])
    assert _destination(ctx, "Collab").startswith(str(root / "mgk" / "mgk - Collab"))


def test_a_case_only_difference_is_left_to_the_folder_casing_rule(db):
    # "MGK" vs "mgk" is #1091's job (it reuses the folder on disk); swapping
    # casing here would retag every file whose source spells it differently
    assert library_artist_name("MGK", {"deezer": MGK_DZ}) is None


def test_two_same_named_artists_never_merge(db, template):
    root = template()
    # each nirvana keeps its own row's name: the rule never crosses ids
    assert _destination(_context("Nirvana", "dz-uk", "Simopath"), "Simopath") \
        .startswith(str(root / "Nirvana" / "Nirvana - Simopath"))
    assert library_artist_name("Nirvana", {"deezer": "dz-us"}) is None


def test_an_artist_split_in_two_keeps_both_names_until_one_is_pinned(db):
    # an older download made a second row with deezer's name and id: the
    # source's name already is a library name for that id, so nothing flips
    # between the two rows. a pin says which one wins
    conn = db._get_connection()
    conn.execute("INSERT INTO lib2_artists (id, name, name_key, external_ids) VALUES "
                 "(5, 'Machine Gun Kelly', 'machine gun kelly', ?)",
                 (json.dumps({"deezer": MGK_DZ}),))
    conn.commit()
    conn.close()
    names_mod.clear_cache()
    assert library_artist_name("Machine Gun Kelly", {"deezer": MGK_DZ}) is None
    assert library_artist_name("mgk", {"deezer": MGK_DZ}) is None
    _pin(db, 5, "mgk")
    assert library_artist_name("Machine Gun Kelly", {"deezer": MGK_DZ}) == "mgk"


def test_a_name_owned_by_a_different_library_artist_is_not_taken(db):
    # a context that pairs camila's name with mgk's id is someone else's
    # credit: camila is in the library with her own deezer id
    assert library_artist_name("Camila Cabello", {"deezer": MGK_DZ}) is None


# ── the pin ──────────────────────────────────────────────────────────────────

def test_a_pin_wins_over_the_library_name(db, template):
    root = template()
    _pin(db, 1, "MGK (rapper)")
    assert _destination(_context("Machine Gun Kelly", MGK_DZ, "Lost Americana")).startswith(
        str(root / "MGK (rapper)" / "MGK (rapper) - Lost Americana"))


def test_a_pin_on_a_row_found_by_name_applies_without_ids(db, template):
    # someone who just prefers a spelling: the importer finds the artist by
    # name, so the pinned name is the one it is filed under
    root = template()
    _pin(db, 2, "Camila")
    assert _destination(_context("Camila Cabello", "", "Familia"), "Familia").startswith(
        str(root / "Camila" / "Camila - Familia"))


def test_a_pin_is_not_applied_through_a_conflicting_id(db):
    _pin(db, 3, "Nirvana (US)")
    # the uk band by name, but its deezer id is the uk row's: not the pin
    assert library_artist_name("Nirvana", {"deezer": "dz-uk"}) is None
    assert library_artist_name("Nirvana", {"deezer": "dz-us"}) == "Nirvana (US)"
    # by name alone two nirvanas can't be told apart: never a guess
    assert library_artist_name("Nirvana", {}) is None


# ── credits ──────────────────────────────────────────────────────────────────

def test_swap_credit_only_touches_whole_credits():
    names = {"machine gun kelly": "mgk"}
    assert swap_credit("Machine Gun Kelly", names) == "mgk"
    assert swap_credit("Machine Gun Kelly feat. Camila Cabello", names) == "mgk feat. Camila Cabello"
    assert swap_credit("Camila Cabello, Machine Gun Kelly", names) == "Camila Cabello, mgk"
    assert swap_credit("Camila Cabello; Machine Gun Kelly", names) == "Camila Cabello; mgk"
    # a name inside another name is not that artist
    assert swap_credit("Machine Gun Kelly Tribute Band", names) == "Machine Gun Kelly Tribute Band"
    assert swap_credit("", names) == ""
    assert swap_credit("Machine Gun Kelly", {}) == "Machine Gun Kelly"


# ── tags ─────────────────────────────────────────────────────────────────────

def _tags(context, **settings):
    from unittest.mock import MagicMock, patch

    import core.metadata as metadata_pkg
    from core.metadata import source as src_module
    values = {"file_organization.collab_artist_mode": "first",
              "metadata_enhancement.embed_album_art": False, **settings}
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: values.get(key, default)
    with patch.object(src_module, "get_config_manager", return_value=cfg), \
            patch.object(src_module, "library_has_artist", lambda n: False), \
            patch.object(src_module, "library_has_identified_artist", lambda n: False), \
            patch.object(metadata_pkg, "get_deezer_client", lambda: None):
        return src_module.extract_source_metadata(
            context, context["artist"], {"is_album": True, "album_name": "Lost Americana",
                                         "track_number": 1, "disc_number": 1})


def test_the_tags_carry_the_library_name(db):
    meta = _tags(_context("Machine Gun Kelly", MGK_DZ, "Lost Americana"))
    assert meta["artist"] == "mgk"
    assert meta["album_artist"] == "mgk"


def test_a_featured_credit_keeps_its_own_name_in_the_tags(db):
    ctx = _context(
        "Machine Gun Kelly", MGK_DZ, "Lost Americana",
        track_artists=[{"name": "Machine Gun Kelly", "id": MGK_DZ},
                       {"name": "Camila Cabello", "id": "dz-camila"}])
    meta = _tags(ctx, **{"metadata_enhancement.tags.artist_separator": "; "})
    assert meta["artist"] == "mgk; Camila Cabello"
    assert meta["_artists_list"] == ["mgk", "Camila Cabello"]
    assert meta["album_artist"] == "mgk"
    # feat in title: the feature is named, the main artist is the library's
    feat = _tags(ctx, **{"metadata_enhancement.tags.feat_in_title": True})
    assert feat["artist"] == "mgk"
    assert feat["title"] == "Song (feat. Camila Cabello)"
    # and the other way round: a feature on someone else's track
    guest = _context(
        "Camila Cabello", "dz-camila", "Familia",
        track_artists=[{"name": "Camila Cabello", "id": "dz-camila"},
                       {"name": "Machine Gun Kelly", "id": MGK_DZ}])
    meta = _tags(guest, **{"metadata_enhancement.tags.feat_in_title": True})
    assert (meta["artist"], meta["album_artist"]) == ("Camila Cabello", "Camila Cabello")
    assert meta["title"] == "Song (feat. mgk)"


def test_a_library_reorganize_keeps_the_library_artist_it_belongs_to(db, template):
    # a reorganize names files by the library artist they're catalogued
    # under, never re-resolved through ids
    from core.imports.library_names import library_names
    ctx = _context("Machine Gun Kelly", MGK_DZ, "Lost Americana")
    ctx["_library_reorganize"] = True
    assert library_names(ctx, ctx["artist"]) == {}
