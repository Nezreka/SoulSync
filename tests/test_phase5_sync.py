"""Phase 5 WebSocket migration tests — Sync/Discovery Progress + Scans.

Verifies that:
 - Room-based sync:progress events are delivered only to subscribed clients
 - Room-based discovery:progress events are delivered only to subscribed clients
 - Broadcast scan:watchlist and scan:media events reach all clients
 - Data shapes are correct for each event type
 - HTTP endpoints still work as fallback

IMPORTANT: Do NOT use ``from tests.conftest import …`` — pytest's auto-discovered
conftest is a different module instance. Use the ``shared_state`` fixture instead.
"""

import pytest


# =========================================================================
# Constants
# =========================================================================

DISCOVERY_PLATFORMS = ['tidal', 'youtube']

SYNC_PLAYLIST_IDS = ['test-playlist-1']


# =========================================================================
# Group A — Sync Event Delivery (room-based)
# =========================================================================

class TestSyncEventDelivery:
    """sync:progress socket events are received by subscribed clients."""

    def test_sync_event_received(self, test_app, shared_state):
        """Client subscribes and receives sync:progress event."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_sync_status']

        # Subscribe to sync room
        client.emit('sync:subscribe', {'playlist_ids': ['test-playlist-1']})

        # Server emits to the room
        socketio.emit('sync:progress', {
            'playlist_id': 'test-playlist-1',
            **build('test-playlist-1')
        }, room='sync:test-playlist-1')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'sync:progress']
        assert len(events) >= 1

        client.disconnect()

    def test_sync_only_subscribed(self, test_app, shared_state):
        """Unsubscribed client does NOT receive sync:progress events."""
        app, socketio = test_app
        subscribed = socketio.test_client(app)
        unsubscribed = socketio.test_client(app)
        build = shared_state['build_sync_status']

        # Only one client subscribes
        subscribed.emit('sync:subscribe', {'playlist_ids': ['test-playlist-1']})

        socketio.emit('sync:progress', {
            'playlist_id': 'test-playlist-1',
            **build('test-playlist-1')
        }, room='sync:test-playlist-1')

        sub_events = [e for e in subscribed.get_received()
                      if e['name'] == 'sync:progress']
        unsub_events = [e for e in unsubscribed.get_received()
                        if e['name'] == 'sync:progress']

        assert len(sub_events) >= 1
        assert len(unsub_events) == 0

        subscribed.disconnect()
        unsubscribed.disconnect()

    def test_sync_subscribe_unsubscribe(self, test_app, shared_state):
        """Client stops receiving events after unsubscribing."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_sync_status']

        # Subscribe
        client.emit('sync:subscribe', {'playlist_ids': ['test-playlist-1']})

        # First emit — should receive
        socketio.emit('sync:progress', {
            'playlist_id': 'test-playlist-1',
            **build('test-playlist-1')
        }, room='sync:test-playlist-1')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'sync:progress']
        assert len(events) >= 1

        # Unsubscribe
        client.emit('sync:unsubscribe', {'playlist_ids': ['test-playlist-1']})

        # Second emit — should NOT receive
        socketio.emit('sync:progress', {
            'playlist_id': 'test-playlist-1',
            **build('test-playlist-1')
        }, room='sync:test-playlist-1')

        received2 = client.get_received()
        events2 = [e for e in received2 if e['name'] == 'sync:progress']
        assert len(events2) == 0

        client.disconnect()


# =========================================================================
# Group B — Sync Data Shape
# =========================================================================

