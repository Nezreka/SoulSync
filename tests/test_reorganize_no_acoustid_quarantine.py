"""Reorganize must not re-adjudicate the identity of a file already in the library.

A reorganize used to stage a COPY of the user's own library file and run it
through the full download post-process, AcoustID identity check included. When
the fingerprint disagreed, that check quarantined the file — so moving a track
you already own into a differently-named folder ended in:

    AcoustID verification result: fail - Audio mismatch:
        'APETITAN' by '澤野弘之' — expected artist not found
    File quarantined: downloads/ss_quarantine/...02 - Apetitan.flac.quarantined
    [Queue] Finished ... status=failed, moved=0, failed=2

The library original survived (only the staged copy was quarantined), which is
why the run had to be repeated with "Rename only" to get anywhere — the reported
"reorganize only works the second time". It also left a ~40 MB quarantined copy
per attempt and a quarantine list full of files the user still owns.

#1182 answered this with an opt-out: `_skip_quarantine_check: 'acoustid'` in the
reorganize context, alongside `is_local_import` (#804) for the duration leg,
which was excluded for the same reason — a re-resolved API tracklist may
legitimately disagree with the user's copy, and so may a fingerprint (a
different master, a regional release, or an artist credited in another script,
as here).

The answer now is structural rather than a flag: a reorganize MOVES files and
runs no acceptance check at all, so there is nothing to opt out of. Identity of
library files is adjudicated by the AcoustID Scanner, which raises a finding
instead of moving anyone's audio.
"""

from __future__ import annotations

from core.imports.pipeline import _should_skip_quarantine_check
from core.library_reorganize import _build_post_process_context


def _ctx():
    return _build_post_process_context(
        {"id": "sp1", "name": "AoT S2 OST", "release_date": "2017",
         "total_tracks": 45, "images": [{"url": ""}]},
        {"name": "Apetitan", "track_number": 2, "disc_number": 1,
         "artists": [{"name": "Sawano Hiroyuki"}]},
        "Sawano Hiroyuki", "AoT S2 OST", 2)


def test_no_acceptance_check_runs_so_there_is_nothing_to_opt_out_of():
    """The context carries neither flag, because it no longer reaches a
    pipeline that reads them. The reorganize context exists for one purpose
    now: handing the shared path builder the shape a download hands it."""
    ctx = _ctx()
    assert "_skip_quarantine_check" not in ctx
    assert "is_local_import" not in ctx


def test_the_executor_that_ran_the_check_is_gone():
    """The quarantine happened inside `reorganize_album`, which staged a copy
    and called `_post_process_matched_download`. Both are gone; the only
    executor moves the file the user already has."""
    import core.library_reorganize as lr
    assert not hasattr(lr, "reorganize_album")
    assert not hasattr(lr, "_stage_track")
    assert not hasattr(lr, "_run_post_process_for_track")
    assert hasattr(lr, "reorganize_album_rename_only")


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
