"""a v1 API track id is looked up on the file table by its legacy id; that
read every file row on a big library (third #1062 review P2C-4)."""

from __future__ import annotations

from database.music_database import MusicDatabase


def test_the_legacy_file_lookup_uses_an_index(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        plan = " ".join(str(r[-1]) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT track_id FROM lib2_track_files "
            "WHERE legacy_track_id IN (?, ?) AND track_id IS NOT NULL", ("7", 7)))
    assert "idx_lib2_track_files_legacy" in plan, plan
