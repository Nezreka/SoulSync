"""Regression tests for idle-client gating on the background WebSocket push
loops in web_server.py.

These loops used to run their full cadence (0.5s-10s, forever) regardless of
whether any browser had a socket open — the heaviest, _emit_rate_monitor_loop,
polled ~15 services' rates plus full enrichment status every second even with
zero clients connected. Each loop now checks _has_connected_clients() and
skips the expensive gather/emit work when nobody is listening.

A few loops mix broadcast with functional side effects that must keep running
regardless of listeners (enrichment auto-pause/resume during downloads, the
DB-update stall self-heal). Those tests assert the functional half still runs
with zero clients while the broadcast half is skipped.

Runs the real web_server module code directly (not the fake app in
conftest.py), by driving exactly one loop iteration: patch socketio.sleep to
raise after the first tick so the `while` loop body runs once and then exits.
"""

from unittest.mock import MagicMock

import pytest

import web_server


class _StopAfterOneTick(Exception):
    """Sentinel used to escape a loop's `while True` after one body execution."""


def _run_one_tick(loop_fn):
    """Call loop_fn(), letting its first socketio.sleep() call succeed and its
    second raise, so exactly one iteration of the loop body executes."""
    calls = {"n": 0}

    def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] > 1:
            raise _StopAfterOneTick()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(web_server.socketio, "sleep", fake_sleep)
        with pytest.raises(_StopAfterOneTick):
            loop_fn()


@pytest.fixture(autouse=True)
def _clean_shared_state():
    """Isolate the module-level sets these loops read/mutate across tests."""
    def _clear():
        web_server._connected_sids.clear()
        web_server._download_auto_paused.discard("musicbrainz")
        web_server._download_yield_override.discard("musicbrainz")
        web_server._auto_yield_cause.pop("musicbrainz", None)

    _clear()
    yield
    _clear()


def test_has_connected_clients_reflects_sid_set():
    assert web_server._has_connected_clients() is False
    web_server._connected_sids.add("sid-1")
    assert web_server._has_connected_clients() is True
    web_server._connected_sids.discard("sid-1")
    assert web_server._has_connected_clients() is False


def test_rate_monitor_loop_skips_work_with_no_clients(monkeypatch):
    """The heaviest loop (1s cadence): with zero clients, it must not touch
    the rate tracker or emit anything."""
    fake_emit = MagicMock()
    monkeypatch.setattr(web_server.socketio, "emit", fake_emit)

    fake_tracker = MagicMock()
    monkeypatch.setitem(
        __import__("sys").modules,
        "core.api_call_tracker",
        MagicMock(api_call_tracker=fake_tracker),
    )

    assert web_server._has_connected_clients() is False
    _run_one_tick(web_server._emit_rate_monitor_loop)

    fake_tracker.get_all_rates.assert_not_called()
    fake_emit.assert_not_called()


def test_rate_monitor_loop_emits_with_a_connected_client(monkeypatch):
    fake_emit = MagicMock()
    monkeypatch.setattr(web_server.socketio, "emit", fake_emit)

    fake_tracker = MagicMock()
    fake_tracker.get_all_rates.return_value = {}
    monkeypatch.setitem(
        __import__("sys").modules,
        "core.api_call_tracker",
        MagicMock(api_call_tracker=fake_tracker),
    )

    web_server._connected_sids.add("sid-1")
    _run_one_tick(web_server._emit_rate_monitor_loop)

    fake_tracker.get_all_rates.assert_called_once()
    fake_emit.assert_called_once()
    assert fake_emit.call_args[0][0] == "rate-monitor:update"


