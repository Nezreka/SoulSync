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
    abridgement_verdict,
    language_verdict,
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


# ---------------------------------------------------------------------------
# Full-cast recordings
#
# A cast recording credits a dozen people. Comparing only against the first one
# meant a release naming any OTHER cast member read as a different narrator and
# was dropped — rejecting exactly the release the listener wanted.
# ---------------------------------------------------------------------------

CAST_BOOK = dict(BOOK, narrator_names=["Michael Kramer", "Kate Reading", "Ray Porter"])


def test_any_credited_narrator_counts_as_a_match():
    assert narrator_verdict("Book Narrated by Kate Reading", CAST_BOOK["narrator_names"]) == "match"
    assert narrator_verdict("Book Narrated by Ray Porter", CAST_BOOK["narrator_names"]) == "match"


def test_someone_not_in_the_cast_is_still_a_mismatch():
    assert narrator_verdict("Book Narrated by Stephen Fry",
                            CAST_BOOK["narrator_names"]) == "mismatch"


def test_a_full_cast_credit_is_not_a_person():
    # "A Full Cast" names a production, not someone to match against; treating
    # it as a different narrator would reject every release of a cast recording.
    assert narrator_verdict("Book Narrated by Michael Kramer", ["A Full Cast"]) == "unknown"
    assert narrator_verdict("Book [Full Cast Recording]", ["Michael Kramer"]) == "unknown"


def test_a_cast_release_is_kept_in_exact_mode():
    ranked = rank_releases(
        [_release(title="Project Hail Mary Narrated by Kate Reading M4B")],
        CAST_BOOK, narrator_mode="exact",
    )
    assert len(ranked) == 1


def test_a_single_narrator_string_still_works():
    assert narrator_verdict("Book Read by Ray Porter", "Ray Porter") == "match"


# ---------------------------------------------------------------------------
# Abridged
#
# Audible sells abridged and unabridged as separate ASINs with different
# runtimes, so wanting a book already means wanting one of them.
# ---------------------------------------------------------------------------

def test_an_abridged_release_matches_an_abridged_book():
    assert abridgement_verdict("Book (Abridged) M4B", "abridged") == "match"


def test_an_abridged_release_mismatches_an_unabridged_book():
    assert abridgement_verdict("Book (Abridged) M4B", "unabridged") == "mismatch"


def test_unabridged_is_not_read_as_abridged():
    assert abridgement_verdict("Book [Unabridged]", "unabridged") == "match"
    assert abridgement_verdict("Book [Unabridged]", "abridged") == "mismatch"


def test_a_silent_release_says_nothing_about_its_edition():
    assert abridgement_verdict("Book M4B 64k", "unabridged") == "unknown"


def test_wanting_the_abridged_edition_stops_penalising_it():
    # The bug: an unconditional penalty punished the very release someone asked
    # for when the abridged edition was the one they picked.
    abridged_book = dict(BOOK, format_type="abridged")
    wanted = score_release(_release("Project Hail Mary (Abridged) M4B"), abridged_book)
    other = score_release(_release("Project Hail Mary (Unabridged) M4B"), abridged_book)
    assert wanted.score > other.score


def test_unabridged_is_still_preferred_when_that_is_the_edition():
    unabridged_book = dict(BOOK, format_type="unabridged")
    full = score_release(_release("Project Hail Mary [Unabridged] M4B"), unabridged_book)
    cut = score_release(_release("Project Hail Mary (Abridged) M4B"), unabridged_book)
    assert full.score > cut.score


def test_with_no_known_edition_abridged_is_still_penalised():
    # Unabridged is what people usually mean when nothing says otherwise.
    unknown_book = dict(BOOK, format_type="")
    full = score_release(_release("Project Hail Mary M4B"), unknown_book)
    cut = score_release(_release("Project Hail Mary (Abridged) M4B"), unknown_book)
    assert cut.score < full.score


