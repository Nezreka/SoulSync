"""Seam-level tests for the ``video_scan_library`` automation handler.

The handler is the VIDEO twin of ``scan_library``: it nudges the media
server to rescan the user's selected video sections, then reads the result
into video.db. Both side effects are injected (``server_refresh`` /
``run_video_scan``) so these tests exercise the real handler logic with
fakes — no Flask, no DB, no media server.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.automation.handlers.video_scan_library import auto_video_scan_library


class _RecordingDeps:
    """Captures every update_progress call so tests can assert on the
    streamed phase/log/status sequence."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def update_progress(self, automation_id: Any = None, **kw: Any) -> None:
        kw['_id'] = automation_id
        self.calls.append(kw)

    # convenience accessors
    def statuses(self) -> List[str]:
        return [c['status'] for c in self.calls if 'status' in c]

    def log_types(self) -> List[str]:
        return [c['log_type'] for c in self.calls if 'log_type' in c]


def _refresh_ok(sections: int = 2):
    return lambda media_type=None: {'ok': True, 'sections': sections}


def _scan_done(movies: int = 3, shows: int = 1, episodes: int = 9):
    return lambda mode, media_type=None: {'state': 'done', 'movies': movies, 'shows': shows, 'episodes': episodes}


class TestHappyPath:
    def test_returns_completed_with_counts(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a', 'mode': 'full'}, deps,
            server_refresh=_refresh_ok(), run_video_scan=_scan_done(3, 1, 9),
        )
        assert result == {
            'status': 'completed', '_manages_own_progress': True,
            'movies': 3, 'shows': 1, 'episodes': 9,
        }

    def test_finishes_at_100_percent(self):
        deps = _RecordingDeps()
        auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=_refresh_ok(), run_video_scan=_scan_done(),
        )
        # The final progress call drives the card to completion.
        final = deps.calls[-1]
        assert final.get('status') == 'finished'
        assert final.get('progress') == 100

    def test_passes_configured_mode_through_to_scan(self):
        seen = {}

        def _scan(mode, media_type=None):
            seen['mode'] = mode
            return {'state': 'done'}

        deps = _RecordingDeps()
        auto_video_scan_library(
            {'_automation_id': 'a', 'mode': 'deep'}, deps,
            server_refresh=_refresh_ok(), run_video_scan=_scan,
        )
        assert seen['mode'] == 'deep'

    def test_defaults_mode_to_full(self):
        seen = {}

        def _scan(mode, media_type=None):
            seen['mode'] = mode
            return {'state': 'done'}

        deps = _RecordingDeps()
        auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=_refresh_ok(), run_video_scan=_scan,
        )
        assert seen['mode'] == 'full'


class TestMediaTypeScope:
    """The Movie and TV deep scans run the same handler scoped via media_type."""

    def test_passes_media_type_through_to_scan(self):
        seen = {}

        def _scan(mode, media_type=None):
            seen['media_type'] = media_type
            return {'state': 'done'}

        auto_video_scan_library(
            {'_automation_id': 'a', 'mode': 'deep', 'media_type': 'show'}, _RecordingDeps(),
            server_refresh=_refresh_ok(), run_video_scan=_scan)
        assert seen['media_type'] == 'show'

    def test_defaults_media_type_to_all(self):
        seen = {}

        def _scan(mode, media_type=None):
            seen['media_type'] = media_type
            return {'state': 'done'}

        auto_video_scan_library(
            {'_automation_id': 'a'}, _RecordingDeps(),
            server_refresh=_refresh_ok(), run_video_scan=_scan)
        assert seen['media_type'] == 'all'

    def test_movie_scan_summary_names_only_movies(self):
        deps = _RecordingDeps()
        auto_video_scan_library(
            {'_automation_id': 'a', 'media_type': 'movie'}, deps,
            server_refresh=_refresh_ok(), run_video_scan=_scan_done(7, 0, 0))
        summary = deps.calls[-1].get('log_line', '')
        assert 'Movie library scanned: 7 movies' == summary  # no "0 shows"

    def test_tv_scan_summary_names_only_tv(self):
        deps = _RecordingDeps()
        auto_video_scan_library(
            {'_automation_id': 'a', 'media_type': 'show'}, deps,
            server_refresh=_refresh_ok(), run_video_scan=_scan_done(0, 4, 22))
        summary = deps.calls[-1].get('log_line', '')
        assert summary == 'TV library scanned: 4 shows, 22 episodes'

    def test_busy_scanner_skips_cleanly(self):
        # The singleton scanner reports another run in progress → skip, don't error.
        res = auto_video_scan_library(
            {'_automation_id': 'a', 'media_type': 'movie'}, _RecordingDeps(),
            server_refresh=_refresh_ok(),
            run_video_scan=lambda mode, media_type=None: {'state': 'in_progress'})
        assert res['status'] == 'skipped'


