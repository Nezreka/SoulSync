"""Regression tests for issue #1504 Phase 5 — walking maintenance jobs cover
own-library roots, not just the shared folder.

- all_library_roots() returns [shared] + own roots
- orphan detector finds orphans in own roots
- empty folder cleaner protects every library root (never deletes one)
"""
import os
import tempfile

from core.repair_jobs.base import all_library_roots, JobContext, JobResult


class _FakeDB:
    def __init__(self, profiles):
        self._profiles = profiles

    def get_own_library_profiles(self):
        return self._profiles


class _Ctx:
    def __init__(self, db, transfer):
        self.db = db
        self.transfer_folder = transfer
        self.config_manager = None

    def check_stop(self):
        return False

    def wait_if_paused(self):
        return False

    def report_progress(self, **kw):
        pass

    def update_progress(self, *a):
        pass


def test_all_library_roots_includes_own():
    with tempfile.TemporaryDirectory() as tmp:
        shared = os.path.join(tmp, 'shared')
        own = os.path.join(tmp, 'own')
        os.makedirs(shared)
        os.makedirs(own)
        db = _FakeDB([{'id': 7, 'name': 'Kid', 'root': own}])
        ctx = _Ctx(db, shared)
        roots = all_library_roots(ctx)
        assert roots[0] == shared  # shared first
        assert own in roots


def test_all_library_roots_shared_only_when_no_own():
    with tempfile.TemporaryDirectory() as tmp:
        shared = os.path.join(tmp, 'shared')
        os.makedirs(shared)
        db = _FakeDB([])
        ctx = _Ctx(db, shared)
        assert all_library_roots(ctx) == [shared]


def test_all_library_roots_skips_missing_own_root():
    with tempfile.TemporaryDirectory() as tmp:
        shared = os.path.join(tmp, 'shared')
        os.makedirs(shared)
        db = _FakeDB([{'id': 7, 'name': 'Kid', 'root': os.path.join(tmp, 'nope')}])
        ctx = _Ctx(db, shared)
        assert all_library_roots(ctx) == [shared]


def test_orphan_detector_walks_own_root():
    """An orphan file sitting in an own library must be found."""
    from core.repair_jobs.orphan_file_detector import OrphanFileDetectorJob
    with tempfile.TemporaryDirectory() as tmp:
        shared = os.path.join(tmp, 'shared')
        own = os.path.join(tmp, 'own')
        os.makedirs(shared)
        os.makedirs(own)
        # orphan audio file in the OWN library (no DB row for it)
        orphan = os.path.join(own, 'stray.mp3')
        with open(orphan, 'wb') as f:
            f.write(b'\x00' * 100)

        import sqlite3
        db_path = os.path.join(tmp, 't.db')
        conn = sqlite3.connect(db_path)
        # ours: an empty Library v2 catalogue
        from core.library2.schema import ensure_library_v2_schema
        ensure_library_v2_schema(conn)
        conn.commit()
        conn.close()

        class _DB(_FakeDB):
            def _get_connection(self):
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c

        ctx = _Ctx(_DB([{'id': 7, 'name': 'Kid', 'root': own}]), shared)
        findings = []
        ctx.create_finding = lambda **kw: findings.append(kw) or True

        job = OrphanFileDetectorJob()
        result = job.scan(ctx)
        assert result.scanned >= 1
        assert any(orphan in (f.get('file_path') or '') for f in findings), \
            f"orphan in own root not flagged: {findings}"


def test_empty_folder_cleaner_never_removes_library_roots():
    """The 'never the library root' guard protects every own root."""
    from core.repair_jobs.empty_folder_cleaner import EmptyFolderCleanerJob
    with tempfile.TemporaryDirectory() as tmp:
        shared = os.path.join(tmp, 'shared')
        own = os.path.join(tmp, 'own')
        os.makedirs(shared)
        os.makedirs(own)
        # empty subdir inside own root -> should be flagged
        empty_sub = os.path.join(own, 'empty_album')
        os.makedirs(empty_sub)

        ctx = _Ctx(_FakeDB([{'id': 7, 'name': 'Kid', 'root': own}]), shared)
        findings = []
        ctx.create_finding = lambda **kw: findings.append(kw) or True

        job = EmptyFolderCleanerJob()
        result = job.scan(ctx)

        flagged = {f.get('file_path') for f in findings}
        # the empty subdir is flagged...
        assert empty_sub in flagged
        # ...but neither library root is
        assert os.path.realpath(shared) not in flagged
        assert os.path.realpath(own) not in flagged
        # and both still exist on disk
        assert os.path.isdir(shared)
        assert os.path.isdir(own)
