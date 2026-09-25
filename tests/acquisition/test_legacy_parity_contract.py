"""F08 contract matrix between the legacy worker and Acquisition/Library v2.

These tests compare normalized business outcomes across the two entry paths.
They deliberately call the production policy, ordering, quality, upgrade and
retry seams instead of maintaining a second test-only implementation.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

import core.downloads.monitor as monitor
import core.imports.file_ops as file_ops
import core.imports.guards as import_guards
import core.quality.selection as quality_selection
from core.acquisition import ensure_acquisition_schema
from core.acquisition.candidates import register_candidate
from core.acquisition.eligibility_gate import (
    CatalogContext,
    EffectivePolicy,
    RuntimeContext,
)
from core.acquisition.imports import (
    get_import,
    record_pipeline_file_completed,
    record_pipeline_file_quarantined,
)
from core.acquisition.requests import create_request, get_request, transition_request
from core.acquisition.retry_resume import resume_interrupted_retry_walks
from core.acquisition.retry_state import get_retry_state
from core.acquisition.workflow import evaluate_request_candidates
from core.downloads.candidates import order_candidates
from core.downloads.source_policy import resolve_source_policy
from core.library2.quality_eval import evaluate_file, profile_targets
from core.quality.model import AudioQuality, QualityTarget, rank_candidate
from core.runtime_state import download_tasks
from tests.acquisition.test_pipeline_callback import _importing_record
from tests.acquisition.test_retry_resume import _seed_walk


TARGETS = (
    QualityTarget(
        label="Hi-Res",
        format="flac",
        bit_depth=24,
        min_sample_rate=96000,
    ),
    QualityTarget(label="CD", format="flac", bit_depth=16),
)


@dataclass(frozen=True)
class _LegacyCandidate:
    key: str
    source: str
    audio_quality: AudioQuality
    confidence: float = 1.0
    quality_score: float = 0.0
    upload_speed: int = 0
    queue_length: int = 0
    free_upload_slots: int = 1
    size: int = 100


def _connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(conn)
    return conn


def _source_candidates():
    return (
        _LegacyCandidate(
            "usenet-cd",
            "usenet",
            AudioQuality(format="flac", bit_depth=16, sample_rate=44100),
        ),
        _LegacyCandidate(
            "torrent-hires",
            "torrent",
            AudioQuality(format="flac", bit_depth=24, sample_rate=96000),
        ),
    )


def _legacy_candidate_order(candidates, source_policy):
    if source_policy.search_all_sources:
        return order_candidates(
            candidates,
            quality_first=True,
            targets=TARGETS,
        )
    ordered = []
    for source in source_policy.source_chain:
        ordered.extend(order_candidates(
            [item for item in candidates if item.source == source],
            quality_first=source_policy.quality_first,
            targets=TARGETS,
        ))
    return ordered


def _acquisition_candidate_order(conn, candidates, source_policy):
    request, _created = create_request(
        conn,
        profile_id=1,
        scope="release_edition",
        entity_id=1,
        quality_profile_id=2,
        trigger="monitor",
        idempotency_key=f"parity-{source_policy.search_mode}",
    )
    request = transition_request(conn, request.id, "searching")
    keys_by_id = {}
    for item in candidates:
        aq = item.audio_quality
        candidate, _created = register_candidate(
            conn,
            request_id=request.id,
            source=item.source,
            protocol=item.source,
            content_scope="release_bundle",
            server_ref=f"ssc1-{item.key}",
            title="Artist - Album",
            indexer="contract",
            guid=item.key,
            grabs=1 if item.source == "usenet" else None,
            seeders=1 if item.source == "torrent" else None,
            facts={
                "artist": "Artist",
                "release_title": "Album",
                "edition": "Deluxe",
                "track_count": 10,
                "format": aq.format,
                "bit_depth": aq.bit_depth,
                "sample_rate": aq.sample_rate,
            },
            now=1000.0,
        )
        keys_by_id[candidate.id] = item.key
    policy = EffectivePolicy(
        quality_targets=TARGETS,
        fallback_enabled=False,
        source_policy=source_policy,
        source_priorities=source_policy.source_priorities,
    )
    result = evaluate_request_candidates(
        conn,
        request.id,
        catalog=CatalogContext(
            artist="Artist",
            release_title="Album",
            edition="Deluxe",
            track_count=10,
        ),
        runtime=RuntimeContext(),
        policy=policy,
        automatic=True,
        now=1001.0,
    )
    return (
        [keys_by_id[item.candidate.id] for item in result.candidates],
        keys_by_id[result.selected.candidate.id],
    )


@pytest.mark.parametrize(
    ("search_mode", "expected_order"),
    [
        ("priority", ["usenet-cd", "torrent-hires"]),
        ("best_quality", ["torrent-hires", "usenet-cd"]),
    ],
)
def test_source_selection_and_candidate_order_match_legacy(
    search_mode,
    expected_order,
):
    source_policy = resolve_source_policy(
        mode="hybrid",
        hybrid_order=["usenet", "torrent"],
        search_mode=search_mode,
    )
    candidates = _source_candidates()
    legacy_order = [
        item.key for item in _legacy_candidate_order(candidates, source_policy)
    ]
    conn = _connection()
    try:
        acquisition_order, selected = _acquisition_candidate_order(
            conn, candidates, source_policy)
    finally:
        conn.close()

    assert legacy_order == expected_order
    assert acquisition_order == legacy_order
    assert selected == legacy_order[0]


@pytest.mark.parametrize(
    ("audio_quality", "rejected"),
    [
        (AudioQuality(format="flac", bit_depth=24, sample_rate=96000), False),
        (AudioQuality(format="flac", bit_depth=16, sample_rate=44100), True),
    ],
)
def test_quality_rejection_matches_shared_import_gate(
    monkeypatch,
    audio_quality,
    rejected,
):
    profile = {
        "ranked_targets": [TARGETS[0].to_dict()],
        "fallback_enabled": False,
    }
    monkeypatch.setattr(file_ops, "probe_audio_quality", lambda _path: audio_quality)
    monkeypatch.setattr(
        quality_selection,
        "load_profile_by_id",
        lambda _profile_id=None: profile,
    )
    legacy_rejected = import_guards.check_quality_target(
        "/contract/audio.flac",
        {"track_info": {"quality_profile_id": 2}},
    ) is not None

    conn = _connection()
    request, _created = create_request(
        conn,
        profile_id=1,
        scope="release_edition",
        entity_id=1,
        quality_profile_id=2,
        trigger="monitor",
        idempotency_key=f"quality-{audio_quality.bit_depth}",
    )
    request = transition_request(conn, request.id, "searching")
    candidate, _created = register_candidate(
        conn,
        request_id=request.id,
        source="usenet",
        protocol="usenet",
        content_scope="release_bundle",
        server_ref=f"ssc1-quality-{audio_quality.bit_depth}",
        title="Artist - Album",
        indexer="contract",
        guid=f"quality-{audio_quality.bit_depth}",
        facts={
            "artist": "Artist",
            "release_title": "Album",
            "edition": "Deluxe",
            "track_count": 10,
            "format": audio_quality.format,
            "bit_depth": audio_quality.bit_depth,
            "sample_rate": audio_quality.sample_rate,
        },
        now=1000.0,
    )
    result = evaluate_request_candidates(
        conn,
        request.id,
        catalog=CatalogContext(
            artist="Artist",
            release_title="Album",
            edition="Deluxe",
            track_count=10,
        ),
        runtime=RuntimeContext(),
        policy=EffectivePolicy.from_profile(profile),
        automatic=False,
        now=1001.0,
    )
    acquisition_rejected = not result.candidates[0].decision_run.decision.accepted
    rejection_codes = {
        reason.code
        for reason in result.candidates[0].decision_run.decision.rejections
    }
    conn.close()

    assert legacy_rejected is rejected
    assert acquisition_rejected == legacy_rejected
    assert ("quality_not_allowed" in rejection_codes) is rejected


def _legacy_upgrade_cutoff_index(profile, targets, settings):
    """Frozen copy of the retired quality_upgrade job's cutoff resolution —
    kept here as the parity oracle for the native evaluator's semantics."""
    policy = profile.get("upgrade_policy")
    if policy == "until_top":
        policy = "until_cutoff"
    if policy in (None, "acceptable") and settings.get("require_top_target"):
        policy = "until_cutoff"
    if policy != "until_cutoff" or not targets:
        return None
    try:
        idx = int(profile.get("upgrade_cutoff_index") or 0)
    except (TypeError, ValueError):
        idx = 0
    return max(0, min(idx, len(targets) - 1))


