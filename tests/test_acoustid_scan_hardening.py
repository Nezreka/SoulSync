"""What a scan may write down, and what it may not.

Every case here is one where the pipeline recorded a VERDICT about a file on
the strength of something that was never about the file: an integration that
was switched off, an exception in our own code, a lookup that never cleared the
confidence floor. A scan that cannot check a file has to say so by writing
nothing — the one thing the old code would not do.
"""

from types import SimpleNamespace

import pytest

from core.acoustid_verification import AcoustIDVerification, VerificationResult
from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob


def _client(**overrides):
    base = {
        "is_available": lambda: (True, "ready"),
        "lookup_with_status": lambda _p: {
            "status": "ok", "best_score": 0.99,
            "recordings": [{"mbid": "rec-1", "title": "T", "artist": "A"}],
            "recording_mbids": ["rec-1"],
        },
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _verifier(client):
    verifier = AcoustIDVerification()
    verifier.acoustid_client = client
    return verifier


# --- the run, not the file -------------------------------------------------


def _scan_context(client, **overrides):
    logged = []
    context = SimpleNamespace(
        db=None,
        transfer_folder="/music",
        config_manager=SimpleNamespace(
            get=lambda key, default=None: default, set=lambda *a, **k: None),
        acoustid_client=client,
        create_finding=None,
        report_progress=lambda **kwargs: logged.append(kwargs),
        update_progress=lambda *a, **k: None,
        check_stop=lambda: False,
        wait_if_paused=lambda: False,
        sleep_or_stop=lambda *a, **k: False,
    )
    for key, value in overrides.items():
        setattr(context, key, value)
    return context, logged


@pytest.mark.parametrize("reason", [
    "AcoustID verification is disabled",
    "No AcoustID API key configured",
    "Chromaprint library not installed (install libchromaprint1)",
])
def test_an_unusable_client_stops_the_run_instead_of_stamping_the_library(
        monkeypatch, reason):
    """`acoustid.enabled` defaults to False and the scan job does not.

    `verify_audio_file` probes availability per file and answers SKIP when the
    client is unusable. For the scan that means every row in the library gets
    `acoustid_status='skip'` — "checked, no claim" — over whatever an earlier
    working scan had concluded, and the run reports zero errors. Availability is
    a property of the run.
    """
    job = AcoustIDScannerJob()
    context, logged = _scan_context(_client(is_available=lambda: (False, reason)))
    monkeypatch.setattr(job, "_load_db_tracks", lambda *_a, **_k: (
        _ for _ in ()).throw(AssertionError("must not walk the library")))

    result = job.scan(context)

    assert result.scanned == 0
    assert result.errors == 1
    assert any(reason in str(entry.get("log_line", "")) for entry in logged)


def test_a_usable_client_still_runs(monkeypatch):
    job = AcoustIDScannerJob()
    context, _logged = _scan_context(_client())
    monkeypatch.setattr(job, "_load_db_tracks", lambda *_a, **_k: {})

    assert job.scan(context).errors == 0


def test_a_client_without_the_probe_is_left_alone(monkeypatch):
    """Injected clients (tests, callers with their own) need not implement it."""
    job = AcoustIDScannerJob()
    context, _logged = _scan_context(object())
    monkeypatch.setattr(job, "_load_db_tracks", lambda *_a, **_k: {})

    assert job.scan(context).errors == 0


# --- our own faults are not verdicts ---------------------------------------


def test_an_exception_inside_verification_is_reported_as_an_error():
    """Fail-open is right for the download and wrong as a recorded result.

    Reported as SKIP, a database error or a MusicBrainz outage mid-verification
    reached the scan as `outcome is None` and was persisted as 'skip' — a
    completed check. With `require_verified` on it was worse: the download path
    turns SKIP into a rejection, so an infrastructure blip threw away a correct
    file. Both callers already treat ERROR as "not the file's fault".
    """
    def _boom(_path):
        raise RuntimeError("database is locked")

    verdict, message = _verifier(
        _client(lookup_with_status=_boom)).verify_audio_file("/x.flac", "T", "A")

    assert verdict is VerificationResult.ERROR
    assert "database is locked" in message


def test_a_lookup_below_the_floor_claims_no_recording_identity():
    """`_acoustid_recording_mbids` is "what this verdict was made against".

    Written before the confidence gate it also described lookups that never
    reached a verdict — and a later scan reads those ids as an identity contract
    it is allowed to trust.
    """
    context = {}
    verdict, _msg = _verifier(_client(lookup_with_status=lambda _p: {
        "status": "ok", "best_score": 0.10,
        "recordings": [{"mbid": "rec-1", "title": "T", "artist": "A"}],
        "recording_mbids": ["rec-1"],
    })).verify_audio_file("/x.flac", "T", "A", context)

    assert verdict is VerificationResult.SKIP
    assert "_acoustid_recording_mbids" not in context


def test_a_lookup_above_the_floor_does_claim_one():
    context = {}
    _verdict, _msg = _verifier(_client()).verify_audio_file(
        "/x.flac", "T", "A", context)

    assert context["_acoustid_recording_mbids"] == ["rec-1"]


# --- what a verdict is allowed to say about a file --------------------------


def _scan_one(monkeypatch, *, expected, recordings, tag=None, best_score=0.99):
    """Drive ``_scan_file`` once and hand back every persisted status write."""
    job = AcoustIDScannerJob()
    persisted = []
    monkeypatch.setattr(job, "_persist_status",
                        lambda *args, **kwargs: persisted.append((args, kwargs)))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _p: tag or {})
    monkeypatch.setattr("core.library2.manual_skips.check_is_skipped",
                        lambda *_a, **_k: False)
    # The alias chain is a live MusicBrainz + DB walk; these cases are about
    # what happens once it has answered.
    monkeypatch.setattr("core.acoustid_verification._resolve_expected_artist_aliases",
                        lambda _name: [])

    client = _client(lookup_with_status=lambda _p: {
        "status": "ok", "best_score": best_score, "recordings": recordings,
        "recording_mbids": [r["mbid"] for r in recordings if r.get("mbid")],
    })
    context, _logged = _scan_context(client, create_finding=lambda **_k: True)
    result = SimpleNamespace(scanned=0, skipped=0, findings_created=0, errors=0)
    job._scan_file("/music/x.flac", "lib2:1", expected, client, context, result,
                   fp_threshold=0.8, title_threshold=0.85, artist_threshold=0.6)
    return persisted


