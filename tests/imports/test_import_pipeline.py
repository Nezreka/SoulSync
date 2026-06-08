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
    # The integrity layer would reject these 5-byte fixture files; bypass
    # it since these tests cover plumbing (notification + metadata_runtime
    # forwarding), not integrity behavior.
    from core.imports.file_integrity import IntegrityResult
    monkeypatch.setattr(import_pipeline, "check_audio_integrity",
                        lambda *_a, **_kw: IntegrityResult(ok=True, checks={"size_bytes": 5, "actual_length_s": 0}))
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
    # Bypass integrity check — the 5-byte fixture would fail it; this test
    # exercises the metadata-runtime forwarding path, not file integrity.
    from core.imports.file_integrity import IntegrityResult
    monkeypatch.setattr(import_pipeline, "check_audio_integrity",
                        lambda *_a, **_kw: IntegrityResult(ok=True, checks={}))
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
    library_calls = []

    def _record_library(context, artist_context, album_info):
        library_calls.append((context, artist_context, album_info))

    monkeypatch.setattr(import_pipeline, "record_soulsync_library_entry", _record_library)
    monkeypatch.setattr(import_pipeline, "check_and_remove_from_wishlist", lambda *args, **kwargs: None)

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
    assert len(library_calls) == 1
    assert library_calls[0][2]["album_name"] == "Album"


# ---------------------------------------------------------------------------
# Quarantine entry-id propagation through the verification wrapper
# (the wrapper pops task_id out of context, so _mark_task_quarantined can't
# write to the task directly — it stashes on context and the wrapper applies it)
# ---------------------------------------------------------------------------

def test_mark_task_quarantined_stashes_entry_id_when_task_id_absent():
    ctx = {}  # wrapper popped task_id before the inner pipeline ran
    import_pipeline._mark_task_quarantined(ctx, "/q/20260514_120000_song.flac.quarantined")
    assert ctx["_quarantine_entry_id"] == "20260514_120000_song"


def test_mark_task_quarantined_sets_on_task_and_stashes_when_present():
    original = dict(runtime_state.download_tasks)
    try:
        runtime_state.download_tasks.clear()
        runtime_state.download_tasks["t1"] = {"status": "running"}
        ctx = {"task_id": "t1"}
        import_pipeline._mark_task_quarantined(ctx, "/q/20260514_120000_song.flac.quarantined")
        assert runtime_state.download_tasks["t1"]["quarantine_entry_id"] == "20260514_120000_song"
        assert ctx["_quarantine_entry_id"] == "20260514_120000_song"
    finally:
        runtime_state.download_tasks.clear()
        runtime_state.download_tasks.update(original)


def test_mark_task_quarantined_noop_without_path():
    ctx = {"task_id": "t1"}
    import_pipeline._mark_task_quarantined(ctx, None)
    assert "_quarantine_entry_id" not in ctx


def test_verification_wrapper_applies_quarantine_entry_id_on_integrity_failure(monkeypatch):
    # End-to-end of the fix: the inner pipeline (mocked) quarantines on
    # integrity failure and — because the wrapper popped task_id — stashes the
    # entry id on context. The wrapper must apply it to the real task so the UI
    # can manage the quarantined file.
    task_id, batch_id, context_key = "qtask-1", "qbatch-1", "qctx-1"
    context = {"track_info": {}, "task_id": task_id, "batch_id": batch_id}

    def _fake_inner(ck, ctx, fp, runtime, metadata_runtime=None):
        ctx["_integrity_failure_msg"] = "Duration mismatch: file is 231.0s, expected 271.0s"
        ctx["_quarantine_entry_id"] = "20260514_120000_song"

    monkeypatch.setattr(import_pipeline, "post_process_matched_download", _fake_inner)

    original = dict(runtime_state.download_tasks)
    original_ctx = dict(runtime_state.matched_downloads_context)
    try:
        runtime_state.download_tasks.clear()
        runtime_state.download_tasks[task_id] = {"track_info": {}, "status": "running"}
        runtime_state.matched_downloads_context.clear()
        runtime_state.matched_downloads_context[context_key] = context

        completion = []
        runtime = types.SimpleNamespace(
            automation_engine=None,
            on_download_completed=lambda b, t, success: completion.append((b, t, success)),
            web_scan_manager=None,
            repair_worker=None,
        )
        import_pipeline.post_process_matched_download_with_verification(
            context_key, context, "/tmp/source.flac", task_id, batch_id, runtime,
        )

        t = runtime_state.download_tasks[task_id]
        assert t["status"] == "failed"
        assert t["error_message"] == "File integrity check failed: Duration mismatch: file is 231.0s, expected 271.0s"
        assert t["quarantine_entry_id"] == "20260514_120000_song"  # the fix
        assert completion == [(batch_id, task_id, False)]
    finally:
        runtime_state.download_tasks.clear()
        runtime_state.download_tasks.update(original)
        runtime_state.matched_downloads_context.clear()
        runtime_state.matched_downloads_context.update(original_ctx)