class TestSyncDataShape:
    """sync:progress event data has the expected keys."""

    def test_sync_progress_shape(self, test_app, shared_state):
        """Sync progress has playlist_id, status, progress dict."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_sync_status']

        client.emit('sync:subscribe', {'playlist_ids': ['test-playlist-1']})

        payload = {'playlist_id': 'test-playlist-1', **build('test-playlist-1')}
        socketio.emit('sync:progress', payload, room='sync:test-playlist-1')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'sync:progress']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'playlist_id' in data
        assert data['playlist_id'] == 'test-playlist-1'
        assert 'status' in data
        assert data['status'] == 'syncing'
        assert 'progress' in data
        progress = data['progress']
        assert 'total_tracks' in progress
        assert 'matched_tracks' in progress
        assert 'progress' in progress
        assert isinstance(progress['progress'], (int, float))

        client.disconnect()


# =========================================================================
# Group C — Discovery Event Delivery (room-based)
# =========================================================================

class TestDiscoveryEventDelivery:
    """discovery:progress socket events are received by subscribed clients."""

    @pytest.mark.parametrize('platform,pid', [
        ('tidal', 'test-tidal-1'),
        ('youtube', 'test-yt-hash'),
    ])
    def test_discovery_event_received(self, test_app, shared_state, platform, pid):
        """Client subscribes and receives discovery:progress event."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_discovery_status']

        client.emit('discovery:subscribe', {'ids': [pid]})

        payload = build(platform, pid)
        payload['platform'] = platform
        payload['id'] = pid
        socketio.emit('discovery:progress', payload, room=f'discovery:{pid}')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'discovery:progress']
        assert len(events) >= 1

        client.disconnect()

    def test_discovery_only_subscribed(self, test_app, shared_state):
        """Unsubscribed client does NOT receive discovery:progress events."""
        app, socketio = test_app
        subscribed = socketio.test_client(app)
        unsubscribed = socketio.test_client(app)
        build = shared_state['build_discovery_status']

        subscribed.emit('discovery:subscribe', {'ids': ['test-tidal-1']})

        payload = build('tidal', 'test-tidal-1')
        payload['platform'] = 'tidal'
        payload['id'] = 'test-tidal-1'
        socketio.emit('discovery:progress', payload, room='discovery:test-tidal-1')

        sub_events = [e for e in subscribed.get_received()
                      if e['name'] == 'discovery:progress']
        unsub_events = [e for e in unsubscribed.get_received()
                        if e['name'] == 'discovery:progress']

        assert len(sub_events) >= 1
        assert len(unsub_events) == 0

        subscribed.disconnect()
        unsubscribed.disconnect()

    def test_discovery_subscribe_unsubscribe(self, test_app, shared_state):
        """Client stops receiving discovery events after unsubscribing."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_discovery_status']

        client.emit('discovery:subscribe', {'ids': ['test-yt-hash']})

        payload = build('youtube', 'test-yt-hash')
        payload['platform'] = 'youtube'
        payload['id'] = 'test-yt-hash'
        socketio.emit('discovery:progress', payload, room='discovery:test-yt-hash')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'discovery:progress']
        assert len(events) >= 1

        client.emit('discovery:unsubscribe', {'ids': ['test-yt-hash']})

        socketio.emit('discovery:progress', payload, room='discovery:test-yt-hash')
        received2 = client.get_received()
        events2 = [e for e in received2 if e['name'] == 'discovery:progress']
        assert len(events2) == 0

        client.disconnect()


# =========================================================================
# Group D — Discovery Data Shape
# =========================================================================

class TestDiscoveryDataShape:
    """discovery:progress event data has the expected keys."""

    @pytest.mark.parametrize('platform,pid', [
        ('tidal', 'test-tidal-1'),
        ('youtube', 'test-yt-hash'),
    ])
    def test_discovery_progress_shape(self, test_app, shared_state, platform, pid):
        """Discovery progress has platform, id, phase, status, complete, results."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_discovery_status']

        client.emit('discovery:subscribe', {'ids': [pid]})

        payload = build(platform, pid)
        payload['platform'] = platform
        payload['id'] = pid
        socketio.emit('discovery:progress', payload, room=f'discovery:{pid}')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'discovery:progress']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert data['platform'] == platform
        assert data['id'] == pid
        assert 'phase' in data
        assert 'status' in data
        assert 'progress' in data
        assert 'spotify_matches' in data
        assert 'spotify_total' in data
        assert 'results' in data
        assert 'complete' in data
        assert isinstance(data['results'], list)
        assert isinstance(data['complete'], bool)

        client.disconnect()


