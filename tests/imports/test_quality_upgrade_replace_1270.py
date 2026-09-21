"""#1270: finder-approved MP3 upgrades must survive wishlist import.

Use the real import pipeline and atomic file move; fake only external
metadata services and audio probes so the destination bytes prove which
copy survived. Ordinary wishlist downloads must retain their protection.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from mutagen import File as read_audio

from core.imports import pipeline, paths
from core.imports.file_integrity import IntegrityResult
from core.imports.file_integrity import check_audio_integrity as real_integrity_check
from core.imports.file_ops import probe_audio_quality as real_quality_probe
from core.quality.model import AudioQuality

_real_length = pipeline._audio_length_seconds


@pytest.fixture
def import_case(tmp_path, monkeypatch):
    album_dir = tmp_path / "library" / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    existing = album_dir / "01 - Song.mp3"
    incoming = tmp_path / "download.mp3"
    existing.write_bytes(b"old audio")
    incoming.write_bytes(b"better audio")
    profile = {
        "ranked_targets": [
            {"format": "mp3", "min_bitrate": br} for br in (320, 256, 192)
        ],
        "replace_lower_quality": False,
        "deep_audio_verify": False,
        "lossy_copy_enabled": False,
        "downsample_hires": False,
    }
    cfg = SimpleNamespace(get=lambda key, default=None: {
        "soulseek.transfer_path": str(tmp_path / "library"),
        "soulseek.download_path": str(tmp_path / "downloads"),
        "post_processing.replaygain_enabled": False,
    }.get(key, default), get_active_media_server=lambda: None)
    context = {
        "track_info": {
            "id": "track", "name": "Song", "artists": [{"name": "Artist"}],
            "album": {"name": "Album"}, "track_number": 1,
            "quality_profile_id": 17,
            "source_info": {"job": "quality_upgrade",
                            "original_file_path": str(existing)},
        },
        "original_search_result": {"title": "Song", "album": "Album"},
        "_quality_profile": profile,
        "is_album_download": False,
    }
    case = SimpleNamespace(existing=existing, incoming=incoming, context=context,
                           profile=profile, old_quality=AudioQuality("mp3", 128),
                           new_quality=AudioQuality("mp3", 320), has_metadata=True)
    monkeypatch.setattr(pipeline, "config_manager", cfg)
    monkeypatch.setattr(paths, "_get_config_manager", lambda: cfg)
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_: None)
    monkeypatch.setattr(pipeline, "get_import_context_artist", lambda _: {"name": "Artist"})
    monkeypatch.setattr(pipeline, "get_import_has_clean_metadata", lambda _: True)
    monkeypatch.setattr(pipeline, "build_import_album_info", lambda *a, **k: {
        "is_album": True, "album_name": "Album", "track_number": 1,
        "disc_number": 1, "clean_track_name": "Song", "source": "spotify",
    })
    monkeypatch.setattr(pipeline, "resolve_album_group", lambda a, b, c: "Album")
    monkeypatch.setattr(pipeline, "get_import_clean_title", lambda *a, **k: "Song")
    monkeypatch.setattr(pipeline, "get_audio_quality_string", lambda *a, **k: "")
    monkeypatch.setattr(pipeline, "check_quality_target", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "check_audio_integrity",
                        lambda *a, **k: IntegrityResult(ok=True, checks={}))
    monkeypatch.setattr(pipeline, "_audio_length_seconds", lambda _: 200.0)
    monkeypatch.setattr("mutagen.File", lambda _: SimpleNamespace(
        tags={"title": "Song", "artist": "Artist", "album": "Album"}
        if case.has_metadata else {}))
    # Both the real quality-upgrade decision and ordinary imports use this probe.
    monkeypatch.setattr("core.imports.quality_replace.probe_audio_quality",
                        lambda path: case.new_quality if Path(path).read_bytes() == b"better audio"
                        else case.old_quality)
    for name in ("enhance_file_metadata", "download_cover_art", "generate_lrc_file",
                 "cleanup_empty_directories", "cleanup_slskd_dedup_siblings",
                 "emit_track_downloaded", "record_library_history_download",
                 "record_download_provenance", "record_soulsync_library_entry",
                 "check_and_remove_from_wishlist"):
        monkeypatch.setattr(pipeline, name, lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "build_final_path_for_track",
                        lambda *a, **k: (str(case.existing), True))
    monkeypatch.setattr(pipeline, "matched_downloads_context", {})
    monkeypatch.setattr(pipeline, "post_process_locks", {})
    monkeypatch.setattr(pipeline, "processed_download_ids", set())
    def run():
        pipeline.post_process_matched_download(
            "upgrade-1270", context, str(case.incoming), SimpleNamespace())
    case.run = run
    return case


@pytest.mark.parametrize("encoded", [False, True])
def test_finder_wishlist_upgrade_replaces_mp3_with_global_replace_off(import_case, encoded):
    c = import_case
    if encoded:
        c.context["track_info"]["source_info"] = json.dumps(
            c.context["track_info"]["source_info"])
    c.run()
    assert c.existing.read_bytes() == b"better audio"
    assert not c.incoming.exists()
    assert c.context.get("_pipeline_import_succeeded") is True


def test_replace_lower_setting_is_bitrate_aware_without_finder_intent(import_case):
    c = import_case
    c.context["track_info"].pop("source_info")
    c.profile["replace_lower_quality"] = True
    c.run()
    assert c.existing.read_bytes() == b"better audio"


@pytest.mark.parametrize("job", [None, "discography_backfill", "dead_files"])
def test_ordinary_wishlist_download_still_cannot_overwrite(import_case, job):
    c = import_case
    c.context["track_info"]["source_info"] = {"job": job}
    c.run()
    assert c.existing.read_bytes() == b"old audio"


@pytest.mark.parametrize("quality", [
    AudioQuality("mp3", 128), AudioQuality("mp3", 64),
    AudioQuality("flac", sample_rate=44100, bit_depth=16),
    AudioQuality("mp3"), None,
])
@pytest.mark.parametrize("has_metadata", [True, False])
def test_upgrade_never_replaces_with_equal_worse_unwanted_or_unknown_audio(
        import_case, quality, has_metadata):
    c = import_case
    c.new_quality = quality
    c.has_metadata = has_metadata
    c.run()
    assert c.existing.read_bytes() == b"old audio"


def test_upgrade_retains_length_guard(import_case, monkeypatch):
    c = import_case
    monkeypatch.setattr(pipeline, "_audio_length_seconds",
                        lambda path: 30.0 if path == str(c.incoming) else 200.0)
    monkeypatch.setattr(pipeline, "move_to_quarantine", lambda *a, **k: None)
    c.run()
    assert c.existing.read_bytes() == b"old audio"
    assert not c.context.get("_pipeline_import_succeeded")


def test_upgrade_retains_integrity_guard(import_case, monkeypatch):
    c = import_case
    monkeypatch.setattr(pipeline, "check_audio_integrity",
                        lambda *a, **k: IntegrityResult(ok=False, reason="broken", checks={}))
    monkeypatch.setattr(pipeline, "move_to_quarantine", lambda *a, **k: None)
    c.run()
    assert c.existing.read_bytes() == b"old audio"
    assert c.context["_integrity_failure_msg"] == "broken"


def test_upgrade_failure_to_publish_keeps_original(import_case, monkeypatch):
    c = import_case
    def fail_move(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(pipeline, "safe_move_file", fail_move)
    c.run()
    assert c.existing.read_bytes() == b"old audio"


def test_upgrade_uses_item_profile_not_global_default(import_case, monkeypatch):
    c = import_case
    c.context.pop("_quality_profile")
    seen = []
    def load_profile(profile_id):
        seen.append(profile_id)
        return c.profile if profile_id == 17 else {
            "ranked_targets": [{"format": "flac"}]}
    monkeypatch.setattr("core.quality.selection.load_profile_by_id", load_profile)
    c.run()
    assert seen == [17]
    assert c.existing.read_bytes() == b"better audio"


def test_upgrade_does_not_retire_original_into_private_album_staging(import_case, monkeypatch):
    c = import_case
    calls = []
    monkeypatch.setattr(pipeline, "_maybe_stage_album_track",
                        lambda context, final: calls.append(final) or str(c.existing) + ".staged")
    c.run()
    assert calls == []
    assert c.existing.read_bytes() == b"better audio"


@pytest.mark.parametrize("old_bitrate,new_bitrate", [(128, 192), (192, 256), (256, 320)])
def test_each_mp3_target_can_upgrade_the_next_lower_one(import_case, old_bitrate, new_bitrate):
    c = import_case
    c.old_quality = AudioQuality("mp3", old_bitrate)
    c.new_quality = AudioQuality("mp3", new_bitrate)
    c.run()
    assert c.existing.read_bytes() == b"better audio"


def test_unknown_existing_bitrate_is_not_evidence_of_improvement(import_case):
    c = import_case
    c.old_quality = AudioQuality("mp3")
    c.run()
    assert c.existing.read_bytes() == b"old audio"
    assert c.context.get("_context_failure_msg")


def test_probe_exception_cannot_fall_through_to_overwrite(import_case, monkeypatch):
    c = import_case
    def broken_probe(path):
        raise OSError("unreadable")
    monkeypatch.setattr("core.imports.quality_replace.probe_audio_quality", broken_probe)
    c.run()
    assert c.existing.read_bytes() == b"old audio"


@pytest.mark.parametrize("short,move_fails", [(False, False), (True, False), (False, True)])
def test_changed_extension_retires_original_only_after_safe_publish(
        import_case, monkeypatch, short, move_fails):
    c = import_case
    c.incoming = c.incoming.rename(c.incoming.with_suffix(".flac"))
    c.new_quality = AudioQuality("flac", sample_rate=44100, bit_depth=16)
    c.profile["ranked_targets"].insert(0, {"format": "flac"})
    final = c.existing.with_suffix(".flac")
    monkeypatch.setattr(pipeline, "build_final_path_for_track",
                        lambda *a, **k: (str(final), True))
    monkeypatch.setattr(pipeline, "move_to_quarantine", lambda *a, **k: None)
    if short:
        monkeypatch.setattr(pipeline, "_audio_length_seconds",
                            lambda path: 30.0 if path == str(c.incoming) else 200.0)
    if move_fails:
        def fail_move(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(pipeline, "safe_move_file", fail_move)
    c.run()
    if short or move_fails:
        assert c.existing.read_bytes() == b"old audio"
        assert not final.exists()
    else:
        assert not c.existing.exists()
        assert final.read_bytes() == b"better audio"


def test_repair_to_wishlist_roundtrip_preserves_per_track_upgrade_intent(import_case):
    from core.repair_worker import RepairWorker
    from core.wishlist.service import WishlistService

    c = import_case
    class DB:
        def add_to_wishlist(self, **kw):
            self.kw = kw
            return True

        def get_wishlist_tracks(self, **kw):
            return [{
                "id": 1, "spotify_track_id": "track",
                "track_data": self.kw["spotify_track_data"],
                "source_type": self.kw["source_type"],
                "source_info": json.dumps(self.kw["source_info"]),
                "quality_profile_id": self.kw["quality_profile_id"],
                "failure_reason": "upgrade", "retry_count": 0,
                "date_added": "", "last_attempted": None,
            }]
    db = DB()
    worker = object.__new__(RepairWorker)
    worker.db = db
    result = worker._fix_quality_upgrade("track", 1, str(c.existing), {
        "matched_track_data": c.context["track_info"],
        "current_format": "mp3", "current_bitrate": 128,
        "quality_profile_id": 17,
    })
    assert result["success"]
    service = WishlistService()
    service._database = db
    c.context["track_info"] = service.get_wishlist_tracks_for_download()[0]
    c.run()
    assert c.existing.read_bytes() == b"better audio"


def test_finder_reuses_original_filename_instead_of_creating_sibling(tmp_path, monkeypatch):
    original = tmp_path / "library" / "Artist" / "Album" / "07 original name.mp3"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"old")
    monkeypatch.setattr(paths, "_get_config_manager", lambda: SimpleNamespace(
        get=lambda key, default=None: str(tmp_path / "library")
        if key == "soulseek.transfer_path" else default,
        get_active_media_server=lambda: None))
    context = {"track_info": {"name": "Different metadata title",
                             "source_info": {"job": "quality_upgrade",
                                             "original_file_path": str(original)}}}
    actual, _ = paths.build_final_path_for_track(
        context, {"name": "Artist"},
        {"is_album": True, "album_name": "Album", "track_number": 1}, ".mp3")
    assert actual == str(original)


def test_real_encoded_mp3_128_is_replaced_by_mp3_320(import_case, monkeypatch):
    """Exercise actual media headers, integrity, length, ranking and file move."""
    c = import_case
    ffmpeg = shutil.which("ffmpeg")
    bundled = Path(__file__).resolve().parents[2] / "tools" / "ffmpeg"
    if not ffmpeg and bundled.is_file():
        ffmpeg = str(bundled)
    if not ffmpeg:
        pytest.skip("ffmpeg required for real MP3 encoding")
    for path, bitrate in ((c.existing, 128), (c.incoming, 320)):
        subprocess.run([
            ffmpeg, "-v", "error", "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=12", "-ar", "44100",
            "-ac", "2", "-c:a", "libmp3lame", "-b:a", f"{bitrate}k",
            "-metadata", "title=Song", "-metadata", "artist=Artist",
            "-metadata", "album=Album", str(path),
        ], check=True, capture_output=True, timeout=30)
    monkeypatch.setattr("mutagen.File", read_audio)
    monkeypatch.setattr("core.imports.quality_replace.probe_audio_quality", real_quality_probe)
    monkeypatch.setattr(pipeline, "check_audio_integrity", real_integrity_check)
    monkeypatch.setattr(pipeline, "_audio_length_seconds", _real_length)
    assert real_quality_probe(str(c.existing)).bitrate == 128
    c.run()
    assert c.context.get("_pipeline_import_succeeded"), c.context.get("_context_failure_msg")
    assert real_quality_probe(str(c.existing)).bitrate == 320
    assert not c.incoming.exists()
