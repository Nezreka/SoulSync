"""Radarr/Sonarr-parity title gate for the video downloader.

A text search for "Paradox (2017)" was accepting "The.Cloverfield.Paradox.2018..." —
the release title is only a SUBSTRING and the year is one off, yet nothing checked
the title (movies passed on year alone). These lock in a real title match: the
release's parsed title must equal the wanted film/show (tolerating only trailing
edition words), so a different film is rejected, while legit releases still pass.
"""

from __future__ import annotations

from core.video.release_parse import (
    extract_title, normalize_title, titles_match, parse_release)
from core.video.quality_eval import evaluate_release

# A permissive profile so ONLY the title/year gate decides accept vs reject.
_PROFILE = {"tiers": [{"key": t, "enabled": True} for t in
                      ("webdl-1080p", "bluray-1080p", "webrip-1080p", "bluray-2160p")]}


# ── extraction ────────────────────────────────────────────────────────────────
def test_extract_title_cuts_at_release_year():
    assert extract_title("The.Cloverfield.Paradox.2018.1080p.WEBRip.x265-PS") == "The Cloverfield Paradox"
    assert extract_title("Paradox.2017.1080p.BluRay.x264-GROUP") == "Paradox"
    assert extract_title("Spider-Man.No.Way.Home.2021.2160p.UHD.BluRay") == "Spider Man No Way Home"


def test_extract_title_keeps_a_year_that_is_part_of_the_title():
    # the LAST year is the release year; 2049 stays in the title
    assert extract_title("Blade.Runner.2049.2017.1080p.BluRay.x264") == "Blade Runner 2049"


def test_extract_title_falls_back_to_quality_token_when_no_release_year():
    assert extract_title("Paradox.1080p.WEB-DL.x264") == "Paradox"
    assert extract_title("The.Wire.S02.1080p.BluRay.x265") == "The Wire"


def test_extract_title_recovers_a_numeric_title_via_the_quality_boundary():
    # '2012' has no separate release year, but cutting at the quality token still isolates it
    assert extract_title("2012.1080p.BluRay") == "2012"
    assert extract_title("") == ""


# ── normalization ─────────────────────────────────────────────────────────────
def test_normalize_folds_articles_punctuation_accents_and_ampersand():
    assert normalize_title("The Dark Knight") == "dark knight"
    assert normalize_title("dark.knight") == "dark knight"
    assert normalize_title("Fast & Furious") == "fast and furious"
    assert normalize_title("Amélie") == "amelie"
    assert normalize_title("Mission: Impossible") == "mission impossible"


# ── the match ─────────────────────────────────────────────────────────────────
def test_the_reported_bug_cloverfield_paradox_is_rejected_for_paradox():
    assert titles_match("The.Cloverfield.Paradox.2018.1080p.WEBRip.x265-PS", "Paradox") is False


def test_exact_and_separator_variants_match():
    assert titles_match("Paradox.2017.1080p.BluRay.x264", "Paradox") is True
    assert titles_match("The.Dark.Knight.2008.1080p.BluRay", "The Dark Knight") is True
    assert titles_match("Spider-Man.No.Way.Home.2021.2160p", "Spider-Man: No Way Home") is True


def test_trailing_edition_words_are_tolerated_but_extra_real_words_are_not():
    assert titles_match("Paradox.Extended.2017.1080p", "Paradox") is True     # edition of same film
    assert titles_match("The.Paradox.Effect.2023.1080p", "Paradox") is False  # a different film


def test_sequels_and_numbers_do_not_collapse():
    assert titles_match("Moana.2.2024.1080p.WEBRip", "Moana 2") is True
    assert titles_match("Moana.2016.1080p.BluRay", "Moana 2") is False   # the original, not the sequel


def test_numeric_or_unknown_title_passes_so_it_is_never_falsely_rejected():
    # can't isolate a numeric title → don't block; the YEAR gate still guards it
    assert titles_match("2012.1080p.BluRay.x264", "2012") is True
    assert titles_match("anything", None) is True
    assert titles_match("anything", "") is True


def test_episode_release_matches_on_the_show_name():
    assert titles_match("The.Wire.S02E03.1080p.BluRay.x265", "The Wire") is True
    assert titles_match("Some.Other.Show.S02E03.1080p", "The Wire") is False


# ── evaluate_release integration (the actual gate the downloader uses) ─────────
def test_evaluate_release_rejects_the_wrong_film_end_to_end():
    parsed = parse_release("The.Cloverfield.Paradox.2018.1080p.WEBRip.x265-PS")
    v = evaluate_release(parsed, _PROFILE, scope="movie", want_year=2017, want_title="Paradox")
    assert v["accepted"] is False
    assert "Wrong title" in (v.get("rejected") or "")


def test_evaluate_release_accepts_the_right_film():
    parsed = parse_release("Paradox.2017.1080p.BluRay.x264-GROUP")
    v = evaluate_release(parsed, _PROFILE, scope="movie", want_year=2017, want_title="Paradox")
    assert v["accepted"] is True


def test_evaluate_release_without_want_title_keeps_old_behavior():
    # back-compat: no wanted title supplied → the title gate is skipped (year still applies)
    parsed = parse_release("The.Cloverfield.Paradox.2018.1080p.WEBRip.x265-PS")
    v = evaluate_release(parsed, _PROFILE, scope="movie", want_year=2017)
    assert v["accepted"] is True     # only the year gate ran (2018 within 2017..2018)
