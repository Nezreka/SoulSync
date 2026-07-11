"""The movie-side maintenance jobs — same standard as missing_episodes:
scan → deduped findings (supersede on change), approve == fix with the
{success, action, message} contract."""

from __future__ import annotations

import pytest

from core.video.repair.worker import VideoRepairWorker
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    return VideoDatabase(database_path=str(tmp_path / "video_library.db"))


@pytest.fixture()
def worker(db):
    return VideoRepairWorker(db)


def _seed_movie(db, *, sid, tmdb, title, year=2010, collection=None, col_name=None,
                overview="about it", genres=("Action",), file=None):
    mid = db.upsert_movie("plex", {"server_id": sid, "title": title, "year": year,
                                   "tmdb_id": tmdb, "overview": overview,
                                   "genres": list(genres),
                                   "file": file or {"path": f"/{sid}.mkv"}})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET tmdb_collection_id=?, tmdb_collection_name=?, "
                 "tmdb_match_status='matched', poster_url='p', backdrop_url='b' WHERE id=?",
                 (collection, col_name, mid))
    conn.commit(); conn.close()
    return mid


def _add_file(db, movie_id, *, path, size=2 * 1024**3, resolution="1080p",
              runtime_seconds=None, codec="hevc"):
    conn = db._get_connection()
    conn.execute("INSERT INTO media_files (movie_id, relative_path, size_bytes, resolution, "
                 "video_codec, quality, runtime_seconds) VALUES (?,?,?,?,?,?,?)",
                 (movie_id, path, size, resolution, codec, resolution, runtime_seconds))
    conn.commit(); conn.close()


def _pending(db):
    return db.repair_get_findings(status="pending")["items"]


# ── Complete the Collection ──────────────────────────────────────────────────
def test_movie_collections_scan_and_fix(db, worker, monkeypatch):
    _seed_movie(db, sid="m1", tmdb=603, title="The Matrix", collection=2344,
                col_name="The Matrix Collection")
    members = [{"tmdb_id": 603, "title": "The Matrix", "year": 1999, "date": "1999-03-31",
                "poster_url": "https://img/a.jpg"},
               {"tmdb_id": 604, "title": "Reloaded", "year": 2003, "date": "2003-05-15",
                "poster_url": "https://img/b.jpg"},
               {"tmdb_id": 605, "title": "Revolutions", "year": 2003, "date": "2003-11-05",
                "poster_url": "https://img/c.jpg"},
               {"tmdb_id": 999, "title": "Matrix 5", "year": 2099, "date": "2099-01-01",
                "poster_url": None},
               {"tmdb_id": 998, "title": "Matrix 6", "year": None, "date": "",
                "poster_url": None}]
    monkeypatch.setattr("core.video.repair.movie_collections._members",
                        lambda cid: members if cid == 2344 else [])
    worker._run_job("movie_collections", forced=True)
    f = _pending(db)[0]
    assert f["title"] == "The Matrix Collection — 1 of 5 owned"
    # Future-dated + dateless members never count as missing.
    assert [m["tmdb_id"] for m in f["details"]["missing"]] == [604, 605]
    res = worker.fix_finding(f["id"])
    assert res["success"] and res["action"] == "wishlisted" and "2 films" in res["message"]
    conn = db._get_connection()
    wished = {r[0] for r in conn.execute(
        "SELECT tmdb_id FROM video_wishlist WHERE kind='movie'").fetchall()}
    conn.close()
    assert wished == {604, 605}


def test_movie_collections_supersede_on_change(db, worker, monkeypatch):
    _seed_movie(db, sid="m1", tmdb=603, title="The Matrix", collection=2344, col_name="Matrix")
    members = [{"tmdb_id": 603, "title": "A", "year": 1999, "date": "1999-01-01", "poster_url": None},
               {"tmdb_id": 604, "title": "B", "year": 2003, "date": "2003-01-01", "poster_url": None}]
    monkeypatch.setattr("core.video.repair.movie_collections._members", lambda cid: members)
    worker._run_job("movie_collections", forced=True)
    old = _pending(db)[0]
    _seed_movie(db, sid="m2", tmdb=604, title="B", collection=2344, col_name="Matrix")
    members.append({"tmdb_id": 605, "title": "C", "year": 2005, "date": "2005-01-01", "poster_url": None})
    worker._run_job("movie_collections", forced=True)
    pend = _pending(db)
    assert len(pend) == 1 and [m["tmdb_id"] for m in pend[0]["details"]["missing"]] == [605]
    assert db.repair_get_finding(old["id"])["status"] == "dismissed"