# ---------------------------------------------------------------------------
# Next-best-candidate retry on AcoustID / integrity quarantine. When a
# verification or integrity check quarantines the wrong/broken file, the wrapper
# asks the monitor to re-run the worker on the next candidate (skipping the bad
# source) instead of failing the task outright.
# ---------------------------------------------------------------------------

def _wire_retry_engine(monkeypatch):
    """Wire monitor's retry globals to capture the worker re-submission."""
    import core.downloads.monitor as monitor

    submitted = []

    class _Exec:
        def submit(self, fn, *args):
            submitted.append(args)

    monkeypatch.setattr(monitor, "missing_download_executor", _Exec())
    monkeypatch.setattr(monitor, "_download_track_worker", lambda task_id, batch_id: None)
    monkeypatch.setattr(monitor, "MAX_QUARANTINE_RETRIES", 5)

    # Pin the retry toggle ON instead of reading the runner's ambient config —
    # CI's fresh default config vs a dev's lived-in config.json must not
    # decide whether these tests pass (they did: 7 failures, CI-only).
    real_get = monitor.config_manager.get

    def _pinned_get(key, default=None):
        if key == "post_processing.retry_next_candidate_on_mismatch":
            return True
        return real_get(key, default)

    monkeypatch.setattr(monitor.config_manager, "get", _pinned_get)
    return submitted


def _patch_config(monkeypatch, overrides):
    """Override specific config keys for the monitor's config_manager reads."""
    import core.downloads.monitor as monitor

    real_get = monitor.config_manager.get

    def fake_get(key, default=None):
        if key in overrides:
            return overrides[key]
        return real_get(key, default)

    monkeypatch.setattr(monitor.config_manager, "get", fake_get)


def _run_wrapper_with_quarantine(monkeypatch, flag_setter, task_extra=None):
    task_id, batch_id, context_key = "rtask", "rbatch", "rctx"
    context = {"track_info": {}, "task_id": task_id, "batch_id": batch_id}

    monkeypatch.setattr(import_pipeline, "post_process_matched_download", flag_setter)

    original = dict(runtime_state.download_tasks)
    original_ctx = dict(runtime_state.matched_downloads_context)
    try:
        runtime_state.download_tasks.clear()
        task = {
            "track_info": {}, "status": "downloading",
            "username": "hifi", "filename": "123||A - B", "used_sources": set(),
        }
        if task_extra:
            task.update(task_extra)
        runtime_state.download_tasks[task_id] = task
        runtime_state.matched_downloads_context.clear()
        runtime_state.matched_downloads_context[context_key] = context

        completion = []
        runtime = types.SimpleNamespace(
            automation_engine=None,
            on_download_completed=lambda b, t, success: completion.append((b, t, success)),
            web_scan_manager=None,
            repair_worker=None,
        )
        import_pipeline.post_process_matched_download_with_verification(
            context_key, context, "/tmp/source.flac", task_id, batch_id, runtime,
        )
        return dict(runtime_state.download_tasks[task_id]), completion, context_key
    finally:
        runtime_state.download_tasks.clear()
        runtime_state.download_tasks.update(original)
        runtime_state.matched_downloads_context.clear()
        runtime_state.matched_downloads_context.update(original_ctx)


def test_acoustid_mismatch_requeues_next_candidate(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)

    def _fake_inner(ck, ctx, fp, runtime, metadata_runtime=None):
        ctx["_acoustid_quarantined"] = True
        ctx["_acoustid_failure_msg"] = "wrong song"

    task, completion, context_key = _run_wrapper_with_quarantine(monkeypatch, _fake_inner)

    # Task goes back to searching for the next candidate — NOT failed.
    assert task["status"] == "searching"
    assert task["quarantine_retry_count"] == 1
    # The quarantined source is flagged so the re-run won't re-pick it.
    assert "hifi_123||A - B" in task["used_sources"]
    # Stale download identity cleared; worker re-submitted; no batch failure.
    assert "download_id" not in task and "username" not in task
    assert submitted == [("rtask", "rbatch")]
    assert completion == []
    # Old context cleaned up (the re-run builds a fresh one for the new pick).
    assert context_key not in runtime_state.matched_downloads_context


