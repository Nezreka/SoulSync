"""Popular Picks was empty for deezer users — wrong popularity-threshold scale.

The discovery pool synthesizes deezer popularity onto a 0-100 score (base 45 + bonuses, capped
at 100), but _get_popularity_thresholds had deezer on the raw-rank scale (500000/100000). So
`popularity >= 500000` matched nothing (Popular Picks empty) while `< 100000` matched the whole
pool (Hidden Gems caught everything). Thresholds must stay on the 0-100 scale.
"""

from __future__ import annotations

from core.personalized_playlists import PersonalizedPlaylistsService


def _svc():
    return PersonalizedPlaylistsService(database=None)


def test_deezer_thresholds_are_on_the_0_100_scale():
    popular_min, hidden_max = _svc()._get_popularity_thresholds('deezer')
    assert (popular_min, hidden_max) == (60, 50)         # was (500000, 100000) — unreachable
    assert 0 < popular_min <= 100 and 0 < hidden_max <= 100
    assert popular_min > hidden_max                       # popular tier sits above the hidden tier


def test_spotify_thresholds_unchanged():
    assert _svc()._get_popularity_thresholds('spotify') == (60, 40)


def test_case_insensitive():
    assert _svc()._get_popularity_thresholds('Deezer') == (60, 50)
    assert _svc()._get_popularity_thresholds('SPOTIFY') == (60, 40)


def test_sources_without_popularity_skip_the_filter():
    assert _svc()._get_popularity_thresholds('itunes') == (None, None)
    assert _svc()._get_popularity_thresholds('musicbrainz') == (None, None)
    assert _svc()._get_popularity_thresholds('') == (None, None)
