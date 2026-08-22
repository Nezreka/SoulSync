"""A sync with nothing to do is still a run, and must be recorded as one.

Boulder, Aug 2026. He clicked Run on Discover Weekly repeatedly and the card
kept saying "no runs yet" and refused to open, while the pipeline was completing
fine — 58 completed pipeline runs, latest that afternoon.

From his log, the two runs back to back:

    [AUTOMATION] Playlist changed: 'ListenBrainz Weekly Exploration' — 0 added, 50 removed
    Updated existing sync history entry 20 for 'ListenBrainz Weekly Exploration'
    Starting sync for playlist: ListenBrainz Weekly Exploration      <- worked

    [AUTOMATION] No changes: 'Discover Weekly' (tracks=30)           <- stopped here

The sync step skips when the track list is unchanged and everything already
matched. That is correct as WORK, there is nothing to download. But it skipped
the bookkeeping with it, so nothing recorded that the run had happened at all
and the dashboard had nothing to show.
"""

from __future__ import annotations

import ast

import pytest

from core.downloads.history import record_sync_history_noop


@pytest.fixture()
def db(tmp_path):
    from database.music_database import MusicDatabase
    return MusicDatabase(database_path=str(tmp_path / "m.db"))


def _rows(db, name):
    conn = db._get_connection()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM sync_history WHERE playlist_name = ?", (name,))]
    finally:
        conn.close()


class TestANoOpRunIsStillARun:
    def test_it_writes_a_row(self, db):
        record_sync_history_noop(db, "auto_mirror_3", "Discover Weekly",
                                 [{"id": f"t{i}"} for i in range(30)], profile_id=1)
        rows = _rows(db, "Discover Weekly")
        assert len(rows) == 1

    def test_the_row_is_already_complete(self, db):
        """It IS complete. Every track is present, nothing was downloaded."""
        record_sync_history_noop(db, "auto_mirror_3", "Discover Weekly",
                                 [{"id": f"t{i}"} for i in range(30)], profile_id=1)
        row = _rows(db, "Discover Weekly")[0]
        assert row["completed_at"] is not None, "a finished run must not look in-flight"
        assert row["tracks_found"] == 30
        assert row["tracks_downloaded"] == 0
        assert row["tracks_failed"] == 0
        assert row["total_tracks"] == 30

    def test_it_counts_as_playlist_history(self, db):
        """Or the dashboard band, which filters to playlists, would not see it."""
        record_sync_history_noop(db, "auto_mirror_3", "Discover Weekly",
                                 [{"id": "t1"}], profile_id=1)
        entries, _ = db.get_sync_history(limit=10, profile_id=1, sync_type='playlist')
        assert [e["playlist_name"] for e in entries] == ["Discover Weekly"]

    def test_a_second_run_updates_rather_than_piling_up(self, db):
        for _ in range(3):
            record_sync_history_noop(db, "auto_mirror_3", "Discover Weekly",
                                     [{"id": "t1"}], profile_id=1)
        assert len(_rows(db, "Discover Weekly")) == 1

    def test_a_broken_database_cannot_fail_the_sync(self):
        """Bookkeeping must never take a sync down with it."""
        class Boom:
            def _get_connection(self):
                raise RuntimeError("db is gone")
            def get_latest_sync_history_by_playlist(self, *a, **k):
                raise RuntimeError("db is gone")
        record_sync_history_noop(Boom(), "auto_mirror_3", "X", [{"id": "t1"}])


class TestWiring:
    def test_the_skip_branch_records_the_run(self):
        """The call site. A recorder nothing calls is the shape that ships here."""
        with open("core/automation/handlers/sync_playlist.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        called = {
            n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "record_sync_history_noop" in called

    def test_it_is_called_before_the_skipped_return(self):
        """Recording AFTER the return would be dead code."""
        with open("core/automation/handlers/sync_playlist.py", encoding="utf-8") as f:
            src = f.read()
        call_at = src.index("record_sync_history_noop(")
        skipped_at = src.index("'status': 'skipped',\n                'reason': f'All ")
        assert call_at < skipped_at
