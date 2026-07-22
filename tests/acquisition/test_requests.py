"""Durable acquisition request identity and lifecycle invariants."""

from __future__ import annotations

import sqlite3

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.requests import (
    REQUEST_ID_PREFIX,
    IdempotencyConflict,
    InvalidRequestTransition,
    create_request,
    get_request,
    transition_request,
)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


def _create(conn, **overrides):
    values = {
        "profile_id": 1,
        "scope": "release_edition",
        "entity_id": 42,
        "quality_profile_id": 2,
        "trigger": "manual",
        "idempotency_key": "manual:edition:42:click-1",
        "search_options": {"protocols": ["usenet", "torrent"]},
    }
    values.update(overrides)
    return create_request(conn, **values)


def test_create_roundtrip_uses_opaque_id_and_typed_options(conn):
    request, created = _create(conn)

    assert created is True
    assert request.id.startswith(REQUEST_ID_PREFIX)
    assert request.scope == "release_edition"
    assert request.entity_id == 42
    assert request.status == "pending"
    assert request.search_options == {"protocols": ["usenet", "torrent"]}
    assert get_request(conn, request.id) == request


def test_identical_idempotent_create_returns_same_request(conn):
    first, first_created = _create(conn)
    second, second_created = _create(conn)

    assert first_created is True
    assert second_created is False
    assert second == first
    assert conn.execute("SELECT COUNT(*) FROM acquisition_requests").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scope", "recording"),
        ("entity_id", 99),
        ("quality_profile_id", 3),
        ("trigger", "scheduled"),
        ("search_options", {"protocols": ["torrent"]}),
        ("force_options", {"allow_below_cutoff": True}),
    ],
)
def test_idempotency_key_reuse_with_changed_semantics_is_rejected(conn, field, value):
    _create(conn)

    with pytest.raises(IdempotencyConflict):
        _create(conn, **{field: value})


def test_lifecycle_compare_and_set_and_retry_attempts(conn):
    request, _ = _create(conn)

    searching = transition_request(
        conn, request.id, "searching", expected_status="pending",
        increment_attempts=True,
    )
    assert searching.attempts == 1
    ready = transition_request(conn, request.id, "candidates_ready")
    grabbing = transition_request(conn, request.id, "grabbing")
    completed = transition_request(conn, request.id, "completed")

    assert ready.status == "candidates_ready"
    assert grabbing.status == "grabbing"
    assert completed.status == "completed"
    assert completed.completed_at is not None
    with pytest.raises(InvalidRequestTransition):
        transition_request(conn, request.id, "searching")


def test_no_candidate_can_retry_but_invalid_jump_is_rejected(conn):
    request, _ = _create(conn)
    transition_request(conn, request.id, "searching")
    empty = transition_request(
        conn, request.id, "no_candidate", error="nothing acceptable",
        next_retry_at="2030-01-01 00:00:00",
    )
    assert empty.last_error == "nothing acceptable"
    assert empty.next_retry_at == "2030-01-01 00:00:00"
    retried = transition_request(
        conn, request.id, "searching", increment_attempts=True)
    assert retried.attempts == 1

    other, _ = _create(conn, idempotency_key="other")
    with pytest.raises(InvalidRequestTransition):
        transition_request(conn, other.id, "completed")


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile_id": 0},
        {"profile_id": 2},
        {"entity_id": -1},
        {"quality_profile_id": None},
        {"scope": "track"},
        {"trigger": "browser"},
        {"idempotency_key": ""},
        {"search_options": {"bad": float("nan")}},
    ],
)
def test_invalid_request_contract_is_rejected(conn, overrides):
    with pytest.raises(ValueError):
        _create(conn, **overrides)