class TestServerUnavailable:
    def test_warns_but_still_reads_library(self):
        """A server that can't be triggered is a warning, not a failure —
        the read still mirrors whatever the server currently reports."""
        scanned = {}

        def _scan(mode, media_type=None):
            scanned['ran'] = True
            return {'state': 'done', 'movies': 5}

        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=lambda media_type=None: {'ok': False, 'error': 'No video server configured'},
            run_video_scan=_scan,
        )
        assert scanned.get('ran') is True
        assert result['status'] == 'completed'
        assert 'warning' in deps.log_types()

    def test_none_refresh_result_is_tolerated(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=lambda media_type=None: None, run_video_scan=_scan_done(),
        )
        assert result['status'] == 'completed'


class TestScanFailure:
    def test_scan_error_state_returns_error(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=_refresh_ok(),
            run_video_scan=lambda mode, media_type=None: {'state': 'error', 'error': 'no connected server'},
        )
        assert result['status'] == 'error'
        assert result['error'] == 'no connected server'
        assert result['_manages_own_progress'] is True
        assert 'error' in deps.statuses()

    def test_none_scan_result_does_not_crash(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=_refresh_ok(), run_video_scan=lambda mode, media_type=None: None,
        )
        # No state -> treated as a (zero-count) completion, never raises.
        assert result['status'] == 'completed'
        assert result['movies'] == 0


class TestHandlerNeverRaises:
    def test_swallows_refresh_exception(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=lambda media_type=None: (_ for _ in ()).throw(RuntimeError('boom')),
        )
        assert result['status'] == 'error'
        assert result['error'] == 'boom'
        assert result['_manages_own_progress'] is True

    def test_swallows_scan_exception(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=_refresh_ok(),
            run_video_scan=lambda mode, media_type=None: (_ for _ in ()).throw(ValueError('kaboom')),
        )
        assert result['status'] == 'error'
        assert result['error'] == 'kaboom'

    def test_missing_automation_id_is_fine(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {}, deps, server_refresh=_refresh_ok(), run_video_scan=_scan_done(),
        )
        assert result['status'] == 'completed'


# ── post-download chain: video_scan_server (stage 1) ───────────────────────
from core.automation.handlers.video_scan_library import (  # noqa: E402
    auto_video_scan_server, auto_video_update_database, wait_for_server_scan)


class TestWaitForServerScan:
    """Poll the server until its scan queue is idle; fixed wait only as a fallback."""

    def test_returns_quickly_when_already_idle(self):
        slept = []
        waited = wait_for_server_scan(lambda: False, slept.append, grace_seconds=15)
        assert waited == 15 and slept == [15]                 # just the grace, then idle

    def test_polls_until_the_scan_finishes(self):
        # scanning for three polls, then idle — handles a 10-20 min big-library scan
        seq = iter([True, True, True, False])
        slept = []
        waited = wait_for_server_scan(lambda: next(seq), slept.append,
                                      grace_seconds=15, interval_seconds=10)
        assert waited == 15 + 30                               # grace + 3 polls
        assert slept == [15, 10, 10, 10]

    def test_falls_back_to_fixed_wait_when_status_unknown(self):
        slept = []
        waited = wait_for_server_scan(lambda: None, slept.append,
                                      grace_seconds=15, fallback_seconds=120)
        assert waited == 120 and slept == [15, 105]           # grace + (fallback - grace)

    def test_stops_at_the_cap_on_a_runaway_scan(self):
        slept = []
        waited = wait_for_server_scan(lambda: True, slept.append,
                                      grace_seconds=0, interval_seconds=10, cap_seconds=50)
        assert waited == 50                                   # never hangs forever

    def test_stops_if_status_becomes_unknown_mid_poll(self):
        seq = iter([True, None])
        slept = []
        wait_for_server_scan(lambda: next(seq), slept.append, grace_seconds=0, interval_seconds=10)
        assert slept == [10]                                  # one poll, then bail — no hang


