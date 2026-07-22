"""Simple-download path must embed the basic metadata it already knows.

The ``is_simple_download`` branch used to only MOVE the file — it never wrote
tags. Manual/search downloads (which carry a real title + artist) therefore
landed in the library with whatever tags the source file happened to have,
often none. That produced tag-poor albums with no source ID, which then
dead-end in library-reorganize + retag.

Fix: write the known title/artist/album as basic tags after the move — but
ONLY non-placeholder values, so we never stamp 'Unknown' over a file's real
tags (reuses the #800 placeholder guard).
"""

from __future__ import annotations

import types

import core.imports.pipeline as import_pipeline


# --- pure helper: which fields get written ---

def test_keeps_real_values():
    db = import_pipeline._build_simple_download_tag_data(
        {'title': 'Money', 'artist': 'Pink Floyd'}, 'The Dark Side of the Moon',
    )
    assert db == {
        'title': 'Money',
        'artist_name': 'Pink Floyd',
        'album_title': 'The Dark Side of the Moon',
    }


def test_drops_placeholder_values():
    db = import_pipeline._build_simple_download_tag_data(
        {'title': 'Money', 'artist': 'Unknown'}, '',
    )
    assert db == {'title': 'Money'}


def test_empty_when_all_unknown():
    db = import_pipeline._build_simple_download_tag_data(
        {'title': 'Unknown', 'artist': 'Unknown Artist'}, None,
    )
    assert db == {}


def test_strips_whitespace():
    db = import_pipeline._build_simple_download_tag_data(
        {'title': '  Money  ', 'artist': 'Pink Floyd'}, None,
    )
    assert db['title'] == 'Money'


# --- wiring: the branch actually calls the tag writer ---

def test_simple_download_branch_writes_known_tags(tmp_path, monkeypatch):
    import sys
    import core.imports.paths as import_paths
    import core.runtime_state as runtime_state

    transfer_root = tmp_path / "Transfer"
    transfer_root.mkdir()
    source_path = tmp_path / "source.flac"
    source_path.write_bytes(b"audio")

    context_key = "ctx-tags"
    context = {
        "search_result": {
            "is_simple_download": True,
            "filename": "Money.flac",
            "title": "Money",
            "artist": "Pink Floyd",
        },
        "track_info": {},
        "original_search_result": {},
        "is_album_download": False,
        "task_id": "t1",
        "batch_id": "b1",
    }

    class _Config:
        def get(self, key, default=None):
            if key == "soulseek.transfer_path":
                return str(transfer_root)
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

    fake_acoustid = types.ModuleType("core.acoustid_verification")
    fake_acoustid.AcoustIDVerification = _FakeAcoustidVerifier
    fake_acoustid.VerificationResult = types.SimpleNamespace(FAIL="FAIL")
    monkeypatch.setitem(sys.modules, "core.acoustid_verification", fake_acoustid)

    from core.imports.file_integrity import IntegrityResult
    monkeypatch.setattr(import_pipeline, "check_audio_integrity",
                        lambda *_a, **_kw: IntegrityResult(ok=True, checks={"size_bytes": 5}))
    monkeypatch.setattr(import_paths, "_get_config_manager", lambda: _Config())
    monkeypatch.setattr(import_pipeline, "add_activity_item", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "emit_track_downloaded", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "record_library_history_download", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "record_download_provenance", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline, "check_and_remove_from_wishlist", lambda c: None)
    monkeypatch.setattr(import_pipeline, "_persist_verification_status", lambda *a, **kw: None)
    monkeypatch.setattr(import_pipeline.threading, "Thread", _ImmediateThread)

    tag_calls = []
    monkeypatch.setattr(import_pipeline, "write_tags_to_file",
                        lambda path, db_data, **kw: tag_calls.append((path, db_data, kw)) or {"success": True})

    runtime = types.SimpleNamespace(
        automation_engine=None,
        on_download_completed=lambda *a, **kw: None,
        web_scan_manager=None,
        repair_worker=None,
    )

    runtime_state.matched_downloads_context.clear()
    runtime_state.post_process_locks.clear()
    runtime_state.matched_downloads_context[context_key] = context

    import_pipeline.post_process_matched_download(context_key, context, str(source_path), runtime)

    assert len(tag_calls) == 1
    path, db_data, kw = tag_calls[0]
    assert db_data == {'title': 'Money', 'artist_name': 'Pink Floyd'}
    assert kw.get('embed_cover') is False
