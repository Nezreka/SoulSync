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