@pytest.mark.parametrize(
    ("policy", "cutoff", "file_row"),
    [
        (
            "acceptable",
            0,
            {"format": "flac", "bit_depth": 16, "sample_rate": 44100},
        ),
        (
            "until_cutoff",
            1,
            {"format": "flac", "bit_depth": 16, "sample_rate": 44100},
        ),
        (
            "until_cutoff",
            0,
            {"format": "flac", "bit_depth": 16, "sample_rate": 44100},
        ),
        (
            "until_top",
            1,
            {"format": "flac", "bit_depth": 24, "sample_rate": 96000},
        ),
    ],
)
def test_upgrade_policy_matches_legacy_quality_job(policy, cutoff, file_row):
    profile = {
        "ranked_targets": [target.to_dict() for target in TARGETS],
        "upgrade_policy": policy,
        "upgrade_cutoff_index": cutoff,
    }
    targets, resolved_policy, resolved_cutoff = profile_targets(profile)
    library_result = evaluate_file(
        file_row,
        targets,
        resolved_policy,
        resolved_cutoff,
    )
    legacy_cutoff = _legacy_upgrade_cutoff_index(profile, targets, {})
    quality = AudioQuality(
        format=file_row["format"],
        bit_depth=file_row.get("bit_depth"),
        sample_rate=file_row.get("sample_rate"),
    )
    rank, _score = rank_candidate(quality, targets)
    legacy_upgrade = (
        rank >= len(targets)
        or (legacy_cutoff is not None and rank > legacy_cutoff)
    )

    assert library_result["upgrade_candidate"] == legacy_upgrade


