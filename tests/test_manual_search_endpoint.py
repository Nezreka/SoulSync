"""Tests for the /api/downloads/task/<id>/manual-search endpoint.

The candidates modal lets the user click an auto-found candidate to retry
a failed download. Manual search adds a second avenue — type a query, hit
search, get fresh results from the configured download source(s) without
having to leave the modal.

The endpoint streams results as NDJSON — one JSON object per line — so
the modal can render rows from each source as that source's search
completes, instead of blocking the whole UI on the slowest source. The
``_consume_ndjson`` helper below replays the stream as a list of message
dicts so test assertions stay readable.

These tests cover the new endpoint's validation + dispatch behavior:

- Query length / source whitelist validation
- Hybrid mode 'all' fans out across every configured source in parallel
- Per-source request hits only the named source
- Unconfigured sources in hybrid mode are silently skipped
- Task lookup gates the endpoint with a 404 when the task isn't known
- Per-source exceptions emit ``source_error`` events but don't fail the
  overall stream

The existing ``/download-candidate`` retry path is unchanged — manual-
search results POST through the same endpoint so AcoustID + post-download
safety nets stay in the loop.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _consume_ndjson(resp) -> list:
    """Parse a Flask test-client streaming response into a list of
    NDJSON message dicts. The endpoint emits one JSON object per line,
    each terminated by ``\\n``.
    """
    raw = resp.get_data(as_text=True)
    msgs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        msgs.append(json.loads(line))
    return msgs


def _flatten_candidates(msgs: list) -> list:
    """Pull all candidate dicts out of every ``source_results`` message —
    same flat list the old single-shot response used to return."""
    out = []
    for m in msgs:
        if m.get('type') == 'source_results':
            out.extend(m.get('candidates', []))
    return out


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
    search — not soulseek, hifi, etc."""
    client, ctx = manual_search_client
    plugins = ctx['plugins']

    plugins['youtube'] = _make_plugin(
        search_results=[_fake_track_result('youtube_song.mp3', 'youtube')]
    )
    import web_server
    web_server.download_orchestrator.registry._plugins['youtube'] = plugins['youtube']

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'drake feelings', 'source': 'youtube'},
    )

    assert resp.status_code == 200
    msgs = _consume_ndjson(resp)
    candidates = _flatten_candidates(msgs)
    assert len(candidates) == 1
    assert plugins['youtube'].search.call_count == 1
    assert plugins['soulseek'].search.call_count == 0
    assert plugins['hifi'].search.call_count == 0
    assert plugins['qobuz'].search.call_count == 0


def test_manual_search_all_dispatches_parallel(manual_search_client):
    """source='all' with hybrid mode → searches every CONFIGURED source.
    Tidal is unconfigured so it's filtered out — 5 of 6 sources hit."""
    client, ctx = manual_search_client
    plugins = ctx['plugins']

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
    msgs = _consume_ndjson(resp)
    candidates = _flatten_candidates(msgs)
    assert len(candidates) == 5
    for src_name in ('soulseek', 'youtube', 'qobuz', 'hifi', 'deezer'):
        assert plugins[src_name].search.call_count == 1
    assert plugins['tidal'].search.call_count == 0


def test_manual_search_streams_one_event_per_source(manual_search_client):
    """source='all' must emit one ``source_results`` event per configured
    source — not a single batched event. That's what lets the frontend
    render rows as they arrive instead of waiting for the slowest source."""
    client, ctx = manual_search_client
    plugins = ctx['plugins']

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

    msgs = _consume_ndjson(resp)
    source_events = [m for m in msgs if m.get('type') == 'source_results']
    seen_sources = {m['source'] for m in source_events}
    assert seen_sources == {'soulseek', 'youtube', 'qobuz', 'hifi', 'deezer'}
    # One header + one per source + one done terminator
    assert msgs[0]['type'] == 'header'
    assert msgs[-1]['type'] == 'done'
    assert msgs[-1]['total'] == 5


def test_manual_search_skips_unconfigured_sources(manual_search_client):
    """Sources whose is_configured() returns False are excluded from the
    'all' dispatch list."""
    client, ctx = manual_search_client
    plugins = ctx['plugins']

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'something', 'source': 'all'},
    )

    assert resp.status_code == 200
    msgs = _consume_ndjson(resp)
    header = msgs[0]
    assert header['type'] == 'header'
    available_ids = {s['id'] for s in header['available_sources']}
    assert 'tidal' not in available_ids
    assert {'soulseek', 'youtube', 'qobuz', 'hifi', 'deezer'} <= available_ids
    assert plugins['tidal'].search.call_count == 0


def test_manual_search_rejects_unconfigured_source_explicitly(manual_search_client):
    """User can't bypass the 'all' filter by naming an unconfigured source
    directly."""
    client, _ctx = manual_search_client

    resp = client.post(
        '/api/downloads/task/task-abc/manual-search',
        json={'query': 'something', 'source': 'tidal'},
    )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_manual_search_header_carries_track_and_source_metadata(manual_search_client):
    """The first NDJSON line is a ``header`` event carrying track_info,
    download_mode, available_sources, and the echoed query — everything
    the frontend needs to render the modal shell before any results
    arrive."""
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
    msgs = _consume_ndjson(resp)
    header = msgs[0]
    assert header['type'] == 'header'
    assert header['task_id'] == 'task-abc'
    assert header['track_info']['name'] == 'Test Track'
    assert header['download_mode'] == 'hybrid'
    assert isinstance(header['available_sources'], list)
    assert header['query'] == 'drake feelings'
    assert header['sources_queried'] == ['youtube']

    candidates = _flatten_candidates(msgs)
    assert len(candidates) == 1
    for candidate in candidates:
        assert candidate['source'] == 'youtube'
        for field in ('username', 'filename', 'size', 'quality',
                      'duration', 'bitrate', 'queue_length',
                      'free_upload_slots'):
            assert field in candidate, f"missing {field}"


def test_manual_search_single_source_mode_only_offers_one_source(monkeypatch, manual_search_client):
    """When download_source.mode is a single source, available_sources
    contains just that one entry. Frontend swaps the dropdown for a static
    label in this case."""
    client, _ctx = manual_search_client

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
    msgs = _consume_ndjson(resp)
    header = msgs[0]
    assert header['download_mode'] == 'soulseek'
    available_ids = [s['id'] for s in header['available_sources']]
    assert available_ids == ['soulseek']


def test_manual_search_handles_plugin_exception_gracefully(manual_search_client):
    """If one source's .search() raises, the endpoint emits a
    ``source_error`` event for it but other sources' results still come
    through. The whole stream doesn't fail."""
    client, ctx = manual_search_client
    plugins = ctx['plugins']

    import web_server

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
    msgs = _consume_ndjson(resp)

    error_events = [m for m in msgs if m.get('type') == 'source_error']
    assert any(m['source'] == 'soulseek' for m in error_events)
    assert any('network blip' in m.get('error', '') for m in error_events)

    candidates = _flatten_candidates(msgs)
    yt_results = [c for c in candidates if c.get('source') == 'youtube']
    assert len(yt_results) == 1
