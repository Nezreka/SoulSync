"""the external-id ownership check walks indexes, every arm of it.

core/library/track_identity ORs every id a source carries (deezer, itunes,
mbid ...) to ask "do I own this track". SQLite only uses an expression index
when the text matches the index's, and a single unindexed arm turns the whole
OR into a scan of lib2_tracks: on every watchlist-scan and import ownership
check of a 300k-track library. the json arms were written as bare
json_extract while the indexes are TRIM(CAST(json_extract(...))), and mbid,
audiodb and jiosaavn had no index at all.
"""

from __future__ import annotations

from contextlib import closing

from core.library.track_identity import EXTERNAL_ID_COLUMNS
from database.music_database import MusicDatabase


def test_every_external_id_arm_is_served_by_an_index(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    clauses = " OR ".join(
        f"({column} = ? AND COALESCE({column}, '') != '')"
        for column in EXTERNAL_ID_COLUMNS.values())
    sql = f"SELECT t.id FROM lib2_tracks t WHERE ({clauses}) LIMIT 1"
    with closing(db._get_connection()) as conn:
        plan = [row[3] for row in conn.execute(
            f"EXPLAIN QUERY PLAN {sql}", ["x"] * len(EXTERNAL_ID_COLUMNS))]
    assert any("MULTI-INDEX OR" in step for step in plan), plan
    assert not any(step.startswith("SCAN") for step in plan), plan