# =========================================================================
# Group E — Scan Events (broadcast)
# =========================================================================

class TestScanEventDelivery:
    """Broadcast scan events are received by all connected clients."""

    def test_watchlist_scan_received(self, test_app, shared_state):
        """All clients receive scan:watchlist event."""
        app, socketio = test_app
        client1 = socketio.test_client(app)
        client2 = socketio.test_client(app)
        build = shared_state['build_watchlist_scan_status']

        socketio.emit('scan:watchlist', build())

        for client in [client1, client2]:
            received = client.get_received()
            events = [e for e in received if e['name'] == 'scan:watchlist']
            assert len(events) >= 1

        client1.disconnect()
        client2.disconnect()

    def test_watchlist_scan_shape(self, test_app, shared_state):
        """scan:watchlist data has success, status, current_artist_name."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_watchlist_scan_status']

        socketio.emit('scan:watchlist', build())

        received = client.get_received()
        events = [e for e in received if e['name'] == 'scan:watchlist']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert data['success'] is True
        assert 'status' in data
        assert data['status'] == 'scanning'
        assert 'current_artist_name' in data
        assert 'current_album' in data
        assert 'current_track_name' in data

        client.disconnect()

    def test_media_scan_received(self, test_app, shared_state):
        """All clients receive scan:media event."""
        app, socketio = test_app
        client1 = socketio.test_client(app)
        client2 = socketio.test_client(app)
        build = shared_state['build_media_scan_status']

        socketio.emit('scan:media', build())

        for client in [client1, client2]:
            received = client.get_received()
            events = [e for e in received if e['name'] == 'scan:media']
            assert len(events) >= 1

        client1.disconnect()
        client2.disconnect()

    def test_media_scan_shape(self, test_app, shared_state):
        """scan:media data has success and status with is_scanning."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_media_scan_status']

        socketio.emit('scan:media', build())

        received = client.get_received()
        events = [e for e in received if e['name'] == 'scan:media']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert data['success'] is True
        assert 'status' in data
        status = data['status']
        assert 'is_scanning' in status
        assert 'status' in status
        assert 'progress_message' in status

        client.disconnect()


# =========================================================================
# Group F — HTTP Parity
# =========================================================================

class TestSyncHttpParity:
    """Socket event data matches HTTP endpoint response for sync."""

    def test_sync_matches_http(self, test_app, shared_state):
        """Sync socket data matches GET /api/sync/status/:id."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_sync_status']

        endpoint = shared_state['sync_endpoints']['sync']
        http_data = flask_client.get(endpoint).get_json()

        ws_client.emit('sync:subscribe', {'playlist_ids': ['test-playlist-1']})

        payload = {'playlist_id': 'test-playlist-1', **build('test-playlist-1')}
        socketio.emit('sync:progress', payload, room='sync:test-playlist-1')

        received = ws_client.get_received()
        events = [e for e in received if e['name'] == 'sync:progress']
        assert len(events) >= 1
        ws_data = events[0]['args'][0]

        assert ws_data['status'] == http_data['status']
        assert ws_data['playlist_id'] == http_data['playlist_id']
        assert ws_data['progress']['total_tracks'] == http_data['progress']['total_tracks']
        assert ws_data['progress']['matched_tracks'] == http_data['progress']['matched_tracks']

        ws_client.disconnect()


class TestDiscoveryHttpParity:
    """Socket event data matches HTTP endpoint response for discovery."""

    @pytest.mark.parametrize('platform,pid,endpoint_key', [
        ('tidal', 'test-tidal-1', 'tidal'),
        ('youtube', 'test-yt-hash', 'youtube'),
    ])
    def test_discovery_matches_http(self, test_app, shared_state,
                                    platform, pid, endpoint_key):
        """Discovery socket data matches GET /api/<platform>/discovery/status/:id."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_discovery_status']

        endpoint = shared_state['discovery_endpoints'][endpoint_key]
        http_data = flask_client.get(endpoint).get_json()

        ws_client.emit('discovery:subscribe', {'ids': [pid]})

        payload = build(platform, pid)
        payload['platform'] = platform
        payload['id'] = pid
        socketio.emit('discovery:progress', payload, room=f'discovery:{pid}')

        received = ws_client.get_received()
        events = [e for e in received if e['name'] == 'discovery:progress']
        assert len(events) >= 1
        ws_data = events[0]['args'][0]

        assert ws_data['phase'] == http_data['phase']
        assert ws_data['status'] == http_data['status']
        assert ws_data['spotify_matches'] == http_data['spotify_matches']
        assert ws_data['spotify_total'] == http_data['spotify_total']
        assert ws_data['complete'] == http_data['complete']

        ws_client.disconnect()


