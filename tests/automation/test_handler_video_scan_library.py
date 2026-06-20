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
    return lambda: {'ok': True, 'sections': sections}


def _scan_done(movies: int = 3, shows: int = 1, episodes: int = 9):
    return lambda mode: {'state': 'done', 'movies': movies, 'shows': shows, 'episodes': episodes}


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

        def _scan(mode):
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

        def _scan(mode):
            seen['mode'] = mode
            return {'state': 'done'}

        deps = _RecordingDeps()
        auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=_refresh_ok(), run_video_scan=_scan,
        )
        assert seen['mode'] == 'full'


class TestServerUnavailable:
    def test_warns_but_still_reads_library(self):
        """A server that can't be triggered is a warning, not a failure —
        the read still mirrors whatever the server currently reports."""
        scanned = {}

        def _scan(mode):
            scanned['ran'] = True
            return {'state': 'done', 'movies': 5}

        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=lambda: {'ok': False, 'error': 'No video server configured'},
            run_video_scan=_scan,
        )
        assert scanned.get('ran') is True
        assert result['status'] == 'completed'
        assert 'warning' in deps.log_types()

    def test_none_refresh_result_is_tolerated(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=lambda: None, run_video_scan=_scan_done(),
        )
        assert result['status'] == 'completed'


class TestScanFailure:
    def test_scan_error_state_returns_error(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=_refresh_ok(),
            run_video_scan=lambda mode: {'state': 'error', 'error': 'no connected server'},
        )
        assert result['status'] == 'error'
        assert result['error'] == 'no connected server'
        assert result['_manages_own_progress'] is True
        assert 'error' in deps.statuses()

    def test_none_scan_result_does_not_crash(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=_refresh_ok(), run_video_scan=lambda mode: None,
        )
        # No state -> treated as a (zero-count) completion, never raises.
        assert result['status'] == 'completed'
        assert result['movies'] == 0


class TestHandlerNeverRaises:
    def test_swallows_refresh_exception(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=lambda: (_ for _ in ()).throw(RuntimeError('boom')),
        )
        assert result['status'] == 'error'
        assert result['error'] == 'boom'
        assert result['_manages_own_progress'] is True

    def test_swallows_scan_exception(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {'_automation_id': 'a'}, deps,
            server_refresh=_refresh_ok(),
            run_video_scan=lambda mode: (_ for _ in ()).throw(ValueError('kaboom')),
        )
        assert result['status'] == 'error'
        assert result['error'] == 'kaboom'

    def test_missing_automation_id_is_fine(self):
        deps = _RecordingDeps()
        result = auto_video_scan_library(
            {}, deps, server_refresh=_refresh_ok(), run_video_scan=_scan_done(),
        )
        assert result['status'] == 'completed'
