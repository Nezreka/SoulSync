"""#1448: strict discography matching must reject substring-only title pairs.

Download Discography asks "is this release owned?" for each discography card.
For requested "Respiro" (2019, 13 tracks) vs library "Sessão Respiro" (2020,
7 tracks) the strict match gate returned True, so every track of the card
was skipped as "already in library". Edition markers are cleaned before the
substring check, so leftover words are meaningful: one title containing the
other means different releases.
"""

import pytest

import database.music_database as mdb


@pytest.fixture(scope="module")
def gate():
    # pure logic: no DB connection needed, bypass __init__ entirely
    db = mdb.MusicDatabase.__new__(mdb.MusicDatabase)

    def check(search, owned, sims=(0.667, 0.667, 0.8), tracks=(13, 7)):
        return db._passes_strict_discography_album_match(
            search, owned, sims[0], sims[1], sims[2], tracks[0], tracks[1])

    return check


def test_substring_pair_is_rejected(gate):  # the #1448 case
    assert gate("Respiro", "Sessão Respiro") is False


def test_reversed_substring_pair_is_rejected(gate):
    assert gate("Sessão Respiro", "Respiro") is False


def test_identical_titles_still_accepted(gate):
    assert gate("Respiro", "Respiro") is True


def test_edition_marker_variants_still_accepted(gate):
    # "(Deluxe Edition)" is stripped by the cleaning step, so the leftover
    # titles are equal — this is an edition match, not a substring case.
    assert gate("Album", "Album (Deluxe Edition)") is True


def test_distinct_unrelated_titles_still_accepted(gate):
    # the non-soundtrack branch previously returned True for anything that
    # reached it; that behavior is preserved for non-substring pairs.
    assert gate("Random Access Memories", "Discovery") is True


def test_empty_title_cannot_substring_match(gate):
    # "" in "x" is True, so the truthiness guards matter
    assert gate("", "Respiro") is True


def test_soundtrack_identical_titles_still_accepted(gate):
    assert gate("Dune (Original Motion Picture Soundtrack)",
               "Dune (Original Motion Picture Soundtrack)") is True


def test_soundtrack_token_overlap_path_unchanged(gate):
    # goes past the equality checks into the distinctive-token logic, which
    # is byte-identical to before #1448
    assert gate("Dune Part Two (Original Motion Picture Soundtrack)",
               "Dune Part Two Soundtrack",
               sims=(0.95, 0.90, 0.92), tracks=(22, 22)) is True