class TestScanHttpParity:
    """Socket event data matches HTTP endpoint response for scans."""

    def test_watchlist_scan_matches_http(self, test_app, shared_state):
        """scan:watchlist socket data matches GET /api/watchlist/scan/status."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_watchlist_scan_status']

        endpoint = shared_state['scan_endpoints']['watchlist']
        http_data = flask_client.get(endpoint).get_json()

        socketio.emit('scan:watchlist', build())

        received = ws_client.get_received()
        events = [e for e in received if e['name'] == 'scan:watchlist']
        assert len(events) >= 1
        ws_data = events[0]['args'][0]

        assert ws_data['success'] == http_data['success']
        assert ws_data['status'] == http_data['status']
        assert ws_data['current_artist_name'] == http_data['current_artist_name']

        ws_client.disconnect()

    def test_media_scan_matches_http(self, test_app, shared_state):
        """scan:media socket data matches GET /api/scan/status."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_media_scan_status']

        endpoint = shared_state['scan_endpoints']['media']
        http_data = flask_client.get(endpoint).get_json()

        socketio.emit('scan:media', build())

        received = ws_client.get_received()
        events = [e for e in received if e['name'] == 'scan:media']
        assert len(events) >= 1
        ws_data = events[0]['args'][0]

        assert ws_data['success'] == http_data['success']
        assert ws_data['status']['is_scanning'] == http_data['status']['is_scanning']
        assert ws_data['status']['status'] == http_data['status']['status']

        ws_client.disconnect()


# =========================================================================
# Group G — HTTP Still Works
# =========================================================================

