import sqlite3

from core.acquisition.retry_state import (
    ensure_retry_state_schema,
    get_retry_state,
    purge_expired_retry_state,
    record_candidate_snapshot,
    update_retry_state,
)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_retry_state_round_trip_is_redacted_and_expiring():
    conn = _conn()
    record_candidate_snapshot(
        conn,
        task_id="task-1",
        import_id="import-1",
        track_id=7,
        candidates=[{
            "username": "peer",
            "filename": "Artist/track.flac",
            "download_url": "https://secret.invalid/file",
            "quality": "flac",
        }],
    )
    update_retry_state(
        conn,
        "task-1",
        used_sources={"peer_Artist/track.flac"},
        exhausted_sources={"soulseek"},
        retry_counts={"soulseek": 1},
        retry_count=1,
        last_error="quality mismatch",
    )
    conn.commit()

    state = get_retry_state(conn, "task-1")
    assert state is not None
    assert state["candidates"][0]["username"] == "peer"
    assert "download_url" not in state["candidates"][0]
    assert state["used_sources"] == ("peer_Artist/track.flac",)
    assert state["retry_count"] == 1

    conn.execute("UPDATE acquisition_retry_state SET expires_at=0")
    assert purge_expired_retry_state(conn, now=1) == 1
    assert get_retry_state(conn, "task-1") is None


def test_acquisition_schema_includes_retry_state():
    conn = _conn()
    ensure_retry_state_schema(conn)
    names = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(acquisition_retry_state)").fetchall()
    }
    assert {"task_id", "candidates_json", "expires_at"} <= names
