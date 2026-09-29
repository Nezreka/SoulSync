"""Regression tests for stems availability gating.

Broque's decision: keep stem separation, but when torch/demucs isn't
installed the UI must show a calm setup note instead of a button that fails.
The backend exposes a real `stems_available` boolean (reusing the actual
import check, not a guess) on the stems payloads.
"""

import sys
import types

import pytest

import api.sample as sample_api
from core.sample import stems as stems_mod
from core.sample import stems_worker as stems_worker_mod
from core.sample import store as sample_store


def _fake_importable(monkeypatch, *names):
    for name in names:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))


def test_stems_available_is_bool():
    assert isinstance(stems_mod.stems_available(), bool)


def test_stems_available_false_in_this_env():
    # torch is not installed in the build env.
    assert stems_mod.stems_available() is False


def test_stems_available_true_when_all_importable(monkeypatch):
    _fake_importable(monkeypatch, "torch", "torchaudio", "demucs")
    assert stems_mod.stems_available() is True


def test_stems_available_false_when_torchaudio_missing(monkeypatch):
    # Broque's exact failure: demucs present but torchaudio missing must
    # not count as available (separation would still fail).
    _fake_importable(monkeypatch, "torch", "demucs")
    monkeypatch.delitem(sys.modules, "torchaudio", raising=False)
    assert "torchaudio" not in sys.modules
    assert stems_mod.stems_available() is False


def test_default_backend_name_follows_availability(monkeypatch):
    _fake_importable(monkeypatch, "torch", "torchaudio", "demucs")
    assert stems_mod._default_backend_name() == "demucs"


def test_default_backend_name_stub_when_unavailable(monkeypatch):
    monkeypatch.setattr(stems_mod, "stems_available", lambda: False)
    assert stems_mod._default_backend_name() == "stub"


@pytest.fixture
def _api_stubs(monkeypatch):
    monkeypatch.setattr(sample_api, "_track_exists", lambda track_id: True)
    monkeypatch.setattr(stems_worker_mod, "get_status", lambda track_id: "idle")
    monkeypatch.setattr(stems_worker_mod, "enqueue_separation", lambda track_id, backend=None: "idle")
    monkeypatch.setattr(sample_store, "get_stems", lambda track_id: None)


def test_stems_status_payload_carries_flag(_api_stubs):
    payload, http_status = sample_api.stems_status(7)
    assert http_status == 200
    assert payload["stems_available"] is False
    assert payload["status"] == "idle"


def test_stems_status_flag_true_when_available(_api_stubs, monkeypatch):
    monkeypatch.setattr(stems_mod, "stems_available", lambda: True)
    payload, _ = sample_api.stems_status(7)
    assert payload["stems_available"] is True


def test_separate_stems_payload_carries_flag(_api_stubs):
    payload, http_status = sample_api.separate_stems(7)
    assert http_status == 200
    assert payload["stems_available"] is False
