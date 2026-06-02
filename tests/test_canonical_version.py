"""Extreme battery for canonical-album-version scoring (#765 / #767-Bug2).

The scorer must: pick the right EDITION by best-fit to the files on disk
(standard when you have the standard, deluxe when you have the deluxe), break
ties deterministically toward the higher-priority candidate (so every tool
agrees), degrade gracefully when durations/titles are missing, and never pin a
low-confidence guess.
"""

from __future__ import annotations

from core.metadata.canonical_version import (
    pick_canonical_release,
    score_release_against_files,
)


# Helpers — build track lists ----------------------------------------------

def _tracks(n, base_ms=180_000, step_ms=10_000, titles=None):
    """n tracks with distinct, deterministic durations + optional titles."""
    out = []
    for i in range(n):
        t = {"duration_ms": base_ms + i * step_ms, "track_number": i + 1}
        if titles:
            t["title"] = titles[i]
        out.append(t)
    return out


STANDARD_TITLES = [f"Song {i+1}" for i in range(11)]
DELUXE_TITLES = STANDARD_TITLES + [f"Bonus {i+1}" for i in range(6)]


# ── edition discrimination ────────────────────────────────────────────────

def test_eleven_files_prefer_standard_over_deluxe():
    files = _tracks(11, titles=STANDARD_TITLES)
    standard = _tracks(11, titles=STANDARD_TITLES)
    deluxe = _tracks(17, titles=DELUXE_TITLES)
    s_std = score_release_against_files(files, standard)
    s_dlx = score_release_against_files(files, deluxe)
    assert s_std > s_dlx
    best, score = pick_canonical_release(
        files,
        [{"source": "standard", "tracks": standard}, {"source": "deluxe", "tracks": deluxe}],
    )
    assert best["source"] == "standard" and score > 0.9


def test_seventeen_files_prefer_deluxe():
    files = _tracks(17, titles=DELUXE_TITLES)
    standard = _tracks(11, titles=STANDARD_TITLES)
    deluxe = _tracks(17, titles=DELUXE_TITLES)
    best, _ = pick_canonical_release(
        files,
        # deluxe deliberately listed SECOND to prove count/fit beats order here
        [{"source": "standard", "tracks": standard}, {"source": "deluxe", "tracks": deluxe}],
    )
    assert best["source"] == "deluxe"


def test_exact_count_and_durations_scores_near_one():
    files = _tracks(11, titles=STANDARD_TITLES)
    assert score_release_against_files(files, _tracks(11, titles=STANDARD_TITLES)) > 0.99


# ── deterministic tiebreak (the #765 resolution) ──────────────────────────

def test_identical_releases_break_tie_to_first_candidate():
    # Same album from two sources (same files match both equally) — must pick
    # the FIRST (higher-priority) deterministically so both tools agree.
    files = _tracks(11, titles=STANDARD_TITLES)
    a = {"source": "spotify", "tracks": _tracks(11, titles=STANDARD_TITLES)}
    b = {"source": "musicbrainz", "tracks": _tracks(11, titles=STANDARD_TITLES)}
    best, _ = pick_canonical_release(files, [a, b])
    assert best["source"] == "spotify"
    # ...and stable when the order flips (priority is the caller's order).
    best2, _ = pick_canonical_release(files, [b, a])
    assert best2["source"] == "musicbrainz"


# ── duration disambiguation when counts tie ───────────────────────────────

def test_duration_breaks_tie_when_counts_equal():
    # Two 11-track candidates; the files' durations match candidate A's lengths,
    # not B's (e.g. album cuts vs radio edits). A must win on duration fit.
    files = _tracks(11, base_ms=200_000, step_ms=5_000)
    cand_a = {"source": "album", "tracks": _tracks(11, base_ms=200_000, step_ms=5_000)}
    cand_b = {"source": "edits", "tracks": _tracks(11, base_ms=140_000, step_ms=5_000)}
    best, _ = pick_canonical_release(files, [cand_b, cand_a])  # B listed first
    assert best["source"] == "album"  # duration fit overrides order


# ── graceful degradation ──────────────────────────────────────────────────

def test_no_durations_falls_back_to_count_and_title():
    files = [{"title": t} for t in STANDARD_TITLES]            # no durations
    standard = [{"title": t} for t in STANDARD_TITLES]
    deluxe = [{"title": t} for t in DELUXE_TITLES]
    best, score = pick_canonical_release(
        files,
        [{"source": "standard", "tracks": standard}, {"source": "deluxe", "tracks": deluxe}],
    )
    assert best["source"] == "standard" and score > 0.5


def test_only_counts_available_still_scores():
    files = [{} for _ in range(11)]
    assert score_release_against_files(files, [{} for _ in range(11)]) > 0.99
    assert score_release_against_files(files, [{} for _ in range(17)]) < 0.8


def test_fuzzy_titles_still_match():
    files = _tracks(3, titles=["Believer", "Whatever It Takes", "Thunder"])
    rel = _tracks(3, titles=["Believer (Remastered)", "Whatever It Takes", "Thunder!"])
    assert score_release_against_files(files, rel) > 0.9


# ── confidence floor / guards ─────────────────────────────────────────────

def test_below_floor_returns_none():
    files = _tracks(11, titles=STANDARD_TITLES)
    # A wildly wrong candidate (3 unrelated tracks) must not be pinned.
    bad = {"source": "wrong", "tracks": _tracks(3, base_ms=60_000, titles=["X", "Y", "Z"])}
    best, score = pick_canonical_release(files, [bad])
    assert best is None
    assert score < 0.5


def test_empty_inputs_are_safe():
    assert score_release_against_files([], _tracks(11)) == 0.0
    assert score_release_against_files(_tracks(11), []) == 0.0
    best, score = pick_canonical_release(_tracks(11), [])
    assert best is None and score == 0.0


def test_min_score_is_tunable():
    files = _tracks(11, titles=STANDARD_TITLES)
    near = {"source": "near", "tracks": _tracks(10, titles=STANDARD_TITLES[:10])}
    # default floor accepts a 10/11 fit, a strict floor rejects it
    assert pick_canonical_release(files, [near])[0] is not None
    assert pick_canonical_release(files, [near], min_score=0.99)[0] is None
