"""A season pack's files, mapped onto the episodes they actually are.

Boulder pointed at the music side's staging pattern: the album download does not
import anything, it fills a folder, and the ordinary per-track flow then claims
files out of that folder one at a time. That is the shape this borrows — the pack
fills a folder, the existing per-episode import does the work, so there is one
import path rather than two that drift.

What is deliberately NOT borrowed is the fuzzy matching. Music scores title and
artist similarity (≥0.75) because audio filenames are unreliable; episode files
declare ``S01E03``, or a date, or an absolute number, and the release parser
already reads all three. Porting the similarity threshold would risk filing
episode 4 as episode 3 — a silent mis-shelving nobody notices until they watch
it. So these tests pin EXACT structural matching, and the cases below are the
ones that actually go wrong in real packs.

The other borrowed lesson is #706/#708: on the music side a staged file that
silently failed to match produced "download the album, stage it, never claim it,
re-add to the wishlist" — undiagnosable from logs. So every skip here carries a
reason, and several tests assert on the reason rather than just the count.
"""

from __future__ import annotations

from core.video.season_pack import (
    MIN_EPISODE_BYTES,
    classify_file,
    episode_keys_for,
    map_pack,
    unclaimed_episodes,
)

_BIG = 900 * 1024 * 1024


def _f(path, size=_BIG):
    return {"path": path, "size_bytes": size}


# ── what is even a candidate ─────────────────────────────────────────────────

def test_a_normal_episode_file_is_a_candidate():
    assert classify_file("/d/Show.S01E01.1080p.WEB.mkv", _BIG) is None


def test_non_video_files_are_skipped_with_a_reason():
    for p in ("/d/Show.S01E01.nfo", "/d/poster.jpg", "/d/Show.S01E01.srt", "/d/readme.txt"):
        why = classify_file(p, _BIG)
        assert why and "not a video" in why, p


def test_a_sample_is_not_the_episode():
    """The classic pack trap: a 30-second sample sits beside the real file,
    parses to the SAME SxxExx, and is smaller. Without this the pack imports
    the sample and reports success."""
    assert "sample" in (classify_file("/d/Sample/Show.S01E01.mkv", _BIG) or "")
    assert "sample" in (classify_file("/d/Show.S01E01.sample.mkv", _BIG) or "")


def test_extras_and_featurettes_are_skipped():
    for p in ("/d/Extras/Behind the Scenes.mkv", "/d/Featurettes/gag reel.mkv"):
        assert classify_file(p, _BIG) is not None, p


def test_a_show_whose_title_contains_a_junk_word_is_still_importable():
    """A bare-substring test reads these as extras and refuses every episode. All
    three are real shows, and 'Bonus Family' has six seasons of them."""
    for p in ("/d/Bonus.Family.S01E01.1080p.WEB.mkv",
              "/d/Extras.S02E03.720p.HDTV.mkv",
              "/d/The.Trailer.Park.Boys.S05E01.mkv",
              "/d/Sample.This.S01E04.1080p.mkv"):
        assert classify_file(p, _BIG) is None, p


def test_a_library_folder_named_extras_does_not_condemn_its_contents():
    """The path above the release is the user's own layout — Boulder's library spans
    eleven mount roots, any one of which could be spelled this way. That protection
    is the ROOT scoping in map_pack, not classify_file, which reads whatever path it
    is handed; both halves are asserted here so neither can be dropped alone."""
    assert classify_file("/mnt/extras/downloads/Show.S01E01.mkv", _BIG) is not None
    out = map_pack([_f("/mnt/extras/downloads/Show.S01/Show.S01E01.mkv")],
                   want_season=1, root="/mnt/extras/downloads/Show.S01")
    assert sorted(out["claimed"]) == [(1, 1)] and out["skipped"] == []


def test_the_marker_still_bites_when_it_follows_the_episode_number():
    """Position is the whole discriminator: before the numbering it is the title,
    after it, it is release detail — and 'sample' there means sample."""
    assert classify_file("/d/Bonus.Family.S01E01.sample.mkv", _BIG) is not None
    assert classify_file("/d/Show.S01E01.1080p.trailer.mkv", _BIG) is not None


def test_a_file_with_no_numbering_is_read_whole():
    """No numbering means no title/detail boundary to respect, so the old broad
    read still applies — that is what catches a bare 'trailer.mkv'."""
    assert classify_file("/d/trailer.mkv", _BIG) is not None
    assert classify_file("/d/behind the scenes.mkv", _BIG) is not None


def test_only_an_exact_directory_name_counts_as_a_junk_folder():
    assert classify_file("/d/Sample/Show.S01E01.mkv", _BIG) is not None
    assert classify_file("/d/Samples/Show.S01E01.mkv", _BIG) is not None
    assert classify_file("/d/Sampled Shows/Show.S01E01.mkv", _BIG) is None


