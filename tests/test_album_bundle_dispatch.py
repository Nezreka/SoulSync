"""Tests for ``core/downloads/album_bundle_dispatch.py``.

Pins the gate predicate, the resolution + run flow, and the
fail / fall-through return contract. Mocks the config, plugin
resolver, and state access so the dispatcher is testable without
standing up runtime_state or a real plugin.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.downloads.album_bundle_dispatch import (
    BatchStateAccess,
    is_eligible,
    try_dispatch,
)


class _FakeState:
    """In-memory ``BatchStateAccess`` for tests — records every
    update so assertions can check the sequence of fields set."""

    def __init__(self) -> None:
        self.fields: dict = {}
        self.update_calls: list = []
        self.failed_with: str = ''

    def update_fields(self, batch_id: str, fields: dict) -> None:
        self.update_calls.append((batch_id, dict(fields)))
        self.fields.update(fields)

    def mark_failed(self, batch_id: str, error: str) -> None:
        self.failed_with = error
        self.fields['phase'] = 'failed'
        self.fields['error'] = error
        self.fields['album_bundle_state'] = 'failed'


def _config(values: dict):
    """Build a config_get callable from a flat dict."""
    def _get(key, default=None):
        return values.get(key, default)
    return _get


# ---------------------------------------------------------------------------
# is_eligible pure predicate
# ---------------------------------------------------------------------------


def test_is_eligible_requires_album_flag() -> None:
    assert is_eligible(mode='torrent', is_album=False,
                       album_name='X', artist_name='Y') is False


def test_is_eligible_requires_album_bundle_mode() -> None:
    for mode in ('youtube', 'tidal', 'qobuz', 'hifi',
                 'deezer_dl', 'amazon', 'lidarr', 'soundcloud', 'hybrid'):
        assert is_eligible(mode=mode, is_album=True,
                           album_name='X', artist_name='Y') is False


def test_is_eligible_accepts_torrent_usenet_and_soulseek() -> None:
    assert is_eligible(mode='torrent', is_album=True,
                       album_name='X', artist_name='Y') is True
    assert is_eligible(mode='usenet', is_album=True,
                       album_name='X', artist_name='Y') is True
    assert is_eligible(mode='soulseek', is_album=True,
                       album_name='X', artist_name='Y') is True


def test_is_eligible_requires_non_empty_names() -> None:
    assert is_eligible(mode='torrent', is_album=True,
                       album_name='', artist_name='Y') is False
    assert is_eligible(mode='torrent', is_album=True,
                       album_name='X', artist_name='') is False
    assert is_eligible(mode='torrent', is_album=True,
                       album_name='   ', artist_name='Y') is False


def test_is_eligible_case_insensitive_mode() -> None:
    assert is_eligible(mode='TORRENT', is_album=True,
                       album_name='X', artist_name='Y') is True


# ---------------------------------------------------------------------------
# try_dispatch — gate evaluation
# ---------------------------------------------------------------------------


def test_dispatch_returns_false_when_not_album() -> None:
    state = _FakeState()
    plugin = MagicMock()
    result = try_dispatch(
        batch_id='b1', is_album=False,
        album_context={'name': 'X'}, artist_context={'name': 'Y'},
        config_get=_config({'download_source.mode': 'torrent'}),
        plugin_resolver=lambda _name: plugin, state=state,
    )
    assert result is False
    assert state.update_calls == []
    plugin.download_album_to_staging.assert_not_called()


def test_dispatch_returns_false_for_non_album_bundle_modes() -> None:
    state = _FakeState()
    plugin = MagicMock()
    result = try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'X'}, artist_context={'name': 'Y'},
        config_get=_config({'download_source.mode': 'youtube'}),
        plugin_resolver=lambda _name: plugin, state=state,
    )
    assert result is False
    assert state.update_calls == []


def test_dispatch_returns_false_when_plugin_missing() -> None:
    """No plugin available → fall through to per-track flow with a
    warning. The state SHOULD NOT have been touched."""
    state = _FakeState()
    result = try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'X'}, artist_context={'name': 'Y'},
        config_get=_config({'download_source.mode': 'torrent'}),
        plugin_resolver=lambda _name: None, state=state,
    )
    assert result is False
    assert state.update_calls == []


def test_dispatch_returns_false_when_plugin_lacks_method() -> None:
    state = _FakeState()
    # Plugin that doesn't implement download_album_to_staging.
    class _LegacyPlugin:
        pass
    result = try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'X'}, artist_context={'name': 'Y'},
        config_get=_config({'download_source.mode': 'torrent'}),
        plugin_resolver=lambda _name: _LegacyPlugin(), state=state,
    )
    assert result is False
    assert state.update_calls == []


def test_dispatch_returns_false_when_resolver_raises() -> None:
    """Plugin resolution can fail (registry not initialised); we log
    and fall through rather than crashing the master worker."""
    state = _FakeState()
    def _boom(_name):
        raise RuntimeError("registry not initialised")
    result = try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'X'}, artist_context={'name': 'Y'},
        config_get=_config({'download_source.mode': 'torrent'}),
        plugin_resolver=_boom, state=state,
    )
    assert result is False


# ---------------------------------------------------------------------------
# try_dispatch — success / failure paths
# ---------------------------------------------------------------------------


def test_dispatch_success_returns_false_so_per_track_can_run() -> None:
    """Success → master worker should CONTINUE to per-track flow so
    each task can hit try_staging_match and find its file."""
    state = _FakeState()
    plugin = MagicMock()
    plugin.download_album_to_staging.return_value = {
        'success': True, 'files': ['/tmp/a.flac', '/tmp/b.flac'], 'error': None,
    }
    result = try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'GNX'}, artist_context={'name': 'Kendrick Lamar'},
        config_get=_config({
            'download_source.mode': 'torrent',
            'import.staging_path': '/staging/path',
        }),
        plugin_resolver=lambda _name: plugin, state=state,
    )
    assert result is False
    # Plugin was called with the right args.
    args = plugin.download_album_to_staging.call_args
    assert args.args[0] == 'GNX'
    assert args.args[1] == 'Kendrick Lamar'
    assert args.args[2].replace('\\', '/').endswith('storage/album_bundle_staging/b1')
    # Phase transitioned through searching → analysis.
    assert state.fields['phase'] == 'analysis'
    assert state.fields['album_bundle_state'] == 'staged'
    assert state.fields['album_bundle_source'] == 'torrent'
    assert state.fields['album_bundle_private_staging'] is True
    assert state.fields['album_bundle_staging_path'].replace('\\', '/').endswith('storage/album_bundle_staging/b1')
    assert state.failed_with == ''


def test_dispatch_uses_configured_private_album_bundle_staging_root() -> None:
    state = _FakeState()
    plugin = MagicMock()
    plugin.download_album_to_staging.return_value = {'success': True, 'files': ['/tmp/a.flac']}

    try_dispatch(
        batch_id='batch:with/slash', is_album=True,
        album_context={'name': 'GNX'}, artist_context={'name': 'Kendrick Lamar'},
        config_get=_config({
            'download_source.mode': 'torrent',
            'download_source.album_bundle_staging_path': '/private/staging',
        }),
        plugin_resolver=lambda _name: plugin, state=state,
    )

    staging_arg = plugin.download_album_to_staging.call_args.args[2].replace('\\', '/')
    assert staging_arg == '/private/staging/batch_with_slash'
    assert state.fields['album_bundle_staging_path'].replace('\\', '/') == staging_arg


def test_dispatch_failure_returns_true_so_master_stops() -> None:
    state = _FakeState()
    plugin = MagicMock()
    plugin.download_album_to_staging.return_value = {
        'success': False, 'files': [], 'error': 'No torrent results found',
    }
    result = try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'GNX'}, artist_context={'name': 'Kendrick Lamar'},
        config_get=_config({'download_source.mode': 'torrent'}),
        plugin_resolver=lambda _name: plugin, state=state,
    )
    assert result is True
    assert state.failed_with == 'No torrent results found'
    assert state.fields['phase'] == 'failed'


def test_dispatch_fallback_failure_returns_false_for_per_track_flow() -> None:
    state = _FakeState()
    plugin = MagicMock()
    plugin.download_album_to_staging.return_value = {
        'success': False,
        'files': [],
        'error': 'No complete Soulseek album folders found',
        'fallback': True,
    }
    result = try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'Album'}, artist_context={'name': 'Artist'},
        config_get=_config({'download_source.mode': 'soulseek'}),
        plugin_resolver=lambda _name: plugin, state=state,
    )
    assert result is False
    assert state.failed_with == ''
    assert state.fields['phase'] == 'analysis'
    assert state.fields['album_bundle_state'] == 'fallback'
    assert state.fields['album_bundle_error'] == 'No complete Soulseek album folders found'


def test_dispatch_plugin_exception_treated_as_failure() -> None:
    """A bug / network error in the plugin must not propagate into
    the master worker — caught + treated as a normal failure so
    the batch reports the error cleanly."""
    state = _FakeState()
    plugin = MagicMock()
    plugin.download_album_to_staging.side_effect = RuntimeError("network down")
    result = try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'GNX'}, artist_context={'name': 'Kendrick Lamar'},
        config_get=_config({'download_source.mode': 'torrent'}),
        plugin_resolver=lambda _name: plugin, state=state,
    )
    assert result is True
    assert 'network down' in state.failed_with


def test_dispatch_staging_oserror_falls_back_to_per_track():
    """A filesystem/staging failure (e.g. #760's PermissionError creating the
    staging dir) means the album downloaded but couldn't be staged locally —
    fall back to the per-track flow rather than hard-failing the whole batch."""
    state = _FakeState()
    plugin = MagicMock()
    plugin.download_album_to_staging.side_effect = PermissionError(
        "[Errno 13] Permission denied: 'storage/album_bundle_staging'")
    result = try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'Carnival'}, artist_context={'name': 'Some Artist'},
        config_get=_config({'download_source.mode': 'soulseek'}),
        plugin_resolver=lambda _name: plugin, state=state,
    )
    assert result is False                       # fell back; master continues per-track
    assert state.failed_with == ''               # NOT hard-failed
    assert state.fields['phase'] == 'analysis'
    assert state.fields['album_bundle_state'] == 'fallback'


def test_dispatch_strips_whitespace_from_names() -> None:
    """Trailing whitespace in batch context shouldn't fail the
    eligibility predicate AND should be cleaned before passing to
    the plugin."""
    state = _FakeState()
    plugin = MagicMock()
    plugin.download_album_to_staging.return_value = {'success': True, 'files': ['/x']}
    try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': '  GNX  '}, artist_context={'name': '  Kendrick  '},
        config_get=_config({'download_source.mode': 'torrent'}),
        plugin_resolver=lambda _name: plugin, state=state,
    )
    args = plugin.download_album_to_staging.call_args
    assert args.args[0] == 'GNX'
    assert args.args[1] == 'Kendrick'


def test_dispatch_source_override_uses_first_hybrid_source() -> None:
    state = _FakeState()
    plugin = MagicMock()
    plugin.download_album_to_staging.return_value = {'success': True, 'files': ['/x']}
    seen = []

    try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'GNX'}, artist_context={'name': 'Kendrick Lamar'},
        config_get=_config({'download_source.mode': 'hybrid'}),
        plugin_resolver=lambda name: seen.append(name) or plugin,
        state=state,
        source_override='soulseek',
    )

    assert seen == ['soulseek']
    assert state.fields['album_bundle_source'] == 'soulseek'


def test_dispatch_progress_callback_mirrors_payload_to_state() -> None:
    """The progress callback the plugin gets must mirror its
    payload onto the batch state under ``album_bundle_*`` keys so
    the Downloads page can render progress while the torrent
    download runs."""
    state = _FakeState()
    captured_emit = {}

    def _capture(album, artist, staging, emit):
        captured_emit['fn'] = emit
        emit({'state': 'searching', 'release': 'GNX [FLAC]'})
        emit({'state': 'downloading', 'progress': 0.42, 'speed': 1024 * 1024})
        emit({'state': 'staged', 'count': 12})
        return {'success': True, 'files': []}

    plugin = MagicMock()
    plugin.download_album_to_staging.side_effect = _capture
    try_dispatch(
        batch_id='b1', is_album=True,
        album_context={'name': 'GNX'}, artist_context={'name': 'Kendrick Lamar'},
        config_get=_config({'download_source.mode': 'torrent'}),
        plugin_resolver=lambda _name: plugin, state=state,
    )
    # State should have seen each of the three lifecycle emissions.
    states_seen = [fields.get('album_bundle_state')
                   for _, fields in state.update_calls
                   if 'album_bundle_state' in fields]
    assert 'searching' in states_seen
    assert 'downloading' in states_seen
    assert 'staged' in states_seen
    # Numeric progress + release name made it through.
    assert state.fields['album_bundle_release'] == 'GNX [FLAC]'
    assert state.fields['album_bundle_progress'] == 0.42
    assert state.fields['album_bundle_count'] == 12


# ---------------------------------------------------------------------------
# Protocol conformance — runtime impl must satisfy the contract
# ---------------------------------------------------------------------------


def test_runtime_state_impl_matches_protocol() -> None:
    """Sanity check that the concrete BatchStateAccess impl in
    master.py implements both methods. We don't import master.py
    here (would pull in heavy deps); duck-check on the _FakeState
    instead since it's a sibling impl of the same Protocol."""
    state: BatchStateAccess = _FakeState()
    state.update_fields('b1', {'x': 1})
    state.mark_failed('b1', 'oops')
    assert state.fields['x'] == 1
    assert state.fields['error'] == 'oops'
