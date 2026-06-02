"""Backfill job: Resolve Canonical Album Versions (#765 Stage 2 trigger)."""

from __future__ import annotations

import types

import core.repair_jobs.canonical_version_resolve as cvr
from core.repair_jobs import get_all_jobs
from core.repair_jobs.canonical_version_resolve import CanonicalVersionResolveJob
from database.music_database import MusicDatabase


def _ctx(db, findings):
    return types.SimpleNamespace(
        db=db,
        config_manager=None,                 # -> active_server None -> all albums
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        report_progress=None,
        update_progress=None,
        create_finding=lambda **kw: (findings.append(kw) or True),
    )


def _seed_two_albums(db):
    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO artists (id, name) VALUES ('art1', 'A')")
    cur.execute("INSERT INTO albums (id, title, artist_id) VALUES ('alb1', 'Album One', 'art1')")
    cur.execute("INSERT INTO albums (id, title, artist_id) VALUES ('alb2', 'Album Two', 'art1')")
    conn.commit()
    conn.close()


def _fake_resolver(monkeypatch):
    def fake(db, album_id, *, min_score=0.5, store=True):
        res = {"source": "spotify", "album_id": f"sp_{album_id}", "score": 0.9}
        if store:
            db.set_album_canonical(album_id, res["source"], res["album_id"], res["score"])
        return res
    monkeypatch.setattr(cvr, "resolve_and_store_canonical_for_album", fake)


def test_job_is_registered():
    jobs = get_all_jobs()  # {job_id: cls}
    assert "canonical_version_resolve" in jobs
    assert jobs["canonical_version_resolve"] is CanonicalVersionResolveJob


def test_job_is_opt_in_and_dry_run_by_default():
    assert CanonicalVersionResolveJob.default_enabled is False
    assert CanonicalVersionResolveJob.default_settings["dry_run"] is True


def test_live_resolves_and_stores(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed_two_albums(db)
    _fake_resolver(monkeypatch)

    findings = []
    ctx = _ctx(db, findings)
    job = CanonicalVersionResolveJob()
    # force live mode
    monkeypatch.setattr(job, "_get_settings", lambda c: {"dry_run": False, "min_score": 0.5})

    result = job.scan(ctx)
    assert result.auto_fixed == 2
    assert db.get_album_canonical("alb1")["source"] == "spotify"
    assert db.get_album_canonical("alb2")["album_id"] == "sp_alb2"
    assert findings == []  # live mode writes, doesn't create findings


def test_dry_run_creates_findings_without_storing(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed_two_albums(db)
    _fake_resolver(monkeypatch)

    findings = []
    ctx = _ctx(db, findings)
    job = CanonicalVersionResolveJob()
    monkeypatch.setattr(job, "_get_settings", lambda c: {"dry_run": True, "min_score": 0.5})

    result = job.scan(ctx)
    assert result.findings_created == 2
    assert len(findings) == 2
    # dry run must NOT persist
    assert db.get_album_canonical("alb1") is None


def test_skips_already_pinned_albums(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    _seed_two_albums(db)
    db.set_album_canonical("alb1", "deezer", "dz_pinned", 0.8)  # alb1 already pinned
    _fake_resolver(monkeypatch)

    ctx = _ctx(db, [])
    job = CanonicalVersionResolveJob()
    monkeypatch.setattr(job, "_get_settings", lambda c: {"dry_run": False, "min_score": 0.5})

    result = job.scan(ctx)
    assert result.skipped == 1            # alb1 skipped
    assert result.auto_fixed == 1         # only alb2 resolved
    assert db.get_album_canonical("alb1")["album_id"] == "dz_pinned"  # untouched
