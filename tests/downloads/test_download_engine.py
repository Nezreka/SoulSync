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


def test_iter_all_records_yields_source_paired_with_record():
    engine = DownloadEngine()
    engine.add_record('youtube', 'yt-1', {'title': 'A'})
    engine.add_record('tidal', 'td-1', {'title': 'B'})

    pairs = list(engine.iter_all_records())
    assert len(pairs) == 2
    sources = {source for source, _ in pairs}
    titles = {record['title'] for _, record in pairs}
    assert sources == {'youtube', 'tidal'}
    assert titles == {'A', 'B'}


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
# find_record — id-only lookup
# ---------------------------------------------------------------------------


def test_find_record_returns_source_and_copy():
    engine = DownloadEngine()
    engine.add_record('youtube', 'shared-id-shape', {'title': 'A'})

    result = engine.find_record('shared-id-shape')
    assert result is not None
    source, record = result
    assert source == 'youtube'
    assert record['title'] == 'A'


def test_find_record_returns_none_for_unknown_id():
    engine = DownloadEngine()
    engine.add_record('youtube', 'yt-1', {})
    assert engine.find_record('nonexistent') is None


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

    total = sum(1 for _ in engine.iter_all_records())
    assert total == 4 * 50  # 200 records, none lost