def test_requeue_flags_quarantine_retry_for_cached_first(monkeypatch):
    _wire_retry_engine(monkeypatch)

    def _fake_inner(ck, ctx, fp, runtime, metadata_runtime=None):
        ctx["_acoustid_quarantined"] = True
        ctx["_acoustid_failure_msg"] = "wrong song"

    task, _, _ = _run_wrapper_with_quarantine(monkeypatch, _fake_inner)

    # The re-run is flagged so the worker walks cached candidates before
    # re-searching (cached-first), rather than re-running the full search.
    assert task["_quarantine_retry"] is True


def test_integrity_mismatch_requeues_next_candidate(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)

    def _fake_inner(ck, ctx, fp, runtime, metadata_runtime=None):
        ctx["_integrity_failure_msg"] = "Duration mismatch: file is 231.0s, expected 271.0s"

    task, completion, _ = _run_wrapper_with_quarantine(monkeypatch, _fake_inner)

    assert task["status"] == "searching"
    assert task["quarantine_retry_count"] == 1
    assert submitted == [("rtask", "rbatch")]
    assert completion == []


def test_manual_pick_does_not_requeue_on_mismatch(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)

    def _fake_inner(ck, ctx, fp, runtime, metadata_runtime=None):
        ctx["_integrity_failure_msg"] = "Duration mismatch"

    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _fake_inner, task_extra={"_user_manual_pick": True},
    )

    # User explicitly chose this file — fail it, don't silently swap.
    assert task["status"] == "failed"
    assert submitted == []
    assert completion == [("rbatch", "rtask", False)]


def test_retry_budget_exhausted_fails_task(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)
    import core.downloads.monitor as monitor
    monkeypatch.setattr(monitor, "MAX_QUARANTINE_RETRIES", 2)

    def _fake_inner(ck, ctx, fp, runtime, metadata_runtime=None):
        ctx["_acoustid_quarantined"] = True
        ctx["_acoustid_failure_msg"] = "wrong song"

    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _fake_inner, task_extra={"quarantine_retry_count": 2},
    )

    # Cap reached — fall through to normal failure handling.
    assert task["status"] == "failed"
    assert submitted == []
    assert completion == [("rbatch", "rtask", False)]


def _acoustid_quarantine(ck, ctx, fp, runtime, metadata_runtime=None):
    ctx["_acoustid_quarantined"] = True
    ctx["_acoustid_failure_msg"] = "wrong song"


def test_exhaustive_mode_uses_per_source_budget(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)
    _patch_config(monkeypatch, {
        "post_processing.retry_exhaustive": True,
        "post_processing.retries_per_query": 5,
    })

    # query_count=2 → budget for source 'hifi' = 2 * 5 = 10; first failure retries.
    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _acoustid_quarantine, task_extra={"query_count": 2},
    )

    assert task["status"] == "searching"
    # Per-source budget tracked separately from the legacy global counter.
    assert task["quarantine_retry_counts_by_source"] == {"hifi": 1}
    assert task["quarantine_retry_count"] == 1
    assert "hifi_123||A - B" in task["used_sources"]
    assert submitted == [("rtask", "rbatch")]
    assert completion == []


def test_exhaustive_source_budget_exhausted_fails(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)
    _patch_config(monkeypatch, {
        "post_processing.retry_exhaustive": True,
        "post_processing.retries_per_query": 5,
    })

    # hifi already at its full budget (query_count 2 * 5 = 10) → fail, no retry.
    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _acoustid_quarantine,
        task_extra={"query_count": 2, "quarantine_retry_counts_by_source": {"hifi": 10}},
    )

    assert task["status"] == "failed"
    assert submitted == []
    assert completion == [("rbatch", "rtask", False)]


def test_exhaustive_budget_is_separate_per_source(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)
    _patch_config(monkeypatch, {
        "post_processing.retry_exhaustive": True,
        "post_processing.retries_per_query": 5,
    })

    # soulseek is already maxed, but the failing download is on hifi — hifi has
    # its own fresh budget, so the task still retries.
    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _acoustid_quarantine,
        task_extra={"query_count": 1, "quarantine_retry_counts_by_source": {"soulseek": 5}},
    )

    assert task["status"] == "searching"
    assert task["quarantine_retry_counts_by_source"] == {"soulseek": 5, "hifi": 1}
    assert submitted == [("rtask", "rbatch")]


