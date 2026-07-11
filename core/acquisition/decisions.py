"""Append-only persistence for acquisition Decision Engine runs."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from core.acquisition.decision_engine import CandidateDecision, DecisionReason


DECISION_RUN_ID_PREFIX = "adr1-"

CANDIDATE_DECISION_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS candidate_decision_runs (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    forced INTEGER NOT NULL DEFAULT 0,
    quality_rank INTEGER NOT NULL,
    cutoff_delta INTEGER,
    custom_format_score INTEGER NOT NULL DEFAULT 0,
    edition_match_confidence REAL NOT NULL DEFAULT 0,
    sort_key_json TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES acquisition_requests(id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES release_candidates(id) ON DELETE CASCADE,
    CHECK(accepted IN (0,1)),
    CHECK(forced IN (0,1))
)
"""

CANDIDATE_DECISIONS_DDL = """
CREATE TABLE IF NOT EXISTS candidate_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    specification TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    overridable INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES candidate_decision_runs(id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_id) REFERENCES release_candidates(id) ON DELETE CASCADE,
    UNIQUE(run_id, position),
    CHECK(severity IN ('rejection','warning','info')),
    CHECK(overridable IN (0,1))
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_candidate_decision_runs_candidate "
    "ON candidate_decision_runs(candidate_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_candidate_decisions_run "
    "ON candidate_decisions(run_id, position)",
)


@dataclass(frozen=True)
class PersistedDecisionRun:
    id: str
    decision: CandidateDecision
    created_at: str


def ensure_candidate_decisions_schema(conn: Any) -> None:
    cursor = conn.cursor()
    cursor.execute(CANDIDATE_DECISION_RUNS_DDL)
    cursor.execute(CANDIDATE_DECISIONS_DDL)
    for index_sql in _INDEXES:
        cursor.execute(index_sql)


def record_decision(conn: Any, decision: CandidateDecision) -> PersistedDecisionRun:
    """Append one immutable decision run and its ordered reasons. Does not commit."""
    ensure_candidate_decisions_schema(conn)
    relation = conn.execute(
        """SELECT 1 FROM release_candidates
            WHERE id=? AND request_id=?""",
        (decision.candidate_id, decision.request_id),
    ).fetchone()
    if relation is None:
        raise ValueError("decision candidate does not belong to decision request")
    run_id = DECISION_RUN_ID_PREFIX + secrets.token_urlsafe(18)
    conn.execute(
        """INSERT INTO candidate_decision_runs(
               id, request_id, candidate_id, engine_version, accepted, forced,
               quality_rank, cutoff_delta, custom_format_score,
               edition_match_confidence, sort_key_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, decision.request_id, decision.candidate_id,
            decision.engine_version, 1 if decision.accepted else 0,
            1 if decision.forced else 0, decision.quality_rank,
            decision.cutoff_delta, decision.custom_format_score,
            decision.edition_match_confidence, json.dumps(list(decision.sort_key)),
        ),
    )
    for position, reason in enumerate(decision.reasons):
        conn.execute(
            """INSERT INTO candidate_decisions(
                   run_id, candidate_id, position, specification, code, severity,
                   message, overridable)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                run_id, decision.candidate_id, position, reason.specification,
                reason.code, reason.severity, reason.message,
                1 if reason.overridable else 0,
            ),
        )
    created_at = conn.execute(
        "SELECT created_at FROM candidate_decision_runs WHERE id=?", (run_id,)
    ).fetchone()[0]
    return PersistedDecisionRun(run_id, decision, str(created_at))


def _reasons_for_run(conn: Any, run_id: str) -> Tuple[DecisionReason, ...]:
    rows = conn.execute(
        """SELECT specification, code, severity, message, overridable
             FROM candidate_decisions WHERE run_id=? ORDER BY position""",
        (run_id,),
    ).fetchall()
    return tuple(DecisionReason(
        str(row[0]), str(row[1]), str(row[2]), str(row[3]), bool(row[4]))
        for row in rows)


def get_decision_run(conn: Any, run_id: str) -> Optional[PersistedDecisionRun]:
    row = conn.execute(
        """SELECT id, request_id, candidate_id, engine_version, accepted, forced,
                  quality_rank, cutoff_delta, custom_format_score,
                  edition_match_confidence, sort_key_json, created_at
             FROM candidate_decision_runs WHERE id=?""",
        (str(run_id),),
    ).fetchone()
    if row is None:
        return None
    try:
        sort_key = tuple(float(value) for value in json.loads(row[10] or "[]"))
    except (TypeError, ValueError):
        sort_key = tuple()
    decision = CandidateDecision(
        request_id=str(row[1]),
        candidate_id=str(row[2]),
        accepted=bool(row[4]),
        forced=bool(row[5]),
        reasons=_reasons_for_run(conn, str(row[0])),
        quality_rank=int(row[6]),
        cutoff_delta=int(row[7]) if row[7] is not None else None,
        custom_format_score=int(row[8]),
        edition_match_confidence=float(row[9]),
        sort_key=sort_key,
        engine_version=str(row[3]),
    )
    return PersistedDecisionRun(str(row[0]), decision, str(row[11]))


def latest_decision_run(
    conn: Any, candidate_id: str,
) -> Optional[PersistedDecisionRun]:
    row = conn.execute(
        """SELECT id FROM candidate_decision_runs
            WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1""",
        (str(candidate_id),),
    ).fetchone()
    return get_decision_run(conn, row[0]) if row is not None else None


def public_decision_history(conn: Any, candidate_id: str) -> list[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT id FROM candidate_decision_runs
            WHERE candidate_id=? ORDER BY rowid""",
        (str(candidate_id),),
    ).fetchall()
    history = []
    for row in rows:
        run = get_decision_run(conn, row[0])
        if run is not None:
            history.append({
                "run_id": run.id,
                "created_at": run.created_at,
                **run.decision.to_public_dict(),
            })
    return history


__all__ = [
    "CANDIDATE_DECISIONS_DDL",
    "CANDIDATE_DECISION_RUNS_DDL",
    "DECISION_RUN_ID_PREFIX",
    "PersistedDecisionRun",
    "ensure_candidate_decisions_schema",
    "get_decision_run",
    "latest_decision_run",
    "public_decision_history",
    "record_decision",
]
