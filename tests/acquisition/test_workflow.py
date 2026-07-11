"""End-to-end durable acquisition workflow without a live download client."""

from __future__ import annotations

import sqlite3

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import register_candidate
from core.acquisition.decision_engine import (
    CatalogContext,
    EffectivePolicy,
    RuntimeContext,
)
from core.acquisition.decisions import public_decision_history
from core.acquisition.grabs import get_grab
from core.acquisition.requests import create_request, get_request, transition_request
from core.acquisition.workflow import (
    evaluate_request_candidates,
    prepare_candidate_grab,
    record_grab_outcome,
)
from core.quality.model import QualityTarget


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


def _request(conn, key="workflow"):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope="release_edition",
        entity_id=10,
        quality_profile_id=2,
        trigger="manual",
        idempotency_key=key,
    )
    return transition_request(conn, request.id, "searching")


def _candidate(conn, request, *, guid, title="Artist - Album", fmt="flac",
               bit_depth=24, sample_rate=96000, grabs=0, artist="Artist"):
    return register_candidate(
        conn,
        request_id=request.id,
        source="usenet",
        protocol="usenet",
        content_scope="release_bundle",
        server_ref=f"ssc1-{guid}",
        title=title,
        indexer="idx",
        guid=guid,
        size_bytes=500,
        age_seconds=3600,
        grabs=grabs,
        facts={
            "artist": artist,
            "release_title": "Album",
            "edition": "Deluxe",
            "format": fmt,
            "bit_depth": bit_depth,
            "sample_rate": sample_rate,
            "track_count": 10,
        },
        now=1000.0,
    )[0]


CATALOG = CatalogContext(
    artist="Artist", release_title="Album", edition="Deluxe", track_count=10)
RUNTIME = RuntimeContext(free_space_bytes=1000)
POLICY = EffectivePolicy(
    quality_targets=(
        QualityTarget(label="Hi-Res", format="flac", bit_depth=24,
                      min_sample_rate=96000),
        QualityTarget(label="CD", format="flac", bit_depth=16),
    ),
    fallback_enabled=False,
)


def test_manual_search_returns_rejected_candidates_with_reasons(conn):
    request = _request(conn)
    good = _candidate(conn, request, guid="good", grabs=10)
    bad = _candidate(conn, request, guid="bad", artist="Other")

    result = evaluate_request_candidates(
        conn, request.id, catalog=CATALOG, runtime=RUNTIME,
        policy=POLICY, automatic=False, now=1001.0)
    public = result.to_public_dict()

    assert result.request.status == "candidates_ready"
    assert result.selected is None
    assert {item.candidate.id for item in result.candidates} == {good.id, bad.id}
    rejected = next(
        item for item in public["candidates"] if item["id"] == bad.id)
    assert rejected["decision"]["accepted"] is False
    assert rejected["decision"]["rejections"][0]["code"] == "artist_mismatch"
    assert "server_ref" not in str(public)


def test_automatic_search_selects_best_accepted_server_decision(conn):
    request = _request(conn)
    lower = _candidate(
        conn, request, guid="cd", bit_depth=16, sample_rate=44100, grabs=100)
    better = _candidate(
        conn, request, guid="hires", bit_depth=24, sample_rate=96000, grabs=1)
    _candidate(conn, request, guid="wrong", artist="Other", grabs=1000)

    result = evaluate_request_candidates(
        conn, request.id, catalog=CATALOG, runtime=RUNTIME,
        policy=POLICY, automatic=True, now=1001.0)

    assert result.selected.candidate.id == better.id
    assert result.selected.candidate.id != lower.id
    assert result.request.status == "candidates_ready"


def test_automatic_search_with_only_rejections_becomes_no_candidate(conn):
    request = _request(conn)
    rejected = _candidate(conn, request, guid="wrong", artist="Other")

    result = evaluate_request_candidates(
        conn, request.id, catalog=CATALOG, runtime=RUNTIME,
        policy=POLICY, automatic=True, now=1001.0)

    assert result.selected is None
    assert result.request.status == "no_candidate"
    assert public_decision_history(conn, rejected.id)[0]["accepted"] is False


