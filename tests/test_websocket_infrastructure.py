"""Phase 1 WebSocket migration tests.

Verifies that:
 - WebSocket infrastructure connects and communicates
 - HTTP endpoints still work (backward compat / fallback)
 - Socket events deliver identical data to HTTP responses
 - Download batch room subscriptions work correctly

IMPORTANT: Do NOT use ``from tests.conftest import …`` — pytest's auto-discovered
conftest is a different module instance. Use the ``shared_state`` fixture instead.
"""

import pytest


# =========================================================================
# Group A — Infrastructure
# =========================================================================

class TestInfrastructure:
    """Socket.IO connects, and HTTP endpoints remain functional."""

    def test_socketio_connects(self, socketio_client):
        """Client can establish a WebSocket connection."""
        assert socketio_client.is_connected()

    def test_socketio_disconnect_and_reconnect(self, test_app):
        """Client can disconnect and reconnect cleanly."""
        app, socketio = test_app
        client = socketio.test_client(app)
        assert client.is_connected()
        client.disconnect()
        assert not client.is_connected()
        client.connect()
        assert client.is_connected()
        client.disconnect()

# =========================================================================
# Group B — Service Status Parity
# =========================================================================

class TestServiceStatus:
    """status:update socket events match GET /status HTTP responses."""

    def test_status_update_shape(self, test_app, shared_state):
        """status:update event data has the expected keys."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_status_payload']
        socketio.emit('status:update', build())
        received = client.get_received()
        status_events = [e for e in received if e['name'] == 'status:update']
        assert len(status_events) >= 1
        data = status_events[0]['args'][0]
        assert 'metadata_source' in data
        assert 'media_server' in data
        assert 'soulseek' in data
        assert 'active_media_server' in data
        assert 'authenticated' in data['metadata_source']

    def test_status_matches_http(self, test_app, shared_state):
        """Socket event data matches HTTP endpoint response exactly."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_status_payload']

        http_data = flask_client.get('/status').get_json()

        socketio.emit('status:update', build())
        received = ws_client.get_received()
        status_events = [e for e in received if e['name'] == 'status:update']
        assert len(status_events) >= 1
        ws_data = status_events[0]['args'][0]

        assert ws_data['metadata_source'] == http_data['metadata_source']
        assert ws_data['media_server'] == http_data['media_server']
        assert ws_data['soulseek'] == http_data['soulseek']
        assert ws_data['active_media_server'] == http_data['active_media_server']

    def test_status_reflects_cache_changes(self, test_app, shared_state):
        """When _status_cache changes, the next emit reflects it."""
        app, socketio = test_app
        client = socketio.test_client(app)
        status_cache = shared_state['status_cache']
        build = shared_state['build_status_payload']

        # Mutate cache
        status_cache['metadata_source']['source'] = 'itunes'

        socketio.emit('status:update', build())
        received = client.get_received()
        status_events = [e for e in received if e['name'] == 'status:update']
        data = status_events[-1]['args'][0]
        assert data['metadata_source']['source'] == 'itunes'


# =========================================================================
# Group C — Watchlist Count Parity
# =========================================================================

class TestWatchlistCount:
    """watchlist:count socket events match GET /api/watchlist/count."""

    def test_watchlist_count_shape(self, test_app, shared_state):
        """watchlist:count event data has expected keys."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_watchlist_count_payload']
        socketio.emit('watchlist:count', build())
        received = client.get_received()
        wl_events = [e for e in received if e['name'] == 'watchlist:count']
        data = wl_events[0]['args'][0]
        assert data['success'] is True
        assert isinstance(data['count'], int)
        assert isinstance(data['next_run_in_seconds'], int)

    def test_watchlist_matches_http(self, test_app, shared_state):
        """Socket event data matches HTTP endpoint response."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_watchlist_count_payload']

        http_data = flask_client.get('/api/watchlist/count').get_json()

        socketio.emit('watchlist:count', build())
        received = ws_client.get_received()
        wl_events = [e for e in received if e['name'] == 'watchlist:count']
        ws_data = wl_events[0]['args'][0]

        assert ws_data['success'] == http_data['success']
        assert ws_data['count'] == http_data['count']
        assert ws_data['next_run_in_seconds'] == http_data['next_run_in_seconds']

    def test_watchlist_reflects_count_change(self, test_app, shared_state):
        """When watchlist count changes, the emit reflects it."""
        app, socketio = test_app
        client = socketio.test_client(app)
        wl_state = shared_state['watchlist_state']
        build = shared_state['build_watchlist_count_payload']

        wl_state['count'] = 42

        socketio.emit('watchlist:count', build())
        received = client.get_received()
        wl_events = [e for e in received if e['name'] == 'watchlist:count']
        data = wl_events[-1]['args'][0]
        assert data['count'] == 42


# =========================================================================
# Group D — Download Batch Rooms
# =========================================================================

