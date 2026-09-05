"""The artist rows behind the Taste Map and the Discovery Web.

Both graphs start from "every artist the library holds", and both put that
row's id into a node the user can click. That makes the id space the whole
point: the link goes through `/artist-detail/<source>/<id>`, which redirects
`library:<n>` into Library V2 as `?artist=<n>` (§50.4.4.23).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.graph.library_artists import load_library_artists
from core.library2.importer import normalize_name
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture()
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_library_v2_schema(connection)
    connection.commit()
    yield connection
    connection.close()


def _artist(conn, name, *, image_url=None, genres=None, legacy_artist_id=None,
            canonical_of=None) -> int:
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name, name_key, image_url, genres,"
        "                         legacy_artist_id, canonical_artist_id)"
        " VALUES(?,?,?,?,?,?)",
        (name, normalize_name(name), image_url, json.dumps(genres or []),
         legacy_artist_id, canonical_of)).lastrowid
    conn.commit()
    return int(artist_id)


def test_nodes_carry_the_catalogue_id_the_link_resolves(conn):
    """Not ``legacy_artist_id``: the artist link lands in Library V2, which
    reads the number as ``lib2_artists.id``."""
    artist_id = _artist(conn, 'Muse', image_url='muse.jpg', legacy_artist_id=999)

    owned, meta, artists = load_library_artists(conn)

    assert owned == {'muse'}
    assert meta['muse'] == {'thumb_url': 'muse.jpg', 'genres': '[]',
                            'id': artist_id}
    assert artists == [('Muse', '[]', 'muse.jpg', artist_id, 'library')]


def test_the_node_source_is_the_library_not_a_media_server(conn):
    """The old rows carried ``server_source`` ('plex'), and the link built from
    it — `/artist-detail/plex/<id>` — was read as a PROVIDER discovery request
    for a provider that does not exist. A library artist opens the library."""
    _artist(conn, 'Muse')

    _owned, _meta, artists = load_library_artists(conn)

    assert artists[0][4] == 'library'


def test_genres_travel_as_the_stored_json(conn):
    """The graph builder parses a JSON-array string; v2 stores exactly that."""
    _artist(conn, 'Muse', genres=['Rock', 'Alternative'])

    _owned, meta, _artists = load_library_artists(conn)

    assert json.loads(meta['muse']['genres']) == ['Rock', 'Alternative']


def test_an_alias_row_is_not_a_second_node(conn):
    """§40: an alias member is the same artist. Two nodes for one artist would
    split its similarity edges across both."""
    canonical = _artist(conn, 'Beyoncé')
    _artist(conn, 'Beyonce Knowles', canonical_of=canonical)

    owned, _meta, artists = load_library_artists(conn)

    assert owned == {'beyoncé'}
    assert [a[3] for a in artists] == [canonical]


def test_an_unnamed_row_is_no_node(conn):
    conn.execute("INSERT INTO lib2_artists(name, name_key) VALUES('','')")
    conn.commit()

    owned, meta, artists = load_library_artists(conn)

    assert (owned, meta, artists) == (set(), {}, [])
