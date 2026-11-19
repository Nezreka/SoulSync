"""#2205: a 47 GB library db with a 2.3 GB wal and a web ui that barely moved.

pins the housekeeping that keeps a big database in shape: retention only on
caches and soulsync's own logs (never listening history or library rows),
the fts merge, the wal checkpoint that cuts the file back, table sizes, and
the connection tuning every pooled connection gets.
"""

from __future__ import annotations

import sqlite3
import time

from core import db_housekeeping as hk


def _db(tmp_path):
    path = str(tmp_path / "hk.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    return path, conn


def _iso(days_ago):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - days_ago * 86400))


def test_prune_deletes_only_rows_past_the_cutoff(tmp_path):
    _, conn = _db(tmp_path)
    conn.execute("CREATE TABLE journal (id INTEGER PRIMARY KEY, at TEXT, kind TEXT)")
    conn.executemany("INSERT INTO journal (at, kind) VALUES (?, ?)",
                     [(_iso(100), "old"), (_iso(100), "keep"), (_iso(10), "new"), (_iso(1), "new")])
    conn.execute("CREATE TABLE epochs (id INTEGER PRIMARY KEY, at REAL)")
    conn.executemany("INSERT INTO epochs (at) VALUES (?)",
                     [(time.time() - 100 * 86400,), (time.time() - 86400,)])
    conn.commit()
    removed = hk.prune(conn, rules=(("journal", "at", 30, "iso", "kind <> 'keep'"),
                                    ("epochs", "at", 30, "epoch", ""),
                                    ("not_there", "at", 1, "iso", ""),
                                    ("epochs", "no_such_column", 1, "epoch", "")))
    assert removed == {"journal": 1, "epochs": 1}
    assert sorted(r[0] for r in conn.execute("SELECT kind FROM journal")) == ["keep", "new", "new"]


def test_a_missing_index_only_drops_the_hint(tmp_path):
    _, conn = _db(tmp_path)
    conn.execute("CREATE TABLE journal (id INTEGER PRIMARY KEY, at TEXT)")
    conn.execute("INSERT INTO journal (at) VALUES (?)", (_iso(90),))
    conn.commit()
    assert hk.prune(conn, rules=(("journal", "at", 30, "iso", "", "idx_not_there"),)) == {"journal": 1}


def test_prune_works_in_batches(tmp_path, monkeypatch):
    _, conn = _db(tmp_path)
    conn.execute("CREATE TABLE journal (id INTEGER PRIMARY KEY, at TEXT)")
    conn.executemany("INSERT INTO journal (at) VALUES (?)", [(_iso(90),)] * 25 + [(_iso(1),)])
    conn.commit()
    monkeypatch.setattr(hk, "PRUNE_BATCH", 10)
    assert hk.prune(conn, rules=(("journal", "at", 30, "iso", ""),)) == {"journal": 25}
    assert conn.execute("SELECT count(*) FROM journal").fetchone()[0] == 1


# tables that hold what a person did or owns. a retention rule on any of them
# would delete it; the rules are for caches and soulsync's own logs only
_PROTECTED_PREFIXES = ("lib2_artists", "lib2_albums", "lib2_tracks", "lib2_track_files",
                       "lib2_recordings", "lib2_release", "listening_", "user_playlist",
                       "mirrored_playlist", "wishlist", "watchlist", "profiles", "playlist",
                       "server_plays", "lib2_entity_history", "lib2_external_id_history",
                       "library_history", "quality_profiles", "automations")


def test_no_retention_rule_touches_user_or_library_data():
    assert hk.RETENTION_RULES, "the rules list went empty"
    for table, column, days, kind, *_rest in hk.RETENTION_RULES:
        assert not table.startswith(_PROTECTED_PREFIXES), table
        assert days >= 30, f"{table}: keep at least a month"
        assert kind in ("iso", "epoch")


