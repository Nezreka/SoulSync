"""Typed provider snapshot storage and migration invariants."""

from __future__ import annotations

import sqlite3

import pytest

from core.library2.provider_snapshots import (
    canonical_payload,
    delete_entity_snapshots,
    get_provider_snapshot,
    record_provider_snapshot,
)
from core.library2.schema import ensure_library_v2_schema


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_library_v2_schema(conn)
    return conn


def test_schema_ensure_creates_snapshot_table_idempotently():
    conn = _connection()

    ensure_library_v2_schema(conn)

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='library_provider_snapshots'"
    ).fetchone()
    assert row is not None
    assert "UNIQUE(provider, entity_type, entity_id, scope)" in row["sql"]


def test_canonical_payload_hash_ignores_mapping_order():
    first_json, first_hash = canonical_payload({"b": 2, "a": [1, {"z": True}]})
    second_json, second_hash = canonical_payload({"a": [1, {"z": True}], "b": 2})

    assert first_json == second_json
    assert first_hash == second_hash


def test_record_and_read_snapshot_with_full_provenance():
    conn = _connection()

    result = record_provider_snapshot(
        conn,
        provider="Spotify",
        entity_type="Artist",
        entity_id=12,
        scope="Discography",
        provider_entity_id="sp-artist",
        etag='"abc"',
        provider_version="cursor-v2",
        parser_version="discography/1",
        payload={"releases": [{"id": "r1", "title": "Album"}]},
        is_complete=False,
        cursor="next-page",
        page_count=1,
    )

    assert result.payload_changed is True
    assert result.previous_hash is None
    assert result.snapshot.provider == "spotify"
    assert result.snapshot.entity_type == "artist"
    assert result.snapshot.scope == "discography"
    assert result.snapshot.is_complete is False
    assert result.snapshot.cursor == "next-page"
    assert result.snapshot.payload["releases"][0]["id"] == "r1"
    assert get_provider_snapshot(
        conn, provider="SPOTIFY", entity_type="ARTIST", entity_id=12,
        scope="DISCOGRAPHY",
    ) == result.snapshot


def test_upsert_reports_payload_noop_but_refreshes_metadata():
    conn = _connection()
    first = record_provider_snapshot(
        conn,
        provider="deezer",
        entity_type="album",
        entity_id=9,
        scope="tracklist",
        parser_version="tracklist/1",
        payload={"tracks": [{"id": "t1"}]},
        is_complete=False,
        cursor="page-2",
        page_count=1,
    )

    second = record_provider_snapshot(
        conn,
        provider="deezer",
        entity_type="album",
        entity_id=9,
        scope="tracklist",
        parser_version="tracklist/2",
        payload={"tracks": [{"id": "t1"}]},
        is_complete=True,
        page_count=2,
    )

    assert second.payload_changed is False
    assert second.previous_hash == first.snapshot.payload_hash
    assert second.snapshot.id == first.snapshot.id
    assert second.snapshot.is_complete is True
    assert second.snapshot.cursor is None
    assert second.snapshot.parser_version == "tracklist/2"
    assert conn.execute(
        "SELECT COUNT(*) FROM library_provider_snapshots"
    ).fetchone()[0] == 1


def test_payload_change_and_entity_cleanup_are_explicit():
    conn = _connection()
    first = record_provider_snapshot(
        conn,
        provider="musicbrainz",
        entity_type="album",
        entity_id=3,
        scope="tracklist",
        parser_version="tracklist/1",
        payload={"tracks": []},
        is_complete=True,
    )
    second = record_provider_snapshot(
        conn,
        provider="musicbrainz",
        entity_type="album",
        entity_id=3,
        scope="tracklist",
        parser_version="tracklist/1",
        payload={"tracks": [{"id": "new"}]},
        is_complete=True,
    )

    assert second.payload_changed is True
    assert second.previous_hash == first.snapshot.payload_hash
    assert delete_entity_snapshots(conn, entity_type="album", entity_id=3) == 1
    assert get_provider_snapshot(
        conn, provider="musicbrainz", entity_type="album", entity_id=3,
        scope="tracklist",
    ) is None


def test_entity_delete_triggers_prune_polymorphic_snapshots():
    conn = _connection()
    artist_id = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Snapshot Artist')"
    ).lastrowid
    album_id = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Snapshot Album')",
        (artist_id,),
    ).lastrowid
    track_id = conn.execute(
        "INSERT INTO lib2_tracks(album_id, title) VALUES(?, 'Snapshot Track')",
        (album_id,),
    ).lastrowid
    edition_id = conn.execute(
        "INSERT INTO lib2_release_editions(release_group_id, is_default) VALUES(?, 1)",
        (album_id,),
    ).lastrowid
    for entity_type, entity_id in (
        ("artist", artist_id),
        ("album", album_id),
        ("track", track_id),
        ("release_edition", edition_id),
    ):
        record_provider_snapshot(
            conn,
            provider="test",
            entity_type=entity_type,
            entity_id=entity_id,
            scope="metadata",
            parser_version="test/1",
            payload={"id": entity_id},
            is_complete=True,
        )

    conn.execute("DELETE FROM lib2_tracks WHERE id=?", (track_id,))
    conn.execute("DELETE FROM lib2_release_editions WHERE id=?", (edition_id,))
    conn.execute("DELETE FROM lib2_albums WHERE id=?", (album_id,))
    conn.execute("DELETE FROM lib2_artists WHERE id=?", (artist_id,))

    assert conn.execute(
        "SELECT COUNT(*) FROM library_provider_snapshots"
    ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"provider": ""}, "provider is required"),
        ({"entity_id": 0}, "entity_id must be positive"),
        ({"page_count": -1}, "page_count cannot be negative"),
        ({"payload": {"bad": float("nan")}}, "payload must be valid JSON"),
    ],
)
def test_record_rejects_invalid_contract_values(kwargs, message):
    conn = _connection()
    values = {
        "provider": "spotify",
        "entity_type": "artist",
        "entity_id": 1,
        "scope": "discography",
        "parser_version": "discography/1",
        "payload": {"releases": []},
        "is_complete": True,
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=message):
        record_provider_snapshot(conn, **values)
