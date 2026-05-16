"""Boundary tests for the personalized-playlists foundation
(``core.personalized.types`` + ``core.personalized.specs`` +
``core.personalized.manager``).

Pin every shape the storage layer + lifecycle has to handle so the
generators that arrive in subsequent commits can rely on a stable
contract: ensure_playlist auto-creates from default config, refresh
atomically replaces the snapshot + appends history, generator
exceptions don't lose the previous good snapshot, config patches
preserve unsent fields, recent_track_ids honors the day window,
list_playlists orders newest-first."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Any, List
from unittest.mock import MagicMock

import pytest

from core.personalized.manager import PersonalizedPlaylistManager
from core.personalized.specs import PlaylistKindRegistry, PlaylistKindSpec
from core.personalized.types import PlaylistConfig, Track
from database.personalized_schema import ensure_personalized_schema


# ─── shared fixtures ─────────────────────────────────────────────────


class _FakeDB:
    """Minimal MusicDatabase stand-in — gives the manager a real
    sqlite connection so the manager exercises actual SQL."""

    def __init__(self, path: str):
        self.path = path

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / 'test.db')
    conn = sqlite3.connect(p)
    ensure_personalized_schema(conn)
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def db(db_path):
    return _FakeDB(db_path)


@pytest.fixture
def registry():
    r = PlaylistKindRegistry()
    return r


def _make_track(name='T1', artist='A1', sid='spot-1', source='spotify') -> Track:
    return Track(
        track_name=name, artist_name=artist, album_name='Album',
        spotify_track_id=sid, source=source,
        duration_ms=200000, popularity=50,
    )


# ─── PlaylistConfig ──────────────────────────────────────────────────


class TestPlaylistConfig:
    def test_default_values(self):
        c = PlaylistConfig()
        assert c.limit == 50
        assert c.max_per_album == 2
        assert c.max_per_artist == 3
        assert c.popularity_min is None
        assert c.popularity_max is None
        assert c.exclude_recent_days == 0
        assert c.recency_days is None
        assert c.seed is None
        assert c.extra == {}

    def test_round_trip_through_json_dict(self):
        c = PlaylistConfig(
            limit=100, max_per_album=5, max_per_artist=10,
            popularity_min=20, popularity_max=80,
            exclude_recent_days=14, recency_days=180,
            seed=42, extra={'selected_seasons': ['halloween', 'christmas']},
        )
        d = c.to_json_dict()
        c2 = PlaylistConfig.from_json_dict(d)
        assert c2 == c

    def test_from_json_dict_handles_none(self):
        c = PlaylistConfig.from_json_dict(None)
        assert c == PlaylistConfig()

    def test_from_json_dict_handles_non_dict(self):
        c = PlaylistConfig.from_json_dict('garbage')  # type: ignore
        assert c == PlaylistConfig()

    def test_from_json_dict_missing_fields_use_defaults(self):
        c = PlaylistConfig.from_json_dict({'limit': 75})
        assert c.limit == 75
        assert c.max_per_album == 2  # default

    def test_merged_overrides_only_named_fields(self):
        base = PlaylistConfig(limit=50, popularity_min=20)
        out = base.merged({'limit': 100})
        assert out.limit == 100
        assert out.popularity_min == 20  # untouched

    def test_merged_extra_dict_is_deep_merged(self):
        base = PlaylistConfig(extra={'a': 1, 'b': 2})
        out = base.merged({'extra': {'b': 99, 'c': 3}})
        assert out.extra == {'a': 1, 'b': 99, 'c': 3}

    def test_merged_ignores_unknown_keys(self):
        base = PlaylistConfig()
        out = base.merged({'unknown_field': 'foo'})
        assert out == base


# ─── Track ────────────────────────────────────────────────────────────


class TestTrack:
    def test_from_dict_legacy_shape(self):
        d = {
            'track_name': 'Song', 'artist_name': 'Band',
            'album_name': 'Album', 'spotify_track_id': 'spot-1',
            'duration_ms': 200000, 'popularity': 60,
            '_artist_genres_raw': '["rock"]',  # ignored extra
        }
        t = Track.from_dict(d)
        assert t.track_name == 'Song'
        assert t.spotify_track_id == 'spot-1'
        assert t.duration_ms == 200000

    def test_primary_id_prefers_spotify(self):
        t = Track(
            track_name='', artist_name='',
            spotify_track_id='spot', itunes_track_id='itu', deezer_track_id='dee',
        )
        assert t.primary_id() == 'spot'

    def test_primary_id_falls_back_through_sources(self):
        t = Track(track_name='', artist_name='', itunes_track_id='itu')
        assert t.primary_id() == 'itu'
        t2 = Track(track_name='', artist_name='', deezer_track_id='dee')
        assert t2.primary_id() == 'dee'

    def test_primary_id_none_when_no_sources(self):
        t = Track(track_name='', artist_name='')
        assert t.primary_id() is None


# ─── PlaylistKindRegistry ────────────────────────────────────────────


class TestRegistry:
    def test_register_and_get(self, registry):
        spec = PlaylistKindSpec(
            kind='hidden_gems', name_template='Hidden Gems',
            description='', default_config=PlaylistConfig(),
            generator=lambda *a, **k: [],
        )
        registry.register(spec)
        assert registry.get('hidden_gems') is spec
        assert registry.get('nonexistent') is None

    def test_duplicate_registration_raises(self, registry):
        spec = PlaylistKindSpec(
            kind='x', name_template='X', description='',
            default_config=PlaylistConfig(), generator=lambda *a, **k: [],
        )
        registry.register(spec)
        with pytest.raises(ValueError, match='already registered'):
            registry.register(spec)

    def test_display_name_singleton(self):
        spec = PlaylistKindSpec(
            kind='x', name_template='Hidden Gems', description='',
            default_config=PlaylistConfig(), generator=lambda *a, **k: [],
        )
        assert spec.display_name('') == 'Hidden Gems'

    def test_display_name_with_variant(self):
        spec = PlaylistKindSpec(
            kind='x', name_template='Time Machine — {variant}',
            description='', default_config=PlaylistConfig(),
            generator=lambda *a, **k: [],
        )
        assert spec.display_name('1980s') == 'Time Machine — 1980s'

    def test_kinds_listing(self, registry):
        for k in ('a', 'b', 'c'):
            registry.register(PlaylistKindSpec(
                kind=k, name_template=k, description='',
                default_config=PlaylistConfig(), generator=lambda *a, **k: [],
            ))
        assert set(registry.kinds()) == {'a', 'b', 'c'}


# ─── PersonalizedPlaylistManager ─────────────────────────────────────


def _register_simple_kind(registry, generator, kind='hidden_gems', requires_variant=False):
    spec = PlaylistKindSpec(
        kind=kind, name_template=kind.replace('_', ' ').title(),
        description='', default_config=PlaylistConfig(limit=10),
        generator=generator, requires_variant=requires_variant,
    )
    registry.register(spec)
    return spec


class TestEnsurePlaylist:
    def test_creates_row_with_default_config(self, db, registry):
        _register_simple_kind(registry, lambda *a, **k: [])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        record = mgr.ensure_playlist('hidden_gems', '', 1)
        assert record.id > 0
        assert record.kind == 'hidden_gems'
        assert record.variant == ''
        assert record.profile_id == 1
        assert record.config.limit == 10  # from default
        assert record.track_count == 0
        assert record.last_generated_at is None

    def test_returns_same_row_on_second_call(self, db, registry):
        _register_simple_kind(registry, lambda *a, **k: [])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        r1 = mgr.ensure_playlist('hidden_gems', '', 1)
        r2 = mgr.ensure_playlist('hidden_gems', '', 1)
        assert r1.id == r2.id

    def test_variant_creates_separate_row(self, db, registry):
        _register_simple_kind(
            registry, lambda *a, **k: [], kind='time_machine', requires_variant=True,
        )
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        r1 = mgr.ensure_playlist('time_machine', '1980s', 1)
        r2 = mgr.ensure_playlist('time_machine', '1990s', 1)
        assert r1.id != r2.id

    def test_unknown_kind_raises(self, db, registry):
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        with pytest.raises(ValueError, match='Unknown playlist kind'):
            mgr.ensure_playlist('does_not_exist', '', 1)

    def test_required_variant_missing_raises(self, db, registry):
        _register_simple_kind(
            registry, lambda *a, **k: [], kind='time_machine', requires_variant=True,
        )
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        with pytest.raises(ValueError, match='requires a variant'):
            mgr.ensure_playlist('time_machine', '', 1)


class TestRefreshPlaylist:
    def test_refresh_persists_tracks(self, db, registry):
        tracks = [_make_track('S1', 'A1', 'sp1'), _make_track('S2', 'A1', 'sp2')]
        _register_simple_kind(registry, lambda deps, variant, config: tracks)
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        record = mgr.refresh_playlist('hidden_gems', '', 1)
        assert record.track_count == 2
        assert record.last_generated_at is not None
        assert record.last_generation_error is None

        persisted = mgr.get_playlist_tracks(record.id)
        assert len(persisted) == 2
        assert persisted[0].track_name == 'S1'
        assert persisted[1].track_name == 'S2'

    def test_refresh_replaces_previous_snapshot_atomically(self, db, registry):
        run = {'tracks': [_make_track('first')]}

        def gen(deps, variant, config):
            return run['tracks']

        _register_simple_kind(registry, gen)
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        r1 = mgr.refresh_playlist('hidden_gems', '', 1)
        assert r1.track_count == 1

        run['tracks'] = [_make_track('A'), _make_track('B'), _make_track('C')]
        r2 = mgr.refresh_playlist('hidden_gems', '', 1)
        assert r2.id == r1.id
        assert r2.track_count == 3

        persisted = mgr.get_playlist_tracks(r2.id)
        assert [t.track_name for t in persisted] == ['A', 'B', 'C']

    def test_generator_exception_preserves_previous_snapshot(self, db, registry):
        run = {'mode': 'success'}

        def gen(deps, variant, config):
            if run['mode'] == 'fail':
                raise RuntimeError('generator boom')
            return [_make_track('first')]

        _register_simple_kind(registry, gen)
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        r1 = mgr.refresh_playlist('hidden_gems', '', 1)
        assert r1.track_count == 1

        run['mode'] = 'fail'
        r2 = mgr.refresh_playlist('hidden_gems', '', 1)
        # Previous snapshot preserved.
        assert r2.track_count == 1
        # Error stamped on row.
        assert r2.last_generation_error is not None
        assert 'generator boom' in r2.last_generation_error
        # Tracks still queryable.
        persisted = mgr.get_playlist_tracks(r2.id)
        assert len(persisted) == 1

    def test_config_overrides_passed_to_generator(self, db, registry):
        captured = {}

        def gen(deps, variant, config):
            captured['limit'] = config.limit
            return []

        _register_simple_kind(registry, gen)
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        mgr.refresh_playlist('hidden_gems', '', 1, config_overrides={'limit': 200})
        assert captured['limit'] == 200

    def test_refresh_records_source_from_first_track(self, db, registry):
        tracks = [_make_track(source='spotify'), _make_track(source='deezer')]
        _register_simple_kind(registry, lambda *a, **k: tracks)
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        record = mgr.refresh_playlist('hidden_gems', '', 1)
        assert record.last_generation_source == 'spotify'

    def test_track_data_json_round_trips(self, db, registry):
        nested = {'id': 'spot-1', 'name': 'Foo', 'artists': [{'name': 'Bar'}]}
        track = Track(
            track_name='Foo', artist_name='Bar',
            spotify_track_id='spot-1', track_data_json=nested,
        )
        _register_simple_kind(registry, lambda *a, **k: [track])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        record = mgr.refresh_playlist('hidden_gems', '', 1)
        persisted = mgr.get_playlist_tracks(record.id)
        assert persisted[0].track_data_json == nested


class TestUpdateConfig:
    def test_patch_merges_with_stored(self, db, registry):
        _register_simple_kind(registry, lambda *a, **k: [])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        mgr.ensure_playlist('hidden_gems', '', 1)
        record = mgr.update_config('hidden_gems', '', 1, {'limit': 75})
        assert record.config.limit == 75
        # Other fields kept.
        assert record.config.max_per_album == 2

    def test_patch_extra_dict_deep_merges(self, db, registry):
        _register_simple_kind(registry, lambda *a, **k: [])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        mgr.ensure_playlist('hidden_gems', '', 1)
        mgr.update_config('hidden_gems', '', 1, {'extra': {'a': 1}})
        record = mgr.update_config('hidden_gems', '', 1, {'extra': {'b': 2}})
        assert record.config.extra == {'a': 1, 'b': 2}


class TestListPlaylists:
    def test_lists_all_playlists_for_profile(self, db, registry):
        _register_simple_kind(registry, lambda *a, **k: [], kind='hidden_gems')
        _register_simple_kind(registry, lambda *a, **k: [], kind='popular_picks')
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        mgr.ensure_playlist('hidden_gems', '', 1)
        mgr.ensure_playlist('popular_picks', '', 1)
        records = mgr.list_playlists(1)
        kinds = {r.kind for r in records}
        assert kinds == {'hidden_gems', 'popular_picks'}

    def test_does_not_list_other_profiles(self, db, registry):
        _register_simple_kind(registry, lambda *a, **k: [])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        mgr.ensure_playlist('hidden_gems', '', 1)
        mgr.ensure_playlist('hidden_gems', '', 2)
        assert len(mgr.list_playlists(1)) == 1
        assert len(mgr.list_playlists(2)) == 1


class TestStalenessFilter:
    """`config.exclude_recent_days > 0` drops tracks served by this
    kind for this profile in the last N days."""

    def test_zero_days_means_no_filter(self, db, registry):
        # Default config has exclude_recent_days=0; everything passes.
        tracks = [_make_track(sid='spot-1'), _make_track(sid='spot-2')]
        run = {'tracks': tracks}
        _register_simple_kind(registry, lambda *a, **k: run['tracks'])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        r1 = mgr.refresh_playlist('hidden_gems', '', 1)
        # Refresh again with same tracks — no filter, all should persist.
        r2 = mgr.refresh_playlist('hidden_gems', '', 1)
        assert r2.track_count == 2

    def test_positive_days_filters_recently_served(self, db, registry):
        run = {'tracks': [_make_track(sid='spot-1'), _make_track(sid='spot-2')]}
        _register_simple_kind(registry, lambda *a, **k: run['tracks'])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        r1 = mgr.refresh_playlist('hidden_gems', '', 1)
        assert r1.track_count == 2
        # Update config to exclude tracks served in last 7 days.
        mgr.update_config('hidden_gems', '', 1, {'exclude_recent_days': 7})
        # Same generator output now → all tracks just got served, all filtered out.
        r2 = mgr.refresh_playlist('hidden_gems', '', 1)
        assert r2.track_count == 0

    def test_filter_preserves_non_recent_tracks(self, db, registry):
        run = {'tracks': [_make_track(sid='spot-1')]}
        _register_simple_kind(registry, lambda *a, **k: run['tracks'])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        r1 = mgr.refresh_playlist('hidden_gems', '', 1)
        mgr.update_config('hidden_gems', '', 1, {'exclude_recent_days': 7})
        # New generator output with a NEW id — should pass.
        run['tracks'] = [_make_track(sid='spot-1'), _make_track(sid='spot-NEW')]
        r2 = mgr.refresh_playlist('hidden_gems', '', 1)
        # spot-1 was just served, dropped. spot-NEW is fresh, kept.
        assert r2.track_count == 1
        persisted = mgr.get_playlist_tracks(r2.id)
        assert persisted[0].spotify_track_id == 'spot-NEW'

    def test_tracks_without_primary_id_pass_through(self, db, registry):
        # Track with no source IDs — primary_id() is None — staleness
        # filter has nothing to dedupe on, so the track passes.
        track_no_id = Track(track_name='X', artist_name='Y', source='spotify')
        run = {'tracks': [track_no_id]}
        _register_simple_kind(registry, lambda *a, **k: run['tracks'])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        mgr.refresh_playlist('hidden_gems', '', 1)
        mgr.update_config('hidden_gems', '', 1, {'exclude_recent_days': 7})
        r2 = mgr.refresh_playlist('hidden_gems', '', 1)
        # Track is kept because there's no id to match against history.
        assert r2.track_count == 1


class TestStalenessHistory:
    def test_recent_track_ids_returns_zero_when_days_zero(self, db, registry):
        _register_simple_kind(registry, lambda *a, **k: [_make_track(sid='spot-1')])
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        mgr.refresh_playlist('hidden_gems', '', 1)
        assert mgr.recent_track_ids(1, 'hidden_gems', 0) == []

    def test_recent_track_ids_after_refresh(self, db, registry):
        _register_simple_kind(
            registry,
            lambda *a, **k: [_make_track(sid='spot-1'), _make_track(sid='spot-2')],
        )
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        mgr.refresh_playlist('hidden_gems', '', 1)
        recent = mgr.recent_track_ids(1, 'hidden_gems', 7)
        assert set(recent) == {'spot-1', 'spot-2'}

    def test_recent_track_ids_scoped_to_kind(self, db, registry):
        _register_simple_kind(registry, lambda *a, **k: [_make_track(sid='gem-1')], kind='hidden_gems')
        _register_simple_kind(registry, lambda *a, **k: [_make_track(sid='pop-1')], kind='popular_picks')
        mgr = PersonalizedPlaylistManager(db, deps=None, registry=registry)
        mgr.refresh_playlist('hidden_gems', '', 1)
        mgr.refresh_playlist('popular_picks', '', 1)
        assert mgr.recent_track_ids(1, 'hidden_gems', 7) == ['gem-1']
        assert mgr.recent_track_ids(1, 'popular_picks', 7) == ['pop-1']