def test_retention_rules_name_real_columns(tmp_path):
    """every rule's table and column exist in a fresh database, so a rename
    can't quietly turn a rule off."""
    import web_server
    db = web_server.get_database()
    conn = db._get_connection()
    try:
        for table, column, *_rest in hk.RETENTION_RULES:
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            assert column in cols, f"{table}.{column}"
    finally:
        conn.close()


def test_optimize_merges_every_fts5_index(tmp_path):
    _, conn = _db(tmp_path)
    conn.execute("CREATE VIRTUAL TABLE song_search USING fts5(title, tokenize='trigram')")
    conn.execute("CREATE TABLE plain (id INTEGER PRIMARY KEY)")
    for i in range(50):
        conn.execute("INSERT INTO song_search (title) VALUES (?)", (f"song {i}",))
        conn.commit()
    segments = lambda: conn.execute("SELECT count(*) FROM song_search_data").fetchone()[0]
    before = segments()
    assert hk.optimize(conn) == {"fts_optimized": ["song_search"], "pages_freed": 0}
    assert segments() < before
    # still answers: 'song 1' and 'song 10'..'song 19'
    assert conn.execute(
        "SELECT count(*) FROM song_search WHERE song_search MATCH '\"song 1\"'").fetchone()[0] == 11


def test_incremental_vacuum_mode_actually_hands_pages_back(tmp_path):
    """the settings switch put the db in incremental mode and nothing ever ran
    the vacuum step, so freed pages stayed in the file for good."""
    path = str(tmp_path / "inc.db")
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [("y" * 2000,)] * 2000)
    conn.commit()
    conn.execute("DELETE FROM t")
    conn.commit()
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] > 500
    out = hk.optimize(conn)
    assert out["pages_freed"] > 500
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0


def test_incremental_vacuum_leaves_a_normal_database_alone(tmp_path):
    _, conn = _db(tmp_path)
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [("y" * 2000,)] * 500)
    conn.commit()
    conn.execute("DELETE FROM t")
    conn.commit()
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    assert hk.optimize(conn)["pages_freed"] == 0
    assert conn.execute("PRAGMA freelist_count").fetchone()[0] == free


def test_checkpoint_cuts_a_big_wal_back(tmp_path):
    path, conn = _db(tmp_path)
    conn.execute("PRAGMA wal_autocheckpoint = 0")
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [("y" * 1000,)] * 3000)
    conn.commit()
    assert hk.wal_size(path) > 1_000_000
    out = hk.checkpoint(path, truncate_over=1000)
    assert out["mode"] == "TRUNCATE" and out["busy"] == 0
    assert hk.wal_size(path) == 0
    assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 3000


def test_checkpoint_leaves_a_small_wal_on_passive(tmp_path):
    path, conn = _db(tmp_path)
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('a')")
    conn.commit()
    out = hk.checkpoint(path, truncate_over=10**9)
    assert out["mode"] == "PASSIVE"


def test_table_sizes_counts_indexes_and_fts_shadows_with_their_table(tmp_path):
    _, conn = _db(tmp_path)
    conn.execute("CREATE TABLE big (id INTEGER PRIMARY KEY, x TEXT)")
    conn.execute("CREATE INDEX big_x ON big(x)")
    conn.execute("CREATE TABLE small (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE VIRTUAL TABLE search USING fts5(title)")
    conn.executemany("INSERT INTO big (x) VALUES (?)", [("z" * 200,)] * 2000)
    conn.executemany("INSERT INTO search (title) VALUES (?)", [("word",)] * 50)
    conn.commit()
    got = hk.table_sizes(conn)
    names = [t["name"] for t in got["tables"]]
    if got["method"] == "dbstat":
        assert names[0] == "big"
        assert "big_x" not in names and "search" in names
        assert not any(n.startswith("search_") for n in names)
    conn.execute("ANALYZE")
    est = hk.table_sizes(conn, use_dbstat=False)
    assert est["method"] == "row_estimates"
    assert est["tables"][0] == {"name": "big", "rows": 2000}