def _retry_task(*, acquisition=False):
    track_info = {"name": "Track"}
    if acquisition:
        track_info.update({
            "_acquisition_import_id": "aim1-contract",
            "_acquisition_track_id": 7,
        })
    return {
        "status": "post_processing",
        "track_info": track_info,
        "username": "peer",
        "filename": "bad.flac",
        "used_sources": {"older_old.flac"},
        "cached_candidates": [
            {"username": "next", "filename": "good.flac", "confidence": 0.8},
        ],
        "query_count": 2,
    }


def _retry_outcome(task):
    return {
        "status": task["status"],
        "used_sources": set(task["used_sources"]),
        "retry_count": task["quarantine_retry_count"],
        "retry_info": task["retry_info"],
        "retry_trigger": task["retry_trigger"],
        "cached_candidates": list(task["cached_candidates"]),
        "quarantine_retry": task["_quarantine_retry"],
        "has_download_identity": any(
            key in task for key in ("download_id", "username", "filename")
        ),
    }


def test_next_candidate_retry_state_matches_legacy(monkeypatch):
    original = dict(download_tasks)
    download_tasks.clear()
    monkeypatch.setattr(
        monitor.config_manager,
        "get",
        lambda key, default=None: (
            False if key == "post_processing.retry_exhaustive" else default
        ),
    )
    try:
        download_tasks["legacy"] = _retry_task()
        legacy_queued, legacy_attempt, legacy_journal = (
            monitor._requeue_decide_and_mark("legacy", "quality")
        )
        legacy_outcome = _retry_outcome(download_tasks["legacy"])

        download_tasks["acquisition"] = _retry_task(acquisition=True)
        acq_queued, acq_attempt, acq_journal = (
            monitor._requeue_decide_and_mark("acquisition", "quality")
        )
        acq_outcome = _retry_outcome(download_tasks["acquisition"])
    finally:
        download_tasks.clear()
        download_tasks.update(original)

    assert (acq_queued, acq_attempt) == (legacy_queued, legacy_attempt)
    assert acq_outcome == legacy_outcome
    assert legacy_journal is None
    assert acq_journal[0] == "snapshot"
    assert acq_journal[1]["used_sources"] == acq_outcome["used_sources"]
    assert acq_journal[1]["retry_count"] == acq_outcome["retry_count"]