_KANJI = [{"mbid": "rec-1", "title": "APETITAN", "artist": "澤野弘之"}]


def test_a_skip_on_the_recording_the_import_checked_gives_the_standing_back(
        monkeypatch):
    """Healing for the files an earlier build of this scanner demoted.

    It read the standing from the file TAG and wrote to the catalogue COLUMN, so
    a file whose tag had gone missing was recorded 'unverified' however the
    import had judged it — and nothing since gives it back, because a SKIP is by
    design not allowed to move the standing. Identity settles it: the
    fingerprint still lands on the recording the import checked this file
    against, and only files the import let through are in the library at all.
    """
    persisted = _scan_one(monkeypatch, recordings=_KANJI, expected={
        "title": "Apetitan", "artist": "Sawano Hiroyuki",
        "db_verification_status": "unverified",
        "import_recording_mbids": frozenset({"rec-1"}),
    })

    assert persisted, "the scan recorded nothing at all"
    args, kwargs = persisted[-1]
    assert args[4] == "verified"
    assert kwargs["acoustid_status"] == "skip"


def test_a_skip_on_a_different_recording_leaves_unverified_alone(monkeypatch):
    """Without that identity there is no evidence, and no standing to restore."""
    persisted = _scan_one(monkeypatch, recordings=_KANJI, expected={
        "title": "Apetitan", "artist": "Sawano Hiroyuki",
        "db_verification_status": "unverified",
        "import_recording_mbids": frozenset({"some-other-recording"}),
    })

    args, _kwargs = persisted[-1]
    assert args[4] == "unverified"


def test_a_skip_never_invents_a_standing_for_a_file_that_had_none(monkeypatch):
    persisted = _scan_one(monkeypatch, recordings=_KANJI, expected={
        "title": "Apetitan", "artist": "Sawano Hiroyuki",
        "import_recording_mbids": frozenset({"rec-1"}),
    })

    args, _kwargs = persisted[-1]
    assert args[4] == "unverified"


def test_the_recorded_reason_is_the_one_this_scan_reached(monkeypatch):
    """The Check column's tooltip and its badge have to come from one run.

    ``acoustid_message`` is written at import time too, and the scan used to
    write only the status — so a row could render "Skipped" over a tooltip
    reading "Audio verified: ... artist 100%", the download's message under the
    scan's verdict.
    """
    persisted = _scan_one(monkeypatch, recordings=_KANJI, expected={
        "title": "Apetitan", "artist": "Sawano Hiroyuki",
        "db_verification_status": "verified",
    })

    _args, kwargs = persisted[-1]
    assert kwargs["acoustid_status"] == "skip"
    assert "different script" in kwargs["acoustid_message"]
