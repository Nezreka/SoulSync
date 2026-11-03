"""Remove Completed (music): a usenet job leaves the client once its audio is out.

Music usenet grabs COPY their audio out of the client's folder (album grabs
into staging, track grabs into transfer) and never looked at the job again, so
every release stayed in the client's completed folder in full. The setting
``usenet_client.remove_completed`` (off / history / files, default off) now
takes the job out once its audio is IMPORTED: for an album grab when its batch
finishes with every track imported (the staged copies go with the batch, so the
client's folder is the only copy left of a track that failed), and for a track
grab from post-processing once the track imported, finding the client job
through the grab row written at submit.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.download_plugins.usenet as usenet
from core.acquisition.grabs import ensure_acquisition_grabs_schema, record_grab, update_grab
from core.download_plugins.usenet import UsenetDownloadPlugin
from core.prowlarr_client import ProwlarrClient


class _Adapter:
    def __init__(self, result=True):
        self.removed = []
        self.result = result

    def is_configured(self):
        return True

    async def remove(self, job_id, delete_files=False):
        self.removed.append((job_id, delete_files))
        return self.result


@pytest.fixture
def grabs_db(tmp_path, monkeypatch):
    path = tmp_path / 'grabs.db'

    def conn():
        c = sqlite3.connect(str(path))
        ensure_acquisition_grabs_schema(c)
        return c
    monkeypatch.setattr(usenet, '_grabs_conn', conn)
    return conn


def _grab(conn_factory, download_id, *, source='usenet', job='SABnzbd_nzo_1', title='Artist - Album'):
    c = conn_factory()
    record_grab(c, download_id, source, title=title)
    if job:
        update_grab(c, download_id, external_job_id=job)
    c.commit()
    c.close()


def _mode(monkeypatch, mode):
    monkeypatch.setattr(usenet, '_remove_completed_mode', lambda: mode)


# ── the setting ───────────────────────────────────────────────────────────────

def test_the_music_setting_defaults_off():
    from core.settings import config_manager
    assert config_manager._get_default_config()['usenet_client']['remove_completed'] == 'off'


def test_an_unknown_or_missing_value_reads_as_off(monkeypatch):
    from core.settings import config_manager
    for raw, want in (('files', 'files'), ('History', 'history'), (None, 'off'), ('bogus', 'off')):
        monkeypatch.setattr(config_manager, 'get', lambda key, default=None, _r=raw: _r)
        assert usenet._remove_completed_mode() == want


# ── a track grab, found through its grab row ──────────────────────────────────

def test_a_track_grab_is_removed_per_the_setting(grabs_db, monkeypatch):
    _grab(grabs_db, 'dl-1')
    _grab(grabs_db, 'dl-2', job='SABnzbd_nzo_2')
    adapter = _Adapter()
    monkeypatch.setattr(usenet, 'get_active_usenet_adapter', lambda: adapter)
    monkeypatch.setattr(usenet, 'run_async', asyncio.run)
    _mode(monkeypatch, 'history')
    assert usenet.remove_imported_usenet_job('dl-1') is True
    _mode(monkeypatch, 'files')
    assert usenet.remove_imported_usenet_job('dl-2') is True
    assert adapter.removed == [('SABnzbd_nzo_1', False), ('SABnzbd_nzo_2', True)]


def test_off_never_touches_the_grab_or_the_client(grabs_db, monkeypatch):
    _grab(grabs_db, 'dl-1')
    adapter = _Adapter()
    monkeypatch.setattr(usenet, 'get_active_usenet_adapter', lambda: adapter)
    _mode(monkeypatch, 'off')
    assert usenet.remove_imported_usenet_job('dl-1') is False
    assert adapter.removed == []


def test_no_grab_another_source_or_no_job_id_is_left_alone(grabs_db, monkeypatch):
    _grab(grabs_db, 'tor-1', source='torrent', job='abcdef')
    _grab(grabs_db, 'dl-nojob', job=None)
    adapter = _Adapter()
    monkeypatch.setattr(usenet, 'get_active_usenet_adapter', lambda: adapter)
    monkeypatch.setattr(usenet, 'run_async', asyncio.run)
    _mode(monkeypatch, 'files')
    for download_id in ('missing', 'tor-1', 'dl-nojob', ''):
        assert usenet.remove_imported_usenet_job(download_id) is False
    assert adapter.removed == []


def test_no_database_means_no_removal(monkeypatch):
    monkeypatch.setattr(usenet, '_grabs_conn', lambda: None)
    _mode(monkeypatch, 'files')
    assert usenet.remove_imported_usenet_job('dl-1') is False


# ── an album grab: the plugin hands the job to its batch ──────────────────────

def _release(indexer=1):
    return ProwlarrClient()._parse_result({
        'guid': f'indexer-{indexer}-release', 'indexerId': indexer,
        'indexer': f'Indexer {indexer}', 'protocol': 'usenet',
        'title': 'Example_Artist-Example_Album-WEB-FLAC-2023-GROUPA',
        'size': 300_000_000, 'downloadUrl': f'https://indexer-{indexer}.invalid/release',
        'categories': [{'id': 3040}],
    })


def _album(monkeypatch, tmp_path, mode, *, audio=True):
    import core.download_plugins.torrent as torrent
    plugin = UsenetDownloadPlugin()
    source = tmp_path / 'release'
    source.mkdir()
    if audio:
        (source / 'Song.flac').write_bytes(b'fLaC-test-payload')
    (source / 'release.nfo').write_text('nfo')

    async def search(*args, **kwargs):
        return [_release()]

    async def add(url):
        return 'client-job'

    adapter = _Adapter()
    adapter.add_nzb = add
    monkeypatch.setattr(plugin, 'is_configured', lambda: True)
    monkeypatch.setattr(usenet, 'get_active_usenet_adapter', lambda: adapter)
    monkeypatch.setattr(torrent, '_prowlarr_search_query_with_variants', search)
    monkeypatch.setattr(usenet, 'run_async', asyncio.run)
    monkeypatch.setattr(usenet, 'poll_album_download', lambda **kwargs: str(source))
    monkeypatch.setattr(usenet, 'profile_allowed_formats', lambda *args: None)
    monkeypatch.setattr(usenet, 'profile_quality_targets', lambda *args: ([], True))
    monkeypatch.setattr(usenet, '_grabs_conn', lambda: None)
    _mode(monkeypatch, mode)
    outcome = plugin.download_album_to_staging('Example Album', 'Example Artist',
                                               str(tmp_path / 'staging'))
    return outcome, adapter, source


def test_an_album_grab_stays_in_the_client_until_its_batch_finishes(monkeypatch, tmp_path):
    outcome, adapter, source = _album(monkeypatch, tmp_path, 'files')
    assert outcome['success'] is True
    assert Path(outcome['files'][0]).read_bytes() == b'fLaC-test-payload'
    assert adapter.removed == [] and source.exists()
    assert outcome['client_job_id'] == 'client-job'
    assert outcome['client_job_label'] == 'Example_Artist-Example_Album-WEB-FLAC-2023-GROUPA'


def test_an_album_grab_that_staged_nothing_names_no_job(monkeypatch, tmp_path):
    outcome, adapter, _ = _album(monkeypatch, tmp_path, 'files', audio=False)
    assert outcome['success'] is False and 'client_job_id' not in outcome


def test_the_batch_records_the_staged_job(monkeypatch):
    from core.downloads import album_bundle_dispatch as dispatch
    fields = {}

    class _State:
        def update_fields(self, batch_id, update):
            fields.update(update)

        def mark_failed(self, *a):
            raise AssertionError('not failed')

    class _Plugin:
        def download_album_to_staging(self, album, artist, staging, emit, **kw):
            return {'success': True, 'files': ['a.flac'], 'client_job_id': 'nzo_9',
                    'client_job_label': 'Release'}

    dispatch.try_dispatch(
        batch_id='b1', is_album=True, album_context={'name': 'Album'}, artist_context={'name': 'Artist'},
        config_get=lambda key, default=None: 'usenet' if key == 'download_source.mode' else default,
        plugin_resolver=lambda mode: _Plugin(), state=_State())
    assert fields['album_bundle_client_job'] == 'nzo_9'
    assert fields['album_bundle_client_job_label'] == 'Release'


# ── an album grab: removed only when its batch imported every track ───────────

def _batch(statuses, *, source='usenet', job='nzo_9'):
    queue = [f't{i}' for i in range(len(statuses))]
    snapshot = {tid: {'status': st} for tid, st in zip(queue, statuses)}
    batch = {'album_bundle_source': source, 'album_bundle_client_job': job,
             'album_bundle_client_job_label': 'Release', 'queue': queue}
    return batch, snapshot


def _album_job(monkeypatch):
    handed = []
    monkeypatch.setattr(usenet, 'remove_imported_album_job',
                        lambda job_id, label='': handed.append((job_id, label)) or True)
    return handed


def test_a_batch_that_imported_every_track_removes_the_job(monkeypatch):
    from core.downloads import lifecycle
    handed = _album_job(monkeypatch)
    batch, snap = _batch(['completed', 'completed', 'already_owned'])
    assert lifecycle._remove_completed_album_bundle('b1', batch, snap) is True
    assert handed == [('nzo_9', 'Release')]


@pytest.mark.parametrize('bad', ['failed', 'not_found', 'cancelled', 'post_processing'])
def test_one_track_not_imported_keeps_the_job(monkeypatch, bad):
    from core.downloads import lifecycle
    handed = _album_job(monkeypatch)
    batch, snap = _batch(['completed', bad, 'completed'])
    assert lifecycle._remove_completed_album_bundle('b1', batch, snap) is False
    assert handed == []


def test_a_missing_task_another_source_or_no_job_keeps_it(monkeypatch):
    from core.downloads import lifecycle
    handed = _album_job(monkeypatch)
    batch, snap = _batch(['completed', 'completed'])
    snap.pop('t1')
    assert lifecycle._remove_completed_album_bundle('b1', batch, snap) is False
    for kwargs in ({'source': 'torrent'}, {'job': None}):
        batch, snap = _batch(['completed'], **kwargs)
        assert lifecycle._remove_completed_album_bundle('b1', batch, snap) is False
    batch, snap = _batch([])
    assert lifecycle._remove_completed_album_bundle('b1', batch, snap) is False
    assert handed == []


def test_batch_completion_runs_it_after_the_staging_cleanup(monkeypatch):
    from core.downloads import lifecycle
    order = []
    monkeypatch.setattr(lifecycle, '_cleanup_private_album_bundle_staging',
                        lambda *a: order.append('staging'))
    monkeypatch.setattr(lifecycle, '_remove_completed_album_bundle',
                        lambda batch_id, batch, snap: order.append(('remove', batch_id, snap)))
    deps = SimpleNamespace(config_manager=SimpleNamespace(get=lambda key, default=None: default))
    try:
        lifecycle._run_batch_completion_side_effects(
            'b1', {'queue': []}, deps, {'tasks_snapshot': {'t0': {'status': 'completed'}}},
            tag='[t]')
    except Exception:  # noqa: BLE001 - later side effects need more wiring than this test gives
        pass
    assert order[:2] == ['staging', ('remove', 'b1', {'t0': {'status': 'completed'}})]


def test_the_album_job_follows_the_setting(monkeypatch):
    adapter = _Adapter()
    monkeypatch.setattr(usenet, 'get_active_usenet_adapter', lambda: adapter)
    monkeypatch.setattr(usenet, 'run_async', asyncio.run)
    _mode(monkeypatch, 'off')
    assert usenet.remove_imported_album_job('nzo_9') is False
    _mode(monkeypatch, 'history')
    assert usenet.remove_imported_album_job('nzo_9', label='Release') is True
    assert usenet.remove_imported_album_job('') is False
    assert adapter.removed == [('nzo_9', False)]


# ── files: the music side deletes the job's own folder ────────────────────────

def test_files_deletes_the_reported_job_folder_but_never_a_protected_one(monkeypatch, tmp_path):
    job = tmp_path / 'complete' / 'Example.Release.1'
    job.mkdir(parents=True)
    (job / 'Song.flac').write_bytes(b'x')

    class _Reporting(_Adapter):
        async def get_status(self, job_id):
            return SimpleNamespace(name='Example.Release', save_path=str(job), state='completed')

    adapter = _Reporting()
    monkeypatch.setattr(usenet, 'get_active_usenet_adapter', lambda: adapter)
    monkeypatch.setattr(usenet, 'run_async', asyncio.run)
    _mode(monkeypatch, 'files')
    monkeypatch.setattr(usenet, '_music_protected_roots', lambda: [str(job)])
    assert usenet.remove_imported_album_job('nzo_9') is True
    assert job.exists()
    monkeypatch.setattr(usenet, '_music_protected_roots', lambda: [str(tmp_path / 'complete')])
    assert usenet.remove_imported_album_job('nzo_9') is True
    assert not job.exists() and (tmp_path / 'complete').exists()


def test_the_protected_roots_come_from_the_settings(monkeypatch):
    from core.settings import config_manager
    values = {'soulseek.download_path': '/dl', 'soulseek.transfer_path': '/tr',
              'import.staging_path': '/st', 'download_source.album_bundle_staging_path': '/ab',
              'download_source.usenet_download_path': '/client/usenet',
              'library.music_paths': ['/music', '']}
    monkeypatch.setattr(config_manager, 'get', lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(usenet, 'resolve_reported_save_path', lambda p: p.replace('/client', '/here'))
    roots = usenet._music_protected_roots()
    for want in ('/dl', '/tr', '/st', '/ab', '/client/usenet', '/here/usenet', '/music'):
        assert want in roots


# ── the settings page ─────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[2]


def test_the_music_setting_is_on_the_settings_page_and_saved():
    html = (_ROOT / 'webui' / 'index.html').read_text(encoding='utf-8')
    i = html.index('id="music-usenet-remove-completed"')
    group = html.rfind('<div class="form-group"', 0, i)
    assert 'data-music-only' not in html[group:i] and '(music)' in html[group:i]
    for value in ('value="off"', 'value="history"', 'value="files"'):
        assert value in html[i:i + 800]
    js = (_ROOT / 'webui' / 'static' / 'settings.js').read_text(encoding='utf-8')
    assert "remove_completed: _cfgStr('music-usenet-remove-completed'" in js
    assert "settings.usenet_client?.remove_completed" in js


def test_both_sides_hand_their_library_folders_to_the_folder_check(monkeypatch):
    """the music transfer/library folders and the video library folders may
    never hold a job folder that files mode deletes"""
    from core.downloads import remove_completed as rc
    from core.download_plugins import usenet
    seen = {}
    monkeypatch.setattr(rc, "remove_imported_job",
                        lambda *a, **k: seen.setdefault("libraries", list(k.get("libraries") or [])) or True)
    from core.settings import config_manager
    real = config_manager.get
    monkeypatch.setattr(config_manager, "get", lambda key, default=None: (
        "/music/transfer" if key == "soulseek.transfer_path"
        else ["/music/library"] if key == "library.music_paths" else real(key, default)))
    usenet._remove_job("j1", "files", adapter=object())
    assert set(seen["libraries"]) == {"/music/transfer", "/music/library"}

    import inspect
    from core.video import download_monitor
    assert "libraries=_library_roots()" in inspect.getsource(download_monitor._remove_completed_usenet)
