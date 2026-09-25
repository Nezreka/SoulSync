"""§49.1 — two catalogue rows for one release, and how they escape the dedup.

`Memory Reboot (Slowed)` (album) and `Memory Reboot` (single) under VØJ share
their Spotify, iTunes and Discogs ids, their SoulSync `soul_id`, and a
byte-identical cached tracklist. They are one release. The dedup never sees
them, because it groups on `(release_title_key, bucket(album_type))` and both
halves of that key differ.

Provider ids are the evidence titles cannot be — but they are not equally
trustworthy, and the production DB says by how much. Albums sharing one id:

    musicbrainz  0 groups      soul_id (Hydrabase)  6 groups
    audiodb      0 groups      spotify             11 groups
    discogs      2 groups      itunes              23 groups / 50 albums
    deezer       4 groups

iTunes is unusable as identity here — it hands `EVA 2` and `EVA 4`,
`NEON BLADE` and `NEON BLADE 2`, `2000` and `2000 - sped up` the same album
id — so it is excluded from the evidence entirely. For the rest: a fold needs
**two** trusted ids to agree and **none** to conflict. One agreement is not
enough (`XSCAPE` and its Track-by-Track Commentary share only Spotify), and a
single conflict is disqualifying (`Thriller` / `Thriller 40` disagree on
MusicBrainz, Deezer and Discogs).
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


def _artist(conn, name="VØJ"):
    return conn.execute(
        "INSERT INTO lib2_artists(name, sort_name) VALUES(?,?)", (name, name)
    ).lastrowid


def _album(conn, artist_id, title, *, album_type="album", soul_id=None,
           spotify_id=None, origin="library", monitored=0, with_track=False,
           external_ids=None):
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, album_type, origin,"
        "                        monitored, external_ids, soul_id, spotify_id)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (artist_id, title, album_type, origin, monitored,
         json.dumps(external_ids or {}), soul_id, spotify_id),
    ).lastrowid
    conn.execute(
        "INSERT INTO lib2_album_artists(album_id, artist_id, role)"
        " VALUES(?,?,'primary')", (album_id, artist_id))
    if with_track:
        conn.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number) VALUES(?,?,1)",
            (album_id, title))
    return album_id


@pytest.fixture
def artist_db(imported_conn, legacy_db):
    return legacy_db, imported_conn


def test_two_agreeing_trusted_ids_fold_across_title_and_bucket(artist_db):
    legacy_db, conn = artist_db
    artist = _artist(conn)
    kept = _album(conn, artist, "Memory Reboot", album_type="single",
                  soul_id="soul_14c324f54f314226", with_track=True,
                  external_ids={"deezer": "393810377"})
    _album(conn, artist, "Memory Reboot (Slowed)", album_type="album",
           soul_id="soul_14c324f54f314226", origin="discography",
           external_ids={"deezer": "393810377"})
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["albums_folded"] == 1
    remaining = [r[0] for r in conn.execute(
        "SELECT id FROM lib2_albums WHERE primary_artist_id=?", (artist,))]
    assert remaining == [kept]


def test_a_shared_spotify_id_alone_never_folds_two_different_releases(artist_db):
    """`Thriller` and `Thriller 40` carry the same Spotify id in production."""
    legacy_db, conn = artist_db
    artist = _artist(conn, "Michael Jackson")
    _album(conn, artist, "Thriller", spotify_id="57TzZhbqvYoUBzJSVKFVlG",
           with_track=True)
    _album(conn, artist, "Thriller 40", spotify_id="57TzZhbqvYoUBzJSVKFVlG",
           origin="discography")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["albums_folded"] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM lib2_albums WHERE primary_artist_id=?", (artist,)
    ).fetchone()[0] == 2


def test_one_agreeing_id_is_not_enough_to_fold(artist_db):
    """`XSCAPE` and its Track-by-Track Commentary share only a Spotify id —
    both are trackless provider stubs, so nothing but this rule stops the
    commentary disc from being swallowed by the album."""
    legacy_db, conn = artist_db
    artist = _artist(conn, "Michael Jackson")
    _album(conn, artist, "XSCAPE", spotify_id="7pomP86PUhoJpY3fsC0WDQ",
           origin="discography")
    _album(conn, artist, "XSCAPE - Track by Track Commentary",
           spotify_id="7pomP86PUhoJpY3fsC0WDQ", origin="discography")
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["albums_folded"] == 0
    assert stats["album_review"] == 1


def test_a_shared_itunes_id_is_no_evidence_at_all(artist_db):
    """iTunes gives `EVA 2` and `EVA 4` the same album id, 23 such groups in
    the production DB. It must not even open a review finding."""
    legacy_db, conn = artist_db
    artist = _artist(conn, "blueberry")
    _album(conn, artist, "EVA 2", album_type="ep", with_track=True,
           external_ids={"itunes": "1783286618"})
    _album(conn, artist, "EVA 4", album_type="single", origin="discography",
           external_ids={"itunes": "1783286618"})
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert (stats["albums_folded"], stats["album_review"]) == (0, 0)


def test_a_conflicting_trusted_id_blocks_the_fold(artist_db):
    """`Thriller` / `Thriller 40` agree on Spotify and disagree on
    MusicBrainz, Deezer and Discogs."""
    legacy_db, conn = artist_db
    artist = _artist(conn, "Michael Jackson")
    _album(conn, artist, "Thriller 40", spotify_id="57TzZhbqvYoUBzJSVKFVlG",
           with_track=True,
           external_ids={"deezer": "375513297", "discogs": "r25205494",
                         "musicbrainz": "5cce1724-3337-4916-bbcf-ca9f4bf3acbc"})
    _album(conn, artist, "Thriller", spotify_id="57TzZhbqvYoUBzJSVKFVlG",
           origin="discography",
           external_ids={"deezer": "96126", "discogs": "r2911293",
                         "musicbrainz": "f32fab67-77dd-3937-addc-9062e28e4c37"})
    conn.commit()

    stats = repair_duplicate_artists(legacy_db)

    assert stats["albums_folded"] == 0
    assert stats["album_review"] == 1