def test_every_connection_caps_the_wal_and_gets_a_bigger_cache():
    import web_server
    from database import music_database as mdb
    conn = web_server.get_database()._get_connection()
    try:
        assert conn.execute("PRAGMA journal_size_limit").fetchone()[0] \
            == mdb.DB_JOURNAL_SIZE_LIMIT_MB * 1024 * 1024 > 0
        assert conn.execute("PRAGMA cache_size").fetchone()[0] == -mdb.DB_CACHE_MB * 1024
        assert mdb.DB_CACHE_MB >= 8
    finally:
        conn.close()


def test_tuning_env_overrides(monkeypatch):
    from database import music_database as mdb
    monkeypatch.setenv("SOULSYNC_DB_CACHE_MB", "32")
    assert mdb._env_mb("SOULSYNC_DB_CACHE_MB", 8) == 32
    monkeypatch.setenv("SOULSYNC_DB_CACHE_MB", "lots")
    assert mdb._env_mb("SOULSYNC_DB_CACHE_MB", 8) == 8


def test_default_rules_spare_history_library_rows_artist_facts_and_decisions(tmp_path):
    """an ancient timestamp on everything: only the two dead-weight kinds go."""
    _, conn = _db(tmp_path)
    old = _iso(2000)
    conn.executescript(f"""
        CREATE TABLE listening_history (id INTEGER PRIMARY KEY, played_at TEXT);
        INSERT INTO listening_history (played_at) VALUES ('{old}');
        CREATE TABLE lib2_tracks (id INTEGER PRIMARY KEY, title TEXT, created_at TEXT);
        INSERT INTO lib2_tracks (title, created_at) VALUES ('t', '{old}');
        CREATE TABLE musicbrainz_cache (id INTEGER PRIMARY KEY, entity_type TEXT, last_updated TEXT);
        INSERT INTO musicbrainz_cache (entity_type, last_updated) VALUES
            ('recording', '{old}'), ('release', '{old}'), ('artist', '{old}'),
            ('artist_recording_pin', '{old}'), ('recording', '{_iso(10)}');
        CREATE TABLE repair_findings (id INTEGER PRIMARY KEY, status TEXT, resolved_at TEXT);
        INSERT INTO repair_findings (status, resolved_at) VALUES
            ('resolved', '{old}'), ('dismissed', '{old}'), ('pending', NULL),
            ('resolved', '{_iso(5)}'), ('resolved', NULL);
    """)
    conn.commit()
    assert hk.prune(conn) == {"musicbrainz_cache": 2, "repair_findings": 1}
    assert conn.execute("SELECT count(*) FROM listening_history").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM lib2_tracks").fetchone()[0] == 1
    assert sorted(r[0] for r in conn.execute("SELECT entity_type FROM musicbrainz_cache")) \
        == ["artist", "artist_recording_pin", "recording"]
    assert sorted(str(r[0]) for r in conn.execute("SELECT status FROM repair_findings")) \
        == ["dismissed", "pending", "resolved", "resolved"]


def test_the_prune_reads_an_index_not_the_table():
    """musicbrainz_cache is the biggest table on a real library and mostly
    json; a prune that scanned it would read all of it every day."""
    import web_server
    conn = web_server.get_database()._get_connection()
    try:
        # the partial index on the age column, not a status index that still
        # reads every resolved row to look at its date
        want = {"musicbrainz_cache": "idx_mb_cache_stale", "repair_findings": "idx_rf_resolved_age"}
        assert set(want) == {r[0] for r in hk.RETENTION_RULES}
        indexes = hk._indexes(conn)
        for rule in hk.RETENTION_RULES:
            plan = " ".join(str(r[3]) for r in conn.execute(
                "EXPLAIN QUERY PLAN " + hk.select_sql(rule, indexes), ("2000-01-01",)))
            assert f"INDEX {want[rule[0]]}" in plan, (rule[0], plan)
    finally:
        conn.close()


