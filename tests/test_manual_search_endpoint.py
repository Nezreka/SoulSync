"""Tests for the /api/downloads/task/<id>/manual-search endpoint.

The candidates modal lets the user click an auto-found candidate to retry
a failed download. Manual search adds a second avenue — type a query, hit
search, get fresh results from the configured download source(s) without
having to leave the modal.

These tests cover the new endpoint's validation + dispatch behavior:

- Query length / source whitelist validation
- Hybrid mode 'all' fans out across every configured source in parallel
- Per-source request hits only the named source
- Unconfigured sources in hybrid mode are silently skipped
- Task lookup gates the endpoint with a 404 when the task isn't known

The endpoint constructs candidate JSON via the same helpers the
``/candidates`` endpoint uses, so the response shape carries the same
fields (track_info + candidates) plus a ``source`` tag on each candidate
so the manual-search frontend can show a per-row source badge in 'all'
mode.

The existing ``/download-candidate`` retry path is unchanged — manual-
search results POST through the same endpoint so AcoustID + post-download
safety nets stay in the loop.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixture — Flask test client + mocked plugin registry.
# ---------------------------------------------------------------------------


def _async_return(value):
    """Return a coroutine that resolves to ``value`` — passed to plugins'
    .search() so run_async() can drive it like a real awaitable."""
    async def _coro():
        return value
    return _coro()


def _fake_track_result(filename: str, source: str, username_override: str = None):
    """Build a TrackResult-shaped MagicMock that the serializer can read."""
    mock = MagicMock()
    mock.filename = filename
    mock.username = username_override or source  # streaming sources stamp the source name
    mock.size = 1024 * 1024 * 4
    mock.bitrate = 320
    mock.duration = 200_000
    mock.quality = 'mp3'
    mock.free_upload_slots = 1
    mock.upload_speed = 100_000
    mock.queue_length = 0
    mock.artist = 'Test Artist'
    mock.title = 'Test Title'
    mock.album = 'Test Album'
    # `hasattr(c, '__dict__')` is what _serialize_candidate uses to detect
    # dataclass-shaped inputs. MagicMock has __dict__, so this works.
    return mock


def _make_plugin(search_results=None, configured=True):
    """Stand-in for a download-source plugin. Records calls to .search()
    so tests can assert which sources got dispatched."""
    plugin = MagicMock()
    plugin.is_configured = MagicMock(return_value=configured)
    # Each call returns a fresh coroutine — async functions can't be
    # awaited twice, so the side_effect rebuilds the awaitable each time.
    plugin.search = MagicMock(
        side_effect=lambda *args, **kwargs: _async_return((search_results or [], []))
    )
    return plugin


@pytest.fixture
def manual_search_client(monkeypatch):
    """Flask test client with a fully mocked download_orchestrator + a
    seeded download_tasks entry. Each test reaches into the plugin mocks
    via the returned ``ctx`` dict to assert dispatch behavior."""
    # Avoid the real activity-feed side effects.
    with patch("web_server.add_activity_item"):
        # Mock external service singletons so import doesn't try to spin up
        # real clients / hit real APIs at module-load time.
        with patch("web_server.SpotifyClient"):
            with patch("core.tidal_client.TidalClient"):
                from web_server import app as flask_app
                import web_server

                flask_app.config['TESTING'] = True

                # Build a fresh registry-like object with deterministic plugins
                # — bypasses the eight real clients the orchestrator instantiates.
                plugins = {
                    'soulseek': _make_plugin(),
                    'youtube':  _make_plugin(),
                    'tidal':    _make_plugin(configured=False),  # not configured
                    'qobuz':    _make_plugin(),
                    'hifi':     _make_plugin(),
                    'deezer':   _make_plugin(),
                }

                class _FakeSpec:
                    def __init__(self, name):
                        self.name = name
                        self.display_name = name.title()
                        self.aliases = ()

                class _FakeRegistry:
                    def __init__(self, plugins_dict):
                        self._plugins = plugins_dict

                    def get(self, name):
                        return self._plugins.get(name)

                    def get_spec(self, name):
                        return _FakeSpec(name) if name in self._plugins else None

                    def names(self):
                        return list(self._plugins.keys())

                    def all_plugins(self):
                        return list(self._plugins.items())

                fake_orch = MagicMock()
                fake_orch.registry = _FakeRegistry(plugins)
                fake_orch.client = MagicMock(side_effect=lambda name: plugins.get(name))

                monkeypatch.setattr(web_server, 'download_orchestrator', fake_orch)

                # run_async drives the awaitable each plugin.search() returns —
                # the real one schedules onto the asyncio loop. The default
                # implementation in utils.async_helpers handles this fine,
                # but force a deterministic synchronous version so test
                # ordering is predictable.
                def _sync_run_async(coro):
                    import asyncio
                    loop = asyncio.new_event_loop()
                    try:
                        return loop.run_until_complete(coro)
                    finally:
                        loop.close()
                monkeypatch.setattr(web_server, 'run_async', _sync_run_async)

                # Seed download_tasks so the endpoint finds a real task.
                from core.runtime_state import download_tasks, tasks_lock
                with tasks_lock:
                    download_tasks.clear()
                    download_tasks['task-abc'] = {
                        'status': 'failed',
                        'track_info': {
                            'name': 'Test Track',
                            'artists': [{'name': 'Test Artist'}],
                            'duration_ms': 200_000,
                        },
                        'cached_candidates': [],
                    }

                # Default config: hybrid mode with all six in hybrid_order.
                # Individual tests override this.
                from config.settings import config_manager
                original_get = config_manager.get

                def _fake_config_get(key, default=None):
                    if key == 'download_source.mode':
                        return 'hybrid'
                    if key == 'download_source.hybrid_order':
                        return ['soulseek', 'youtube', 'tidal', 'qobuz', 'hifi', 'deezer']
                    return original_get(key, default)
                monkeypatch.setattr(config_manager, 'get', _fake_config_get)

                ctx = {
                    'plugins': plugins,
                    'config_get_setter': lambda fn: monkeypatch.setattr(config_manager, 'get', fn),
                }

                yield flask_app.test_client(), ctx

                with tasks_lock:
                    download_tasks.clear()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_manual_search_validates_query_length(manual_search_client):
    """Empty / 1-char query returns 400 — frontend hint says ≥2 chars."""
    client, _ctx = manual_search_client

    for bad_query in ['', ' ', 'a', '  a  ']:
        resp = client.post(
            '/api/downloads/task/task-abc/manual-search',
            json={'query': bad_query, 'source': 'all'},
        )
        assert resp.status_code == 400, f"query={bad_query!r} should 400"
        body = resp.get_json()
        assert 'error' in body


def test_manual_search_validates_source(manual_search_client):
    """Source must be 'all' or one of the configured source ids — anything
    else returns 400. Prevents users from triggering searches against a
    source the user explicitly disabled."""
    client, _ctx = manual_search_client

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'drake feelings', 'source': 'made_up_source'},
    )
    assert resp.status_code == 400
    assert 'error' in resp.get_json()


def test_manual_search_handles_task_not_found(manual_search_client):
    """Unknown task_id returns 404 — same gate as the existing /candidates
    and /download-candidate endpoints."""
    client, _ctx = manual_search_client

    resp = client.post(
        '/api/downloads/task/no-such-task/manual-search',
        json={'query': 'drake feelings', 'source': 'all'},
    )
    assert resp.status_code == 404
    assert 'error' in resp.get_json()


# ---------------------------------------------------------------------------
# Dispatch behavior — single source vs. parallel "all"
# ---------------------------------------------------------------------------


def test_manual_search_dispatches_to_configured_source_only(manual_search_client):
    """In hybrid mode, source='youtube' should hit only the youtube plugin's
    search — not soulseek, hifi, etc. The candidates endpoint already
    validates source against the configured list, so unconfigured plugins
    aren't reachable here."""
    client, ctx = manual_search_client
    plugins = ctx['plugins']

    # Configure youtube to return one result so we can verify the response.
    plugins['youtube'] = _make_plugin(
        search_results=[_fake_track_result('youtube_song.mp3', 'youtube')]
    )
    # Re-wire the orchestrator's client() so it returns the new mock.
    import web_server
    web_server.download_orchestrator.registry._plugins['youtube'] = plugins['youtube']

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'drake feelings', 'source': 'youtube'},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body['candidates']) == 1
    # Only youtube should have been searched.
    assert plugins['youtube'].search.call_count == 1
    assert plugins['soulseek'].search.call_count == 0
    assert plugins['hifi'].search.call_count == 0
    assert plugins['qobuz'].search.call_count == 0


