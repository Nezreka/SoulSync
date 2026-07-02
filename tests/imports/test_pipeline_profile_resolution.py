"""`_resolve_context_quality_profile` — the single place the import pipeline
resolves "which quality profile governs this file". Every post-processing
step (deep verify, AcoustID strictness, replace-lower, downsample, lossy
copy) reads its settings from this dict, so per-item profiles govern the
WHOLE pipeline, not just search ranking and the quality gate.
"""

from __future__ import annotations

import core.imports.pipeline as pipeline


def test_resolves_track_info_profile_id(monkeypatch):
    seen = {}

    def _fake_load(pid):
        seen["pid"] = pid
        return {"acoustid_required": True, "deep_audio_verify": False}

    monkeypatch.setattr("core.quality.selection.load_profile_by_id", _fake_load)

    context = {"track_info": {"quality_profile_id": 42}}
    profile = pipeline._resolve_context_quality_profile(context)
    assert seen["pid"] == 42
    assert profile["acoustid_required"] is True


def test_none_track_info_resolves_default(monkeypatch):
    """Simple downloads set track_info to None — must resolve the app-wide
    default (id None), not crash."""
    seen = {}

    def _fake_load(pid):
        seen["pid"] = pid
        return {"deep_audio_verify": True}

    monkeypatch.setattr("core.quality.selection.load_profile_by_id", _fake_load)

    context = {"track_info": None}
    profile = pipeline._resolve_context_quality_profile(context)
    assert seen["pid"] is None
    assert profile["deep_audio_verify"] is True


def test_result_is_cached_on_the_context(monkeypatch):
    calls = []

    def _fake_load(pid):
        calls.append(pid)
        return {"acoustid_required": False}

    monkeypatch.setattr("core.quality.selection.load_profile_by_id", _fake_load)

    context = {"track_info": {"quality_profile_id": 7}}
    first = pipeline._resolve_context_quality_profile(context)
    second = pipeline._resolve_context_quality_profile(context)
    assert first is second
    assert calls == [7]  # resolved exactly once per file


def test_pre_seeded_cache_is_honored():
    """Tests (and any caller) can force config-fallback behavior by seeding
    an empty dict — `.get(key, config_default)` then falls through."""
    context = {"_quality_profile": {}}
    assert pipeline._resolve_context_quality_profile(context) == {}


def test_resolution_failure_falls_back_to_empty(monkeypatch):
    def _boom(pid):
        raise RuntimeError("db down")

    monkeypatch.setattr("core.quality.selection.load_profile_by_id", _boom)

    context = {"track_info": {"quality_profile_id": 3}}
    profile = pipeline._resolve_context_quality_profile(context)
    assert profile == {}
    # Failure is cached too — the pipeline must not retry per step.
    assert pipeline._resolve_context_quality_profile(context) is profile