def test_cache_maintenance_runs_the_housekeeping_once_a_day(monkeypatch):
    import web_server
    from core.repair_jobs import cache_evictor
    from core.repair_jobs.base import JobContext, JobResult
    calls = []
    monkeypatch.setattr(hk, "prune", lambda conn, **k: calls.append("prune") or {"repair_findings": 3})
    monkeypatch.setattr(hk, "optimize", lambda conn: calls.append("optimize") or {})
    monkeypatch.setattr(hk, "checkpoint", lambda path, **k: calls.append("checkpoint") or {})
    monkeypatch.setattr(cache_evictor.CacheEvictorJob, "_last_housekeeping", {})
    db = web_server.get_database()
    ctx = JobContext(db=db, transfer_folder="", config_manager=None)
    job = cache_evictor.CacheEvictorJob()
    result = JobResult()
    job._housekeeping(ctx, result)
    assert calls == ["prune", "optimize", "checkpoint"] and result.auto_fixed == 3
    calls.clear()
    job._housekeeping(ctx, JobResult())
    assert calls == ["checkpoint"]
    # run now by hand: does it all again
    ctx.manual = True
    calls.clear()
    job._housekeeping(ctx, JobResult())
    assert calls == ["prune", "optimize", "checkpoint"]


# ── the maintenance panel's endpoints ────────────────────────────────────────


def _admin_client():
    import web_server
    return web_server.app.test_client()


def test_info_reports_the_wal_tuning_and_room_to_vacuum():
    data = _admin_client().get("/api/database/maintenance/info").get_json()
    assert data["success"] is True
    assert "wal_size" in data and data["vacuum_needs"] == data["total_size"]
    assert data["tuning"]["wal_limit_mb"] == 64
    assert isinstance(data["vacuum_has_room"], bool)


def test_tables_answers_estimates_now_and_measures_off_the_request(monkeypatch):
    started = []
    monkeypatch.setattr(hk, "start_measure", lambda path: started.append(path) or True)
    client = _admin_client()
    data = client.get("/api/database/maintenance/tables").get_json()
    assert data["success"] is True and started == [] and isinstance(data["estimates"], list)
    data = client.get("/api/database/maintenance/tables?measure=1").get_json()
    assert data["started"] is True and len(started) == 1


def test_measure_reports_sizes_when_done(tmp_path):
    path, conn = _db(tmp_path)
    conn.execute("CREATE TABLE big (x TEXT)")
    conn.executemany("INSERT INTO big VALUES (?)", [("z" * 300,)] * 500)
    conn.commit()
    hk._measure.update(state="idle", result=None)
    assert hk.start_measure(path) is True
    for t in __import__("threading").enumerate():
        if t.name == "db-measure":
            t.join(10)
    state = hk.measure_state()
    assert state["state"] == "done", state
    assert state["result"]["tables"][0]["name"] == "big"


def test_optimize_endpoint_prunes_merges_and_checkpoints(monkeypatch):
    seen = []
    monkeypatch.setattr(hk, "prune", lambda conn, **k: seen.append("prune") or {"repair_findings": 2})
    monkeypatch.setattr(hk, "optimize", lambda conn: seen.append("optimize") or {"fts_optimized": ["x"]})
    monkeypatch.setattr(hk, "checkpoint", lambda path, **k: seen.append(("checkpoint", k)) or
                        {"wal_before": 10, "wal_after": 0, "busy": 0})
    data = _admin_client().post("/api/database/maintenance/optimize").get_json()
    assert data["success"] is True and data["pruned"] == {"repair_findings": 2}
    assert seen == ["prune", "optimize", ("checkpoint", {"truncate_over": 0})]


def test_vacuum_refuses_without_room_for_the_copy(monkeypatch):
    import shutil
    from collections import namedtuple
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(10, 10, 1))
    resp = _admin_client().post("/api/database/maintenance/vacuum", json={})
    assert resp.status_code == 409
    assert "free disk space" in resp.get_json()["error"]
