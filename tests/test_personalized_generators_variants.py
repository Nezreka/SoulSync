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
        # the parent genre as discover names it, never the url key
        assert spec.display_name('electronic_dance') == 'Genre — Electronic/Dance'
        assert spec.display_name('hip_hop_rap') == 'Genre — Hip Hop/Rap'
        assert spec.display_name('deep_house') == 'Genre — Deep House'   # free-form keyword


def test_seasonal_names_read_like_discover():
    spec = get_registry().get('seasonal_mix')
    assert spec.display_name('halloween') == 'Seasonal — Halloween Hits'
    assert spec.display_name('nope') == 'Seasonal — Nope'


def test_kinds_carry_variant_labels():
    from types import SimpleNamespace
    from core.personalized import api
    manager = SimpleNamespace(deps=SimpleNamespace(service=SimpleNamespace(
        GENRE_MAPPING={'Electronic/Dance': []})))
    kinds = {k['kind']: k for k in api.list_kinds(manager=manager)['kinds']}
    assert kinds['seasonal_mix']['variant_labels']['halloween'] == 'Halloween Hits'
    assert kinds['genre_playlist']['variant_labels']['electronic_dance'] == 'Electronic/Dance'


def test_time_machine_lists_only_decades_with_music():
    svc = _StubService()
    svc.available_decades = lambda: [1980, 2010]
    assert get_registry().get('time_machine').variant_resolver(
        SimpleNamespace(service=svc)) == ['1980s', '2010s']


def test_genre_lists_only_families_the_pool_has_biggest_first():
    svc = _StubService()
    svc.get_available_genres = lambda: [{'name': 'Rock', 'track_count': 90},
                                        {'name': 'Electronic/Dance', 'track_count': 40},
                                        {'name': 'Other', 'track_count': 9}]
    assert get_registry().get('genre_playlist').variant_resolver(
        SimpleNamespace(service=svc)) == ['rock', 'electronic_dance']


def test_available_decades_reads_only_this_profiles_pool(tmp_path):
    from core.personalized_playlists import PersonalizedPlaylistsService
    from core.profile_context import reset_background_profile, set_background_profile
    from database.music_database import MusicDatabase

    db = MusicDatabase(str(tmp_path / 'd.db'))
    conn = db._get_connection()
    for i in range(12):
        for pid, year in ((1, '1985-01-01'), (2, '1995-01-01')):
            conn.execute(
                "INSERT INTO discovery_pool (spotify_track_id, track_name, artist_name, album_name, "
                "source, release_date, profile_id, track_data_json) VALUES (?,?,?,?,?,?,?,'{}')",
                (f'{pid}-{i}', f't{i}', 'A', 'X', 'spotify', year, pid))
    conn.commit()
    conn.close()
    svc = PersonalizedPlaylistsService(db)
    assert svc.available_decades(source='spotify') == [1980]
    token = set_background_profile(2)
    try:
        assert svc.available_decades(source='spotify') == [1990]
    finally:
        reset_background_profile(token)
