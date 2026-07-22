"""Coverage diagnostics for the Roadmap-3 legacy-consumer cutover."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from core.acquisition.correlation_coverage import (
    correlation_coverage_summary,
    record_correlation_outcome,
)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def test_summary_aggregates_without_storing_dispatch_details(conn):
    for _ in range(3):
        record_correlation_outcome(
            conn, "manual", "prepared", bucket_date="2026-07-14")
    record_correlation_outcome(
        conn, "scheduled", "prepared", bucket_date="2026-07-14")
    record_correlation_outcome(
        conn, "scheduled", "unprepared_dispatched", bucket_date="2026-07-14")
    record_correlation_outcome(
        conn, "scheduled", "blocked", bucket_date="2026-07-14")

    summary = correlation_coverage_summary(
        conn, days=1, today=date(2026, 7, 14))

    assert summary["consumers"]["manual"] == {
        "prepared": 3,
        "unprepared_dispatched": 0,
        "blocked": 0,
        "coverage_percent": 100.0,
        "ready": True,
    }
    assert summary["consumers"]["scheduled"]["coverage_percent"] == 50.0
    assert summary["consumers"]["scheduled"]["ready"] is False
    assert summary["ready"] is False
    columns = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(acquisition_correlation_coverage)")
    }
    assert columns == {
        "bucket_date", "consumer", "outcome", "count", "updated_at"}


@pytest.mark.parametrize("days", [0, 91])
def test_summary_rejects_unbounded_windows(conn, days):
    with pytest.raises(ValueError, match="between 1 and 90"):
        correlation_coverage_summary(conn, days=days)


def test_metric_dimensions_are_closed(conn):
    with pytest.raises(ValueError, match="consumer"):
        record_correlation_outcome(conn, "other", "prepared")
    with pytest.raises(ValueError, match="outcome"):
        record_correlation_outcome(conn, "manual", "filename-specific")
