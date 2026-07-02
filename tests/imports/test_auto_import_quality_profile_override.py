"""Auto-Import can be assigned its own quality profile (Settings -> Import ->
Auto-Import), independent of the app-wide default used by normal downloads /
Wishlist items. `_process_matches` must inject `quality_profile_id` onto
`track_info` — everything profile-specific (quality gate, AcoustID
strictness, downsample/lossy-copy) is then resolved LIVE from that id at each
pipeline stage (`core/imports/pipeline.py::_resolve_context_quality_profile`).
Deliberately NO `_skip_quarantine_check` injection: the profile's
`acoustid_required` is a strictness dial enforced inside the pipeline, not an
on/off switch for running AcoustID at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest


@dataclass
class _FakeCandidate:
    path: str
    name: str
    audio_files: List[str] = field(default_factory=list)
    disc_structure: Dict[int, List[str]] = field(default_factory=dict)
    folder_hash: str = "fake-hash"
    is_single: bool = False


def _worker_with_capture(tmp_path, config_overrides):
    from core.auto_import_worker import AutoImportWorker

    captured: List[Dict[str, Any]] = []
    fake_db = MagicMock()
    fake_cfg = MagicMock()
    fake_cfg.get.side_effect = lambda key, default=None: config_overrides.get(key, default)

    def _capture(_key, ctx, _path):
        captured.append(ctx)

    worker = AutoImportWorker(
        database=fake_db,
        staging_path=str(tmp_path),
        transfer_path=str(tmp_path / "transfer"),
        process_callback=_capture,
        config_manager=fake_cfg,
        automation_engine=None,
    )
    worker._captured = captured
    return worker


def _run_one_track(worker, tmp_path):
    f = tmp_path / "01.flac"
    f.write_bytes(b"audio")
    cand = _FakeCandidate(path=str(tmp_path), name="Album")
    ident = {
        "source": "spotify", "artist_name": "A", "artist_id": "AID",
        "album_name": "Album", "album_id": "ALID", "image_url": "",
        "release_date": "2024-01-01", "method": "tags",
    }
    mr = {
        "matches": [{
            "track": {"id": "t1", "name": "Track", "track_number": 1,
                      "disc_number": 1, "duration_ms": 200000,
                      "artists": [{"name": "A"}]},
            "file": str(f), "confidence": 0.95,
        }],
        "unmatched_files": [], "total_tracks": 1, "matched_count": 1,
        "confidence": 0.95,
        "album_data": {"id": "ALID", "total_tracks": 1, "album_type": "album",
                       "release_date": "2024-01-01", "images": [],
                       "artists": [{"name": "A", "id": "AID"}]},
    }
    worker._process_matches(cand, ident, mr)
    return worker._captured[0]


def test_no_override_configured_leaves_context_unchanged(tmp_path):
    worker = _worker_with_capture(tmp_path, {})
    ctx = _run_one_track(worker, tmp_path)
    assert "quality_profile_id" not in ctx["track_info"]
    assert "_skip_quarantine_check" not in ctx


def test_configured_profile_id_injected_into_track_info(tmp_path, monkeypatch):
    from core.quality import selection as selection_mod
    monkeypatch.setattr(
        selection_mod, "load_profile_by_id",
        lambda pid: {"acoustid_required": True},
    )
    worker = _worker_with_capture(tmp_path, {"auto_import.quality_profile_id": 7})
    ctx = _run_one_track(worker, tmp_path)
    assert ctx["track_info"]["quality_profile_id"] == 7
    # acoustid_required True on the profile -> no skip.
    assert "_skip_quarantine_check" not in ctx


def test_lenient_profile_does_not_skip_acoustid_entirely(tmp_path, monkeypatch):
    """acoustid_required=False means LENIENT (unverified files import with the
    warning badge instead of being quarantined) — it must NOT disable the
    AcoustID check altogether. The strictness is enforced inside the pipeline
    from the profile; no skip flag may be set here."""
    from core.quality import selection as selection_mod
    monkeypatch.setattr(
        selection_mod, "load_profile_by_id",
        lambda pid: {"acoustid_required": False},
    )
    worker = _worker_with_capture(tmp_path, {"auto_import.quality_profile_id": 3})
    ctx = _run_one_track(worker, tmp_path)
    assert ctx["track_info"]["quality_profile_id"] == 3
    assert "_skip_quarantine_check" not in ctx


def test_profile_resolution_failure_is_non_fatal(tmp_path, monkeypatch):
    from core.quality import selection as selection_mod

    def _boom(pid):
        raise RuntimeError("db down")

    monkeypatch.setattr(selection_mod, "load_profile_by_id", _boom)
    worker = _worker_with_capture(tmp_path, {"auto_import.quality_profile_id": 3})
    # Must not raise — the batch still processes; the pipeline re-resolves
    # the id itself (with its own fallback) at each stage.
    ctx = _run_one_track(worker, tmp_path)
    assert ctx["track_info"]["quality_profile_id"] == 3
    assert "_skip_quarantine_check" not in ctx


def _run_one_track_in_folder(worker, tmp_path, folder_name):
    """Like `_run_one_track` but stages the file under
    `<tmp_path>/<folder_name>/AlbumFolder/` so `resolve_folder_artist` has the
    >=2 path segments (Artist/Album) it needs (the flat `_run_one_track`
    layout has 0 segments, so it never exercises the override)."""
    album_dir = tmp_path / folder_name / "AlbumFolder"
    album_dir.mkdir(parents=True)
    f = album_dir / "01.flac"
    f.write_bytes(b"audio")
    cand = _FakeCandidate(path=str(album_dir), name="Album")
    ident = {
        "source": "spotify", "artist_name": "Tag Artist", "artist_id": "AID",
        "album_name": "Album", "album_id": "ALID", "image_url": "",
        "release_date": "2024-01-01", "method": "tags",
    }
    mr = {
        "matches": [{
            "track": {"id": "t1", "name": "Track", "track_number": 1,
                      "disc_number": 1, "duration_ms": 200000,
                      "artists": [{"name": "Tag Artist"}]},
            "file": str(f), "confidence": 0.95,
        }],
        "unmatched_files": [], "total_tracks": 1, "matched_count": 1,
        "confidence": 0.95,
        "album_data": {"id": "ALID", "total_tracks": 1, "album_type": "album",
                       "release_date": "2024-01-01", "images": [],
                       "artists": [{"name": "Tag Artist", "id": "AID"}]},
    }
    worker._process_matches(cand, ident, mr)
    return worker._captured[0]


def test_folder_artist_override_defaults_on_when_no_profile_assigned(tmp_path, monkeypatch):
    # No `auto_import.quality_profile_id` configured -> `load_profile_by_id`
    # is called with `None` and falls back to the app-wide default profile
    # (simulated here with a bare dict — `folder_artist_override` is absent,
    # so the `.get(..., True)` default applies, same as a real default row).
    from core.quality import selection as selection_mod
    monkeypatch.setattr(selection_mod, "load_profile_by_id", lambda pid: {})
    worker = _worker_with_capture(tmp_path, {})
    ctx = _run_one_track_in_folder(worker, tmp_path, "Folder Artist")
    assert ctx["spotify_artist"]["name"] == "Folder Artist"


def test_folder_artist_override_disabled_on_assigned_profile_keeps_tag_artist(tmp_path, monkeypatch):
    from core.quality import selection as selection_mod
    monkeypatch.setattr(
        selection_mod, "load_profile_by_id",
        lambda pid: {"acoustid_required": True, "folder_artist_override": False},
    )
    worker = _worker_with_capture(tmp_path, {"auto_import.quality_profile_id": 5})
    ctx = _run_one_track_in_folder(worker, tmp_path, "Folder Artist")
    assert ctx["spotify_artist"]["name"] == "Tag Artist"


def test_folder_artist_override_enabled_on_assigned_profile_uses_folder_name(tmp_path, monkeypatch):
    from core.quality import selection as selection_mod
    monkeypatch.setattr(
        selection_mod, "load_profile_by_id",
        lambda pid: {"acoustid_required": True, "folder_artist_override": True},
    )
    worker = _worker_with_capture(tmp_path, {"auto_import.quality_profile_id": 5})
    ctx = _run_one_track_in_folder(worker, tmp_path, "Folder Artist")
    assert ctx["spotify_artist"]["name"] == "Folder Artist"