# ---------------------------------------------------------------------------
# Language
#
# A title search drags translations in alongside the original: "Proyecto Hail
# Mary" scores well against "Project Hail Mary" because the author and half the
# words match.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,wanted,expected", [
    ("Proyecto Hail Mary Spanish M4B", "english", "mismatch"),
    ("Project Hail Mary German Edition", "english", "mismatch"),
    ("Projekt Hail Mary Deutsch", "german", "match"),
    ("Project Hail Mary M4B", "english", "unknown"),
    ("Project Hail Mary M4B", "", "unknown"),
])
def test_language_verdict(title, wanted, expected):
    assert language_verdict(title, wanted) == expected


def test_a_translation_is_dropped_whatever_the_narrator_setting():
    english = dict(BOOK, language="english")
    ranked = rank_releases([
        _release(title="Project Hail Mary Spanish Edition M4B", guid="es"),
        _release(title="Project Hail Mary M4B", guid="en"),
    ], english, narrator_mode="any")
    assert [r.guid for r in ranked] == ["en"]


def test_a_release_that_names_no_language_is_kept():
    # Almost none of them do; requiring one would find nothing.
    english = dict(BOOK, language="english")
    assert len(rank_releases([_release(title="Project Hail Mary M4B")], english)) == 1


# ---------------------------------------------------------------------------
# The source chain
# ---------------------------------------------------------------------------

def _chain_config(values):
    from unittest.mock import MagicMock, patch
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: values.get(key, default)
    return patch("core.settings.config_manager", manager)


def test_the_chain_defaults_to_all_three_sources():
    from core.audiobook_release_search import configured_chain
    with _chain_config({}):
        assert configured_chain() == ["torrent", "usenet", "soulseek"]


@pytest.mark.parametrize("mode", ["torrent", "usenet", "soulseek"])
def test_a_single_source_mode_is_the_whole_chain(mode):
    from core.audiobook_release_search import configured_chain
    with _chain_config({"audiobooks.download_source.mode": mode}):
        assert configured_chain() == [mode]


def test_the_hybrid_order_is_honoured():
    from core.audiobook_release_search import configured_chain
    with _chain_config({"audiobooks.download_source.mode": "hybrid",
                        "audiobooks.download_source.hybrid_order": ["soulseek", "usenet"]}):
        assert configured_chain() == ["soulseek", "usenet"]


def test_an_empty_order_falls_back_rather_than_searching_nothing():
    from core.audiobook_release_search import configured_chain
    with _chain_config({"audiobooks.download_source.mode": "hybrid",
                        "audiobooks.download_source.hybrid_order": []}):
        assert configured_chain() == ["torrent", "usenet", "soulseek"]


def test_the_chain_reads_only_audiobook_settings():
    # Music's chain must never decide what a book search does.
    from unittest.mock import MagicMock, patch

    from core.audiobook_release_search import configured_chain
    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: default
    with patch("core.settings.config_manager", manager):
        configured_chain()
    assert all(str(call.args[0]).startswith("audiobooks.")
               for call in manager.get.call_args_list)


def test_both_sources_are_ranked_together_as_one_pool():
    # A peer with the right narrator has to be able to beat a torrent with the
    # wrong one, which cannot happen if each source is ranked on its own.
    from unittest.mock import patch

    from core.audiobook_release_search import AudiobookRelease, search_all_sources

    torrent = AudiobookRelease(source="prowlarr", protocol="torrent",
                               title="Project Hail Mary read by Scott Brick",
                               indexer="x", size_bytes=800_000_000)
    peer = AudiobookRelease(source="soulseek", protocol="soulseek",
                            title="Project Hail Mary read by Ray Porter",
                            indexer="soulseek:peer", size_bytes=800_000_000)

    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases", return_value=[torrent]), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", return_value=[peer]):
        found = search_all_sources(BOOK, narrator_mode="exact")

    assert [r.source for r in found] == ["soulseek"]


def test_a_broken_prowlarr_still_leaves_the_soulseek_results():
    from unittest.mock import patch

    from core.audiobook_release_search import AudiobookRelease, search_all_sources

    peer = AudiobookRelease(source="soulseek", protocol="soulseek",
                            title="Project Hail Mary", indexer="soulseek:peer",
                            size_bytes=800_000_000)
    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases",
               side_effect=RuntimeError("down")), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", return_value=[peer]):
        assert len(search_all_sources(BOOK)) == 1


