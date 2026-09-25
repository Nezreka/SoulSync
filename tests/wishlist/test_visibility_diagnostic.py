"""Explaining a wishlist count/list disagreement.

The 2026-08-22 production report could see that `/api/wishlist/count` said 614
and `/api/wishlist/tracks` returned 611, but nothing in the app could say WHICH
three rows were missing or which stage dropped them. `scripts/
diagnose_wishlist_visibility.py` walks the same pipeline against a read-only
copy of the database and names them.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import diagnose_wishlist_visibility as diag  # noqa: E402


def _db(tmp_path, rows):
    path = tmp_path / "music_library.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE wishlist_tracks (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               spotify_track_id TEXT NOT NULL,
               spotify_data TEXT NOT NULL,
               failure_reason TEXT,
               retry_count INTEGER DEFAULT 0,
               last_attempted TIMESTAMP,
               date_added TIMESTAMP,
               source_type TEXT,
               source_info TEXT,
               profile_id INTEGER DEFAULT 1)"""
    )
    conn.executemany(
        "INSERT INTO wishlist_tracks "
        "(spotify_track_id, spotify_data, source_info, date_added, profile_id) "
        "VALUES (?,?,?,?,1)",
        rows,
    )
    conn.commit()
    conn.close()
    return str(path)


def test_a_healthy_wishlist_reports_no_gap(tmp_path):
    path = _db(tmp_path, [
        (f"t{i}", json.dumps({"name": f"T{i}"}), '{"source": "library_v2"}', f"2026-01-0{i+1}")
        for i in range(3)
    ])
    result = diag.diagnose(path, 1)
    assert (result["stored"], result["visible"], result["gap"]) == (3, 3, 0)


def test_unreadable_json_is_named_not_just_counted(tmp_path):
    path = _db(tmp_path, [
        ("good", json.dumps({"name": "A"}), None, "2026-01-01"),
        ("broken", "{not json", None, "2026-01-02"),
    ])
    result = diag.diagnose(path, 1)
    assert result["stored"] == 2
    assert result["visible"] == 1
    assert result["gap"] == 1
    assert [d["spotify_track_id"] for d in result["dropped_unreadable_json"]] == ["broken"]


def test_a_repeated_track_id_names_both_rows(tmp_path):
    path = _db(tmp_path, [
        ("dup", json.dumps({"name": "A"}), None, "2026-01-01"),
        ("dup", json.dumps({"name": "A"}), None, "2026-01-02"),
        ("solo", json.dumps({"name": "B"}), None, "2026-01-03"),
    ])
    result = diag.diagnose(path, 1)
    assert result["gap"] == 1
    dropped = result["dropped_duplicate_track_id"]
    assert len(dropped) == 1
    # The newer row is the one hidden; the report names its shadow.
    assert dropped[0]["date_added"] == "2026-01-02"
    assert dropped[0]["shadowed_by_wishlist_id"] == 1
    assert result["repeated_track_ids"] == {"dup": 2}


def test_the_diagnostic_cannot_write(tmp_path):
    """It runs against production data, where the whole point is to preserve
    the evidence — including from itself."""
    path = _db(tmp_path, [("t", json.dumps({"name": "A"}), None, "2026-01-01")])
    conn = diag._open_readonly(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM wishlist_tracks")
    finally:
        conn.close()


def test_rows_are_grouped_by_source(tmp_path):
    path = _db(tmp_path, [
        ("a", json.dumps({"name": "A"}), '{"source": "library_v2"}', "2026-01-01"),
        ("b", json.dumps({"name": "B"}), None, "2026-01-02"),
    ])
    result = diag.diagnose(path, 1)
    assert result["rows_by_source"] == {"library_v2": 1, "other": 1}
