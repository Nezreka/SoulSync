"""Tests for the enrichment service registry + generic Flask blueprint.

Covers the registry contract (registration / lookup / fallback status
shape) and the generic ``/api/enrichment/<service_id>/...`` routes,
including per-service quirks (rate-limit pre-resume guard, auto-pause
token cleanup, persisted-pause config keys, extra default fields).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from flask import Flask

from core.enrichment.api import configure as configure_api, create_blueprint
from core.enrichment.services import (
    EnrichmentService,
    all_service_ids,
    all_services,
    clear_registry,
    get_service,
    register_services,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeWorker:
    """Captures pause / resume calls + returns controllable get_stats."""

    def __init__(self, stats: Dict[str, Any] | None = None):
        self.stats = stats or {
            'enabled': True, 'running': True, 'paused': False,
            'current_item': None,
            'stats': {'matched': 5, 'not_found': 1, 'pending': 10, 'errors': 0},
            'progress': {},
        }
        self.pause_calls = 0
        self.resume_calls = 0
        self.pause_should_raise: Exception | None = None
        self.resume_should_raise: Exception | None = None

    def pause(self) -> None:
        if self.pause_should_raise:
            raise self.pause_should_raise
        self.pause_calls += 1

    def resume(self) -> None:
        if self.resume_should_raise:
            raise self.resume_should_raise
        self.resume_calls += 1

    def get_stats(self) -> Dict[str, Any]:
        return self.stats


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry():
    """Every test starts from a clean registry."""
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def host_state():
    """Host-side state collections (config, auto-pause set, yield-override set)."""
    state = {
        'config': {},
        'auto_paused': set(),
        'yield_override': set(),
    }
    configure_api(
        config_set=lambda k, v: state['config'].__setitem__(k, v),
        auto_paused_discard=lambda token: state['auto_paused'].discard(token),
        yield_override_add=lambda token: state['yield_override'].add(token),
    )
    yield state
    # Reset hooks so other tests run on a clean slate.
    configure_api(config_set=None, auto_paused_discard=None, yield_override_add=None)


@pytest.fixture
def app(host_state):
    """Flask app with the enrichment blueprint registered."""
    app = Flask(__name__)
    app.register_blueprint(create_blueprint())
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Registry behavior
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_register_and_lookup(self):
        worker = _FakeWorker()
        svc = EnrichmentService(
            id='example', display_name='Example', worker_getter=lambda: worker,
        )
        register_services([svc])
        assert get_service('example') is svc
        assert all_service_ids() == ['example']
        assert all_services() == [svc]

    def test_unknown_service_returns_none(self):
        register_services([])
        assert get_service('does_not_exist') is None

    def test_re_register_replaces(self):
        register_services([
            EnrichmentService(id='a', display_name='A', worker_getter=lambda: None),
        ])
        register_services([
            EnrichmentService(id='b', display_name='B', worker_getter=lambda: None),
        ])
        assert get_service('a') is None
        assert get_service('b') is not None

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError):
            register_services([
                EnrichmentService(id='', display_name='X', worker_getter=lambda: None),
            ])

    def test_worker_getter_exception_returns_none(self):
        def boom():
            raise RuntimeError("init failed")

        svc = EnrichmentService(id='broken', display_name='Broken', worker_getter=boom)
        register_services([svc])
        assert svc.get_worker() is None

    def test_fallback_status_default_shape(self):
        svc = EnrichmentService(id='x', display_name='X', worker_getter=lambda: None)
        fb = svc.fallback_status()
        assert fb['enabled'] is False
        assert fb['running'] is False
        assert fb['paused'] is False
        assert fb['current_item'] is None
        assert fb['stats'] == {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
        assert fb['progress'] == {}

    def test_fallback_status_extra_defaults_merged(self):
        """Tidal / Qobuz add ``'authenticated': False`` to the fallback."""
        svc = EnrichmentService(
            id='tidal', display_name='Tidal', worker_getter=lambda: None,
            extra_status_defaults={'authenticated': False},
        )
        fb = svc.fallback_status()
        assert fb['authenticated'] is False
        # And the standard keys still present.
        assert fb['enabled'] is False

    def test_fallback_status_does_not_share_stats_dict(self):
        svc = EnrichmentService(id='x', display_name='X', worker_getter=lambda: None)
        fb1 = svc.fallback_status()
        fb1['stats']['matched'] = 999
        fb2 = svc.fallback_status()
        assert fb2['stats']['matched'] == 0


# ---------------------------------------------------------------------------
# Status route
# ---------------------------------------------------------------------------


class TestStatusRoute:
    def test_returns_worker_stats_when_initialized(self, client):
        worker = _FakeWorker(stats={'enabled': True, 'matched': 42})
        register_services([
            EnrichmentService(id='spotify', display_name='Spotify', worker_getter=lambda: worker),
        ])
        resp = client.get('/api/enrichment/spotify/status')
        assert resp.status_code == 200
        assert resp.get_json() == {'enabled': True, 'matched': 42}

    def test_returns_fallback_when_worker_none(self, client):
        register_services([
            EnrichmentService(id='spotify', display_name='Spotify', worker_getter=lambda: None),
        ])
        resp = client.get('/api/enrichment/spotify/status')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['enabled'] is False
        assert body['stats'] == {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}

    def test_unknown_service_returns_404(self, client):
        register_services([])
        resp = client.get('/api/enrichment/no_such_service/status')
        assert resp.status_code == 404

    def test_get_stats_exception_returns_500(self, client):
        class BoomWorker:
            def get_stats(self):
                raise RuntimeError("db gone")

        register_services([
            EnrichmentService(id='x', display_name='X', worker_getter=lambda: BoomWorker()),
        ])
        resp = client.get('/api/enrichment/x/status')
        assert resp.status_code == 500
        assert 'db gone' in resp.get_json()['error']


# ---------------------------------------------------------------------------
# Pause route
# ---------------------------------------------------------------------------


class TestPauseRoute:
    def test_pause_calls_worker_and_persists_config(self, client, host_state):
        worker = _FakeWorker()
        register_services([
            EnrichmentService(
                id='itunes', display_name='iTunes', worker_getter=lambda: worker,
                config_paused_key='itunes_enrichment_paused',
            ),
        ])
        resp = client.post('/api/enrichment/itunes/pause')
        assert resp.status_code == 200
        assert resp.get_json() == {'status': 'paused'}
        assert worker.pause_calls == 1
        assert host_state['config']['itunes_enrichment_paused'] is True

    def test_pause_drops_auto_pause_token(self, client, host_state):
        worker = _FakeWorker()
        host_state['auto_paused'].add('lastfm-enrichment')
        register_services([
            EnrichmentService(
                id='lastfm', display_name='Last.fm', worker_getter=lambda: worker,
                config_paused_key='lastfm_enrichment_paused',
                auto_pause_token='lastfm-enrichment',
            ),
        ])
        resp = client.post('/api/enrichment/lastfm/pause')
        assert resp.status_code == 200
        assert 'lastfm-enrichment' not in host_state['auto_paused']

    def test_pause_without_config_key_skips_persistence(self, client, host_state):
        worker = _FakeWorker()
        register_services([
            EnrichmentService(
                id='hydra', display_name='Hydra', worker_getter=lambda: worker,
                config_paused_key='',  # No persistence
            ),
        ])
        resp = client.post('/api/enrichment/hydra/pause')
        assert resp.status_code == 200
        assert host_state['config'] == {}  # Nothing persisted

    def test_pause_when_worker_none_returns_400(self, client):
        register_services([
            EnrichmentService(id='x', display_name='X', worker_getter=lambda: None),
        ])
        resp = client.post('/api/enrichment/x/pause')
        assert resp.status_code == 400
        assert 'not initialized' in resp.get_json()['error']

    def test_pause_worker_exception_returns_500(self, client):
        worker = _FakeWorker()
        worker.pause_should_raise = RuntimeError("worker dead")
        register_services([
            EnrichmentService(id='x', display_name='X', worker_getter=lambda: worker),
        ])
        resp = client.post('/api/enrichment/x/pause')
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Resume route
# ---------------------------------------------------------------------------


class TestResumeRoute:
    def test_resume_calls_worker_persists_and_adds_yield_override(self, client, host_state):
        worker = _FakeWorker()
        register_services([
            EnrichmentService(
                id='spotify', display_name='Spotify', worker_getter=lambda: worker,
                config_paused_key='spotify_enrichment_paused',
                auto_pause_token='spotify-enrichment',
            ),
        ])
        resp = client.post('/api/enrichment/spotify/resume')
        assert resp.status_code == 200
        assert resp.get_json() == {'status': 'running'}
        assert worker.resume_calls == 1
        assert host_state['config']['spotify_enrichment_paused'] is False
        assert 'spotify-enrichment' in host_state['yield_override']

    def test_resume_blocked_by_pre_check_returns_429(self, client):
        """Spotify rate-limit guard: pre-check returns (429, message)."""
        worker = _FakeWorker()
        register_services([
            EnrichmentService(
                id='spotify', display_name='Spotify', worker_getter=lambda: worker,
                config_paused_key='spotify_enrichment_paused',
                pre_resume_check=lambda: (429, 'Cannot resume while Spotify is rate limited'),
            ),
        ])
        resp = client.post('/api/enrichment/spotify/resume')
        assert resp.status_code == 429
        body = resp.get_json()
        assert body['rate_limited'] is True
        assert 'rate limited' in body['error']
        assert worker.resume_calls == 0  # Worker not touched

    def test_resume_pre_check_returning_none_passes(self, client):
        worker = _FakeWorker()
        register_services([
            EnrichmentService(
                id='spotify', display_name='Spotify', worker_getter=lambda: worker,
                pre_resume_check=lambda: None,
            ),
        ])
        resp = client.post('/api/enrichment/spotify/resume')
        assert resp.status_code == 200
        assert worker.resume_calls == 1

    def test_resume_pre_check_exception_treated_as_pass(self, client):
        """A buggy pre-check shouldn't permanently lock out resume."""
        worker = _FakeWorker()

        def boom():
            raise RuntimeError("pre-check broke")

        register_services([
            EnrichmentService(
                id='spotify', display_name='Spotify', worker_getter=lambda: worker,
                pre_resume_check=boom,
            ),
        ])
        resp = client.post('/api/enrichment/spotify/resume')
        assert resp.status_code == 200
        assert worker.resume_calls == 1

    def test_resume_when_worker_none_returns_400(self, client):
        register_services([
            EnrichmentService(id='x', display_name='X', worker_getter=lambda: None),
        ])
        resp = client.post('/api/enrichment/x/resume')
        assert resp.status_code == 400

    def test_resume_worker_exception_returns_500(self, client):
        worker = _FakeWorker()
        worker.resume_should_raise = RuntimeError("worker dead")
        register_services([
            EnrichmentService(id='x', display_name='X', worker_getter=lambda: worker),
        ])
        resp = client.post('/api/enrichment/x/resume')
        assert resp.status_code == 500

    def test_resume_without_auto_pause_token_skips_yield_override(self, client, host_state):
        """Services without an auto_pause_token (e.g. iTunes, Deezer) should
        NOT add to yield_override — that's a Spotify/LastFM/Genius-only
        mechanism."""
        worker = _FakeWorker()
        register_services([
            EnrichmentService(
                id='itunes', display_name='iTunes', worker_getter=lambda: worker,
                config_paused_key='itunes_enrichment_paused',
                auto_pause_token=None,
            ),
        ])
        resp = client.post('/api/enrichment/itunes/resume')
        assert resp.status_code == 200
        assert host_state['yield_override'] == set()


# ---------------------------------------------------------------------------
# 404 path
# ---------------------------------------------------------------------------


class TestUnknownService:
    @pytest.mark.parametrize('verb,path', [
        ('get', '/api/enrichment/no_such/status'),
        ('post', '/api/enrichment/no_such/pause'),
        ('post', '/api/enrichment/no_such/resume'),
    ])
    def test_404_for_unknown_service(self, client, verb, path):
        register_services([])
        method = getattr(client, verb)
        resp = method(path)
        assert resp.status_code == 404
        assert 'no_such' in resp.get_json()['error']
