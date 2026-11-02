"""Skip Audit Cleanup removes overrides whose file is gone, never ones whose
storage is just unreachable (an unmounted drive during the weekly run took
every manual AcoustID/quality skip with it, and the files were flagged again)."""

from types import SimpleNamespace

from core.library2 import manual_skips
from core.repair_jobs.base import JobContext
from core.repair_jobs.lib2_skips_cleanup import Lib2SkipsCleanupJob


def _skips(legacy_db, paths):
    for n, path in enumerate(paths):
        key = f"user::{n}.flac"
        manual_skips.record_manual_skip(legacy_db, content_key=key, title=f"Song {n}",
                                        artist="Artist", skipped_checks=("quality",), profile_id=1)
        manual_skips.attach_manual_skip_file(legacy_db, content_key=key, file_path=path)


def _left(conn):
    return conn.execute("SELECT COUNT(*) FROM lib2_manual_skips").fetchone()[0]


def _run(legacy_db):
    cfg = SimpleNamespace(get=lambda key, default=None: default)
    return Lib2SkipsCleanupJob().scan(JobContext(legacy_db, "/music", cfg))


def test_a_gone_file_on_healthy_storage_loses_its_override(legacy_db, imported_conn, tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr("core.library2.paths.missing_path_root_is_healthy", lambda *a, **k: True)
    _skips(legacy_db, [str(tmp_path / "gone.flac")])
    assert _run(legacy_db).auto_fixed == 1
    assert _left(imported_conn) == 0


def test_unreachable_storage_keeps_the_override(legacy_db, imported_conn, tmp_path, monkeypatch):
    monkeypatch.setattr("core.library2.paths.missing_path_root_is_healthy", lambda *a, **k: False)
    _skips(legacy_db, [str(tmp_path / "nas" / "song.flac")])
    assert _run(legacy_db).auto_fixed == 0
    assert _left(imported_conn) == 1


def test_most_overrides_unreachable_at_once_removes_none(legacy_db, imported_conn, tmp_path,
                                                         monkeypatch):
    monkeypatch.setattr("core.library2.paths.missing_path_root_is_healthy", lambda *a, **k: True)
    real = tmp_path / "here.flac"
    real.write_bytes(b"audio")
    _skips(legacy_db, [str(real)] + [str(tmp_path / f"gone{n}.flac") for n in range(11)])
    assert _run(legacy_db).auto_fixed == 0
    assert _left(imported_conn) == 12
