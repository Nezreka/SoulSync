"""Retry-state journal: redaction, lifecycle, expiry (docs/library-v2.md §8)."""

import sqlite3

import pytest

from core.acquisition.retry_state import (
    RETRY_STATE_TTL_SECONDS,
    acquisition_task_ref,
    close_retry_state,
    get_retry_state,
    journal_retry_snapshot,
    list_active_retry_states,
    purge_expired_retry_state,
    redact_candidates,
    restore_candidates,
    update_retry_progress,
)


@pytest.fixture()
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


class _Candidate:
    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)


def _snapshot(conn, **overrides):
    payload = dict(
        task_id="acq-aim1-x-7",
        import_id="aim1-x",
        track_id=7,
        candidates=[
            {
                "username": "peer1",
                "filename": "a.flac",
                "size": 100,
                "bitrate": 1000,
                "quality": "flac",
                "confidence": 0.9,
                "_source_metadata": {"download_url": "https://secret/nzb"},
            },
            _Candidate(
                username="peer2", filename="b.mp3", size=50, quality="mp3",
                upload_speed=100, queue_length=2, confidence=0.5,
                magnet="magnet:?xt=urn:btih:deadbeef",
            ),
        ],
        used_sources={"peer0_bad.flac"},
        exhausted_sources={"soulseek"},
        retry_counts={"soulseek": 3},
        retry_count=3,
        query_count=2,
        last_error="quality mismatch at https://indexer/api?apikey=123",
        last_progress="requeued after quality quarantine",
        now=1000.0,
    )
    payload.update(overrides)
    journal_retry_snapshot(conn, **payload)


def test_snapshot_roundtrip_is_redacted(conn):
    _snapshot(conn)
    state = get_retry_state(conn, "acq-aim1-x-7")

    assert state is not None
    assert state.status == "active"
    assert state.import_id == "aim1-x"
    assert state.track_id == 7
    assert state.used_sources == ("peer0_bad.flac",)
    assert state.exhausted_sources == ("soulseek",)
    assert state.retry_counts == {"soulseek": 3}
    assert state.retry_count == 3
    assert state.query_count == 2
    assert state.expires_at == 1000.0 + RETRY_STATE_TTL_SECONDS
    # Candidate whitelist: identity + quality facts survive, secrets never.
    assert [c["username"] for c in state.candidates] == ["peer1", "peer2"]
    flat = str(state.candidates)
    assert "secret" not in flat and "magnet" not in flat
    assert "_source_metadata" not in flat
    # Sensitive fragments in the error text are redacted before persistence.
    assert "apikey" not in (state.last_error or "")
    assert "[redacted]" in (state.last_error or "")


def test_redact_candidates_drops_incomplete_entries():
    redacted = redact_candidates([
        {"username": "u", "filename": "f", "size": 1},
        {"username": "", "filename": "f"},
        {"filename": "only-file"},
        "not-a-candidate",
    ])
    assert [c["username"] for c in redacted] == ["u"]


def test_restore_candidates_rebuilds_track_results():
    _snapshot_source = [
        {
            "username": "peer1", "filename": "Artist - Song.flac", "size": 9,
            "bitrate": 1411, "quality": "flac", "sample_rate": 44100,
            "bit_depth": 16, "confidence": 0.87, "track_number": 3,
        },
        {"username": "", "filename": "broken"},
    ]
    restored = restore_candidates(_snapshot_source)

    assert len(restored) == 1
    candidate = restored[0]
    assert candidate.username == "peer1"
    assert candidate.filename == "Artist - Song.flac"
    assert candidate.quality == "flac"
    assert candidate.bit_depth == 16
    assert candidate.track_number == 3
    # The legacy walk reads .confidence directly — it must exist.
    assert candidate.confidence == pytest.approx(0.87)
    assert candidate.result_type == "track"


