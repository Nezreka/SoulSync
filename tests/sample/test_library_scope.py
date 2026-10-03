"""Sample jobs and derived audio stay within the selected library."""

import os
import queue

import pytest

from api import sample as sample_api
from core.library_scope import current_library_scope, library_scope
from core.sample import store, worker, stems_worker
from core.sample.analyze import ANALYZER_VERSION
from database.music_database import MusicDatabase
from tests.lib2_seed import track


@pytest.fixture
def scoped_db(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr(store, "get_database", lambda: db)
    monkeypatch.setattr(sample_api, "get_database", lambda: db)
    monkeypatch.setattr("core.library_scope.SCOPE_PARKED", False)
    monkeypatch.setattr("core.library_scope.any_own_library_exists", lambda: True)
    source = tmp_path / "shared.wav"
    source.write_bytes(b"shared audio")
    with db._get_connection() as conn:
        tid = track(conn, "Artist", "Album", "Song", path=str(source))
        conn.commit()
    return db, tid, source


def test_analysis_requires_a_file_in_the_selected_library(scoped_db):
    _, tid, source = scoped_db
    store.save_analysis(tid, {"bpm": 100, "analyzer_version": ANALYZER_VERSION},
                        source_sig=store.source_signature(str(source)))
    with library_scope(2), pytest.raises(sample_api.SampleHttpError) as exc:
        sample_api.fetch_analysis(tid)
    assert exc.value.status == 404


def test_stem_preview_requires_source_ownership(scoped_db, tmp_path):
    _, tid, _ = scoped_db
    paths = {}
    for name in ("vocals", "drums", "bass", "other"):
        path = tmp_path / (name + ".wav")
        path.write_bytes(b"derived audio")
        paths[name] = str(path)
    store.save_stems(tid, paths, "stub")
    with library_scope(2), pytest.raises(sample_api.SampleHttpError) as exc:
        sample_api._resolve_source_path(tid, "vocals")
    assert exc.value.status == 404


def test_source_signatures_distinguish_same_stat_files(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"first!")
    second.write_bytes(b"second")
    os.utime(second, ns=(first.stat().st_atime_ns, first.stat().st_mtime_ns))
    assert first.stat().st_size == second.stat().st_size
    assert store.source_signature(str(first)) != store.source_signature(str(second))


def test_tempo_requires_analysis_of_this_librarys_file(scoped_db, tmp_path):
    db, tid, source = scoped_db
    profile = db.create_profile(name="Independent library")
    own = tmp_path / "own.wav"
    own.write_bytes(b"own audio")
    with db._get_connection() as conn:
        conn.execute("INSERT INTO lib2_track_files (track_id,path,owner_profile_id) VALUES (?,?,?)",
                     (tid, str(own), profile))
        conn.commit()
    store.save_analysis(tid, {"bpm": 100, "analyzer_version": ANALYZER_VERSION},
                        source_sig=store.source_signature(str(source)))
    with library_scope(profile), pytest.raises(sample_api.SampleHttpError) as exc:
        sample_api._source_bpm_for_stretch(tid, 130)
    assert exc.value.code == "BPM_UNKNOWN"


def test_analysis_queue_and_status_are_independent_per_library(monkeypatch):
    monkeypatch.setattr(worker, "_ensure_started", lambda: None)
    monkeypatch.setattr(worker, "_task_queue", queue.Queue())
    monkeypatch.setattr(worker, "_pending", set())
    monkeypatch.setattr(worker, "_status", {})
    monkeypatch.setattr(store, "is_current", lambda *a, **kw: False)
    with library_scope(2):
        assert worker.enqueue_analysis(7) == "pending"
    with library_scope(3):
        assert worker.get_status(7) == "idle"
        assert worker.enqueue_analysis(7) == "pending"
    assert worker._task_queue.qsize() == 2


def test_stem_queue_and_progress_are_independent_per_library(monkeypatch):
    monkeypatch.setattr(stems_worker, "_ensure_started", lambda lane: None)
    monkeypatch.setattr(stems_worker, "_queues", {"demucs": queue.Queue()})
    monkeypatch.setattr(stems_worker, "_pending", set())
    monkeypatch.setattr(stems_worker, "_status", {})
    monkeypatch.setattr(stems_worker, "_progress", {})
    monkeypatch.setattr(stems_worker, "_last_method", {})
    monkeypatch.setattr(store, "stems_complete", lambda *a, **kw: False)
    with library_scope(2):
        assert stems_worker.enqueue_separation(7, "stub") == "queued"
    with library_scope(3):
        assert stems_worker.get_status(7) == "idle"
        assert stems_worker.enqueue_separation(7, "stub") == "queued"
    assert stems_worker.queue_depth() == 2


def test_stem_outputs_use_separate_directories_per_library(scoped_db):
    with library_scope(2):
        first = store.stems_dir()
    with library_scope(3):
        second = store.stems_dir()
    assert first != second


@pytest.mark.parametrize("derived", ["analysis", "stems"])
def test_missing_owned_source_never_serves_another_librarys_cache(scoped_db, tmp_path, derived):
    db, tid, source = scoped_db
    profile = db.create_profile(name="Independent library")
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO lib2_track_files (track_id,path,owner_profile_id) VALUES (?,?,?)",
            (tid, str(tmp_path / "missing-own.wav"), profile),
        )
        conn.commit()
    signature = store.source_signature(str(source))
    if derived == "analysis":
        store.save_analysis(tid, {"bpm": 100, "analyzer_version": ANALYZER_VERSION}, source_sig=signature)
    else:
        paths = {}
        for name in ("vocals", "drums", "bass", "other"):
            path = tmp_path / (name + ".wav")
            path.write_bytes(b"shared derived audio")
            paths[name] = str(path)
        store.save_stems(tid, paths, "stub", source_sig=signature)
    with library_scope(profile), pytest.raises(sample_api.SampleHttpError):
        if derived == "analysis":
            sample_api.fetch_analysis(tid)
        else:
            sample_api.stem_audio_path(tid, "vocals")


@pytest.mark.parametrize("module", [worker, stems_worker])
def test_worker_error_stays_in_queued_scope_and_restores_explicit_none(scoped_db, monkeypatch, module):
    """A failed owner's job cannot block another owner's copy or alter all-library scope."""
    q = queue.Queue()
    q.put((777, 2) if module is worker else (777, "stub", "demucs", 2))
    original_get = q.get
    monkeypatch.setattr(q, "get", lambda *a, **kw: original_get(block=False))
    monkeypatch.setattr(module, "_pending", set())
    monkeypatch.setattr(module, "_status", {})
    if module is worker:
        monkeypatch.setattr(module, "_task_queue", q)
    else:
        monkeypatch.setattr(module, "_queues", {"demucs": q})
        monkeypatch.setattr(module, "_last_method", {})
        monkeypatch.setattr(module, "_progress", {})
    with library_scope(None):
        with pytest.raises(queue.Empty):
            module._run() if module is worker else module._run("demucs")
        assert current_library_scope() is None
    with library_scope(2):
        assert module.get_status(777).startswith("error:")
    with library_scope(3):
        assert module.get_status(777) == "idle"
