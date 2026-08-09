"""#1132 — "AcousticID Scanner ID Issues": correct detection, wrong suggestion.

The reporter's files were genuinely mistagged (the scanner was right to flag
them) but the suggested replacement was wrong nearly every time:

  * a file of Chicago's "You're the Inspiration", tagged "Stay the Night",
    was reported as "Saturday in the Park" — a third, unrelated Chicago song;
  * several correct tracks were reported as instrumental / karaoke / acoustic
    versions of themselves.

Mechanism: `acoustid.parse_lookup_result` yields one entry per RECORDING but
uses the enclosing RESULT's score, so a single fingerprint match commonly
produces many recordings that all carry the SAME score. Their order is
MusicBrainz's, not a ranking. A long-lived AcoustID entry accumulates
mis-submitted recording links, so that tie can span different songs.

`evaluate` then picks the recording most similar to the EXPECTED title to
report — but this code path only runs when the expected title is already
believed wrong, so it is ranking candidates against noise, and among tied
candidates it lands somewhere arbitrary.

Deciding "this file is mislabelled" is unaffected (that asks whether ANY
candidate matches). Only the "it is actually X" claim has to be withheld.
"""

from __future__ import annotations

import pytest

from core.matching.audio_verification import fingerprint_is_ambiguous


def rec(title, artist="Chicago", score=0.95):
    return {"title": title, "artist": artist, "score": score, "mbid": title}


# ── the reported case ────────────────────────────────────────────────────────

def test_tied_recordings_naming_different_songs_are_ambiguous():
    """The Chicago case: one fingerprint, three different songs, same score."""
    recordings = [
        rec("Saturday in the Park"),
        rec("You're the Inspiration"),
        rec("Stay the Night"),
    ]
    assert fingerprint_is_ambiguous(recordings) is True


def test_tied_original_and_instrumental_are_ambiguous():
    """The other reported shape — a variant tied with the original."""
    recordings = [rec("Hard to Say I'm Sorry"),
                  rec("Hard to Say I'm Sorry (Instrumental)")]
    assert fingerprint_is_ambiguous(recordings) is True


# ── cases that must stay actionable ──────────────────────────────────────────

def test_single_recording_is_not_ambiguous():
    assert fingerprint_is_ambiguous([rec("Saturday in the Park")]) is False


def test_same_song_on_many_releases_is_not_ambiguous():
    """The common, healthy case: one song linked to many releases. Same title
    repeated is not a conflict, and must still produce a usable finding."""
    recordings = [rec("Saturday in the Park") for _ in range(5)]
    assert fingerprint_is_ambiguous(recordings) is False


def test_title_differences_that_normalize_away_are_not_ambiguous():
    """Punctuation/casing variants of one title are the same song."""
    recordings = [rec("Saturday In The Park"), rec("saturday in the park")]
    assert fingerprint_is_ambiguous(recordings) is False


def test_a_clear_winner_outranks_lower_scored_noise():
    """When one recording genuinely scores highest, the lower-scored links are
    irrelevant — the claim is safe to make."""
    recordings = [
        rec("You're the Inspiration", score=0.97),
        rec("Saturday in the Park", score=0.71),
        rec("Stay the Night", score=0.68),
    ]
    assert fingerprint_is_ambiguous(recordings) is False


def test_ties_only_count_at_the_top_score():
    """Two different songs tied for SECOND place don't make the winner unsafe."""
    recordings = [
        rec("You're the Inspiration", score=0.97),
        rec("Saturday in the Park", score=0.71),
        rec("Stay the Night", score=0.71),
    ]
    assert fingerprint_is_ambiguous(recordings) is False


# ── defensive shapes ─────────────────────────────────────────────────────────

def test_empty_recordings_is_not_ambiguous():
    assert fingerprint_is_ambiguous([]) is False


def test_scoreless_recordings_fall_back_to_distinct_titles():
    """Older/synthetic payloads without per-recording scores: nothing
    distinguishes the candidates, so >1 distinct title is ambiguous."""
    assert fingerprint_is_ambiguous(
        [{"title": "A"}, {"title": "B"}]) is True
    assert fingerprint_is_ambiguous(
        [{"title": "A"}, {"title": "A"}]) is False


def test_blank_titles_are_ignored_not_counted_as_a_rival():
    recordings = [rec("Saturday in the Park"), rec(""), rec(None)]
    assert fingerprint_is_ambiguous(recordings) is False


def test_ambiguous_finding_still_fires_but_withholds_the_claim():
    """The detection must survive — only the "is actually X" wording goes.

    Suppressing the finding entirely would hide genuinely mislabelled files,
    which is the opposite of what the tool is for.
    """
    from types import SimpleNamespace
    from tests.test_acoustid_scanner import (
        AcoustIDScannerJob, JobResultStub, _make_finding_capturing_context)

    job = AcoustIDScannerJob()
    captured = []
    context = _make_finding_capturing_context(
        track_row=("99", "Expected Title", "Expected Artist",
                   "/music/track.flac", 1, "Album", None, None),
        captured=captured,
    )
    fake = SimpleNamespace(fingerprint_and_lookup=lambda fpath: {
        'best_score': 0.99,
        'recordings': [
            {'title': 'Saturday in the Park', 'artist': 'Chicago', 'score': 0.99},
            {'title': "You're the Inspiration", 'artist': 'Chicago', 'score': 0.99},
        ],
    })

    job._scan_file('/music/track.flac', '99',
                   {'title': 'Expected Title', 'artist': 'Expected Artist'},
                   fake, context, JobResultStub(),
                   fp_threshold=0.85, title_threshold=0.85, artist_threshold=0.6)

    assert len(captured) == 1, "a genuinely mislabelled file must still be reported"
    finding = captured[0]
    assert finding['details']['ambiguous'] is True
    assert 'is actually' not in finding['title'], (
        f"ambiguous finding still asserts a single track: {finding['title']!r}")
    assert len(finding['details']['candidates']) == 2


def test_retag_is_refused_for_an_ambiguous_finding():
    """The guard that stops a wrong suggestion becoming wrong data."""
    from core.repair_worker import RepairWorker

    worker = RepairWorker.__new__(RepairWorker)
    out = worker._fix_acoustid_mismatch(
        'track', '99', '/music/track.flac',
        {'_fix_action': 'retag', 'ambiguous': True,
         'acoustid_title': 'Saturday in the Park',
         'candidates': ['"Saturday in the Park" by Chicago',
                        '"You\'re the Inspiration" by Chicago']},
    )
    assert out['success'] is False
    assert 'several different recordings' in out['error']


def test_the_scanner_imports_and_uses_the_guard():
    """Pin the wiring — the pure helper is useless if the scanner drops it."""
    import inspect
    from core.repair_jobs import acoustid_scanner

    src = inspect.getsource(acoustid_scanner.AcoustIDScannerJob._scan_file)
    assert "fingerprint_is_ambiguous(fp_result['recordings'])" in src, (
        "the ambiguity guard is no longer consulted before creating a finding")
    # It must run BEFORE the finding is built, not after.
    assert src.index("fingerprint_is_ambiguous") < src.index("create_finding")
