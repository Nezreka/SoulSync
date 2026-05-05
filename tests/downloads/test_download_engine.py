"""Tests for the DownloadEngine skeleton (Phase B).

Pinning the engine's state-storage contract: add/update/remove,
per-source iteration, find-by-id, plugin registration, lock-held
mutations vs lock-released reads. Future phases (C/D/E/F) bolt
behavior on top of this surface — these tests stay green and act
as the regression net while behavior moves in.
"""

from __future__ import annotations

import threading

import pytest

from core.download_engine import DownloadEngine


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def test_register_plugin_stores_under_source_name():
    engine = DownloadEngine()
    plugin = object()
    engine.register_plugin('soulseek', plugin)
    assert engine.get_plugin('soulseek') is plugin
    assert 'soulseek' in engine.registered_sources()


def test_get_plugin_returns_none_for_unknown_source():
    engine = DownloadEngine()
    assert engine.get_plugin('made_up') is None


def test_register_plugin_overwrites_on_duplicate(caplog):
    """Re-registering under the same name overwrites and warns. Not a
    common path but useful so test fixtures that build a fresh engine
    can swap a mock plugin in without setup gymnastics."""
    engine = DownloadEngine()
    first = object()
    second = object()
    engine.register_plugin('soulseek', first)
    engine.register_plugin('soulseek', second)
    assert engine.get_plugin('soulseek') is second


# ---------------------------------------------------------------------------
# Active-download state — add / get / update / remove
# ---------------------------------------------------------------------------


def test_add_record_inserts_under_composite_key():
    engine = DownloadEngine()
    engine.add_record('youtube', 'dl-1', {'state': 'Initializing', 'progress': 0.0})

    rec = engine.get_record('youtube', 'dl-1')
    assert rec is not None
    assert rec['state'] == 'Initializing'
    assert rec['progress'] == 0.0


def test_get_record_returns_shallow_copy():
    """Mutating the returned dict must NOT affect engine state.
    Engine reads should be safe to hold / iterate without locks."""
    engine = DownloadEngine()
    engine.add_record('youtube', 'dl-1', {'state': 'Initializing'})

    rec = engine.get_record('youtube', 'dl-1')
    rec['state'] = 'TamperedByCaller'

    # Engine state still has the original.
    fresh = engine.get_record('youtube', 'dl-1')
    assert fresh['state'] == 'Initializing'


def test_update_record_applies_partial_patch():
    engine = DownloadEngine()
    engine.add_record('tidal', 'dl-2', {'state': 'Initializing', 'progress': 0.0,
                                         'file_path': None})

    engine.update_record('tidal', 'dl-2', {'state': 'Completed, Succeeded',
                                            'progress': 100.0,
                                            'file_path': '/tmp/song.flac'})

    rec = engine.get_record('tidal', 'dl-2')
    assert rec['state'] == 'Completed, Succeeded'
    assert rec['progress'] == 100.0
    assert rec['file_path'] == '/tmp/song.flac'


def test_update_record_is_noop_when_record_removed():
    """If a record was removed (e.g. user cancelled mid-download),
    the worker thread's late update is silently dropped — never
    raises. Mirrors the per-client `if download_id in active_downloads`
    guard pattern that's all over the existing clients."""
    engine = DownloadEngine()
    engine.add_record('tidal', 'dl-2', {'state': 'Initializing'})
    engine.remove_record('tidal', 'dl-2')

    # Should not raise.
    engine.update_record('tidal', 'dl-2', {'state': 'Completed, Succeeded'})

    assert engine.get_record('tidal', 'dl-2') is None


def test_remove_record_returns_removed_record():
    engine = DownloadEngine()
    engine.add_record('qobuz', 'dl-3', {'state': 'InProgress'})

    removed = engine.remove_record('qobuz', 'dl-3')
    assert removed is not None
    assert removed['state'] == 'InProgress'
    assert engine.get_record('qobuz', 'dl-3') is None


def test_remove_record_returns_none_when_missing():
    engine = DownloadEngine()
    assert engine.remove_record('qobuz', 'never-existed') is None


# ---------------------------------------------------------------------------
# Iteration
# ---------------------------------------------------------------------------


