"""a band with "&" in its name is one artist, never a list of its words (#2136).

SeadogsBooty: "Above & Beyond" came out as "Above (feat. Beyond)", "Kyau &
Albert" as "Kyau". the library's own credit splitter cut "Above & Beyond &
Mat Zo" on every "&" and made "Above" and "Beyond" artists, credited next to
"Above & Beyond" on the same track. those phantoms then named the tags and
the reorganize folder (#2131).
"""

from __future__ import annotations

import sqlite3

import pytest

from core.library2 import native_enrich as NE
from core.library2.importer import (
    _credit_names_for_import,
    normalize_name,
    split_known_artist_credits,
)
from core.library2.schema import ensure_library_v2_schema


def _known(*names):
    keys = {normalize_name(n) for n in names}
    return lambda name: normalize_name(name) in keys


# ── the splitter ─────────────────────────────────────────────────────────────

def test_a_known_band_inside_a_credit_stays_whole():
    known = _known("Above & Beyond", "Above", "Beyond")
    assert split_known_artist_credits("Above & Beyond & Mat Zo", known) == [
        "Above & Beyond", "Mat Zo"]
    assert split_known_artist_credits("Mat Zo, Above & Beyond", known) == [
        "Mat Zo", "Above & Beyond"]
    assert split_known_artist_credits("Kyau & Albert x Super8 & Tab",
                                      _known("Kyau & Albert", "Super8 & Tab")) == [
        "Kyau & Albert", "Super8 & Tab"]


def test_real_features_still_split():
    known = _known("Above & Beyond")
    assert split_known_artist_credits("Above & Beyond feat. Zoë Johnston", known) == [
        "Above & Beyond", "Zoë Johnston"]
    # nothing known: the same answer the blind split gives
    assert split_known_artist_credits("Ian Asher & Galantis", _known()) == [
        "Ian Asher", "Galantis"]
    # a feat. marker is a boundary even when the words around it spell a name
    assert split_known_artist_credits("A feat. B", _known("A feat. B")) == ["A", "B"]


def test_the_importer_keeps_the_band_even_with_phantoms_known():
    known = _known("Above & Beyond", "Above", "Beyond", "Mat Zo")
    assert _credit_names_for_import("Above & Beyond, Mat Zo", known,
                                    album_artist="Above & Beyond") == [
        "Above & Beyond", "Mat Zo"]


# ── the healer ───────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    yield c
    c.close()


def _artist(conn, name, spotify=None):
    return conn.execute("INSERT INTO lib2_artists(name, name_key, spotify_id) VALUES(?,?,?)",
                        (name, normalize_name(name), spotify)).lastrowid


def _album(conn, primary):
    return conn.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Album')",
                        (primary,)).lastrowid


def _track(conn, album, credits):
    track = conn.execute("INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Song')",
                         (album,)).lastrowid
    for pos, artist in enumerate(credits):
        conn.execute("INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
                     "VALUES(?,?,?,?)", (track, artist, "primary" if pos == 0 else "featured", pos))
    return track


def _provider(conn, track, *names):
    """the list a provider credits on the track: the confirmation a split needs"""
    for position, name in enumerate(names):
        conn.execute("INSERT INTO lib2_provider_credits(entity_type, entity_id, source, "
                     "position, name, source_artist_id) VALUES('track', ?, 'spotify', ?, ?, ?)",
                     (track, position, name, f"sp-{name}"))


def _credits(conn, track):
    return [r[0] for r in conn.execute(
        "SELECT a.name FROM lib2_track_artists ta JOIN lib2_artists a ON a.id=ta.artist_id "
        "WHERE ta.track_id=? ORDER BY ta.position", (track,))]


def _names(conn):
    return {r[0] for r in conn.execute("SELECT name FROM lib2_artists")}


def _resolver(name):
    # every word is some artist somewhere: "Above" and "Beyond" both resolve
    return {"source": "spotify", "artist_id": "SP_" + name.replace(" ", "_"),
            "name": name, "image_url": "", "genres": []}


