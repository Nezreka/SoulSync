"""The artist index's "in your library" test must not read every wanted track.

After the upgrade every imported file is wanted, so the old IN list of wanted
tracks was the whole library (310k rows) built on every page load: the
library page timed out on a live install (11-17 s per request). The wanted
arms are correlated lookups now, asked per artist only when the owned and
monitored arms didn't already admit it.
"""

import sqlite3

from core.library2.schema import ensure_library_v2_schema
from core.library2.sql_util import library_artist_sql


def test_membership_never_scans_the_whole_wanted_table():
    conn = sqlite3.connect(":memory:")
    ensure_library_v2_schema(conn)
    plan = [row[3] for row in conn.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM lib2_artists a "
        f"WHERE a.canonical_artist_id IS NULL AND {library_artist_sql('a')}")]
    # the artists themselves are scanned once; no track table ever is
    assert not [line for line in plan if line.startswith("SCAN") and line != "SCAN va"], plan