def test_restart_resume_rebuilds_the_same_retry_contract(tmp_path):
    original = dict(download_tasks)
    download_tasks.clear()
    try:
        factory, _importing, _request, task_id = _seed_walk(tmp_path)
        conn = factory()
        state = get_retry_state(conn, task_id)
        conn.close()

        submitted = []
        assert resume_interrupted_retry_walks(
            factory, submit=submitted.append) == (task_id,)
        task = download_tasks[task_id]

        assert submitted == [task_id]
        assert task["used_sources"] == set(state.used_sources)
        assert task["exhausted_download_sources"] == set(state.exhausted_sources)
        assert task["quarantine_retry_counts_by_source"] == state.retry_counts
        assert task["quarantine_retry_count"] == state.retry_count
        assert task["query_count"] == state.query_count
        assert [
            (item.username, item.filename, item.confidence)
            for item in task["cached_candidates"]
        ] == [
            (
                item.get("username"),
                item.get("filename"),
                item.get("confidence"),
            )
            for item in state.candidates
        ]
    finally:
        download_tasks.clear()
        download_tasks.update(original)


def _normalized_legacy_state(task):
    if task.get("status") == "completed":
        return "completed"
    if task.get("quarantine_entry_id"):
        return "quarantined"
    return task.get("status")


def _normalized_acquisition_state(record, *, relative_path="01.flac", track_id=101):
    if any(
        str(item.get("relative_path") or "") == relative_path
        and int(item.get("track_id") or 0) == track_id
        for item in record.result.get("processed", [])
    ):
        return "completed"
    if any(
        str(item.get("relative_path") or "") == relative_path
        and int(item.get("track_id") or 0) == track_id
        for item in record.result.get("quarantined", [])
    ):
        return "quarantined"
    return record.status


def test_quarantine_and_terminal_state_follow_the_shared_pipeline():
    conn = _connection()
    importing, request = _importing_record(conn)
    legacy_task = {
        "status": "failed",
        "quarantine_entry_id": "q-1",
    }

    quarantined = record_pipeline_file_quarantined(
        conn,
        importing.id,
        relative_path="01.flac",
        track_id=101,
        trigger="quality",
        reason="Below profile",
    )
    assert _normalized_acquisition_state(quarantined) == (
        _normalized_legacy_state(legacy_task)
    ) == "quarantined"
    assert get_request(conn, request.id).status == "grabbing"

    legacy_task.update(status="completed")
    legacy_task.pop("quarantine_entry_id")
    completed = record_pipeline_file_completed(
        conn,
        importing.id,
        relative_path="01.flac",
        final_path="/library/01.flac",
        track_id=101,
    )
    assert _normalized_acquisition_state(completed) == (
        _normalized_legacy_state(legacy_task)
    ) == "completed"
    # The second planned track is still open, so the owning request is not
    # terminal until the same shared pipeline reports its success too.
    assert get_request(conn, request.id).status == "grabbing"
    final = record_pipeline_file_completed(
        conn,
        importing.id,
        relative_path="02.flac",
        final_path="/library/02.flac",
        track_id=102,
    )
    assert get_import(conn, importing.id).status == "completed"
    assert final.status == "completed"
    assert get_request(conn, request.id).status == "completed"
    conn.close()
