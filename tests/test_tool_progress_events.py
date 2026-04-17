"""Phase 4 WebSocket migration tests — Tool progress pollers.

Verifies that:
 - All 7 tool progress statuses are delivered identically via
   WebSocket events and HTTP endpoints
 - Each tool's data shape is correct
 - HTTP endpoints still work as fallback

IMPORTANT: Do NOT use ``from tests.conftest import …`` — pytest's auto-discovered
conftest is a different module instance. Use the ``shared_state`` fixture instead.
"""

import pytest


# All 7 tool progress pollers
TOOLS = [
    'stream', 'quality-scanner', 'duplicate-cleaner',
    'retag', 'db-update', 'metadata', 'logs',
]

# Endpoint URLs keyed by tool name
ENDPOINTS = {
    'stream': '/api/stream/status',
    'quality-scanner': '/api/quality-scanner/status',
    'duplicate-cleaner': '/api/duplicate-cleaner/status',
    'retag': '/api/retag/status',
    'db-update': '/api/database/update/status',
    'metadata': '/api/metadata/status',
    'logs': '/api/logs',
}


# =========================================================================
# Group A — Event Delivery (parameterized)
# =========================================================================

class TestToolEventDelivery:
    """tool:<name> socket events are received by the client."""

# =========================================================================
# Group B — Data Shape (individual per tool)
# =========================================================================

class TestToolDataShape:
    """tool:<name> event data has the expected keys."""

    def test_stream_shape(self, test_app, shared_state):
        """Stream status has status, progress, track_info, error_message."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_stream_status']

        socketio.emit('tool:stream', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'tool:stream']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'status' in data
        assert 'progress' in data
        assert 'track_info' in data
        assert 'error_message' in data
        assert isinstance(data['progress'], (int, float))

    def test_quality_scanner_shape(self, test_app, shared_state):
        """Quality scanner has status, phase, progress, processed, total, quality_met."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_quality_scanner_status']

        socketio.emit('tool:quality-scanner', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'tool:quality-scanner']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'status' in data
        assert 'phase' in data
        assert 'progress' in data
        assert 'processed' in data
        assert 'total' in data
        assert 'quality_met' in data
        assert 'low_quality' in data
        assert 'matched' in data

    def test_duplicate_cleaner_shape(self, test_app, shared_state):
        """Duplicate cleaner has status, phase, progress, space_freed_mb."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_duplicate_cleaner_status']

        socketio.emit('tool:duplicate-cleaner', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'tool:duplicate-cleaner']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'status' in data
        assert 'phase' in data
        assert 'progress' in data
        assert 'files_scanned' in data
        assert 'total_files' in data
        assert 'duplicates_found' in data
        assert 'deleted' in data
        assert 'space_freed_mb' in data
        assert isinstance(data['space_freed_mb'], (int, float))

    def test_retag_shape(self, test_app, shared_state):
        """Retag has status, phase, progress, current_track, total_tracks."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_retag_status']

        socketio.emit('tool:retag', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'tool:retag']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'status' in data
        assert 'phase' in data
        assert 'progress' in data
        assert 'current_track' in data
        assert 'total_tracks' in data
        assert 'processed' in data

    def test_db_update_shape(self, test_app, shared_state):
        """DB update has status, phase, progress, removed_artists/albums/tracks."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_db_update_status']

        socketio.emit('tool:db-update', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'tool:db-update']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'status' in data
        assert 'phase' in data
        assert 'progress' in data
        assert 'current_item' in data
        assert 'processed' in data
        assert 'total' in data
        assert 'removed_artists' in data
        assert 'removed_albums' in data
        assert 'removed_tracks' in data

    def test_metadata_shape(self, test_app, shared_state):
        """Metadata has {success, status} wrapper with inner percentage, successful, failed."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_metadata_status']

        socketio.emit('tool:metadata', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'tool:metadata']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'success' in data
        assert data['success'] is True
        assert 'status' in data
        status = data['status']
        assert 'status' in status
        assert 'current_artist' in status
        assert 'processed' in status
        assert 'total' in status
        assert 'percentage' in status
        assert 'successful' in status
        assert 'failed' in status

    def test_logs_shape(self, test_app, shared_state):
        """Logs has logs array of strings."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_logs']

        socketio.emit('tool:logs', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'tool:logs']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'logs' in data
        assert isinstance(data['logs'], list)
        assert len(data['logs']) >= 1
        assert isinstance(data['logs'][0], str)


# =========================================================================
# Group C — HTTP Parity (parameterized)
# =========================================================================

class TestToolHttpParity:
    """Socket event data matches HTTP endpoint response."""

    @pytest.mark.parametrize('tool', [t for t in TOOLS if t != 'logs'])
    def test_tool_matches_http(self, test_app, shared_state, tool):
        """Socket event data matches GET endpoint for non-logs tools."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_tool_status']

        endpoint = ENDPOINTS[tool]
        http_data = flask_client.get(endpoint).get_json()

        socketio.emit(f'tool:{tool}', build(tool))
        received = ws_client.get_received()
        events = [e for e in received if e['name'] == f'tool:{tool}']
        assert len(events) >= 1
        ws_data = events[0]['args'][0]

        if tool == 'metadata':
            # Metadata wraps in {success, status}
            assert ws_data['success'] == http_data['success']
            assert ws_data['status']['status'] == http_data['status']['status']
            assert ws_data['status']['processed'] == http_data['status']['processed']
        else:
            assert ws_data['status'] == http_data['status']

    def test_logs_matches_http(self, test_app, shared_state):
        """Logs event data matches GET /api/logs."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_logs']

        http_data = flask_client.get('/api/logs').get_json()

        socketio.emit('tool:logs', build())
        received = ws_client.get_received()
        events = [e for e in received if e['name'] == 'tool:logs']
        assert len(events) >= 1
        ws_data = events[0]['args'][0]

        assert len(ws_data['logs']) == len(http_data['logs'])
        if ws_data['logs']:
            assert ws_data['logs'][0] == http_data['logs'][0]


# =========================================================================
# Group D — Backward Compatibility
# =========================================================================

class TestToolBackwardCompat:
    """WebSocket clients still receive broadcast tool updates."""

    def test_multiple_clients_get_tool_updates(self, test_app, shared_state):
        """Multiple WebSocket clients each receive tool events."""
        app, socketio = test_app
        client1 = socketio.test_client(app)
        client2 = socketio.test_client(app)
        build = shared_state['build_tool_status']

        socketio.emit('tool:quality-scanner', build('quality-scanner'))

        for client in [client1, client2]:
            received = client.get_received()
            events = [e for e in received if e['name'] == 'tool:quality-scanner']
            assert len(events) >= 1

        client1.disconnect()
        client2.disconnect()

    def test_tool_reflects_state_change(self, test_app, shared_state):
        """When tool state changes, the next emit reflects it."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_tool_status']
        qs = shared_state['quality_scanner_state']

        # Mutate state
        qs['status'] = 'finished'
        qs['progress'] = 100
        qs['processed'] = 100

        socketio.emit('tool:quality-scanner', build('quality-scanner'))
        received = client.get_received()
        events = [e for e in received if e['name'] == 'tool:quality-scanner']
        data = events[-1]['args'][0]
        assert data['status'] == 'finished'
        assert data['progress'] == 100
        assert data['processed'] == 100
