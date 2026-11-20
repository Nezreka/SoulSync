"""#2222: a wishlist download asked for as "Δέσποινα Βανδή" (itunes) was
tagged artist/album artist "Despina Vandi" and filed under a greek folder,
next to the library's "Despina Vandi/" folder, so plex showed the album twice.

two causes:
  * the ALBUMARTIST tag takes the track's own album artist over a
    disagreeing explicit artist context (#1316); the folder kept the explicit
    context. both now take the same answer.
  * nothing knew the greek name was the library's "Despina Vandi": the
    #2165 library naming only matched ids and pinned names. it now also
    finds the library artist under another script or musicbrainz alias.
"""

from __future__ import annotations

import json

import pytest

import core.imports.library_names as names_mod
from core.imports.library_names import library_artist_name, swap_credit
from core.imports.paths import build_final_path_for_track
from database.music_database import MusicDatabase
from tests.imports.test_library_artist_name_2165 import _context, template  # noqa: F401

GREEK = "Δέσποινα Βανδή"
ALBUM = "Gia - Collector's Edition"


def _artists(db, rows):
    conn = db._get_connection()
    for artist_id, name, ids, aliases in rows:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, name_key, external_ids, aliases) VALUES (?, ?, ?, ?, ?)",
            (artist_id, name, name.casefold(), json.dumps(ids), json.dumps(aliases, ensure_ascii=False)))
    conn.commit()
    conn.close()
    names_mod.clear_cache()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    monkeypatch.setattr(names_mod, "_get_db", lambda: db)
    names_mod.clear_cache()
    yield db
    names_mod.clear_cache()


def _destination(context, album=ALBUM):
    path, _ = build_final_path_for_track(
        context, context["artist"],
        {"is_album": True, "album_name": album, "track_number": 14, "disc_number": 1},
        ".mp3", create_dirs=False)
    return path


# ── the library knows the artist under its latin name ───────────────────────

def test_a_greek_itunes_download_joins_the_latin_library_artist(db, template):
    _artists(db, [(1, "Despina Vandi", {"deezer": "dz-vandi"}, [])])
    root = template()
    ctx = _context(GREEK, "itunes-vandi", ALBUM, source="itunes")
    assert _destination(ctx) == str(
        root / "Despina Vandi" / "Despina Vandi - Gia - Collector's Edition" / "14 - Song.mp3")


def test_the_tags_take_the_same_name(db):
    _artists(db, [(1, "Despina Vandi", {}, [])])
    names = names_mod.library_names(_context(GREEK, "itunes-vandi", ALBUM, source="itunes"))
    assert swap_credit(GREEK, names) == "Despina Vandi"


def test_a_musicbrainz_alias_names_the_library_artist(db):
    # a spelling romanization can't reach: the library row's stored
    # musicbrainz names list the source's
    _artists(db, [(1, "Despina Vandi", {}, ["Δέσποινα Βανδή-Παπαδοπούλου", "Vandi, Despina"])])
    assert library_artist_name("Δέσποινα Βανδή-Παπαδοπούλου", {"itunes": "1"}) == "Despina Vandi"


def test_sakis_rouvas(db):
    _artists(db, [(1, "Sakis Rouvas", {}, [])])
    assert library_artist_name("Σάκης Ρουβάς", {"itunes": "2"}) == "Sakis Rouvas"


# ── what it must not do ──────────────────────────────────────────────────────

def test_no_library_artist_keeps_the_source_name(db, template):
    root = template()
    ctx = _context(GREEK, "itunes-vandi", ALBUM, source="itunes")
    assert _destination(ctx).startswith(str(root / GREEK))


def test_two_library_artists_answering_is_not_guessed_between(db):
    _artists(db, [(1, "Despina Vandi", {}, []), (2, "Despoina Vandi", {}, [])])
    assert library_artist_name(GREEK, {"itunes": "9"}) is None


def test_a_library_artist_holding_another_itunes_id_is_left_alone(db):
    _artists(db, [(1, "Despina Vandi", {"itunes": "1111"}, [])])
    assert library_artist_name(GREEK, {"itunes": "2222"}) is None


def test_the_greek_artist_already_in_the_library_keeps_its_own_folder(db):
    # one library artist goes by the greek name already: that's the artist
    _artists(db, [(1, "Despina Vandi", {}, []), (2, GREEK, {}, [])])
    assert library_artist_name(GREEK, {"itunes": "9"}) is None


# ── the folder agrees with the album artist tag ──────────────────────────────

def test_the_reporters_download_folder_follows_the_album_artist_tag(db, template):
    """no library artist at all. the wishlist row asked for the greek name
    (explicit artist context), the matched album is credited "Despina Vandi".
    the tag took the album's credit (#1316), so the folder must too."""
    root = template()
    ctx = _context("Despina Vandi", "dz-vandi", ALBUM, source="deezer")
    ctx["track_info"]["_explicit_artist_context"] = {"name": GREEK}
    assert _destination(ctx) == str(
        root / "Despina Vandi" / "Despina Vandi - Gia - Collector's Edition" / "14 - Song.mp3")


def test_album_artist_override_is_the_tag_rule():
    from core.metadata.source import album_artist_override, evaluate_batch_album_distrust
    album = {"name": ALBUM, "id": "alb", "artists": [{"name": "Despina Vandi", "id": "dz"}]}
    track = {"artists": [{"name": "Despina Vandi"}], "album": album}
    verdict = evaluate_batch_album_distrust({"artists": [{"name": "Despina Vandi"}]}, track,
                                            album, {"name": GREEK})
    assert not verdict["distrusted"]
    assert album_artist_override(verdict)[0] == "Despina Vandi"
    # the same artist named twice is no override
    same = evaluate_batch_album_distrust({}, track, album, {"name": "despina vandi"})
    assert album_artist_override(same) is None


def test_an_agreeing_explicit_context_still_names_the_folder(db, template):
    root = template()
    ctx = _context("Despina Vandi", "dz-vandi", ALBUM, source="deezer")
    ctx["track_info"]["_explicit_artist_context"] = {"name": "Despina Vandi"}
    assert _destination(ctx).startswith(str(root / "Despina Vandi" / "Despina Vandi - "))