# ── Quality Upgrades ─────────────────────────────────────────────────────────
def test_quality_upgrade_scan_and_grab(db, worker, monkeypatch):
    low = _seed_movie(db, sid="m1", tmdb=1, title="Lowres", file={"path": "/l.mkv"})
    hi = _seed_movie(db, sid="m2", tmdb=2, title="Sharp", file={"path": "/h.mkv"})
    _add_file(db, low, path="/l.mkv", resolution="720p", size=1024**3)
    _add_file(db, hi, path="/h.mkv", resolution="2160p")
    monkeypatch.setattr("core.video.quality_profile.load",
                        lambda _db: {"cutoff_resolution": "1080p"})
    worker._run_job("quality_upgrade", forced=True)
    pend = _pending(db)
    assert len(pend) == 1
    f = pend[0]
    assert "Lowres — 720p, cutoff is 1080p" in f["title"]
    assert f["details"]["file"]["resolution"] == "720p"
    grabbed = {}

    def fake_grab(details):
        grabbed.update(details)
        return {"success": True, "action": "grabbed", "message": "Grabbed a 1080p of Lowres"}

    monkeypatch.setattr("core.video.repair.grab.grab_movie", fake_grab)
    res = worker.fix_finding(f["id"])
    assert res["success"] and grabbed["tmdb_id"] == 1
    assert db.repair_get_finding(f["id"])["status"] == "resolved"


def test_quality_upgrade_no_cutoff_means_no_noise(db, worker, monkeypatch):
    mid = _seed_movie(db, sid="m1", tmdb=1, title="X", file={"path": "/x.mkv"})
    _add_file(db, mid, path="/x.mkv", resolution="480p")
    monkeypatch.setattr("core.video.quality_profile.load", lambda _db: {"cutoff_resolution": ""})
    worker._run_job("quality_upgrade", forced=True)
    assert _pending(db) == []


# ── Broken Files ─────────────────────────────────────────────────────────────
def test_broken_files_truncated_and_stub(db, worker):
    ok = _seed_movie(db, sid="m1", tmdb=1, title="Fine", file={"path": "/f.mkv"})
    cut = _seed_movie(db, sid="m2", tmdb=2, title="Cut Short", file={"path": "/c.mkv"})
    stub = _seed_movie(db, sid="m3", tmdb=3, title="Stub", file={"path": "/s.mkv"})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET runtime_minutes=120")
    conn.commit(); conn.close()
    _add_file(db, ok, path="/f.mkv", runtime_seconds=118 * 60)
    _add_file(db, cut, path="/c.mkv", runtime_seconds=61 * 60)
    _add_file(db, stub, path="/s.mkv", size=1024 * 1024, runtime_seconds=None)
    worker._run_job("broken_files", forced=True)
    pend = {f["details"]["title"]: f for f in _pending(db)}
    assert set(pend) == {"Cut Short", "Stub"}
    assert "runs 61 of 120 min" in pend["Cut Short"]["title"]
    assert "stub file" in pend["Stub"]["title"]
    assert pend["Cut Short"]["severity"] == "warning"


# ── Metadata Gaps ────────────────────────────────────────────────────────────
def test_metadata_gaps_scan_respects_locks_and_fixes(db, worker, monkeypatch):
    bare = db.upsert_movie("plex", {"server_id": "m1", "title": "Bare", "genres": [],
                                    "file": {"path": "/b.mkv"}})
    locked = db.upsert_movie("plex", {"server_id": "m2", "title": "Chosen Blank",
                                      "tmdb_id": 7, "genres": ["Drama"],
                                      "file": {"path": "/c.mkv"}})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET tmdb_match_status='matched', poster_url='p', "
                 "backdrop_url='b', overview='' WHERE id=?", (locked,))
    conn.commit(); conn.close()
    db.update_item_fields("movie", locked, {"overview": ""})   # deliberate blank + lock
    worker._run_job("metadata_gaps", forced=True)
    pend = _pending(db)
    assert len(pend) == 1                                      # locked blank respected
    f = pend[0]
    assert f["details"]["title"] == "Bare" and "unmatched" in f["details"]["gaps"]
    assert f["severity"] == "warning"

    calls = []

    class FakeEngine:
        def refresh_movie_art(self, mid):
            calls.append(mid)
            return {"ok": True}

    monkeypatch.setattr("core.video.enrichment.engine.get_video_enrichment_engine",
                        lambda: FakeEngine())
    res = worker.fix_finding(f["id"])
    assert res["success"] and res["action"] == "refreshed" and calls == [bare]