def test_a_broken_soulseek_still_leaves_the_prowlarr_results():
    from unittest.mock import patch

    from core.audiobook_release_search import AudiobookRelease, search_all_sources

    torrent = AudiobookRelease(source="prowlarr", protocol="torrent",
                               title="Project Hail Mary", indexer="x",
                               size_bytes=800_000_000)
    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases", return_value=[torrent]), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", side_effect=RuntimeError("down")):
        assert len(search_all_sources(BOOK)) == 1


def test_a_source_the_chain_excludes_is_never_asked():
    from unittest.mock import patch

    from core.audiobook_release_search import search_all_sources

    with _chain_config({"audiobooks.download_source.mode": "torrent"}), \
         patch("core.audiobook_release_search.search_releases", return_value=[]), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search") as slsk:
        search_all_sources(BOOK)
    # Available but not in the chain: the chain is what decides, not what
    # happens to be installed.
    slsk.assert_not_called()


def test_a_soulseek_only_chain_never_touches_prowlarr():
    from unittest.mock import patch

    from core.audiobook_release_search import search_all_sources

    with _chain_config({"audiobooks.download_source.mode": "soulseek"}), \
         patch("core.audiobook_release_search.search_releases") as prowlarr, \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", return_value=[]):
        search_all_sources(BOOK)
    prowlarr.assert_not_called()


def test_every_source_being_down_is_an_error_not_an_empty_shelf():
    # Reporting it as "no releases found" would tell the user their book does
    # not exist and send a wishlist row into a backoff it did not earn.
    from unittest.mock import patch

    from core.audiobook_release_search import search_all_sources

    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases",
               side_effect=RuntimeError("prowlarr down")), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", side_effect=RuntimeError("slskd down")), \
         pytest.raises(RuntimeError):
        search_all_sources(BOOK)


def test_the_only_source_being_down_is_an_error():
    from unittest.mock import patch

    from core.audiobook_release_search import search_all_sources

    with _chain_config({"audiobooks.download_source.mode": "torrent"}), \
         patch("core.audiobook_release_search.search_releases",
               side_effect=RuntimeError("prowlarr down")), \
         pytest.raises(RuntimeError):
        search_all_sources(BOOK)


def test_a_source_that_is_only_UNCONFIGURED_never_masks_the_other_being_down():
    # slskd listed in the chain but with no URL was never really asked, so it
    # must not turn "prowlarr is down" into "one of two sources answered".
    from unittest.mock import patch

    from core.audiobook_release_search import search_all_sources

    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases",
               side_effect=RuntimeError("prowlarr down")), \
         patch("core.audiobook_soulseek.is_available", return_value=False), \
         pytest.raises(RuntimeError):
        search_all_sources(BOOK)


def test_an_unconfigured_soulseek_is_never_searched():
    from unittest.mock import patch

    from core.audiobook_release_search import search_all_sources

    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases", return_value=[]), \
         patch("core.audiobook_soulseek.is_available", return_value=False), \
         patch("core.audiobook_soulseek.search") as slsk:
        search_all_sources(BOOK)
    slsk.assert_not_called()


def test_one_source_answering_hides_the_other_being_down():
    from unittest.mock import patch

    from core.audiobook_release_search import AudiobookRelease, search_all_sources

    peer = AudiobookRelease(source="soulseek", protocol="soulseek",
                            title="Project Hail Mary", indexer="soulseek:peer",
                            size_bytes=800_000_000)
    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases",
               side_effect=RuntimeError("prowlarr down")), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", return_value=[peer]):
        assert len(search_all_sources(BOOK)) == 1


