"""Redacted daily coverage metrics for the legacy-consumer cutover."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("acquisition.correlation_coverage")

CONSUMERS = frozenset({"manual", "scheduled"})
OUTCOMES = frozenset({"prepared", "unprepared_dispatched", "blocked"})

CORRELATION_COVERAGE_DDL = """
CREATE TABLE IF NOT EXISTS acquisition_correlation_coverage (
    bucket_date TEXT NOT NULL,
    consumer TEXT NOT NULL,
    outcome TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(bucket_date, consumer, outcome),
    CHECK(consumer IN ('manual','scheduled')),
    CHECK(outcome IN ('prepared','unprepared_dispatched','blocked')),
    CHECK(count >= 0)
)
"""


def ensure_correlation_coverage_schema(conn: Any) -> None:
    conn.execute(CORRELATION_COVERAGE_DDL)


def _choice(value: Any, allowed: frozenset[str], name: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"invalid correlation coverage {name}: {value!r}")
    return normalized


def record_correlation_outcome(
    conn: Any,
    consumer: str,
    outcome: str,
    *,
    bucket_date: Optional[str] = None,
) -> None:
    """Increment one aggregate bucket; never stores entity or client data."""
    ensure_correlation_coverage_schema(conn)
    consumer = _choice(consumer, CONSUMERS, "consumer")
    outcome = _choice(outcome, OUTCOMES, "outcome")
    bucket = str(bucket_date or date.today().isoformat())
    # Validate the caller-provided test/backfill bucket without accepting an
    # arbitrary high-cardinality label.
    date.fromisoformat(bucket)
    conn.execute(
        """INSERT INTO acquisition_correlation_coverage(
               bucket_date, consumer, outcome, count)
           VALUES(?,?,?,1)
           ON CONFLICT(bucket_date, consumer, outcome) DO UPDATE SET
               count=count+1,
               updated_at=CURRENT_TIMESTAMP""",
        (bucket, consumer, outcome),
    )


def record_correlation_outcome_fail_open(
    consumer: str,
    outcome: str,
    *,
    connection_factory: Optional[Callable[[], Any]] = None,
) -> bool:
    """Best-effort caller hook; coverage must never alter dispatch behavior."""
    try:
        if connection_factory is None:
            from database.music_database import get_database
            connection_factory = get_database()._get_connection
        conn = connection_factory()
        try:
            record_correlation_outcome(conn, consumer, outcome)
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        logger.warning(
            "correlation coverage unavailable consumer=%s outcome=%s error_type=%s",
            consumer,
            outcome,
            type(exc).__name__,
        )
        return False


def correlation_coverage_summary(
    conn: Any,
    *,
    days: int = 7,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Return redacted per-consumer totals and strict-gate readiness."""
    ensure_correlation_coverage_schema(conn)
    days = int(days)
    if days < 1 or days > 90:
        raise ValueError("coverage days must be between 1 and 90")
    end = today or date.today()
    start = end - timedelta(days=days - 1)
    rows = conn.execute(
        """SELECT consumer, outcome, SUM(count) AS total
             FROM acquisition_correlation_coverage
            WHERE bucket_date BETWEEN ? AND ?
            GROUP BY consumer, outcome""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    consumers: Dict[str, Dict[str, Any]] = {}
    for consumer in sorted(CONSUMERS):
        consumers[consumer] = {
            "prepared": 0,
            "unprepared_dispatched": 0,
            "blocked": 0,
            "coverage_percent": None,
            "ready": False,
        }
    for row in rows:
        consumer, outcome, total = row[0], row[1], int(row[2] or 0)
        if consumer in consumers and outcome in OUTCOMES:
            consumers[consumer][outcome] = total
    for values in consumers.values():
        dispatched = values["prepared"] + values["unprepared_dispatched"]
        if dispatched:
            values["coverage_percent"] = round(
                values["prepared"] * 100.0 / dispatched, 2)
        values["ready"] = (
            values["prepared"] > 0
            and values["unprepared_dispatched"] == 0
        )
    return {
        "days": days,
        "from": start.isoformat(),
        "through": end.isoformat(),
        "consumers": consumers,
        "ready": all(values["ready"] for values in consumers.values()),
    }


__all__ = [
    "CONSUMERS",
    "CORRELATION_COVERAGE_DDL",
    "OUTCOMES",
    "correlation_coverage_summary",
    "ensure_correlation_coverage_schema",
    "record_correlation_outcome",
    "record_correlation_outcome_fail_open",
]