def test_the_local_split_keeps_the_band(conn):
    band = _artist(conn, "Above & Beyond", spotify="10gzBoINW3cLJfZUka8Zoe")
    _artist(conn, "Mat Zo", spotify="2n7USVO8fO8FF8zq4kG2N1")
    ghost = _artist(conn, "Above & Beyond & Mat Zo")
    track = _track(conn, _album(conn, band), [band, ghost])
    _provider(conn, track, "Above & Beyond", "Mat Zo")

    NE.split_combined_credits_locally(conn)

    assert _credits(conn, track) == ["Above & Beyond", "Mat Zo"]
    assert not {"Above", "Beyond"} & _names(conn)


def test_the_online_split_keeps_the_band(conn):
    _artist(conn, "Above & Beyond", spotify="10gzBoINW3cLJfZUka8Zoe")
    ghost = _artist(conn, "Above & Beyond & Mat Zo")
    track = _track(conn, _album(conn, ghost), [ghost])
    _provider(conn, track, "Above & Beyond", "Mat Zo")

    assert NE.smart_split_combined_artist(conn, ghost, resolver=_resolver) is not None

    assert _credits(conn, track) == ["Above & Beyond", "Mat Zo"]
    assert not {"Above", "Beyond"} & _names(conn)


def test_the_online_split_leaves_a_name_the_library_already_knows(conn):
    _artist(conn, "Kyau & Albert", spotify="SP_KA")
    copy = _artist(conn, "Kyau & Albert")
    _track(conn, _album(conn, copy), [copy])

    assert NE.smart_split_combined_artist(conn, copy, resolver=_resolver) is None
    assert not {"Kyau", "Albert"} & _names(conn)


def test_a_real_collab_still_splits_online(conn):
    ghost = _artist(conn, "Ian Asher & Galantis")
    track = _track(conn, _album(conn, ghost), [ghost])
    # names alone propose it; the provider's list confirms it
    assert NE.smart_split_combined_artist(conn, ghost, resolver=_resolver) is None
    _provider(conn, track, "Ian Asher", "Galantis")

    assert NE.smart_split_combined_artist(conn, ghost, resolver=_resolver) is not None
    assert _credits(conn, track) == ["Ian Asher", "Galantis"]


# ── the repair for libraries the old split already cut ──────────────────────

def test_the_phantom_parts_come_off_a_track_that_credits_the_band(conn):
    band = _artist(conn, "Above & Beyond", spotify="10gzBoINW3cLJfZUka8Zoe")
    above, beyond = _artist(conn, "Above", spotify="x1"), _artist(conn, "Beyond", spotify="x2")
    mat = _artist(conn, "Mat Zo", spotify="x3")
    album = _album(conn, band)
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?, 'primary')",
                 (album, band))
    for part in (above, beyond):
        conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) "
                     "VALUES(?,?, 'featured')", (album, part))
    track = _track(conn, album, [band, above, beyond, mat])
    # a track that only names one of the words keeps it: that one may be real
    other = _track(conn, _album(conn, above), [above, mat])

    assert NE.drop_band_fragment_credits(conn) == 4

    assert _credits(conn, track) == ["Above & Beyond", "Mat Zo"]
    assert {r[0] for r in conn.execute(
        "SELECT artist_id FROM lib2_album_artists WHERE album_id=?", (album,))} == {band}
    assert _credits(conn, other) == ["Above", "Mat Zo"]


def test_a_real_guest_next_to_a_band_is_left_alone(conn):
    """Simon & Garfunkel with Paul Simon is two different names, nothing to take."""
    duo = _artist(conn, "Simon & Garfunkel", spotify="sg")
    paul = _artist(conn, "Paul Simon", spotify="ps")
    track = _track(conn, _album(conn, duo), [duo, paul])

    assert NE.drop_band_fragment_credits(conn) == 0
    assert _credits(conn, track) == ["Simon & Garfunkel", "Paul Simon"]