@pytest.mark.parametrize("soulseek_available", [True, False])
def test_an_outage_reads_the_same_whether_or_not_slskd_is_set_up(soulseek_available):
    """The verdict must not depend on which sources happen to be configured.

    Requiring EVERY source to have failed made "Prowlarr is down" report as
    "no releases found" on an install with slskd set up, and as an error on
    one without. Same outage, two different answers, and the first sends a
    wishlist row into a backoff it did not earn.

    Caught by an order-dependent test failure, not by design: the two
    behaviours only diverge when a real config leaks between tests.
    """
    from unittest.mock import patch

    from core.audiobook_release_search import search_all_sources

    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases",
               side_effect=RuntimeError("prowlarr down")), \
         patch("core.audiobook_soulseek.is_available", return_value=soulseek_available), \
         patch("core.audiobook_soulseek.search", return_value=[]), \
         pytest.raises(RuntimeError):
        search_all_sources(BOOK)


def test_a_source_finding_nothing_does_not_mask_another_being_down():
    # "I looked and there is none" and "I could not look" are different
    # answers, and only the first is safe to act on.
    from unittest.mock import patch

    from core.audiobook_release_search import search_all_sources

    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases", return_value=[]), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", side_effect=RuntimeError("slskd down")), \
         pytest.raises(RuntimeError):
        search_all_sources(BOOK)


def test_finding_something_beats_any_number_of_broken_sources():
    # Results win: a book that was found is a book that was found.
    from unittest.mock import patch

    from core.audiobook_release_search import AudiobookRelease, search_all_sources

    peer = AudiobookRelease(source="soulseek", protocol="soulseek",
                            title="Project Hail Mary", indexer="soulseek:peer",
                            size_bytes=800_000_000)
    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases",
               side_effect=RuntimeError("prowlarr down")), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", return_value=[peer]):
        assert len(search_all_sources(BOOK)) == 1


def test_everything_working_and_finding_nothing_is_not_an_error():
    # A genuine "this book is not out there" must still read as empty.
    from unittest.mock import patch

    from core.audiobook_release_search import search_all_sources

    with _chain_config({}), \
         patch("core.audiobook_release_search.search_releases", return_value=[]), \
         patch("core.audiobook_soulseek.is_available", return_value=True), \
         patch("core.audiobook_soulseek.search", return_value=[]):
        assert search_all_sources(BOOK) == []


# ---------------------------------------------------------------------------
# Why one release is 100MB and another 2GB
# ---------------------------------------------------------------------------

_MB = 1024 * 1024
_GB = 1024 * 1024 * 1024


def test_the_implied_bitrate_explains_the_size():
    from core.audiobook_release_search import implied_bitrate_kbps

    # A 16h10m book. These are the sizes a real search turns up.
    assert implied_bitrate_kbps(100 * _MB, 970) == 14
    assert implied_bitrate_kbps(465 * _MB, 970) == 67
    assert implied_bitrate_kbps(2 * _GB, 970) == 295


def test_the_same_size_means_different_things_at_different_runtimes():
    # Which is the whole reason size alone cannot be judged: 800MB is generous
    # for a six-hour book and thin for a forty-hour one.
    from core.audiobook_release_search import implied_bitrate_kbps

    short = implied_bitrate_kbps(800 * _MB, 6 * 60)
    long = implied_bitrate_kbps(800 * _MB, 40 * 60)
    assert short > long * 5


@pytest.mark.parametrize("size,minutes", [(0, 970), (100 * _MB, 0), (None, 970),
                                          (100 * _MB, None), ("x", 970)])
def test_an_unknowable_bitrate_is_none_not_a_guess(size, minutes):
    from core.audiobook_release_search import implied_bitrate_kbps

    assert implied_bitrate_kbps(size, minutes) is None


@pytest.mark.parametrize("kbps,band", [
    (14, "thin"), (32, "standard"), (67, "good"), (115, "generous"), (295, "oversized"),
])
def test_the_band_matches_what_audible_itself_ships(kbps, band):
    # Audible's own standard files are 32 kbps mono, enhanced 64 kbps stereo.
    # Those are the reference points, not an arbitrary scale.
    from core.audiobook_release_search import quality_band

    assert quality_band(kbps)[0] == band


