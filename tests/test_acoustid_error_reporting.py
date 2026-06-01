"""AcoustID error-vs-no-match reporting.

Regression for the masking bug: an invalid API key (and other lookup errors)
used to collapse into the same `None` as a genuine no-match, so the UI showed a
benign "Skipped" and the "Test API key" button reported a dead key as valid.
These tests pin the distinction end to end:
  - lookup_with_status separates ok / no_match / error / no_backend / unavailable
  - fingerprint_and_lookup (legacy) stays dict-or-None
  - verify_audio_file -> ERROR for a real error, SKIP for a genuine no-match
  - test_api_key reports an invalid key (API error code 4) as invalid
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import core.acoustid_client as acc
from core.acoustid_client import AcoustIDClient
from core.acoustid_verification import AcoustIDVerification, VerificationResult


# ── lookup_with_status: structured status distinction ──────────────────────

def _client_with_fake_acoustid(monkeypatch, *, match=None, raises=None):
    """An AcoustIDClient wired to a fake `acoustid` module so we can drive
    match() without network or chromaprint."""
    fake = types.ModuleType("acoustid")

    class WebServiceError(Exception):
        pass

    class NoBackendError(Exception):
        pass

    class FingerprintGenerationError(Exception):
        pass

    fake.WebServiceError = WebServiceError
    fake.NoBackendError = NoBackendError
    fake.FingerprintGenerationError = FingerprintGenerationError

    def _match(api_key, audio_file, parse=True):
        if raises is not None:
            raise raises
        return match or []

    fake.match = _match
    monkeypatch.setitem(sys.modules, "acoustid", fake)
    monkeypatch.setattr(acc, "ACOUSTID_AVAILABLE", True)

    c = AcoustIDClient()
    c._api_key = "testkey123"   # bypass config
    return c, fake


def test_lookup_status_ok(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"not audio")  # mutagen -> None, channel check skipped
    c, _ = _client_with_fake_acoustid(monkeypatch, match=[(0.95, "mbid-1", "Title", "Artist")])
    res = c.lookup_with_status(str(f))
    assert res["status"] == "ok"
    assert res["recordings"] and res["recordings"][0]["mbid"] == "mbid-1"


def test_lookup_status_no_match(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"not audio")
    c, _ = _client_with_fake_acoustid(monkeypatch, match=[])
    res = c.lookup_with_status(str(f))
    assert res["status"] == "no_match"
    assert res["recordings"] == []


def test_lookup_status_error_on_webservice(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"not audio")
    c, fake = _client_with_fake_acoustid(monkeypatch)
    # invalid key surfaces (old pyacoustid) as the bare "status: error"
    monkeypatch.setattr(c, "_api_key", "testkey123")

    def _raise(*a, **k):
        raise fake.WebServiceError("status: error")
    fake.match = _raise

    res = c.lookup_with_status(str(f))
    assert res["status"] == "error"
    assert res["invalid_key"] is True


def test_lookup_status_no_backend(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"not audio")
    c, fake = _client_with_fake_acoustid(monkeypatch)

    def _raise(*a, **k):
        raise fake.NoBackendError()
    fake.match = _raise

    assert c.lookup_with_status(str(f))["status"] == "no_backend"


def test_lookup_status_unavailable_without_key(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"x")
    monkeypatch.setattr(acc, "ACOUSTID_AVAILABLE", True)
    c = AcoustIDClient()
    c._api_key = ""   # no key
    assert c.lookup_with_status(str(f))["status"] == "unavailable"


# ── fingerprint_and_lookup keeps its dict-or-None contract ─────────────────

def test_legacy_wrapper_returns_dict_on_match(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"not audio")
    c, _ = _client_with_fake_acoustid(monkeypatch, match=[(0.9, "mbid-1", "T", "A")])
    out = c.fingerprint_and_lookup(str(f))
    assert out is not None and out["recordings"][0]["mbid"] == "mbid-1"


def test_legacy_wrapper_returns_none_on_error(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"not audio")
    c, fake = _client_with_fake_acoustid(monkeypatch)

    def _raise(*a, **k):
        raise fake.WebServiceError("status: error")
    fake.match = _raise
    assert c.fingerprint_and_lookup(str(f)) is None


# ── verify_audio_file: ERROR vs SKIP ───────────────────────────────────────

def _verifier_with_lookup(result):
    v = AcoustIDVerification()
    client = MagicMock()
    client.is_available.return_value = (True, "ready")
    client.lookup_with_status.return_value = result
    v.acoustid_client = client
    return v


def test_verify_reports_error_for_api_error():
    v = _verifier_with_lookup({"status": "error", "recordings": [], "error": "AcoustID API error: invalid"})
    result, msg = v.verify_audio_file("/x.flac", "Song", "Artist")
    assert result == VerificationResult.ERROR
    assert "error" in msg.lower() or "invalid" in msg.lower()


def test_verify_reports_skip_for_no_match():
    v = _verifier_with_lookup({"status": "no_match", "recordings": [], "error": "Track not found in AcoustID database"})
    result, msg = v.verify_audio_file("/x.flac", "Song", "Artist")
    assert result == VerificationResult.SKIP
    assert "no match" in msg.lower() or "not found" in msg.lower()


# ── test_api_key: invalid key reported as invalid ──────────────────────────

def _api_response(payload):
    r = MagicMock()
    r.json.return_value = payload
    return r


def test_test_api_key_invalid_when_code_4(monkeypatch):
    c = AcoustIDClient()
    c._api_key = "badkey"
    with patch("requests.get",
               return_value=_api_response({"status": "error", "error": {"code": 4, "message": "invalid API key"}})):
        ok, msg = c.test_api_key()
    assert ok is False
    assert "invalid" in msg.lower()


def test_test_api_key_valid_when_accepted(monkeypatch):
    c = AcoustIDClient()
    c._api_key = "goodkey"
    # A non-key error (e.g. bad dummy fingerprint) means the key was accepted.
    with patch("requests.get",
               return_value=_api_response({"status": "error", "error": {"code": 3, "message": "invalid fingerprint"}})):
        ok, msg = c.test_api_key()
    assert ok is True
    assert "valid" in msg.lower()