# ── Duplicate Movies (report-only) ───────────────────────────────────────────
def test_duplicate_movies_reports_and_has_no_fix(db, worker):
    a = _seed_movie(db, sid="m1", tmdb=42, title="Twice")
    _seed_movie(db, sid="m2", tmdb=42, title="Twice")
    _add_file(db, a, path="/v1.mkv", resolution="1080p")
    _add_file(db, a, path="/v2.mkv", resolution="2160p")
    worker._run_job("duplicate_movies", forced=True)
    pend = _pending(db)
    kinds = sorted(f["details"]["kind"] for f in pend)
    assert kinds == ["files", "rows"]
    rows_f = next(f for f in pend if f["details"]["kind"] == "rows")
    assert "2 library entries" in rows_f["title"]
    res = worker.fix_finding(rows_f["id"])
    assert not res["success"]                                  # report-only: no fix
    assert db.repair_get_finding(rows_f["id"])["status"] == "pending"
    assert worker.dismiss_finding(rows_f["id"])


# ── absent-dismissal: a resolved-elsewhere problem retires its finding ───────
def test_complete_scan_retires_findings_for_fixed_problems(db, worker):
    ok = _seed_movie(db, sid="m1", tmdb=1, title="Was Broken", file={"path": "/w.mkv"})
    conn = db._get_connection()
    conn.execute("UPDATE movies SET runtime_minutes=120")
    conn.commit(); conn.close()
    _add_file(db, ok, path="/w.mkv", runtime_seconds=30 * 60)
    worker._run_job("broken_files", forced=True)
    old = _pending(db)[0]
    # The file gets replaced outside SoulSync (rescan updates the probe).
    conn = db._get_connection()
    conn.execute("UPDATE media_files SET runtime_seconds=? WHERE relative_path='/w.mkv'",
                 (118 * 60,))
    conn.commit(); conn.close()
    worker._run_job("broken_files", forced=True)
    assert _pending(db) == []
    assert db.repair_get_finding(old["id"])["status"] == "dismissed"


# ── the grab seam (upgrade/re-download fixes) ────────────────────────────────
def test_grab_movie_contract(monkeypatch):
    from core.automation.handlers import video_process_wishlist as vpw
    from core.video.repair.grab import grab_movie
    calls = {}
    monkeypatch.setattr(vpw, "_default_search",
                        lambda item, mt: ([{"accepted": True, "quality": "1080p",
                                            "username": "u", "filename": "f",
                                            "size_bytes": 1}], None))
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/movies")
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda item, best, cands, mt, tdir: calls.update(
                            item=item, best=best, mt=mt, tdir=tdir) or True)
    res = grab_movie({"tmdb_id": 7, "title": "Film", "year": 2020})
    assert res["success"] and res["action"] == "grabbed" and "1080p" in res["message"]
    assert calls["mt"] == "movie" and calls["tdir"] == "/movies"
    # Search backend down → honest error, not a crash.
    monkeypatch.setattr(vpw, "_default_search", lambda item, mt: (None, "slskd offline"))
    res = grab_movie({"tmdb_id": 7, "title": "Film"})
    assert not res["success"] and res["error"] == "slskd offline"
    # Real search, nothing acceptable → stays pending.
    monkeypatch.setattr(vpw, "_default_search", lambda item, mt: ([], None))
    assert "quality profile" in grab_movie({"tmdb_id": 7, "title": "Film"})["error"]


# ── Wishlist Audit ───────────────────────────────────────────────────────────
def test_wishlist_audit_finds_and_removes_owned_rows(db, worker):
    _seed_movie(db, sid="m1", tmdb=9, title="Owned Film")
    db.add_movie_to_wishlist(9, "Owned Film", year=2010)
    db.add_movie_to_wishlist(10, "Still Wanted", year=2012)
    db.upsert_show_tree("plex", {"server_id": "s1", "title": "Show", "tmdb_id": 77, "seasons": [
        {"season_number": 1, "episodes": [
            {"episode_number": 1, "title": "Ep", "server_id": "e1", "file": {"path": "/e.mkv"}}]}]})
    db.add_episodes_to_wishlist(77, "Show", [{"season_number": 1, "episode_number": 1}])
    worker._run_job("wishlist_audit", forced=True)
    pend = _pending(db)
    assert len(pend) == 2                                       # movie + episode; not tmdb 10
    for f in pend:
        res = worker.fix_finding(f["id"])
        assert res["success"] and res["action"] == "removed"
    conn = db._get_connection()
    left = [r[0] for r in conn.execute("SELECT tmdb_id FROM video_wishlist").fetchall()]
    conn.close()
    assert left == [10]