def test_every_band_explains_itself():
    from core.audiobook_release_search import quality_band

    for kbps in (14, 32, 67, 115, 295):
        band, note = quality_band(kbps)
        assert band and note, kbps


def test_an_unknown_bitrate_has_no_band():
    from core.audiobook_release_search import quality_band

    assert quality_band(None) == ("", "")
    assert quality_band(0) == ("", "")


def test_a_ranked_release_carries_the_arithmetic():
    # The person choosing needs this more than the ranker does, so it rides on
    # the release whether or not it moved the score.
    release = _release("Project Hail Mary M4B")
    release.size_bytes = 465 * _MB
    ranked = rank_releases([release], BOOK, 0.0, "any")
    assert ranked[0].implied_kbps == 67
    assert ranked[0].quality_band == "good"
    assert ranked[0].quality_note
    assert any("kbps" in reason for reason in ranked[0].reasons)


def test_the_arithmetic_survives_serialisation():
    release = _release("Project Hail Mary M4B")
    release.size_bytes = 2 * _GB
    payload = rank_releases([release], BOOK, 0.0, "any")[0].to_dict()
    assert payload["implied_kbps"] == 295
    assert payload["quality_band"] == "oversized"
    assert "lossless" in payload["quality_note"]


def test_a_book_with_no_runtime_gets_no_bitrate_rather_than_a_wrong_one():
    release = _release("Project Hail Mary M4B")
    release.size_bytes = 465 * _MB
    ranked = rank_releases([release], {**BOOK, "runtime_minutes": 0}, 0.0, "any")
    assert ranked[0].implied_kbps is None
    assert ranked[0].quality_band == ""


# ---------------------------------------------------------------------------
# Catching a partial release BEFORE downloading it
# ---------------------------------------------------------------------------

def test_a_stated_bitrate_turns_a_size_into_a_runtime():
    from core.audiobook_release_search import implied_runtime_minutes

    # 100MB at 64kbps is 3.6 hours, whatever the book claims to be.
    assert round(implied_runtime_minutes(100 * _MB, 64) / 60, 1) == 3.6


def test_coverage_says_how_much_of_the_book_can_fit():
    from core.audiobook_release_search import runtime_coverage

    assert round(runtime_coverage(100 * _MB, 64, 970), 2) == 0.23
    assert runtime_coverage(465 * _MB, 64, 970) > 1.0


@pytest.mark.parametrize("size,kbps,minutes", [
    (0, 64, 970), (100 * _MB, 0, 970), (100 * _MB, 64, 0),
    (None, 64, 970), (100 * _MB, None, 970),
])
def test_coverage_is_none_rather_than_a_guess(size, kbps, minutes):
    from core.audiobook_release_search import runtime_coverage

    assert runtime_coverage(size, kbps, minutes) is None


def test_a_release_too_short_for_the_book_is_flagged_and_demoted():
    """The only partial-release check that works before downloading.

    Playing time can otherwise be measured only by decoding the files, so a
    release holding a third of the book looks identical to a well-compressed
    complete one until the download has already been spent.

    Sized to clear the completeness floor deliberately: this is the STATED
    bitrate check, and a release the floor already removes would prove nothing.
    """
    short = _release("Project Hail Mary 192kbps mp3")
    short.size_bytes = 465 * _MB          # 67 kbps implied, clears the floor
    short.bitrate_kbps = 192              # but at 192 that is only ~5.6 hours
    whole = _release("Project Hail Mary 64kbps m4b")
    whole.size_bytes = 465 * _MB
    whole.bitrate_kbps = 64

    ranked = rank_releases([short, whole], BOOK, 0.0, "any")

    assert ranked[0].title == "Project Hail Mary 64kbps m4b"
    flagged = [r for r in ranked if r.short_warning]
    assert len(flagged) == 1
    assert "35%" in flagged[0].short_warning


def test_a_release_that_does_not_name_a_bitrate_is_not_accused():
    # Inferring the bitrate from the size would be circular: the size is the
    # thing being explained.
    release = _release("Project Hail Mary mp3")
    release.size_bytes = 465 * _MB
    release.bitrate_kbps = None
    assert rank_releases([release], BOOK, 0.0, "any")[0].short_warning == ""


