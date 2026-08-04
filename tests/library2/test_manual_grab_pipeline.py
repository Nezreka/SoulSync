"""docs/library-v2.md §69.2 / §71.2: a Library-v2 manual grab (Interactive
Search "Download" on a specific track) must end up in the real, organized
library — not stranded in a raw /Transfer dump with a phantom DB link.

``web_server.py::start_download`` used to unconditionally stamp
``search_result.is_simple_download = True`` for every manual grab, routing
post-processing through the metadata-free "just move to Transfer" shortcut
(``core/imports/pipeline.py``, the ``is_simple_download`` branch) even when
the grab named a specific, resolved Library-v2 track. The fix
(``core.library2.grab_context.build_lib2_import_pipeline_fields``) grounds
the grab's artist/album/track metadata in the targeted DB row itself (no
Spotify/provider object needed — the full pipeline only ever wanted a name)
and routes it through the SAME full import pipeline automatic/wishlist
downloads already use successfully.

- ``test_manual_grab_with_lib2_entity_routes_through_full_pipeline`` proves
  the fix: a grab naming a resolved track goes through the full pipeline
  (real organized path, not /Transfer) and still autolinks correctly.
- ``test_manual_grab_without_lib2_entity_keeps_simple_download_shortcut``
  proves the fix didn't touch the pre-existing "search page, no library
  target" behavior — those grabs still land in the /Transfer shortcut path,
  unchanged, and still autolink via the old heuristic-matching route.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

import core.imports.pipeline as import_pipeline
import core.imports.paths as import_paths
import core.runtime_state as runtime_state
from core.library2.grab_context import (
    build_lib2_import_pipeline_fields,
    build_lib2_track_info,
    resolve_lib2_grab_context,
)


class _Config:
    def __init__(self, transfer_path):
        self.transfer_path = transfer_path

    def get(self, key, default=None):
        if key == "soulseek.transfer_path":
            return self.transfer_path
        if key == "soulseek.download_path":
            return self.transfer_path
        if key == "features.library_v2":
            return True
        if key in {
            "post_processing.replaygain_enabled",
            "lossy_copy.enabled",
            "lossy_copy.delete_original",
            "import.replace_lower_quality",
        }:
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


@pytest.fixture
def manual_grab_target(imported_conn):
    """A lib2 track row a manual grab can name directly, plus the DB shim."""
    conn = imported_conn
    row = conn.execute(
        "SELECT id FROM lib2_tracks WHERE title='Hotline Bling'"
    ).fetchone()
    assert row is not None, "fixture legacy library must seed a missing-file track"
    return row["id"]


@pytest.fixture(autouse=True)
def _isolated_runtime_state():
    """Every test here drives the real ``matched_downloads_context`` — keep
    the module-global runtime state from leaking between tests/files."""
    original_matched_context = dict(runtime_state.matched_downloads_context)
    original_processed_ids = set(runtime_state.processed_download_ids)
    original_post_locks = dict(runtime_state.post_process_locks)
    runtime_state.matched_downloads_context.clear()
    runtime_state.processed_download_ids.clear()
    runtime_state.post_process_locks.clear()
    try:
        yield
    finally:
        runtime_state.matched_downloads_context.clear()
        runtime_state.matched_downloads_context.update(original_matched_context)
        runtime_state.processed_download_ids.clear()
        runtime_state.processed_download_ids.update(original_processed_ids)
        runtime_state.post_process_locks.clear()
        runtime_state.post_process_locks.update(original_post_locks)


def _patch_common(monkeypatch, transfer_root):
    fake_acoustid = types.ModuleType("core.acoustid_verification")
    fake_acoustid.AcoustIDVerification = _FakeAcoustidVerifier
    fake_acoustid.VerificationResult = types.SimpleNamespace(FAIL="FAIL")
    monkeypatch.setitem(sys.modules, "core.acoustid_verification", fake_acoustid)

    from core.imports.file_integrity import IntegrityResult
    monkeypatch.setattr(
        import_pipeline,
        "check_audio_integrity",
        lambda *_a, **_kw: IntegrityResult(ok=True, checks={"size_bytes": 11, "actual_length_s": 0}),
    )
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config(str(transfer_root)))
    monkeypatch.setattr(import_pipeline, "add_activity_item", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "emit_track_downloaded", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "record_library_history_download", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "check_and_remove_from_wishlist", lambda context: None)
    monkeypatch.setattr(import_pipeline.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(import_pipeline, "_journal_pipeline_check", lambda _context, **event: True)
    monkeypatch.setattr("config.settings.config_manager.get", _Config(str(transfer_root)).get)


def test_manual_grab_with_lib2_entity_routes_through_full_pipeline(
    tmp_path, monkeypatch, legacy_db, manual_grab_target
):
    """Simulates: user does Interactive Search on a specific track row (whose
    id is ``manual_grab_target``) and clicks "Download" on a Soulseek result.
    The request's own title/artist strings are deliberately stale/wrong to
    prove the pipeline trusts the resolved DB row, not the browser's card.
    """
    target_track_id = manual_grab_target
    monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)

    request_data = {
        "result_type": "track",
        "username": "someuser",
        "filename": "someuser\\Music\\Views\\02 Hotline Bling.flac",
        "size": 30_000_000,
        "title": "Hotline Bling (stale search-result title)",
        "artist": "Stale Artist Name",
        "quality": "flac",
        "lib2_track_id": target_track_id,
    }
    state, lib2_ctx = resolve_lib2_grab_context(legacy_db, request_data)
    assert state == "ok"
    assert lib2_ctx["track_id"] == target_track_id

    pipeline_fields = build_lib2_import_pipeline_fields(
        request_data, lib2_ctx, album_name=request_data.get("album_name"),
    )
    assert pipeline_fields["is_simple_download"] is False
    assert pipeline_fields["artist"]["name"] == "Drake"
    assert pipeline_fields["track_info"]["name"] == "Hotline Bling"

    context_key = "ctx-manual-grab-full"
    context = {
        "search_result": {
            "username": request_data["username"],
            "filename": request_data["filename"],
            "size": request_data["size"],
            "title": request_data["title"],
            "artist": request_data["artist"],
            "quality": request_data["quality"],
            "is_simple_download": pipeline_fields["is_simple_download"],
        },
        "artist": pipeline_fields["artist"],
        "album": pipeline_fields["album"],
        "spotify_artist": None,
        "spotify_album": None,
        "track_info": pipeline_fields["track_info"],
        "_skip_quarantine_check": None,
        "lib2_entity": lib2_ctx,
    }

    transfer_root = tmp_path / "Transfer"
    transfer_root.mkdir()
    source_path = tmp_path / "downloaded.flac"
    source_path.write_bytes(b"audio-bytes")
    organized_final_path = tmp_path / "Music" / "Drake" / "Views" / "02 Hotline Bling.flac"

    runtime_state.matched_downloads_context[context_key] = context
    _patch_common(monkeypatch, transfer_root)

    # This is the fix under test: is_simple_download=False must take the
    # REAL path-building/tagging branch, not the /Transfer shortcut. Pin its
    # destination deterministically rather than reimplementing the naming
    # template — build_final_path_for_track's own logic is covered elsewhere.
    monkeypatch.setattr(
        import_pipeline, "build_final_path_for_track",
        lambda *a, **kw: (str(organized_final_path), None),
    )
    monkeypatch.setattr(import_pipeline, "detect_album_info_web", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "enhance_file_metadata", lambda *a, **kw: True)
    def _fake_move(src, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.rename(src, dst)

    monkeypatch.setattr(import_pipeline, "safe_move_file", _fake_move)
    monkeypatch.setattr(import_pipeline, "download_cover_art", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "generate_lrc_file", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "downsample_hires_flac", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "create_lossy_copy", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "cleanup_empty_directories", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "record_soulsync_library_entry", lambda *a, **kw: None)

    runtime = types.SimpleNamespace(
        automation_engine=None,
        on_download_completed=lambda *a, **kw: None,
        web_scan_manager=None,
        repair_worker=None,
    )

    import_pipeline.post_process_matched_download(
        context_key, context, str(source_path), runtime,
    )

    assert context.get("_simple_download_completed") is None, (
        "manual grab with a resolved lib2 entity still took the "
        "metadata-free simple-download shortcut instead of the full pipeline"
    )
    assert context.get("_final_processed_path") == str(organized_final_path)
    assert "Transfer" not in context["_final_processed_path"]
    assert organized_final_path.exists()

    conn = legacy_db._get_connection()
    try:
        file_row = conn.execute(
            "SELECT track_id, path FROM lib2_track_files WHERE track_id=?",
            (target_track_id,),
        ).fetchone()
    finally:
        conn.close()

    assert file_row is not None, (
        "Manual grab completed through the full pipeline, but no "
        "lib2_track_files row was created for the targeted track"
    )
    assert file_row["path"] == str(organized_final_path)


def test_manual_grab_without_lib2_entity_keeps_simple_download_shortcut(
    tmp_path, monkeypatch, legacy_db
):
    """A plain "search page" manual download with NO Library-v2 target must
    keep its pre-existing behavior: dump to /Transfer, heuristic autolink.
    """
    monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)

    request_data = {
        "result_type": "track",
        "username": "someuser",
        "filename": "someuser\\Music\\Some Random Album\\Some Random Song.flac",
        "size": 30_000_000,
        "title": "Some Random Song",
        "artist": "Some Random Artist",
        "quality": "flac",
    }
    state, lib2_ctx = resolve_lib2_grab_context(legacy_db, request_data)
    assert state == "absent"

    pipeline_fields = build_lib2_import_pipeline_fields(request_data, lib2_ctx)
    assert pipeline_fields == {}

    track_info = build_lib2_track_info(request_data, lib2_ctx)
    assert track_info is None  # matches web_server.py falling back to a bare dict

    context_key = "ctx-manual-grab-no-entity"
    context = {
        "search_result": {
            "username": request_data["username"],
            "filename": request_data["filename"],
            "size": request_data["size"],
            "title": request_data["title"],
            "artist": request_data["artist"],
            "quality": request_data["quality"],
            "is_simple_download": pipeline_fields.get("is_simple_download", True),
        },
        "artist": pipeline_fields.get("artist"),
        "album": pipeline_fields.get("album"),
        "spotify_artist": None,
        "spotify_album": None,
        "track_info": track_info,
        "_skip_quarantine_check": None,
        "lib2_entity": lib2_ctx,
    }

    transfer_root = tmp_path / "Transfer"
    transfer_root.mkdir()
    source_path = tmp_path / "downloaded.flac"
    source_path.write_bytes(b"audio-bytes")

    runtime_state.matched_downloads_context[context_key] = context
    _patch_common(monkeypatch, transfer_root)

    runtime = types.SimpleNamespace(
        automation_engine=None,
        on_download_completed=lambda *a, **kw: None,
        web_scan_manager=None,
        repair_worker=None,
    )

    import_pipeline.post_process_matched_download(
        context_key, context, str(source_path), runtime,
    )

    assert context.get("_simple_download_completed") is True
    final_path = context.get("_final_path")
    assert final_path and os.path.exists(final_path)
    assert str(transfer_root) in final_path
