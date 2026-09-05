"""Shared manual/automatic candidate decisions and persisted reasons."""

from __future__ import annotations

import sqlite3

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import register_candidate
from core.acquisition.eligibility_gate import (
    ENGINE_VERSION,
    CatalogContext,
    EligibilityGate,
    EffectivePolicy,
    RuntimeContext,
)
from core.acquisition.decisions import (
    get_decision_run,
    latest_decision_run,
    public_decision_history,
    record_decision,
)
from core.acquisition.requests import create_request, transition_request
from core.quality.model import QualityTarget
from core.downloads.source_policy import resolve_source_policy


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


def _request(conn, *, scope="release_edition", trigger="manual", key="request"):
    request, _ = create_request(
        conn,
        profile_id=1,
        scope=scope,
        entity_id=9,
        quality_profile_id=2,
        trigger=trigger,
        idempotency_key=key,
    )
    return transition_request(conn, request.id, "searching")


def _candidate(conn, request, **overrides):
    bundle = request.scope != "recording"
    values = {
        "request_id": request.id,
        "source": "usenet" if bundle else "soulseek",
        "protocol": "usenet" if bundle else "p2p",
        "content_scope": "release_bundle" if bundle else "recording",
        "server_ref": "ssc1-token",
        "title": "Artist - Album",
        "indexer": "idx",
        "guid": f"guid-{request.id}",
        "size_bytes": 500,
        "age_seconds": 7200,
        "grabs": 20,
        "facts": {
            "artist": "Artist",
            "release_title": "Album",
            "edition": "Deluxe",
            "format": "flac",
            "bit_depth": 24,
            "sample_rate": 96000,
            "track_count": 10,
            "language": "en",
            "release_type": "album",
            "custom_formats": ["scene"],
        },
        "now": 1000.0,
    }
    values.update(overrides)
    return register_candidate(conn, **values)[0]


def _catalog(**overrides):
    values = {
        "artist": "Artist",
        "release_title": "Album",
        "edition": "Deluxe",
        "track_count": 10,
    }
    values.update(overrides)
    return CatalogContext(**values)


def _policy(**overrides):
    values = {
        "quality_targets": (
            QualityTarget(label="Hi-Res", format="flac", bit_depth=24,
                          min_sample_rate=96000),
            QualityTarget(label="CD", format="flac", bit_depth=16),
        ),
        "fallback_enabled": False,
        "cutoff_index": 0,
        "custom_format_scores": {"scene": 10},
        "protocol_priorities": {"usenet": 1, "p2p": 2},
        "source_priorities": {"usenet": 1, "soulseek": 2},
    }
    values.update(overrides)
    return EffectivePolicy(**values)


def _evaluate(request, candidate, *, catalog=None, runtime=None, policy=None, **kwargs):
    return EligibilityGate.evaluate(
        request,
        candidate,
        catalog or _catalog(),
        runtime or RuntimeContext(free_space_bytes=1000, expected_size_bytes=500),
        policy or _policy(),
        now=1001.0,
        **kwargs,
    )


def test_valid_candidate_is_accepted_with_deterministic_ranking(conn):
    request = _request(conn)
    candidate = _candidate(conn, request)

    decision = _evaluate(request, candidate)

    assert decision.accepted is True
    assert decision.rejections == ()
    assert decision.quality_rank == 0
    assert decision.custom_format_score == 10
    assert decision.edition_match_confidence == 1.0
    assert decision.engine_version == ENGINE_VERSION


def test_recording_request_cannot_accept_release_bundle(conn):
    request = _request(conn, scope="recording")
    candidate = _candidate(
        conn,
        request,
        source="usenet",
        protocol="usenet",
        content_scope="release_bundle",
    )

    decision = _evaluate(request, candidate)

    assert decision.accepted is False
    assert "content_scope_mismatch" in {reason.code for reason in decision.rejections}


def test_request_ownership_and_expiry_are_non_overridable(conn):
    first = _request(conn, key="first")
    second = _request(conn, key="second")
    candidate = _candidate(conn, first)

    mismatch = _evaluate(second, candidate, force=True, is_admin=True)
    expired = EligibilityGate.evaluate(
        first, candidate, _catalog(), RuntimeContext(), _policy(),
        now=candidate.expires_at, force=True, is_admin=True,
    )

    assert mismatch.accepted is False
    assert expired.accepted is False
    assert mismatch.can_force is False
    assert expired.can_force is False


