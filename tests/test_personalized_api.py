"""Boundary tests for `core.personalized.api` handler functions.

These are pure-function dispatchers — they take a manager + ids,
return a JSON-serializable dict. No Flask required, no real DB.
The Flask wiring in `web_server.py` adds `jsonify` + URL routing
on top.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any, List

import pytest

from core.personalized import api as _api
from core.personalized.manager import PersonalizedPlaylistManager
from core.personalized.specs import PlaylistKindRegistry, PlaylistKindSpec
from core.personalized.types import PlaylistConfig, Track
from database.personalized_schema import ensure_personalized_schema


class _FakeDB:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / 't.db')
    conn = sqlite3.connect(p)
    ensure_personalized_schema(conn)
    conn.commit()
    conn.close()
    return _FakeDB(p)


@pytest.fixture
def registry():
    return PlaylistKindRegistry()


@pytest.fixture
def manager(db, registry):
    return PersonalizedPlaylistManager(db, deps=None, registry=registry)


def _register(reg, kind='hidden_gems', requires_variant=False, generator=None):
    spec = PlaylistKindSpec(
        kind=kind, name_template=kind.replace('_', ' ').title(),
        description=f'Description for {kind}',
        default_config=PlaylistConfig(limit=20),
        generator=generator or (lambda *a, **k: []),
        requires_variant=requires_variant,
        tags=['test'],
    )
    reg.register(spec)
    return spec


# ─── list_kinds ──────────────────────────────────────────────────────


class TestListKinds:
    def test_lists_every_registered_kind(self, registry):
        _register(registry, kind='hidden_gems')
        _register(registry, kind='time_machine', requires_variant=True)
        out = _api.list_kinds(registry)
        assert out['success'] is True
        kinds = {k['kind'] for k in out['kinds']}
        assert kinds == {'hidden_gems', 'time_machine'}

    def test_kind_metadata_shape(self, registry):
        _register(registry, kind='hidden_gems')
        out = _api.list_kinds(registry)
        kind = out['kinds'][0]
        assert kind['kind'] == 'hidden_gems'
        assert kind['requires_variant'] is False
        assert kind['tags'] == ['test']
        assert kind['default_config']['limit'] == 20

    def test_empty_registry(self):
        out = _api.list_kinds(PlaylistKindRegistry())
        assert out == {'success': True, 'kinds': []}


# ─── list_playlists ─────────────────────────────────────────────────


class TestListPlaylists:
    def test_returns_empty_when_no_playlists(self, manager, registry):
        _register(registry)
        out = _api.list_playlists(manager, profile_id=1)
        assert out == {'success': True, 'playlists': []}

    def test_serializes_playlist_record(self, manager, registry):
        _register(registry)
        manager.ensure_playlist('hidden_gems', '', 1)
        out = _api.list_playlists(manager, profile_id=1)
        assert out['success'] is True
        assert len(out['playlists']) == 1
        pl = out['playlists'][0]
        assert pl['kind'] == 'hidden_gems'
        assert pl['variant'] == ''
        assert pl['name'] == 'Hidden Gems'
        assert pl['track_count'] == 0
        assert pl['config']['limit'] == 20


# ─── get_playlist_with_tracks ───────────────────────────────────────


class TestGetPlaylistWithTracks:
    def test_auto_creates_on_first_get(self, manager, registry):
        _register(registry)
        out = _api.get_playlist_with_tracks(manager, 'hidden_gems', '', 1)
        assert out['success'] is True
        assert out['playlist']['kind'] == 'hidden_gems'
        assert out['tracks'] == []

    def test_returns_persisted_tracks(self, manager, registry):
        gen_calls = []

        def gen(deps, variant, config):
            gen_calls.append(1)
            return [Track(track_name='X', artist_name='Y', spotify_track_id='sp-1')]

        _register(registry, generator=gen)
        manager.refresh_playlist('hidden_gems', '', 1)
        out = _api.get_playlist_with_tracks(manager, 'hidden_gems', '', 1)
        assert len(out['tracks']) == 1
        assert out['tracks'][0]['track_name'] == 'X'
        assert out['tracks'][0]['spotify_track_id'] == 'sp-1'

    def test_unknown_kind_raises_value_error(self, manager):
        with pytest.raises(ValueError):
            _api.get_playlist_with_tracks(manager, 'nope', '', 1)


# ─── refresh_playlist ───────────────────────────────────────────────


class TestRefreshPlaylist:
    def test_refresh_runs_generator_and_returns_tracks(self, manager, registry):
        _register(registry, generator=lambda *a, **k: [
            Track(track_name='T1', artist_name='A', spotify_track_id='1'),
            Track(track_name='T2', artist_name='B', spotify_track_id='2'),
        ])
        out = _api.refresh_playlist(manager, 'hidden_gems', '', 1)
        assert out['success'] is True
        assert len(out['tracks']) == 2
        assert out['playlist']['track_count'] == 2
        assert out['playlist']['last_generated_at'] is not None

    def test_config_overrides_passed_through(self, manager, registry):
        captured = {}

        def gen(deps, variant, config):
            captured['limit'] = config.limit
            return []

        _register(registry, generator=gen)
        _api.refresh_playlist(manager, 'hidden_gems', '', 1, config_overrides={'limit': 99})
        assert captured['limit'] == 99


# ─── update_config ──────────────────────────────────────────────────


class TestUpdateConfig:
    def test_patches_config(self, manager, registry):
        _register(registry)
        out = _api.update_config(manager, 'hidden_gems', '', 1, {'limit': 75})
        assert out['success'] is True
        assert out['playlist']['config']['limit'] == 75
