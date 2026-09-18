"""match_recording's symmetric version-marker gate.

MusicBrainz recording search runs a bare phrase query with no version
awareness. A query for "Nothing In Return" happily returns a recording
titled "Nothing In Return (Acoustic)" at ~0.76 title similarity, and the
artist bonus + mb_score walk it past the 70-confidence gate — a different
PERFORMANCE of the same song, not the same recording. Real cases observed:
"1.000.000 Lightyears" -> "(live)", "Nothing In Return" -> "(acoustic)",
"Christmas Eve" -> "(English Version)", "Summer Paradise" -> "(French
version)".

``recording_version_markers`` (core/text/title_match.py) extracts a
comparable marker set from a title's bracketed/dashed qualifier text;
``match_recording`` requires that set to be EQUAL between query and
candidate in both directions. Noise words that just restate "this is the
plain cut" ("Album Version", "Original Mix", "Radio Edit", "Single
Version", "2011 Remaster") must not trip the gate, and "feat"/"ft" must
stay excluded (a featured guest is still the same recording).

Test idiom copied from tests/test_enrichment_matching_fixes.py.
"""

from __future__ import annotations

import pytest

from core.musicbrainz_service import MusicBrainzService
from core.text.title_match import recording_version_markers
from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


@pytest.fixture
def svc(db):
    return MusicBrainzService(db)


def _fake_search(candidates, artist_name=None):
    """candidates: list of (title, score) -> a search_recording stand-in.

    ``artist_name``, when given, is stamped onto every candidate's
    'artist-credit' so match_recording's artist_bonus (+20) applies. That
    bonus matters for the *_rejects_*/*_blocks_* tests below: without it,
    several of these pairs would already fall under the PRE-EXISTING
    title_similarity/confidence floors on their own, and the test would
    pass regardless of whether the new version-marker gate does anything.
    Passing a matching artist_name makes the pre-gate confidence clear 70,
    so the assertion actually depends on the marker gate.
    """
    def _search(name, artist=None, limit=5):
        result = []
        for i, (title, score) in enumerate(candidates):
            entry = {"id": f"mbid-{i}", "title": title, "score": score}
            if artist_name:
                entry["artist-credit"] = [{"artist": {"name": artist_name}}]
            result.append(entry)
        return result
    return _search


# ── a/b/c: bare vs "(Acoustic)" ──────────────────────────────────────────────

def test_bare_query_rejects_acoustic_only_candidate(svc):
    # artist_name matches -> artist_bonus +20 -> pre-gate confidence would be
    # int(0.756*50 + 30 + 20) = 87, comfortably >=70. Only the marker gate
    # stands between this candidate and a match.
    svc.mb_client.search_recording = _fake_search(
        [("Nothing In Return (Acoustic)", 100)], artist_name="Example Artist"
    )
    assert svc.match_recording("Nothing In Return", "Example Artist") is None


def test_bare_query_picks_plain_candidate_over_acoustic(svc):
    svc.mb_client.search_recording = _fake_search(
        [("Nothing In Return (Acoustic)", 100), ("Nothing In Return", 95)]
    )
    result = svc.match_recording("Nothing In Return")
    assert result is not None
    assert result["mbid"] == "mbid-1"  # the plain candidate


def test_acoustic_query_picks_acoustic_candidate(svc):
    svc.mb_client.search_recording = _fake_search(
        [("Nothing In Return", 95), ("Nothing In Return (Acoustic)", 100)]
    )
    result = svc.match_recording("Nothing In Return (Acoustic)")
    assert result is not None
    assert result["mbid"] == "mbid-1"  # the acoustic candidate


# ── d: long venue tail, and symmetric "(Live)" ───────────────────────────────
#
# "Stan" alone is too short for a long venue tail to clear the PRE-EXISTING
# title_similarity floor at all (sim("stan", "stan (live at the 43rd grammy
# awards)") = 0.195 < 0.6) — that pair is floor-rejected with or without the
# marker gate, so it wouldn't actually exercise it. Use a long shared base
# (same technique as _LONG_BASE below) so the floor clears and, with a
# matching artist_name, the pre-gate confidence clears 70 too.
_LIVE_BASE = "Stan Walk This Way Forever Tonight And Always"


def test_bare_query_rejects_long_live_venue_tail(svc):
    svc.mb_client.search_recording = _fake_search(
        [(f"{_LIVE_BASE} (Live at the 43rd Grammy Awards)", 100)],
        artist_name="Example Artist",
    )
    assert svc.match_recording(_LIVE_BASE, "Example Artist") is None


def test_live_query_matches_live_candidate(svc):
    svc.mb_client.search_recording = _fake_search(
        [(f"{_LIVE_BASE} (Live)", 100)], artist_name="Example Artist"
    )
    result = svc.match_recording(f"{_LIVE_BASE} (Live)", "Example Artist")
    assert result is not None
    assert result["mbid"] == "mbid-0"


# ── e: noise words are NOT version markers ───────────────────────────────────
#
# The base title is deliberately long (and shared with test f below): title-
# similarity here is 0.5*char-ratio, and a short base + qualifier pulls the
# raw char ratio below match_recording's own 70-confidence line before the
# marker gate is even reached — that would make these tests pass for the
# wrong reason. A long shared base keeps the qualifier's share of the string
# small, so the char ratio (and therefore confidence) stays comfortably
# above 70 regardless of the marker gate, isolating what's under test.
_LONG_BASE = "Somewhere Over The Rainbow Tonight Forever And Ever Amen"