def test_snapshot_updates_active_but_never_reopens_closed(conn):
    _snapshot(conn)
    _snapshot(conn, retry_count=4, used_sources={"peer0_bad.flac", "peer1_a.flac"})
    state = get_retry_state(conn, "acq-aim1-x-7")
    assert state.retry_count == 4
    assert state.used_sources == ("peer0_bad.flac", "peer1_a.flac")

    assert close_retry_state(conn, status="completed", task_id="acq-aim1-x-7") == 1
    _snapshot(conn, retry_count=9)
    state = get_retry_state(conn, "acq-aim1-x-7")
    assert state.status == "completed"
    assert state.retry_count == 4


def test_update_retry_progress_only_touches_active_rows(conn):
    _snapshot(conn)
    assert update_retry_progress(
        conn, "acq-aim1-x-7",
        used_sources={"peer0_bad.flac", "peer2_b.mp3"},
        last_progress="downloading peer2",
    ) is True
    state = get_retry_state(conn, "acq-aim1-x-7")
    assert state.used_sources == ("peer0_bad.flac", "peer2_b.mp3")
    assert state.last_progress == "downloading peer2"

    close_retry_state(conn, status="cancelled", task_id="acq-aim1-x-7")
    assert update_retry_progress(
        conn, "acq-aim1-x-7", last_progress="zombie") is False
    assert update_retry_progress(conn, "missing", last_progress="x") is False


def test_close_by_import_scope_and_validation(conn):
    _snapshot(conn)
    _snapshot(conn, task_id="acq-aim1-x-8", track_id=8)
    _snapshot(conn, task_id="acq-aim1-y-7", import_id="aim1-y")

    assert close_retry_state(
        conn, status="failed", import_id="aim1-x", error="exhausted") == 2
    assert get_retry_state(conn, "acq-aim1-x-7").status == "failed"
    assert get_retry_state(conn, "acq-aim1-x-7").last_error == "exhausted"
    assert get_retry_state(conn, "acq-aim1-y-7").status == "active"

    assert close_retry_state(
        conn, status="approved", import_id="aim1-y", track_id=7) == 1

    with pytest.raises(ValueError):
        close_retry_state(conn, status="active", task_id="t")
    with pytest.raises(ValueError):
        close_retry_state(conn, status="nonsense", task_id="t")
    with pytest.raises(ValueError):
        close_retry_state(conn, status="failed")


def test_list_active_skips_closed_and_expired(conn):
    _snapshot(conn)
    _snapshot(conn, task_id="expired", track_id=9, ttl_seconds=3600, now=0.0)
    _snapshot(conn, task_id="closed", track_id=10)
    close_retry_state(conn, status="completed", task_id="closed")

    active = list_active_retry_states(conn, now=100000.0)
    assert [state.task_id for state in active] == ["acq-aim1-x-7"]
    assert list_active_retry_states(conn, import_id="other", now=100000.0) == ()


def test_purge_drops_only_expired_rows(conn):
    _snapshot(conn)
    _snapshot(conn, task_id="expired-active", track_id=9, ttl_seconds=3600, now=0.0)
    _snapshot(conn, task_id="expired-closed", track_id=10, ttl_seconds=3600, now=0.0)
    close_retry_state(conn, status="failed", task_id="expired-closed")

    assert purge_expired_retry_state(conn, now=100000.0) == 2
    assert get_retry_state(conn, "acq-aim1-x-7") is not None
    assert get_retry_state(conn, "expired-active") is None
    assert get_retry_state(conn, "expired-closed") is None


def test_acquisition_task_ref_parses_markers():
    assert acquisition_task_ref({
        "_acquisition_import_id": "aim1-x",
        "_acquisition_track_id": "7",
    }) == ("aim1-x", 7)
    assert acquisition_task_ref({"_acquisition_import_id": "aim1-x"}) is None
    assert acquisition_task_ref({}) is None
    assert acquisition_task_ref(None) is None
    assert acquisition_task_ref({
        "_acquisition_import_id": "aim1-x",
        "_acquisition_track_id": "not-a-number",
    }) is None