def test_a_tiny_video_is_rejected_however_it_is_named():
    why = classify_file("/d/Show.S01E01.1080p.mkv", 4 * 1024 * 1024)
    assert why and "too small" in why


def test_an_unknown_size_does_not_disqualify():
    """Not every caller can supply sizes; missing size must not reject a real
    file — the other guards still apply."""
    assert classify_file("/d/Show.S01E01.mkv", None) is None


# ── reading the numbers ──────────────────────────────────────────────────────

def test_a_plain_episode_claims_one_key():
    assert episode_keys_for("Show.S02E05.1080p.WEB.x264.mkv") == [(2, 5)]


def test_a_multi_episode_file_claims_every_episode_it_spans():
    """S01E01E02 genuinely IS both episodes — claiming only the first would
    leave episode 2 looking missing and trigger a pointless second download."""
    assert episode_keys_for("Show.S01E01E02.1080p.mkv") == [(1, 1), (1, 2)]
    assert episode_keys_for("Show.S01E03-E05.mkv") == [(1, 3), (1, 4), (1, 5)]


def test_the_pack_folder_itself_claims_nothing():
    """'Show.S01.1080p.WEB' is the pack, not an episode."""
    assert episode_keys_for("Show.S01.1080p.WEB-GROUP.mkv") == []


def test_a_daily_episode_resolves_through_the_air_date_map():
    """Dailies are named by date, not SxxExx. The mapping is the caller's —
    numbering authority stays in one place."""
    keys = episode_keys_for("The.Daily.Thing.2026.07.08.1080p.WEB.mkv",
                            air_dates={"2026-07-08": (12, 91)})
    assert keys == [(12, 91)]


def test_an_unknown_air_date_claims_nothing_rather_than_guessing():
    assert episode_keys_for("Show.2026.07.09.1080p.mkv", air_dates={"2026-07-08": (1, 1)}) == []


def test_anime_absolute_numbering_resolves_through_its_map():
    keys = episode_keys_for("[SubsPlease] One Piece - 1071 (1080p).mkv",
                            absolute_map={1071: (21, 15)})
    assert keys == [(21, 15)]


# ── the mapping ──────────────────────────────────────────────────────────────

def test_a_clean_pack_maps_every_episode():
    out = map_pack([_f("/d/S/Show.S01E%02d.1080p.WEB.mkv" % n) for n in range(1, 4)],
                   want_season=1)
    assert sorted(out["claimed"]) == [(1, 1), (1, 2), (1, 3)]
    assert out["skipped"] == []


def test_the_real_file_beats_the_sample_that_parses_the_same():
    """Both claim S01E01. Size is the honest signal — music picks by similarity
    score, which has no equivalent here."""
    out = map_pack([_f("/d/Show.S01E01.1080p.mkv", 20 * _BIG),
                    _f("/d/Show.S01E01.snippet.mkv", 40 * 1024 * 1024)], want_season=1)
    assert out["claimed"][(1, 1)]["path"].endswith("Show.S01E01.1080p.mkv")
    assert any("larger" in s["why"] for s in out["skipped"])


def test_a_stray_wrong_season_file_is_skipped_not_re_homed():
    """A season-2 pack carrying a stray season-1 file must not quietly file it:
    the wishlist rows this pack will satisfy are season 2's."""
    out = map_pack([_f("/d/Show.S02E01.mkv"), _f("/d/Show.S01E09.mkv")], want_season=2)
    assert sorted(out["claimed"]) == [(2, 1)]
    assert any("season 1" in s["why"] and "season 2" in s["why"] for s in out["skipped"])


def test_files_with_no_numbering_are_skipped_loudly():
    """The #706/#708 lesson: a silent non-match is what made the music loop
    impossible to diagnose."""
    out = map_pack([_f("/d/Show/whatever.mkv")], want_season=1)
    assert out["claimed"] == {}
    assert out["skipped"][0]["why"] == "no episode number in the filename"


def test_every_skip_carries_a_reason():
    out = map_pack([_f("/d/a.nfo"), _f("/d/Sample/b.mkv"), _f("/d/c.mkv"),
                    _f("/d/tiny.mkv", 1024)], want_season=1)
    assert len(out["skipped"]) == 4
    assert all(s["why"] for s in out["skipped"]), out["skipped"]


