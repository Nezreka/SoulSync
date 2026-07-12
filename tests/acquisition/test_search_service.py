"""Server-owned acquisition search collection without long DB transactions."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.decision_engine import CatalogContext
from core.acquisition.requests import create_request, transition_request
from core.acquisition.search_contract import (
    CandidateParseError,
    ParsedCandidate,
    build_search_criteria,
)
from core.acquisition.search_service import (
    collect_search_results,
    persist_search_results,
)
from core.downloads.source_policy import resolve_source_policy


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


def _criteria(conn, *, scope="release_edition"):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope=scope,
        entity_id=3,
        quality_profile_id=2,
        trigger="manual",
        idempotency_key=f"service-{scope}",
    )
    request = transition_request(conn, request.id, "searching")
    return build_search_criteria(
        request,
        CatalogContext(artist="Artist", release_title="Album", track_count=10),
    )


class _Parser:
    def __init__(self, source):
        self.source = source

    def parse(self, payload, *, criteria):
        if payload == "bad":
            raise CandidateParseError("malformed provider row")
        return ParsedCandidate(
            source=self.source,
            protocol="usenet" if self.source == "usenet" else "torrent",
            content_scope="release_bundle",
            server_ref=f"ssc1-{payload['guid']}",
            title=payload["title"],
            guid=payload["guid"],
            facts={
                "artist": criteria.artist,
                "release_title": criteria.release_title,
                "track_count": criteria.track_count,
            },
        )


class _Adapter:
    def __init__(self, source, payloads=(), *, configured=True, error=None):
        self.source = source
        self.parser = _Parser(source)
        self.payloads = payloads
        self.configured = configured
        self.error = error
        self.calls = 0

    def is_configured(self):
        return self.configured

    async def search(self, criteria):
        self.calls += 1
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        return self.payloads


def test_collection_selects_sources_by_explicit_capability(conn):
    criteria = _criteria(conn)
    usenet = _Adapter("usenet", [{"guid": "u1", "title": "Artist - Album"}])
    torrent = _Adapter("torrent", configured=False)
    soulseek = _Adapter("soulseek")

    collection = asyncio.run(collect_search_results(
        criteria, [usenet, torrent, soulseek]))

    assert [(item.source, item.status) for item in collection.outcomes] == [
        ("usenet", "searched"),
        ("torrent", "unconfigured"),
        ("soulseek", "unsupported"),
    ]
    assert usenet.calls == 1
    assert torrent.calls == soulseek.calls == 0
    assert collection.candidate_count == 1


def test_source_failures_are_isolated_and_redacted(conn):
    criteria = _criteria(conn)
    failed = _Adapter(
        "usenet", error=RuntimeError(
            "GET https://indexer.invalid/api?api_key=secret failed"))
    healthy = _Adapter(
        "torrent", [{"guid": "t1", "title": "Artist - Album"}])

    collection = asyncio.run(collect_search_results(
        criteria, [failed, healthy]))

    assert collection.outcomes[0].status == "failed"
    assert collection.outcomes[0].error == "GET [redacted] failed"
    assert "secret" not in str(collection.to_public_dict())
    assert collection.outcomes[1].status == "searched"
    assert collection.candidate_count == 1


def test_parse_failures_do_not_discard_valid_source_results(conn):
    criteria = _criteria(conn)
    adapter = _Adapter("usenet", [
        "bad",
        {"guid": "u1", "title": "Artist - Album"},
    ])

    collection = asyncio.run(collect_search_results(criteria, [adapter]))
    outcome = collection.outcomes[0]

    assert outcome.candidate_count == 1
    assert outcome.batch.failures[0].position == 0
    assert outcome.to_public_dict()["parse_failures"][0]["error"] == (
        "malformed provider row")


def test_persistence_happens_after_collection_and_is_idempotent(conn):
    criteria = _criteria(conn)
    adapter = _Adapter(
        "usenet", [{"guid": "u1", "title": "Artist - Album"}])
    collection = asyncio.run(collect_search_results(criteria, [adapter]))

    first = persist_search_results(conn, collection, now=1000, ttl_seconds=100)
    second = persist_search_results(conn, collection, now=1010, ttl_seconds=100)

    assert first.created_count == 1
    assert second.refreshed_count == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM release_candidates").fetchone()[0] == 1


def test_duplicate_or_mismatched_adapters_are_rejected(conn):
    criteria = _criteria(conn)
    duplicate = [_Adapter("usenet"), _Adapter("usenet")]

    with pytest.raises(ValueError, match="duplicate"):
        asyncio.run(collect_search_results(criteria, duplicate))

    mismatch = _Adapter("usenet")
    mismatch.parser = _Parser("torrent")
    with pytest.raises(ValueError, match="uses parser"):
        asyncio.run(collect_search_results(criteria, [mismatch]))


def test_timeout_is_reported_per_source(conn):
    criteria = _criteria(conn)

    class SlowAdapter(_Adapter):
        async def search(self, criteria):
            self.calls += 1
            await asyncio.sleep(0.05)
            return []

    collection = asyncio.run(collect_search_results(
        criteria, [SlowAdapter("usenet")], timeout_seconds=0.001))

    assert collection.outcomes[0].status == "failed"
    assert collection.outcomes[0].error == "Source search timed out"


def test_priority_policy_searches_sources_in_configured_order(conn):
    criteria = _criteria(conn)
    calls = []

    class OrderedAdapter(_Adapter):
        async def search(self, criteria):
            calls.append(self.source)
            return self.payloads

    usenet = OrderedAdapter(
        "usenet", [{"guid": "u1", "title": "Artist - Album"}])
    torrent = OrderedAdapter(
        "torrent", [{"guid": "t1", "title": "Artist - Album"}])
    policy = resolve_source_policy(
        mode="hybrid",
        hybrid_order=["torrent", "usenet"],
        search_mode="priority",
    )

    collection = asyncio.run(collect_search_results(
        criteria, [usenet, torrent], source_policy=policy))

    assert calls == ["torrent", "usenet"]
    assert [item.source for item in collection.outcomes] == ["torrent", "usenet"]


def test_single_source_policy_does_not_call_other_adapter(conn):
    criteria = _criteria(conn)
    usenet = _Adapter("usenet", [{"guid": "u1", "title": "Artist - Album"}])
    torrent = _Adapter("torrent", [{"guid": "t1", "title": "Artist - Album"}])
    policy = resolve_source_policy(mode="usenet")

    collection = asyncio.run(collect_search_results(
        criteria, [torrent, usenet], source_policy=policy))

    assert usenet.calls == 1
    assert torrent.calls == 0
    assert [(item.source, item.status) for item in collection.outcomes] == [
        ("usenet", "searched"),
        ("torrent", "unsupported"),
    ]
