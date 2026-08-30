"""Intentional output transforms must not create endless quality upgrades."""

from __future__ import annotations

import json

from core.quality.model import AudioQuality, QualityTarget
from core.quality.retention import (
    acquired_quality_from_json,
    evaluation_qualities,
    quality_json,
    retention_meets_profile,
    transforms_json,
)


HIRES = QualityTarget(
    label="FLAC 24-bit/96kHz", format="flac", bit_depth=24,
    min_sample_rate=96_000,
)


def test_downsampled_file_uses_acquired_hires_quality_for_upgrade_policy():
    measured = AudioQuality(format="flac", bit_depth=16, sample_rate=44_100)
    acquired = AudioQuality(format="flac", bit_depth=24, sample_rate=96_000)
    retention = transforms_json([{
        "type": "downsample_hires_flac", "source_replaced": True,
    }])

    assert retention_meets_profile(
        measured, [HIRES], acquired_quality_json=quality_json(acquired),
        retention_json=retention,
    ) is True


def test_unproven_acquired_quality_never_suppresses_a_real_upgrade():
    measured = AudioQuality(format="flac", bit_depth=16, sample_rate=44_100)
    acquired = AudioQuality(format="flac", bit_depth=24, sample_rate=96_000)

    assert retention_meets_profile(
        measured, [HIRES], acquired_quality_json=quality_json(acquired),
        retention_json=None,
    ) is False
    assert evaluation_qualities(measured, "not-json", "not-json") == [measured]


def test_lossy_only_retention_can_satisfy_lossless_or_lossy_profile():
    measured = AudioQuality(format="mp3", bitrate=320)
    acquired = AudioQuality(format="flac", bit_depth=24, sample_rate=96_000)
    retention = transforms_json([{
        "type": "lossy_copy", "source_replaced": True,
    }])

    assert retention_meets_profile(
        measured, [HIRES], acquired_quality_json=quality_json(acquired),
        retention_json=retention,
    ) is True
    assert retention_meets_profile(
        measured, [QualityTarget(label="MP3 320", format="mp3", min_bitrate=320)],
        acquired_quality_json=quality_json(acquired), retention_json=retention,
    ) is True


def test_quality_provenance_round_trip_is_stable_and_typed():
    quality = AudioQuality(
        format="flac", bitrate=4608, sample_rate=96_000, bit_depth=24)
    encoded = quality_json(quality)

    assert encoded == json.dumps(
        quality.to_dict(), sort_keys=True, separators=(",", ":"))
    assert acquired_quality_from_json(encoded) == quality


def test_pipeline_keeps_lossless_primary_when_lossy_companion_is_retained(
        tmp_path, monkeypatch):
    import core.imports.pipeline as pipeline

    source = tmp_path / "track.flac"
    lossy = tmp_path / "track.opus"
    source.write_bytes(b"lossless")
    lossy.write_bytes(b"lossy")
    qualities = iter([
        AudioQuality(format="flac", bit_depth=24, sample_rate=96_000),
        AudioQuality(format="opus", bitrate=256),
    ])
    monkeypatch.setattr(pipeline, "probe_audio_quality", lambda _path: next(qualities))
    monkeypatch.setattr(pipeline, "downsample_hires_flac", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "create_lossy_copy", lambda *_a, **_k: str(lossy))
    monkeypatch.setattr(pipeline, "_persist_verification_status", lambda *_a: None)
    context = {"_final_processed_path": str(source)}

    result = pipeline._apply_profile_output_transforms(
        str(source), context, {
            "lossy_copy_enabled": True,
            "lossy_copy_codec": "opus",
            "lossy_copy_bitrate": "256",
            "lossy_copy_delete_original": False,
        })

    assert result == str(source)
    assert context["_final_processed_path"] == str(source)
    assert context["_companion_file_paths"] == [str(lossy)]
    assert context["_retention_transforms"][-1]["source_replaced"] is False


def test_pipeline_records_destructive_lossy_retention(tmp_path, monkeypatch):
    import core.imports.pipeline as pipeline

    source = tmp_path / "track.flac"
    lossy = tmp_path / "track.mp3"
    source.write_bytes(b"lossless")
    lossy.write_bytes(b"lossy")
    qualities = iter([
        AudioQuality(format="flac", bit_depth=24, sample_rate=96_000),
        AudioQuality(format="mp3", bitrate=320),
    ])

    def _convert(*_args, **_kwargs):
        source.unlink()
        return str(lossy)

    monkeypatch.setattr(pipeline, "probe_audio_quality", lambda _path: next(qualities))
    monkeypatch.setattr(pipeline, "downsample_hires_flac", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "create_lossy_copy", _convert)
    monkeypatch.setattr(pipeline, "_persist_verification_status", lambda *_a: None)
    context = {"_final_processed_path": str(source)}

    result = pipeline._apply_profile_output_transforms(
        str(source), context, {
            "lossy_copy_enabled": True,
            "lossy_copy_codec": "mp3",
            "lossy_copy_bitrate": "320",
            "lossy_copy_delete_original": True,
        })

    assert result == str(lossy)
    assert context["_final_processed_path"] == str(lossy)
    assert context["_acquired_audio_quality"]["bit_depth"] == 24
    assert context["_retention_transforms"][-1]["source_replaced"] is True


def test_pipeline_records_hires_downsample_as_destructive_retention(
        tmp_path, monkeypatch):
    import core.imports.pipeline as pipeline

    source = tmp_path / "track.flac"
    source.write_bytes(b"audio")
    qualities = iter([
        AudioQuality(format="flac", bit_depth=24, sample_rate=96_000),
        AudioQuality(format="flac", bit_depth=16, sample_rate=44_100),
    ])
    monkeypatch.setattr(pipeline, "probe_audio_quality", lambda _path: next(qualities))
    monkeypatch.setattr(
        pipeline, "downsample_hires_flac", lambda *_a, **_k: str(source))
    monkeypatch.setattr(pipeline, "create_lossy_copy", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_persist_verification_status", lambda *_a: None)
    context = {"_final_processed_path": str(source)}

    result = pipeline._apply_profile_output_transforms(
        str(source), context, {"downsample_enabled": True})

    assert result == str(source)
    assert context["_acquired_audio_quality"]["sample_rate"] == 96_000
    assert context["_retention_transforms"] == [{
        "type": "downsample_hires_flac",
        "source_replaced": True,
        "target_bit_depth": 16,
        "target_sample_rate": 44_100,
        "output_quality": {
            "format": "flac", "bit_depth": 16, "sample_rate": 44_100,
        },
    }]
