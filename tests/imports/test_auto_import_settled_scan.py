"""a folder that already failed or needs attention sits in staging until the
user retries it. the scan still lists it every minute, but it must never be
submitted again, and the log has to say it's being left alone (a user read
570 "13 candidates" lines as 570 re-imports)."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.auto_import_worker import AutoImportWorker, FolderCandidate


def _cand(h):
    return FolderCandidate(path=f'/staging/{h}', name=h,
                           audio_files=[f'/staging/{h}/01.flac'], folder_hash=h)


def _worker(monkeypatch, tmp_path, settled_hashes):
    w = AutoImportWorker(database=MagicMock(), process_callback=MagicMock())
    w._executor = MagicMock()
    monkeypatch.setattr(w, '_resolve_staging_path', lambda: str(tmp_path))
    monkeypatch.setattr('core.imports.side_effects.is_active_media_server_ready', lambda: (True, ''))
    monkeypatch.setattr(w, '_enumerate_folders', lambda s: [_cand('failed1'), _cand('failed2'), _cand('new1')])
    monkeypatch.setattr(w, '_is_already_processed', lambda c: c.folder_hash in settled_hashes)
    monkeypatch.setattr(w, '_is_folder_stable', lambda c: True)
    logged = []
    import core.auto_import_worker as mod
    monkeypatch.setattr(mod.logger, 'info', lambda msg, *a, **k: logged.append(msg % a if a else msg))
    return w, logged


def test_settled_folders_are_listed_but_never_submitted(monkeypatch, tmp_path):
    w, logged = _worker(monkeypatch, tmp_path, {'failed1', 'failed2'})
    w._scan_and_submit()
    submitted = [c.args[1].folder_hash for c in w._executor.submit.call_args_list]
    assert submitted == ['new1']
    line = next(m for m in logged if 'Scan cycle' in m)
    assert '3 candidates' in line and '2 already handled' in line


def test_all_settled_submits_nothing(monkeypatch, tmp_path):
    w, logged = _worker(monkeypatch, tmp_path, {'failed1', 'failed2', 'new1'})
    w._scan_and_submit()
    w._executor.submit.assert_not_called()
    assert any('3 already handled' in m for m in logged)


def test_nothing_settled_reads_like_before(monkeypatch, tmp_path):
    w, logged = _worker(monkeypatch, tmp_path, set())
    w._scan_and_submit()
    assert w._executor.submit.call_count == 3
    assert not any('already handled' in m for m in logged)
