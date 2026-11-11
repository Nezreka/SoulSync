"""#2042: turning off download verification must not make the scan impossible.

"Enable Download Verification" means "check every new download". It gated
everything, so someone who turned it off to check their library later, by
hand, found the AcoustID Scanner refusing to run: "AcoustID unavailable,
nothing scanned: AcoustID verification is disabled".

A run a person starts scans whatever the setting says. The schedule still
follows it.
"""

from types import SimpleNamespace

import pytest

import core.acoustid_client as ac
from core.acoustid_client import AcoustIDClient
from core.acoustid_verification import AcoustIDVerification, VerificationResult
from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob
from core.repair_jobs.base import JobContext


@pytest.fixture
def ready(monkeypatch):
    """an install that can fingerprint: library, chromaprint, a key."""
    monkeypatch.setattr(ac, "ACOUSTID_AVAILABLE", True)
    monkeypatch.setattr(ac, "CHROMAPRINT_AVAILABLE", True)

    def make(enabled):
        client = AcoustIDClient(api_key="key")
        client._enabled = enabled
        return client
    return make


def test_the_setting_gates_downloads_and_not_the_ability_to_fingerprint(ready):
    off = ready(False)
    assert off.is_available() == (False, "AcoustID verification is disabled")
    assert off.can_fingerprint()[0] is True
    on = ready(True)
    assert on.is_available()[0] is True and on.can_fingerprint()[0] is True


def test_a_missing_key_or_library_still_stops_a_manual_check(ready, monkeypatch):
    client = ready(False)
    client._api_key = ""
    assert client.can_fingerprint() == (False, "No AcoustID API key configured")
    monkeypatch.setattr(ac, "ACOUSTID_AVAILABLE", False)
    assert ready(True).can_fingerprint() == (False, "pyacoustid library not installed")


def _context(client, *, manual):
    logged = []
    return SimpleNamespace(
        db=None, transfer_folder="/music", manual=manual,
        config_manager=SimpleNamespace(get=lambda key, default=None: default,
                                       set=lambda *a, **k: None),
        acoustid_client=client, create_finding=None,
        report_progress=lambda **kw: logged.append(kw),
        update_progress=lambda *a, **k: None, check_stop=lambda: False,
        wait_if_paused=lambda: False, sleep_or_stop=lambda *a, **k: False,
    ), logged


class _Walked(Exception):
    pass


def _job(monkeypatch):
    job = AcoustIDScannerJob()

    def walk(*_a, **_k):
        raise _Walked()
    monkeypatch.setattr(job, "_load_db_tracks", walk)
    return job


def test_a_run_started_by_hand_scans_with_verification_off(ready, monkeypatch):
    client = ready(False)
    monkeypatch.setattr(client, "test_api_key", lambda: (True, "ok"))
    context, logged = _context(client, manual=True)
    # it got past the gate and went for the library
    with pytest.raises(_Walked):
        _job(monkeypatch).scan(context)
    assert not any("unavailable" in str(line.get("log_line", "")) for line in logged)


def test_the_schedule_stays_off_with_verification_off_and_says_why(ready, monkeypatch):
    context, logged = _context(ready(False), manual=False)
    result = _job(monkeypatch).scan(context)
    # nothing walked, and it's a skip, not a failed run in the history
    assert (result.scanned, result.errors) == (0, 0)
    assert "Run this job by hand" in logged[-1]["log_line"]
    assert logged[-1]["log_type"] == "info"


def test_the_schedule_scans_as_before_with_verification_on(ready, monkeypatch):
    client = ready(True)
    monkeypatch.setattr(client, "test_api_key", lambda: (True, "ok"))
    context, _ = _context(client, manual=False)
    with pytest.raises(_Walked):
        _job(monkeypatch).scan(context)


def test_a_manual_run_with_no_key_is_refused_as_an_error(ready, monkeypatch):
    client = ready(False)
    client._api_key = ""
    context, logged = _context(client, manual=True)
    result = _job(monkeypatch).scan(context)
    assert result.errors == 1
    assert "No AcoustID API key configured" in logged[-1]["log_line"]


def test_a_file_check_follows_who_asked(ready):
    """the per-file gate is the same split: a download is skipped with
    verification off, a manual scan's file is not."""
    seen = []

    class _Stop(Exception):
        pass

    def lookup(*_a, **_k):
        seen.append("looked up")
        raise _Stop()

    verifier = AcoustIDVerification()
    verifier.acoustid_client = ready(False)
    verifier.acoustid_client.lookup_with_status = lookup
    verdict, reason = verifier.verify_audio_file("/x.flac", "Song", "Band")
    assert (verdict, reason) == (VerificationResult.SKIP, "AcoustID verification is disabled")
    assert seen == []
    verdict, reason = verifier.verify_audio_file("/x.flac", "Song", "Band", require_enabled=False)
    assert seen == ["looked up"]
    assert reason != "AcoustID verification is disabled"


def test_the_worker_tells_a_job_who_started_it():
    assert JobContext(db=None, transfer_folder="", config_manager=None).manual is False
    assert JobContext(db=None, transfer_folder="", config_manager=None, manual=True).manual is True
    import inspect
    from core.repair_worker import RepairWorker
    assert "manual=bool(forced)" in inspect.getsource(RepairWorker._run_job)
