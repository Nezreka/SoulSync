"""Direct unit tests for ``core.imports.album_matching`` — the lifted
helper that powers ``AutoImportWorker._match_tracks``.

The original test file (``test_auto_import_multi_disc_matching.py``)
exercised the matching logic via the worker, requiring monkeypatches
on ``_read_file_tags`` + mocks on the metadata client. These tests
exercise the helper directly with dict inputs / dict outputs — no I/O,
no class instantiation, no patches.

Together with the worker-level tests, the helper has full behavior
coverage:
- Dedup: same-(disc, track) collapses, cross-disc preserves
- Match: per-component scoring, threshold, position weights, cross-disc
  consolation, near-position bonus
- Edge cases: tag-less files (track_number=0), missing artist tags,
  cross-disc collision when one side has no disc tag
"""

from __future__ import annotations

from difflib import SequenceMatcher

from core.imports.album_matching import (
    ALBUM_WEIGHT,
    ARTIST_WEIGHT,
    CROSS_DISC_POSITION_WEIGHT,
    MATCH_THRESHOLD,
    NEAR_POSITION_WEIGHT,
    POSITION_WEIGHT,
    TITLE_WEIGHT,
    dedupe_files_by_position,
    match_files_to_tracks,
    score_file_against_track,
)


# ---------------------------------------------------------------------------
# Stand-in similarity + quality_rank — match real worker behavior closely
# enough that test scores reflect production behavior.
# ---------------------------------------------------------------------------


def _sim(a: str, b: str) -> float:
    """Mirror of the worker's _similarity (case-folded SequenceMatcher)."""
    return SequenceMatcher(None, (a or '').lower(), (b or '').lower()).ratio()


def _qrank(ext: str) -> int:
    """Mirror of the worker's _quality_rank."""
    ranks = {'.flac': 100, '.alac': 95, '.wav': 80, '.aac': 60,
             '.ogg': 50, '.opus': 50, '.m4a': 60, '.mp3': 30, '.wma': 20}
    return ranks.get((ext or '').lower(), 0)


def _tags(*, title='', artist='Artist', album='Album', track=0, disc=1):
    return {
        'title': title, 'artist': artist, 'album': album,
        'track_number': track, 'disc_number': disc, 'year': '',
    }


# ---------------------------------------------------------------------------
# Constants — pin the weights so accidental tweaks fail at the test boundary
# ---------------------------------------------------------------------------


def test_constants_sum_to_one():
    """Sum of TITLE + ARTIST + POSITION + ALBUM should equal 1.0 in
    the happy case (perfect agreement). Catches accidental drift if
    someone edits one weight without checking the rest. Float tolerance
    because 0.45 + 0.15 + 0.30 + 0.10 has a 1e-16 rounding error."""
    total = TITLE_WEIGHT + ARTIST_WEIGHT + POSITION_WEIGHT + ALBUM_WEIGHT
    assert abs(total - 1.0) < 1e-9


def test_match_threshold_requires_more_than_position_alone():
    """Pin the design intent: a file matching ONLY on position
    (perfect track + disc, zero title similarity) should NOT meet
    the threshold. The matcher requires meaningful title agreement
    AT LEAST in addition to position. Catches accidental threshold
    drops that would let position-only matches sneak through."""
    assert MATCH_THRESHOLD > POSITION_WEIGHT


# ---------------------------------------------------------------------------
# dedupe_files_by_position — pure-function tests
# ---------------------------------------------------------------------------


def test_dedupe_keeps_higher_quality_at_same_position():
    files = ['/a/track1.mp3', '/a/track1.flac']
    file_tags = {
        '/a/track1.mp3': _tags(track=1, disc=1),
        '/a/track1.flac': _tags(track=1, disc=1),
    }
    result = dedupe_files_by_position(files, file_tags, quality_rank=_qrank)
    assert result == ['/a/track1.flac']


def test_dedupe_preserves_same_track_across_discs():
    """The fix for the multi-disc bug: track_number=1 on disc 1 and
    track_number=1 on disc 2 are different positions, both survive."""
    files = ['/a/d1t1.flac', '/a/d2t1.flac']
    file_tags = {
        '/a/d1t1.flac': _tags(track=1, disc=1),
        '/a/d2t1.flac': _tags(track=1, disc=2),
    }
    result = dedupe_files_by_position(files, file_tags, quality_rank=_qrank)
    assert set(result) == {'/a/d1t1.flac', '/a/d2t1.flac'}