def test_manual_and_automatic_requests_receive_identical_reasons(conn):
    manual = _request(conn, trigger="manual", key="manual")
    automatic = _request(conn, trigger="scheduled", key="auto")
    manual_candidate = _candidate(
        conn, manual, facts={"artist": "Other", "release_title": "Album"})
    auto_candidate = _candidate(
        conn, automatic, facts={"artist": "Other", "release_title": "Album"})

    manual_decision = _evaluate(manual, manual_candidate)
    auto_decision = _evaluate(automatic, auto_candidate)

    assert [reason.code for reason in manual_decision.reasons] == [
        reason.code for reason in auto_decision.reasons]
    assert manual_decision.accepted == auto_decision.accepted is False


def test_quality_cutoff_custom_format_size_and_availability_matrix(conn):
    request = _request(conn)
    candidate = _candidate(
        conn,
        request,
        protocol="usenet",
        facts={
            "artist": "Artist", "release_title": "Album", "edition": "Deluxe",
            "format": "mp3", "bitrate": 128, "track_count": 9,
            "custom_formats": ["banned"],
        },
        size_bytes=50,
        age_seconds=10,
    )
    policy = _policy(
        blocked_custom_formats=frozenset({"banned"}),
        required_custom_formats=frozenset({"scene"}),
        min_size_bytes=100,
        minimum_age_seconds=60,
    )

    decision = _evaluate(request, candidate, policy=policy)
    codes = {reason.code for reason in decision.rejections}

    assert {"quality_not_allowed", "custom_format_blocked",
            "custom_format_required", "size_too_small",
            "usenet_too_new"} <= codes
    assert "track_count_mismatch" in {reason.code for reason in decision.warnings}


def test_force_grab_only_overrides_policy_rejections(conn):
    request = _request(conn)
    candidate = _candidate(
        conn, request,
        facts={"artist": "Artist", "release_title": "Album",
               "edition": "Other", "format": "mp3", "bitrate": 128},
    )
    policy_only = _evaluate(request, candidate, force=True, is_admin=True)
    blocklisted = _evaluate(
        request,
        candidate,
        catalog=_catalog(blocklisted_dedupe_keys=frozenset({candidate.dedupe_key})),
        force=True,
        is_admin=True,
    )

    assert policy_only.accepted is True
    assert policy_only.forced is True
    assert blocklisted.accepted is False
    assert blocklisted.forced is False


def test_runtime_safety_failures_are_not_forceable(conn):
    request = _request(conn)
    candidate = _candidate(conn, request)
    runtime = RuntimeContext(
        client_available=False,
        path_mapping_valid=False,
        staging_available=False,
        free_space_bytes=1,
        active_dedupe_keys=frozenset({candidate.dedupe_key}),
    )

    decision = _evaluate(
        request, candidate, runtime=runtime, force=True, is_admin=True)

    assert decision.accepted is False
    assert {"download_client_unavailable", "path_mapping_invalid",
            "staging_unavailable", "insufficient_free_space",
            "duplicate_active_grab"} <= {
                reason.code for reason in decision.rejections}


def test_torrent_minimum_seeders_is_a_profile_rejection(conn):
    request = _request(conn)
    candidate = _candidate(
        conn,
        request,
        source="torrent",
        protocol="torrent",
        content_scope="release_bundle",
        seeders=1,
    )

    decision = _evaluate(
        request, candidate, policy=_policy(minimum_seeders=5))

    assert "not_enough_seeders" in {
        reason.code for reason in decision.rejections}


def test_quality_fallback_warns_but_remains_acceptable(conn):
    request = _request(conn)
    candidate = _candidate(
        conn,
        request,
        facts={
            "artist": "Artist", "release_title": "Album", "edition": "Deluxe",
            "format": "mp3", "bitrate": 128, "track_count": 10,
        },
    )

    decision = _evaluate(
        request, candidate, policy=_policy(fallback_enabled=True))

    assert decision.accepted is True
    assert "quality_fallback" in {reason.code for reason in decision.warnings}


