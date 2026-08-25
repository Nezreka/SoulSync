"""Download and scan are one pipeline, and this is what says so.

The requirement, stated plainly: the same file with the same expected metadata
must get the same verdict whether it came through the download or through the
library scan. That was true of the final decision — both called
``audio_verification.evaluate`` — and false of everything leading up to it. The
scan had its own copy of the availability check, the lookup, the confidence
floor and the alias wiring, and it never enriched title-less recordings from
MusicBrainz the way the download did. Five steps of drift around one shared
conclusion.

The scan now calls ``AcoustIDVerification.verify_audio_file`` — the download's
entry point. Two things legitimately differ and always will: where the expected
title and artist come from (the download has a provider payload, the scan has a
catalogue row) and what each side does with the verdict (quarantine and retry
vs. persist and file a finding). Everything between those two ends is one path,
and this test walks both ends of it over the same inputs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.acoustid_verification import AcoustIDVerification, VerificationResult
from core.repair_jobs.acoustid_scanner import AcoustIDScannerJob
from core.repair_jobs.base import JobResult

# (label, expected title, expected artist, fingerprinted title, artist, score)
CASES = [
    ("exact match", "Apetitan", "Sawano Hiroyuki", "APETITAN", "Sawano Hiroyuki", 1.0),
    ("cross-script artist", "Apetitan", "Sawano Hiroyuki", "APETITAN", "澤野弘之", 1.0),
    ("cross-script title", "Zankoku na Tenshi no Thesis", "Yoko Takahashi",
     "残酷な天使のテーゼ", "Yoko Takahashi", 0.90),
    ("both cross-script", "Zankoku na Tenshi no Thesis", "Yoko Takahashi",
     "残酷な天使のテーゼ", "高橋洋子", 0.90),
    ("genuinely wrong song", "Barricades", "Sawano Hiroyuki",
     "Wanna Be Startin' Somethin'", "Michael Jackson", 1.0),
    ("genuinely wrong artist", "Apetitan", "Sawano Hiroyuki", "Apetitan",
     "Taylor Swift", 1.0),
    ("fingerprint below the floor", "Apetitan", "Sawano Hiroyuki", "APETITAN",
     "Sawano Hiroyuki", 0.40),
    ("cover by another artist", "Hurt", "Johnny Cash", "Hurt", "Nine Inch Nails", 0.95),
]


def _client(title, artist, score):
    return SimpleNamespace(
        lookup_with_status=lambda _p: {
            "status": "ok", "best_score": score, "recording_mbids": ["mb-1"],
            "recordings": [{"mbid": "mb-1", "title": title, "artist": artist,
                            "score": score}],
        },
    )


def _download_verdict(client, title, artist):
    verifier = AcoustIDVerification()
    verifier.acoustid_client = client
    probe: dict = {}
    result, _msg = verifier.verify_audio_file("/x.flac", title, artist, probe)
    return result, probe.get("_acoustid_decision")


def _scan_verdict(monkeypatch, client, title, artist):
    """Drive the scanner over one catalogue row carrying the same metadata.

    The scan has no verdict column of its own on this branch, so its verdict is
    read from what it DOES with one: an untagged file becomes 'verified' on a
    PASS and 'unverified' on a SKIP, a FAIL raises a finding and moves nothing,
    and a lookup that reached no judgement writes nothing at all.
    """
    persisted, findings = [], []
    context = SimpleNamespace(
        db=None, transfer_folder="/music",
        config_manager=SimpleNamespace(get=lambda *a, **k: None,
                                       set=lambda *a, **k: None),
        acoustid_client=None,
        create_finding=lambda **kw: findings.append(kw) or True,
        report_progress=lambda **kw: None, update_progress=lambda *a, **k: None,
        check_stop=lambda: False, wait_if_paused=lambda: False,
        sleep_or_stop=lambda *a, **k: False,
    )

    job = AcoustIDScannerJob()
    monkeypatch.setattr(job, "_resolve_path", lambda p, _c: p)
    monkeypatch.setattr(job, "_persist_status",
                        lambda *a, **kw: persisted.append(a[4]))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _p: {})
    job._scan_file("/music/track.flac", "42",
                   {"title": title, "artist": artist, "track_artist": artist,
                    "album_artist": artist},
                   client, context, JobResult(), 0.80, 0.70, 0.60)

    if findings:
        return "fail"
    if not persisted:
        return None
    return {"verified": "pass", "unverified": "skip"}.get(persisted[0], persisted[0])


@pytest.mark.parametrize(
    "label,title,artist,found_title,found_artist,score",
    CASES, ids=[c[0] for c in CASES])
def test_both_paths_reach_the_same_verdict(
    monkeypatch, label, title, artist, found_title, found_artist, score,
):
    monkeypatch.setattr(
        "core.acoustid_verification._resolve_expected_artist_aliases", lambda _n: [])
    client = _client(found_title, found_artist, score)

    download_result, download_outcome = _download_verdict(client, title, artist)
    scan_status = _scan_verdict(monkeypatch, client, title, artist)

    if download_outcome is None:
        # The verifier never reached a judgement — the scan must not invent
        # one, and above all must not write "unverified" off the back of it.
        assert download_result == VerificationResult.SKIP
        assert scan_status is None
    else:
        assert scan_status == download_outcome.decision.value, (
            f"{label}: download said {download_outcome.decision.value}, "
            f"scan said {scan_status}")


def test_the_scanner_does_not_carry_its_own_verification_logic():
    """A structural guard, because the drift came back the moment the two
    halves could evolve apart. The scan may not call the decision core, resolve
    aliases, gate on the fingerprint score or enrich recordings itself — all of
    that belongs to the one entry point it now shares with the download."""
    import inspect

    from core.repair_jobs import acoustid_scanner

    src = inspect.getsource(acoustid_scanner)
    assert "verify_audio_file" in src
    for owned_by_the_verifier in (
        "evaluate(",
        "_resolve_expected_artist_aliases",
        "MIN_ACOUSTID_SCORE",
        "_enrich_recordings_from_musicbrainz",
    ):
        assert owned_by_the_verifier not in src, (
            f"{owned_by_the_verifier} is the verifier's job; the scan calling it "
            f"is how the two paths drifted apart before"
        )


def test_an_unusable_client_stops_the_run_instead_of_flagging_the_library():
    """`verify_audio_file` answers SKIP when AcoustID is not usable, and a SKIP
    on an untagged file is 'unverified'. Without a check up front, a missing API
    key would walk every file in the library into the review queue and report a
    clean run."""
    job = AcoustIDScannerJob()
    scanned = []
    context = SimpleNamespace(
        db=None, transfer_folder="/music",
        config_manager=SimpleNamespace(get=lambda key, default=None: default,
                                       set=lambda *a, **k: None),
        acoustid_client=SimpleNamespace(
            is_available=lambda: (False, "no API key configured")),
        create_finding=lambda **kw: True,
        report_progress=lambda **kw: None, update_progress=lambda *a, **k: None,
        check_stop=lambda: False, wait_if_paused=lambda: False,
        sleep_or_stop=lambda *a, **k: False,
    )
    context.db = SimpleNamespace(_get_connection=lambda: scanned.append("db"))

    result = job.scan(context)

    assert result.errors == 1
    assert result.scanned == 0
    assert scanned == [], "the run must stop before it reads the catalogue"