def test_prepare_grab_rechecks_and_links_full_correlation(conn):
    request = _request(conn)
    candidate = _candidate(conn, request, guid="selected")
    evaluate_request_candidates(
        conn, request.id, catalog=CATALOG, runtime=RUNTIME,
        policy=POLICY, automatic=False, now=1001.0)

    prepared = prepare_candidate_grab(
        conn,
        request.id,
        candidate.id,
        download_id="download-1",
        catalog=CATALOG,
        runtime=RUNTIME,
        policy=POLICY,
        now=1002.0,
    )

    grab = get_grab(conn, "download-1")
    assert prepared.server_ref == "ssc1-selected"
    assert "server_ref" not in prepared.to_public_dict()
    assert grab["acquisition_request_id"] == request.id
    assert grab["release_candidate_id"] == candidate.id
    assert grab["decision_run_id"] == prepared.decision_run.id
    assert grab["context"]["quality_profile_id"] == 2
    assert get_request(conn, request.id).status == "grabbing"
    assert len(public_decision_history(conn, candidate.id)) == 2


def test_prepare_grab_is_idempotent_per_download_id(conn):
    request = _request(conn)
    candidate = _candidate(conn, request, guid="selected")
    evaluate_request_candidates(
        conn, request.id, catalog=CATALOG, runtime=RUNTIME,
        policy=POLICY, automatic=False, now=1001.0)
    first = prepare_candidate_grab(
        conn, request.id, candidate.id, download_id="download-1",
        catalog=CATALOG, runtime=RUNTIME, policy=POLICY, now=1002.0)

    second = prepare_candidate_grab(
        conn, request.id, candidate.id, download_id="download-1",
        catalog=CATALOG, runtime=RUNTIME, policy=POLICY, now=1003.0)

    assert second.decision_run.id == first.decision_run.id
    assert conn.execute(
        "SELECT COUNT(*) FROM acquisition_grabs WHERE download_id='download-1'"
    ).fetchone()[0] == 1


def test_prepare_grab_rejects_changed_runtime_before_submit(conn):
    request = _request(conn)
    candidate = _candidate(conn, request, guid="selected")
    evaluate_request_candidates(
        conn, request.id, catalog=CATALOG, runtime=RUNTIME,
        policy=POLICY, automatic=False, now=1001.0)

    with pytest.raises(ValueError, match="Decision Engine"):
        prepare_candidate_grab(
            conn, request.id, candidate.id, download_id="download-1",
            catalog=CATALOG,
            runtime=RuntimeContext(client_available=False),
            policy=POLICY,
            now=1002.0,
        )

    assert get_request(conn, request.id).status == "candidates_ready"
    assert get_grab(conn, "download-1") is None


@pytest.mark.parametrize("completed", [True, False])
def test_terminal_grab_outcome_updates_owning_request(conn, completed):
    request = _request(conn, key=f"outcome-{completed}")
    candidate = _candidate(conn, request, guid=f"selected-{completed}")
    evaluate_request_candidates(
        conn, request.id, catalog=CATALOG, runtime=RUNTIME,
        policy=POLICY, automatic=False, now=1001.0)
    prepare_candidate_grab(
        conn, request.id, candidate.id, download_id=f"download-{completed}",
        catalog=CATALOG, runtime=RUNTIME, policy=POLICY, now=1002.0)

    updated = record_grab_outcome(
        conn,
        f"download-{completed}",
        completed=completed,
        error=None if completed else "client failed",
        output_path="/done" if completed else None,
    )

    assert updated.status == ("completed" if completed else "failed")
    grab = get_grab(conn, f"download-{completed}")
    assert grab["status"] == ("completed" if completed else "failed")