class TestScanServerStage:
    def test_waits_for_idle_then_emits_scan_done(self):
        deps = _RecordingDeps()
        events = []
        # scanning once, then idle
        seq = iter([True, False])
        r = auto_video_scan_server(
            {'_automation_id': 'a'}, deps,
            server_refresh=_refresh_ok(), sleep=lambda s: None,
            scan_status=lambda mt: next(seq),
            emit=lambda ev, data: events.append((ev, data)))
        assert r['status'] == 'completed'
        assert events == [('video_library_scan_completed', {'server': '', 'media_type': 'all'})]

    def test_falls_back_to_fixed_wait_when_server_cant_report(self):
        slept = []
        auto_video_scan_server(
            {'_automation_id': 'a', 'debounce_seconds': 90}, _RecordingDeps(),
            server_refresh=_refresh_ok(), sleep=slept.append,
            scan_status=lambda mt: None, emit=lambda ev, data: None)
        assert sum(slept) == 90                               # honoured the fallback wait

    def test_server_unavailable_still_emits(self):
        deps = _RecordingDeps()
        events = []
        auto_video_scan_server(
            {'_automation_id': 'a'}, deps,
            server_refresh=lambda media_type=None: {'ok': False, 'error': 'no server'},
            sleep=lambda s: None, scan_status=lambda mt: False, emit=lambda ev, data: events.append(ev))
        assert events == ['video_library_scan_completed']     # fires even if refresh failed
        assert 'warning' in deps.log_types()

    def test_never_raises(self):
        deps = _RecordingDeps()
        r = auto_video_scan_server(
            {'_automation_id': 'a'}, deps,
            server_refresh=lambda media_type=None: (_ for _ in ()).throw(RuntimeError('boom')),
            sleep=lambda s: None, scan_status=lambda mt: False, emit=lambda *a: None)
        assert r['status'] == 'error' and r['error'] == 'boom'

    def test_scopes_refresh_and_status_and_carries_media_type_on_the_event(self):
        seen = {}
        events = []

        def _refresh(media_type=None):
            seen['refresh_mt'] = media_type
            return {'ok': True}

        auto_video_scan_server(
            {'_automation_id': 'a', 'media_type': 'show'}, _RecordingDeps(),
            server_refresh=_refresh, sleep=lambda s: None,
            scan_status=lambda mt: seen.setdefault('status_mt', mt) and False,
            emit=lambda ev, data: events.append((ev, data)))
        assert seen['refresh_mt'] == 'show'                           # only TV sections nudged
        assert seen['status_mt'] == 'show'                            # polled TV scan status
        assert events[0][1]['media_type'] == 'show'                   # stage 2 inherits the scope


# ── post-download chain: video_update_database (stage 2) ───────────────────
class TestUpdateDatabaseStage:
    def test_incremental_read_returns_counts(self):
        deps = _RecordingDeps()
        r = auto_video_update_database({'_automation_id': 'a'}, deps, run_video_scan=_scan_done(2, 1, 5))
        assert r == {'status': 'completed', '_manages_own_progress': True,
                     'movies': 2, 'shows': 1, 'episodes': 5}

    def test_defaults_to_incremental_mode(self):
        seen = {}

        def _scan(mode, media_type=None):
            seen['mode'] = mode
            return {'state': 'done'}

        auto_video_update_database({'_automation_id': 'a'}, _RecordingDeps(), run_video_scan=_scan)
        assert seen['mode'] == 'incremental'

    def test_scan_error_propagates(self):
        deps = _RecordingDeps()
        r = auto_video_update_database({'_automation_id': 'a'}, deps,
                                       run_video_scan=lambda m, media_type=None: {'state': 'error', 'error': 'no server'})
        assert r['status'] == 'error' and r['error'] == 'no server'

    def test_inherits_media_type_from_the_scan_event(self):
        # The post-download chain carries the scope: a TV-only rescan updates only TV.
        seen = {}

        def _scan(mode, media_type=None):
            seen['media_type'] = media_type
            return {'state': 'done', 'shows': 3, 'episodes': 12}

        deps = _RecordingDeps()
        auto_video_update_database(
            {'_automation_id': 'a', '_event_data': {'media_type': 'show'}}, deps, run_video_scan=_scan)
        assert seen['media_type'] == 'show'
        assert deps.calls[-1]['log_line'] == 'Video database updated: 3 shows, 12 episodes'

    def test_explicit_media_type_beats_event(self):
        seen = {}
        auto_video_update_database(
            {'_automation_id': 'a', 'media_type': 'movie', '_event_data': {'media_type': 'show'}},
            _RecordingDeps(),
            run_video_scan=lambda mode, media_type=None: seen.setdefault('mt', media_type) or {'state': 'done'})
        assert seen['mt'] == 'movie'

    def test_busy_scanner_skips_cleanly(self):
        r = auto_video_update_database(
            {'_automation_id': 'a'}, _RecordingDeps(),
            run_video_scan=lambda mode, media_type=None: {'state': 'in_progress'})
        assert r['status'] == 'skipped'
