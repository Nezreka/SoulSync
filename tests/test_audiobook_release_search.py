"""Tests for core/audiobook_release_search.py.

Hermetic: the Prowlarr client is always a stub, so nothing here touches an
indexer or the shared search throttle.

Several of these guard traps that only show up on real release names, where the
title is a dotted scene string carrying a group tag, a year, a format marker and
sometimes the word "unabridged" — none of which the catalogue title contains.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.audiobook_release_search import (
    AUDIOBOOK_CATEGORY,
    AudiobookRelease,
    build_queries,
    deduplicate,
    detect_bitrate,
    detect_format,
    is_abridged,
    narrator_in_release,
    narrator_verdict,
    normalize_text,
    plausible_size,
    rank_releases,
    release_from_prowlarr,
    score_release,
    search_releases,
    significant_tokens,
    title_relevance,
)

BOOK = {
    "asin": "B08G9PRS1K",
    "title": "Project Hail Mary",
    "author_names": ["Andy Weir"],
    "narrator_names": ["Ray Porter"],
    "runtime_minutes": 970,
    "series": [],
}


def _release(title="Project Hail Mary M4B", **overrides):
    payload = {
        "source": "prowlarr",
        "protocol": "torrent",
        "title": title,
        "indexer": "SomeTracker",
        "size_bytes": 700 * 1024 * 1024,
        "guid": "guid-1",
        "download_url": "https://example.invalid/a.torrent",
        "audio_format": detect_format(title),
        "bitrate_kbps": detect_bitrate(title),
        "abridged": is_abridged(title),
        "seeders": 20,
    }
    payload.update(overrides)
    return AudiobookRelease(**payload)


def _prowlarr_result(**overrides):
    payload = {
        "title": "Project.Hail.Mary.2021.Andy.Weir.M4B-GRP",
        "guid": "g1",
        "protocol": "torrent",
        "indexer_name": "SomeTracker",
        "size": 700 * 1024 * 1024,
        "download_url": "https://example.invalid/a.torrent",
        "magnet_uri": None,
        "seeders": 30,
        "publish_date": "2021-05-04",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

def test_scene_punctuation_normalizes_to_the_catalogue_title():
    assert normalize_text("Project.Hail.Mary.2021-GRP") == "project hail mary 2021 grp"


@pytest.mark.parametrize("raw", [None, "", "   ", "..."])
def test_normalize_empty(raw):
    assert normalize_text(raw) == ""


def test_filler_words_are_not_matchable_tokens():
    # "The" and "audiobook" are in nearly every release name; counting them as
    # matches makes an unrelated release look like a hit.
    assert significant_tokens("The Martian Audiobook Unabridged") == ["martian"]


# ---------------------------------------------------------------------------
# Format and bitrate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Book Name M4B", "m4b"),
    ("Book Name [M4A]", "m4a"),
    ("Book Name FLAC", "flac"),
    ("Book Name MP3 64k", "mp3"),
    ("Book Name (opus)", "opus"),
    ("Book Name", ""),
])
def test_detect_format(title, expected):
    assert detect_format(title) == expected


def test_m4b_wins_over_a_mentioned_mp3_source():
    # "M4B (from MP3 source)" is an m4b; a plain substring search calls it mp3.
    assert detect_format("Book Name M4B (from MP3 source)") == "m4b"


@pytest.mark.parametrize("title,expected", [
    ("Book 64kbps", 64), ("Book 128k", 128), ("Book 32 kbps", 32),
])
def test_detect_bitrate(title, expected):
    assert detect_bitrate(title) == expected


@pytest.mark.parametrize("title", ["Book 2021", "Book 1080k", "Book", "Book 8k"])
def test_bitrate_ignores_things_that_are_not_bitrates(title):
    assert detect_bitrate(title) is None


# ---------------------------------------------------------------------------
# Abridged
# ---------------------------------------------------------------------------

def test_unabridged_is_not_abridged():
    # "Unabridged" contains "abridged"; a substring check marks every full
    # recording as cut down.
    assert is_abridged("Project Hail Mary [Unabridged] M4B") is False


def test_abridged_is_detected():
    assert is_abridged("Project Hail Mary (Abridged)") is True


def test_no_marker_means_not_abridged():
    assert is_abridged("Project Hail Mary M4B") is False


# ---------------------------------------------------------------------------
# Relevance
# ---------------------------------------------------------------------------

def test_a_scene_named_release_still_scores_as_the_right_book():
    score = title_relevance("Project.Hail.Mary.2021.Andy.Weir.M4B-GRP",
                            "Project Hail Mary", ["Andy Weir"])
    assert score == 1.0


def test_extra_words_in_a_release_name_are_not_penalised():
    # Scored on the book's words appearing in the release, not the reverse —
    # otherwise a well-tagged upload ranks below a bare one.
    bare = title_relevance("Project Hail Mary", "Project Hail Mary", ["Andy Weir"])
    tagged = title_relevance("Project Hail Mary Andy Weir Unabridged M4B 64k [GRP]",
                             "Project Hail Mary", ["Andy Weir"])
    assert tagged >= bare


def test_a_missing_author_costs_only_a_quarter():
    with_author = title_relevance("Project Hail Mary Andy Weir", "Project Hail Mary", ["Andy Weir"])
    without = title_relevance("Project Hail Mary", "Project Hail Mary", ["Andy Weir"])
    assert with_author == 1.0
    assert 0.7 <= without < 1.0


def test_a_different_book_scores_low():
    assert title_relevance("The Martian Andy Weir M4B", "Project Hail Mary", ["Andy Weir"]) < 0.5


@pytest.mark.parametrize("release,book", [(None, "Title"), ("Title", None), ("", ""), ("x", "")])
def test_relevance_of_empty_inputs(release, book):
    assert title_relevance(release, book) == 0.0


# ---------------------------------------------------------------------------
# Size sanity
# ---------------------------------------------------------------------------

def test_a_normal_audiobook_size_is_plausible():
    assert plausible_size(700 * 1024 * 1024, 970) is True


def test_a_sample_sized_release_is_rejected():
    # A few MB is a sample, a link file, or a scam — and grabbing it costs a
    # whole download slot to find out.
    assert plausible_size(3 * 1024 * 1024, 970) is False


def test_a_bundle_sized_release_is_rejected():
    assert plausible_size(60 * 1024 * 1024 * 1024, 970) is False


def test_without_a_known_runtime_only_the_floor_applies():
    # Guessing a ceiling from nothing would throw out legitimate box sets.
    assert plausible_size(40 * 1024 * 1024 * 1024, None) is True
    assert plausible_size(1024, None) is False


@pytest.mark.parametrize("size", [None, 0, -5, "big"])
def test_unusable_sizes_are_rejected(size):
    assert plausible_size(size, 970) is False


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def test_author_and_title_is_tried_first():
    assert build_queries(BOOK)[0] == "Andy Weir Project Hail Mary"


def test_title_alone_is_a_fallback():
    # Uploaders routinely leave the author out of the release name.
    assert "Project Hail Mary" in build_queries(BOOK)


def test_a_series_book_gets_a_series_query():
    book = dict(BOOK, series=[{"title": "The Stormlight Archive", "sequence": "3"}])
    assert "The Stormlight Archive 3" in build_queries(book)


def test_queries_are_deduplicated():
    book = {"title": "Dune", "author_names": []}
    assert build_queries(book) == ["Dune"]


@pytest.mark.parametrize("book", [{}, {"title": ""}, {"title": "   "}])
def test_no_title_means_no_queries(book):
    assert build_queries(book) == []


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def test_relevance_dominates_the_score():
    # Grabbing a beautifully seeded m4b of the WRONG book is the only expensive
    # mistake here, so nothing else may outweigh being the right book.
    right = score_release(_release("Project Hail Mary MP3", seeders=1), BOOK)
    wrong = score_release(_release("The Martian M4B", seeders=5000), BOOK)
    assert right.score > wrong.score


def test_m4b_beats_mp3_all_else_equal():
    m4b = score_release(_release("Project Hail Mary M4B"), BOOK)
    mp3 = score_release(_release("Project Hail Mary MP3"), BOOK)
    assert m4b.score > mp3.score


def test_abridged_is_penalised():
    full = score_release(_release("Project Hail Mary M4B"), BOOK)
    cut = score_release(_release("Project Hail Mary M4B Abridged"), BOOK)
    assert cut.score < full.score


def test_a_dead_torrent_is_penalised():
    alive = score_release(_release(seeders=40), BOOK)
    dead = score_release(_release(seeders=0), BOOK)
    assert dead.score < alive.score


def test_seeder_bonus_has_diminishing_returns():
    few = score_release(_release(seeders=10), BOOK).score
    many = score_release(_release(seeders=400), BOOK).score
    assert many > few
    assert many - few < 10


def test_usenet_is_not_penalised_for_having_no_seeders():
    # Usenet reports no seeder count at all; treating that as zero would put
    # every usenet release below every torrent.
    usenet = score_release(_release(protocol="usenet", seeders=None), BOOK)
    dead_torrent = score_release(_release(protocol="torrent", seeders=0), BOOK)
    assert usenet.score > dead_torrent.score


def test_the_score_records_its_reasoning():
    scored = score_release(_release("Project Hail Mary M4B", seeders=20), BOOK)
    assert any("relevance" in reason for reason in scored.reasons)
    assert any("m4b" in reason for reason in scored.reasons)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def test_wrong_books_are_dropped_not_ranked_low():
    # A half-matching release is a DIFFERENT book, not a worse copy of this one;
    # leaving it in the list invites someone to grab it.
    ranked = rank_releases([_release("The Martian M4B"), _release("Project Hail Mary M4B")], BOOK)
    assert [r.title for r in ranked] == ["Project Hail Mary M4B"]


def test_ranking_puts_the_best_first():
    ranked = rank_releases([
        _release("Project Hail Mary MP3", guid="a", seeders=2),
        _release("Project Hail Mary M4B", guid="b", seeders=90),
    ], BOOK)
    assert ranked[0].title == "Project Hail Mary M4B"


def test_ranking_an_empty_list():
    assert rank_releases([], BOOK) == []


def test_deduplicate_by_guid():
    same = [_release(guid="same"), _release(guid="same"), _release(guid="other")]
    assert len(deduplicate(same)) == 2


def test_deduplicate_falls_back_to_indexer_and_title():
    # Running three query variants against one indexer returns the same upload
    # three times, and some indexers give no guid.
    same = [_release(guid=""), _release(guid=""), _release(guid="", indexer="Other")]
    assert len(deduplicate(same)) == 2


# ---------------------------------------------------------------------------
# Prowlarr conversion
# ---------------------------------------------------------------------------

def test_conversion_reads_every_field():
    release = release_from_prowlarr(_prowlarr_result(), BOOK)
    assert release.title.startswith("Project.Hail.Mary")
    assert release.protocol == "torrent"
    assert release.indexer == "SomeTracker"
    assert release.audio_format == "m4b"
    assert release.seeders == 30
    assert release.source == "prowlarr"


def test_a_release_with_no_way_to_fetch_it_is_dropped():
    # Ranking one only produces a button that fails.
    assert release_from_prowlarr(
        _prowlarr_result(download_url=None, magnet_uri=None), BOOK) is None


def test_a_magnet_only_release_is_kept():
    release = release_from_prowlarr(
        _prowlarr_result(download_url=None, magnet_uri="magnet:?xt=urn:btih:abc"), BOOK)
    assert release is not None
    assert release.magnet_uri.startswith("magnet:")


def test_an_implausibly_sized_release_never_becomes_a_candidate():
    assert release_from_prowlarr(_prowlarr_result(size=1024), BOOK) is None


def test_a_titleless_result_is_dropped():
    assert release_from_prowlarr(_prowlarr_result(title=""), BOOK) is None


# ---------------------------------------------------------------------------
# search_releases
# ---------------------------------------------------------------------------

class _StubProwlarr:
    def __init__(self, pages=None, configured=True):
        self.pages = pages if pages is not None else [[]]
        self.configured = configured
        self.queries = []
        self.categories = []

    def is_configured(self):
        return self.configured

    async def search(self, query, categories=None, limit=None, **kwargs):
        self.queries.append(query)
        self.categories.append(list(categories or []))
        index = len(self.queries) - 1
        return self.pages[index] if index < len(self.pages) else []


def test_search_returns_ranked_releases():
    stub = _StubProwlarr([[_prowlarr_result(guid=f"g{i}") for i in range(6)]])
    found = search_releases(BOOK, prowlarr_client=stub)
    assert found
    assert all(r.relevance >= 0.5 for r in found)


def test_search_asks_only_for_the_audiobook_category():
    # The music tree returns albums; no filter returns the whole internet.
    stub = _StubProwlarr([[_prowlarr_result()]])
    search_releases(BOOK, prowlarr_client=stub)
    assert stub.categories[0] == [AUDIOBOOK_CATEGORY]


def test_a_precise_query_stops_the_search_early():
    # Every query is a real search landing on every configured indexer, so
    # firing all the variants when the first one answered triples the load.
    stub = _StubProwlarr([[_prowlarr_result(guid=f"g{i}") for i in range(6)]])
    search_releases(BOOK, prowlarr_client=stub)
    assert stub.queries == ["Andy Weir Project Hail Mary"]


def test_a_thin_first_query_falls_through_to_the_next():
    stub = _StubProwlarr([[_prowlarr_result(guid="only")], [_prowlarr_result(guid="more")]])
    search_releases(BOOK, prowlarr_client=stub)
    assert len(stub.queries) > 1


def test_an_unconfigured_prowlarr_searches_nothing():
    stub = _StubProwlarr(configured=False)
    assert search_releases(BOOK, prowlarr_client=stub) == []
    assert stub.queries == []


def test_a_failing_indexer_does_not_raise():
    # Fails open: the page says "no releases found", not 500.
    class Boom(_StubProwlarr):
        async def search(self, query, categories=None, limit=None, **kwargs):
            raise RuntimeError("indexer on fire")

    assert search_releases(BOOK, prowlarr_client=Boom()) == []


def test_a_book_with_no_title_never_searches():
    stub = _StubProwlarr()
    assert search_releases({"title": ""}, prowlarr_client=stub) == []
    assert stub.queries == []


def test_the_category_constant_matches_the_prowlarr_client():
    from core.prowlarr_client import MUSIC_CATEGORY_AUDIOBOOK

    assert AUDIOBOOK_CATEGORY == MUSIC_CATEGORY_AUDIOBOOK


def test_settings_can_override_the_categories():
    stub = _StubProwlarr([[_prowlarr_result()]])
    with patch("core.settings.config_manager.get", return_value=[3030, 3000]):
        search_releases(BOOK, prowlarr_client=stub)
    assert stub.categories[0] == [3030, 3000]


# ---------------------------------------------------------------------------
# Narrator
#
# On Audible the narrator is baked into the ASIN — Jim Dale and Stephen Fry are
# different catalogue entries, not options on one book — so choosing a book has
# already chosen a reading. The only open question is whether the DOWNLOAD has
# to match it.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Mistborn Narrated by Michael Kramer M4B", "Michael Kramer"),
    ("Mistborn [Read by Michael Kramer]", "Michael Kramer"),
    ("Mistborn - Narrator: Michael Kramer", "Michael Kramer"),
    ("Mistborn narrated by michael kramer", "michael kramer"),
])
def test_a_release_that_names_its_narrator(title, expected):
    assert narrator_in_release(title) == expected


@pytest.mark.parametrize("title", [
    "Mistborn The Final Empire M4B 64k",
    "Brandon.Sanderson.Mistborn.2006.M4B-GRP",
    "",
    None,
])
def test_most_releases_name_no_narrator(title):
    # "" means unknown, not absent — and unknown is the common case, which is
    # why it is never treated as a failure.
    assert narrator_in_release(title) == ""


def test_format_words_are_not_swallowed_into_the_name():
    assert narrator_in_release("Book Read by Ray Porter M4B 64kbps") == "Ray Porter"


@pytest.mark.parametrize("named,wanted", [
    ("Michael Kramer", "Michael Kramer"),
    ("M. Kramer", "Michael Kramer"),
    ("Michael Kramer", "Kramer"),
])
def test_abbreviated_names_are_the_same_person(named, wanted):
    # Releases abbreviate and reorder; treating those as different people would
    # reject the very release the listener asked for.
    assert narrator_verdict(f"Book Narrated by {named}", wanted) == "match"


def test_a_different_narrator_is_a_mismatch():
    assert narrator_verdict("Book Narrated by Stephen Fry", "Jim Dale") == "mismatch"


def test_a_release_that_says_nothing_is_unknown():
    assert narrator_verdict("Book M4B", "Jim Dale") == "unknown"


def test_no_wanted_narrator_means_nothing_to_check():
    assert narrator_verdict("Book Narrated by Anyone", "") == "unknown"


# ---- ranking ----

def _narrated(title, **overrides):
    return _release(title=title, **overrides)


NARRATED_BOOK = dict(BOOK, narrator_names=["Ray Porter"])


def test_the_wanted_narrator_outranks_a_silent_release():
    ranked = rank_releases([
        _narrated("Project Hail Mary M4B", guid="silent"),
        _narrated("Project Hail Mary Narrated by Ray Porter M4B", guid="named"),
    ], NARRATED_BOOK)
    assert ranked[0].guid == "named"
    assert ranked[0].narrator_verdict == "match"


def test_exact_mode_drops_a_different_narrator():
    # The default. Wanting a book on Audible already means wanting a reading.
    ranked = rank_releases([
        _narrated("Project Hail Mary Narrated by Someone Else M4B", guid="wrong"),
        _narrated("Project Hail Mary M4B", guid="silent"),
    ], NARRATED_BOOK, narrator_mode="exact")
    assert [r.guid for r in ranked] == ["silent"]


def test_any_mode_keeps_a_different_narrator_but_outranks_it():
    ranked = rank_releases([
        _narrated("Project Hail Mary Narrated by Someone Else M4B", guid="wrong"),
        _narrated("Project Hail Mary M4B", guid="silent"),
    ], NARRATED_BOOK, narrator_mode="any")
    assert [r.guid for r in ranked] == ["silent", "wrong"]


def test_a_silent_release_is_never_dropped_for_its_narrator():
    # Most releases never name one; requiring it would find nothing at all.
    ranked = rank_releases([_narrated("Project Hail Mary M4B")], NARRATED_BOOK,
                           narrator_mode="exact")
    assert len(ranked) == 1
    assert ranked[0].narrator_verdict == "unknown"


def test_a_book_with_no_known_narrator_checks_nothing():
    ranked = rank_releases([
        _narrated("Project Hail Mary Narrated by Anybody M4B"),
    ], dict(BOOK, narrator_names=[]), narrator_mode="exact")
    assert len(ranked) == 1


def test_the_verdict_is_reported_to_the_caller():
    # The modal shows why a release ranked where it did.
    ranked = rank_releases([
        _narrated("Project Hail Mary Narrated by Ray Porter M4B"),
    ], NARRATED_BOOK)
    payload = ranked[0].to_dict()
    assert payload["narrator_verdict"] == "match"
    assert any("narrator" in reason for reason in payload["reasons"])


def test_search_passes_the_narrator_mode_through():
    stub = _StubProwlarr([[
        _prowlarr_result(guid="wrong", title="Project Hail Mary Narrated by Someone Else M4B"),
    ]])
    assert search_releases(NARRATED_BOOK, prowlarr_client=stub, narrator_mode="exact") == []

    stub = _StubProwlarr([[
        _prowlarr_result(guid="wrong", title="Project Hail Mary Narrated by Someone Else M4B"),
    ]])
    assert search_releases(NARRATED_BOOK, prowlarr_client=stub, narrator_mode="any")
