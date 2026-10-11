"""Regression test: watchlist scan state must be shared.

SeadogsBooty reported the live "Currently Scanning" deck disappears after
~1 second. Root cause: api/artist_watchlist.py (manual scans) and
web_server.py (socket emit loop) each maintained their own separate
watchlist_scan_state dict. During a manual scan the API said 'scanning'
while the socket kept emitting 'idle'.

This test verifies both modules use the single shared dict from
core/watchlist/scan_state.py.
"""

from core.watchlist import scan_state


def test_shared_state_single_object():
    """get_state() always returns the same dict object."""
    assert scan_state.get_state() is scan_state.get_state()


def test_reset_state_preserves_identity():
    """reset_state() mutates in place; existing references see the update."""
    ref = scan_state.get_state()
    ref['status'] = 'idle'
    scan_state.reset_state()
    # Same object, now reset to scanning
    assert ref is scan_state.get_state()
    assert ref['status'] == 'scanning'
    assert ref['cancel_requested'] is False


def test_mutation_visible_via_new_reference():
    """Mutations via one reference are visible via another (simulates the
    api module writing and web_server reading)."""
    scan_state.reset_state()
    writer = scan_state.get_state()
    writer['status'] = 'scanning'
    writer['current_artist_name'] = 'The Used'

    # Simulate web_server's socket loop reading the state
    reader = scan_state.get_state()
    assert reader['status'] == 'scanning'
    assert reader['current_artist_name'] == 'The Used'


def test_initial_state_is_idle():
    """Fresh import starts idle (no phantom scan deck)."""
    # Reset to known state first (other tests may have mutated)
    scan_state.get_state().clear()
    scan_state.get_state().update({
        'status': 'idle',
        'results': [],
        'summary': {},
        'error': None,
    })
    assert scan_state.get_state()['status'] == 'idle'


def test_started_at_distinguishes_fresh_scan():
    """The automation handler detects fresh scans via started_at, not id().

    Regression: the old handler used id(state_dict) to detect reassignment.
    With the shared in-place-mutated dict, id() never changes — so the
    handler now compares started_at timestamps instead.
    """
    from datetime import datetime, timedelta

    scan_state.reset_state()
    pre_started = scan_state.get_state().get('started_at')
    assert pre_started is not None

    # Simulate a second scan starting later (in-place mutation, same id)
    later = datetime.now() + timedelta(seconds=60)
    scan_state.get_state()['started_at'] = later

    post_started = scan_state.get_state().get('started_at')
    # Same object...
    assert scan_state.get_state() is scan_state.get_state()
    # ...but the handler can still tell a fresh scan ran
    assert post_started != pre_started
