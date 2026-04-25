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
    wishlist_calls = []

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
    monkeypatch.setattr(import_pipeline, "check_and_remove_from_wishlist", lambda context: wishlist_calls.append(dict(context)))
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
        assert wishlist_calls and wishlist_calls[0]["search_result"]["is_simple_download"] is True
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


def test_post_process_matched_download_forwards_separate_metadata_runtime(tmp_path, monkeypatch):
    source_path = tmp_path / "source.flac"
    source_path.write_bytes(b"audio")
    target_path = tmp_path / "Album Folder" / "track.flac"

    runtime = types.SimpleNamespace(
        automation_engine=None,
        on_download_completed=None,
        web_scan_manager=None,
        repair_worker=None,
    )
    metadata_runtime = types.SimpleNamespace(marker="metadata-runtime")
    seen = {}

    monkeypatch.setattr(import_pipeline, "config_manager", types.SimpleNamespace(
        get=lambda key, default=None: {
            "post_processing.replaygain_enabled": False,
            "lossy_copy.enabled": False,
            "lossy_copy.delete_original": False,
            "import.replace_lower_quality": False,
            "soulseek.download_path": str(tmp_path / "downloads"),
        }.get(key, default)
    ))
    monkeypatch.setattr(import_pipeline, "normalize_import_context", lambda context: context)
    monkeypatch.setattr(import_pipeline, "get_import_track_info", lambda context: {"_playlist_folder_mode": True, "_playlist_name": "Playlist"})
    monkeypatch.setattr(import_pipeline, "get_import_original_search", lambda context: {"title": "Track", "album": "Album"})
    monkeypatch.setattr(import_pipeline, "get_import_context_artist", lambda context: {"name": "Artist"})
    monkeypatch.setattr(import_pipeline, "get_import_has_clean_metadata", lambda context: True)
    monkeypatch.setattr(
        import_pipeline,
        "build_import_album_info",
        lambda context, force_album=False: {
            "is_album": True,
            "album_name": "Album",
            "track_number": 1,
            "disc_number": 1,
            "clean_track_name": "Track",
            "source": "spotify",
        },
    )
    monkeypatch.setattr(import_pipeline, "resolve_album_group", lambda artist_context, album_info, original_album: album_info["album_name"])
    monkeypatch.setattr(import_pipeline, "get_import_clean_title", lambda *args, **kwargs: "Track")
    monkeypatch.setattr(import_pipeline, "get_audio_quality_string", lambda file_path: "")
    monkeypatch.setattr(import_pipeline, "check_flac_bit_depth", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "build_final_path_for_track", lambda *args, **kwargs: (str(target_path), None))

    def _capture_enhance(file_path, context, artist, album_info, runtime=None):
        seen["runtime"] = runtime
        return True

    monkeypatch.setattr(import_pipeline, "enhance_file_metadata", _capture_enhance)
    monkeypatch.setattr(import_pipeline, "safe_move_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "download_cover_art", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "generate_lrc_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "downsample_hires_flac", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "create_lossy_copy", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "cleanup_empty_directories", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "emit_track_downloaded", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "record_library_history_download", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "record_download_provenance", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "record_soulsync_library_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "check_and_remove_from_wishlist", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_pipeline, "record_retag_download", lambda *args, **kwargs: None)

    context = {
        "track_info": {"_playlist_folder_mode": True, "_playlist_name": "Playlist"},
        "original_search_result": {"title": "Track", "album": "Album"},
        "is_album_download": False,
    }

    import_pipeline.post_process_matched_download(
        "ctx-1",
        context,
        str(source_path),
        runtime,
        metadata_runtime=metadata_runtime,
    )

    assert seen["runtime"] is metadata_runtime
