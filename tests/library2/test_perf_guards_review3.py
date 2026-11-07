"""third #1062 review perf findings: indexed lookups, no DDL churn."""

from __future__ import annotations

from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import row_conn


def _conn(tmp_path):
    conn = row_conn(str(tmp_path / "lib2.db"))
    ensure_library_v2_schema(conn)
    conn.commit()
    return conn


def _plans(conn, fn):
    seen = []
    conn.set_trace_callback(seen.append)
    fn()
    conn.set_trace_callback(None)
    return [" ".join(str(tuple(r)) for r in conn.execute("EXPLAIN QUERY PLAN " + q))
            for q in seen if q.lstrip().upper().startswith("SELECT")]


def test_album_musicbrainz_owner_check_uses_the_index(tmp_path):
    """P2C-9: lib2_albums indexes LOWER(musicbrainz_id); a plain = scanned"""
    from core.library2.match_status import provider_id_owner
    conn = _conn(tmp_path)
    conn.execute("INSERT INTO lib2_artists(id, name) VALUES(1, 'A')")
    conn.execute("INSERT INTO lib2_albums(id, primary_artist_id, title, musicbrainz_id)"
                 " VALUES(5, 1, 'B', 'abcd-ef')")
    found = []
    plans = _plans(conn, lambda: found.append(
        provider_id_owner(conn, "album", "musicbrainz", "ABCD-EF")))
    assert found == [5]
    assert plans and all("SEARCH" in p for p in plans), plans


def test_a_snapshot_write_changes_no_schema(tmp_path):
    """P2C-6: every snapshot write dropped and recreated four triggers"""
    from core.library2.provider_snapshots import ensure_provider_snapshot_schema
    conn = _conn(tmp_path)
    ensure_provider_snapshot_schema(conn.cursor())
    before = conn.execute("PRAGMA schema_version").fetchone()[0]
    ensure_provider_snapshot_schema(conn.cursor())
    assert conn.execute("PRAGMA schema_version").fetchone()[0] == before
