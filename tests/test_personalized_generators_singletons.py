"""Boundary tests for the singleton-kind personalized generators
(`hidden_gems`, `discovery_shuffle`, `popular_picks`).

Each generator wraps the legacy
``PersonalizedPlaylistsService`` method 1:1, so the tests pin:
- registration side-effect at import
- generator forwards `config.limit` correctly
- empty / None / non-dict service output → []
- tracks coerced through `Track.from_dict`
- missing service in deps raises a clear error"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List

import pytest

# Importing each generator triggers registration as a side-effect.
from core.personalized.generators import discovery_shuffle as _ds_mod
from core.personalized.generators import hidden_gems as _hg_mod
from core.personalized.generators import popular_picks as _pp_mod
from core.personalized.specs import get_registry
from core.personalized.types import PlaylistConfig


class _StubService:
    """Records every call so tests can assert on dispatched limits."""

    def __init__(self, return_value=None):
        self.calls: List[dict] = []
        self.return_value = return_value if return_value is not None else []

    def get_hidden_gems(self, limit):
        self.calls.append({'method': 'get_hidden_gems', 'limit': limit})
        return self.return_value

    def get_discovery_shuffle(self, limit):
        self.calls.append({'method': 'get_discovery_shuffle', 'limit': limit})
        return self.return_value

    def get_popular_picks(self, limit):
        self.calls.append({'method': 'get_popular_picks', 'limit': limit})
        return self.return_value


def _deps(svc):
    return SimpleNamespace(service=svc)


# ─── registration ────────────────────────────────────────────────────


class TestRegistration:
    def test_hidden_gems_registered(self):
        spec = get_registry().get('hidden_gems')
        assert spec is not None
        assert spec.kind == 'hidden_gems'
        assert spec.requires_variant is False
        assert spec.default_config.limit == 50

    def test_discovery_shuffle_registered(self):
        spec = get_registry().get('discovery_shuffle')
        assert spec is not None
        assert spec.requires_variant is False

    def test_popular_picks_registered(self):
        spec = get_registry().get('popular_picks')
        assert spec is not None
        assert spec.requires_variant is False

    def test_display_names(self):
        assert get_registry().get('hidden_gems').display_name('') == 'Hidden Gems'
        assert get_registry().get('discovery_shuffle').display_name('') == 'Discovery Shuffle'
        assert get_registry().get('popular_picks').display_name('') == 'Popular Picks'


# ─── generator dispatch ──────────────────────────────────────────────


class TestHiddenGemsGenerator:
    def test_forwards_limit(self):
        svc = _StubService()
        _hg_mod.generate(_deps(svc), '', PlaylistConfig(limit=75))
        assert svc.calls == [{'method': 'get_hidden_gems', 'limit': 75}]

    def test_uses_default_limit_when_config_default(self):
        svc = _StubService()
        _hg_mod.generate(_deps(svc), '', PlaylistConfig())
        assert svc.calls[0]['limit'] == 50

    def test_coerces_tracks(self):
        svc = _StubService(return_value=[
            {'track_name': 'A', 'artist_name': 'X', 'spotify_track_id': 'sp-1'},
            {'track_name': 'B', 'artist_name': 'Y', 'spotify_track_id': 'sp-2'},
        ])
        out = _hg_mod.generate(_deps(svc), '', PlaylistConfig())
        assert len(out) == 2
        assert out[0].track_name == 'A'
        assert out[0].spotify_track_id == 'sp-1'

    def test_empty_service_output_returns_empty_list(self):
        svc = _StubService(return_value=[])
        out = _hg_mod.generate(_deps(svc), '', PlaylistConfig())
        assert out == []

    def test_none_service_output_returns_empty_list(self):
        svc = _StubService(return_value=None)
        out = _hg_mod.generate(_deps(svc), '', PlaylistConfig())
        assert out == []


class TestDiscoveryShuffleGenerator:
    def test_forwards_limit(self):
        svc = _StubService()
        _ds_mod.generate(_deps(svc), '', PlaylistConfig(limit=42))
        assert svc.calls == [{'method': 'get_discovery_shuffle', 'limit': 42}]

    def test_coerces_tracks(self):
        svc = _StubService(return_value=[{'track_name': 'Z', 'artist_name': 'Q'}])
        out = _ds_mod.generate(_deps(svc), '', PlaylistConfig())
        assert out[0].track_name == 'Z'


class TestPopularPicksGenerator:
    def test_forwards_limit(self):
        svc = _StubService()
        _pp_mod.generate(_deps(svc), '', PlaylistConfig(limit=10))
        assert svc.calls == [{'method': 'get_popular_picks', 'limit': 10}]


# ─── deps validation ─────────────────────────────────────────────────


class TestDepsValidation:
    def test_missing_service_raises(self):
        # No `service` attribute on deps.
        deps = SimpleNamespace()
        with pytest.raises(RuntimeError, match='missing `service`'):
            _hg_mod.generate(deps, '', PlaylistConfig())

    def test_dict_form_deps_accepted(self):
        # generators._common.get_service tolerates dict deps too.
        svc = _StubService()
        out = _hg_mod.generate({'service': svc}, '', PlaylistConfig())
        assert isinstance(out, list)
        assert svc.calls
