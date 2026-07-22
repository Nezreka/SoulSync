"""Server-side candidate identity, capabilities, TTL and redaction."""

from __future__ import annotations

import sqlite3

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import (
    CANDIDATE_ID_PREFIX,
    list_request_candidates,
    prune_expired_candidates,
    register_candidate,
    resolve_candidate,
)
from core.acquisition.capabilities import SourceCapabilities
from core.acquisition.requests import create_request, transition_request


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


def _request(conn, key="search-1"):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="release_edition",
        entity_id=7,
        quality_profile_id=2,
        trigger="manual",
        idempotency_key=key,
    )
    return transition_request(conn, request.id, "searching")


def _candidate(conn, request_id, **overrides):
    values = {
        "request_id": request_id,
        "source": "usenet",
        "protocol": "usenet",
        "content_scope": "release_bundle",
        "server_ref": "ssc1-opaque-token",
        "title": "Artist - Album [FLAC]",
        "indexer": "indexer-a",
        "guid": "guid-1",
        "size_bytes": 500_000_000,
        "age_seconds": 3600,
        "grabs": 12,
        "facts": {
            "artist": "Artist",
            "release_title": "Album",
            "format": "flac",
            "track_count": 10,
        },
        "raw_payload": {
            "release": "Album",
            "download_url": "https://secret.invalid/?apikey=SECRET",
            "nested": {
                "api_key": "SECRET",
                "category": "music",
                "mirror": "https://also-secret.invalid/download",
            },
        },
        "now": 1000.0,
        "ttl_seconds": 100,
    }
    values.update(overrides)
    return register_candidate(conn, **values)


def test_candidate_is_opaque_request_bound_and_browser_safe(conn):
    request = _request(conn)
    candidate, created = _candidate(conn, request.id)

    assert created is True
    assert candidate.id.startswith(CANDIDATE_ID_PREFIX)
    assert candidate.request_id == request.id
    assert candidate.facts.format == "flac"
    assert candidate.raw_payload["download_url"] == "[redacted]"
    assert candidate.raw_payload["nested"]["api_key"] == "[redacted]"
    assert candidate.raw_payload["nested"]["mirror"] == "[redacted]"
    public = candidate.to_public_dict()
    assert "server_ref" not in public
    assert "raw_payload" not in public
    assert "SECRET" not in str(public)


def test_resolve_enforces_request_profile_and_expiry(conn):
    request = _request(conn)
    candidate, _ = _candidate(conn, request.id)

    assert resolve_candidate(
        conn, candidate.id, request_id=request.id, profile_id=1, now=1099.0
    ) == candidate
    assert resolve_candidate(
        conn, candidate.id, request_id="other", profile_id=1, now=1099.0
    ) is None
    assert resolve_candidate(
        conn, candidate.id, request_id=request.id, profile_id=2, now=1099.0
    ) is None
    assert resolve_candidate(
        conn, candidate.id, request_id=request.id, profile_id=1, now=1100.0
    ) is None


def test_guid_indexer_dedup_updates_facts_and_ttl(conn):
    request = _request(conn)
    first, first_created = _candidate(conn, request.id)
    second, second_created = _candidate(
        conn,
        request.id,
        server_ref="ssc1-refreshed",
        seeders=30,
        facts={"artist": "Artist", "release_title": "Album", "format": "flac",
               "track_count": 11},
        now=1050.0,
    )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert second.server_ref == "ssc1-refreshed"
    assert second.facts.track_count == 11
    assert second.expires_at == 1150.0
    assert len(list_request_candidates(conn, request.id, now=1100.0)) == 1


def test_same_guid_isolated_between_requests(conn):
    first_request = _request(conn, "search-1")
    second_request = _request(conn, "search-2")
    first, _ = _candidate(conn, first_request.id)
    second, _ = _candidate(conn, second_request.id)

    assert first.id != second.id


def test_expired_candidates_are_pruned(conn):
    request = _request(conn)
    _candidate(conn, request.id)

    assert list_request_candidates(conn, request.id, now=1100.0) == []
    assert prune_expired_candidates(conn, now=1100.0) == 1


def test_deleting_request_cascades_candidates(conn):
    request = _request(conn)
    _candidate(conn, request.id)

    conn.execute("DELETE FROM acquisition_requests WHERE id=?", (request.id,))

    assert conn.execute("SELECT COUNT(*) FROM release_candidates").fetchone()[0] == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "unknown"},
        {"content_scope": "recording"},
        {"server_ref": "https://indexer.invalid/download"},
        {"server_ref": "magnet:?xt=urn:btih:secret"},
        {"ttl_seconds": 0},
        {"size_bytes": -1},
    ],
)
def test_candidate_contract_rejects_unsafe_or_incompatible_values(conn, overrides):
    request = _request(conn)
    with pytest.raises(ValueError):
        _candidate(conn, request.id, **overrides)


def test_candidate_requires_active_search_request(conn):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="release_edition",
        entity_id=7,
        quality_profile_id=2,
        trigger="manual",
        idempotency_key="pending",
    )

    with pytest.raises(ValueError, match="while request is pending"):
        _candidate(conn, request.id)


def test_source_capability_requires_exactly_one_content_scope():
    with pytest.raises(ValueError):
        SourceCapabilities("invalid", True, True)
    with pytest.raises(ValueError):
        SourceCapabilities("invalid", False, False)
