"""Regression tests for stems availability gating.

stem separation runs on onnxruntime now (htdemucs exported to ONNX), not
torch. when onnxruntime isn't installed the UI must show a calm setup note
instead of a button that fails, so the backend exposes a real
`stems_available` boolean from the actual import check.
"""

import sys
import types

import pytest

import api.sample as sample_api
from core.sample import stems as stems_mod
from core.sample import stems_worker as stems_worker_mod
from core.sample import store as sample_store


def _importable(monkeypatch, *names):
    for name in names:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))


def _missing(monkeypatch, *names):
    # None in sys.modules makes `import name` raise ImportError
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)


def test_stems_available_is_bool():
    assert isinstance(stems_mod.stems_available(), bool)


def test_available_when_onnxruntime_and_soxr_import(monkeypatch):
    _importable(monkeypatch, "onnxruntime", "soxr")
    assert stems_mod.stems_available() is True


def test_unavailable_without_onnxruntime(monkeypatch):
    _importable(monkeypatch, "soxr")
    _missing(monkeypatch, "onnxruntime")
    assert stems_mod.stems_available() is False


def test_unavailable_without_soxr(monkeypatch):
    _importable(monkeypatch, "onnxruntime")
    _missing(monkeypatch, "soxr")
    assert stems_mod.stems_available() is False


def test_torch_is_not_needed(monkeypatch):
    _importable(monkeypatch, "onnxruntime", "soxr")
    _missing(monkeypatch, "torch", "torchaudio", "demucs")
    assert stems_mod.stems_available() is True


def test_default_backend_name_follows_availability(monkeypatch):
    monkeypatch.setattr(stems_mod, "stems_available", lambda: True)
    assert stems_mod._default_backend_name() == "demucs"


def test_default_backend_name_stub_when_unavailable(monkeypatch):
    monkeypatch.setattr(stems_mod, "stems_available", lambda: False)
    assert stems_mod._default_backend_name() == "stub"


@pytest.fixture
def _api_stubs(monkeypatch):
    monkeypatch.setattr(sample_api, "_track_exists", lambda track_id: True)
    monkeypatch.setattr(stems_worker_mod, "get_status", lambda track_id, method=None: "idle")
    monkeypatch.setattr(stems_worker_mod, "current_method", lambda track_id: "demucs")
    monkeypatch.setattr(stems_worker_mod, "enqueue_separation", lambda track_id, backend=None, method="demucs": "idle")
    monkeypatch.setattr(sample_store, "get_stems", lambda track_id, method="demucs", source_sig=None: None)


def test_stems_status_payload_carries_flag(_api_stubs, monkeypatch):
    monkeypatch.setattr(stems_mod, "stems_available", lambda: False)
    payload, http_status = sample_api.stems_status(7)
    assert http_status == 200
    assert payload["stems_available"] is False
    assert payload["status"] == "idle"


def test_stems_status_flag_true_when_available(_api_stubs, monkeypatch):
    monkeypatch.setattr(stems_mod, "stems_available", lambda: True)
    payload, _ = sample_api.stems_status(7)
    assert payload["stems_available"] is True


def test_separate_stems_payload_carries_flag(_api_stubs, monkeypatch):
    monkeypatch.setattr(stems_mod, "stems_available", lambda: True)
    payload, http_status = sample_api.separate_stems(7)
    assert http_status == 200
    assert payload["stems_available"] is True


def test_separation_without_onnxruntime_is_refused_not_attempted(_api_stubs, monkeypatch):
    """can't run it: say so up front, never queue a job that downloads the
    model and then dies on the import."""
    monkeypatch.setattr(stems_mod, "stems_available", lambda: False)
    queued = []
    monkeypatch.setattr(stems_worker_mod, "enqueue_separation",
                        lambda *a, **k: queued.append(a) or "queued")
    with pytest.raises(sample_api.SampleHttpError) as exc_info:
        sample_api.separate_stems(7)
    assert exc_info.value.status == 409
    assert exc_info.value.code == "STEMS_UNAVAILABLE"
    assert queued == []
