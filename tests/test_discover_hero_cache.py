"""The discover-hero payload cache.

The hero endpoint is the slowest request on the discover page (6-14s live:
the 200-row consensus query, metadata fetches for missing art, the rotation
write) and it is the FIRST thing the page shows. The payload now caches per
(profile, source) for ten minutes, so only the first load after a restart
pays. Empty answers stay uncached so data appearing shows up immediately.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask

import core.discovery.hero as hero


def _artist(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        similar_artist_name=name,
        similar_artist_spotify_id='sp-1',
        similar_artist_itunes_id='it-1',
        similar_artist_musicbrainz_id=None,
        occurrence_count=3,
        similarity_rank=1,
        image_url='https://img/x.jpg',
        genres=['idm'],
        popularity=70,
    )


class _FakeDb:
    def __init__(self, artists):
        self.artists = artists
        self.top_calls = 0

    def get_top_similar_artists(self, **_kw):
        self.top_calls += 1
        return list(self.artists)

    def get_blocklist(self, *_a, **_kw):
        return []

    def mark_artists_featured(self, _names):
        pass

    def get_watchlist_artists(self, profile_id=None):
        return []

    def get_owned_album_count_by_artist_name(self, artist_name):
        return 3 if artist_name == 'Aphex Twin' else 0


@pytest.fixture()
def app_ctx(monkeypatch):
    app = Flask(__name__)
    hero._HERO_CACHE.clear()
    hero.init(lambda: None)
    monkeypatch.setattr(hero, '_get_active_discovery_source', lambda: 'spotify')
    with app.test_request_context():
        yield app


def test_second_request_serves_from_cache(app_ctx, monkeypatch):
    db = _FakeDb([_artist('Aphex Twin')])
    monkeypatch.setattr(hero, 'get_database', lambda: db)

    first = hero.get_discover_hero().get_json()
    assert first['success'] is True
    assert first['artists'][0]['artist_name'] == 'Aphex Twin'
    # The ownership meter rides the payload (and therefore the cache).
    assert first['artists'][0]['owned_album_count'] == 3
    assert db.top_calls == 1

    second = hero.get_discover_hero().get_json()
    assert second == first
    # The heavy consensus query did NOT run again.
    assert db.top_calls == 1


def test_expired_cache_recomputes(app_ctx, monkeypatch):
    db = _FakeDb([_artist('Aphex Twin')])
    monkeypatch.setattr(hero, 'get_database', lambda: db)

    hero.get_discover_hero()
    # Force expiry.
    for key, (_, payload) in list(hero._HERO_CACHE.items()):
        hero._HERO_CACHE[key] = (0, payload)
    hero.get_discover_hero()
    assert db.top_calls == 2


def test_empty_answers_stay_uncached(app_ctx, monkeypatch):
    db = _FakeDb([])
    monkeypatch.setattr(hero, 'get_database', lambda: db)

    out = hero.get_discover_hero().get_json()
    assert out['artists'] == []
    assert hero._HERO_CACHE == {}
    # A scan lands data — the very next request sees it.
    db.artists = [_artist('SZA')]
    out2 = hero.get_discover_hero().get_json()
    assert out2['artists'][0]['artist_name'] == 'SZA'


def test_hero_cards_carry_the_explanation(app_ctx, monkeypatch):
    db = _FakeDb([_artist('Aphex Twin'), _artist('SZA')])
    db.get_recommendation_sources = lambda names, profile_id=1: {'Aphex Twin': ['Autechre']}
    monkeypatch.setattr(hero, 'get_database', lambda: db)

    by_name = {a['artist_name']: a for a in hero.get_discover_hero().get_json()['artists']}
    assert by_name['Aphex Twin']['explanation'] == {
        'kind': 'similar_to', 'confidence': 0.5,
        'seeds': [{'name': 'Autechre', 'id': None, 'source': None}]}
    # no resolvable source: still the shape, confidence from how many point here
    assert by_name['SZA']['explanation'] == {'kind': 'similar_to', 'seeds': [], 'confidence': 0.88}


def _less_row(name: str) -> dict:
    return {'kind': 'less', 'entity_type': 'artist', 'name': name,
            'artist_name': None, 'seed_context_json': None, 'ids_json': '{}',
            'created_at': '2026-09-26 00:00:00', 'expires_at': None}


def test_less_like_this_sinks_the_hero(app_ctx, monkeypatch):
    # "less like this" ranks lower everywhere, hero included.
    db = _FakeDb([_artist('Aphex Twin'), _artist('SZA')])
    db.get_recommendation_sources = lambda names, profile_id=1: {}
    db.get_discovery_feedback = lambda profile_id: [_less_row('Aphex Twin')]
    monkeypatch.setattr(hero, 'get_database', lambda: db)

    names = [a['artist_name'] for a in hero.get_discover_hero().get_json()['artists']]
    assert names[0] == 'SZA'
    assert names[-1] == 'Aphex Twin'


def test_less_like_this_reranks_the_cached_hero(app_ctx, monkeypatch):
    # The re-rank also applies on a cache hit: the payload is cached, the
    # feedback is not, and the heavy query must not re-run.
    db = _FakeDb([_artist('Aphex Twin'), _artist('SZA')])
    db.get_recommendation_sources = lambda names, profile_id=1: {}
    db.feedback_rows = []
    db.get_discovery_feedback = lambda profile_id: list(db.feedback_rows)
    monkeypatch.setattr(hero, 'get_database', lambda: db)

    first = hero.get_discover_hero().get_json()
    assert [a['artist_name'] for a in first['artists']][0] == 'Aphex Twin'

    db.feedback_rows = [_less_row('Aphex Twin')]
    second = hero.get_discover_hero().get_json()
    names = [a['artist_name'] for a in second['artists']]
    assert names[0] == 'SZA'
    assert names[-1] == 'Aphex Twin'
    assert db.top_calls == 1
