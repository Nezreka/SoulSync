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


# ── Issue #1672: dynamic config, preflight validation & fast scan abort ─────

def test_client_picks_up_config_change_without_restart(monkeypatch):
    """A client created once in a long-running process must see config changes
    dynamically without needing a container restart."""
    fake_config = {"acoustid.api_key": "initial_key", "acoustid.enabled": True}
    monkeypatch.setattr(acc.config_manager, "get", lambda k, d=None: fake_config.get(k, d))

    c = AcoustIDClient()
    assert c.api_key == "initial_key"
    assert c.enabled is True

    # User changes key in Web UI settings
    fake_config["acoustid.api_key"] = "new_updated_key"
    fake_config["acoustid.enabled"] = False

    # Same client instance reads new values immediately
    assert c.api_key == "new_updated_key"
    assert c.enabled is False


def test_refresh_config_clears_override(monkeypatch):
    """Explicitly set overrides are cleared by refresh_config()."""
    fake_config = {"acoustid.api_key": "config_key"}
    monkeypatch.setattr(acc.config_manager, "get", lambda k, d=None: fake_config.get(k, d))

    c = AcoustIDClient()
    c._api_key = "manual_override"
    assert c.api_key == "manual_override"

    c.refresh_config()
    assert c.api_key == "config_key"


def test_lookup_status_structured_error_code_and_message(tmp_path, monkeypatch):
    """When pyacoustid returns a dict with status!=ok, lookup_with_status extracts
    the exact code and message instead of dropping details."""
    f = tmp_path / "a.bin"
    f.write_bytes(b"not audio")
    c, fake = _client_with_fake_acoustid(monkeypatch)
    monkeypatch.setattr(c, "_api_key", "user_key_instead_of_app_key")

    # Mock pyacoustid match(parse=False) returning error dict
    fake.parse_lookup_result = MagicMock()
    fake.match = MagicMock(return_value={"status": "error", "error": {"code": 4, "message": "invalid client key"}})

    res = c.lookup_with_status(str(f))
    assert res["status"] == "error"
    assert res["invalid_key"] is True
    assert "code 4" in res["error"] or "invalid client key" in res["error"]


def test_scanner_refuses_to_start_on_invalid_api_key(monkeypatch):
    """AcoustID scanner pre-flight test validates the key and refuses to start
    before walking the catalogue if the key is invalid."""
    from types import SimpleNamespace
    from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob

    scanned_tracks = []
    job = AcoustIDScannerJob()
    context = SimpleNamespace(
        db=SimpleNamespace(_get_connection=lambda: scanned_tracks.append("db")),
        transfer_folder="/music",
        config_manager=SimpleNamespace(get=lambda key, default=None: default, set=lambda *a, **k: None),
        acoustid_client=SimpleNamespace(
            is_available=lambda: (True, "ready"),
            test_api_key=lambda: (False, "Invalid AcoustID API key — use an Application API key"),
        ),
        report_progress=MagicMock(),
        update_progress=MagicMock(),
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        sleep_or_stop=lambda *a, **k: False,
    )

    result = job.scan(context)
    assert result.errors == 1
    assert result.scanned == 0
    assert scanned_tracks == [], "scan must abort before reading tracks from DB"
    context.report_progress.assert_called_with(
        log_line="AcoustID key invalid — scan aborted: Invalid AcoustID API key — use an Application API key",
        log_type="error",
    )


