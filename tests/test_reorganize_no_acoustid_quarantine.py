"""Reorganize must not re-adjudicate the identity of a file already in the library.

A reorganize stages a COPY of the user's own library file and runs it through the
full download post-process, AcoustID identity check included. When the fingerprint
disagrees, that check quarantines the file — so moving a track you already own
into a differently-named folder ended in:

    AcoustID verification result: fail - Audio mismatch:
        'APETITAN' by '澤野弘之' — expected artist not found
    File quarantined: downloads/ss_quarantine/...02 - Apetitan.flac.quarantined
    [Queue] Finished ... status=failed, moved=0, failed=2

The library original survives (only the staged copy is quarantined), which is why
the run had to be repeated with "Rename only" to get anywhere — the reported
"reorganize only works the second time". It also left a ~40 MB quarantined copy
per attempt and a quarantine list full of files the user still owns.

The duration leg was excluded from this pipeline for exactly the same reason
(#804): a re-resolved API tracklist may legitimately disagree with the user's
copy. A fingerprint may too — a different master, a regional release, or an
artist credited in a different script, as here. Identity of library files is
adjudicated by the AcoustID Scanner, which raises a finding instead of moving
anyone's audio.
"""

from __future__ import annotations

import pytest

from core.imports.pipeline import _should_skip_quarantine_check
from core.library_reorganize import _build_post_process_context


@pytest.fixture(autouse=True)
def _preserve(monkeypatch):
    monkeypatch.setattr("core.library_reorganize._preserve_casing_enabled", lambda: True)
    monkeypatch.setattr("core.library_reorganize._feat_in_title_enabled", lambda: False)


def _ctx():
    return _build_post_process_context(
        {"id": "sp1", "name": "AoT S2 OST", "release_date": "2017",
         "total_tracks": 45, "images": [{"url": ""}]},
        {"name": "Apetitan", "track_number": 2, "disc_number": 1,
         "artists": [{"name": "Sawano Hiroyuki"}]},
        "Sawano Hiroyuki", "AoT S2 OST", 2, local_title="Apetitan")


def test_the_acoustid_quarantine_leg_is_skipped():
    assert _should_skip_quarantine_check(_ctx(), "acoustid") is True


def test_the_corruption_legs_still_run():
    """Skipping identity is not skipping safety: a truncated or unparseable file
    must still be caught before it is moved anywhere."""
    ctx = _ctx()
    assert _should_skip_quarantine_check(ctx, "integrity") is False
    assert _should_skip_quarantine_check(ctx, "bit_depth") is False


def test_a_normal_download_still_gets_the_identity_check():
    """The bypass belongs to the reorganize context, not to the pipeline."""
    assert _should_skip_quarantine_check({}, "acoustid") is False


# ── and it must not downgrade a verdict the download already made ────────────
#
# The file carries the import's decision in its own SOULSYNC_VERIFICATION tag
# (core/matching/verification_status.py), and _persist_verification_status
# rewrites that tag on every successful post-process exit. Skipping the identity
# leg therefore must not be reported as "ran and could not confirm" — that maps
# to `unverified` and would quietly downgrade a `verified` file every time it is
# moved. Choosing not to run a check is not a finding.

def test_a_reorganize_makes_no_verification_claim():
    from core.matching.verification_status import status_for_import

    assert status_for_import(_ctx()) is None


def test_a_real_result_still_maps_to_a_status():
    from core.matching.verification_status import (
        UNVERIFIED, VERIFIED, status_for_import,
    )

    assert status_for_import({"_acoustid_result": "pass"}) == VERIFIED
    assert status_for_import({"_acoustid_result": "skip"}) == UNVERIFIED