@pytest.mark.parametrize(
    "query,candidate_title",
    [
        (_LONG_BASE, f"{_LONG_BASE} (Album Version)"),
        (_LONG_BASE, f"{_LONG_BASE} (2011 Remaster)"),
        (_LONG_BASE, f"{_LONG_BASE} (Radio Edit)"),
        (_LONG_BASE, f"{_LONG_BASE} (Original Mix)"),
        (f"{_LONG_BASE} (Single Version)", _LONG_BASE),
    ],
)
def test_noise_qualifiers_do_not_block_a_match(svc, query, candidate_title):
    svc.mb_client.search_recording = _fake_search([(candidate_title, 100)])
    result = svc.match_recording(query)
    assert result is not None, f"{query!r} vs {candidate_title!r} should match"


# ── f: "feat" stays out of the marker vocabulary ─────────────────────────────

def test_feat_qualifier_does_not_block_a_match(svc):
    svc.mb_client.search_recording = _fake_search([(_LONG_BASE, 100)])
    result = svc.match_recording(f"{_LONG_BASE} (feat. K'naan)")
    assert result is not None


# ── g: language qualifiers are markers ───────────────────────────────────────

def test_bare_query_rejects_english_version_candidate(svc):
    # sim=0.735, conf-with-artist-bonus=86 -> gate-dependent (see _fake_search).
    svc.mb_client.search_recording = _fake_search(
        [("Christmas Eve Celebration (English Version)", 100)],
        artist_name="Example Artist",
    )
    assert svc.match_recording("Christmas Eve Celebration", "Example Artist") is None


def test_language_qualifier_matches_symmetrically(svc):
    title = "クリスマス・イブ (English Version)"
    svc.mb_client.search_recording = _fake_search([(title, 100)])
    result = svc.match_recording(title)
    assert result is not None


# ── h: marker words in the MAIN title are not flagged ────────────────────────

def test_main_title_word_live_is_not_a_marker(svc):
    svc.mb_client.search_recording = _fake_search([("Live Forever", 100)])
    result = svc.match_recording("Live Forever")
    assert result is not None


def test_main_title_word_live_still_gates_a_real_live_tag(svc):
    # sim=0.774, conf-with-artist-bonus=88 -> gate-dependent (see _fake_search).
    svc.mb_client.search_recording = _fake_search(
        [("Live Forever (Live)", 100)], artist_name="Example Artist"
    )
    assert svc.match_recording("Live Forever", "Example Artist") is None


# ── i: JP katakana markers, including mixed-script "TVサイズ" ────────────────

def test_katakana_live_tag_blocks_bare_query(svc):
    # sim=0.857, conf-with-artist-bonus=92 -> gate-dependent (see _fake_search).
    svc.mb_client.search_recording = _fake_search(
        [("Waterfall Memories (ライブ)", 100)], artist_name="Example Artist"
    )
    assert svc.match_recording("Waterfall Memories", "Example Artist") is None


def test_katakana_tv_size_matches_ascii_tv_size(svc):
    svc.mb_client.search_recording = _fake_search(
        [("Waterfall Memories (TV Size)", 100)]
    )
    result = svc.match_recording("Waterfall Memories (TVサイズ)")
    assert result is not None


# ── j: unit tests for the marker extractor itself ────────────────────────────

@pytest.mark.parametrize(
    "title,expected",
    [
        ("Nothing In Return", frozenset()),
        ("Nothing In Return (Acoustic)", frozenset({"acoustic"})),
        ("Stan (Live at the 43rd Grammy Awards)", frozenset({"live"})),
        ("Firework Celebration (Album Version)", frozenset()),
        ("Waking Up In Vegas (2011 Remaster)", frozenset()),
        ("Uptown Funk Tonight (Radio Edit)", frozenset()),
        ("Shape Of My Heart (Original Mix)", frozenset()),
        ("Shape Of My Heart (Single Version)", frozenset()),
        ("Summer Paradise (feat. K'naan)", frozenset()),
        ("Christmas Eve Celebration (English Version)", frozenset({"english"})),
        ("Live Forever", frozenset()),
        ("Live Forever (Live)", frozenset({"live"})),
        ("Demo Lition", frozenset()),
        ("Waterfall Memories (ライブ)", frozenset({"live"})),
        ("Waterfall Memories (TVサイズ)", frozenset({"size"})),
        ("Waterfall Memories (TV Size)", frozenset({"size"})),
        ("Song Title - Remastered 2011", frozenset()),  # noise: same recording
        ("Song Title - Live at Wembley", frozenset({"live"})),
        ("Song Title (Taylor's Version)", frozenset({"taylors_version"})),
        ("Song Title (feat. Taylor Swift)", frozenset()),
        ("Song Title (Rerecorded)", frozenset({"rerecorded"})),
        ("Song Title (Instrumental)", frozenset({"instrumental"})),
        ("曲名 (インストゥルメンタル)", frozenset({"instrumental"})),
        ("曲名 (カバー)", frozenset({"cover"})),
        ("曲名 (リミックス)", frozenset({"remix"})),
        ("曲名 (アコースティック)", frozenset({"acoustic"})),
        # Package/format metadata, not a different performance — review nit 2.
        ("Song Title (Deluxe Edition)", frozenset()),
        ("Song Title (Bonus Track)", frozenset()),
        ("Song Title (Mono)", frozenset()),
        ("Song Title (Stereo)", frozenset()),
        ("Song Title (Explicit)", frozenset()),
        ("Song Title (Clean)", frozenset()),
        ("Song Title (Anniversary Edition)", frozenset()),
        ("Song Title (Expanded Edition)", frozenset()),
        ("Song Title (Special Edition)", frozenset()),
        # ...but a genuine performance marker alongside one still counts.
        ("Song Title (Live) (Explicit)", frozenset({"live"})),
    ],
)
def test_recording_version_markers_extraction(title, expected):
    assert recording_version_markers(title) == expected
