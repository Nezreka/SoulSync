"""#1270: replacing library audio is ranked against the quality profile.

Upstream's #1270 has two halves. The finder route (a wishlist item carrying
``source_info.job == 'quality_upgrade'``) does not exist on this branch: a
Library v2 upgrade travels as a server-issued upgrade intent
(``core.imports.upgrade_intent``) and is covered by its own tests. What does
apply here is the ranking itself, which now decides the "replace lower
quality" setting instead of a guess from the file extension.

Real import pipeline and atomic file move; only external metadata services and
audio probes are faked, so the destination bytes prove which copy survived.
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


def test_replace_lower_setting_is_bitrate_aware(import_case):
    c = import_case
    c.context["track_info"].pop("source_info")
    c.profile["replace_lower_quality"] = True
    c.run()
    assert c.existing.read_bytes() == b"better audio"


@pytest.mark.parametrize("old_bitrate,new_bitrate", [(128, 192), (192, 256), (256, 320)])
def test_each_mp3_target_can_replace_the_next_lower_one(import_case, old_bitrate, new_bitrate):
    c = import_case
    c.context["track_info"].pop("source_info")
    c.profile["replace_lower_quality"] = True
    c.old_quality = AudioQuality("mp3", old_bitrate)
    c.new_quality = AudioQuality("mp3", new_bitrate)
    c.run()
    assert c.existing.read_bytes() == b"better audio"


@pytest.mark.parametrize("quality", [
    AudioQuality("mp3", 128), AudioQuality("mp3", 64),
    # off-profile: a FLAC must never replace the MP3 an MP3-only profile asked for
    AudioQuality("flac", sample_rate=44100, bit_depth=16),
    AudioQuality("mp3"), None,
])
def test_replace_lower_never_takes_equal_worse_unwanted_or_unknown_audio(import_case, quality):
    c = import_case
    c.context["track_info"].pop("source_info")
    c.profile["replace_lower_quality"] = True
    c.new_quality = quality
    c.run()
    assert c.existing.read_bytes() == b"old audio"


def test_unknown_existing_bitrate_is_not_evidence_of_improvement(import_case):
    c = import_case
    c.context["track_info"].pop("source_info")
    c.profile["replace_lower_quality"] = True
    c.old_quality = AudioQuality("mp3")
    c.run()
    assert c.existing.read_bytes() == b"old audio"


def test_probe_exception_cannot_fall_through_to_overwrite(import_case, monkeypatch):
    c = import_case
    c.context["track_info"].pop("source_info")
    c.profile["replace_lower_quality"] = True
    def broken_probe(path):
        raise OSError("unreadable")
    monkeypatch.setattr("core.imports.quality_replace.probe_audio_quality", broken_probe)
    c.run()
    assert c.existing.read_bytes() == b"old audio"


@pytest.mark.parametrize("job", [None, "discography_backfill", "dead_files", "quality_upgrade"])
def test_an_ordinary_wishlist_download_cannot_overwrite(import_case, job):
    """replace_lower off: no job name -- not even upstream's finder one --
    turns a download into an overwrite on this branch."""
    c = import_case
    c.context["track_info"]["source_info"] = {"job": job}
    c.run()
    assert c.existing.read_bytes() == b"old audio"


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