def test_manual_search_all_dispatches_parallel(manual_search_client):
    """source='all' with hybrid mode → searches every CONFIGURED source.
    Tidal is unconfigured (is_configured()=False) so it's filtered out
    upstream by _list_available_download_sources — 5 of 6 sources hit."""
    client, ctx = manual_search_client
    plugins = ctx['plugins']

    # Make each configured source return one distinct result.
    import web_server
    for src_name in ('soulseek', 'youtube', 'qobuz', 'hifi', 'deezer'):
        new_plugin = _make_plugin(
            search_results=[_fake_track_result(f'{src_name}_song.mp3', src_name)]
        )
        plugins[src_name] = new_plugin
        web_server.download_orchestrator.registry._plugins[src_name] = new_plugin

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'drake feelings', 'source': 'all'},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    # 5 configured sources × 1 result each
    assert len(body['candidates']) == 5
    # Each configured source got searched once.
    for src_name in ('soulseek', 'youtube', 'qobuz', 'hifi', 'deezer'):
        assert plugins[src_name].search.call_count == 1, (
            f"{src_name} should have been searched in 'all' mode"
        )
    # Tidal is unconfigured → not in available_sources → not searched.
    assert plugins['tidal'].search.call_count == 0


def test_manual_search_skips_unconfigured_sources(manual_search_client):
    """Sources whose is_configured() returns False are excluded from the
    'all' dispatch list. This is the same gate hybrid-mode fallback uses
    for actual downloads — keeps manual search consistent with the rest
    of the orchestrator."""
    client, ctx = manual_search_client
    plugins = ctx['plugins']

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'something', 'source': 'all'},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    # Tidal is unconfigured — not in available_sources
    available_ids = {s['id'] for s in body['available_sources']}
    assert 'tidal' not in available_ids
    assert {'soulseek', 'youtube', 'qobuz', 'hifi', 'deezer'} <= available_ids
    # And tidal.search was NEVER called.
    assert plugins['tidal'].search.call_count == 0