def test_exhaustive_soulseek_peer_resolves_to_soulseek(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)
    _patch_config(monkeypatch, {
        "post_processing.retry_exhaustive": True,
        "post_processing.retries_per_query": 5,
    })

    # A Soulseek peer name (not a streaming source) is bucketed under 'soulseek'.
    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _acoustid_quarantine,
        task_extra={"username": "DjPeer", "filename": "f.flac", "query_count": 1},
    )

    assert task["status"] == "searching"
    assert task["quarantine_retry_counts_by_source"] == {"soulseek": 1}


def test_exhaustive_budget_defaults_query_count_to_one(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)
    _patch_config(monkeypatch, {
        "post_processing.retry_exhaustive": True,
        "post_processing.retries_per_query": 1,
    })

    # No query_count on the task → budget defaults to 1 * 1 = 1; hifi already at 1.
    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _acoustid_quarantine,
        task_extra={"quarantine_retry_counts_by_source": {"hifi": 1}},
    )

    assert task["status"] == "failed"
    assert submitted == []


def test_exhaustive_absolute_ceiling_guards_runaway(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)
    import core.downloads.monitor as monitor
    monkeypatch.setattr(monitor, "MAX_TOTAL_QUARANTINE_RETRIES", 3)
    _patch_config(monkeypatch, {
        "post_processing.retry_exhaustive": True,
        "post_processing.retries_per_query": 1000,  # per-source budget effectively unbounded
    })

    # Per-source budget is huge, but the absolute total ceiling (3) still fires.
    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _acoustid_quarantine,
        task_extra={"query_count": 1, "quarantine_retry_count": 3,
                    "quarantine_retry_counts_by_source": {"hifi": 0}},
    )

    assert task["status"] == "failed"
    assert submitted == []


def _wire_orchestrator(monkeypatch, mode, hybrid_order):
    """Wire monitor's download_orchestrator so per-source budget exhaustion can
    decide whether another source remains to fall back to."""
    import core.downloads.monitor as monitor
    orch = types.SimpleNamespace(mode=mode, hybrid_order=list(hybrid_order))
    monkeypatch.setattr(monitor, "download_orchestrator", orch)
    return orch


def test_exhaustive_exhausted_source_switches_in_hybrid(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)
    _wire_orchestrator(monkeypatch, "hybrid", ["soulseek", "hifi"])
    _patch_config(monkeypatch, {
        "post_processing.retry_exhaustive": True,
        "post_processing.retries_per_query": 5,
    })

    # soulseek's budget (query_count 2 * 5 = 10) is spent. In hybrid mode the
    # task switches to the next source instead of failing the whole track.
    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _acoustid_quarantine,
        task_extra={"username": "DjPeer", "query_count": 2,
                    "quarantine_retry_counts_by_source": {"soulseek": 10}},
    )

    assert task["status"] == "searching"
    # The spent source is flagged so the worker excludes it from the next search.
    assert task["exhausted_download_sources"] == {"soulseek"}
    # Its per-source counter is NOT pushed past budget — the source is simply done.
    assert task["quarantine_retry_counts_by_source"]["soulseek"] == 10
    assert submitted == [("rtask", "rbatch")]
    assert completion == []


def test_exhaustive_all_sources_exhausted_fails_in_hybrid(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)
    _wire_orchestrator(monkeypatch, "hybrid", ["soulseek", "hifi"])
    _patch_config(monkeypatch, {
        "post_processing.retry_exhaustive": True,
        "post_processing.retries_per_query": 5,
    })

    # soulseek was exhausted on an earlier attempt; now hifi spends its last
    # budget too — no fallback source remains, so the task finally fails.
    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _acoustid_quarantine,
        task_extra={"username": "hifi", "query_count": 2,
                    "exhausted_download_sources": {"soulseek"},
                    "quarantine_retry_counts_by_source": {"hifi": 10}},
    )

    assert task["status"] == "failed"
    assert submitted == []
    assert completion == [("rbatch", "rtask", False)]


def test_exhaustive_single_source_exhausted_fails(monkeypatch):
    submitted = _wire_retry_engine(monkeypatch)
    # Single-source mode: nothing to fall back to once the budget is spent.
    _wire_orchestrator(monkeypatch, "soulseek", [])
    _patch_config(monkeypatch, {
        "post_processing.retry_exhaustive": True,
        "post_processing.retries_per_query": 5,
    })

    task, completion, _ = _run_wrapper_with_quarantine(
        monkeypatch, _acoustid_quarantine,
        task_extra={"username": "DjPeer", "query_count": 2,
                    "quarantine_retry_counts_by_source": {"soulseek": 10}},
    )

    assert task["status"] == "failed"
    assert submitted == []
    assert completion == [("rbatch", "rtask", False)]
