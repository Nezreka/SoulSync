"""Scan must not wipe enrichment-owned fields the server leaves blank.

The media server (Plex) returns a blank `status`, but TMDB enrichment fills it —
and the airing watchlist depends on it. A routine re-scan (incremental/deep) must
PRESERVE that backfilled status; only a FULL scan (an explicit reset) clobbers it.
"""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase
from core.video.scanner import VideoLibraryScanner


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


def _status(db, show_id):
    conn = db._get_connection()
    try:
        return conn.execute("SELECT status FROM shows WHERE id=?", (show_id,)).fetchone()[0]
    finally:
        conn.close()


def _set_status(db, show_id, status):
    conn = db._get_connection()
    conn.execute("UPDATE shows SET status=? WHERE id=?", (status, show_id))
    conn.commit()
    conn.close()


def _show(status=None):
    return {"server_id": "s1", "title": "S", "tmdb_id": 1, "status": status, "seasons": []}


def test_preserve_keeps_enriched_status_when_server_is_blank(db):
    sid = db.upsert_show_tree("plex", _show())
    _set_status(db, sid, "Returning Series")          # TMDB enrichment fills it
    db.upsert_show_tree("plex", _show(status=None), preserve_enrichment=True)
    assert _status(db, sid) == "Returning Series"      # re-scan didn't wipe it


def test_server_provided_value_still_wins(db):
    sid = db.upsert_show_tree("plex", _show())
    _set_status(db, sid, "Returning Series")
    db.upsert_show_tree("plex", _show(status="Ended"), preserve_enrichment=True)
    assert _status(db, sid) == "Ended"                 # a real server value overwrites


def test_full_scan_resets_enriched_status(db):
    sid = db.upsert_show_tree("plex", _show())
    _set_status(db, sid, "Returning Series")
    db.upsert_show_tree("plex", _show(status=None), preserve_enrichment=False)
    assert _status(db, sid) is None                    # full = fresh start, clobbers


class _Src:
    server_name = "plex"

    def __init__(self, shows):
        self._shows = shows

    def iter_movies(self, incremental=False):
        return iter([])

    def iter_shows(self, incremental=False):
        return iter(self._shows)


def test_scanner_modes_pick_the_right_preserve(db):
    # seed a show + enriched status, then re-scan it via each mode
    sid = db.upsert_show_tree("plex", _show())
    _set_status(db, sid, "Returning Series")
    blank = [{"server_id": "s1", "title": "S", "tmdb_id": 1, "status": None, "seasons": []}]

    VideoLibraryScanner(db).scan_sync(lambda: _Src(blank), mode="incremental")
    assert _status(db, sid) == "Returning Series"      # incremental preserves

    VideoLibraryScanner(db).scan_sync(lambda: _Src(blank), mode="deep")
    assert _status(db, sid) == "Returning Series"      # deep preserves

    VideoLibraryScanner(db).scan_sync(lambda: _Src(blank), mode="full")
    assert _status(db, sid) is None                    # full resets