class TestHttpStillWorks:
    """HTTP endpoints return 200 with expected structure."""

    def test_sync_http_works(self, flask_client):
        """GET /api/sync/status/:id returns 200."""
        resp = flask_client.get('/api/sync/status/test-playlist-1')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'status' in data
        assert 'progress' in data

    def test_sync_http_404_unknown(self, flask_client):
        """GET /api/sync/status/:id returns 404 for unknown playlist."""
        resp = flask_client.get('/api/sync/status/nonexistent')
        assert resp.status_code == 404

    @pytest.mark.parametrize('platform,endpoint', [
        ('tidal', '/api/tidal/discovery/status/test-tidal-1'),
        ('youtube', '/api/youtube/discovery/status/test-yt-hash'),
    ])
    def test_discovery_http_works(self, flask_client, platform, endpoint):
        """GET /api/<platform>/discovery/status/:id returns 200."""
        resp = flask_client.get(endpoint)
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'phase' in data
        assert 'status' in data
        assert 'results' in data

    def test_discovery_http_not_found(self, flask_client):
        """GET /api/tidal/discovery/status/:id returns error for unknown ID."""
        resp = flask_client.get('/api/tidal/discovery/status/nonexistent')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'error' in data

    def test_watchlist_scan_http_works(self, flask_client):
        """GET /api/watchlist/scan/status returns 200."""
        resp = flask_client.get('/api/watchlist/scan/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert 'status' in data

    def test_media_scan_http_works(self, flask_client):
        """GET /api/scan/status returns 200."""
        resp = flask_client.get('/api/scan/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert 'status' in data


# =========================================================================
# Group H — Backward Compatibility
# =========================================================================

class TestBackwardCompat:
    """HTTP endpoints work when no WebSocket is connected."""

    def test_all_http_endpoints_work_without_socket(self, flask_client):
        """All Phase 5 HTTP endpoints work without any WebSocket connection."""
        endpoints = [
            '/api/sync/status/test-playlist-1',
            '/api/tidal/discovery/status/test-tidal-1',
            '/api/youtube/discovery/status/test-yt-hash',
            '/api/watchlist/scan/status',
            '/api/scan/status',
        ]
        for endpoint in endpoints:
            resp = flask_client.get(endpoint)
            assert resp.status_code == 200

    def test_multiple_clients_get_scan_updates(self, test_app, shared_state):
        """Multiple WebSocket clients each receive scan events."""
        app, socketio = test_app
        client1 = socketio.test_client(app)
        client2 = socketio.test_client(app)
        build = shared_state['build_watchlist_scan_status']

        socketio.emit('scan:watchlist', build())

        for client in [client1, client2]:
            received = client.get_received()
            events = [e for e in received if e['name'] == 'scan:watchlist']
            assert len(events) >= 1

        client1.disconnect()
        client2.disconnect()

    def test_sync_reflects_state_change(self, test_app, shared_state):
        """When sync state changes, the next emit reflects it."""
        app, socketio = test_app
        client = socketio.test_client(app)
        ss = shared_state['sync_states']
        build = shared_state['build_sync_status']

        client.emit('sync:subscribe', {'playlist_ids': ['test-playlist-1']})

        # Mutate state
        ss['test-playlist-1']['status'] = 'completed'
        ss['test-playlist-1']['progress']['progress'] = 100
        ss['test-playlist-1']['progress']['matched_tracks'] = 11

        payload = {'playlist_id': 'test-playlist-1', **build('test-playlist-1')}
        socketio.emit('sync:progress', payload, room='sync:test-playlist-1')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'sync:progress']
        data = events[-1]['args'][0]
        assert data['status'] == 'completed'
        assert data['progress']['progress'] == 100
        assert data['progress']['matched_tracks'] == 11

        client.disconnect()

    def test_discovery_reflects_state_change(self, test_app, shared_state):
        """When discovery state changes, the next emit reflects it."""
        app, socketio = test_app
        client = socketio.test_client(app)
        ds = shared_state['discovery_states']
        build = shared_state['build_discovery_status']

        client.emit('discovery:subscribe', {'ids': ['test-tidal-1']})

        # Mutate state
        ds['tidal']['test-tidal-1']['phase'] = 'discovered'
        ds['tidal']['test-tidal-1']['spotify_matches'] = 10

        payload = build('tidal', 'test-tidal-1')
        payload['platform'] = 'tidal'
        payload['id'] = 'test-tidal-1'
        socketio.emit('discovery:progress', payload, room='discovery:test-tidal-1')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'discovery:progress']
        data = events[-1]['args'][0]
        assert data['phase'] == 'discovered'
        assert data['complete'] is True
        assert data['spotify_matches'] == 10

        client.disconnect()

    def test_scan_reflects_state_change(self, test_app, shared_state):
        """When scan state changes, the next emit reflects it."""
        app, socketio = test_app
        client = socketio.test_client(app)
        wss = shared_state['watchlist_scan_state']
        build = shared_state['build_watchlist_scan_status']

        # Mutate state
        wss['status'] = 'completed'
        wss['current_artist_name'] = 'Led Zeppelin'

        socketio.emit('scan:watchlist', build())

        received = client.get_received()
        events = [e for e in received if e['name'] == 'scan:watchlist']
        data = events[-1]['args'][0]
        assert data['status'] == 'completed'
        assert data['current_artist_name'] == 'Led Zeppelin'

        client.disconnect()


# =========================================================================
# Group I — Phase 6: Platform Sync via WebSocket Rooms
# =========================================================================

PLATFORM_SYNC_IDS = [
    ('tidal', 'tidal_test-tidal-1'),
    ('youtube', 'youtube_test-yt-hash'),
    ('beatport', 'beatport_sync_test-bp-hash_1234'),
    ('listenbrainz', 'listenbrainz_test-lb-mbid'),
]


