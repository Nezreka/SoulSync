"""review 4 R4-A-4: a media-server re-key repoints sync_match_cache by the
server's id once per re-keyed track, inside the scan's write transaction.
with no index on that id each one scanned the whole cache."""

from __future__ import annotations

from database.music_database import MusicDatabase


def test_the_rekey_repoint_uses_an_index(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        plan = " | ".join(r[3] for r in conn.execute(
            "EXPLAIN QUERY PLAN UPDATE sync_match_cache SET server_track_id=? "
            "WHERE server_source=? AND server_track_id=?", ("new", "plex", "old")))
    assert "USING INDEX" in plan and "SCAN sync_match_cache" not in plan, plan
