"""the help page's tools table names jobs that exist, and the api docs keep
their video endpoints.

the library v2 merge renamed jobs in the table to names no job has
("Monitoring List Reconcile"), dropped rows for jobs that still run (Fix
Unknown Artists), and lost the /api/v1/video/stats section. nobody reading
the help could find the job the table told them about.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path("webui/static/docs-content")


def _job_names() -> set:
    import core.repair_jobs  # noqa: F401 - registers every job
    from core.repair_jobs import get_all_jobs
    return {cls.display_name for cls in get_all_jobs().values()}


def _table_rows() -> list:
    text = (DOCS / "03-dashboard.js").read_text(encoding="utf-8")
    start = text.index("| Track Number Repair |")
    rows = []
    for line in text[start:].splitlines():
        if not line.startswith("|"):
            break
        rows.append(line.split("|")[1].strip())
    return rows


def test_every_tools_row_is_a_job_that_exists():
    names = _job_names()
    assert [row for row in _table_rows() if row not in names] == []


def test_the_jobs_v2_dropped_from_the_table_are_back():
    rows = set(_table_rows())
    for name in ("Fix Unknown Artists", "Album Edition Review", "Quality Profile Audit",
                 "Watchlist & Wishlist Reconcile", "Discography Refresh"):
        assert name in rows


def test_the_api_docs_keep_video_stats_and_its_status_filter():
    text = (DOCS / "16-api.js").read_text(encoding="utf-8")
    assert re.search(r"### \\`GET /api/v1/video/stats\\`", text)
    assert "video/downloads?status=" in text
