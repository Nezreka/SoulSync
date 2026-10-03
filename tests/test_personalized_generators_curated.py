"""Boundary tests for the curated / hybrid personalized generators
(`daily_mix`, `fresh_tape`, `archives`, `seasonal_mix`)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from core.personalized.generators import archives as _arch_mod
from core.personalized.generators import daily_mix as _dm_mod
from core.personalized.generators import fresh_tape as _ft_mod
from core.personalized.generators import seasonal_mix as _sm_mod
from core.personalized.specs import get_registry
from core.personalized.types import PlaylistConfig


# ─── daily_mix ───────────────────────────────────────────────────────
# Daily Mix N on auto-sync is Daily Mix N on Discover. it used to be a separate
# genre generator, so the two showed different playlists under one name.


def _discover_payload(n_mixes=5):
    mixes = []
    for i in range(1, n_mixes + 1):
        mixes.append({
            'key': f'daily_mix_{i}', 'name': f'Daily Mix {i}',
            'tracks': [
                {'name': f'Owned {i}', 'artists': [{'name': 'Katy Perry'}],
                 'album': {'name': 'One Of The Boys', 'images': [{'url': '/api/image-cache/x'}]},
                 'duration_ms': 179640, 'play_count': 1, 'owned': True},
                {'id': f'dz{i}', 'source': 'deezer', 'name': f'Pick {i}',
                 'artists': [{'name': 'M83'}], 'album': {'name': 'Hurry Up'},
                 'duration_ms': 200000, 'owned': False},
            ],
        })
    return {'mixes': mixes}


@pytest.fixture
def discover_mixes(monkeypatch):
    import core.personalized.daily_mixes as dmx
    calls = []
    state = {'payload': _discover_payload()}

    def _get(database, profile_id=1, **kw):
        calls.append((database, profile_id))
        return state['payload']
    monkeypatch.setattr(dmx, 'get_or_build_daily_mixes', _get)
    return SimpleNamespace(calls=calls, state=state)


def _dm_deps(profile_id=4):
    return SimpleNamespace(database='db', get_current_profile_id=lambda: profile_id)


class TestDailyMix:
    def test_registered(self):
        spec = get_registry().get('daily_mix')
        assert spec is not None
        assert spec.requires_variant is True

    def test_variants_follow_the_discover_mixes(self, discover_mixes):
        spec = get_registry().get('daily_mix')
        assert spec.variant_resolver(_dm_deps()) == ['1', '2', '3', '4', '5']

    def test_variants_default_to_four_before_discover_has_built(self, discover_mixes):
        discover_mixes.state['payload'] = {'mixes': []}
        assert get_registry().get('daily_mix').variant_resolver(_dm_deps()) == ['1', '2', '3', '4']

    def test_mix_n_is_discovers_mix_n_for_this_profile(self, discover_mixes):
        out = _dm_mod.generate(_dm_deps(profile_id=4), '3', PlaylistConfig(limit=10))
        assert discover_mixes.calls == [('db', 4)]
        assert [t.track_name for t in out] == ['Owned 3', 'Pick 3']
        assert out[0].artist_name == 'Katy Perry'
        assert out[0].album_name == 'One Of The Boys'
        assert out[0].album_cover_url == '/api/image-cache/x'

    def test_owned_tracks_reach_sync_as_title_artist(self, discover_mixes):
        from core.automation.handlers.personalized_pipeline import _track_to_sync_shape
        owned, pick = _dm_mod.generate(_dm_deps(), '1', PlaylistConfig(limit=10))
        shaped = _track_to_sync_shape(owned)
        assert shaped['name'] == 'Owned 1'
        assert shaped['artists'] == [{'name': 'Katy Perry'}]
        assert shaped['id'] == ''          # sync skips its id caches, matches by title + artist
        assert 'owned' not in shaped
        # a discovery pick keeps its real id + source for sync and the wishlist
        picked = _track_to_sync_shape(pick)
        assert picked['id'] == 'dz1'
        assert pick.source == 'deezer'

    def test_a_mix_discover_did_not_build_today_is_empty(self, discover_mixes):
        discover_mixes.state['payload'] = _discover_payload(2)
        assert _dm_mod.generate(_dm_deps(), '4', PlaylistConfig()) == []

    def test_limit_applies(self, discover_mixes):
        assert len(_dm_mod.generate(_dm_deps(), '1', PlaylistConfig(limit=1))) == 1

    def test_invalid_variant_raises(self, discover_mixes):
        with pytest.raises(ValueError, match='must be a mix number'):
            _dm_mod.generate(_dm_deps(), 'abc', PlaylistConfig())


# ─── fresh_tape / archives shared shape ─────────────────────────────


class _StubPoolTrack:
    def __init__(self, sid, name='T', artist='A', source='spotify'):
        self.spotify_track_id = sid
        self.itunes_track_id = None
        self.deezer_track_id = None
        self.track_name = name
        self.artist_name = artist
        self.album_name = 'Album'
        self.album_cover_url = None
        self.duration_ms = 200000
        self.popularity = 50
        self.track_data_json = None
        self.source = source


class _CuratedDB:
    def __init__(self, curated_ids=None, pool_tracks=None):
        self.curated_ids = curated_ids or []
        self.pool_tracks = pool_tracks or []
        self.requested_keys: List[str] = []

    def get_curated_playlist(self, key, profile_id=1):
        self.requested_keys.append(key)
        return list(self.curated_ids)

    def get_discovery_pool_tracks(self, **kwargs):
        return list(self.pool_tracks)


def _curated_deps(db):
    return SimpleNamespace(
        database=db,
        get_current_profile_id=lambda: 1,
        get_active_discovery_source=lambda: 'spotify',
    )


class TestFreshTape:
    def test_registered(self):
        spec = get_registry().get('fresh_tape')
        assert spec is not None
        assert spec.requires_variant is False
        assert spec.display_name('') == 'Fresh Tape'

    def test_returns_empty_when_no_curated_ids(self):
        db = _CuratedDB(curated_ids=[])
        out = _ft_mod.generate(_curated_deps(db), '', PlaylistConfig())
        assert out == []

    def test_hydrates_curated_ids_from_pool(self):
        db = _CuratedDB(
            curated_ids=['sp-1', 'sp-2', 'sp-missing'],
            pool_tracks=[
                _StubPoolTrack('sp-1', name='Song1', artist='Artist1'),
                _StubPoolTrack('sp-2', name='Song2', artist='Artist2'),
            ],
        )
        out = _ft_mod.generate(_curated_deps(db), '', PlaylistConfig())
        # Missing IDs silently skipped; order preserved.
        assert [t.track_name for t in out] == ['Song1', 'Song2']

    def test_tries_source_specific_then_fallback_key(self):
        # First lookup (source-specific) returns []; second (generic) returns IDs.
        class _DB:
            def __init__(self):
                self.calls = []
                self.responses = {
                    'release_radar_spotify': [],
                    'release_radar': ['sp-1'],
                }

            def get_curated_playlist(self, key, profile_id=1):
                self.calls.append(key)
                return self.responses.get(key, [])

            def get_discovery_pool_tracks(self, **kw):
                return [_StubPoolTrack('sp-1', name='Hit')]

        db = _DB()
        out = _ft_mod.generate(_curated_deps(db), '', PlaylistConfig())
        assert db.calls == ['release_radar_spotify', 'release_radar']
        assert len(out) == 1

    def test_respects_limit(self):
        db = _CuratedDB(
            curated_ids=[f'sp-{i}' for i in range(20)],
            pool_tracks=[_StubPoolTrack(f'sp-{i}', name=f'T{i}') for i in range(20)],
        )
        out = _ft_mod.generate(_curated_deps(db), '', PlaylistConfig(limit=5))
        assert len(out) == 5

    def test_missing_database_dep_raises(self):
        with pytest.raises(RuntimeError, match='missing `database`'):
            _ft_mod.generate(SimpleNamespace(), '', PlaylistConfig())


class TestArchives:
    def test_registered(self):
        spec = get_registry().get('archives')
        assert spec is not None
        assert spec.display_name('') == 'The Archives'

    def test_uses_discovery_weekly_curated_key(self):
        db = _CuratedDB(
            curated_ids=['sp-1'],
            pool_tracks=[_StubPoolTrack('sp-1', name='Discover')],
        )
        _arch_mod.generate(_curated_deps(db), '', PlaylistConfig())
        # Source-specific request fires first; fallback only fires
        # when source-specific returns empty. Stub returns IDs on
        # every call, so only the first key gets queried.
        assert db.requested_keys[0] == 'discovery_weekly_spotify'


# ─── seasonal_mix ───────────────────────────────────────────────────


class _SeasonalService:
    def __init__(self, track_ids):
        self.track_ids = track_ids

    def get_curated_seasonal_playlist(self, season_key, source=None):
        return list(self.track_ids)


@pytest.fixture
def seasonal_db(tmp_path):
    """Real sqlite DB with seasonal_tracks rows for hydration."""
    p = str(tmp_path / 'seasonal.db')
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE seasonal_tracks (
            id INTEGER PRIMARY KEY,
            season_key TEXT, source TEXT,
            spotify_track_id TEXT, track_name TEXT, artist_name TEXT,
            album_name TEXT, album_cover_url TEXT,
            duration_ms INTEGER, popularity INTEGER, track_data_json TEXT
        )
    """)
    seed = [
        ('halloween', 'spotify', 'sp-1', 'Spooky', 'Ghost Band', 'Album1', None, 200000, 80, '{"id":"sp-1"}'),
        ('halloween', 'spotify', 'sp-2', 'Haunted', 'Ghost Band', 'Album2', None, 210000, 70, None),
        ('halloween', 'spotify', 'sp-extra', 'Extra', 'Other', 'Album3', None, 200000, 60, None),
    ]
    cursor.executemany("""
        INSERT INTO seasonal_tracks
            (season_key, source, spotify_track_id, track_name, artist_name,
             album_name, album_cover_url, duration_ms, popularity, track_data_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, seed)
    conn.commit()
    conn.close()

    class _DB:
        def __init__(self, path): self.path = path
        def _get_connection(self):
            c = sqlite3.connect(self.path)
            c.row_factory = sqlite3.Row
            return c
    return _DB(p)


class TestSeasonalMix:
    def test_registered(self):
        spec = get_registry().get('seasonal_mix')
        assert spec is not None
        assert spec.requires_variant is True

    def test_variant_resolver_returns_seasons(self):
        spec = get_registry().get('seasonal_mix')
        seasons = spec.variant_resolver(None)
        assert 'halloween' in seasons
        assert 'christmas' in seasons

    def test_no_variant_raises(self, seasonal_db):
        deps = SimpleNamespace(
            seasonal_service=_SeasonalService(['sp-1']),
            database=seasonal_db,
            get_active_discovery_source=lambda: 'spotify',
        )
        with pytest.raises(ValueError, match='requires a season variant'):
            _sm_mod.generate(deps, '', PlaylistConfig())

    def test_hydrates_curated_ids_in_order(self, seasonal_db):
        deps = SimpleNamespace(
            seasonal_service=_SeasonalService(['sp-2', 'sp-1']),
            database=seasonal_db,
            get_active_discovery_source=lambda: 'spotify',
        )
        out = _sm_mod.generate(deps, 'halloween', PlaylistConfig())
        assert [t.track_name for t in out] == ['Haunted', 'Spooky']

    def test_missing_track_id_silently_skipped(self, seasonal_db):
        deps = SimpleNamespace(
            seasonal_service=_SeasonalService(['sp-1', 'sp-not-in-db']),
            database=seasonal_db,
            get_active_discovery_source=lambda: 'spotify',
        )
        out = _sm_mod.generate(deps, 'halloween', PlaylistConfig())
        assert len(out) == 1
        assert out[0].track_name == 'Spooky'

    def test_track_data_json_round_trips(self, seasonal_db):
        deps = SimpleNamespace(
            seasonal_service=_SeasonalService(['sp-1']),
            database=seasonal_db,
            get_active_discovery_source=lambda: 'spotify',
        )
        out = _sm_mod.generate(deps, 'halloween', PlaylistConfig())
        # sp-1 had JSON; sp-2 had None.
        assert out[0].track_data_json == {'id': 'sp-1'}

    def test_empty_curated_returns_empty(self, seasonal_db):
        deps = SimpleNamespace(
            seasonal_service=_SeasonalService([]),
            database=seasonal_db,
            get_active_discovery_source=lambda: 'spotify',
        )
        out = _sm_mod.generate(deps, 'halloween', PlaylistConfig())
        assert out == []

    def test_respects_limit(self, seasonal_db):
        deps = SimpleNamespace(
            seasonal_service=_SeasonalService(['sp-1', 'sp-2', 'sp-extra']),
            database=seasonal_db,
            get_active_discovery_source=lambda: 'spotify',
        )
        out = _sm_mod.generate(deps, 'halloween', PlaylistConfig(limit=2))
        assert len(out) == 2

    def test_missing_seasonal_service_raises(self):
        deps = SimpleNamespace(database=object())
        with pytest.raises(RuntimeError, match='missing `seasonal_service`'):
            _sm_mod.generate(deps, 'halloween', PlaylistConfig())