class TestPlatformSyncEventDelivery:
    """Platform sync pollers receive sync:progress via WS rooms."""

    @pytest.mark.parametrize('platform,sync_id', PLATFORM_SYNC_IDS)
    def test_platform_sync_event_received(self, test_app, shared_state, platform, sync_id):
        """Client subscribes to platform sync_playlist_id and receives sync:progress."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_sync_status']

        client.emit('sync:subscribe', {'playlist_ids': [sync_id]})

        socketio.emit('sync:progress', {
            'playlist_id': sync_id, **build(sync_id)
        }, room=f'sync:{sync_id}')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'sync:progress']
        assert len(events) >= 1
        data = events[0]['args'][0]
        assert data['playlist_id'] == sync_id
        assert data['status'] == 'syncing'

        client.disconnect()

    @pytest.mark.parametrize('platform,sync_id', PLATFORM_SYNC_IDS)
    def test_platform_sync_not_received_when_unsubscribed(
            self, test_app, shared_state, platform, sync_id):
        """Unsubscribed client does NOT receive platform sync events."""
        app, socketio = test_app
        subscribed = socketio.test_client(app)
        unsubscribed = socketio.test_client(app)
        build = shared_state['build_sync_status']

        subscribed.emit('sync:subscribe', {'playlist_ids': [sync_id]})

        socketio.emit('sync:progress', {
            'playlist_id': sync_id, **build(sync_id)
        }, room=f'sync:{sync_id}')

        sub_events = [e for e in subscribed.get_received()
                      if e['name'] == 'sync:progress']
        unsub_events = [e for e in unsubscribed.get_received()
                        if e['name'] == 'sync:progress']

        assert len(sub_events) >= 1
        assert len(unsub_events) == 0

        subscribed.disconnect()
        unsubscribed.disconnect()

    @pytest.mark.parametrize('platform,sync_id', PLATFORM_SYNC_IDS)
    def test_platform_sync_progress_shape(self, test_app, shared_state, platform, sync_id):
        """Platform sync progress data has expected keys."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_sync_status']

        client.emit('sync:subscribe', {'playlist_ids': [sync_id]})

        payload = {'playlist_id': sync_id, **build(sync_id)}
        socketio.emit('sync:progress', payload, room=f'sync:{sync_id}')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'sync:progress']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'playlist_id' in data
        assert 'status' in data
        assert 'progress' in data
        progress = data['progress']
        assert 'total_tracks' in progress
        assert 'matched_tracks' in progress
        assert 'failed_tracks' in progress
        assert 'progress' in progress

        client.disconnect()

    def test_platform_sync_completion_detection(self, test_app, shared_state):
        """When status changes to 'finished', client detects completion."""
        app, socketio = test_app
        client = socketio.test_client(app)
        ss = shared_state['sync_states']

        sync_id = 'tidal_test-tidal-1'
        client.emit('sync:subscribe', {'playlist_ids': [sync_id]})

        # Mutate to finished
        ss[sync_id]['status'] = 'finished'
        ss[sync_id]['progress']['progress'] = 100
        ss[sync_id]['progress']['matched_tracks'] = 8

        build = shared_state['build_sync_status']
        socketio.emit('sync:progress', {
            'playlist_id': sync_id, **build(sync_id)
        }, room=f'sync:{sync_id}')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'sync:progress']
        data = events[-1]['args'][0]
        assert data['status'] == 'finished'
        assert data['progress']['progress'] == 100

        client.disconnect()

    def test_platform_sync_error_detection(self, test_app, shared_state):
        """When status changes to 'error', client detects the error."""
        app, socketio = test_app
        client = socketio.test_client(app)
        ss = shared_state['sync_states']

        sync_id = 'youtube_test-yt-hash'
        client.emit('sync:subscribe', {'playlist_ids': [sync_id]})

        ss[sync_id]['status'] = 'error'
        ss[sync_id]['error'] = 'Connection lost'

        build = shared_state['build_sync_status']
        socketio.emit('sync:progress', {
            'playlist_id': sync_id, **build(sync_id)
        }, room=f'sync:{sync_id}')

        received = client.get_received()
        events = [e for e in received if e['name'] == 'sync:progress']
        data = events[-1]['args'][0]
        assert data['status'] == 'error'

        client.disconnect()

    @pytest.mark.parametrize('platform,sync_id', PLATFORM_SYNC_IDS)
    def test_platform_sync_http_still_works(self, flask_client, platform, sync_id):
        """GET /api/sync/status/<platform_sync_id> returns 200."""
        resp = flask_client.get(f'/api/sync/status/{sync_id}')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'status' in data
        assert 'progress' in data


