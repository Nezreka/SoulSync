"""Album preflight and profile AcoustID policy through the real import pipeline.

Only remote verification/enrichment and event sinks are doubled. File parsing,
quality-profile resolution, quality decisions, quarantine and moves remain real.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import struct
from types import SimpleNamespace
import wave

import pytest

import core.acoustid_verification as acoustid
import core.imports.guards as guards
import core.imports.paths as paths
import core.imports.pipeline as pipeline
import database.music_database as database
from database.music_database import MusicDatabase


@pytest.fixture
def import_environment(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "music.db"))
    monkeypatch.setattr(database, "MusicDatabase", lambda *args, **kwargs: db)
    effects = []
    defaults = {
        "soulseek.download_path": str(tmp_path / "downloads"),
        "soulseek.transfer_path": str(tmp_path / "Library"),
        "acoustid.require_verified": True,
        "post_processing.audio_completeness_check": True,
    }
    config = SimpleNamespace(get=lambda key, default=None: defaults.get(key, default))
    monkeypatch.setattr(pipeline, "config_manager", config)
    monkeypatch.setattr(guards, "_get_config_manager", lambda: config)
    monkeypatch.setattr(paths, "_get_config_manager", lambda: config)
    monkeypatch.setattr(pipeline.time, "sleep", lambda seconds: None)

    # Event/network boundaries must be absent on a successful preflight.
    for name in (
        "emit_track_downloaded", "record_library_history_download",
        "record_download_provenance", "record_soulsync_library_entry",
        "check_and_remove_from_wishlist", "add_activity_item", "enhance_file_metadata",
    ):
        monkeypatch.setattr(
            pipeline, name,
            lambda *args, _name=name, **kwargs: effects.append(_name),
        )
    runtime = SimpleNamespace(
        automation_engine=SimpleNamespace(
            emit=lambda *args, **kwargs: effects.append("automation_event"),
            is_event_action_enabled=lambda *args: False,
        ),
        on_download_completed=lambda *args, **kwargs: effects.append("completion_callback"),
        web_scan_manager=SimpleNamespace(request_scan=lambda *args: effects.append("scan")),
        repair_worker=None,
    )
    return SimpleNamespace(db=db, effects=effects, runtime=runtime, root=tmp_path, settings=defaults)


def _audio_file(root, name="song.wav", *, silence=False):
    path = root / name
    # Three seconds gives the deep guard a meaningful silent interval.
    samples = b"\x00\x00" * (44100 * 3) if silence else b"".join(
        struct.pack("<h", round(12000 * math.sin(2 * math.pi * 440 * index / 44100)))
        for index in range(44100 * 3)
    )
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes(samples)
    return path


def _context(env, *, required=False, preflight=True, simple=True, deep=False, targets=None, fallback=False):
    profile_id = env.db.create_quality_profile("Required" if required else "Advisory", {
        "ranked_targets": targets if targets is not None else [
            {"label": "PCM 16-bit", "format": "wav", "bit_depth": 16},
        ],
        "fallback_enabled": fallback,
        "acoustid_required": required,
        "deep_audio_verify": deep,
        "downsample_enabled": False,
    })
    assert profile_id is not None
    return {
        "source": "usenet",
        "artist": {"name": "Expected Artist"},
        "album": {"name": "Album"},
        "track_info": {
            "name": "Expected Track", "artists": [{"name": "Expected Artist"}],
            "duration_ms": 3000, "quality_profile_id": profile_id,
        },
        "original_search_result": {"title": "Expected Track", "artist": "Expected Artist"},
        "search_result": {"is_simple_download": simple, "filename": "song.wav"},
        "task_id": "preflight-task", "batch_id": "preflight-batch",
        "_release_preflight_only": preflight,
    }


def _verification(monkeypatch, result=acoustid.VerificationResult.PASS, *, available=True, raises=False):
    class Verifier:
        def quick_check_available(self):
            return available, "service unavailable"

        def verify_audio_file(self, path, title, artist, context):
            assert title == "Expected Track"
            assert artist == "Expected Artist"
            if raises:
                raise RuntimeError("fingerprint service failed")
            return result, "fingerprint diagnostic"

    monkeypatch.setattr(acoustid, "AcoustIDVerification", Verifier)


def _run(env, context, path):
    pipeline.post_process_matched_download("release-preflight", context, str(path), env.runtime)


@pytest.mark.parametrize("simple", [False, True])
def test_preflight_success_preserves_file_and_has_no_success_effects(import_environment, monkeypatch, simple):
    # Removing the early preflight return would move/tag/register this file.
    env = import_environment
    path = _audio_file(env.root)
    initial = hashlib.sha256(path.read_bytes()).hexdigest()
    context = _context(env, required=True, simple=simple)
    _verification(monkeypatch)
    _run(env, context, path)

    assert context.get("_release_checks_passed") is True
    assert path.exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == initial
    assert not (env.root / "Library").exists()
    assert env.effects == []
    assert context.get("_pipeline_import_succeeded") is not True
    assert context.get("_simple_download_completed") is not True
    assert context.get("_acoustid_result") == "pass"


def test_optional_mismatch_imports_with_unverified_advisory(import_environment, monkeypatch):
    # A profile's false requirement must override the opposite legacy global.
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env, preflight=False)
    _verification(monkeypatch, acoustid.VerificationResult.FAIL)
    _run(env, context, path)

    assert context.get("_pipeline_import_succeeded") is True
    assert not path.exists()
    assert Path(context["_final_path"]).exists()
    assert context["_acoustid_result"] == "fail"
    assert "fingerprint diagnostic" in context["_acoustid_advisory_msg"]
    assert context["_verification_status"] == "unverified"
    assert not context.get("_acoustid_quarantined")


@pytest.mark.parametrize("result", [
    acoustid.VerificationResult.FAIL,
    acoustid.VerificationResult.SKIP,
    acoustid.VerificationResult.ERROR,
    acoustid.VerificationResult.DISABLED,
])
def test_required_nonpass_is_rejected_before_import(import_environment, monkeypatch, result):
    # Treating ERROR/DISABLED as a successful verification violates required.
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env, required=True, preflight=False)
    _verification(monkeypatch, result)
    _run(env, context, path)

    assert context.get("_pipeline_import_succeeded") is not True
    assert context.get("_acoustid_quarantined") is True
    assert "AcoustID" in pipeline.import_rejection_reason(context)
    assert not path.exists()
    assert len(list((env.root / "downloads" / "ss_quarantine").glob("*.quarantined"))) == 1
    assert not (env.root / "Library").exists()


@pytest.mark.parametrize("case", ["unavailable", "exception", "missing_metadata"])
def test_required_preflight_cannot_pass_without_verification(import_environment, monkeypatch, case):
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env, required=True)
    if case == "missing_metadata":
        context["track_info"]["name"] = ""
        context["original_search_result"]["title"] = ""
    _verification(monkeypatch, available=case != "unavailable", raises=case == "exception")
    _run(env, context, path)

    assert context.get("_release_checks_passed") is not True
    assert context.get("_acoustid_quarantined") is True
    assert pipeline.import_rejection_reason(context)
    assert not (env.root / "Library").exists()


@pytest.mark.parametrize("result", [
    acoustid.VerificationResult.FAIL,
    acoustid.VerificationResult.SKIP,
    acoustid.VerificationResult.ERROR,
])
def test_optional_preflight_nonpass_is_advisory(import_environment, monkeypatch, result):
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env)
    _verification(monkeypatch, result)
    _run(env, context, path)

    assert context.get("_release_checks_passed") is True
    assert path.exists()
    assert context["_acoustid_result"] == result.value
    assert context["_acoustid_advisory_msg"]
    assert pipeline.import_rejection_reason(context) is None
    assert env.effects == []


def test_preflight_quality_rejects_even_without_display_label(import_environment, monkeypatch):
    # WAV has measurable quality but no display chip; its quality gate must run.
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env, targets=[{"label": "FLAC 24-bit", "format": "flac", "bit_depth": 24}])
    _verification(monkeypatch)
    _run(env, context, path)

    assert context.get("_release_checks_passed") is not True
    assert context.get("_bitdepth_rejected") is True
    assert "Quality mismatch" in context["_quarantine_reject_reason"]


@pytest.mark.parametrize("fallback", [False, True])
def test_preflight_requires_measured_quality_when_profile_has_targets(import_environment, monkeypatch, fallback):
    # Mutagen reads WAV content, while the quality probe cannot classify .bin.
    env = import_environment
    path = _audio_file(env.root, name="song.bin")
    context = _context(env, fallback=fallback)
    _verification(monkeypatch)
    _run(env, context, path)

    assert context.get("_release_checks_passed") is not True
    assert context.get("_bitdepth_rejected") is True
    assert "quality" in pipeline.import_rejection_reason(context).lower()
    assert "could not" in context["_quarantine_reject_reason"].lower()


def test_preflight_runs_real_integrity_and_clears_stale_pass(import_environment, monkeypatch):
    env = import_environment
    path = env.root / "song.wav"
    path.write_bytes(b"broken")
    context = _context(env)
    context["_release_checks_passed"] = True
    _verification(monkeypatch)
    _run(env, context, path)

    assert context.get("_release_checks_passed") is not True
    assert context.get("_integrity_failure_msg")
    assert pipeline.import_rejection_reason(context)


@pytest.mark.parametrize("deep, rejected", [(True, True), (False, False)])
def test_preflight_deep_audio_uses_item_profile(import_environment, monkeypatch, deep, rejected):
    env = import_environment
    path = _audio_file(env.root, silence=True)
    context = _context(env, deep=deep)
    _verification(monkeypatch)
    _run(env, context, path)

    assert bool(context.get("_silence_rejected")) is rejected
    assert bool(context.get("_release_checks_passed")) is (not rejected)


def test_preflight_outcome_reaches_original_context_when_normalizer_returns_copy(import_environment, monkeypatch):
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env)
    normalizer = pipeline.normalize_import_context
    monkeypatch.setattr(pipeline, "normalize_import_context", lambda value: normalizer(dict(value)))
    _verification(monkeypatch)
    _run(env, context, path)

    assert context.get("_release_checks_passed") is True
    assert context.get("_acoustid_result") == "pass"
    assert path.exists()
    assert env.effects == []


def test_explicit_release_staging_runs_when_global_atomic_publish_is_off(import_environment):
    env = import_environment
    library = env.root / "Library"
    staging = library / ".soulsync_atomic_staging" / "release-one"
    live = library / "Artist" / "Album" / "song.wav"
    context = {"_release_staging_root": str(staging), "_release_transfer_dir": str(library)}
    assert pipeline._maybe_stage_album_track(context, str(live)) == str(staging / "Artist" / "Album" / "song.wav")
    assert not library.exists()  # mapping itself must not publish or create files


@pytest.mark.parametrize("case", ["outside_library", "outside_staging", "missing_root", "missing_library"])
def test_explicit_release_staging_never_falls_back_to_live_path(import_environment, case):
    env = import_environment
    library = env.root / "Library"
    staging = library / ".soulsync_atomic_staging" / "release-one"
    live = library / "Artist" / "Album" / "song.wav"
    context = {"_release_staging_root": str(staging), "_release_transfer_dir": str(library)}
    if case == "outside_library":
        live = env.root / "OtherLibrary" / "song.wav"
    elif case == "outside_staging":
        context["_release_staging_root"] = str(env.root / "OtherLibrary" / "staging")
    elif case == "missing_root":
        context.pop("_release_staging_root")
    else:
        context.pop("_release_transfer_dir")
    with pytest.raises(ValueError):
        pipeline._maybe_stage_album_track(context, str(live))
    assert not live.exists()


def test_explicit_release_staging_refuses_existing_live_destination(import_environment):
    env = import_environment
    library = env.root / "Library"
    staging = library / ".soulsync_atomic_staging" / "release-one"
    live = library / "Artist" / "Album" / "song.wav"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"already imported better audio")
    context = {
        "_release_staging_root": str(staging), "_release_transfer_dir": str(library),
        "_release_abort_on_existing": True,
    }
    with pytest.raises(FileExistsError):
        pipeline._maybe_stage_album_track(context, str(live))
    assert live.read_bytes() == b"already imported better audio"
    assert not staging.exists()


def test_staging_mapping_error_stops_real_pipeline_before_publication(import_environment, monkeypatch):
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env, preflight=False)
    context.update({
        "_release_staging_root": str(env.root / "OtherLibrary" / "staging"),
        "_release_transfer_dir": str(env.root / "Library"),
    })
    _verification(monkeypatch)
    _run(env, context, path)
    assert path.exists()
    assert context.get("_pipeline_import_succeeded") is not True
    assert context.get("_context_failure_msg")
    assert not list((env.root / "Library").rglob("*.wav"))


@pytest.mark.parametrize("outcome", ["exception", "missing_result", "unknown_length", "unavailable_parse"])
def test_preflight_integrity_check_errors_cannot_be_counted_as_passed(import_environment, monkeypatch, outcome):
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env)
    _verification(monkeypatch)
    def broken_probe(*args, **kwargs):
        from core.imports.file_integrity import IntegrityResult
        if outcome == "exception":
            raise OSError("audio probe unavailable")
        if outcome == "unknown_length":
            return IntegrityResult(ok=True, checks={"mutagen_parse": "zero_length_unknown", "length_check": "skipped_unknown_length"})
        if outcome == "unavailable_parse":
            return IntegrityResult(ok=True, checks={"mutagen_parse": "unavailable"})
        return None
    monkeypatch.setattr(pipeline, "check_audio_integrity", broken_probe)
    _run(env, context, path)
    assert context.get("_release_checks_passed") is not True
    assert context.get("_integrity_failure_msg")
    assert pipeline.import_rejection_reason(context)
    assert not (env.root / "Library").exists()


@pytest.mark.parametrize("failure", ["unavailable", "decode_error", "no_measurement"])
def test_preflight_active_deep_check_requires_a_measured_decode(import_environment, monkeypatch, failure):
    import core.imports.silence as silence
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env, deep=True)
    _verification(monkeypatch)
    real_run = silence.subprocess.run
    def failed_decoder(args, *other_args, **kwargs):
        if args[:2] == ["ffmpeg", "-version"]:
            if failure == "unavailable":
                raise FileNotFoundError("ffmpeg unavailable")
            return real_run(args, *other_args, **kwargs)
        return SimpleNamespace(returncode=1 if failure == "decode_error" else 0, stderr=b"decoder diagnostic")
    monkeypatch.setattr(silence.subprocess, "run", failed_decoder)
    _run(env, context, path)
    assert context.get("_release_checks_passed") is not True
    assert context.get("_silence_rejected") is True
    assert "measure" in context["_quarantine_reject_reason"].lower()
    assert not (env.root / "Library").exists()


def test_preflight_rejection_reaches_original_context_when_normalizer_returns_copy(import_environment, monkeypatch):
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env, required=True)
    normalizer = pipeline.normalize_import_context
    monkeypatch.setattr(pipeline, "normalize_import_context", lambda value: normalizer(dict(value)))
    _verification(monkeypatch, acoustid.VerificationResult.ERROR)
    _run(env, context, path)
    assert context.get("_release_checks_passed") is not True
    assert context.get("_acoustid_result") == "error"
    assert context.get("_acoustid_quarantined") is True
    assert pipeline.import_rejection_reason(context)


@pytest.mark.parametrize("case", ["unavailable", "exception", "missing_metadata"])
def test_optional_preflight_without_verification_still_retains_advisory(import_environment, monkeypatch, case):
    env = import_environment
    path = _audio_file(env.root)
    context = _context(env)
    if case == "missing_metadata":
        context["track_info"]["name"] = ""
        context["original_search_result"]["title"] = ""
    _verification(monkeypatch, available=case != "unavailable", raises=case == "exception")
    _run(env, context, path)
    assert context.get("_release_checks_passed") is True
    assert context.get("_acoustid_advisory_msg")
    assert path.exists()
    assert env.effects == []


@pytest.mark.parametrize("enabled, failure", [(False, "unavailable"), (True, "unavailable"), (True, "timeout")])
def test_preflight_active_flac_decode_requires_success(import_environment, monkeypatch, enabled, failure):
    import shutil
    import subprocess
    from core.imports import file_integrity
    env = import_environment
    source = _audio_file(env.root)
    path = env.root / "song.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-c:a", "flac", "-compression_level", "0", str(path)], check=True)
    context = _context(env, targets=[{"label": "FLAC", "format": "flac"}])
    env.settings["post_processing.verify_flac_decode"] = enabled
    _verification(monkeypatch)
    real_which = shutil.which
    real_run = subprocess.run
    if failure == "unavailable":
        monkeypatch.setattr(file_integrity.shutil, "which", lambda name: None if name == "flac" else real_which(name))
    else:
        def timed_out(args, *other_args, **kwargs):
            if Path(args[0]).name == "flac":
                raise subprocess.TimeoutExpired(args, 600)
            return real_run(args, *other_args, **kwargs)
        monkeypatch.setattr(file_integrity.subprocess, "run", timed_out)
    _run(env, context, path)
    assert bool(context.get("_release_checks_passed")) is (not enabled)
    if enabled:
        assert context.get("_integrity_failure_msg")
        assert not path.exists()
    else:
        assert path.exists()
        assert env.effects == []