def test_manual_search_rejects_unconfigured_source_explicitly(manual_search_client):
    """User can't bypass the 'all' filter by naming an unconfigured source
    directly — endpoint validates `source` against the live configured-
    sources list."""
    client, _ctx = manual_search_client

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'something', 'source': 'tidal'},
    )

    # Tidal is in the registry but is_configured()=False, so it's not in
    # available_sources, so the endpoint should reject the request.
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_manual_search_returns_same_shape_as_candidates(manual_search_client):
    """Response includes track_info + candidates array; each candidate
    carries a source field so the frontend can show per-row badges in
    'all' mode. Frontend renderer reuses the same row template for both
    auto-candidates and manual results."""
    client, ctx = manual_search_client
    plugins = ctx['plugins']

    import web_server
    plugins['youtube'] = _make_plugin(
        search_results=[_fake_track_result('youtube_song.mp3', 'youtube')]
    )
    web_server.download_orchestrator.registry._plugins['youtube'] = plugins['youtube']

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'drake feelings', 'source': 'youtube'},
    )

    assert resp.status_code == 200
    body = resp.get_json()

    # Top-level shape mirrors /candidates
    assert body['task_id'] == 'task-abc'
    assert 'track_info' in body
    assert body['track_info']['name'] == 'Test Track'
    assert 'candidates' in body
    assert 'candidate_count' in body
    assert body['candidate_count'] == len(body['candidates'])
    assert body['download_mode'] == 'hybrid'
    assert isinstance(body['available_sources'], list)
    # Echoed query lets frontend show "No results for X" with the same casing
    # the user typed.
    assert body['query'] == 'drake feelings'

    # Each candidate carries source for the frontend badge.
    for candidate in body['candidates']:
        assert 'source' in candidate
        assert candidate['source'] == 'youtube'
        # And the standard candidate fields are present (same shape as
        # /candidates serialization).
        for field in ('username', 'filename', 'size', 'quality',
                      'duration', 'bitrate', 'queue_length',
                      'free_upload_slots'):
            assert field in candidate, f"missing {field}"


def test_manual_search_single_source_mode_only_offers_one_source(monkeypatch, manual_search_client):
    """When download_source.mode is a single source (not hybrid), the
    available_sources list should contain just that one source. Frontend
    swaps the dropdown for a static label in this case."""
    client, _ctx = manual_search_client

    # Override config to single-source mode (soulseek only).
    from config.settings import config_manager
    monkeypatch.setattr(
        config_manager, 'get',
        lambda key, default=None: (
            'soulseek' if key == 'download_source.mode'
            else (['soulseek', 'youtube'] if key == 'download_source.hybrid_order' else default)
        ),
    )

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'something', 'source': 'soulseek'},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['download_mode'] == 'soulseek'
    available_ids = [s['id'] for s in body['available_sources']]
    assert available_ids == ['soulseek']


def test_manual_search_handles_plugin_exception_gracefully(manual_search_client):
    """If one source's .search() raises, the endpoint logs + skips it
    instead of failing the whole 'all' request. Other sources' results
    still come through."""
    client, ctx = manual_search_client
    plugins = ctx['plugins']

    import web_server

    # Soulseek raises; youtube returns one result.
    flaky_plugin = MagicMock()
    flaky_plugin.is_configured = MagicMock(return_value=True)

    def _raise(*args, **kwargs):
        async def _coro():
            raise RuntimeError("network blip")
        return _coro()

    flaky_plugin.search = MagicMock(side_effect=_raise)
    plugins['soulseek'] = flaky_plugin
    web_server.download_orchestrator.registry._plugins['soulseek'] = flaky_plugin

    plugins['youtube'] = _make_plugin(
        search_results=[_fake_track_result('youtube_song.mp3', 'youtube')]
    )
    web_server.download_orchestrator.registry._plugins['youtube'] = plugins['youtube']

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'something', 'source': 'all'},
    )

    assert resp.status_code == 200
    body = resp.get_json()
    # Failed plugin contributed 0; youtube's 1 result still comes through.
    yt_results = [c for c in body['candidates'] if c.get('source') == 'youtube']
    assert len(yt_results) == 1