def test_an_abridged_release_is_not_accused_of_being_short():
    # It IS shorter than the published runtime, legitimately.
    release = _release("Project Hail Mary ABRIDGED 192kbps")
    release.size_bytes = 465 * _MB
    release.bitrate_kbps = 192
    release.abridged = True
    assert rank_releases([release], BOOK, 0.0, "any")[0].short_warning == ""


def test_the_warning_survives_serialisation():
    release = _release("Project Hail Mary 192kbps mp3")
    release.size_bytes = 465 * _MB
    release.bitrate_kbps = 192
    assert "35%" in rank_releases([release], BOOK, 0.0, "any")[0].to_dict()["short_warning"]


# ---------------------------------------------------------------------------
# The wrong edition is the wrong product
# ---------------------------------------------------------------------------

def test_an_abridged_release_is_dropped_for_an_unabridged_book():
    """Audible sells the two as separate ASINs with different runtimes.

    So the book being looked at is already one or the other, and an
    abridgement is a different product rather than a worse copy — the same
    argument that drops a release in the wrong language.
    """
    unabridged = {**BOOK, "format_type": "unabridged"}
    wrong = _release("Project Hail Mary ABRIDGED mp3")
    right = _release("Project Hail Mary Unabridged m4b")

    ranked = rank_releases([wrong, right], unabridged, 0.0, "any")

    assert [r.title for r in ranked] == ["Project Hail Mary Unabridged m4b"]


def test_an_unabridged_release_is_dropped_for_an_abridged_book():
    # Symmetrical: someone who opened the abridged entry wants the abridgement.
    abridged = {**BOOK, "format_type": "abridged"}
    ranked = rank_releases(
        [_release("Project Hail Mary Unabridged m4b"),
         _release("Project Hail Mary Abridged mp3")],
        abridged, 0.0, "any",
    )
    assert [r.title for r in ranked] == ["Project Hail Mary Abridged mp3"]


def test_a_release_that_names_no_edition_is_always_kept():
    # Most releases say nothing, and dropping them would empty the list.
    unabridged = {**BOOK, "format_type": "unabridged"}
    ranked = rank_releases([_release("Project Hail Mary m4b")], unabridged, 0.0, "any")
    assert len(ranked) == 1


def test_nothing_is_dropped_when_the_catalogue_does_not_say():
    # Without a known edition there is no mismatch to be confident about.
    silent = {**BOOK}
    silent.pop("format_type", None)
    ranked = rank_releases(
        [_release("Project Hail Mary ABRIDGED mp3"),
         _release("Project Hail Mary Unabridged m4b")],
        silent, 0.0, "any",
    )
    assert len(ranked) == 2


def test_the_edition_filter_is_independent_of_the_narrator_setting():
    # The wrong edition is wrong however relaxed the narrator choice is.
    unabridged = {**BOOK, "format_type": "unabridged"}
    for mode in ("exact", "any"):
        ranked = rank_releases(
            [_release("Project Hail Mary ABRIDGED mp3")], unabridged, 0.0, mode,
        )
        assert ranked == [], mode


# ---------------------------------------------------------------------------
# A release that is one piece of a split posting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("The Stormlight Archive 1 - The Way of Kings (1 of 5)", (1, 5)),
    ("Way of Kings [2/6] mp3", (2, 6)),
    ("Way of Kings Part 3 of 4", (3, 4)),
])
def test_a_split_posting_is_recognised(title, expected):
    from core.audiobook_release_search import part_marker

    assert part_marker(title) == expected


@pytest.mark.parametrize("title", [
    "The Way of Kings m4b",
    "The Way of Kings Disc 1",       # normal structure inside a complete set
    "The Way of Kings CD2",
    "Project Hail Mary (1 of 1)",    # a whole book saying so
    "Some Book 1 of 40",             # not a part count
])
def test_a_complete_release_is_not_mistaken_for_a_part(title):
    from core.audiobook_release_search import part_marker

    assert part_marker(title) is None