def test_iter_records_for_source_filters_correctly():
    engine = DownloadEngine()
    engine.add_record('youtube', 'yt-1', {'title': 'A'})
    engine.add_record('youtube', 'yt-2', {'title': 'B'})
    engine.add_record('tidal', 'td-1', {'title': 'C'})

    yt_records = list(engine.iter_records_for_source('youtube'))
    assert len(yt_records) == 2
    assert {r['title'] for r in yt_records} == {'A', 'B'}

    td_records = list(engine.iter_records_for_source('tidal'))
    assert len(td_records) == 1
    assert td_records[0]['title'] == 'C'


def test_iter_yields_shallow_copies():
    """Iteration returns COPIES — caller can hold the list and mutate
    each record without affecting engine state. Important: future
    Phase B3's `get_all_downloads` will iterate then build
    DownloadStatus objects from the snapshots."""
    engine = DownloadEngine()
    engine.add_record('youtube', 'yt-1', {'title': 'A'})

    snapshot = list(engine.iter_records_for_source('youtube'))
    snapshot[0]['title'] = 'TAMPERED'

    fresh = engine.get_record('youtube', 'yt-1')
    assert fresh['title'] == 'A'


# ---------------------------------------------------------------------------
# Thread safety — basic concurrent-mutation smoke
# ---------------------------------------------------------------------------


