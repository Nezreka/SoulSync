"""Issue #887: the Spotify enrichment worker's get_stats() must report
``using_free`` when it's enriching via the no-creds Spotify Free source — even
with no official auth — so the dashboard shows "Running (Spotify Free)" instead
of a misleading "Not Authenticated".

Builds the worker via __new__ (bypassing the real SpotifyClient()) and stubs the
db-querying helpers, so the get_stats() free/auth logic is tested in isolation.
"""

from __future__ import annotations

import types

from core.spotify_worker import SpotifyWorker


def _worker(*, serving_via_free, sp=None, rate_limited=False, budget_free=False):
    w = SpotifyWorker.__new__(SpotifyWorker)
    w.running = True
    w.paused = False
    w.thread = types.SimpleNamespace(is_alive=lambda: True)
    w.current_item = None
    w.stats = {'pending': 0, 'processed': 0}
    w._serving_via_free = serving_via_free
    w.client = types.SimpleNamespace(
        sp=sp,
        is_rate_limited=lambda: rate_limited,
        get_rate_limit_info=lambda: None,
        get_post_ban_cooldown_remaining=lambda: 0,
        is_spotify_metadata_available=lambda: True,
        _budget_exhausted_use_free=budget_free,
    )
    # db-backed helpers stubbed — we only exercise the auth/free reporting.
    w._count_pending_items = lambda: 100
    w._get_progress_breakdown = lambda: {}
    w._get_daily_budget_info = lambda: {'exhausted': False}
    return w


def test_no_auth_but_serving_via_free_reports_using_free():
    # #887: no official auth (sp is None), worker enriching via Spotify Free.
    stats = _worker(serving_via_free=True, sp=None).get_stats()
    assert stats['authenticated'] is False     # no official auth
    assert stats['using_free'] is True          # ...but Free is carrying it


def test_no_auth_and_not_serving_free_is_not_using_free():
    # Genuinely can't enrich (no auth, free not active) -> Not Authenticated stands.
    stats = _worker(serving_via_free=False, sp=None).get_stats()
    assert stats['authenticated'] is False
    assert stats['using_free'] is False


def test_rate_limit_bridge_still_reports_using_free():
    # Pre-existing bridge path must still work (cache False, but rate-limited).
    stats = _worker(serving_via_free=False, sp=object(), rate_limited=True).get_stats()
    assert stats['using_free'] is True


def test_budget_bridge_still_reports_using_free():
    stats = _worker(serving_via_free=False, sp=object(), budget_free=True).get_stats()
    assert stats['using_free'] is True


def test_authed_and_not_on_free_reports_authenticated_not_free():
    stats = _worker(serving_via_free=False, sp=object()).get_stats()
    assert stats['authenticated'] is True
    assert stats['using_free'] is False
