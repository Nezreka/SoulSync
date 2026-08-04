"""Prowlarr release results normalized under the Phase-4 contract."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.prowlarr_adapter import (
    ProwlarrAcquisitionAdapter,
    ProwlarrCandidateParser,
    parse_indexer_ids,
)
from core.acquisition.requests import create_request, transition_request
from core.acquisition.search_contract import (
    SearchCriteria,
    aggregate_candidates,
    parse_candidate_batch,
)
from core.download_plugins.candidate_store import (
    CandidateStore,
    candidate_binding,
)
from core.prowlarr_client import ProwlarrSearchResult


def _criteria(**overrides):
    values = {
        "request_id": "arq1-test",
        "profile_id": 1,
        "request_scope": "release_edition",
        "entity_id": 9,
        "content_scope": "release_bundle",
        "artist": "Björk",
        "release_title": "Debut",
        "edition": "Deluxe Edition",
        "track_count": 12,
    }
    values.update(overrides)
    return SearchCriteria(**values)


def _result(**overrides):
    values = {
        "guid": "guid-1",
        "title": "Bjork - Debut [Deluxe Edition] [24bit 96kHz FLAC]",
        "indexer_id": 4,
        "indexer_name": "Indexer",
        "protocol": "usenet",
        "download_url": "https://indexer.invalid/get?id=1&api_key=secret",
        "size": 900_000_000,
        "seeders": None,
        "grabs": 18,
        "publish_date": "2029-12-31T23:00:00Z",
        "categories": [3000, 3040],
        "raw": {"downloadUrl": "https://indexer.invalid/secret"},
    }
    values.update(overrides)
    return ProwlarrSearchResult(**values)


def test_usenet_parser_tokens_url_and_extracts_release_facts():
    store = CandidateStore(ttl_seconds=100)
    parser = ProwlarrCandidateParser(
        "usenet", candidate_store=store,
        clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp(),
    )

    candidate = parser.parse(_result(), criteria=_criteria())

    assert candidate.source == candidate.protocol == "usenet"
    assert candidate.content_scope == "release_bundle"
    assert candidate.server_ref.startswith("ssc1-")
    assert "://" not in candidate.server_ref
    with candidate_binding(1):
        assert store.resolve(candidate.server_ref).startswith("https://indexer.invalid/")
    assert candidate.facts == {
        "artist": "Björk",
        "release_title": "Debut",
        "edition": "Deluxe Edition",
        "year": None,
        "track_count": None,
        "format": "flac",
        "bit_depth": 24,
        "sample_rate": 96000,
    }
    assert candidate.age_seconds == 3600
    assert candidate.seeders is None


def test_persisted_prowlarr_payload_redacts_every_download_reference():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_acquisition_schema(conn)
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="release_edition",
        entity_id=9,
        quality_profile_id=2,
        trigger="manual",
        idempotency_key="prowlarr-redaction",
    )
    transition_request(conn, request.id, "searching")
    criteria = _criteria(request_id=request.id)
    parser = ProwlarrCandidateParser(
        "usenet", candidate_store=CandidateStore())
    batch = parse_candidate_batch(parser, [_result()], criteria=criteria)

    stored = aggregate_candidates(
        conn, criteria=criteria, batches=[batch]).registrations[0].candidate

    assert "indexer.invalid" not in str(stored.raw_payload)
    assert "secret" not in str(stored.raw_payload)
    assert stored.raw_payload["download_url"] == "[redacted]"
    assert stored.raw_payload["provider"]["downloadUrl"] == "[redacted]"
    conn.close()


def test_prowlarr_parser_never_projects_release_as_one_track():
    candidate = ProwlarrCandidateParser(
        "usenet", candidate_store=CandidateStore()).parse(
            _result(), criteria=_criteria())

    assert candidate.content_scope == "release_bundle"
    assert candidate.facts["track_count"] is None


def test_unknown_size_remains_unknown_and_unsafe_scheme_is_rejected():
    parser = ProwlarrCandidateParser(
        "usenet", candidate_store=CandidateStore())

    assert parser.parse(
        _result(size=0), criteria=_criteria()).size_bytes is None
    with pytest.raises(ValueError, match="unsupported download-reference scheme"):
        parser.parse(
            _result(download_url="file:///etc/passwd"), criteria=_criteria())


def test_protocol_mismatch_is_skipped_and_torrent_prefers_magnet():
    store = CandidateStore()
    usenet = ProwlarrCandidateParser("usenet", candidate_store=store)
    torrent = ProwlarrCandidateParser("torrent", candidate_store=store)
    payload = _result(
        protocol="torrent",
        magnet_uri="magnet:?xt=urn:btih:abc",
        download_url="https://indexer.invalid/file.torrent",
    )

    assert usenet.parse(payload, criteria=_criteria()) is None
    parsed = torrent.parse(payload, criteria=_criteria())
    with candidate_binding(1):
        assert store.resolve(parsed.server_ref).startswith("magnet:")


def test_indexer_filter_normalization_is_deterministic():
    assert parse_indexer_ids("3, 2,invalid,3,0,-1") == (3, 2)
    assert parse_indexer_ids([5, "7", None]) == (5, 7)


def test_adapter_requires_both_prowlarr_and_download_client():
    class Client:
        def __init__(self, configured):
            self.configured = configured

        def is_configured(self):
            return self.configured

    assert ProwlarrAcquisitionAdapter(
        "usenet", client=Client(True),
        download_client_configured=lambda: True,
    ).is_configured() is True
    assert ProwlarrAcquisitionAdapter(
        "usenet", client=Client(True),
        download_client_configured=lambda: False,
    ).is_configured() is False
    assert ProwlarrAcquisitionAdapter(
        "usenet", client=Client(False),
        download_client_configured=lambda: True,
    ).is_configured() is False


def test_adapter_uses_structured_catalog_query_and_indexer_allowlist():
    class Client:
        def __init__(self):
            self.call = None

        def is_configured(self):
            return True

        async def search(self, query, **kwargs):
            self.call = (query, kwargs)
            return []

    client = Client()
    adapter = ProwlarrAcquisitionAdapter(
        "usenet",
        client=client,
        download_client_configured=lambda: True,
        indexer_ids_getter=lambda: "4,8",
    )

    import asyncio
    assert asyncio.run(adapter.search(_criteria())) == []
    assert client.call[0] == "Björk Debut Deluxe Edition"
    assert client.call[1]["indexer_ids"] == (4, 8)