def test_concurrent_adds_dont_lose_records():
    """Hammer the engine with concurrent add_record from multiple
    threads. With proper locking, every record lands in state.
    Future Phase C BackgroundDownloadWorker spawns N threads doing
    exactly this kind of mutation."""
    engine = DownloadEngine()

    def add_records(source, base):
        for i in range(50):
            engine.add_record(source, f'{base}-{i}', {'i': i})

    threads = [
        threading.Thread(target=add_records, args=(f'src-{n}', f'dl-{n}'))
        for n in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = sum(
        1
        for n in range(4)
        for _ in engine.iter_records_for_source(f'src-{n}')
    )
    assert total == 4 * 50  # 200 records, none lost


# ---------------------------------------------------------------------------
# Cross-source query dispatch (Phase B2)
# ---------------------------------------------------------------------------


def _run_async(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakePlugin:
    """Minimal plugin double for engine query tests. Exposes the
    methods engine.get_all_downloads / get_download_status /
    cancel_download / clear_all_completed_downloads call."""

    def __init__(self, name, configured=True, downloads=None,
                 cancel_result=True, clear_result=True):
        self.name = name
        self._configured = configured
        self._downloads = downloads or []
        self._cancel_result = cancel_result
        self._clear_result = clear_result
        self.cancel_calls = []
        self.clear_calls = 0

    def is_configured(self):
        return self._configured

    async def get_all_downloads(self):
        return list(self._downloads)

    async def get_download_status(self, download_id):
        for d in self._downloads:
            if getattr(d, 'id', None) == download_id:
                return d
        return None

    async def cancel_download(self, download_id, source_hint, remove):
        self.cancel_calls.append((download_id, source_hint, remove))
        return self._cancel_result

    async def clear_all_completed_downloads(self):
        self.clear_calls += 1
        return self._clear_result


class _FakeStatus:
    def __init__(self, id, source):
        self.id = id
        self.source = source


def test_engine_get_all_downloads_aggregates_across_plugins():
    """Engine concatenates every plugin's get_all_downloads output —
    same behavior as the legacy orchestrator."""
    engine = DownloadEngine()
    yt_plugin = _FakePlugin('youtube', downloads=[_FakeStatus('yt-1', 'youtube')])
    td_plugin = _FakePlugin('tidal', downloads=[_FakeStatus('td-1', 'tidal'),
                                                  _FakeStatus('td-2', 'tidal')])
    engine.register_plugin('youtube', yt_plugin)
    engine.register_plugin('tidal', td_plugin)

    result = _run_async(engine.get_all_downloads())
    assert len(result) == 3
    assert {r.id for r in result} == {'yt-1', 'td-1', 'td-2'}


def test_engine_get_all_downloads_swallows_per_plugin_exceptions():
    """One plugin throwing must NOT take down the whole list — same
    defensive behavior as the legacy orchestrator (matched by
    `try ... except: pass` on every iteration)."""
    engine = DownloadEngine()

    class _BrokenPlugin:
        async def get_all_downloads(self):
            raise RuntimeError("boom")

    yt_plugin = _FakePlugin('youtube', downloads=[_FakeStatus('yt-1', 'youtube')])
    engine.register_plugin('broken', _BrokenPlugin())
    engine.register_plugin('youtube', yt_plugin)

    result = _run_async(engine.get_all_downloads())
    assert [r.id for r in result] == ['yt-1']


def test_engine_get_download_status_returns_first_match():
    engine = DownloadEngine()
    yt_plugin = _FakePlugin('youtube', downloads=[_FakeStatus('shared', 'youtube')])
    td_plugin = _FakePlugin('tidal', downloads=[])
    engine.register_plugin('youtube', yt_plugin)
    engine.register_plugin('tidal', td_plugin)

    result = _run_async(engine.get_download_status('shared'))
    assert result is not None
    assert result.id == 'shared'


def test_engine_cancel_routes_streaming_source_directly():
    """When source_hint is a known streaming-source name (not
    'soulseek'), engine routes the cancel to that specific plugin
    only — doesn't ask every other plugin first."""
    engine = DownloadEngine()
    yt_plugin = _FakePlugin('youtube')
    td_plugin = _FakePlugin('tidal')
    engine.register_plugin('youtube', yt_plugin)
    engine.register_plugin('tidal', td_plugin)

    _run_async(engine.cancel_download('dl-1', 'tidal', remove=False))
    assert yt_plugin.cancel_calls == []
    assert td_plugin.cancel_calls == [('dl-1', 'tidal', False)]


def test_engine_cancel_routes_unknown_source_hint_to_soulseek():
    """A username that's NOT in the plugin registry is a real
    Soulseek peer name — route to the soulseek plugin."""
    engine = DownloadEngine()
    sl_plugin = _FakePlugin('soulseek')
    yt_plugin = _FakePlugin('youtube')
    engine.register_plugin('soulseek', sl_plugin)
    engine.register_plugin('youtube', yt_plugin)

    _run_async(engine.cancel_download('dl-1', 'random_peer_username', remove=False))
    assert sl_plugin.cancel_calls == [('dl-1', 'random_peer_username', False)]
    assert yt_plugin.cancel_calls == []


def test_engine_cancel_falls_back_to_iterating_all_plugins_without_hint():
    """No source hint → ask every plugin until one accepts the
    cancel (returns True). Mirrors legacy orchestrator behavior."""
    engine = DownloadEngine()
    yt_plugin = _FakePlugin('youtube', cancel_result=False)
    td_plugin = _FakePlugin('tidal', cancel_result=True)
    engine.register_plugin('youtube', yt_plugin)
    engine.register_plugin('tidal', td_plugin)

    result = _run_async(engine.cancel_download('dl-1', None, remove=False))
    assert result is True
    # Both plugins were asked; tidal accepted.
    assert len(yt_plugin.cancel_calls) == 1
    assert len(td_plugin.cancel_calls) == 1


def test_engine_clear_all_skips_unconfigured_plugins():
    """Unconfigured plugins are silently skipped (no API call, no
    error) — matches legacy orchestrator's defensive handling."""
    engine = DownloadEngine()
    configured = _FakePlugin('youtube', configured=True, clear_result=True)
    unconfigured = _FakePlugin('tidal', configured=False)
    engine.register_plugin('youtube', configured)
    engine.register_plugin('tidal', unconfigured)

    result = _run_async(engine.clear_all_completed_downloads())
    assert result is True
    assert configured.clear_calls == 1
    assert unconfigured.clear_calls == 0


def test_engine_clear_all_returns_false_when_any_configured_plugin_fails():
    engine = DownloadEngine()
    failing = _FakePlugin('youtube', configured=True, clear_result=False)
    engine.register_plugin('youtube', failing)

    result = _run_async(engine.clear_all_completed_downloads())
    assert result is False


# ---------------------------------------------------------------------------
# Hybrid fallback (Phase F)
# ---------------------------------------------------------------------------


class _FakeSearchPlugin:
    def __init__(self, name, configured=True, search_result=None, raises=None):
        self.name = name
        self._configured = configured
        self._search_result = search_result if search_result is not None else ([], [])
        self._raises = raises
        self.search_calls = 0

    def is_configured(self):
        return self._configured

    async def search(self, query, timeout=None, progress_callback=None):
        self.search_calls += 1
        if self._raises:
            raise self._raises
        return self._search_result


class _FakeDownloadPlugin:
    def __init__(self, name, configured=True, download_result=None, raises=None):
        self.name = name
        self._configured = configured
        self._download_result = download_result
        self._raises = raises
        self.download_calls = []

    def is_configured(self):
        return self._configured

    async def download(self, username, filename, file_size):
        self.download_calls.append((username, filename, file_size))
        if self._raises:
            raise self._raises
        return self._download_result


def test_search_with_fallback_returns_first_non_empty_result():
    engine = DownloadEngine()
    yt = _FakeSearchPlugin('youtube', search_result=([], []))
    td = _FakeSearchPlugin('tidal', search_result=(['track1'], []))
    qz = _FakeSearchPlugin('qobuz', search_result=(['track2'], []))
    engine.register_plugin('youtube', yt)
    engine.register_plugin('tidal', td)
    engine.register_plugin('qobuz', qz)

    tracks, _ = _run_async(engine.search_with_fallback('q', ['youtube', 'tidal', 'qobuz']))
    assert tracks == ['track1']
    # Tidal short-circuits — qobuz never queried.
    assert yt.search_calls == 1
    assert td.search_calls == 1
    assert qz.search_calls == 0


def test_search_with_fallback_skips_unconfigured_plugins():
    engine = DownloadEngine()
    yt = _FakeSearchPlugin('youtube', configured=False)
    td = _FakeSearchPlugin('tidal', configured=True, search_result=(['hit'], []))
    engine.register_plugin('youtube', yt)
    engine.register_plugin('tidal', td)

    tracks, _ = _run_async(engine.search_with_fallback('q', ['youtube', 'tidal']))
    assert tracks == ['hit']
    assert yt.search_calls == 0  # skipped


def test_search_with_fallback_continues_after_per_source_exception():
    engine = DownloadEngine()
    yt = _FakeSearchPlugin('youtube', raises=RuntimeError("yt down"))
    td = _FakeSearchPlugin('tidal', search_result=(['fallback-hit'], []))
    engine.register_plugin('youtube', yt)
    engine.register_plugin('tidal', td)

    tracks, _ = _run_async(engine.search_with_fallback('q', ['youtube', 'tidal']))
    assert tracks == ['fallback-hit']


def test_search_with_fallback_returns_empty_when_chain_exhausted():
    engine = DownloadEngine()
    yt = _FakeSearchPlugin('youtube', search_result=([], []))
    td = _FakeSearchPlugin('tidal', search_result=([], []))
    engine.register_plugin('youtube', yt)
    engine.register_plugin('tidal', td)

    tracks, _ = _run_async(engine.search_with_fallback('q', ['youtube', 'tidal']))
    assert tracks == []
    assert yt.search_calls == 1
    assert td.search_calls == 1


def test_download_with_fallback_returns_first_accepted_download_id():
    """Phase F bug fix: legacy hybrid download routed to one source
    via username hint with no retry. Engine now falls through chain."""
    engine = DownloadEngine()
    yt = _FakeDownloadPlugin('youtube', download_result=None)  # refuses
    td = _FakeDownloadPlugin('tidal', download_result='td-id')
    engine.register_plugin('youtube', yt)
    engine.register_plugin('tidal', td)

    result = _run_async(engine.download_with_fallback(
        'youtube', 'v||t', 0, ['youtube', 'tidal'],
    ))
    assert result == 'td-id'
    assert len(yt.download_calls) == 1  # tried first
    assert len(td.download_calls) == 1  # took over


def test_download_with_fallback_promotes_username_hint_to_head():
    """A username hint that matches a source-chain entry tries that
    source FIRST regardless of declared chain order."""
    engine = DownloadEngine()
    yt = _FakeDownloadPlugin('youtube', download_result='yt-id')
    td = _FakeDownloadPlugin('tidal', download_result='td-id')
    engine.register_plugin('youtube', yt)
    engine.register_plugin('tidal', td)

    # Chain says tidal-first, but username hint promotes youtube.
    result = _run_async(engine.download_with_fallback(
        'youtube', 'v||t', 0, ['tidal', 'youtube'],
    ))
    assert result == 'yt-id'
    assert len(yt.download_calls) == 1
    assert len(td.download_calls) == 0  # never reached


def test_download_with_fallback_returns_none_when_all_refuse():
    engine = DownloadEngine()
    yt = _FakeDownloadPlugin('youtube', download_result=None)
    td = _FakeDownloadPlugin('tidal', download_result=None)
    engine.register_plugin('youtube', yt)
    engine.register_plugin('tidal', td)

    result = _run_async(engine.download_with_fallback(
        'youtube', 'v||t', 0, ['youtube', 'tidal'],
    ))
    assert result is None
    assert len(yt.download_calls) == 1
    assert len(td.download_calls) == 1


def test_download_with_fallback_continues_past_exception():
    engine = DownloadEngine()
    yt = _FakeDownloadPlugin('youtube', raises=RuntimeError("yt died"))
    td = _FakeDownloadPlugin('tidal', download_result='td-id')
    engine.register_plugin('youtube', yt)
    engine.register_plugin('tidal', td)

    result = _run_async(engine.download_with_fallback(
        'youtube', 'v||t', 0, ['youtube', 'tidal'],
    ))
    assert result == 'td-id'


# ---------------------------------------------------------------------------
# Cin bug 1: alias resolution on cancel_download
# ---------------------------------------------------------------------------


def test_register_plugin_records_aliases():
    """Aliases passed to register_plugin resolve to the canonical plugin
    via get_plugin. Cin caught engine.cancel_download routing 'deezer_dl'
    to soulseek because the alias never made it to the engine."""
    engine = DownloadEngine()
    deezer = _FakePlugin('deezer')
    engine.register_plugin('deezer', deezer, aliases=('deezer_dl',))

    assert engine.get_plugin('deezer') is deezer
    assert engine.get_plugin('deezer_dl') is deezer


def test_cancel_download_resolves_alias_to_canonical_plugin():
    """The legacy 'deezer_dl' source_hint must route to the deezer
    plugin, not fall through to soulseek. This was Cin's bug 1 —
    cancel of a Deezer download silently no-op'd."""
    engine = DownloadEngine()
    soulseek = _FakePlugin('soulseek')
    deezer = _FakePlugin('deezer')
    engine.register_plugin('soulseek', soulseek)
    engine.register_plugin('deezer', deezer, aliases=('deezer_dl',))

    _run_async(engine.cancel_download('dl-1', 'deezer_dl', remove=False))
    assert deezer.cancel_calls == [('dl-1', 'deezer_dl', False)]
    assert soulseek.cancel_calls == []


# ---------------------------------------------------------------------------
# Cin bug 3: atomic update_record_unless_state
# ---------------------------------------------------------------------------


def test_update_record_unless_state_applies_when_state_not_blocked():
    engine = DownloadEngine()
    engine.add_record('youtube', 'dl-1', {'state': 'InProgress, Downloading'})

    applied = engine.update_record_unless_state(
        'youtube', 'dl-1',
        {'state': 'Completed, Succeeded', 'progress': 100.0},
        skip_if_state_in=('Cancelled',),
    )
    assert applied is True
    assert engine.get_record('youtube', 'dl-1')['state'] == 'Completed, Succeeded'
    assert engine.get_record('youtube', 'dl-1')['progress'] == 100.0


def test_update_record_unless_state_skips_when_state_blocked():
    """A worker-thread terminal write must NOT clobber a Cancelled
    state set by the user. Returns False so caller knows the patch
    was skipped."""
    engine = DownloadEngine()
    engine.add_record('youtube', 'dl-1', {'state': 'Cancelled'})

    applied = engine.update_record_unless_state(
        'youtube', 'dl-1',
        {'state': 'Completed, Succeeded'},
        skip_if_state_in=('Cancelled',),
    )
    assert applied is False
    assert engine.get_record('youtube', 'dl-1')['state'] == 'Cancelled'


def test_update_record_unless_state_returns_false_for_missing_record():
    engine = DownloadEngine()
    applied = engine.update_record_unless_state(
        'youtube', 'never-existed',
        {'state': 'Completed, Succeeded'},
        skip_if_state_in=('Cancelled',),
    )
    assert applied is False
