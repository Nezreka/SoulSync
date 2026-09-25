"""Parser and aggregation contract for acquisition source results."""

from __future__ import annotations

import sqlite3

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.eligibility_gate import CatalogContext
from core.acquisition.requests import create_request, transition_request
from core.acquisition.search_contract import (
    CandidateParseError,
    ParsedCandidate,
    aggregate_candidates,
    build_search_criteria,
    parse_candidate_batch,
)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


def _request(conn, *, scope="release_edition", options=None):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope=scope,
        entity_id=8,
        quality_profile_id=2,
        trigger="manual",
        idempotency_key=f"search-{scope}",
        search_options=options,
    )
    return transition_request(conn, request.id, "searching")


class _Parser:
    source = "usenet"

    def parse(self, payload, *, criteria):
        if payload == "skip":
            return None
        if payload == "bad":
            raise CandidateParseError("provider title is missing")
        return ParsedCandidate(
            source=self.source,
            protocol="usenet",
            content_scope="release_bundle",
            server_ref=f"ssc1-{payload['guid']}",
            title=payload["title"],
            indexer=payload.get("indexer"),
            guid=payload["guid"],
            size_bytes=payload.get("size"),
            facts={
                "artist": criteria.artist,
                "release_title": criteria.release_title,
                "edition": criteria.edition,
                "track_count": criteria.track_count,
                "format": "flac",
            },
            raw_payload=payload,
        )


def _criteria(conn):
    request = _request(
        conn,
        options={"identifiers": {"musicbrainz_release_id": "mb-release"}},
    )
    return build_search_criteria(
        request,
        CatalogContext(
            artist="Artist", release_title="Album", edition="Deluxe",
            track_count=12,
        ),
    )


def test_criteria_are_derived_from_request_and_catalog(conn):
    criteria = _criteria(conn)

    assert criteria.request_scope == "release_edition"
    assert criteria.content_scope == "release_bundle"
    assert criteria.text_query == "Artist Album Deluxe"
    assert criteria.identifiers == {"musicbrainz_release_id": "mb-release"}
    assert criteria.supports_source("usenet") is True
    assert criteria.supports_source("soulseek") is False


def test_upgrade_requires_explicit_searchable_entity_type(conn):
    request = _request(conn, scope="upgrade")

    with pytest.raises(ValueError, match="searchable content scope"):
        build_search_criteria(request, CatalogContext(artist="Artist"))


def test_batch_parser_isolates_malformed_rows(conn):
    criteria = _criteria(conn)
    batch = parse_candidate_batch(
        _Parser(),
        [
            {"guid": "one", "title": "Artist - Album", "size": 100},
            "bad",
            "skip",
            {"guid": "two", "title": "Artist - Album", "size": -1},
        ],
        criteria=criteria,
    )

    assert [candidate.guid for candidate in batch.candidates] == ["one"]
    assert batch.skipped == 1
    assert [(failure.position, failure.error) for failure in batch.failures] == [
        (1, "provider title is missing"),
        (3, "size_bytes must be a non-negative integer"),
    ]


def test_batch_parser_redacts_sensitive_failure_details(conn):
    class LeakyParser(_Parser):
        def parse(self, payload, *, criteria):
            raise CandidateParseError(
                "failed https://indexer.invalid/get?api_key=secret")

    batch = parse_candidate_batch(
        LeakyParser(), [{}], criteria=_criteria(conn))

    assert batch.failures[0].error == "failed [redacted]"
    assert "secret" not in batch.failures[0].error


def test_parsed_candidate_rejects_raw_url_and_capability_mismatch():
    with pytest.raises(CandidateParseError, match="opaque"):
        ParsedCandidate(
            source="usenet", protocol="usenet", content_scope="release_bundle",
            server_ref="https://indexer.invalid/download?api_key=secret",
            title="Artist - Album",
        )
    with pytest.raises(CandidateParseError, match="declares release_bundle"):
        ParsedCandidate(
            source="usenet", protocol="usenet", content_scope="recording",
            server_ref="ssc1-token", title="Artist - Track",
        )


def test_parser_source_must_be_explicit_and_consistent(conn):
    class WrongSourceParser(_Parser):
        def parse(self, payload, *, criteria):
            return ParsedCandidate(
                source="torrent", protocol="torrent",
                content_scope="release_bundle", server_ref="ssc1-token",
                title="Artist - Album",
            )

    batch = parse_candidate_batch(
        WrongSourceParser(), [{}], criteria=_criteria(conn))

    assert batch.candidates == ()
    assert "returned candidate for torrent" in batch.failures[0].error


def test_aggregation_persists_and_refreshes_guid_deduplication(conn):
    criteria = _criteria(conn)
    first_batch = parse_candidate_batch(
        _Parser(),
        [{
            "guid": "same", "title": "Artist - Album", "size": 100,
            "indexer": "indexer-a",
        }],
        criteria=criteria,
    )
    first = aggregate_candidates(
        conn, criteria=criteria, batches=[first_batch], ttl_seconds=100, now=1000)
    second_batch = parse_candidate_batch(
        _Parser(),
        [{
            "guid": "same", "title": "Artist - Album FLAC", "size": 200,
            "indexer": "indexer-a",
        }],
        criteria=criteria,
    )
    second = aggregate_candidates(
        conn, criteria=criteria, batches=[second_batch], ttl_seconds=100, now=1010)

    assert first.created_count == 1 and first.refreshed_count == 0
    assert second.created_count == 0 and second.refreshed_count == 1
    refreshed = second.registrations[0].candidate
    assert refreshed.id == first.registrations[0].candidate.id
    assert refreshed.title == "Artist - Album FLAC"
    assert refreshed.size_bytes == 200
    assert refreshed.expires_at == 1110
    assert "server_ref" not in refreshed.to_public_dict()


def test_recording_request_rejects_bundle_parser_before_provider_work(conn):
    request = _request(conn, scope="recording")
    criteria = build_search_criteria(
        request, CatalogContext(artist="Artist", release_title="Track"))

    with pytest.raises(ValueError, match="cannot search recording"):
        parse_candidate_batch(_Parser(), [], criteria=criteria)
