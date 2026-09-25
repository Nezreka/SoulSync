"""Append-only acquisition history and exact candidate blocklisting."""

from __future__ import annotations

import sqlite3

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.blocklist import (
    active_blocklisted_dedupe_keys,
    block_candidate,
    list_blocklist_entries,
    unblock_candidate,
)
from core.acquisition.candidates import register_candidate
from core.acquisition.history import list_history_events, record_history_event
from core.acquisition.requests import create_request, transition_request


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


def _request(conn):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="release_edition",
        entity_id=4,
        quality_profile_id=2,
        trigger="manual",
        idempotency_key="history-request",
    )
    return transition_request(conn, request.id, "searching")


def _candidate(conn, request, *, guid="guid-1", indexer="indexer-a"):
    return register_candidate(
        conn,
        request_id=request.id,
        source="usenet",
        protocol="usenet",
        content_scope="release_bundle",
        server_ref=f"ssc1-{guid}-{indexer}",
        title="Artist - Album",
        indexer=indexer,
        guid=guid,
        facts={"artist": "Artist", "release_title": "Album"},
    )[0]


def test_history_is_append_only_and_redacts_payloads(conn):
    request = _request(conn)
    event = record_history_event(
        conn,
        "search_completed",
        request_id=request.id,
        payload={
            "download_url": "https://indexer.invalid/get?api_key=secret",
            "nested": {"token": "secret", "safe": "value"},
            "tuple": ("safe", "https://indexer.invalid/tuple-secret"),
        },
        message="GET https://indexer.invalid/message?token=secret failed",
    )

    public = event.to_public_dict()
    assert public["payload"]["download_url"] == "[redacted]"
    assert public["payload"]["nested"] == {
        "token": "[redacted]", "safe": "value"}
    assert public["payload"]["tuple"] == ["safe", "[redacted]"]
    assert public["message"] == "GET [redacted] failed"
    assert "indexer.invalid" not in str(public)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE acquisition_history SET event_type='search_failed' WHERE id=?",
            (event.id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM acquisition_history WHERE id=?", (event.id,))


def test_blocklist_is_exact_and_idempotent_not_title_based(conn):
    request = _request(conn)
    first = _candidate(conn, request, guid="guid-1", indexer="indexer-a")
    second = _candidate(conn, request, guid="guid-2", indexer="indexer-a")
    third = _candidate(conn, request, guid="guid-1", indexer="indexer-b")

    blocked, created = block_candidate(
        conn, first.id, reason_code="client_failed", message="Bad archive")
    same, created_again = block_candidate(
        conn, first.id, reason_code="client_failed")

    assert created is True and created_again is False
    assert same.id == blocked.id
    assert active_blocklisted_dedupe_keys(conn) == frozenset({first.dedupe_key})
    assert second.dedupe_key not in active_blocklisted_dedupe_keys(conn)
    assert third.dedupe_key not in active_blocklisted_dedupe_keys(conn)
    assert len(list_blocklist_entries(conn)) == 1
    events = list_history_events(conn, candidate_id=first.id)
    assert [event.event_type for event in events] == ["candidate_blocklisted"]


def test_expired_block_does_not_reject_candidate(conn):
    request = _request(conn)
    candidate = _candidate(conn, request)
    entry, _ = block_candidate(
        conn,
        candidate.id,
        reason_code="temporary_failure",
        expires_at=1100,
        now=1000,
    )

    assert candidate.dedupe_key in active_blocklisted_dedupe_keys(conn, now=1099)
    assert candidate.dedupe_key not in active_blocklisted_dedupe_keys(conn, now=1100)
    assert list_blocklist_entries(conn, now=1100) == ()
    assert list_blocklist_entries(conn, active_only=False)[0].id == entry.id

    replacement, created = block_candidate(
        conn,
        candidate.id,
        reason_code="failed_again",
        message="Bad https://indexer.invalid/get?api_key=secret",
        now=1100,
    )
    assert created is True and replacement.id != entry.id
    assert replacement.message == "Bad [redacted]"


def test_unblock_is_idempotent_and_allows_new_block_event(conn):
    request = _request(conn)
    candidate = _candidate(conn, request)
    first, _ = block_candidate(
        conn, candidate.id, reason_code="client_failed")

    removed, changed = unblock_candidate(conn, first.id, message="Reviewed")
    unchanged, changed_again = unblock_candidate(conn, first.id)
    second, created = block_candidate(
        conn, candidate.id, reason_code="client_failed_again")

    assert changed is True and changed_again is False
    assert removed.active is unchanged.active is False
    assert created is True and second.id != first.id
    assert [event.event_type for event in list_history_events(
        conn, candidate_id=candidate.id)] == [
            "candidate_blocklisted",
            "candidate_unblocked",
            "candidate_blocklisted",
        ]


def test_history_and_blocklist_are_admin_only(conn):
    request = _request(conn)
    candidate = _candidate(conn, request)

    with pytest.raises(ValueError, match="admin-profile only"):
        record_history_event(
            conn, "search_started", request_id=request.id, actor_profile_id=2)
    with pytest.raises(ValueError, match="admin-profile only"):
        block_candidate(
            conn, candidate.id, reason_code="failed", actor_profile_id=2)