def test_scanner_aborts_mid_scan_on_invalid_api_key(tmp_path, monkeypatch):
    """If an invalid API key is encountered mid-scan, the scanner aborts
    immediately instead of walking thousands of remaining tracks."""
    from types import SimpleNamespace
    from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob

    f1 = tmp_path / "track1.mp3"
    f1.write_bytes(b"dummy audio 1")
    f2 = tmp_path / "track2.mp3"
    f2.write_bytes(b"dummy audio 2")

    job = AcoustIDScannerJob()
    monkeypatch.setattr(
        "core.repair_jobs.acoustid_scanner.hand_tagged_path_keys",
        lambda db: set(),
    )
    monkeypatch.setattr(
        job,
        "_load_db_tracks",
        lambda ctx: {
            "1": {"file_path": str(f1), "title": "Track 1", "artist": "Artist 1"},
            "2": {"file_path": str(f2), "title": "Track 2", "artist": "Artist 2"},
        },
    )
    monkeypatch.setattr(job, "_resolve_path", lambda path, ctx: path)

    # Mock _scan_file to simulate first file failing with abort_invalid_key
    scan_file_calls = []

    def _mock_scan_file(fpath, track_id, track_info, client, ctx, res, *args):
        scan_file_calls.append(track_id)
        res.errors += 1
        return "abort_invalid_key"

    monkeypatch.setattr(job, "_scan_file", _mock_scan_file)

    context = SimpleNamespace(
        db=SimpleNamespace(),
        transfer_folder=str(tmp_path),
        config_manager=SimpleNamespace(get=lambda k, d=None: d, set=lambda *a, **k: None),
        acoustid_client=SimpleNamespace(
            is_available=lambda: (True, "ready"),
            test_api_key=lambda: (True, "valid"),
        ),
        report_progress=MagicMock(),
        update_progress=MagicMock(),
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        sleep_or_stop=lambda *a, **k: False,
    )

    result = job.scan(context)
    # Only 1 file should have been scanned before aborting; track 2 must NOT be touched
    assert len(scan_file_calls) == 1
    assert scan_file_calls == ["1"]
    assert result.errors == 1



# ── an unfingerprintable file is not a dead key ────────────────────────────
#
# AcoustID answers a fingerprint it cannot decode (a file with headers but no
# usable audio) with code 3 "invalid fingerprint". The client key is checked
# first, so that answer means the key was ACCEPTED. Reading "invalid" in the
# message as a key failure aborted the whole library scan at that one file,
# every run.

@pytest.mark.parametrize("code, message, expected", [
    # real responses, acoustid-server acoustid/api/errors.py
    (3, "invalid fingerprint", False),
    (4, "invalid API key", True),
    (2, 'missing required parameter "fingerprint"', False),
    (8, 'parameter "duration" must be a positive integer', False),
    (14, "rate limit (3.000000 requests per second) exceeded, try again later", False),
    # the code decides over the wording
    (4, "invalid client key", True),
    # no code: only the message is left to go on
    (0, "invalid API key", True),
    (None, "invalid fingerprint", False),
    (None, "HTTP request failed: connection reset", False),
])
def test_is_invalid_key_error(code, message, expected):
    assert acc.is_invalid_key_error(code, message) is expected


