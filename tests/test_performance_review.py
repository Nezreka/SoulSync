"""Behavioral regressions found reviewing the five unpushed #1245 commits."""
import threading
from types import SimpleNamespace

import pytest


def test_repeated_search_timeouts_do_not_accumulate_unbounded_workers(monkeypatch):
    from core.metadata import multi_source_search as m
    release = threading.Event()
    condition = threading.Condition()
    active = 0
    def blocked(name, *args, **kwargs):
        nonlocal active
        with condition:
            active += 1
            condition.notify_all()
        try:
            assert release.wait(5)
            return name, [], []
        finally:
            with condition:
                active -= 1
                condition.notify_all()
    monkeypatch.setattr(m, '_search_one_source', blocked)
    try:
        for i in range(12):
            m.search_all_sources(m.TrackQuery('song', 'artist'), [(str(i), None)], timeout_seconds=0.02)
        with condition:
            assert active <= 8, f'{active} provider workers still running after 12 expired requests'
    finally:
        release.set()
        with condition:
            assert condition.wait_for(lambda: active == 0, timeout=3)


def test_video_status_has_short_read_timeout(monkeypatch):
    from core.video import sources as m
    import plexapi.server
    m.invalidate_video_source_cache()
    monkeypatch.setattr(m, '_vdb', lambda: None)
    monkeypatch.setattr(m, 'resolve_video_server', lambda db=None: 'plex')
    monkeypatch.setattr(m, '_load_selection', lambda: {})
    monkeypatch.setattr(m, 'video_plex_config', lambda db=None: {'base_url': 'http://example.test', 'token': 'token'})
    seen = []
    def connect(*args, timeout=None):
        seen.append(timeout)
        read_timeout = timeout[1] if isinstance(timeout, tuple) else timeout
        assert read_timeout <= 8, 'Status handshake can wait 120 seconds for HTTP headers'
        return SimpleNamespace(library=SimpleNamespace(sections=lambda: []), activities=lambda: [])
    monkeypatch.setattr(plexapi.server, 'PlexServer', connect)
    try:
        assert m.video_server_scan_in_progress() is False
        assert seen
    finally:
        m.invalidate_video_source_cache()


@pytest.mark.parametrize('video', [False, True])
def test_plex_token_suffix_changes_reconnect(monkeypatch, video):
    import plexapi.server
    if video:
        from core.video import sources as m
        invalidate, connect = m.invalidate_video_source_cache, m._build_source
        monkeypatch.setattr(m, '_vdb', lambda: None)
        monkeypatch.setattr(m, 'resolve_video_server', lambda db=None: 'plex')
        config_name = 'video_plex_config'
    else:
        import core.server_activity as m
        invalidate, connect = m.invalidate_plex_server_cache, m._plex_server
        config_name = '_plex_config'
    cfg = {'base_url': 'http://example.test', 'token': 'abcdef-old'}
    monkeypatch.setattr(m, config_name, lambda db=None: cfg)
    seen = []
    def build(url, token, **kwargs):
        seen.append(token)
        return SimpleNamespace()
    monkeypatch.setattr(plexapi.server, 'PlexServer', build)
    invalidate()
    try:
        connect()
        cfg['token'] = 'abcdef-new'
        connect()
        assert seen == ['abcdef-old', 'abcdef-new']
    finally:
        invalidate()


def test_recovery_does_not_start_for_already_requested_cancellation(monkeypatch):
    from core.downloads import status as m
    calls = []
    monkeypatch.setattr(m, '_recovery_pool', SimpleNamespace(submit=lambda fn: calls.append(fn)))
    task = {'status': 'downloading', 'filename': 'song.flac', 'cancel_requested': True}
    try:
        with m.tasks_lock:
            m._schedule_file_recovery('review-cancel', 'batch', task, None)
        assert not calls
    finally:
        if calls:
            m._recovery_pending.pop('review-cancel', None)
            m._recovery_slots.release()


def test_recovery_submission_error_preserves_a_newer_attempt(monkeypatch):
    from core.downloads import status as m
    monkeypatch.setattr(m, '_recovery_pool', SimpleNamespace(submit=lambda fn: fn()))
    task = dict(status='downloading', filename='old.flac', status_change_time=0)
    def submit(*args):
        task.update(filename='new.flac', status='post_processing', status_change_time=123)
        raise RuntimeError('old submission failed after another attempt took over')
    deps = m.StatusDeps(SimpleNamespace(get=lambda k, d=None: d), lambda p: p,
                        lambda *args: ('/old.flac', 'downloads'), lambda u, f: f, submit, lambda: {})
    m.download_tasks['review-retry'] = task
    try:
        m._schedule_file_recovery('review-retry', 'batch', task, deps)
        assert task['status'] == 'post_processing'
        assert task['status_change_time'] == 123
    finally:
        m.download_tasks.pop('review-retry', None)


@pytest.mark.parametrize('video', [False, True])
def test_offline_cache_ttl_starts_when_connection_finishes(monkeypatch, video):
    import plexapi.server
    if video:
        from core.video import sources as m
        invalidate, connect = m.invalidate_video_source_cache, m._build_source
        monkeypatch.setattr(m, '_vdb', lambda: None)
        monkeypatch.setattr(m, 'resolve_video_server', lambda db=None: 'plex')
        config_name = 'video_plex_config'
    else:
        import core.server_activity as m
        invalidate, connect = m.invalidate_plex_server_cache, m._plex_server
        config_name = '_plex_config'
    monkeypatch.setattr(m, config_name, lambda db=None: {'base_url': 'http://example.test', 'token': 'token'})
    clock = [100.0]
    monkeypatch.setattr(m.time, 'time', lambda: clock[0])
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        clock[0] += 70
        raise ConnectionError('slow failure')
    monkeypatch.setattr(plexapi.server, 'PlexServer', fail)
    invalidate()
    try:
        assert connect() is None
        assert connect() is None
        assert len(calls) == 1
    finally:
        invalidate()


def test_expired_metadata_search_does_not_start_fallback_queries(monkeypatch):
    from core.metadata import multi_source_search as m
    clock = [10.0]
    monkeypatch.setattr(m.time, 'monotonic', lambda: clock[0])
    queries = []
    def search(query, **kwargs):
        queries.append(query)
        clock[0] = 20.0
        return []
    m._search_one_source('deezer', SimpleNamespace(search_tracks=search), m.TrackQuery('Song', 'Artist'), 'Song', deadline=15.0)
    assert len(queries) == 1


def test_reused_plex_client_does_not_keep_old_refreshing_flag():
    from core.video.sources import PlexVideoSource
    state = {'refreshing': True}
    class CachedLibrary:
        def __init__(self):
            self.cached = None
        def reload(self):
            self.cached = None
        def sections(self):
            if self.cached is None:
                self.cached = [SimpleNamespace(type='movie', title='Movies', refreshing=state['refreshing'])]
            return self.cached
    src = PlexVideoSource(SimpleNamespace(library=CachedLibrary(), activities=lambda: []), movies_lib='Movies')
    assert src.is_scanning('movie') is True
    state['refreshing'] = False
    assert src.is_scanning('movie') is False
