"""Every enrichment source that makes rate-limited API calls must be
registered in all three api_call_tracker maps, or it gets no rate-monitor
badge / row in system status.

PR #968 review: Bandcamp was missing from RATE_LIMITS, SERVICE_LABELS and
SERVICE_ORDER, so its worker made calls that never showed up in the monitor.
"""

from __future__ import annotations

from core.api_call_tracker import RATE_LIMITS, SERVICE_LABELS, SERVICE_ORDER


def test_bandcamp_registered_in_all_rate_monitor_maps():
    assert 'bandcamp' in RATE_LIMITS
    assert 'bandcamp' in SERVICE_LABELS
    assert 'bandcamp' in SERVICE_ORDER


def test_rate_monitor_maps_are_mutually_consistent():
    # A service listed for display must have both a label and a rate limit.
    for svc in SERVICE_ORDER:
        assert svc in SERVICE_LABELS, f'{svc} in SERVICE_ORDER but missing a label'
        assert svc in RATE_LIMITS, f'{svc} in SERVICE_ORDER but missing a rate limit'
