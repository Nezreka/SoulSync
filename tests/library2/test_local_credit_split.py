"""Guest credits settle from the library's own knowledge, no network.

The live upgrade of a 310k-track library made 28,860 artists out of track
credits like "1788-L, Deathpact" (credit strings, not artists), and the healer
then looked each up online, one by one: 39 done in 35 minutes, weeks to go.
18,512 of them carried the album's own artist as one part, 44 more had every
part already known. Those split here, at import and in the healer; a real
band name ("Earth, Wind & Fire") never does.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.importer import _credit_names_for_import, normalize_name
from core.library2.native_enrich import split_combined_credits_locally
from core.library2.schema import ensure_library_v2_schema


# ── the importer ─────────────────────────────────────────────────────────────

def _known(*names):
    keys = {normalize_name(n) for n in names}
    return lambda name: normalize_name(name) in keys


def test_a_credit_with_the_album_artist_in_it_is_a_list():
    assert _credit_names_for_import("1788-L, Deathpact", _known("1788-L"),
                                    album_artist="1788-L") == ["1788-L", "Deathpact"]


def test_without_the_album_artist_an_unknown_band_name_stays_whole():
    # one known part on someone else's album is not enough
    assert _credit_names_for_import("Earth, Wind & Fire", _known("Earth"),
                                    album_artist="Various Artists") == ["Earth, Wind & Fire"]


def test_a_band_on_its_own_album_is_never_split():
    assert _credit_names_for_import("Earth, Wind & Fire", _known("Earth, Wind & Fire"),
                                    album_artist="Earth, Wind & Fire") == ["Earth, Wind & Fire"]


# ── the healer ───────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    return c


def _artist(conn, name, spotify=None, legacy=None):
    return conn.execute("INSERT INTO lib2_artists(name, name_key, spotify_id, legacy_artist_id) "
                        "VALUES(?,?,?,?)", (name, normalize_name(name), spotify, legacy)).lastrowid


def _credited_track(conn, album_artist, credited, title="Song"):
    album = conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')",
                         (album_artist,)).lastrowid
    track = conn.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?, ?)",
                         (album, title)).lastrowid
    conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                 "VALUES(?,?, 'primary', 0)", (track, album_artist))
    conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                 "VALUES(?,?, 'featured', 1)", (track, credited))
    return track


def _credits(conn, track):
    return [r[0] for r in conn.execute(
        "SELECT a.name FROM lib2_track_artists ta JOIN lib2_artists a ON a.id=ta.artist_id "
        "WHERE ta.track_id=? ORDER BY ta.position, a.name", (track,))]


def _exists(conn, artist_id):
    return conn.execute("SELECT 1 FROM lib2_artists WHERE id=?", (artist_id,)).fetchone() is not None


def test_a_ghost_with_the_album_artist_in_it_splits(conn):
    lead = _artist(conn, "1788-L", spotify="sp-1788", legacy=5)
    ghost = _artist(conn, "1788-L, Deathpact")
    track = _credited_track(conn, lead, ghost)

    stats = split_combined_credits_locally(conn)

    assert (stats["split"], stats["created"]) == (1, 1)
    assert not _exists(conn, ghost)
    assert _credits(conn, track) == ["1788-L", "Deathpact"]
    # the known part kept its artist, ids and all
    assert conn.execute("SELECT spotify_id FROM lib2_artists WHERE id=?", (lead,)).fetchone()[0] == "sp-1788"


def test_a_ghost_named_like_a_known_artist_merges(conn):
    real = _artist(conn, "Tyler, The Creator", spotify="sp-tyler", legacy=9)
    host = _artist(conn, "Kali Uchis", spotify="sp-kali", legacy=10)
    ghost = _artist(conn, "Tyler, The Creator")
    track = _credited_track(conn, host, ghost)

    stats = split_combined_credits_locally(conn)

    assert stats["merged"] == 1 and stats["split"] == 0
    assert not _exists(conn, ghost)
    assert _credits(conn, track) == ["Kali Uchis", "Tyler, The Creator"]
    assert conn.execute("SELECT COUNT(*) FROM lib2_track_artists WHERE artist_id=?",
                        (real,)).fetchone()[0] == 1


def test_an_unknown_band_on_someone_elses_album_is_left_for_the_online_pass(conn):
    host = _artist(conn, "Various Artists", spotify="sp-va", legacy=11)
    _artist(conn, "Crosby", spotify="sp-crosby", legacy=12)
    ghost = _artist(conn, "Crosby, Stills & Nash")
    track = _credited_track(conn, host, ghost)

    stats = split_combined_credits_locally(conn)

    assert stats["left"] == 1 and stats["split"] == 0
    assert _exists(conn, ghost)
    assert _credits(conn, track) == ["Various Artists", "Crosby, Stills & Nash"]


def test_every_part_known_splits_even_off_the_album_artist(conn):
    host = _artist(conn, "Various Artists", spotify="sp-va", legacy=11)
    a = _artist(conn, "Asian Doll", spotify="sp-ad", legacy=13)
    b = _artist(conn, "Antha Pantha", spotify="sp-ap", legacy=14)
    ghost = _artist(conn, "Asian Doll & Antha Pantha")
    track = _credited_track(conn, host, ghost)

    stats = split_combined_credits_locally(conn)

    assert stats["split"] == 1 and stats["created"] == 0
    assert not _exists(conn, ghost)
    assert {r[0] for r in conn.execute("SELECT artist_id FROM lib2_track_artists WHERE track_id=?",
                                       (track,))} == {host, a, b}


def test_a_ghost_that_owns_an_album_is_never_touched(conn):
    ghost = _artist(conn, "Owner, Of Albums")
    conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Mine')", (ghost,))
    stats = split_combined_credits_locally(conn)
    assert stats["considered"] == 0 and _exists(conn, ghost)


def test_the_automatic_online_pass_only_takes_what_the_workers_cannot(conn):
    """A plain single name is every enrichment worker's to match; the online
    pass after the upgrade only walks the combined credits they skip."""
    from core.library2.native_enrich import _pending_unmapped_artists
    single = _artist(conn, "Geino")
    combined = _artist(conn, "Skratch Bastid, Shad")
    everything = {r["id"] for r in _pending_unmapped_artists(conn, None)}
    automatic = {r["id"] for r in _pending_unmapped_artists(conn, None, combined_only=True)}
    assert {single, combined} <= everything
    assert automatic == {combined}