def test_dedupe_passes_through_files_with_no_track_number():
    """Files with track_number=0 (no tag) can't be deduped — keep them
    all so the matcher gets a chance to title-match them."""
    files = ['/a/no_tag_a.mp3', '/a/no_tag_b.mp3', '/a/no_tag_c.mp3']
    file_tags = {f: _tags(title='Untagged', track=0, disc=1) for f in files}
    result = dedupe_files_by_position(files, file_tags, quality_rank=_qrank)
    assert set(result) == set(files)


def test_dedupe_keeps_first_when_quality_equal():
    """Two files at same position, same quality — first one wins."""
    files = ['/a/first.flac', '/a/second.flac']
    file_tags = {
        '/a/first.flac': _tags(track=1, disc=1),
        '/a/second.flac': _tags(track=1, disc=1),
    }
    result = dedupe_files_by_position(files, file_tags, quality_rank=_qrank)
    assert result == ['/a/first.flac']


# ---------------------------------------------------------------------------
# score_file_against_track — per-component scoring
# ---------------------------------------------------------------------------


def test_score_perfect_agreement_equals_one():
    """Title + artist + (disc, track) + album all match → score = 1.0."""
    track = {
        'name': 'Song', 'track_number': 5, 'disc_number': 2,
        'artists': [{'name': 'Artist'}],
    }
    tags = _tags(title='Song', artist='Artist', album='Album', track=5, disc=2)
    score = score_file_against_track(
        '/a/file.flac', tags, track,
        target_album='Album', similarity=_sim,
    )
    assert abs(score - 1.0) < 0.001


def test_score_position_match_requires_both_disc_and_track():
    """Same track number, different disc → only CROSS_DISC bonus, not
    full POSITION bonus. This is the regression fix for multi-disc
    cross-collisions."""
    track = {'name': 'X', 'track_number': 6, 'disc_number': 1, 'artists': []}
    # File for disc 2 track 6 — same number, wrong disc
    tags = _tags(title='X', track=6, disc=2)
    score = score_file_against_track(
        '/a/file.flac', tags, track,
        target_album='', similarity=_sim,
    )
    # Title weight (1.0) + cross-disc consolation (0.05) + nothing else
    expected = TITLE_WEIGHT + CROSS_DISC_POSITION_WEIGHT
    assert abs(score - expected) < 0.001


def test_cross_disc_consolation_is_load_bearing_for_imperfect_titles():
    """Pin the design rationale for ``CROSS_DISC_POSITION_WEIGHT`` so
    the magic number isn't silently regressable.

    Scenario: file has the right title spelling but the metadata
    source returns a slightly-different version (e.g. "(Remix)"
    suffix), AND the file's disc tag is wrong / missing while the
    track number agrees. The bonus is sized so this case still
    matches:

        title_only_score = sim("Auntie Diaries",
                               "Auntie Diaries (Remix)") * 0.45
                         ≈ 0.78 * 0.45 = ~0.35   ← below MATCH_THRESHOLD
        with cross_disc bonus  ≈ 0.35 + 0.05 = ~0.40   ← clears

    Without this consolation, the imperfect-title cross-disc case
    would silently start going unmatched. If anyone considers setting
    ``CROSS_DISC_POSITION_WEIGHT`` to 0, this test makes the trade-off
    explicit (this case becomes unmatched) instead of letting it
    regress invisibly.
    """
    track = {
        'name': 'Auntie Diaries (Remix)',
        'track_number': 6, 'disc_number': 1,
        'artists': [],
    }
    # File: same track number, different disc, similar but not perfect
    # title (file has the canonical name, source has the version
    # variant — common with deluxe / remix / live editions)
    tags = _tags(
        title='Auntie Diaries',
        track=6,
        disc=2,
    )

    # Compute the title-only contribution to verify the test's premise:
    # title agreement is moderate, NOT high enough on its own to clear
    # MATCH_THRESHOLD. The consolation has to be load-bearing.
    title_only_score = _sim(
        'Auntie Diaries', 'Auntie Diaries (Remix)',
    ) * TITLE_WEIGHT
    assert title_only_score < MATCH_THRESHOLD, (
        f"Test premise broken — title sim alone ({title_only_score:.3f}) "
        f"already clears MATCH_THRESHOLD ({MATCH_THRESHOLD}). The "
        f"cross-disc consolation isn't load-bearing for this scenario; "
        f"pick a less-similar title pair."
    )

    score = score_file_against_track(
        '/a/file.flac', tags, track,
        target_album='', similarity=_sim,
    )
    assert score >= MATCH_THRESHOLD, (
        f"Cross-disc consolation ({CROSS_DISC_POSITION_WEIGHT}) is no "
        f"longer enough to push the score across MATCH_THRESHOLD "
        f"({MATCH_THRESHOLD}) for imperfect-title cases. Total score: "
        f"{score:.3f}. Either bump the consolation OR drop it to 0 "
        f"deliberately and accept that these files now go unmatched."
    )


