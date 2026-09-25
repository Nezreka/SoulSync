"""A live file-writing repair job must prove it can SEE the library first.

The Discord incident (Jose): Navidrome without "Report Real Path" fills the
catalogue with paths this process cannot resolve — or resolves to the WRONG
files via the pattern-probing resolver. Running reorganize/retag-class jobs
LIVE in that state rearranged a library SoulSync was blind to, sweeping good
tracks into the deleted quarantine. Boulder's promised fix: warn/refuse instead
of silently destroying. The guard lives in the worker so it protects every
caller — UI, automations, and Run Now alike. Dry runs stay allowed: findings
are reviewable, moves are not.
"""

from __future__ import annotations

import os

from database.music_database import MusicDatabase
from core.repair_worker import RepairWorker


def _seed(db, tmp_path, n=40, on_disk=True):
    """Seed the NATIVE catalogue — the preflight samples ``lib2_track_files``.

    (Upstream seeds the legacy ``tracks`` table; this branch no longer creates
    it, and a sample of zero rows would fall under the floor and report the
    library visible however broken the paths are.)
    """
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_artists (id, name, name_key) VALUES (1, 'A', 'a')")
        conn.execute(
            "INSERT INTO lib2_albums (id, title, primary_artist_id) "
            "VALUES (1, 'Alb', 1)")
        for i in range(n):
            if on_disk:
                p = tmp_path / "Transfer" / "A" / ("t%02d.flac" % i)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x")
                path = str(p)
            else:
                # what a Navidrome-virtual catalogue looks like from here
                path = "/navidrome/virtual/A/Alb/t%02d.flac" % i
            conn.execute(
                "INSERT INTO lib2_tracks (id, title, album_id) VALUES (?, ?, 1)",
                (i + 1, "t%02d" % i))
            conn.execute(
                "INSERT INTO lib2_track_files (track_id, path, is_primary, file_state) "
                "VALUES (?, ?, 1, 'active')", (i + 1, path))
        conn.commit()


def _worker(tmp_path, on_disk=True):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db, tmp_path, on_disk=on_disk)
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    return w


def test_an_invisible_library_fails_the_preflight(tmp_path):
    w = _worker(tmp_path, on_disk=False)
    ok, detail = w.library_visibility_preflight()
    assert ok is False
    assert "cannot see the library" in detail
    assert "deep scan" in detail, "the message must say what to DO, not just what broke"


def test_a_visible_library_passes(tmp_path):
    assert _worker(tmp_path, on_disk=True).library_visibility_preflight() == (True, '')


def test_a_tiny_library_is_never_blocked(tmp_path):
    """Below the floor a poor resolve rate can be real, and the stakes are small.
    Mirrors the dead-file cleaner's min_tracks_for_guard."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db, tmp_path, n=10, on_disk=False)
    w = RepairWorker(database=db)
    w._config_manager = None
    w.transfer_folder = str(tmp_path / "Transfer")
    assert w.library_visibility_preflight() == (True, '')


def test_a_live_writing_job_is_refused_and_a_dry_run_is_not(tmp_path, monkeypatch):
    """The whole guard, through the real _run_job seam."""
    w = _worker(tmp_path, on_disk=False)

    ran = []

    class _J:
        job_id = 'library_reorganize'
        display_name = 'Library Reorganize'
        auto_fix = True
        writes_library_files = True

        def scan(self, context):
            from core.repair_jobs.base import JobResult
            ran.append(True)
            return JobResult(scanned=1)

    job = _J()
    w._jobs = {'library_reorganize': job}
    recorded = {}
    monkeypatch.setattr(w, '_record_job_start', lambda jid: 'run-1')
    monkeypatch.setattr(w, '_record_job_end', lambda *a, **k: recorded.update(
        status=(a[2] if len(a) > 2 else k.get('status'))), raising=False)
    # live mode: dry_run off
    monkeypatch.setattr(w, 'get_job_config', lambda jid: {'settings': {'dry_run': False}})
    w._run_job('library_reorganize', forced=True)
    assert ran == [], "a live job ran against a library the worker cannot see"

    # dry run: allowed — findings are reviewable, moves are not
    monkeypatch.setattr(w, 'get_job_config', lambda jid: {'settings': {'dry_run': True}})
    w._run_job('library_reorganize', forced=True)
    assert ran == [True], "a dry run must still be allowed"


def test_jobs_that_do_not_write_files_are_never_gated(tmp_path, monkeypatch):
    w = _worker(tmp_path, on_disk=False)
    ran = []

    class _J:
        job_id = 'dead_file_cleaner'
        display_name = 'Dead File Cleaner'
        auto_fix = False
        writes_library_files = False

        def scan(self, context):
            from core.repair_jobs.base import JobResult
            ran.append(True)
            return JobResult()

    w._jobs = {'dead_file_cleaner': _J()}
    monkeypatch.setattr(w, '_record_job_start', lambda jid: 'run-1')
    monkeypatch.setattr(w, 'get_job_config', lambda jid: {'settings': {'dry_run': False}})
    w._run_job('dead_file_cleaner', forced=True)
    assert ran == [True]


def test_every_file_writing_job_carries_the_flag():
    """The guard only helps if the dangerous jobs are actually flagged. These
    four move or rewrite real library files from catalogue paths.

    (Upstream lists five; ``unknown_artist_fixer`` was a legacy-table job this
    branch deleted, so it is not on the list here.)
    """
    from core.repair_jobs import get_all_jobs
    flagged = {jid for jid, cls in get_all_jobs().items()
               if getattr(cls, 'writes_library_files', False)}
    for jid in ('library_reorganize', 'library_retag', 'track_number_repair',
                'comma_artist_splitter'):
        assert jid in flagged, jid + " moves/rewrites files but is not gated"
