"""Phase 3 WebSocket migration tests — Enrichment sidebar workers.

Verifies that:
 - All 7 enrichment worker statuses are delivered identically via
   WebSocket events and HTTP endpoints
 - Each worker's data shape is correct
 - HTTP endpoints still work as fallback

IMPORTANT: Do NOT use ``from tests.conftest import …`` — pytest's auto-discovered
conftest is a different module instance. Use the ``shared_state`` fixture instead.
"""

import pytest


# All 7 enrichment workers
WORKERS = [
    'musicbrainz', 'audiodb', 'deezer',
    'spotify-enrichment', 'itunes-enrichment',
    'hydrabase', 'repair',
]

# Endpoint URLs keyed by worker name
ENDPOINTS = {
    'musicbrainz': '/api/musicbrainz/status',
    'audiodb': '/api/audiodb/status',
    'deezer': '/api/deezer/status',
    'spotify-enrichment': '/api/spotify-enrichment/status',
    'itunes-enrichment': '/api/itunes-enrichment/status',
    'hydrabase': '/api/hydrabase-worker/status',
    'repair': '/api/repair/status',
}


# =========================================================================
# Group A — Event Delivery (parameterized)
# =========================================================================

class TestEnrichmentEventDelivery:
    """enrichment:<worker> socket events are received by the client."""

# =========================================================================
# Group B — Data Shape (parameterized)
# =========================================================================

class TestEnrichmentDataShape:
    """enrichment:<worker> event data has the expected keys."""

    @pytest.mark.parametrize('worker', [
        'musicbrainz', 'audiodb', 'deezer',
        'spotify-enrichment', 'itunes-enrichment',
    ])
    def test_standard_enrichment_shape(self, test_app, shared_state, worker):
        """Standard enrichment worker data has running, paused, idle, progress."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_enrichment_status']

        socketio.emit(f'enrichment:{worker}', build(worker))
        received = client.get_received()
        events = [e for e in received if e['name'] == f'enrichment:{worker}']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'running' in data
        assert 'paused' in data
        assert 'idle' in data
        assert 'current_item' in data
        assert 'progress' in data
        assert isinstance(data['running'], bool)
        assert isinstance(data['paused'], bool)

    def test_spotify_enrichment_has_authenticated(self, test_app, shared_state):
        """Spotify enrichment includes the 'authenticated' field."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_enrichment_status']

        socketio.emit('enrichment:spotify-enrichment', build('spotify-enrichment'))
        received = client.get_received()
        events = [e for e in received if e['name'] == 'enrichment:spotify-enrichment']
        data = events[0]['args'][0]
        assert 'authenticated' in data

    def test_hydrabase_shape(self, test_app, shared_state):
        """Hydrabase worker has running, paused, queue_size (no idle/progress)."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_enrichment_status']

        socketio.emit('enrichment:hydrabase', build('hydrabase'))
        received = client.get_received()
        events = [e for e in received if e['name'] == 'enrichment:hydrabase']
        data = events[0]['args'][0]

        assert 'running' in data
        assert 'paused' in data
        assert 'queue_size' in data
        assert 'idle' not in data  # Hydrabase doesn't have idle

    def test_repair_shape(self, test_app, shared_state):
        """Repair worker has progress.tracks with checked/repaired counters."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_enrichment_status']

        socketio.emit('enrichment:repair', build('repair'))
        received = client.get_received()
        events = [e for e in received if e['name'] == 'enrichment:repair']
        data = events[0]['args'][0]

        assert 'running' in data
        assert 'progress' in data
        tracks = data['progress']['tracks']
        assert 'checked' in tracks
        assert 'total' in tracks
        assert 'repaired' in tracks


# =========================================================================
# Group C — HTTP Parity (parameterized)
# =========================================================================

class TestEnrichmentHttpParity:
    """Socket event data matches HTTP endpoint response."""

    @pytest.mark.parametrize('worker', WORKERS)
    def test_enrichment_matches_http(self, test_app, shared_state, worker):
        """Socket event data matches GET /api/<worker>/status."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_enrichment_status']

        endpoint = ENDPOINTS[worker]
        http_data = flask_client.get(endpoint).get_json()

        socketio.emit(f'enrichment:{worker}', build(worker))
        received = ws_client.get_received()
        events = [e for e in received if e['name'] == f'enrichment:{worker}']
        assert len(events) >= 1
        ws_data = events[0]['args'][0]

        # Both should have the same running/paused state
        assert ws_data['running'] == http_data['running']
        assert ws_data['paused'] == http_data['paused']


# =========================================================================
# Group D — Backward Compatibility
# =========================================================================

class TestEnrichmentBackwardCompat:
    """WebSocket clients still receive broadcast enrichment updates."""

    def test_multiple_clients_get_enrichment_updates(self, test_app, shared_state):
        """Multiple WebSocket clients each receive enrichment events."""
        app, socketio = test_app
        client1 = socketio.test_client(app)
        client2 = socketio.test_client(app)
        build = shared_state['build_enrichment_status']

        socketio.emit('enrichment:musicbrainz', build('musicbrainz'))

        for client in [client1, client2]:
            received = client.get_received()
            events = [e for e in received if e['name'] == 'enrichment:musicbrainz']
            assert len(events) >= 1

        client1.disconnect()
        client2.disconnect()

    def test_enrichment_reflects_state_change(self, test_app, shared_state):
        """When enrichment state changes, the next emit reflects it."""
        app, socketio = test_app
        client = socketio.test_client(app)
        enrich = shared_state['enrichment_status']
        build = shared_state['build_enrichment_status']

        # Mutate state
        enrich['musicbrainz']['paused'] = True
        enrich['musicbrainz']['running'] = False

        socketio.emit('enrichment:musicbrainz', build('musicbrainz'))
        received = client.get_received()
        events = [e for e in received if e['name'] == 'enrichment:musicbrainz']
        data = events[-1]['args'][0]
        assert data['paused'] is True
        assert data['running'] is False