def test_score_near_position_only_when_same_disc():
    """Off-by-one track number gets NEAR_POSITION bonus, but ONLY when
    disc agrees. Cross-disc off-by-one gets nothing."""
    track = {'name': 'Y', 'track_number': 5, 'disc_number': 1, 'artists': []}

    same_disc = _tags(title='Y', track=6, disc=1)  # off by 1 on same disc
    score_same = score_file_against_track(
        '/a/f.flac', same_disc, track, target_album='', similarity=_sim,
    )
    expected_same = TITLE_WEIGHT + NEAR_POSITION_WEIGHT
    assert abs(score_same - expected_same) < 0.001

    diff_disc = _tags(title='Y', track=6, disc=2)  # off by 1, different disc
    score_diff = score_file_against_track(
        '/a/f.flac', diff_disc, track, target_album='', similarity=_sim,
    )
    # No position bonus at all (off-by-one + cross-disc)
    expected_diff = TITLE_WEIGHT
    assert abs(score_diff - expected_diff) < 0.001


def test_score_handles_missing_track_artist():
    """Track with no artists list — artist component just contributes 0."""
    track = {'name': 'Z', 'track_number': 1, 'disc_number': 1, 'artists': []}
    tags = _tags(title='Z', artist='Real Artist', track=1, disc=1)
    score = score_file_against_track(
        '/a/f.flac', tags, track, target_album='', similarity=_sim,
    )
    # Title (1.0) + position (0.30) + no artist bonus + no album
    expected = TITLE_WEIGHT + POSITION_WEIGHT
    assert abs(score - expected) < 0.001


def test_score_handles_missing_file_artist():
    """File with no artist tag — same as missing track artist, no bonus."""
    track = {'name': 'Z', 'track_number': 1, 'disc_number': 1,
             'artists': [{'name': 'Artist'}]}
    tags = _tags(title='Z', artist='', track=1, disc=1)
    score = score_file_against_track(
        '/a/f.flac', tags, track, target_album='', similarity=_sim,
    )
    expected = TITLE_WEIGHT + POSITION_WEIGHT
    assert abs(score - expected) < 0.001


def test_score_disc_field_aliases():
    """API track disc number can come from disc_number / disk_number /
    discNumber depending on source. All three should be honored."""
    tags = _tags(title='X', track=1, disc=2)
    for disc_field in ('disc_number', 'disk_number', 'discNumber'):
        track = {'name': 'X', 'track_number': 1, disc_field: 2, 'artists': []}
        score = score_file_against_track(
            '/a/f.flac', tags, track, target_album='', similarity=_sim,
        )
        # Should get full POSITION bonus
        expected = TITLE_WEIGHT + POSITION_WEIGHT
        assert abs(score - expected) < 0.001, (
            f"Disc field '{disc_field}' should be recognised (score={score})"
        )


def test_score_filename_fallback_when_title_tag_missing():
    """File with no title tag falls back to the filename stem for the
    title-similarity comparison."""
    track = {'name': 'Filename Title', 'track_number': 0, 'artists': []}
    tags = _tags(title='', track=0, disc=1)
    score = score_file_against_track(
        '/a/Filename Title.flac', tags, track,
        target_album='', similarity=_sim,
    )
    # Title fallback gives perfect match → TITLE_WEIGHT
    assert abs(score - TITLE_WEIGHT) < 0.001


# ---------------------------------------------------------------------------
# match_files_to_tracks — end-to-end (still pure)
# ---------------------------------------------------------------------------


def test_match_pairs_files_to_correct_tracks():
    """Happy path — 3 files, 3 tracks, all match perfectly."""
    files = ['/a/01.flac', '/a/02.flac', '/a/03.flac']
    file_tags = {
        '/a/01.flac': _tags(title='A', track=1, disc=1),
        '/a/02.flac': _tags(title='B', track=2, disc=1),
        '/a/03.flac': _tags(title='C', track=3, disc=1),
    }
    tracks = [
        {'name': 'A', 'track_number': 1, 'disc_number': 1, 'artists': [{'name': 'Artist'}]},
        {'name': 'B', 'track_number': 2, 'disc_number': 1, 'artists': [{'name': 'Artist'}]},
        {'name': 'C', 'track_number': 3, 'disc_number': 1, 'artists': [{'name': 'Artist'}]},
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='Album', similarity=_sim, quality_rank=_qrank,
    )
    assert len(result['matches']) == 3
    assert not result['unmatched_files']


