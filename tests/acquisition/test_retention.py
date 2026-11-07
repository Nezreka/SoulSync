"""third #1062 review G04-4: the acquisition journal had no retention.

every admin download wrote a request, candidates, a decision run, a grab and
several history rows, and nothing deleted any of it."""

from __future__ import annotations

import sqlite3

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.retention import prune_acquisition_journal

OLD = "2020-01-01 00:00:00"


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "acq.db"))
    c.execute("PRAGMA foreign_keys = ON")
    ensure_acquisition_schema(c)
    c.commit()
    yield c
    c.close()


def _request(conn, rid, status, when=OLD):
    conn.execute(
        "INSERT INTO acquisition_requests(id, profile_id, scope, entity_id, quality_profile_id, trigger,"
        " idempotency_key, status, created_at, updated_at, completed_at)"
        " VALUES(?, 1, 'recording', 1, 1, 'manual', ?, ?, ?, ?, ?)",
        (rid, rid, status, when, when, when if status != "pending" else None))


def _candidate(conn, cid, rid, expires=0.0):
    conn.execute(
        "INSERT INTO release_candidates(id, request_id, source, protocol, content_scope, title,"
        " server_ref, dedupe_key, expires_at) VALUES(?, ?, 'usenet', 'nzb', 'recording', 't', 'x', ?, ?)",
        (cid, rid, cid, expires))


def _grab(conn, download_id, rid, status, when=OLD):
    conn.execute(
        "INSERT INTO acquisition_grabs(download_id, acquisition_request_id, source, status,"
        " created_at, updated_at) VALUES(?, ?, 'usenet', ?, ?, ?)",
        (download_id, rid, status, when, when))


def _history(conn, hid, event, *, request_id=None, download_id=None, when=OLD):
    conn.execute(
        "INSERT INTO acquisition_history(id, request_id, download_id, event_type, actor_profile_id,"
        " created_at) VALUES(?, ?, ?, ?, 1, ?)", (hid, request_id, download_id, event, when))


def _ids(conn, table, column="id"):
    return {r[0] for r in conn.execute(f"SELECT {column} FROM {table}")}


def test_settled_rows_past_retention_go_and_everything_open_stays(conn):
    _request(conn, "done-old", "completed")
    _candidate(conn, "cand-old", "done-old")
    conn.execute("INSERT INTO candidate_decision_runs(id, request_id, candidate_id, engine_version,"
                 " accepted, quality_rank) VALUES('run-old', 'done-old', 'cand-old', 'v1', 1, 1)")
    _grab(conn, "dl-old", "done-old", "completed")
    _history(conn, "h-old", "grab_completed", request_id="done-old", download_id="dl-old")
    _history(conn, "h-recovered", "recovered_to_staging", download_id="dl-old")

    _request(conn, "done-but-downloading", "failed")
    _grab(conn, "dl-live", "done-but-downloading", "downloading")
    _history(conn, "h-live", "grab_submitted", request_id="done-but-downloading", download_id="dl-live")

    _request(conn, "done-recent", "completed", when="2999-01-01 00:00:00")
    _history(conn, "h-recent-request", "request_created", request_id="done-recent")
    _request(conn, "still-pending", "pending")
    conn.commit()

    stats = prune_acquisition_journal(conn, days=90)

    assert _ids(conn, "acquisition_requests") == {"done-but-downloading", "done-recent", "still-pending"}
    assert _ids(conn, "acquisition_grabs", "download_id") == {"dl-live"}
    assert _ids(conn, "release_candidates") == set()
    assert _ids(conn, "candidate_decision_runs") == set()
    # history of anything kept stays, and recovery's own record always does
    assert _ids(conn, "acquisition_history") == {"h-live", "h-recent-request", "h-recovered"}
    assert stats["requests"] == 1 and stats["grabs"] == 1 and stats["history"] == 1


def test_history_stays_append_only_outside_the_prune(conn):
    _history(conn, "h1", "request_created")
    conn.commit()
    prune_acquisition_journal(conn, days=90)  # installs the gated trigger
    _history(conn, "h2", "request_created")
    conn.commit()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM acquisition_history")
    assert _ids(conn, "acquisition_history_prune_gate", "open") == set()


def test_zero_days_keeps_everything(conn):
    _request(conn, "done-old", "completed")
    conn.commit()
    assert not any(prune_acquisition_journal(conn, days=0).values())
    assert _ids(conn, "acquisition_requests") == {"done-old"}