def test_a_split_posting_is_flagged_and_demoted():
    """The failure that costs a whole download and is invisible until the
    files are decoded: every chunk plays perfectly and is simply not the book.

    Caught live — a 45-hour book grabbed as "(1 of 5)" imported nothing and
    held at staged with 16% of the runtime on disk.
    """
    part = _release("The Way of Kings (1 of 5)")
    whole = _release("The Way of Kings Unabridged m4b")

    ranked = rank_releases([part, whole], BOOK, 0.0, "any")

    assert ranked[0].title == "The Way of Kings Unabridged m4b"
    flagged = [r for r in ranked if r.short_warning]
    assert len(flagged) == 1
    assert "part 1 of 5" in flagged[0].short_warning


def test_the_part_warning_says_roughly_how_much_of_the_book_it_is():
    release = _release("The Way of Kings (1 of 5)")
    warning = rank_releases([release], BOOK, 0.0, "any")[0].short_warning
    assert "20%" in warning


def test_the_part_warning_needs_no_bitrate():
    # The cheapest catch there is: the uploader already told us.
    release = _release("The Way of Kings (1 of 5)")
    release.bitrate_kbps = None
    assert rank_releases([release], BOOK, 0.0, "any")[0].short_warning


# ---------------------------------------------------------------------------
# Dramatised adaptations are a different product
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "The Way of Kings GraphicAudio (1 of 5)",
    "Graphic Audio - Mistborn",
    "The Way of Kings - A Movie In Your Mind",
    "Dramatized Adaptation",
    "Full-Cast Dramatisation",
])
def test_a_dramatisation_is_recognised(title):
    from core.audiobook_release_search import is_dramatized

    assert is_dramatized(title) is True


@pytest.mark.parametrize("title", [
    "The Way of Kings Unabridged m4b",
    "Full Cast Recording",      # Audible sells genuine full-cast READINGS
    "Narrated by a full cast",
])
def test_a_reading_is_not_mistaken_for_a_dramatisation(title):
    from core.audiobook_release_search import is_dramatized

    assert is_dramatized(title) is False


def test_a_dramatisation_is_flagged_and_outranked():
    """A full-cast re-recording with music and effects is not the audiobook.

    Different cast, unrelated running time, sold in separately-downloaded
    parts. Somebody expecting a narrator reading the book gets a radio play,
    so it is flagged — but plenty of people want exactly that, so it is not
    dropped.
    """
    drama = _release("The Way of Kings GraphicAudio")
    reading = _release("The Way of Kings Unabridged m4b")

    ranked = rank_releases([drama, reading], BOOK, 0.0, "any")

    assert ranked[0].title == "The Way of Kings Unabridged m4b"
    flagged = [r for r in ranked if r.dramatized]
    assert len(flagged) == 1
    assert "dramatised adaptation" in flagged[0].short_warning


def test_a_dramatisation_still_appears_for_someone_who_wants_one():
    ranked = rank_releases([_release("The Way of Kings GraphicAudio")], BOOK, 0.0, "any")
    assert len(ranked) == 1


def test_the_dramatisation_flag_survives_serialisation():
    payload = rank_releases(
        [_release("The Way of Kings GraphicAudio")], BOOK, 0.0, "any",
    )[0].to_dict()
    assert payload["dramatized"] is True


def test_every_problem_with_a_release_is_reported_not_just_the_last():
    """A GraphicAudio release split into five parts is two separate things
    wrong with it, and the reader needs both.

    Assigning to short_warning in turn kept only whichever check ran last,
    which silently hid the more important half — this is exactly the release
    that was grabbed live.
    """
    release = _release("The Way of Kings GraphicAudio (1 of 5)")
    warning = rank_releases([release], BOOK, 0.0, "any")[0].short_warning

    assert "dramatised adaptation" in warning
    assert "part 1 of 5" in warning


