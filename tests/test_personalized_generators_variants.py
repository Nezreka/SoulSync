"""Boundary tests for variant-bearing personalized generators
(`time_machine` per decade, `genre_playlist` per genre).

Each generator coerces a URL-safe variant string into the form the
legacy service expects, then forwards. Tests pin the variant
parsing + service dispatch + variant_resolver listing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import List

import pytest

from core.personalized.generators import genre_playlist as _gp_mod
from core.personalized.generators import time_machine as _tm_mod
from core.personalized.specs import get_registry
from core.personalized.types import PlaylistConfig


class _StubService:
    GENRE_MAPPING = {
        'Electronic/Dance': ['house', 'techno'],
        'Hip Hop/Rap': ['hip hop', 'rap'],
        'Rock': ['rock', 'punk'],
    }

    def __init__(self):
        self.calls: List[dict] = []

    def get_decade_playlist(self, decade, limit, **kw):
        self.calls.append({'method': 'get_decade_playlist', 'decade': decade, 'limit': limit})
        return [{'track_name': f'D{decade}', 'artist_name': 'A'}]

    def get_genre_playlist(self, genre, limit, **kw):
        self.calls.append({'method': 'get_genre_playlist', 'genre': genre, 'limit': limit})
        return [{'track_name': f'G{genre}', 'artist_name': 'A'}]


def _deps():
    return SimpleNamespace(service=_StubService())


# ─── time_machine ───────────────────────────────────────────────────


class TestTimeMachine:
    def test_registered(self):
        spec = get_registry().get('time_machine')
        assert spec is not None
        assert spec.requires_variant is True
        assert spec.variant_resolver is not None

    def test_variant_resolver_returns_decades(self):
        spec = get_registry().get('time_machine')
        decades = spec.variant_resolver(_deps())
        assert '1980s' in decades
        assert '2020s' in decades
        # All decades should be 4-digit + 's'
        for d in decades:
            assert d.endswith('s')
            assert d[:-1].isdigit()

    def test_decade_label_to_year(self):
        deps = _deps()
        _tm_mod.generate(deps, '1980s', PlaylistConfig(limit=20))
        assert deps.service.calls == [
            {'method': 'get_decade_playlist', 'decade': 1980, 'limit': 20}
        ]

    def test_invalid_variant_raises(self):
        deps = _deps()
        with pytest.raises(ValueError, match='not a decade label'):
            _tm_mod.generate(deps, 'banana', PlaylistConfig())

    def test_out_of_range_year_raises(self):
        deps = _deps()
        with pytest.raises(ValueError, match='out of range'):
            _tm_mod.generate(deps, '1500s', PlaylistConfig())

    def test_tolerates_no_s_suffix(self):
        deps = _deps()
        _tm_mod.generate(deps, '1990', PlaylistConfig())
        assert deps.service.calls[0]['decade'] == 1990

    def test_default_limit_is_100(self):
        spec = get_registry().get('time_machine')
        assert spec.default_config.limit == 100

    def test_display_name_with_variant(self):
        spec = get_registry().get('time_machine')
        assert spec.display_name('1980s') == 'Time Machine — 1980s'


# ─── genre_playlist ─────────────────────────────────────────────────


class TestGenrePlaylist:
    def test_registered(self):
        spec = get_registry().get('genre_playlist')
        assert spec is not None
        assert spec.requires_variant is True

    def test_variant_resolver_normalizes_parent_keys(self):
        spec = get_registry().get('genre_playlist')
        variants = spec.variant_resolver(_deps())
        # 'Electronic/Dance' → 'electronic_dance' (slash → underscore + lowercase)
        assert 'electronic_dance' in variants
        assert 'hip_hop_rap' in variants
        assert 'rock' in variants

    def test_normalized_variant_resolves_to_parent_key(self):
        deps = _deps()
        _gp_mod.generate(deps, 'electronic_dance', PlaylistConfig())
        # Service receives ORIGINAL parent key.
        assert deps.service.calls[0]['genre'] == 'Electronic/Dance'

    def test_unknown_variant_passed_through_as_freeform(self):
        # Service handles partial-matching for free-form keywords.
        deps = _deps()
        _gp_mod.generate(deps, 'shoegaze', PlaylistConfig())
        assert deps.service.calls[0]['genre'] == 'shoegaze'

    def test_empty_variant_raises(self):
        deps = _deps()
        with pytest.raises(ValueError, match='requires a variant'):
            _gp_mod.generate(deps, '', PlaylistConfig())

    def test_display_name(self):
        spec = get_registry().get('genre_playlist')
        assert spec.display_name('electronic_dance') == 'Genre — electronic_dance'
