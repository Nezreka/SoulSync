"""the Downloads page does not list scan-flagged library files as downloads.

storm: "Downloads list shows old items and Clear Completed does nothing."
735 rows, every one a pre-existing library file the acoustid scanner had
flagged. the scanner inserts a synthetic library_history row per flagged
file (download_source 'acoustid_scan') so it lands in the review queue;
the persistent history tail already excluded those, the unverified loader
and the review badge did not. Clear Completed deleted them and the next
daily scan put every one back. those files are reviewed from the
scanner's own findings; the Downloads page shows downloads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "m.db"))


def _row(db, title, source, status):
    conn = db._get_connection()
    try:
        conn.execute(
            "INSERT INTO library_history (event_type, title, file_path, download_source, verification_status) "
            "VALUES ('download', ?, ?, ?, ?)", (title, f"/music/{title}.flac", source, status))
        conn.commit()
    finally:
        conn.close()


def test_scanner_rows_are_left_out_when_asked(db):
    _row(db, "real download", "Soulseek", "unverified")
    _row(db, "forced", "Soulseek", "force_imported")
    _row(db, "old library file", "acoustid_scan", "unverified")
    _row(db, "verified download", "Soulseek", "verified")

    everything = db.get_library_history_unverified()
    assert {r["title"] for r in everything} == {"real download", "forced", "old library file"}

    downloads_page = db.get_library_history_unverified(exclude_download_sources=("acoustid_scan",))
    assert {r["title"] for r in downloads_page} == {"real download", "forced"}

    assert db.count_library_history_unverified() == 3
    assert db.count_library_history_unverified(exclude_download_sources=("acoustid_scan",)) == 2


def test_a_row_with_no_source_is_still_a_download(db):
    _row(db, "no source recorded", None, "unverified")
    assert [r["title"] for r in db.get_library_history_unverified(exclude_download_sources=("acoustid_scan",))] == \
        ["no source recorded"]


def test_the_downloads_page_and_its_badge_ask_for_the_exclusion():
    root = Path(__file__).resolve().parents[1]
    web = (root / "web_server.py").read_text(encoding="utf-8")
    quarantine = (root / "api" / "quarantine.py").read_text(encoding="utf-8")
    assert re.search(r"get_library_history_unverified\(\s*exclude_download_sources=\('acoustid_scan',\)", web)
    assert re.search(r"count_library_history_unverified\(\s*exclude_download_sources=\('acoustid_scan',\)", quarantine)