def test_enrichment_status_loop_still_auto_pauses_with_no_clients(monkeypatch):
    """The enrichment loop's auto-pause/resume of workers during active
    downloads is functional, not UI — it must run even with zero clients,
    while the stats-gather + emit half must be skipped."""
    fake_worker = MagicMock()
    fake_worker.paused = False
    monkeypatch.setattr(web_server, "mb_worker", fake_worker)
    monkeypatch.setattr(web_server, "_has_active_downloads", lambda: True)
    monkeypatch.setattr(web_server, "_has_active_discovery", lambda: False)

    fake_emit = MagicMock()
    monkeypatch.setattr(web_server.socketio, "emit", fake_emit)

    assert web_server._has_connected_clients() is False
    _run_one_tick(web_server._emit_enrichment_status_loop)

    assert fake_worker.paused is True, "auto-pause must run regardless of listeners"
    fake_worker.get_stats.assert_not_called()
    fake_emit.assert_not_called()


def test_enrichment_status_loop_emits_with_a_connected_client(monkeypatch):
    fake_worker = MagicMock()
    fake_worker.paused = False
    fake_worker.get_stats.return_value = {}
    monkeypatch.setattr(web_server, "mb_worker", fake_worker)
    monkeypatch.setattr(web_server, "_has_active_downloads", lambda: False)
    monkeypatch.setattr(web_server, "_has_active_discovery", lambda: False)

    fake_emit = MagicMock()
    monkeypatch.setattr(web_server.socketio, "emit", fake_emit)

    web_server._connected_sids.add("sid-1")
    _run_one_tick(web_server._emit_enrichment_status_loop)

    fake_worker.get_stats.assert_called()
    fake_emit.assert_any_call("enrichment:musicbrainz", {})


def test_tool_progress_loop_self_heal_runs_with_no_clients(monkeypatch):
    """The DB-update stall self-heal (#859) is a functional fix, not UI — it
    must run even with zero clients, while the 4 status emits are skipped."""
    heal_called = []
    monkeypatch.setattr(
        web_server, "_check_db_update_stall", lambda: heal_called.append(True)
    )

    fake_emit = MagicMock()
    monkeypatch.setattr(web_server.socketio, "emit", fake_emit)

    assert web_server._has_connected_clients() is False
    _run_one_tick(web_server._emit_tool_progress_loop)

    assert heal_called == [True]
    fake_emit.assert_not_called()


def test_sync_progress_loop_reconcile_runs_with_no_clients(monkeypatch):
    """The stuck-'syncing'-state reconcile (#972) is a functional self-heal —
    it must run even with zero clients, while the progress emits are skipped."""
    reconcile_called = []
    monkeypatch.setattr(
        web_server,
        "_reconcile_discovery_sync_phases",
        lambda: reconcile_called.append(True),
    )

    fake_emit = MagicMock()
    monkeypatch.setattr(web_server.socketio, "emit", fake_emit)

    assert web_server._has_connected_clients() is False
    _run_one_tick(web_server._emit_sync_progress_loop)

    assert reconcile_called == [True]
    fake_emit.assert_not_called()


def test_download_status_loop_skips_slskd_poll_with_no_clients(monkeypatch):
    """Follow-up to the original gating PR: this loop's get_cached_transfer_data()
    is a REAL slskd transfers API call (TTL-cached) made every 2s even with zero
    batches — the worst idle offender. With no clients it must not poll slskd or
    emit; download progression doesn't depend on it (workers poll transfers
    themselves)."""
    fake_fetch = MagicMock()
    monkeypatch.setattr(web_server, "get_cached_transfer_data", fake_fetch)

    fake_emit = MagicMock()
    monkeypatch.setattr(web_server.socketio, "emit", fake_emit)

    assert web_server._has_connected_clients() is False
    _run_one_tick(web_server._emit_download_status_loop)

    fake_fetch.assert_not_called()
    fake_emit.assert_not_called()


def test_download_status_loop_fetches_with_a_connected_client(monkeypatch):
    fake_fetch = MagicMock(return_value={})
    monkeypatch.setattr(web_server, "get_cached_transfer_data", fake_fetch)

    fake_emit = MagicMock()
    monkeypatch.setattr(web_server.socketio, "emit", fake_emit)

    web_server._connected_sids.add("sid-1")
    _run_one_tick(web_server._emit_download_status_loop)

    fake_fetch.assert_called_once()
