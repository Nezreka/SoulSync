"""Sokhi's wrong-cover-art report: 'Vol.4' must never match 'Vol.4.5'.

Two paths served wrong art for volume-numbered series:
- the art picker's _album_matches subset check (Vol.4's tokens are a subset
  of Vol.4.5's once CJK/punctuation normalizes away) — covered in
  tests/metadata/test_art_lookup.py
- MusicBrainz match_release: 0.97 string similarity let the wrong volume
  win, and its MBID feeds CAA art with no downstream validation — covered
  here, plus the shared helper itself.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.text.title_match import numeric_tokens_differ
from core.musicbrainz_service import MusicBrainzService

VOL4 = "B小町 - TVアニメ「【推しの子】」キャラクターソングCD Vol.4"
VOL45 = "B小町 - TVアニメ「【推しの子】」キャラクターソングCD Vol.4.5"


def test_helper_volume_and_sequel_differ():
    assert numeric_tokens_differ(VOL4, VOL45)
    assert numeric_tokens_differ("Album", "Album 2")
    assert numeric_tokens_differ("Now 99", "Now 100")


def test_helper_shared_or_no_digits_match():
    assert not numeric_tokens_differ("1989", "1989 (Deluxe)")
    assert not numeric_tokens_differ(VOL4, VOL4)
    assert not numeric_tokens_differ("IGOR", "IGOR (Deluxe)")
    assert not numeric_tokens_differ("", "")


def _service_with_results(results):
    svc = MusicBrainzService.__new__(MusicBrainzService)
    svc.mb_client = MagicMock()
    svc.mb_client.search_release.return_value = results
    svc._check_cache = lambda *a, **k: None
    svc._save_to_cache = lambda *a, **k: None
    return svc


def _result(title, score=100, mbid="mb-x"):
    return {
        'id': mbid, 'title': title, 'score': score,
        'artist-credit': [{'artist': {'name': 'B小町'}}],
    }


def test_match_release_rejects_wrong_volume():
    """Only the wrong volume is returned by search → no match at all is
    better than caching Vol.4.5's MBID (CAA would serve its art unvalidated)."""
    svc = _service_with_results([_result(VOL45, score=100, mbid='mb-wrong')])
    assert svc.match_release(VOL4, 'B小町') is None


def test_match_release_prefers_exact_volume_over_near_identical():
    svc = _service_with_results([
        _result(VOL45, score=100, mbid='mb-wrong'),
        _result(VOL4, score=90, mbid='mb-right'),
    ])
    got = svc.match_release(VOL4, 'B小町')
    assert got and got['mbid'] == 'mb-right'
