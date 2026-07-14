"""Watched Cleanup job — the video Expired Download Cleaner (Maintainerr rule).

Substrate: movies.last_viewed_at (schema v34) scanned from Plex lastViewedAt /
Jellyfin UserData.LastPlayedDate, kept fresh by a Plex lastViewedAt incremental
delta (watch-state changes never bump updatedAt — without the second delta the
job would only see week-old deep-scan state). Job: watched ≥ N days ago →
finding; approve = file to the RECYCLE BIN + row marked file-less. Watched
items with no date are skipped, never guessed. Dismissed movies never re-flag.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.video.repair.worker import VideoRepairWorker
from database.video_database import VideoDatabase

_ROOT = Path(__file__).resolve().parent.parent.parent
_SOURCES = (_ROOT / "core" / "video" / "sources.py").read_text(encoding="utf-8")
_REPAIR_JS = (_ROOT / "webui" / "static" / "video" / "video-repair.js").read_text(encoding="utf-8")
_HUB_JS = (_ROOT / "webui" / "static" / "stats-automations.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    d = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    d.set_setting("movies_path", str(tmp_path / "Movies"))
    return d


@pytest.fixture()
def worker(db):
    return VideoRepairWorker(db)


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")


def _seed_movie(db, tmp_path, *, title, play_count=0, last_viewed=None,
                added=None, size=2 * 1024 ** 3, on_disk=True):
    mid = db.upsert_movie("plex", {"server_id": "sv-" + title, "title": title, "year": 2020,
                                   "tmdb_id": abs(hash(title)) % 10 ** 6,
                                   "play_count": play_count, "last_viewed_at": last_viewed})
    rel = f"Movies/{title}/{title}.mkv"
    real = tmp_path / rel
    if on_disk:
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_bytes(b"x" * 16)
        size = 16
    conn = db._get_connection()
    conn.execute("INSERT INTO media_files (movie_id, relative_path, size_bytes) VALUES (?,?,?)",
                 (mid, str(real), size))
    conn.execute("UPDATE movies SET has_file=1 WHERE id=?", (mid,))
    if added:
        conn.execute("UPDATE movies SET added_at=? WHERE id=?", (added, mid))
    conn.commit(); conn.close()
    return mid, real


def _pending(db):
    return [f for f in db.repair_get_findings(status="pending")["items"]
            if f["finding_type"] == "watched_cleanup"]


# ── substrate ────────────────────────────────────────────────────────────────
def test_upsert_persists_last_viewed_at(db):
    mid = db.upsert_movie("plex", {"server_id": "x1", "title": "Heat", "tmdb_id": 949,
                                   "play_count": 2, "last_viewed_at": "2026-06-01 20:00:00"})
    conn = db._get_connection()
    row = conn.execute("SELECT play_count, last_viewed_at FROM movies WHERE id=?", (mid,)).fetchone()
    conn.close()
    assert row["play_count"] == 2 and row["last_viewed_at"] == "2026-06-01 20:00:00"


def test_scanners_read_the_watch_dates():
    # Plex: lastViewedAt on the movie dict + the incremental watch-state delta
    assert '"last_viewed_at": _iso_dt(getattr(m, "lastViewedAt", None))' in _SOURCES
    assert '"lastViewedAt>>": since' in _SOURCES
    # Jellyfin: UserData.LastPlayedDate
    assert 'LastPlayedDate' in _SOURCES


def test_iso_dt_handles_both_shapes():
    from core.video.sources import _iso_dt
    assert _iso_dt("2026-07-01T20:15:30.0000000Z") == "2026-07-01 20:15:30"
    assert _iso_dt(datetime(2026, 7, 1, 20, 15, 30)) == "2026-07-01 20:15:30"
    assert _iso_dt(None) is None and _iso_dt("") is None


# ── scan rules ───────────────────────────────────────────────────────────────
def test_scan_flags_only_stale_watched_movies(db, worker, tmp_path):
    _seed_movie(db, tmp_path, title="OldWatch", play_count=1, last_viewed=_days_ago(45))
    _seed_movie(db, tmp_path, title="FreshWatch", play_count=1, last_viewed=_days_ago(3))
    _seed_movie(db, tmp_path, title="NeverWatched", play_count=0, added=_days_ago(500))
    _seed_movie(db, tmp_path, title="DatelessWatch", play_count=1, last_viewed=None)

    worker._run_job("watched_cleanup", forced=True)
    items = _pending(db)
    assert [f["details"]["title"] for f in items] == ["OldWatch"]
    f = items[0]
    assert f["severity"] == "info" and "watched 4" in f["title"] and "GB" in f["title"]
    assert f["details"]["watched"] is True


def test_unwatched_rule_is_opt_in(db, worker, tmp_path):
    _seed_movie(db, tmp_path, title="DustyShelf", play_count=0, added=_days_ago(400))
    worker._run_job("watched_cleanup", forced=True)
    assert _pending(db) == []                       # off by default

    worker.set_job_config("watched_cleanup", settings={"watched_days": 30,
                                                       "include_unwatched": True,
                                                       "unwatched_days": 365})
    worker._run_job("watched_cleanup", forced=True)
    items = _pending(db)
    assert len(items) == 1 and "never watched" in items[0]["title"]


def test_rewatched_movie_leaves_the_candidate_set(db, worker, tmp_path):
    mid, _ = _seed_movie(db, tmp_path, title="Comfort", play_count=1, last_viewed=_days_ago(60))
    worker._run_job("watched_cleanup", forced=True)
    assert len(_pending(db)) == 1
    conn = db._get_connection()
    conn.execute("UPDATE movies SET last_viewed_at=? WHERE id=?", (_days_ago(1), mid))
    conn.commit(); conn.close()
    worker._run_job("watched_cleanup", forced=True)   # re-watched → retire the pending finding
    assert _pending(db) == []


# ── fix: recycle + mark file-less ────────────────────────────────────────────
def test_fix_recycles_the_file_and_marks_fileless(db, worker, tmp_path):
    mid, real = _seed_movie(db, tmp_path, title="SeenIt", play_count=2, last_viewed=_days_ago(90))
    worker._run_job("watched_cleanup", forced=True)
    f = _pending(db)[0]

    res = worker.fix_finding(f["id"])
    assert res["success"] and res["action"] == "cleaned" and "recycle bin" in res["message"]
    assert not real.exists()
    trash = tmp_path / "Movies" / "ss_recycle"
    assert any(n.endswith("_SeenIt.mkv") for n in (trash.iterdir() and [p.name for p in trash.iterdir()]))
    conn = db._get_connection()
    row = conn.execute("SELECT has_file FROM movies WHERE id=?", (mid,)).fetchone()
    nfiles = conn.execute("SELECT COUNT(*) FROM media_files WHERE movie_id=?", (mid,)).fetchone()[0]
    conn.close()
    assert row["has_file"] == 0 and nfiles == 0


def test_fix_recycles_all_versions_of_a_multi_part_movie(db, worker, tmp_path):
    """A multi-version movie (Plex multi-part) must have EVERY file recycled —
    recycling only the largest and marking file-less would orphan the rest."""
    mid, big = _seed_movie(db, tmp_path, title="MultiPart", play_count=1,
                           last_viewed=_days_ago(90))
    small = tmp_path / "Movies" / "MultiPart" / "MultiPart 720p.mkv"
    small.write_bytes(b"y" * 8)
    conn = db._get_connection()
    conn.execute("INSERT INTO media_files (movie_id, relative_path, size_bytes) VALUES (?,?,?)",
                 (mid, str(small), 8))
    conn.commit(); conn.close()

    worker._run_job("watched_cleanup", forced=True)
    f = _pending(db)[0]
    res = worker.fix_finding(f["id"])
    assert res["success"]
    assert not big.exists() and not small.exists()          # BOTH gone from the library
    trash = tmp_path / "Movies" / "ss_recycle"
    names = [p.name for p in trash.iterdir()]
    assert any(n.endswith("_MultiPart.mkv") for n in names)
    assert any(n.endswith("_MultiPart 720p.mkv") for n in names)


def test_fix_refuses_when_the_file_cannot_be_located(db, worker, tmp_path):
    _seed_movie(db, tmp_path, title="Phantom", play_count=1, last_viewed=_days_ago(90),
                on_disk=False)
    worker._run_job("watched_cleanup", forced=True)
    f = _pending(db)[0]
    res = worker.fix_finding(f["id"])
    assert res["success"] is False and "locate" in res["error"]
    conn = db._get_connection()
    assert conn.execute("SELECT has_file FROM movies").fetchone()[0] == 1   # untouched
    conn.close()


# ── surface contracts ────────────────────────────────────────────────────────
def test_job_is_registered_everywhere():
    from core.video.repair import get_all_jobs
    assert "watched_cleanup" in get_all_jobs()
    from core.automation.blocks import blocks_for_scope
    repair_block = next(b for b in blocks_for_scope("video")["actions"]
                        if b["type"] == "video_run_repair_job")
    values = [o["value"] for o in repair_block["config_fields"][0]["options"]]
    assert "watched_cleanup" in values              # runnable from automations
    assert "watched_cleanup: 'Watched Cleanup'" in _REPAIR_JS
    assert "watched_cleanup: 'Clean Up'" in _REPAIR_JS
    assert "cleaned: 'Cleaned'" in _REPAIR_JS
    assert "function watchedDetailHTML" in _REPAIR_JS
    assert "v-watched-cleanup" in _HUB_JS           # discoverable via a hub recipe
