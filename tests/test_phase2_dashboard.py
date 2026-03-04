"""Phase 2 WebSocket migration tests — Dashboard pollers.

Verifies that:
 - System stats, activity feed, toasts, DB stats, and wishlist count
   are delivered identically via WebSocket events and HTTP endpoints
 - Instant toast push from add_activity_item() works correctly
 - HTTP endpoints still work as fallback

IMPORTANT: Do NOT use ``from tests.conftest import …`` — pytest's auto-discovered
conftest is a different module instance. Use the ``shared_state`` fixture instead.
"""

import pytest
import time


# =========================================================================
# Group A — System Stats
# =========================================================================

class TestSystemStats:
    """dashboard:stats socket events match GET /api/system/stats."""

    def test_stats_event_received(self, test_app, shared_state):
        """Client receives a dashboard:stats event."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_system_stats']
        socketio.emit('dashboard:stats', build())
        received = client.get_received()
        stats_events = [e for e in received if e['name'] == 'dashboard:stats']
        assert len(stats_events) >= 1

    def test_stats_shape(self, test_app, shared_state):
        """dashboard:stats event data has expected keys."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_system_stats']
        socketio.emit('dashboard:stats', build())
        received = client.get_received()
        stats_events = [e for e in received if e['name'] == 'dashboard:stats']
        assert len(stats_events) >= 1
        data = stats_events[0]['args'][0]
        assert 'active_downloads' in data
        assert 'finished_downloads' in data
        assert 'download_speed' in data
        assert 'active_syncs' in data
        assert 'uptime' in data
        assert 'memory_usage' in data

    def test_stats_matches_http(self, test_app, shared_state):
        """Socket event data matches HTTP endpoint response."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_system_stats']

        http_data = flask_client.get('/api/system/stats').get_json()

        socketio.emit('dashboard:stats', build())
        received = ws_client.get_received()
        stats_events = [e for e in received if e['name'] == 'dashboard:stats']
        assert len(stats_events) >= 1
        ws_data = stats_events[0]['args'][0]

        assert ws_data['active_downloads'] == http_data['active_downloads']
        assert ws_data['finished_downloads'] == http_data['finished_downloads']
        assert ws_data['download_speed'] == http_data['download_speed']
        assert ws_data['active_syncs'] == http_data['active_syncs']
        assert ws_data['uptime'] == http_data['uptime']
        assert ws_data['memory_usage'] == http_data['memory_usage']

    def test_http_stats_still_works(self, flask_client):
        """GET /api/system/stats returns 200 with expected keys."""
        resp = flask_client.get('/api/system/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'active_downloads' in data
        assert 'finished_downloads' in data
        assert 'download_speed' in data


# =========================================================================
# Group B — Activity Feed
# =========================================================================

class TestActivityFeed:
    """dashboard:activity socket events match GET /api/activity/feed."""

    def test_activity_event_received(self, test_app, shared_state):
        """Client receives a dashboard:activity event."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_activity_feed_payload']
        socketio.emit('dashboard:activity', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'dashboard:activity']
        assert len(events) >= 1

    def test_activity_shape(self, test_app, shared_state):
        """dashboard:activity event data has activities array."""
        app, socketio = test_app
        client = socketio.test_client(app)
        # Add some activities first
        add_item = shared_state['add_activity_item']
        add_item('🎵', 'Download Complete', 'Artist - Song', show_toast=False)

        build = shared_state['build_activity_feed_payload']
        socketio.emit('dashboard:activity', build())
        received = client.get_received()
        # Filter out any toast events, get only activity events
        events = [e for e in received if e['name'] == 'dashboard:activity']
        assert len(events) >= 1
        data = events[0]['args'][0]
        assert 'activities' in data
        assert isinstance(data['activities'], list)

    def test_activity_matches_http(self, test_app, shared_state):
        """Socket event data matches HTTP endpoint response."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)

        # Add an activity
        add_item = shared_state['add_activity_item']
        add_item('🎵', 'Test Activity', 'Test subtitle', show_toast=False)

        http_data = flask_client.get('/api/activity/feed').get_json()
        build = shared_state['build_activity_feed_payload']
        socketio.emit('dashboard:activity', build())
        received = ws_client.get_received()
        events = [e for e in received if e['name'] == 'dashboard:activity']
        ws_data = events[0]['args'][0]

        assert len(ws_data['activities']) == len(http_data['activities'])
        if ws_data['activities']:
            assert ws_data['activities'][0]['title'] == http_data['activities'][0]['title']

    def test_http_activity_still_works(self, flask_client):
        """GET /api/activity/feed returns 200 with expected structure."""
        resp = flask_client.get('/api/activity/feed')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'activities' in data


# =========================================================================
# Group C — Toasts (instant push)
# =========================================================================

class TestToasts:
    """dashboard:toast events are pushed instantly from add_activity_item()."""

    def test_toast_emitted_on_add(self, test_app, shared_state):
        """Calling add_activity_item() with show_toast=True emits dashboard:toast."""
        app, socketio = test_app
        client = socketio.test_client(app)
        client.get_received()  # clear

        add_item = shared_state['add_activity_item']
        add_item('✅', 'Download Complete', 'Artist - Song', show_toast=True)

        received = client.get_received()
        toast_events = [e for e in received if e['name'] == 'dashboard:toast']
        assert len(toast_events) >= 1
        data = toast_events[0]['args'][0]
        assert data['title'] == 'Download Complete'
        assert data['subtitle'] == 'Artist - Song'

    def test_toast_not_emitted_when_disabled(self, test_app, shared_state):
        """add_activity_item() with show_toast=False does NOT emit dashboard:toast."""
        app, socketio = test_app
        client = socketio.test_client(app)
        client.get_received()  # clear

        add_item = shared_state['add_activity_item']
        add_item('📊', 'Background Task', 'Silent update', show_toast=False)

        received = client.get_received()
        toast_events = [e for e in received if e['name'] == 'dashboard:toast']
        assert len(toast_events) == 0

    def test_toast_shape(self, test_app, shared_state):
        """Toast data has expected keys."""
        app, socketio = test_app
        client = socketio.test_client(app)
        client.get_received()  # clear

        add_item = shared_state['add_activity_item']
        add_item('✅', 'Test Title', 'Test Subtitle', 'Now', show_toast=True)

        received = client.get_received()
        toast_events = [e for e in received if e['name'] == 'dashboard:toast']
        assert len(toast_events) >= 1
        data = toast_events[0]['args'][0]
        assert 'icon' in data
        assert 'title' in data
        assert 'subtitle' in data
        assert 'time' in data
        assert 'timestamp' in data
        assert 'show_toast' in data
        assert data['show_toast'] is True

    def test_http_toasts_still_works(self, flask_client, shared_state):
        """GET /api/activity/toasts returns 200 with expected structure."""
        # Add a toast-worthy activity first
        add_item = shared_state['add_activity_item']
        add_item('✅', 'Test', 'Sub', show_toast=True)

        resp = flask_client.get('/api/activity/toasts')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'toasts' in data


# =========================================================================
# Group D — DB Stats
# =========================================================================

class TestDbStats:
    """dashboard:db_stats socket events match GET /api/database/stats."""

    def test_db_stats_event_received(self, test_app, shared_state):
        """Client receives a dashboard:db_stats event."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_db_stats']
        socketio.emit('dashboard:db_stats', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'dashboard:db_stats']
        assert len(events) >= 1

    def test_db_stats_shape(self, test_app, shared_state):
        """dashboard:db_stats event data has expected keys."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_db_stats']
        socketio.emit('dashboard:db_stats', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'dashboard:db_stats']
        assert len(events) >= 1
        data = events[0]['args'][0]
        assert 'artists' in data
        assert 'albums' in data
        assert 'tracks' in data
        assert 'database_size_mb' in data
        assert 'server_source' in data

    def test_db_stats_matches_http(self, test_app, shared_state):
        """Socket event data matches HTTP endpoint response."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_db_stats']

        http_data = flask_client.get('/api/database/stats').get_json()

        socketio.emit('dashboard:db_stats', build())
        received = ws_client.get_received()
        events = [e for e in received if e['name'] == 'dashboard:db_stats']
        ws_data = events[0]['args'][0]

        assert ws_data['artists'] == http_data['artists']
        assert ws_data['albums'] == http_data['albums']
        assert ws_data['tracks'] == http_data['tracks']
        assert ws_data['database_size_mb'] == http_data['database_size_mb']
        assert ws_data['server_source'] == http_data['server_source']

    def test_http_db_stats_still_works(self, flask_client):
        """GET /api/database/stats returns 200 with expected keys."""
        resp = flask_client.get('/api/database/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'artists' in data
        assert 'albums' in data
        assert 'tracks' in data


# =========================================================================
# Group E — Wishlist Count
# =========================================================================

class TestWishlistCount:
    """dashboard:wishlist_count socket events match GET /api/wishlist/count."""

    def test_wishlist_count_received(self, test_app, shared_state):
        """Client receives a dashboard:wishlist_count event."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_wishlist_count_payload_ws']
        socketio.emit('dashboard:wishlist_count', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'dashboard:wishlist_count']
        assert len(events) >= 1

    def test_wishlist_count_shape(self, test_app, shared_state):
        """dashboard:wishlist_count event data has count key."""
        app, socketio = test_app
        client = socketio.test_client(app)
        build = shared_state['build_wishlist_count_payload_ws']
        socketio.emit('dashboard:wishlist_count', build())
        received = client.get_received()
        events = [e for e in received if e['name'] == 'dashboard:wishlist_count']
        assert len(events) >= 1
        data = events[0]['args'][0]
        assert 'count' in data
        assert isinstance(data['count'], int)

    def test_wishlist_count_matches_http(self, test_app, shared_state):
        """Socket event data matches HTTP endpoint response."""
        app, socketio = test_app
        flask_client = app.test_client()
        ws_client = socketio.test_client(app)
        build = shared_state['build_wishlist_count_payload_ws']

        http_data = flask_client.get('/api/wishlist/count').get_json()

        socketio.emit('dashboard:wishlist_count', build())
        received = ws_client.get_received()
        events = [e for e in received if e['name'] == 'dashboard:wishlist_count']
        ws_data = events[0]['args'][0]

        assert ws_data['count'] == http_data['count']

    def test_http_wishlist_count_still_works(self, flask_client):
        """GET /api/wishlist/count returns 200 with count."""
        resp = flask_client.get('/api/wishlist/count')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'count' in data
        assert isinstance(data['count'], int)


# =========================================================================
# Group F — Backward Compatibility
# =========================================================================

class TestBackwardCompat:
    """HTTP endpoints work when no WebSocket is connected."""

    def test_all_http_endpoints_work_without_socket(self, flask_client):
        """All 5 Phase 2 HTTP endpoints work without any WebSocket connection."""
        # System stats
        resp = flask_client.get('/api/system/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['active_downloads'] == 2

        # Activity feed
        resp = flask_client.get('/api/activity/feed')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'activities' in data

        # Toasts
        resp = flask_client.get('/api/activity/toasts')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'toasts' in data

        # DB stats
        resp = flask_client.get('/api/database/stats')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['artists'] == 350

        # Wishlist count
        resp = flask_client.get('/api/wishlist/count')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['count'] == 5

    def test_multiple_clients_get_dashboard_updates(self, test_app, shared_state):
        """Multiple WebSocket clients each receive dashboard broadcast events."""
        app, socketio = test_app
        client1 = socketio.test_client(app)
        client2 = socketio.test_client(app)
        build = shared_state['build_system_stats']

        socketio.emit('dashboard:stats', build())

        for client in [client1, client2]:
            received = client.get_received()
            events = [e for e in received if e['name'] == 'dashboard:stats']
            assert len(events) >= 1

        client1.disconnect()
        client2.disconnect()