@pytest.mark.parametrize("protocol", ["torrent", "usenet", "soulseek"])
def test_the_warnings_apply_to_every_source(protocol):
    # Prowlarr supplies the indexer's title; Soulseek supplies the peer's
    # folder name. Both land in the same field and get the same checks.
    release = _release("The Way of Kings GraphicAudio (1 of 5)")
    release.protocol = protocol
    ranked = rank_releases([release], BOOK, 0.0, "any")
    assert ranked[0].dramatized is True
    assert ranked[0].short_warning


def test_a_clean_release_carries_no_warning():
    # The flags must not fire on an ordinary good release.
    ranked = rank_releases([_release("The Way of Kings Unabridged m4b")], BOOK, 0.0, "any")
    assert ranked[0].short_warning == ""
    assert ranked[0].dramatized is False


# ---------------------------------------------------------------------------
# Too small to be the whole book
# ---------------------------------------------------------------------------

def test_a_release_too_small_for_the_runtime_is_hidden():
    """Audible's own files are 32 kbps mono; nothing real is under ~24.

    Caught live: a 423MB torrent of a 45.5-hour book implied 22 kbps, ranked
    FIRST, and turned out to hold a sixth of the book. The old bounds allowed
    anything from 8 kbps up.
    """
    kings = {**BOOK, "title": "The Way of Kings",
             "author_names": ["Brandon Sanderson"], "runtime_minutes": 2730}
    tiny = _release("The Way of Kings")
    tiny.size_bytes = 423 * _MB
    real = _release("The Way of Kings Unabridged m4b")
    real.size_bytes = 1250 * _MB

    ranked = rank_releases([tiny, real], kings, 0.0, "any")

    assert [r.title for r in ranked] == ["The Way of Kings Unabridged m4b"]


def test_a_frugal_but_real_mono_rip_still_passes():
    # The floor is for "impossible", not for "good" — 32 kbps mono is exactly
    # what Audible themselves ship.
    kings = {**BOOK, "title": "The Way of Kings",
             "author_names": ["Brandon Sanderson"], "runtime_minutes": 2730}
    release = _release("The Way of Kings 32k mono")
    release.size_bytes = 625 * _MB
    assert len(rank_releases([release], kings, 0.0, "any")) == 1


def test_the_floor_works_for_a_short_book_too():
    # It is a rate, not a size: 100MB is fine for a three-hour book.
    short_book = {**BOOK, "runtime_minutes": 180}
    release = _release("Some Novella")
    release.size_bytes = 100 * _MB
    assert len(rank_releases([release], short_book, 0.0, "any")) == 1


def test_the_floor_needs_a_known_runtime():
    # With no runtime there is no rate to check, so nothing is hidden.
    from core.audiobook_release_search import too_small_to_be_complete

    assert too_small_to_be_complete(423 * _MB, 0) is False
    assert too_small_to_be_complete(423 * _MB, None) is False


def test_the_floor_measures_against_the_edition_being_viewed():
    """Both directions, with no extra rules.

    The runtime always belongs to the edition being looked at, so an abridged
    entry is measured against the abridged runtime. Picking the wrong edition
    fails this check by itself.
    """
    from core.audiobook_release_search import too_small_to_be_complete

    # 423MB is too small for 45.5 hours...
    assert too_small_to_be_complete(423 * _MB, 2730) is True
    # ...but ample for the 9-hour abridgement.
    assert too_small_to_be_complete(423 * _MB, 540) is False


def test_the_floor_is_configurable():
    from unittest.mock import MagicMock, patch

    from core.audiobook_release_search import min_complete_kbps

    manager = MagicMock()
    manager.get.side_effect = lambda key, default=None: 48
    with patch("core.settings.config_manager", manager):
        assert min_complete_kbps() == 48


def test_a_broken_setting_falls_back_to_the_default():
    from unittest.mock import MagicMock, patch

    from core.audiobook_release_search import DEFAULT_MIN_COMPLETE_KBPS, min_complete_kbps

    manager = MagicMock()
    manager.get.side_effect = RuntimeError("no config")
    with patch("core.settings.config_manager", manager):
        assert min_complete_kbps() == DEFAULT_MIN_COMPLETE_KBPS
