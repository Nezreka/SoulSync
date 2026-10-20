"""German release names must match the catalogue spelling.

Found on a real install: of 43 wishlisted audiobooks that SoulSync reported as
"No releases found", several had releases on the indexer that the relevance
filter threw away (minimum 0.5):

* "Fußballstar in Gefahr" against "Fussballstar": the token pattern is
  ``[a-z0-9]+``, so the unfolded eszett split the word into "fu" and "ballstar"
  (relevance 0.25).
* "Die drei !!!-043Nixensommer-16BIT-...": the digits are glued to the title, so
  the word was "043nixensommer" (relevance 0.00).
* A series release names the series, not the authors, so a one-word title such
  as "Nixensommer" lost the 60 % that the author carries (relevance 0.40).
"""
from core.audiobook_release_search import (
    AudiobookRelease,
    normalize_text,
    rank_releases,
    significant_tokens,
    title_relevance,
)

AUTHORS = ["Hartmut Cyriacks", "Peter Nissen"]
SERIES = [{"title": "Die drei !!!", "sequence": "43"}]


def test_eszett_and_umlauts_fold_to_the_spelling_releases_use():
    assert significant_tokens("Fußballstar in Gefahr") == ["fussballstar", "gefahr"]
    assert significant_tokens("Das geheime Parfüm") == ["das", "geheime", "parfuem"]
    # the release writes the ASCII form, both sides now agree
    assert title_relevance(
        "Die Drei Ausrufezeichen-Fall 24 Fussballstar In Gefahr-DE-AUDIOBOOK-CD",
        "Fußballstar in Gefahr", AUTHORS) >= 0.5
    assert title_relevance(
        "Die drei Ausrufezeichen  - 059  Das geheime Parfuem",
        "Das geheime Parfüm", AUTHORS) >= 0.5
    assert title_relevance(
        "Die drei Ausrufezeichen  - 061 Der Fall Dornroeschen",
        "Der Fall Dornröschen", AUTHORS) >= 0.5


def test_other_accents_are_dropped_not_split():
    assert significant_tokens("Café Crème") == ["cafe", "creme"]


def test_digits_glued_to_a_word_are_split_but_format_tokens_stay_whole():
    assert normalize_text("Die drei !!!-043Nixensommer-16BIT") == "die drei 043 nixensommer 16 bit"
    assert normalize_text("Folge14 Wald") == "folge 14 wald"
    for token in ("mp3", "m4b", "h264", "x265"):
        assert token in normalize_text(f"Book.{token}").split()


def test_series_release_without_authors_clears_the_floor():
    for release in (
        "Die drei !!!-043Nixensommer-16BIT-44-KHZ-WEB-FLAC-2016-WALKMAN",
        "Die drei Ausrufezeichen - Folge 43 - Nixensommer",
    ):
        assert title_relevance(release, "Nixensommer", AUTHORS, SERIES) >= 0.5, release


def test_series_name_alone_never_makes_another_book_look_right():
    # another volume of the same series: the title is absent, so no series credit
    assert title_relevance(
        "Die drei Ausrufezeichen - Folge 44 - Spuk am See",
        "Nixensommer", AUTHORS, SERIES) < 0.5
    # same single word, different book and author, no series words
    assert title_relevance(
        "Jennifer Wellen - Rosebay Hope - Herzklopfen in Irland",
        "Herzklopfen!", AUTHORS,
        [{"title": "Die drei !!!", "sequence": "25"}]) < 0.5


def test_series_words_already_in_the_title_do_not_vouch_for_the_release():
    # review: the series name is part of the title, so it must not count twice
    harry = [{"title": "Harry Potter", "sequence": "2"}]
    assert title_relevance(
        "Harry Potter and the Prisoner of Azkaban",
        "Harry Potter and the Chamber of Secrets", ["J.K. Rowling"], harry) < 0.5
    # "Dune Messiah" is another book than "Dune"
    assert title_relevance(
        "Dune Messiah", "Dune", ["Frank Herbert"],
        [{"title": "Dune", "sequence": "1"}]) < 0.5
    # the right volume still passes on its title and author alone
    assert title_relevance(
        "Harry Potter and the Chamber of Secrets (Unabridged) - J.K. Rowling",
        "Harry Potter and the Chamber of Secrets", ["J.K. Rowling"], harry) >= 0.5


def test_without_series_the_old_rule_is_unchanged():
    assert title_relevance(
        "Die drei Ausrufezeichen - Folge 43 - Nixensommer",
        "Nixensommer", AUTHORS) < 0.5


def test_rank_releases_keeps_the_right_release_and_drops_the_wrong_volume():
    book = {
        "title": "Nixensommer", "author_names": AUTHORS, "series": SERIES,
        "runtime_minutes": 55, "narrator_names": [],
    }

    def release(title, size_mb):
        return AudiobookRelease(
            source="prowlarr", protocol="usenet", title=title, indexer="x",
            size_bytes=size_mb * 1024 * 1024, guid=title)

    kept = rank_releases([
        release("Die drei Ausrufezeichen - Folge 43 - Nixensommer", 146),
        release("Die drei !!!-043Nixensommer-16BIT-44-KHZ-WEB-FLAC-2016-WALKMAN", 425),
        release("Die drei Ausrufezeichen - Folge 44 - Spuk am See", 140),
    ], book)
    titles = {r.title for r in kept}
    assert "Die drei Ausrufezeichen - Folge 43 - Nixensommer" in titles
    assert not any("Folge 44" in t for t in titles)