def test_match_each_file_used_at_most_once():
    """Two tracks competing for the same file — only one wins, the
    other gets no match."""
    files = ['/a/only.flac']
    file_tags = {'/a/only.flac': _tags(title='Track Name', track=1, disc=1)}
    tracks = [
        {'name': 'Track Name', 'track_number': 1, 'disc_number': 1, 'artists': []},
        {'name': 'Track Name', 'track_number': 1, 'disc_number': 1, 'artists': []},  # dup
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='', similarity=_sim, quality_rank=_qrank,
    )
    assert len(result['matches']) == 1


def test_match_below_threshold_files_left_unmatched():
    """File with weak title + no other signals should be left in
    unmatched_files, not force-matched."""
    files = ['/a/random.flac']
    file_tags = {'/a/random.flac': _tags(title='Totally Different', track=0, disc=1)}
    tracks = [
        {'name': 'Specific Track', 'track_number': 99, 'disc_number': 1, 'artists': []},
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='', similarity=_sim, quality_rank=_qrank,
    )
    assert not result['matches']
    assert result['unmatched_files'] == ['/a/random.flac']


# ---------------------------------------------------------------------------
# Edge case Cin would flag: tag-less file matching against multi-disc API
# ---------------------------------------------------------------------------


def test_tagless_file_matches_disc1_track_with_perfect_title():
    """User has a perfectly-named file with no embedded tags — file
    title in the filename matches the metadata title exactly. The
    matcher should still pair it correctly even though disc info is
    missing on the file side (defaults to disc 1)."""
    files = ['/a/Auntie Diaries.flac']
    file_tags = {
        '/a/Auntie Diaries.flac': _tags(title='', track=0, disc=1),  # no tags
    }
    tracks = [
        {'name': 'Auntie Diaries', 'track_number': 6, 'disc_number': 2,
         'artists': [{'name': 'Kendrick Lamar'}]},
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='Mr. Morale & The Big Steppers',
        similarity=_sim, quality_rank=_qrank,
    )
    # Perfect title sim (1.0 × 0.45 = 0.45) > MATCH_THRESHOLD (0.4)
    # → file matches the track even with missing position info
    assert len(result['matches']) == 1
    assert result['matches'][0]['file'] == '/a/Auntie Diaries.flac'


def test_tagless_files_against_multidisc_album_partial_match():
    """Two tag-less files with strong filename titles (one matches a
    disc-1 track, one matches a disc-2 track). Both should match
    correctly via title — no disc info needed."""
    files = ['/a/Father Time.flac', '/a/Mother I Sober.flac']
    file_tags = {f: _tags(title='', track=0, disc=1) for f in files}
    tracks = [
        {'name': 'Father Time', 'track_number': 5, 'disc_number': 1, 'artists': []},
        {'name': 'Mother I Sober', 'track_number': 8, 'disc_number': 2, 'artists': []},
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='Mr. Morale', similarity=_sim, quality_rank=_qrank,
    )
    assert len(result['matches']) == 2
    by_track = {m['track']['name']: m['file'] for m in result['matches']}
    assert by_track['Father Time'] == '/a/Father Time.flac'
    assert by_track['Mother I Sober'] == '/a/Mother I Sober.flac'


def test_tagless_file_with_weak_title_unmatched_in_multidisc():
    """Edge case Cin would flag: tag-less file (so disc defaults to 1)
    with a weak filename title against a disc-2-only API track. Pre-fix,
    the position bonus fired on track_number alone, so files like this
    would sneak matches via just track_number agreement. Post-fix, the
    cross-disc consolation (5%) plus weak title can fall below
    MATCH_THRESHOLD → file goes unmatched.

    This is the BEHAVIOR CHANGE worth pinning. For correctly-tagged
    files in multi-disc albums (the user's actual case) this is the
    right call. For users with weak tags this is a regression — they
    now have to rely on title or fix their tags."""
    files = ['/a/track06.flac']  # weak title, no tags
    file_tags = {
        '/a/track06.flac': _tags(title='', track=6, disc=1),  # disc defaults to 1
    }
    tracks = [
        # API has only this disc-2 track 6 — file's disc-1-track-6
        # signal would have fired full position bonus pre-fix
        {'name': 'Auntie Diaries', 'track_number': 6, 'disc_number': 2,
         'artists': [{'name': 'Kendrick Lamar'}]},
    ]
    result = match_files_to_tracks(
        files, file_tags, tracks,
        target_album='Mr. Morale', similarity=_sim, quality_rank=_qrank,
    )
    # Title sim "track06" vs "Auntie Diaries" is near zero (~0.10)
    # × 0.45 = ~0.045. Plus cross-disc 0.05 = ~0.095. Below 0.4
    # threshold → no match.
    assert not result['matches']
    assert result['unmatched_files'] == ['/a/track06.flac']
