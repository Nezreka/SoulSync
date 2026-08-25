"""Automatic MusicBrainz artist matching has to reach a cross-script artist.

Manual matching should be the exception, so the worker has to be able to land
`Sawano Hiroyuki` on `澤野弘之` by itself. Today it structurally cannot, for two
reasons that compound:

1. ``match_artist`` searches strict-only. A strict query hits the ``artist``
   field alone and skips MusicBrainz's alias and sortname indexes, which is
   where a romanised spelling of a natively-scripted artist lives. Issue #586
   fixed this in ``lookup_artist_aliases`` and never came back for this one.

2. Its confidence is ``similarity * 60 + mb_score * 0.4``. Across scripts the
   similarity is 0.0 by construction, so the ceiling is 40 against a gate of
   70 — unreachable no matter how certain MusicBrainz is.

And the signal that *would* settle it is already computed here and thrown away:
the overlap between the albums the user owns and the candidate's release
groups. Album titles survive a script difference far better than names, and an
entity whose catalogue contains records this library holds is not a different
person. It was only ever consulted to disambiguate candidates that had already
passed the name gate — never for the case where the name is worthless.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.musicbrainz_service import MusicBrainzService

OWNED = ['TV Anime "Attack on Titan Season 2" (Original Soundtrack)']


@pytest.fixture
def service():
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    return svc


def _catalogue(svc, titles_by_mbid):
    svc._candidate_release_titles = lambda mbid: titles_by_mbid.get(mbid, [])


def test_a_cross_script_artist_is_matched_on_its_catalogue(service):
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "澤野弘之", "score": 100},
    ]
    _catalogue(service, {"mbid-sawano": OWNED})

    out = service.match_artist("Sawano Hiroyuki", owned_titles=OWNED)

    assert out is not None
    assert out["mbid"] == "mbid-sawano"


def test_a_cross_script_candidate_without_catalogue_overlap_is_refused(service):
    # MusicBrainz is confident about the name, but nothing this library owns
    # is in that entity's catalogue — so there is no evidence it is the same
    # artist, and inventing one would smear an id onto the wrong row.
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-someone", "name": "米津玄師", "score": 100},
    ]
    _catalogue(service, {"mbid-someone": ["Some Other Album"]})

    assert service.match_artist("Sawano Hiroyuki", owned_titles=OWNED) is None


def test_a_cross_script_candidate_is_refused_when_we_own_nothing_yet(service):
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "澤野弘之", "score": 100},
    ]
    _catalogue(service, {"mbid-sawano": OWNED})

    assert service.match_artist("Sawano Hiroyuki", owned_titles=[]) is None


def test_a_lukewarm_cross_script_candidate_is_refused(service):
    # MusicBrainz itself is unsure; catalogue overlap alone is not enough to
    # override that.
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-sawano", "name": "澤野弘之", "score": 55},
    ]
    _catalogue(service, {"mbid-sawano": OWNED})

    assert service.match_artist("Sawano Hiroyuki", owned_titles=OWNED) is None


def test_the_alias_index_is_consulted_when_a_strict_search_finds_nothing(service):
    def _search(name, limit=5, strict=True, **_kw):
        return [] if strict else [
            {"id": "mbid-sawano", "name": "澤野弘之", "score": 100}]

    service.mb_client.search_artist.side_effect = _search
    _catalogue(service, {"mbid-sawano": OWNED})

    out = service.match_artist("Sawano Hiroyuki", owned_titles=OWNED)

    assert out is not None and out["mbid"] == "mbid-sawano"


def test_a_same_script_match_is_unchanged(service):
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-mj", "name": "Michael Jackson", "score": 100},
    ]
    _catalogue(service, {"mbid-mj": ["Thriller"]})

    out = service.match_artist("Michael Jackson", owned_titles=["Thriller"])

    assert out is not None
    assert out["mbid"] == "mbid-mj"
    assert out["confidence"] >= 70


def test_a_same_script_near_miss_is_still_refused(service):
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-grant", "name": "Amy Grant", "score": 60},
    ]
    _catalogue(service, {"mbid-grant": ["Heart in Motion"]})

    assert service.match_artist("Grant", owned_titles=["Heart in Motion"]) is None


def test_a_shared_album_title_does_not_outrank_a_same_script_name_match():
    """The cross-script rescue is a fallback, never a preemption.

    Ahead of the name gate it returned the FIRST cross-script candidate with one
    overlapping album — worth 80 by its own formula — before a same-script
    candidate scoring 95 was ever considered. One shared title is a low bar:
    "Home", "Anthology", a soundtrack two artists both appear on. And the id it
    picked did not just answer this call, it was written onto the artist row and
    fed the alias bridge from then on.
    """
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    svc.mb_client.search_artist.return_value = [
        {"id": "mbid-right", "name": "Sawano Hiroyuki", "score": 95},
        {"id": "mbid-cross", "name": "澤野弘之", "score": 100},
    ]
    _catalogue(svc, {"mbid-right": OWNED, "mbid-cross": OWNED})

    out = svc.match_artist("Sawano Hiroyuki", owned_titles=OWNED)

    assert out is not None
    assert out["mbid"] == "mbid-right"


def test_the_rescue_still_speaks_when_no_name_clears_the_gate():
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc._check_cache = MagicMock(return_value=None)
    svc._save_to_cache = MagicMock()
    svc.mb_client.search_artist.return_value = [
        {"id": "mbid-noise", "name": "Somebody Else", "score": 40},
        {"id": "mbid-cross", "name": "澤野弘之", "score": 100},
    ]
    _catalogue(svc, {"mbid-noise": [], "mbid-cross": OWNED})

    out = svc.match_artist("Sawano Hiroyuki", owned_titles=OWNED)

    assert out is not None
    assert out["mbid"] == "mbid-cross"


# --- when two candidates both hold something we own ------------------------


def test_two_tied_cross_script_candidates_are_refused_rather_than_ordered(service):
    """Result ORDER must not be the tie-break.

    Both candidates are written in another script, so the ranking above is
    decided almost entirely by MusicBrainz's name relevance — the one signal
    this branch exists precisely because it cannot use. And an album title as
    ordinary as "Home" overlaps several catalogues. Taking the first hit would
    write a coin flip onto the artist row, where it then feeds the alias
    bridge for every later verification.
    """
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-first", "name": "米津玄師", "score": 98},
        {"id": "mbid-second", "name": "澤野弘之", "score": 97},
    ]
    _catalogue(service, {"mbid-first": ["Home"], "mbid-second": ["Home"]})

    assert service.match_artist("Sawano Hiroyuki", owned_titles=["Home"]) is None
    service._save_to_cache.assert_called_once()   # the "no match" row, not a pick
    assert service._save_to_cache.call_args.args[3] is None


def test_the_candidate_with_more_owned_albums_wins_regardless_of_order(service):
    """A decisive winner is still taken — and it is not the one MusicBrainz
    happened to rank first."""
    service.mb_client.search_artist.return_value = [
        {"id": "mbid-decoy", "name": "米津玄師", "score": 99},
        {"id": "mbid-sawano", "name": "澤野弘之", "score": 95},
    ]
    owned = OWNED + ["Kill la Kill Original Soundtrack"]
    _catalogue(service, {
        "mbid-decoy": [OWNED[0]],
        "mbid-sawano": owned,
    })

    out = service.match_artist("Sawano Hiroyuki", owned_titles=owned)

    assert out is not None
    assert out["mbid"] == "mbid-sawano"