def _album_credits(conn, album):
    return {r[0]: r[1] for r in conn.execute(
        "SELECT a.name, aa.role FROM lib2_album_artists aa JOIN lib2_artists a ON a.id=aa.artist_id "
        "WHERE aa.album_id=?", (album,))}


def _primary(conn, album):
    return conn.execute("SELECT a.name FROM lib2_albums al JOIN lib2_artists a "
                        "ON a.id=al.primary_artist_id WHERE al.id=?", (album,)).fetchone()[0]


def _credit_album(conn, album, artist, role):
    conn.execute("INSERT INTO lib2_album_artists(album_id, artist_id, role) VALUES(?,?,?)",
                 (album, artist, role))


def test_an_album_filed_under_a_phantom_part_goes_back_to_the_band(conn):
    """live shape: 'Circle 59' had primary "Foam" (no id) while "Foam and Sand"
    sat on it as a featured credit. dropping Foam's credit alone would leave
    the album filed under an artist nothing credits."""
    band = _artist(conn, "Foam and Sand", spotify="1auVJGxP1B7a0GYbD6G6dd")
    foam, sand = _artist(conn, "Foam"), _artist(conn, "Sand")
    guest = _artist(conn, "Stegonaute", spotify="st")
    album = _album(conn, foam)
    for artist, role in ((band, "featured"), (foam, "primary"), (sand, "featured"),
                         (guest, "featured")):
        _credit_album(conn, album, artist, role)
    track = _track(conn, album, [band, foam, sand, guest])

    NE.drop_band_fragment_credits(conn)

    assert _primary(conn, album) == "Foam and Sand"
    assert _album_credits(conn, album) == {"Foam and Sand": "primary", "Stegonaute": "featured"}
    assert _credits(conn, track) == ["Foam and Sand", "Stegonaute"]


def test_a_real_solo_artist_keeps_its_album(conn):
    """Drew Holcomb is a real artist with an id: his album and credits stay,
    even next to "Drew Holcomb & the Neighbors" and a phantom "the Neighbors"."""
    drew = _artist(conn, "Drew Holcomb", spotify="0Obcl00RkSejk0z0m4vUwT")
    band = _artist(conn, "Drew Holcomb & the Neighbors", spotify="dhn")
    neighbors = _artist(conn, "the Neighbors")
    ellie = _artist(conn, "Ellie Holcomb", spotify="eh")
    album = _album(conn, drew)
    for artist, role in ((drew, "primary"), (band, "featured"), (neighbors, "featured"),
                         (ellie, "featured")):
        _credit_album(conn, album, artist, role)
    track = _track(conn, album, [drew, band, neighbors, ellie])

    NE.drop_band_fragment_credits(conn)

    assert _primary(conn, album) == "Drew Holcomb"
    assert _album_credits(conn, album) == {
        "Drew Holcomb": "primary", "Drew Holcomb & the Neighbors": "featured",
        "the Neighbors": "featured", "Ellie Holcomb": "featured"}
    assert _credits(conn, track) == [
        "Drew Holcomb", "Drew Holcomb & the Neighbors", "the Neighbors", "Ellie Holcomb"]


def test_an_unidentified_credit_string_is_not_a_band(conn):
    """live shape: "bbno$, So Loki" (no id) on bbno$'s own track. it is a
    credit list, not a band, so bbno$ and So Loki keep theirs."""
    bbno = _artist(conn, "bbno$", spotify="41X1TR6hrK8Q2ZCpp2EqCz")
    combined, loki = _artist(conn, "bbno$, So Loki"), _artist(conn, "So Loki")
    track = _track(conn, _album(conn, bbno), [bbno, combined, loki])

    assert NE.drop_band_fragment_credits(conn) == 0
    assert _credits(conn, track) == ["bbno$", "bbno$, So Loki", "So Loki"]