def test_upgrade_candidate_must_improve_current_quality_rank(conn):
    request = _request(conn, scope="upgrade")
    candidate = _candidate(conn, request)

    decision = _evaluate(
        request,
        candidate,
        runtime=RuntimeContext(current_quality_rank=0, free_space_bytes=1000),
    )

    assert decision.accepted is False
    assert "not_an_upgrade" in {reason.code for reason in decision.rejections}


def test_decision_runs_are_append_only_and_public(conn):
    request = _request(conn)
    candidate = _candidate(conn, request)
    first = record_decision(conn, _evaluate(request, candidate))
    rejected = _evaluate(
        request,
        candidate,
        runtime=RuntimeContext(client_available=False),
    )
    second = record_decision(conn, rejected)

    assert first.id != second.id
    assert get_decision_run(conn, first.id).decision.accepted is True
    assert latest_decision_run(conn, candidate.id).id == second.id
    history = public_decision_history(conn, candidate.id)
    assert [entry["accepted"] for entry in history] == [True, False]
    assert history[1]["engine_version"] == ENGINE_VERSION
    assert "server_ref" not in str(history)


def test_profile_factory_uses_ranked_targets_and_cutoff():
    policy = EffectivePolicy.from_profile({
        "ranked_targets": [
            {"label": "Hi-Res", "format": "flac", "bit_depth": 24},
            {"label": "CD", "format": "flac", "bit_depth": 16},
        ],
        "fallback_enabled": False,
        "upgrade_policy": "until_cutoff",
        "upgrade_cutoff_index": 1,
    })

    assert len(policy.quality_targets) == 2
    assert policy.fallback_enabled is False
    assert policy.cutoff_index == 1


def test_priority_mode_ranks_source_before_quality(conn):
    request = _request(conn)
    preferred = _candidate(
        conn,
        request,
        source="usenet",
        protocol="usenet",
        guid="preferred",
        facts={
            "artist": "Artist", "release_title": "Album", "edition": "Deluxe",
            "format": "flac", "bit_depth": 16, "track_count": 10,
        },
    )
    better_quality = _candidate(
        conn,
        request,
        source="torrent",
        protocol="torrent",
        guid="better-quality",
        server_ref="ssc1-other",
        facts={
            "artist": "Artist", "release_title": "Album", "edition": "Deluxe",
            "format": "flac", "bit_depth": 24, "sample_rate": 96000,
            "track_count": 10,
        },
    )
    source_policy = resolve_source_policy(
        mode="hybrid",
        hybrid_order=["usenet", "torrent"],
        search_mode="priority",
    )
    policy = _policy(
        source_policy=source_policy,
        source_priorities=source_policy.source_priorities,
        protocol_priorities={},
    )

    preferred_decision = _evaluate(request, preferred, policy=policy)
    better_decision = _evaluate(request, better_quality, policy=policy)

    assert preferred_decision.sort_key < better_decision.sort_key


def test_best_quality_mode_ranks_quality_before_source(conn):
    source_policy = resolve_source_policy(
        mode="hybrid",
        hybrid_order=["usenet", "torrent"],
        search_mode="best_quality",
    )
    policy = _policy(
        source_policy=source_policy,
        source_priorities=source_policy.source_priorities,
        protocol_priorities={},
    )
    request = _request(conn)
    preferred = _candidate(
        conn,
        request,
        source="usenet",
        protocol="usenet",
        guid="preferred-best-mode",
        facts={
            "artist": "Artist", "release_title": "Album", "edition": "Deluxe",
            "format": "flac", "bit_depth": 16, "track_count": 10,
        },
    )
    better = _candidate(
        conn,
        request,
        source="torrent",
        protocol="torrent",
        guid="better-best-mode",
        server_ref="ssc1-best-other",
        facts={
            "artist": "Artist", "release_title": "Album", "edition": "Deluxe",
            "format": "flac", "bit_depth": 24, "sample_rate": 96000,
            "track_count": 10,
        },
    )

    assert _evaluate(request, better, policy=policy).sort_key < _evaluate(
        request, preferred, policy=policy).sort_key
