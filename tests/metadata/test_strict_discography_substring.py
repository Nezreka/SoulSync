"""#1448: strict discography matching must reject substring-only title pairs.

Download Discography asks "is this release owned?" for each discography card.
For requested "Respiro" (2019, 13 tracks) vs library "Sessão Respiro" (2020,
7 tracks) the strict match gate returned True, so every track of the card
was skipped as "already in library".

The substring check runs on normalized titles; the equality checks run on
both normalized and edition-cleaned titles. So one title containing the other
means different releases — except the known residual below where an edition
marker on the *requested* side breaks the substring (documented, not fixed:
closing it would newly reject plausible same-release edition labelings).
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


def test_edition_marker_on_requested_side_is_known_residual(gate):
    # Review note (#1448): the substring check runs on normalized (uncleaned)
    # titles, so "Respiro (Remastered)" is not a substring of "Sessão Respiro"
    # and the pair is still accepted — the exact false-ownership shape of the
    # issue. Documented here so any future change to this trade-off is
    # deliberate: checking cleaned titles too would newly reject plausible
    # same-release edition labelings ("Respiro (Deluxe Edition)" vs
    # "Respiro Deluxe").
    assert gate("Respiro (Remastered)", "Sessão Respiro") is True


def test_article_drift_rejects_in_safe_direction(gate):
    # Review note (#1448): normalization strips neither articles nor
    # punctuation, so "The Dark Side of the Moon" vs "Dark Side of the Moon"
    # is a substring pair and gets rejected — a false negative (the card shows
    # missing / Download Discography queues a re-download), never a false
    # skip. That is the failure direction the issue prefers.
    assert gate("The Dark Side of the Moon", "Dark Side of the Moon") is False
