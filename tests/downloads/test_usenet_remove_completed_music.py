"""Remove Completed (music): a usenet job leaves the client once its audio is out.

Music usenet grabs COPY their audio out of the client's folder (album grabs
into staging, track grabs into transfer) and never looked at the job again, so
every release stayed in the client's completed folder in full. The setting
``usenet_client.remove_completed`` (off / history / files, default off) now
takes the job out once its audio is copied: right after the staging copy for an
album grab, and from post-processing for a track grab, which finds the client
job through the grab row written at submit.
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


# ── an album grab, right after its staging copy ───────────────────────────────

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
    return outcome, adapter


def test_an_album_grab_is_removed_after_its_staging_copy(monkeypatch, tmp_path):
    outcome, adapter = _album(monkeypatch, tmp_path, 'files')
    assert outcome['success'] is True
    assert Path(outcome['files'][0]).read_bytes() == b'fLaC-test-payload'   # staged first
    assert adapter.removed == [('client-job', True)]


def test_an_album_grab_with_history_keeps_its_files(monkeypatch, tmp_path):
    outcome, adapter = _album(monkeypatch, tmp_path, 'history')
    assert outcome['success'] is True and adapter.removed == [('client-job', False)]


def test_an_album_grab_with_the_setting_off_stays_in_the_client(monkeypatch, tmp_path):
    outcome, adapter = _album(monkeypatch, tmp_path, 'off')
    assert outcome['success'] is True and adapter.removed == []


def test_an_album_grab_that_staged_nothing_keeps_its_job(monkeypatch, tmp_path):
    outcome, adapter = _album(monkeypatch, tmp_path, 'files', audio=False)
    assert outcome['success'] is False and adapter.removed == []


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
