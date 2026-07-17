"""§62.6 Stufe 4: one-shot repair for already-duplicated artists/albums.

The Sawano DB state (§62.1): artist 31 (name-only, created by an early
wishlist materialize) next to artist 32 (legacy import, full ids), each
holding overlapping album rows. The repair folds no-conflict same-name
artists together, re-homes their albums, then folds/flags same-title album
pairs inside the merged artist.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.dedup_repair import repair_duplicate_artists


def _conn(legacy_db):
    conn = sqlite3.connect(legacy_db.path)
    conn.row_factory = sqlite3.Row
    return conn


def _artist(conn, name, **cols):
    keys = ", ".join(["name", "sort_name", *cols.keys()])
    marks = ", ".join("?" for _ in range(2 + len(cols)))
    cur = conn.execute(
        f"INSERT INTO lib2_artists({keys}) VALUES({marks})",
        (name, name, *cols.values()))
    return cur.lastrowid


def _album(conn, artist_id, title, *, origin="library", monitored=0,
           album_type="album", external_ids="{}", with_track=False, **cols):
    keys = ", ".join(["primary_artist_id", "title", "album_type", "origin",
                      "monitored", "external_ids", *cols.keys()])
    marks = ", ".join("?" for _ in range(6 + len(cols)))
    cur = conn.execute(
        f"INSERT INTO lib2_albums({keys}) VALUES({marks})",
        (artist_id, title, album_type, origin, monitored, external_ids,
         *cols.values()))
    album_id = cur.lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
        "VALUES(?,?, 'primary')", (album_id, artist_id))
    if with_track:
        conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number) "
            "VALUES(?, 'Cut', 1)", (album_id,))
    return album_id


@pytest.fixture
def repair_db(imported_conn, legacy_db):
    """imported_conn gives a full lib2 schema; hand back both handles."""
    return legacy_db, imported_conn


def test_no_conflict_same_name_artists_are_merged(repair_db):
    legacy_db, conn = repair_db
    bare = _artist(conn, "Hiroyuki Sawano")
    rich = _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y",
                   legacy_artist_id=730596583,
                   external_ids=json.dumps({"deezer": "1315147"}))
    kept_album = _album(conn, bare, "Sengoku BASARA 2 Ongaku Emaki",
                        origin="discography")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 1
    rows = conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Hiroyuki Sawano'").fetchall()
    assert [r["id"] for r in rows] == [rich]
    # The bare row's album moved over to the survivor.
    moved = conn.execute(
        "SELECT primary_artist_id FROM lib2_albums WHERE id=?",
        (kept_album,)).fetchone()
    assert moved["primary_artist_id"] == rich
    link = conn.execute(
        "SELECT artist_id FROM lib2_album_artists WHERE album_id=?",
        (kept_album,)).fetchall()
    assert [r["artist_id"] for r in link] == [rich]


def test_merge_prefers_the_legacy_linked_richer_row(repair_db):
    legacy_db, conn = repair_db
    rich = _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y",
                   legacy_artist_id=730596583)
    bare = _artist(conn, "hiroyuki  sawano")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    survivor = conn.execute(
        "SELECT id, spotify_id FROM lib2_artists WHERE lower(name) LIKE 'hiroyuki%'"
    ).fetchall()
    assert len(survivor) == 1
    assert survivor[0]["id"] == rich


def test_conflicting_ids_become_alias_link_not_merge(repair_db):
    legacy_db, conn = repair_db
    first = _artist(conn, "Hiroyuki Sawano", spotify_id="sp-one")
    second = _artist(conn, "Hiroyuki Sawano", spotify_id="sp-two")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 0
    assert stats["alias_linked"] == 1
    rows = {r["id"]: r["canonical_artist_id"] for r in conn.execute(
        "SELECT id, canonical_artist_id FROM lib2_artists "
        "WHERE name='Hiroyuki Sawano'")}
    assert len(rows) == 2
    assert rows[second] == first
    assert rows[first] is None


def test_post_merge_same_title_pristine_album_is_folded(repair_db):
    legacy_db, conn = repair_db
    bare = _artist(conn, "Hiroyuki Sawano")
    rich = _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y")
    # artist 31 flavor: pristine discography row.
    dup = _album(conn, bare, "Sengoku BASARA Digital Original Director's Special Edition",
                 origin="discography", album_type="ep")
    # artist 32 flavor: same release, also provider-only.
    keeper = _album(conn, rich, "Sengoku BASARA Digital Original Director's Special Edition",
                    origin="discography", album_type="ep",
                    external_ids=json.dumps({"deezer": "398610457"}))
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["artists_merged"] == 1
    assert stats["albums_folded"] == 1
    titles = conn.execute(
        "SELECT id FROM lib2_albums WHERE title LIKE 'Sengoku BASARA Digital%'"
    ).fetchall()
    assert len(titles) == 1


def test_post_merge_two_track_bearing_albums_become_review_finding(repair_db):
    legacy_db, conn = repair_db
    bare = _artist(conn, "Hiroyuki Sawano")
    rich = _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y")
    a1 = _album(conn, bare, "TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
                with_track=True)
    a2 = _album(conn, rich, "TVアニメ「進撃の巨人」Season 2 オリジナルサウンドトラック",
                with_track=True)
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["albums_folded"] == 0
    assert stats["album_review"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM lib2_albums WHERE id IN (?,?)", (a1, a2)
    ).fetchone()["c"] == 2
    finding = conn.execute(
        "SELECT reason FROM lib2_release_group_review").fetchone()
    assert finding["reason"] == "duplicate_title_unmerged"


def test_repair_is_idempotent(repair_db):
    legacy_db, conn = repair_db
    _artist(conn, "Hiroyuki Sawano")
    _artist(conn, "Hiroyuki Sawano", spotify_id="0Riv2KnFcLZA3JSVryRg4y")
    conn.commit()

    repair_duplicate_artists(legacy_db)
    stats2 = repair_duplicate_artists(legacy_db)

    assert stats2["artists_merged"] == 0
    assert stats2["alias_linked"] == 0


# ---------------------------------------------------------------------------
# §62.6 Stufe 5: heal foreign-shaped ids already sitting in spotify_id columns
# ---------------------------------------------------------------------------

def test_sanitize_moves_itunes_id_out_of_spotify_column(repair_db):
    """The 1229 shape: spotify_id + external_ids.spotify hold the iTunes id."""
    legacy_db, conn = repair_db
    artist = _artist(conn, "Hiroyuki Sawano")
    album = _album(conn, artist, "TVアニメ「進撃の巨人」Season 2 OST",
                   spotify_id="1239706770",
                   external_ids=json.dumps({
                       "deezer": "196470602", "itunes": "1239706770",
                       "spotify": "1239706770"}))
    conn.commit()

    repair_duplicate_artists(legacy_db)

    row = conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_albums WHERE id=?",
        (album,)).fetchone()
    assert row["spotify_id"] is None
    ids = json.loads(row["external_ids"])
    assert "spotify" not in ids
    assert ids["itunes"] == "1239706770"
    assert ids["deezer"] == "196470602"


def test_sanitize_resolves_namespace_via_legacy_table(repair_db):
    """The 1169 shape: a bare Deezer id as spotify_id, nothing else — the
    legacy albums table knows which column the value really lives in."""
    legacy_db, conn = repair_db
    conn.execute("ALTER TABLE albums ADD COLUMN deezer_id TEXT")
    conn.execute("UPDATE albums SET deezer_id='42388621' WHERE id=10")
    artist = _artist(conn, "Hiroyuki Sawano")
    album = _album(conn, artist, 'TV Anime "Attack on Titan" OST',
                   spotify_id="42388621")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    row = conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_albums WHERE id=?",
        (album,)).fetchone()
    assert row["spotify_id"] is None
    assert json.loads(row["external_ids"])["deezer"] == "42388621"


def test_sanitize_uuid_shaped_spotify_id_is_musicbrainz(repair_db):
    legacy_db, conn = repair_db
    artist = _artist(conn, "Sawano",
                     spotify_id="60d2ea34-1912-425f-bf9c-fc544e4448cd")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    row = conn.execute(
        "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_artists "
        "WHERE id=?", (artist,)).fetchone()
    assert row["spotify_id"] is None
    assert row["musicbrainz_id"] == "60d2ea34-1912-425f-bf9c-fc544e4448cd"


def test_sanitize_parks_unresolvable_numeric_id(repair_db):
    legacy_db, conn = repair_db
    artist = _artist(conn, "Sawano")
    album = _album(conn, artist, "Mystery Album", spotify_id="777000111")
    conn.commit()

    repair_duplicate_artists(legacy_db)

    row = conn.execute(
        "SELECT spotify_id, external_ids FROM lib2_albums WHERE id=?",
        (album,)).fetchone()
    assert row["spotify_id"] is None
    # Value survives for value-based matching, but under no provider's name.
    assert json.loads(row["external_ids"])["legacy_unknown"] == "777000111"