def test_the_junk_check_stops_at_the_pack_folder():
    """The folders ABOVE the pack are the user's own layout and say nothing about
    the release. Reading them means a download dir at /mnt/extras marks every
    episode an extra — and the Netflix show *Bonus Family* can never be imported
    at all, because 'bonus' is in its name."""
    root = "/mnt/extras/downloads/Bonus.Family.S01.1080p"
    out = map_pack([_f(root + "/Bonus.Family.S01E01.1080p.mkv")], want_season=1, root=root)
    assert sorted(out["claimed"]) == [(1, 1)]
    assert out["skipped"] == []


def test_a_sample_inside_the_pack_is_still_caught():
    """Scoping the check must not disarm it — 'Sample/' INSIDE the pack still means
    what it always did."""
    root = "/dl/Show.S01"
    out = map_pack([_f(root + "/Show.S01E01.mkv", 20 * _BIG),
                    _f(root + "/Sample/Show.S01E01.mkv", 40 * 1024 * 1024)],
                   want_season=1, root=root)
    assert out["claimed"][(1, 1)]["path"] == root + "/Show.S01E01.mkv"
    assert any("sample" in s["why"] for s in out["skipped"])


def test_a_windows_style_root_is_scoped_the_same_way():
    root = r"C:\Downloads\Extras Drive\Show.S01"
    out = map_pack([_f(root + r"\Show.S01E02.mkv")], want_season=1, root=root)
    assert sorted(out["claimed"]) == [(1, 2)]


def test_without_a_root_the_whole_path_is_still_read():
    """Callers that pass no root keep the old, broader behaviour — the scoping is
    an addition, not a silent relaxation of the sample guard."""
    out = map_pack([_f("/d/Sample/Show.S01E01.mkv")], want_season=1)
    assert out["claimed"] == {} and "sample" in out["skipped"][0]["why"]


def test_a_pack_with_no_season_hint_still_maps():
    """A series pack spans seasons; without a want_season nothing is filtered."""
    out = map_pack([_f("/d/Show.S01E01.mkv"), _f("/d/Show.S02E01.mkv")])
    assert sorted(out["claimed"]) == [(1, 1), (2, 1)]


def test_a_multi_episode_file_claims_both_slots_in_the_map():
    out = map_pack([_f("/d/Show.S01E01E02.1080p.mkv")], want_season=1)
    assert sorted(out["claimed"]) == [(1, 1), (1, 2)]
    assert out["claimed"][(1, 1)]["path"] == out["claimed"][(1, 2)]["path"]


def test_junk_input_never_raises():
    for bad in (None, [], [None], [{}], [{"path": None}], ["", 12345]):
        out = map_pack(bad, want_season=1)
        assert isinstance(out["claimed"], dict) and isinstance(out["skipped"], list)


# ── what the pack did NOT supply ─────────────────────────────────────────────

def test_missing_episodes_are_reported_for_the_normal_search_path():
    """A partial pack must not silently leave gaps — the leftovers fall through
    to ordinary per-episode searches, exactly as an unmatched track does on the
    music side."""
    out = map_pack([_f("/d/Show.S01E01.mkv"), _f("/d/Show.S01E03.mkv")], want_season=1)
    missing = unclaimed_episodes(out["claimed"], [(1, 1), (1, 2), (1, 3), (1, 4)])
    assert missing == [(1, 2), (1, 4)]


def test_a_complete_pack_leaves_nothing_to_chase():
    out = map_pack([_f("/d/Show.S01E%02d.mkv" % n) for n in (1, 2)], want_season=1)
    assert unclaimed_episodes(out["claimed"], [(1, 1), (1, 2)]) == []


def test_unclaimed_tolerates_empty_input():
    assert unclaimed_episodes({}, []) == []
    assert unclaimed_episodes(None, [(1, 1)]) == [(1, 1)]


# ── the threshold that must NOT be ported ────────────────────────────────────

def test_matching_is_exact_and_never_similarity_scored():
    """Music matches on a 0.75 similarity score because audio filenames are
    unreliable. Episode files are not, and a near-miss here would file the
    wrong episode — silently, permanently, and invisibly until playback."""
    import ast
    import inspect

    from core.video import season_pack
    # Read the CODE, not the prose — the module docstring explains why the 0.75
    # threshold is not used, and a naive grep would flag that explanation.
    tree = ast.parse(inspect.getsource(season_pack))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in getattr(node, "names", [])] + [getattr(node, "module", "") or ""]
            assert not any("difflib" in n for n in names), "fuzzy matching does not belong here"
        if isinstance(node, ast.Name):
            assert node.id != "SequenceMatcher"
        # No float literal is compared against anything — an episode either
        # matches its numbers or it does not.
        if isinstance(node, ast.Compare):
            for side in [node.left, *node.comparators]:
                assert not (isinstance(side, ast.Constant) and isinstance(side.value, float)), (
                    "a similarity threshold has no business in episode matching")
