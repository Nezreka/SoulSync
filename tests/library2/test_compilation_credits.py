"""A compilation track credited to "Various Artists" alone gets its performer
back from the file's ARTIST tag (feature-parity A05)."""

from __future__ import annotations

import pytest

from core.library2.duplicate_relationship import validate_duplicate_pair
from core.library2.schema import ensure_library_v2_schema
from core.library2.tag_cache import persist_tag_cache
from tests import lib2_seed
from tests.lib2_seed import row_conn


@pytest.fixture
def conn(tmp_path):
    c = row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(c)
    yield c
    c.close()


def _file_id(conn, track_id):
    return conn.execute("SELECT id FROM lib2_track_files WHERE track_id=?",
                        (track_id,)).fetchone()[0]


def test_the_tag_names_the_performer_of_a_various_artists_track(conn):
    comp = lib2_seed.track(conn, "Various Artists", "Trail Songs", "Such Great Heights",
                           duration=250_000, album_cols={"album_type": "compilation"})
    own = lib2_seed.track(conn, "Iron & Wine", "Around the Well", "Such Great Heights",
                          duration=251_000)
    performer = conn.execute("SELECT id FROM lib2_artists WHERE name='Iron & Wine'").fetchone()[0]

    persist_tag_cache(conn, _file_id(conn, comp), {"artist": "Iron & Wine",
                                                   "album_artist": "Various Artists"})

    credited = {r[0] for r in conn.execute(
        "SELECT artist_id FROM lib2_track_artists WHERE track_id=?", (comp,))}
    assert performer in credited
    assert conn.execute("SELECT track_artist FROM lib2_tracks WHERE id=?",
                        (comp,)).fetchone()[0] == "Iron & Wine"
    # the safety validator now has the evidence it asked for
    validate_duplicate_pair(conn, comp, own)


@pytest.mark.parametrize("album_artist, tag_artist", [
    ("Iron & Wine", "Someone Else"),          # not a compilation
    ("Various Artists", "Various Artists"),   # the tag says nothing more
])
def test_nothing_changes_outside_the_narrow_case(conn, album_artist, tag_artist):
    track = lib2_seed.track(conn, album_artist, "Record", "Song")

    persist_tag_cache(conn, _file_id(conn, track), {"artist": tag_artist})

    assert conn.execute("SELECT COUNT(*) FROM lib2_track_artists WHERE track_id=?",
                        (track,)).fetchone()[0] == 0


def test_an_existing_performer_credit_is_left_alone(conn):
    track = lib2_seed.track(conn, "Various Artists", "Comp", "Song", credit="Real Performer")

    persist_tag_cache(conn, _file_id(conn, track), {"artist": "Tag Performer"})

    names = {r[0] for r in conn.execute(
        "SELECT ar.name FROM lib2_track_artists ta JOIN lib2_artists ar ON ar.id=ta.artist_id"
        " WHERE ta.track_id=?", (track,))}
    assert names == {"Real Performer"}