def test_lookup_status_invalid_fingerprint_is_not_an_invalid_key(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"
    f.write_bytes(b"not audio")
    c, fake = _client_with_fake_acoustid(monkeypatch)
    fake.parse_lookup_result = MagicMock()
    fake.match = MagicMock(return_value={
        "status": "error", "error": {"code": 3, "message": "invalid fingerprint"}})

    res = c.lookup_with_status(str(f))
    assert res["status"] == "error"
    assert res["invalid_key"] is False
    assert "code 3" in res["error"]


def test_lookup_status_bare_status_error_on_current_pyacoustid_is_not_an_invalid_key(tmp_path, monkeypatch):
    """With parse_lookup_result available the bare "status: error" only shows up
    when the raw lookup failed and the parse=True retry hit a server error —
    which may just as well be the file's fingerprint as the key."""
    f = tmp_path / "a.bin"
    f.write_bytes(b"not audio")
    c, fake = _client_with_fake_acoustid(monkeypatch)
    fake.parse_lookup_result = MagicMock()

    def _match(api_key, audio_file, parse=True):
        if not parse:
            raise fake.WebServiceError("HTTP request failed: connection reset")
        raise fake.WebServiceError("status: error")
    fake.match = _match

    res = c.lookup_with_status(str(f))
    assert res["status"] == "error"
    assert res["invalid_key"] is False


def test_lookup_status_webservice_error_code_decides(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"
    f.write_bytes(b"not audio")
    c, fake = _client_with_fake_acoustid(monkeypatch)
    fake.parse_lookup_result = MagicMock()

    def _raise(code, message):
        def _match(*a, **k):
            e = fake.WebServiceError(message)
            e.code = code
            raise e
        return _match

    fake.match = _raise(4, "invalid API key")
    assert c.lookup_with_status(str(f))["invalid_key"] is True
    fake.match = _raise(3, "invalid fingerprint")
    assert c.lookup_with_status(str(f))["invalid_key"] is False


def _scanner_over(tmp_path, monkeypatch, responses):
    """The real scan loop, _scan_file, verifier and lookup_with_status over one
    file per entry in ``responses`` — only pyacoustid is faked."""
    from types import SimpleNamespace
    from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob

    files = {}
    for i, response in enumerate(responses, start=1):
        f = tmp_path / f"track{i}.flac"
        f.write_bytes(b"headers, no audio")
        files[str(f)] = response

    client, fake = _client_with_fake_acoustid(monkeypatch)
    fake.parse_lookup_result = MagicMock()
    fingerprinted = []

    def _match(api_key, audio_file, parse=True):
        fingerprinted.append(audio_file)
        return files[audio_file]
    fake.match = _match
    client._enabled = True
    monkeypatch.setattr(client, "is_available", lambda: (True, "ready"))
    monkeypatch.setattr(client, "test_api_key", lambda: (True, "valid"))

    job = AcoustIDScannerJob()
    monkeypatch.setattr("core.repair_jobs.acoustid_scanner.hand_tagged_path_keys", lambda db: set())
    monkeypatch.setattr("core.repair_jobs.base.whole_library_run", lambda ctx: True)
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _p: {})
    monkeypatch.setattr(job, "_load_db_tracks", lambda ctx: {
        str(i): {"file_path": path, "title": f"Track {i}", "artist": "Artist"}
        for i, path in enumerate(files, start=1)
    })
    monkeypatch.setattr(job, "_resolve_path", lambda path, ctx: path)

    cfg = {}
    context = SimpleNamespace(
        db=SimpleNamespace(),
        transfer_folder=str(tmp_path),
        config_manager=SimpleNamespace(get=lambda k, d=None: cfg.get(k, d), set=cfg.__setitem__),
        acoustid_client=client,
        report_progress=MagicMock(),
        update_progress=MagicMock(),
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        sleep_or_stop=lambda *a, **k: False,
    )
    return job.scan(context), fingerprinted, list(files), cfg


def test_scanner_keeps_going_past_an_unfingerprintable_file(tmp_path, monkeypatch):
    bad = {"status": "error", "error": {"code": 3, "message": "invalid fingerprint"}}
    result, fingerprinted, paths, cfg = _scanner_over(tmp_path, monkeypatch, [bad, bad, bad])

    assert fingerprinted == paths, "the scan stopped at the unfingerprintable file"
    assert result.errors == 3
    # reached the end of the list: the checkpoint is cleared, not left on file 1
    assert "repair.jobs.acoustid_scanner.checkpoint_id" in cfg
    assert cfg["repair.jobs.acoustid_scanner.checkpoint_id"] is None


def test_scanner_still_aborts_on_a_rejected_key(tmp_path, monkeypatch):
    dead = {"status": "error", "error": {"code": 4, "message": "invalid API key"}}
    result, fingerprinted, paths, _cfg = _scanner_over(tmp_path, monkeypatch, [dead, dead, dead])

    assert fingerprinted == paths[:1]
    assert result.errors == 1
