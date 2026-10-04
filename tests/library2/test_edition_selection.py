"""One release per album for every consumer (feature-parity audit A01).

The match chip read the album row, completeness and track-number repair read
the default edition, and the backfill built that edition from the album's ids
while ignoring the canonical pin. A manual correction from DELUXE to REGULAR
therefore showed REGULAR and kept fetching the DELUXE tracklist.
"""

from __future__ import annotations

import json

import pytest

from core.library2.completeness import _album_tracklist_context
from core.library2.editions import backfill_editions, pin_album_release
from core.library2.match_status import set_library_v2_match
from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import row_conn


@pytest.fixture
def conn(tmp_path):
    c = row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(c)
    c.execute("INSERT INTO lib2_artists(id, name) VALUES (1, 'Artist')")
    yield c
    c.close()


def _album(conn, **cols):
    cols = {"primary_artist_id": 1, "title": "Record", "spotify_id": "DELUXE",
            "track_count": 20, **cols}
    keys = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    return conn.execute(f"INSERT INTO lib2_albums({keys}) VALUES({marks})",
                        tuple(cols.values())).lastrowid


def _edition(conn, album_id):
    return conn.execute(
        "SELECT spotify_id, track_count, external_ids FROM lib2_release_editions "
        "WHERE release_group_id=? AND is_default=1", (album_id,)).fetchone()


def _tracklist_spotify(conn, album_id):
    _row, reference, _ids = _album_tracklist_context(conn, album_id)
    return reference["spotify_id"]


def test_the_backfilled_edition_is_the_pinned_release(conn):
    album_id = _album(conn, canonical_source="spotify", canonical_album_id="REGULAR",
                      canonical_locked=1)
    backfill_editions(conn.cursor())

    edition = _edition(conn, album_id)
    assert edition["spotify_id"] == "REGULAR"
    # 20 is the DELUXE's count; the pinned release's is not known yet
    assert edition["track_count"] is None
    assert _tracklist_spotify(conn, album_id) == "REGULAR"


def test_a_manual_match_moves_the_tracklist_with_the_chip(conn):
    album_id = _album(conn)
    backfill_editions(conn.cursor())
    assert _tracklist_spotify(conn, album_id) == "DELUXE"

    set_library_v2_match(conn, "album", album_id, "spotify", "REGULAR", steal=True)

    assert _edition(conn, album_id)["spotify_id"] == "REGULAR"
    assert _tracklist_spotify(conn, album_id) == "REGULAR"


def test_an_added_provider_id_keeps_the_release_facts(conn):
    album_id = _album(conn)
    backfill_editions(conn.cursor())

    set_library_v2_match(conn, "album", album_id, "deezer", "dz-1")

    edition = _edition(conn, album_id)
    assert edition["spotify_id"] == "DELUXE" and edition["track_count"] == 20
    assert json.loads(edition["external_ids"]) == {"deezer": "dz-1"}


def test_a_pin_written_elsewhere_is_reconciled_on_the_next_backfill(conn):
    album_id = _album(conn)
    backfill_editions(conn.cursor())
    conn.execute("UPDATE lib2_albums SET canonical_source='spotify', "
                 "canonical_album_id='REGULAR', canonical_track_count=11 WHERE id=?",
                 (album_id,))

    stats = backfill_editions(conn.cursor())

    assert stats["pinned_editions"] == 1
    assert dict(_edition(conn, album_id))["spotify_id"] == "REGULAR"
    assert _edition(conn, album_id)["track_count"] == 11


def test_clearing_the_manual_match_lifts_its_pin(conn):
    album_id = _album(conn)
    backfill_editions(conn.cursor())
    pin_album_release(conn, album_id, "spotify", "REGULAR")
    assert conn.execute("SELECT canonical_locked FROM lib2_albums WHERE id=?",
                        (album_id,)).fetchone()[0] == 1

    set_library_v2_match(conn, "album", album_id, "spotify", None)
    pin_album_release(conn, album_id, "spotify", None)

    assert conn.execute("SELECT canonical_album_id FROM lib2_albums WHERE id=?",
                        (album_id,)).fetchone()[0] is None
    assert _edition(conn, album_id)["spotify_id"] is None


def test_set_album_canonical_moves_the_default_edition(tmp_path):
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / "music.db"))
    with db._get_connection() as c:
        c.execute("INSERT INTO lib2_artists(id, name) VALUES (1, 'Artist')")
        album_id = _album(c)
        backfill_editions(c.cursor())
        c.commit()

    assert db.set_album_canonical(album_id, "spotify", "REGULAR", 1.0, locked=True)

    with db._get_connection() as c:
        assert _edition(c, album_id)["spotify_id"] == "REGULAR"
