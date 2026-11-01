from __future__ import annotations

import threading

import core.imports.pipeline as pipeline
from core.imports.upgrade_intent import (
    attach_upgrade_intent,
    get_upgrade_intent,
    issue_upgrade_intent,
    sanitize_client_import_metadata,
)
from core.quality.model import AudioQuality


def test_client_json_cannot_mint_upgrade_authority():
    payload = sanitize_client_import_metadata({
        "title": "safe",
        "quality_profile_id": 999,
        "lib2_entity": {"track_id": 4},
        "source_info": {"upgrade_check": True, "lib2_track_id": 4},
        "nested": [{"lib2_track_id": 5, "name": "kept"}],
    })
    assert payload == {"title": "safe", "nested": [{"name": "kept"}]}
    assert get_upgrade_intent(payload) is None


def test_only_sealed_python_intent_is_accepted():
    context = {"_server_library_v2_upgrade_intent": {
        "track_id": 8, "origin": "forged",
    }}
    assert get_upgrade_intent(context) is None
    assert attach_upgrade_intent(
        context, issue_upgrade_intent(8, origin="server"))
    assert get_upgrade_intent(context).track_id == 8


def test_upgrade_snapshot_cas_detects_primary_change(monkeypatch):
    expected = pipeline._UpgradeSnapshot(
        4, 10, "/old.flac", "/old.flac", {"id": 2}, "same")
    changed = pipeline._UpgradeSnapshot(
        4, 11, "/winner.flac", "/winner.flac", {"id": 2}, "same")
    monkeypatch.setattr(pipeline, "_load_upgrade_snapshot", lambda _track: changed)
    assert pipeline._upgrade_snapshot_still_current(expected) is False


def test_snapshot_rejects_cross_format_same_rank_tier_only_upgrade(monkeypatch):
    from core.quality.model import AudioQuality

    snapshot = pipeline._UpgradeSnapshot(
        4, 10, "/old.mp3", "/old.mp3", {
            "id": 2,
            "ranked_targets": [{"format": "flac", "bit_depth": 24}, {}],
            "fallback_enabled": True,
            "upgrade_policy": "until_cutoff",
            "upgrade_cutoff_index": 0,
        }, "same")
    qualities = {
        "/old.mp3": AudioQuality("mp3", 128, None, None),
        "/new.ogg": AudioQuality("ogg", 320, None, None),
    }
    monkeypatch.setattr(
        "core.imports.file_ops.probe_audio_quality", lambda path: qualities[path])

    assert pipeline._decide_snapshot_upgrade(snapshot, "/new.ogg").allowed is False


def test_upgrade_transform_returns_exact_lossy_artifact_when_original_deleted(
    tmp_path, monkeypatch,
):
    source = tmp_path / "incoming.flac"
    source.write_bytes(b"lossless")
    lossy = tmp_path / "incoming.mp3"

    monkeypatch.setattr(pipeline, "downsample_hires_flac", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "core.imports.file_ops.probe_audio_quality",
        lambda _path: AudioQuality(
            "flac", sample_rate=44100, bit_depth=16,
        ),
    )
    monkeypatch.setattr(
        "core.quality.lossless.is_lossless_audio_path", lambda _path: True)

    def convert(path, settings=None):
        lossy.write_bytes(b"lossy")
        source.unlink()
        return str(lossy)

    monkeypatch.setattr(pipeline, "create_lossy_copy", convert)
    monkeypatch.setattr(pipeline, "get_audio_quality_string", lambda _p: "MP3-320")
    context = {}
    retained, companions = pipeline._prepare_upgrade_artifact(
        str(source), context, {
            "downsample_enabled": False,
            "lossy_copy_enabled": True,
            "lossy_copy_codec": "mp3",
            "lossy_copy_bitrate": "320",
            "lossy_copy_delete_original": True,
        })
    assert retained == str(lossy)
    assert companions == []
    assert context["_audio_quality"] == "MP3-320"


def test_upgrade_transform_compares_downsampled_staging_file(tmp_path, monkeypatch):
    source = tmp_path / "incoming.flac"
    source.write_bytes(b"hires")
    quality = AudioQuality("flac", sample_rate=96000, bit_depth=24)
    monkeypatch.setattr(
        "core.imports.file_ops.probe_audio_quality", lambda _path: quality)

    def downsample(path, _context, enabled=None):
        assert enabled is True
        source.write_bytes(b"downsampled")
        return path

    monkeypatch.setattr(pipeline, "downsample_hires_flac", downsample)
    monkeypatch.setattr(pipeline, "create_lossy_copy", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "get_audio_quality_string", lambda _p: "FLAC 16bit")
    retained, companions = pipeline._prepare_upgrade_artifact(
        str(source), {}, {
            "downsample_enabled": True,
            "lossy_copy_enabled": False,
        })
    assert retained == str(source)
    assert source.read_bytes() == b"downsampled"
    assert companions == []


def test_per_track_upgrade_lock_serializes_workers():
    first = pipeline._claim_upgrade_lock(404)
    entered = threading.Event()

    def contender():
        second = pipeline._claim_upgrade_lock(404)
        entered.set()
        pipeline._release_upgrade_lock(404, second)

    thread = threading.Thread(target=contender)
    thread.start()
    assert not entered.wait(0.05)
    pipeline._release_upgrade_lock(404, first)
    assert entered.wait(1)
    thread.join(timeout=1)
