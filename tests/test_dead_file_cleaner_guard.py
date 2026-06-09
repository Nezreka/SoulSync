"""Dead File Cleaner — mass-false-positive guard (#828).

macstainless: a Plex-on-macOS user running SoulSync in Docker had all 5,250
tracks flagged "dead" because their stored /Volumes/... paths don't exist inside
the container. The resolver returning None means "couldn't find it at any known
base dir" — for a mis-mounted library that's EVERY track, not a real deletion.
The job now refuses to flag when a large fraction is unresolvable (a path-mapping
problem) and reports it as such, mirroring the existing transfer-folder abort.
"""

from __future__ import annotations

from core.repair_jobs.base import JobContext
from core.repair_jobs.dead_file_cleaner import DeadFileCleanerJob


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        pass

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return [len(self._rows)]

    def close(self):
        pass


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _Cur(self._rows)

    def close(self):
        pass


class _Db:
    def __init__(self, rows):
        self._rows = rows

    def _get_connection(self):
        return _Conn(self._rows)


class _Cfg:
    def __init__(self, overrides=None):
        self._o = overrides or {}

    def get(self, key, default=None):
        return self._o.get(key, default)


def _row(i, path):
    # (track_id, title, artist, album, file_path, album_thumb, artist_thumb)
    return (i, f"Track {i}", "Yellowcard", "Ocean Avenue", path, None, None)


def _run(rows, transfer_folder, cfg_overrides=None):
    findings = []
    cfg = _Cfg({'soulseek.download_path': '', **(cfg_overrides or {})})
    ctx = JobContext(
        db=_Db(rows),
        transfer_folder=str(transfer_folder),
        config_manager=cfg,
        create_finding=lambda **kw: (findings.append(kw) or True),
    )
    res = DeadFileCleanerJob().scan(ctx)
    return res, findings


def test_mass_unresolvable_aborts_without_findings(tmp_path):
    # 30 tracks all pointing to a /Volumes path that doesn't exist in this env
    # -> systemic path problem -> abort, zero findings, one error.
    rows = [_row(i, f"/Volumes/Core/Music/Plex/Yellowcard/{i}.mp3") for i in range(30)]
    res, findings = _run(rows, tmp_path)
    assert findings == []
    assert res.findings_created == 0
    assert res.errors >= 1
    assert res.scanned == 30


def test_few_unresolvable_creates_findings(tmp_path):
    # 4 real (resolvable) files + 1 genuinely missing -> fraction 0.2 < 0.5 ->
    # the one dead file IS reported.
    rows = []
    for i in range(4):
        f = tmp_path / f"real_{i}.mp3"
        f.write_text("x")
        rows.append(_row(i, str(f)))
    rows.append(_row(99, "/no/such/path/dead.mp3"))
    res, findings = _run(rows, tmp_path,
                         {'repair.jobs.dead_file_cleaner.min_tracks_for_guard': 4})
    assert res.findings_created == 1
    assert len(findings) == 1
    assert findings[0]['entity_id'] == '99'
    assert res.errors == 0


def test_small_library_all_dead_still_reports(tmp_path):
    # 3 dead tracks, below the default min_tracks_for_guard (25) -> guard doesn't
    # apply -> all 3 reported (a tiny library can legitimately be all-dead).
    rows = [_row(i, f"/no/such/{i}.mp3") for i in range(3)]
    res, findings = _run(rows, tmp_path)
    assert res.findings_created == 3


def test_guard_thresholds_configurable(tmp_path):
    # Lower min to 4; all 4 dead -> fraction 1.0 >= 0.5 -> abort.
    rows = [_row(i, f"/no/such/{i}.mp3") for i in range(4)]
    res, findings = _run(rows, tmp_path,
                         {'repair.jobs.dead_file_cleaner.min_tracks_for_guard': 4})
    assert res.findings_created == 0
    assert res.errors >= 1


def test_healthy_library_no_abort_no_findings(tmp_path):
    # 30 fully-resolvable tracks -> 0 dead -> neither aborts nor flags anything.
    rows = []
    for i in range(30):
        f = tmp_path / f"ok_{i}.mp3"
        f.write_text("x")
        rows.append(_row(i, str(f)))
    res, findings = _run(rows, tmp_path)
    assert res.findings_created == 0
    assert res.errors == 0
    assert res.scanned == 30
    assert findings == []
