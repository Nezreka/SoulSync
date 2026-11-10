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


def test_start_time_quality_profile_migrations_run_once(tmp_path):
    """P2C-5: six full-table UPDATEs under the init lock on every start"""
    from core.library2.schema import _backfill_quality_profile_provenance
    conn = _conn(tmp_path)
    seen = []
    conn.set_trace_callback(seen.append)
    ensure_library_v2_schema(conn, run_backfills=False)
    _backfill_quality_profile_provenance(conn.cursor())
    conn.set_trace_callback(None)
    scans = [q for q in seen if q.lstrip().upper().startswith("UPDATE")
             and ("quality_profile_explicit IS NULL" in q or "NOT IN (" in q)]
    assert scans == []


def test_the_zero_metadata_sweep_is_a_seek(tmp_path):
    conn = _conn(tmp_path)
    plan = " ".join(str(tuple(r)) for r in conn.execute(
        "EXPLAIN QUERY PLAN UPDATE lib2_tracks SET bpm=NULLIF(bpm, 0), duration=NULLIF(duration, 0)"
        " WHERE bpm=0 OR duration=0"))
    assert "idx_lib2_tracks_zero_metadata" in plan


def test_setting_a_default_profile_rewrites_only_changed_rows(tmp_path):
    """P2C-7: "set as default" rewrote every inherited row"""
    conn = _conn(tmp_path)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='quality_profiles'").fetchone():
        import pytest
        pytest.skip("no app-wide quality_profiles table in this schema")
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='trg_quality_profiles_lib2_default_update'"
                       ).fetchone()[0]
    assert sql.count("quality_profile_id IS NOT") == 3
