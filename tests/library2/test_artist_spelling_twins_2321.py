"""#2321: one artist listed two or three times, once per way its name was typed.

the rows are shaped like the live ones: 98º (ordinal sign) imported from the
media server with a musicbrainz id and aliases, 98° (degree sign) and
98 Degrees from catalogue pages with only their own deezer/tidal/itunes ids;
alt-J from the server, alt‐J (unicode hyphen) from a second deezer page.
"""

from __future__ import annotations

import json

import pytest

from core.library2.dedup_repair import repair_duplicate_artists
from core.library2.importer import _ArtistResolver, normalize_name
from core.library2.schema import _backfill_artist_name_keys
from tests.lib2_seed import row_conn

MBID_98 = "177fde26-5a95-46d9-922f-0933940d97e5"
MBID_ALTJ = "fc7bbf00-fbaa-4736-986b-b3ac0266ca9b"


def _artist(conn, name, **cols):
    keys = ", ".join(["name", "sort_name", "name_key", *cols.keys()])
    marks = ", ".join("?" for _ in range(3 + len(cols)))
    return conn.execute(f"INSERT INTO lib2_artists({keys}) VALUES({marks})",
                        (name, name, normalize_name(name), *cols.values())).lastrowid


def _album(conn, artist_id, title):
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, origin) "
        "VALUES(?, ?, 'album', 'library')", (artist_id, title)).lastrowid
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) "
                 "VALUES(?, ?, 'primary')", (album_id, artist_id))
    return album_id


@pytest.fixture
def db(imported_conn, legacy_db):
    return legacy_db, imported_conn


def _row(conn, artist_id):
    return conn.execute("SELECT * FROM lib2_artists WHERE id=?", (artist_id,)).fetchone()


def test_typed_spellings_share_one_key():
    assert normalize_name("98º") == normalize_name("98°") == normalize_name("98˚")
    assert normalize_name("alt‐J") == normalize_name("alt-J") == "alt-j"
    assert normalize_name("Guns N’ Roses") == normalize_name("Guns N' Roses")
    # a real character still counts
    assert normalize_name("98o") != normalize_name("98°")
    assert normalize_name("Attack Attack!") != normalize_name("Attack! Attack!")


def test_98_degrees_three_rows_become_one_artist(db):
    legacy_db, conn = db
    real = _artist(conn, "98º", musicbrainz_id=MBID_98, spotify_id="6V03b3Y36lolYP2orXn8mV",
                   legacy_artist_id=563565,
                   aliases=json.dumps(["98°", "98 Degrees", "98º"]),
                   external_ids=json.dumps({"deezer": "110744", "itunes": "257902",
                                            "musicbrainz": MBID_98, "tidal": "17485"}))
    degree = _artist(conn, "98°", external_ids=json.dumps({"deezer": "1571829", "tidal": "5921794"}))
    spelled = _artist(conn, "98 Degrees", external_ids=json.dumps({"itunes": "1741209225"}))
    _album(conn, real, "98 Degrees and Rising")
    single = _album(conn, spelled, "Christmas Time")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    assert _row(conn, real)["canonical_artist_id"] is None
    assert _row(conn, degree)["canonical_artist_id"] == real
    assert _row(conn, spelled)["canonical_artist_id"] == real
    # a link, not a fold: the single is still where it was, under the one artist
    from core.library2.artist_aliases import artist_album_scope_ids
    assert single in artist_album_scope_ids(conn, real)


def test_alt_j_unicode_hyphen_twin_folds_in(db):
    legacy_db, conn = db
    real = _artist(conn, "alt-J", musicbrainz_id=MBID_ALTJ, spotify_id="3XHO7cRUPCLOr6jwp8vsx5",
                   legacy_artist_id=419213,
                   external_ids=json.dumps({"deezer": "1606315", "musicbrainz": MBID_ALTJ}))
    twin = _artist(conn, "alt‐J")   # no ids of its own: the same artist
    album = _album(conn, twin, "Hard Drive Gold")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 1
    assert _row(conn, twin) is None
    moved = conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album,)).fetchone()
    assert moved["primary_artist_id"] == real


def test_alt_j_second_deezer_page_is_linked_not_lost(db):
    legacy_db, conn = db
    real = _artist(conn, "alt-J", musicbrainz_id=MBID_ALTJ,
                   external_ids=json.dumps({"deezer": "1606315", "musicbrainz": MBID_ALTJ}))
    twin = _artist(conn, "alt‐J", external_ids=json.dumps({"deezer": "6247302"}))
    conn.commit()

    repair_duplicate_artists(legacy_db)

    assert _row(conn, twin)["canonical_artist_id"] == real


def test_two_real_artists_sharing_a_name_stay_apart(db):
    legacy_db, conn = db
    us = _artist(conn, "Nirvana", musicbrainz_id="5b11f4ce-a62d-471e-81fc-a69a8278c7da",
                 aliases=json.dumps(["Nirvana US"]))
    uk = _artist(conn, "Nirvana", musicbrainz_id="9282c8b4-ca0b-4c6b-b7e3-4f7762dfc4d6",
                 aliases=json.dumps(["Nirvana UK"]))
    # an alias one identified artist claims is not stolen from another identified one
    other = _artist(conn, "Nirvana UK", musicbrainz_id="11111111-2222-3333-4444-555555555555")
    _album(conn, us, "Nevermind")
    _album(conn, uk, "The Story of Simon Simopath")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 0
    for artist_id in (us, uk, other):
        row = _row(conn, artist_id)
        assert row is not None and row["canonical_artist_id"] is None


def test_kept_apart_alias_name_is_left_alone(db):
    legacy_db, conn = db
    real = _artist(conn, "98º", musicbrainz_id=MBID_98, aliases=json.dumps(["98 Degrees"]))
    spelled = _artist(conn, "98 Degrees")
    from core.library2.artist_merges import ensure_tables, keep_apart
    ensure_tables(conn)
    keep_apart(conn, real, spelled, "two acts")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    assert _row(conn, spelled)["canonical_artist_id"] is None


def test_resolver_finds_the_artist_under_another_hyphen(db):
    _legacy_db, conn = db
    real = _artist(conn, "alt-J", musicbrainz_id=MBID_ALTJ)
    conn.commit()
    resolver = _ArtistResolver(conn.cursor(), default_profile_id=None)
    resolver.seed_existing()
    assert resolver.get_or_create_by_name("alt‐J") == real
    assert resolver.get_or_create_by_name("98º") != resolver.get_or_create_by_name("alt-J")
    assert resolver.get_or_create_by_name("98°") == resolver.get_or_create_by_name("98º")


def test_stored_keys_made_before_the_fold_are_rekeyed_once(db):
    _legacy_db, conn = db
    twin = conn.execute("INSERT INTO lib2_artists(name, sort_name, name_key) VALUES(?,?,?)",
                        ("alt‐J", "alt‐J", "alt‐j")).lastrowid
    conn.execute("DROP TABLE IF EXISTS _lib2_name_key_fold")
    assert _backfill_artist_name_keys(conn.cursor()) == 1
    assert _row(conn, twin)["name_key"] == "alt-j"
    assert _backfill_artist_name_keys(conn.cursor()) == 0
