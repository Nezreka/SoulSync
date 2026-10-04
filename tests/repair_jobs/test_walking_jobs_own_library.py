"""#1504 — folder-walking maintenance jobs cover own-library roots.

Orphan File Detector, Fake Lossless Detector, Track Number Repair, and
Empty Folder Cleaner used to walk only the shared transfer folder. Now
they walk every root from ``maintenance_roots`` (shared first, own roots
appended, nested roots dropped — no double scan).

Hermetic: ``own_library_roots`` is faked so ``maintenance_roots`` returns
real tmp_path trees; no DB rows, no ffprobe, no mutagen needed.
"""

from __future__ import annotations

import os

import pytest

import core.imports.paths as paths
from core.repair_jobs.base import JobContext
from core.repair_jobs.empty_folder_cleaner import (
    EmptyFolderCleanerJob, remove_empty_folder,
)
from core.repair_jobs.fake_lossless_detector import FakeLosslessDetectorJob
from core.repair_jobs.orphan_file_detector import OrphanFileDetectorJob
from core.repair_jobs.track_number_repair import (
    TrackNumberRepairJob, _rename_to_basename,
)


@pytest.fixture
def roots(monkeypatch, tmp_path):
    """shared transfer + one own-library root, both real dirs."""
    transfer = tmp_path / "Transfer"
    own = tmp_path / "own"
    transfer.mkdir()
    own.mkdir()
    monkeypatch.setattr(paths, "own_library_roots",
                        lambda: [(2, str(own))])
    return transfer, own


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    def execute(self, *a, **k):
        return self
    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows=()):
        self._rows = rows
    def cursor(self):
        return _FakeCursor(self._rows)
    def close(self):
        pass


class _FakeDb:
    def _get_connection(self):
        return _FakeConn()


def _ctx(transfer, **kw):
    kw.setdefault("db", None)
    kw.setdefault("config_manager", None)
    return JobContext(transfer_folder=str(transfer), **kw)


# ── Orphan File Detector ─────────────────────────────────────────────────

def test_orphan_detector_walks_own_roots(roots):
    transfer, own = roots
    (transfer / "Artist" / "Album").mkdir(parents=True)
    (own / "Artist2" / "Album2").mkdir(parents=True)
    (transfer / "Artist" / "Album" / "01 - a.mp3").write_bytes(b"\x00" * 32)
    (own / "Artist2" / "Album2" / "01 - b.mp3").write_bytes(b"\x00" * 32)

    findings = []
    job = OrphanFileDetectorJob()
    result = job.scan(_ctx(transfer, db=_FakeDb(),
                           create_finding=lambda **k: findings.append(k) or True))
    assert result.errors == 0
    assert result.findings_created == 2
    flagged = {f["file_path"] for f in findings}
    assert any(str(own) in p for p in flagged)


def test_orphan_detector_fail_open_without_own_roots(monkeypatch, roots):
    transfer, own = roots
    (own / "x.mp3").write_bytes(b"\x00" * 32)
    def _boom():
        raise RuntimeError("db gone")
    monkeypatch.setattr(paths, "own_library_roots", _boom)
    findings = []
    job = OrphanFileDetectorJob()
    result = job.scan(_ctx(transfer, db=_FakeDb(),
                           create_finding=lambda **k: findings.append(k) or True))
    # own-library lookup blew up → shared-only walk, no crash
    assert result.errors == 0
    assert result.findings_created == 0


def test_orphan_estimate_scope_covers_own_roots(roots):
    transfer, own = roots
    (transfer / "a.mp3").write_bytes(b"\x00" * 32)
    (own / "b.mp3").write_bytes(b"\x00" * 32)
    job = OrphanFileDetectorJob()
    assert job.estimate_scope(_ctx(transfer)) == 2


# ── Fake Lossless Detector (scope only — scan needs ffprobe) ─────────────

def test_fake_lossless_estimate_scope_covers_own_roots(roots):
    transfer, own = roots
    (transfer / "a.flac").write_bytes(b"\x00" * 32)
    (own / "b.flac").write_bytes(b"\x00" * 32)
    (own / "c.mp3").write_bytes(b"\x00" * 32)  # lossy: not counted
    job = FakeLosslessDetectorJob()
    assert job.estimate_scope(_ctx(transfer)) == 2


# ── Track Number Repair ──────────────────────────────────────────────────

def test_track_number_estimate_scope_covers_own_roots(roots):
    transfer, own = roots
    (transfer / "a.mp3").write_bytes(b"\x00" * 32)
    (own / "b.mp3").write_bytes(b"\x00" * 32)
    job = TrackNumberRepairJob()
    assert job.estimate_scope(_ctx(transfer)) == 2


def test_rename_to_basename_stays_in_same_folder(tmp_path):
    """The in-place guarantee that makes widening the walk safe: a rename
    can never move a file between libraries."""
    folder = tmp_path / "own" / "Artist" / "Album"
    folder.mkdir(parents=True)
    src = folder / "01 - Song.mp3"
    src.write_bytes(b"\x00" * 32)
    new_path = _rename_to_basename(str(src), "01 - Song.mp3", "02 - Song")
    assert new_path is not None
    assert os.path.dirname(new_path) == str(folder)
    assert os.path.isfile(os.path.join(str(folder), "02 - Song.mp3"))
    assert not src.exists()


# ── Empty Folder Cleaner ─────────────────────────────────────────────────

def test_empty_folder_cleaner_flags_own_root_empties_but_never_the_root(roots):
    transfer, own = roots
    (transfer / "stale-artist").mkdir()
    (own / "stale-artist").mkdir()
    (own / "lived-in" / "Album").mkdir(parents=True)
    (own / "lived-in" / "Album" / "01 - x.mp3").write_bytes(b"\x00" * 32)

    findings = []
    job = EmptyFolderCleanerJob()
    result = job.scan(_ctx(
        transfer, create_finding=lambda **k: findings.append(k) or True))
    assert result.errors == 0
    flagged = {f["entity_id"] for f in findings}
    assert os.path.join(str(transfer), "stale-artist") in flagged
    assert os.path.join(str(own), "stale-artist") in flagged
    # the own-library root itself is never a removal candidate
    assert str(own) not in flagged
    assert str(transfer) not in flagged
    # the finding records which root it was scanned under (apply guard)
    own_finding = next(f for f in findings
                       if f["entity_id"] == os.path.join(str(own), "stale-artist"))
    assert os.path.normpath(own_finding["details"]["library_root"]) == \
        os.path.normpath(os.path.realpath(str(own)))


def test_empty_folder_cleaner_estimate_scope_covers_own_roots(roots):
    transfer, own = roots
    (transfer / "a").mkdir()
    (own / "b").mkdir()
    job = EmptyFolderCleanerJob()
    assert job.estimate_scope(_ctx(transfer)) == 2


def test_remove_empty_folder_refuses_own_library_root(tmp_path):
    own = tmp_path / "own"
    own.mkdir()
    res = remove_empty_folder(
        str(own), junk_files=[], remove_junk=True, root=str(own),
        listdir=os.listdir, isdir=os.path.isdir, islink=os.path.islink,
        remove_file=os.remove, rmdir=os.rmdir,
    )
    assert res["removed"] is False
    assert own.is_dir()