class TestDownloadBatch:
    """Download batch updates are delivered via room subscriptions."""

    def _add_batch(self, shared_state, batch_id, **kwargs):
        """Helper to add a fake download batch."""
        defaults = {
            'phase': 'downloading',
            'tasks': [
                {'task_id': 't1', 'status': 'downloading', 'progress': 50},
                {'task_id': 't2', 'status': 'searching', 'progress': 0},
            ],
            'active_count': 2,
            'max_concurrent': 3,
            'playlist_id': 'spotify_test',
            'playlist_name': 'Test Playlist',
        }
        defaults.update(kwargs)
        batches = shared_state['download_batches']
        lock = shared_state['tasks_lock']
        with lock:
            batches[batch_id] = defaults

    def test_download_receives_updates(self, test_app, shared_state):
        """After subscribing, client receives batch_update for that batch."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build_batch = shared_state['build_batch_status_data']

        self._add_batch(shared_state, 'batch_123')
        client.emit('downloads:subscribe', {'batch_ids': ['batch_123']})
        client.get_received()  # clear

        batches = shared_state['download_batches']
        lock = shared_state['tasks_lock']
        with lock:
            batch = batches['batch_123']
            socketio.emit('downloads:batch_update', {
                'batch_id': 'batch_123',
                'data': build_batch('batch_123', batch),
            }, room='batch:batch_123')

        received = client.get_received()
        dl_events = [e for e in received if e['name'] == 'downloads:batch_update']
        assert len(dl_events) >= 1
        payload = dl_events[0]['args'][0]
        assert payload['batch_id'] == 'batch_123'
        assert payload['data']['phase'] == 'downloading'
        assert len(payload['data']['tasks']) == 2

    def test_download_only_subscribed_batches(self, test_app, shared_state):
        """Client only receives updates for subscribed batches, not others."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build_batch = shared_state['build_batch_status_data']

        self._add_batch(shared_state, 'batch_A')
        self._add_batch(shared_state, 'batch_B')

        client.emit('downloads:subscribe', {'batch_ids': ['batch_A']})
        client.get_received()  # clear

        batches = shared_state['download_batches']
        lock = shared_state['tasks_lock']
        with lock:
            for bid in ['batch_A', 'batch_B']:
                socketio.emit('downloads:batch_update', {
                    'batch_id': bid,
                    'data': build_batch(bid, batches[bid]),
                }, room=f'batch:{bid}')

        received = client.get_received()
        dl_events = [e for e in received if e['name'] == 'downloads:batch_update']
        batch_ids_received = {e['args'][0]['batch_id'] for e in dl_events}

        assert 'batch_A' in batch_ids_received
        assert 'batch_B' not in batch_ids_received

    def test_download_unsubscribe_stops_updates(self, test_app, shared_state):
        """After unsubscribing, client stops receiving updates for that batch."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build_batch = shared_state['build_batch_status_data']

        self._add_batch(shared_state, 'batch_X')
        client.emit('downloads:subscribe', {'batch_ids': ['batch_X']})
        client.get_received()  # clear

        client.emit('downloads:unsubscribe', {'batch_ids': ['batch_X']})
        client.get_received()  # clear

        batches = shared_state['download_batches']
        lock = shared_state['tasks_lock']
        with lock:
            socketio.emit('downloads:batch_update', {
                'batch_id': 'batch_X',
                'data': build_batch('batch_X', batches['batch_X']),
            }, room='batch:batch_X')

        received = client.get_received()
        dl_events = [e for e in received if e['name'] == 'downloads:batch_update']
        assert len(dl_events) == 0

    def test_download_batch_shape(self, test_app, shared_state):
        """Batch update data has the expected structure."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build_batch = shared_state['build_batch_status_data']

        self._add_batch(shared_state, 'batch_shape')
        client.emit('downloads:subscribe', {'batch_ids': ['batch_shape']})
        client.get_received()

        batches = shared_state['download_batches']
        lock = shared_state['tasks_lock']
        with lock:
            socketio.emit('downloads:batch_update', {
                'batch_id': 'batch_shape',
                'data': build_batch('batch_shape', batches['batch_shape']),
            }, room='batch:batch_shape')

        received = client.get_received()
        dl_events = [e for e in received if e['name'] == 'downloads:batch_update']
        payload = dl_events[0]['args'][0]

        assert 'batch_id' in payload
        assert 'data' in payload
        data = payload['data']
        assert 'phase' in data
        assert 'tasks' in data
        assert 'active_count' in data
        assert 'max_concurrent' in data

    def test_multiple_clients_get_updates(self, test_app, shared_state):
        """Multiple WebSocket clients each receive broadcast events."""
        app, socketio = test_app
        client1 = socketio.test_client(app)
        client2 = socketio.test_client(app)
        build = shared_state['build_status_payload']

        socketio.emit('status:update', build())

        for client in [client1, client2]:
            received = client.get_received()
            status_events = [e for e in received if e['name'] == 'status:update']
            assert len(status_events) >= 1

        client1.disconnect()
        client2.disconnect()
