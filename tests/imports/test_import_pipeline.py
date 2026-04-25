import logging
import sys
import types

import core.imports.pipeline as import_pipeline
import core.imports.paths as import_paths
import core.runtime_state as runtime_state


class _Config:
    def __init__(self, transfer_path):
        self.transfer_path = transfer_path

    def get(self, key, default=None):
        if key == "soulseek.transfer_path":
            return self.transfer_path
        if key in {"post_processing.replaygain_enabled", "lossy_copy.enabled", "lossy_copy.delete_original", "import.replace_lower_quality"}:
            return False
        return default


class _FakeAcoustidVerifier:
    def quick_check_available(self):
        return False, "disabled"


class _ImmediateThread:
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def test_verification_wrapper_handles_simple_download(tmp_path, monkeypatch):
    transfer_root = tmp_path / "Transfer"
    transfer_root.mkdir()
    source_path = tmp_path / "source.flac"
    source_path.write_bytes(b"audio")

    context_key = "ctx-1"
    task_id = "task-1"
    batch_id = "batch-1"
    context = {
        "search_result": {
            "is_simple_download": True,
            "filename": "Album Folder/source.flac",
            "album": "Album Folder",
        },
        "track_info": {},
        "original_search_result": {},
        "is_album_download": False,
        "task_id": task_id,
        "batch_id": batch_id,
    }

    mark_calls = []
    completion_calls = []
    scan_calls = []
    activity_calls = []

    original_matched_context = dict(runtime_state.matched_downloads_context)
    original_download_tasks = dict(runtime_state.download_tasks)
    original_download_batches = dict(runtime_state.download_batches)
    original_processed_ids = set(runtime_state.processed_download_ids)
    original_post_locks = dict(runtime_state.post_process_locks)

    runtime_state.matched_downloads_context.clear()
    runtime_state.download_tasks.clear()
    runtime_state.download_batches.clear()
    runtime_state.processed_download_ids.clear()
    runtime_state.post_process_locks.clear()

    runtime = types.SimpleNamespace(
        automation_engine=None,
        on_download_completed=lambda batch, task, success: completion_calls.append((batch, task, success)),
        web_scan_manager=types.SimpleNamespace(request_scan=lambda reason: scan_calls.append(reason)),
        repair_worker=None,
    )

    fake_acoustid = types.ModuleType("core.acoustid_verification")
    fake_acoustid.AcoustIDVerification = _FakeAcoustidVerifier
    fake_acoustid.VerificationResult = types.SimpleNamespace(FAIL="FAIL")

    monkeypatch.setitem(sys.modules, "core.acoustid_verification", fake_acoustid)
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config(str(transfer_root)))
    monkeypatch.setattr(import_pipeline, "add_activity_item", lambda *args, **kwargs: activity_calls.append((args, kwargs)))
    monkeypatch.setattr(import_pipeline, "emit_track_downloaded", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "record_library_history_download", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "record_download_provenance", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "_mark_task_completed", lambda task, track_info: mark_calls.append((task, track_info)))
    monkeypatch.setattr(import_pipeline.threading, "Thread", _ImmediateThread)

    runtime_state.matched_downloads_context[context_key] = context
    runtime_state.download_tasks[task_id] = {"track_info": {}, "status": "running"}

    try:
        import_pipeline.post_process_matched_download_with_verification(
            context_key,
            context,
            str(source_path),
            task_id,
            batch_id,
            runtime,
        )

        expected_path = transfer_root / "Album Folder" / "source.flac"
        assert expected_path.exists()
        assert not source_path.exists()
        assert context["_simple_download_completed"] is True
        assert context["_final_path"] == str(expected_path)
        assert mark_calls == [(task_id, {})]
        assert completion_calls == [(batch_id, task_id, True)]
        assert context_key not in runtime_state.matched_downloads_context
        assert scan_calls == ["Simple download completed"]
        assert activity_calls
    finally:
        runtime_state.matched_downloads_context.clear()
        runtime_state.matched_downloads_context.update(original_matched_context)
        runtime_state.download_tasks.clear()
        runtime_state.download_tasks.update(original_download_tasks)
        runtime_state.download_batches.clear()
        runtime_state.download_batches.update(original_download_batches)
        runtime_state.processed_download_ids.clear()
        runtime_state.processed_download_ids.update(original_processed_ids)
        runtime_state.post_process_locks.clear()
        runtime_state.post_process_locks.update(original_post_locks)