# =========================================================================
# Group H — Wishlist Stats (broadcast)
# =========================================================================

class TestWishlistStatsEventDelivery:
    """wishlist:stats broadcast events are received by the client."""

    def test_wishlist_stats_event_received(self, test_app, shared_state):
        """Client receives a wishlist:stats event."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_wishlist_stats']

        socketio.emit('wishlist:stats', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'wishlist:stats']
        assert len(events) >= 1
        client.disconnect()

    def test_wishlist_stats_data_shape(self, test_app, shared_state):
        """wishlist:stats has is_auto_processing and next_run_in_seconds."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_wishlist_stats']

        socketio.emit('wishlist:stats', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'wishlist:stats']
        assert len(events) >= 1
        data = events[0]['args'][0]

        assert 'is_auto_processing' in data
        assert 'next_run_in_seconds' in data
        assert isinstance(data['is_auto_processing'], bool)
        assert isinstance(data['next_run_in_seconds'], (int, float))
        client.disconnect()

    def test_wishlist_stats_http_still_works(self, flask_client):
        """GET /api/wishlist/stats returns 200 with expected keys."""
        resp = flask_client.get('/api/wishlist/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'is_auto_processing' in data
        assert 'next_run_in_seconds' in data

    def test_wishlist_stats_auto_processing_detection(self, test_app, shared_state):
        """When is_auto_processing is True, client detects it."""
        app, socketio = test_app
        client = socketio.test_client(app)
        ws = shared_state['wishlist_stats_state']

        ws['is_auto_processing'] = True
        ws['next_run_in_seconds'] = 0
        build = shared_state['build_wishlist_stats']

        socketio.emit('wishlist:stats', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'wishlist:stats']
        data = events[-1]['args'][0]
        assert data['is_auto_processing'] is True
        client.disconnect()

    def test_wishlist_stats_countdown_value(self, test_app, shared_state):
        """next_run_in_seconds reflects the mutable state."""
        app, socketio = test_app
        client = socketio.test_client(app)
        ws = shared_state['wishlist_stats_state']

        ws['next_run_in_seconds'] = 42
        build = shared_state['build_wishlist_stats']

        socketio.emit('wishlist:stats', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'wishlist:stats']
        data = events[-1]['args'][0]
        assert data['next_run_in_seconds'] == 42
        client.disconnect()

    def test_wishlist_stats_multiple_clients(self, test_app, shared_state):
        """Multiple clients each receive wishlist:stats broadcast."""
        app, socketio = test_app
        client1 = socketio.test_client(app)
        client2 = socketio.test_client(app)
        build = shared_state['build_wishlist_stats']

        socketio.emit('wishlist:stats', build())

        for client in [client1, client2]:
            received = client.get_received()
            events = [e for e in received if e['name'] == 'wishlist:stats']
            assert len(events) >= 1

        client1.disconnect()
        client2.disconnect()

    def test_wishlist_stats_matches_http(self, test_app, shared_state):
        """Socket event data matches HTTP endpoint response."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_wishlist_stats']

        http_data = flask_client.get('/api/wishlist/stats').get_json()

        socketio.emit('wishlist:stats', build())
        received = ws_client.get_received()
        events = [e for e in received if e['name'] == 'wishlist:stats']
        ws_data = events[0]['args'][0]

        assert ws_data['is_auto_processing'] == http_data['is_auto_processing']
        assert ws_data['next_run_in_seconds'] == http_data['next_run_in_seconds']
        ws_client.disconnect()
