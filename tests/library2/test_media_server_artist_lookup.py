"""The one thing the media server actually got out of the legacy tables.

The enrichment flow is almost entirely one-way: media server → legacy rows →
SoulSync's own UI. Exactly one path goes back the other way. The metadata-update
worker (``core/workers/metadata_update.py``) reads the legacy ``artists`` row
and pushes its genres into Plex/Jellyfin, and uses the row's stored Spotify id
to skip a provider search. Photos and album art always come straight from
Spotify, and ``update_artist_biography`` only stamps a timestamp into the
server's own summary — the collected bios never left SoulSync.

So this worker is a *reader* of legacy, not a writer, and it needs precisely two
fields. Both already exist on the lib2 row, which is why moving it is mechanical
(user decision, 11 August 2026) and does not touch the open question of whether
a media-server scan may create lib2 rows.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from core.library2.queries import find_artists_by_name
from core.library2.schema import ensure_library_v2_schema


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "lib2.db"))
    c.row_factory = sqlite3.Row
    ensure_library_v2_schema(c)
    yield c
    c.close()


def _artist(conn, name, **columns):
    columns.setdefault("sort_name", name)
    cols = ", ".join(["name", *columns])
    holes = ", ".join("?" for _ in range(len(columns) + 1))
    return conn.execute(
        f"INSERT INTO lib2_artists({cols}) VALUES({holes})",
        (name, *columns.values()),
    ).lastrowid


def test_returns_the_two_fields_the_media_server_path_needs(conn):
    artist_id = _artist(conn, "Massive Attack", genres='["trip hop","downtempo"]',
                        spotify_id="sp-1")
    conn.commit()

    found = find_artists_by_name(conn, "Massive Attack")

    assert [item["id"] for item in found] == [artist_id]
    assert found[0]["name"] == "Massive Attack"
    assert found[0]["genres"] == ["trip hop", "downtempo"]
    assert found[0]["spotify_id"] == "sp-1"


def test_the_spotify_id_is_found_in_external_ids_too(conn):
    """Not every row has the promoted column filled; the JSON is the other
    half of the same truth (see ``enrich._sync_dedicated_id_columns``)."""
    _artist(conn, "Portishead",
            external_ids=json.dumps({"spotify": "sp-2", "deezer": "dz-9"}))
    conn.commit()

    assert find_artists_by_name(conn, "Portishead")[0]["spotify_id"] == "sp-2"


def test_matches_on_a_partial_name(conn):
    _artist(conn, "Massive Attack")
    conn.commit()

    assert len(find_artists_by_name(conn, "massive")) == 1


def test_a_user_override_of_the_name_or_genres_wins(conn):
    """lib2 layers user overrides at read time. A lookup that returned the raw
    provider values would push genres the user has explicitly corrected into
    the media server.

    The filter itself matches the stored name, exactly as ``list_artists``
    does — the projection happens after the page is chosen, and a lookup that
    searched overridden names would be new behaviour, not a port.
    """
    from core.library2.metadata_overrides import set_field_override

    artist_id = _artist(conn, "Masive Atack", genres='["trip hip"]')
    for field, value in (("name", "Massive Attack"), ("genres", ["trip hop"])):
        set_field_override(conn, entity_type="artist", entity_id=artist_id,
                           field_name=field, value=value)
    conn.commit()

    found = find_artists_by_name(conn, "Masive Atack")

    assert [item["name"] for item in found] == ["Massive Attack"]
    assert found[0]["genres"] == ["trip hop"]


def test_alias_member_rows_are_not_listed_separately(conn):
    """§40 folds alias members into their canonical artist everywhere else;
    returning both would make the worker push the same genres twice."""
    canonical = _artist(conn, "Massive Attack")
    _artist(conn, "Massive Attack UK", canonical_artist_id=canonical)
    conn.commit()

    assert [item["id"] for item in find_artists_by_name(conn, "Massive")] == [canonical]


def test_the_limit_is_honoured(conn):
    for index in range(6):
        _artist(conn, f"Massive {index}")
    conn.commit()

    assert len(find_artists_by_name(conn, "Massive", limit=2)) == 2
