"""Track requests search albums only through release sources, using real HTTP contracts."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.download_orchestrator import DownloadOrchestrator
from core.download_plugins.registry import DownloadPluginRegistry, PluginSpec
from core.download_plugins.torrent import TorrentDownloadPlugin
from core.download_plugins.usenet import UsenetDownloadPlugin
from core.download_plugins.types import TrackResult
from core.downloads import task_worker, validation
from core.matching_engine import MusicMatchingEngine
from core.prowlarr_client import ProwlarrClient
from core.runtime_state import download_batches, download_tasks


class _LocalSource:
    def __init__(self, name, rows=()):
        self.name, self.rows, self.queries = name, list(rows), []

    def is_configured(self):
        return True

    async def search(self, query, timeout=None, progress_callback=None):
        self.queries.append(query)
        return self.rows, []


def _track(source, title='Money', artist='Pink Floyd'):
    return TrackResult(username=source, filename=f'{artist} - {title}.flac',
                       size=30_000_000, bitrate=None, duration=383000, quality='flac',
                       free_upload_slots=1, upload_speed=0, queue_length=0,
                       artist=artist, title=title, album='The Dark Side of the Moon')


def _release(title, guid='album', protocol='usenet', categories=(3040,)):
    # ReleaseResource deliberately has no normalized artist/album/codec fields.
    return {'guid': guid, 'title': title, 'indexerId': 1, 'indexer': 'Local indexer',
            'protocol': protocol, 'downloadUrl': f'https://nzb.invalid/{guid}',
            'size': 350_000_000, 'publishDate': '2026-10-07T00:00:00Z',
            'categories': [{'id': cat, 'name': 'Audio'} for cat in categories]}


@pytest.fixture()
def local_http(monkeypatch):
    from core import prowlarr_client
    calls, answers, throttle_calls = [], {}, []
    monkeypatch.setattr('core.library2.download_catalogue.hydrate_download_album', lambda context: None)

    def get(url, headers=None, params=None, timeout=None):
        if url.endswith('/indexer'):
            body = [{'id': 1, 'name': 'Local indexer', 'protocol': 'usenet',
                     'enable': True, 'privacy': 'private', 'priority': 25}]
        else:
            assert url == 'http://prowlarr.invalid/api/v1/search'
            pairs = list(params)
            assert {key for key, _ in pairs} <= {'query', 'type', 'limit', 'categories', 'indexerIds'}
            assert ('type', 'search') in pairs
            query = dict(pairs)['query']
            calls.append(query)
            body = answers.get(query, [])
            if callable(body):
                body = body()
        return SimpleNamespace(ok=True, status_code=200, json=lambda: body)

    monkeypatch.setattr(prowlarr_client.http_requests, 'get', get)
    monkeypatch.setattr(prowlarr_client, '_INDEXER_PRIORITY_CACHE', None)
    monkeypatch.setattr('core.prowlarr_throttle.wait_for_slot',
                        lambda *args, **kwargs: throttle_calls.append(kwargs) or True)
    monkeypatch.setattr('core.download_plugins.torrent._parse_indexer_id_filter', lambda: [])
    client = ProwlarrClient.__new__(ProwlarrClient)
    client._url, client._api_key = 'http://prowlarr.invalid', 'fixture-key'
    return client, answers, calls, throttle_calls


def _worker(monkeypatch, plugins, order, *, title='Money', album='The Dark Side of the Moon',
            queries=None, mode='hybrid', quality_order=False, fallback_enabled=True):
    download_tasks.clear()
    download_batches.clear()
    registry = DownloadPluginRegistry()
    for name, plugin in plugins.items():
        registry.register(PluginSpec(name, lambda p=plugin: p, name))
    orch = DownloadOrchestrator(registry=registry)
    orch.mode, orch.hybrid_order = mode, order
    engine = MusicMatchingEngine()
    monkeypatch.setattr(engine, 'generate_download_queries', lambda track: queries or [f'Pink Floyd {title}'])
    monkeypatch.setattr(validation, 'matching_engine', engine)
    monkeypatch.setattr(validation, 'download_orchestrator', orch)
    monkeypatch.setattr('core.quality.selection.load_profile_by_id', lambda _id=None: {
        'ranked_targets': [{'format': 'flac'}], 'fallback_enabled': fallback_enabled,
        'search_mode': 'priority', 'rank_candidates_by_quality': quality_order})
    picked = []

    def attempt(_task_id, rows, _track, _batch, **kwargs):
        picked.extend(rows[:1])
        return True

    noop = lambda *args, **kwargs: None
    deps = task_worker.TaskWorkerDeps(orch, engine, asyncio.run,
        lambda *args: False, noop, lambda *args: False,
        validation.get_valid_candidates, attempt, noop, noop)
    download_tasks['request'] = {'status': 'pending', 'track_info': {
        'id': 'track', 'name': title, 'artists': ['Pink Floyd'],
        'album': {'name': album}, 'duration_ms': 383000}}
    task_worker.download_track_worker('request', None, deps)
    download_tasks.clear()
    download_batches.clear()
    return picked


def _plugin(monkeypatch, client, protocol='usenet'):
    cls = UsenetDownloadPlugin if protocol == 'usenet' else TorrentDownloadPlugin
    plugin = cls.__new__(cls)
    plugin._prowlarr = client
    monkeypatch.setattr(plugin, 'is_configured', lambda: True)
    return plugin


def test_usenet_album_is_chosen_before_later_hifi_after_track_miss(monkeypatch, local_http):
    client, answers, calls, throttle = local_http
    answers['Pink Floyd The Dark Side of the Moon'] = [_release('Pink Floyd - The Dark Side of the Moon [FLAC]')]
    hifi = _LocalSource('hifi', [_track('hifi')])
    picked = _worker(monkeypatch, {'usenet': _plugin(monkeypatch, client), 'hifi': hifi}, ['usenet', 'hifi'])
    assert [r.username for r in picked] == ['usenet']
    assert hifi.queries == []
    assert calls == ['Pink Floyd Money', 'Pink Floyd The Dark Side of the Moon']
    assert len(throttle) == 2


def test_irrelevant_track_hits_do_not_hide_the_requested_album(monkeypatch, local_http):
    client, answers, calls, _ = local_http
    answers['Pink Floyd Money'] = [_release('Other Artist - Money [FLAC]', guid='wrong')]
    answers['Pink Floyd The Dark Side of the Moon'] = [_release('Pink Floyd - The Dark Side of the Moon [FLAC]')]
    picked = _worker(monkeypatch, {'usenet': _plugin(monkeypatch, client)}, ['usenet'])
    assert [r.album for r in picked] == ['The Dark Side of the Moon']
    assert calls.count('Pink Floyd The Dark Side of the Moon') == 1


def test_rejected_earlier_source_reaches_later_usenet_album(monkeypatch, local_http):
    client, answers, calls, _ = local_http
    answers['Pink Floyd The Dark Side of the Moon'] = [_release('Pink Floyd - The Dark Side of the Moon [FLAC]')]
    earlier = _LocalSource('hifi', [_track('hifi', artist='Other Artist')])
    picked = _worker(monkeypatch, {'hifi': earlier, 'usenet': _plugin(monkeypatch, client)}, ['hifi', 'usenet'])
    assert [r.username for r in picked] == ['usenet']
    assert calls == ['Pink Floyd Money', 'Pink Floyd The Dark Side of the Moon']
    assert 'Pink Floyd The Dark Side of the Moon' not in earlier.queries


def test_album_searches_are_bounded_and_do_not_reach_soulseek_or_streaming(monkeypatch, local_http):
    client, _, calls, _ = local_http
    peer = _LocalSource('soulseek')
    hifi = _LocalSource('hifi')
    picked = _worker(monkeypatch, {'usenet': _plugin(monkeypatch, client), 'soulseek': peer, 'hifi': hifi},
                     ['usenet', 'soulseek', 'hifi'])
    assert picked == []
    assert calls.count('Pink Floyd The Dark Side of the Moon') == 1
    assert 'Pink Floyd The Dark Side of the Moon' not in peer.queries + hifi.queries


def test_direct_track_stays_before_comparable_album_candidate(monkeypatch, local_http):
    client, answers, _, _ = local_http
    answers['Pink Floyd Money'] = [_release('Pink Floyd - Money [FLAC]', guid='single')]
    answers['Pink Floyd The Dark Side of the Moon'] = [_release('Pink Floyd - The Dark Side of the Moon [FLAC]')]
    picked = _worker(monkeypatch, {'usenet': _plugin(monkeypatch, client)}, ['usenet'])
    assert [r.title for r in picked] == ['Money']


def test_priority_quality_ordering_still_reaches_later_release_source(monkeypatch, local_http):
    client, answers, _, _ = local_http
    answers['Pink Floyd The Dark Side of the Moon'] = [_release('Pink Floyd - The Dark Side of the Moon [FLAC]')]
    earlier = _LocalSource('hifi', [_track('hifi', artist='Other Artist')])
    picked = _worker(monkeypatch, {'hifi': earlier, 'usenet': _plugin(monkeypatch, client)},
                     ['hifi', 'usenet'], quality_order=True)
    assert [r.username for r in picked] == ['usenet']


def test_later_release_source_sees_track_queries_after_the_first_two(monkeypatch, local_http):
    client, answers, calls, _ = local_http
    answers['Pink Floyd Money'] = [_release('Pink Floyd - Money [FLAC]', guid='single')]
    earlier = _LocalSource('hifi', [_track('hifi', artist='Other Artist')])
    picked = _worker(monkeypatch, {'hifi': earlier, 'usenet': _plugin(monkeypatch, client)},
                     ['hifi', 'usenet'], queries=['Unhelpful first', 'Unhelpful second', 'Pink Floyd Money'])
    assert [r.username for r in picked] == ['usenet']
    assert calls.count('Pink Floyd The Dark Side of the Moon') == 1


@pytest.mark.parametrize('protocol', ['usenet', 'torrent'])
def test_single_source_track_request_can_retrieve_its_album(monkeypatch, local_http, protocol):
    client, answers, calls, _ = local_http
    answers['Pink Floyd The Dark Side of the Moon'] = [_release(
        'Pink Floyd - The Dark Side of the Moon [FLAC]', protocol=protocol)]
    picked = _worker(monkeypatch, {protocol: _plugin(monkeypatch, client, protocol)},
                     [protocol], mode=protocol)
    assert [r.username for r in picked] == [protocol]
    assert calls == ['Pink Floyd Money', 'Pink Floyd The Dark Side of the Moon']


def test_same_release_returned_by_track_and_album_queries_is_projected_once(monkeypatch, local_http):
    from core.downloads.track_hint import track_hint_context

    client, answers, _, _ = local_http
    row = _release('Pink Floyd - The Dark Side of the Moon [FLAC]')
    answers['Pink Floyd Money'] = [row]
    answers['Pink Floyd The Dark Side of the Moon'] = [row]
    with track_hint_context({'title': 'Money', 'artist': 'Pink Floyd', 'album': 'The Dark Side of the Moon'}):
        tracks, albums = asyncio.run(_plugin(monkeypatch, client).search('Pink Floyd Money'))
    assert len(tracks) == len(albums) == 1


@pytest.mark.parametrize('album', ['', 'Unknown Album', 'Money'])
def test_missing_or_track_identical_album_has_no_extra_query(monkeypatch, local_http, album):
    client, _, calls, _ = local_http
    _worker(monkeypatch, {'usenet': _plugin(monkeypatch, client)}, ['usenet'], album=album)
    assert calls == ['Pink Floyd Money', 'Money Pink']


def test_album_query_is_cached_when_it_was_already_the_main_query(monkeypatch, local_http):
    from core.downloads.track_hint import track_hint_context

    client, answers, calls, _ = local_http
    answers['Pink Floyd The Dark Side of the Moon'] = [_release('Pink Floyd - The Dark Side of the Moon [FLAC]')]
    plugin = _plugin(monkeypatch, client)
    with track_hint_context({'title': 'Money', 'artist': 'Pink Floyd', 'album': 'The Dark Side of the Moon'}):
        first, _ = asyncio.run(plugin.search('Pink Floyd The Dark Side of the Moon'))
        second, _ = asyncio.run(plugin.search('Pink Floyd Money'))
    assert calls.count('Pink Floyd The Dark Side of the Moon') == 1
    assert first[0].filename == second[0].filename


def test_album_hydration_uses_shared_io_pool_and_task_context_once(monkeypatch, local_http):
    import threading
    from core.downloads.track_hint import current_track_hint, track_hint_context

    client, _, _, _ = local_http
    context, calls = {'album_name': 'Album'}, []
    hint = {'title': 'Money', 'artist': 'Pink Floyd', 'album': 'Album', 'catalogue_context': context}
    monkeypatch.setattr('core.library2.download_catalogue.hydrate_download_album',
                        lambda value: calls.append((value, current_track_hint(), threading.current_thread().name)))
    with track_hint_context(hint):
        for _ in range(2):
            asyncio.run(_plugin(monkeypatch, client).search('Pink Floyd Money'))
    assert len(calls) == 1
    assert calls[0][:2] == (context, hint)
    assert calls[0][2].startswith('soulsync-slow-io')


@pytest.mark.parametrize('release_title', [
    'Other Artist - The Dark Side of the Moon [FLAC]',
    'Pink Floyd - The Dark Side of the Moon Live [FLAC]',
])
def test_album_query_keeps_normal_artist_and_version_gates(monkeypatch, local_http, release_title):
    client, answers, _, _ = local_http
    answers['Pink Floyd The Dark Side of the Moon'] = [_release(release_title)]
    hifi = _LocalSource('hifi', [_track('hifi')])
    picked = _worker(monkeypatch, {'usenet': _plugin(monkeypatch, client), 'hifi': hifi}, ['usenet', 'hifi'])
    assert [r.username for r in picked] == ['hifi']


def test_cancelled_task_does_not_grab_a_later_release_source_result(monkeypatch, local_http):
    client, answers, _, _ = local_http

    def cancel_during_search():
        download_tasks['request']['status'] = 'cancelled'
        return [_release('Pink Floyd - The Dark Side of the Moon [FLAC]')]

    answers['Pink Floyd The Dark Side of the Moon'] = cancel_during_search
    earlier = _LocalSource('hifi', [_track('hifi', artist='Other Artist')])
    picked = _worker(monkeypatch, {'hifi': earlier, 'usenet': _plugin(monkeypatch, client)}, ['hifi', 'usenet'])
    assert picked == []


@pytest.mark.parametrize('release_title', [
    'Pink Floyd - The Dark Side of the Moon [ALAC]',
    'Pink Floyd - The Dark Side of the Moon [MP3 320kbps]',
    'Pink Floyd - The Dark Side of the Moon [MP3 + FLAC]',
])
def test_album_candidates_keep_the_initiating_strict_quality_gate(monkeypatch, local_http, release_title):
    client, answers, _, _ = local_http
    answers['Pink Floyd The Dark Side of the Moon'] = [_release(release_title)]
    hifi = _LocalSource('hifi', [_track('hifi')])
    picked = _worker(monkeypatch, {'usenet': _plugin(monkeypatch, client), 'hifi': hifi},
                     ['usenet', 'hifi'], fallback_enabled=False)
    assert [r.username for r in picked] == ['hifi']
