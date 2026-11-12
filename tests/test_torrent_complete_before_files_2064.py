"""#2064: a torrent the client already holds complete is adopted and reads
"seeding" on the first poll. The poll wrote "Completed, Succeeded" on the row
before ``_finalize_download`` had collected the audio files, the download
monitor saw the completed row in that gap, found no files, and failed the
track with "No matching audio file found in torrent release", on every retry.

A poll no longer announces completion. Only the finalizer does, and it sets
the files and the state together."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.download_plugins import torrent as torrent_mod
from core.download_plugins.torrent import TorrentDownloadPlugin, _poll_display_state
from core.torrent_clients.base import TorrentStatus


class _Adapter:
    def __init__(self, state):
        self.state = state

    def is_configured(self):
        return True

    async def get_status(self, torrent_hash):
        return TorrentStatus(id=torrent_hash, name='Some Album', state=self.state, progress=1.0,
                             size=100, downloaded=100, download_speed=0, upload_speed=0,
                             save_path='/downloads/Some Album')


@pytest.fixture
def plugin():
    with patch('core.download_plugins.torrent.ProwlarrClient'):
        yield TorrentDownloadPlugin()


def _row(plugin, download_id='dl-1'):
    plugin.active_downloads[download_id] = {
        'id': download_id, 'filename': 'x||Some Album', 'username': 'torrent',
        'state': 'Initializing', 'progress': 0.0, 'size': 0, 'transferred': 0, 'speed': 0,
        'file_path': None, 'audio_files': [], 'torrent_hash': None, 'error': None,
    }
    return plugin.active_downloads[download_id]


@pytest.mark.parametrize('client_state', ['seeding', 'completed'])
def test_a_finished_torrent_is_not_completed_until_its_files_are_collected(plugin, client_state):
    row = _row(plugin)
    seen = {}

    def finalize(download_id, save_path, torrent_name=None):
        # what the download monitor could read at this moment
        seen.update(state=row['state'], audio_files=list(row['audio_files']), save_path=save_path)

    async def add(*_args, **_kwargs):
        return 'hash123'

    with patch.object(torrent_mod, 'get_active_torrent_adapter', return_value=_Adapter(client_state)), \
            patch('core.torrent_clients.base.add_torrent_smart', add), \
            patch.object(plugin, '_finalize_download', finalize):
        plugin._download_thread('dl-1', 'magnet:?xt=urn:btih:hash123', 'Some Album')

    assert seen['save_path'] == '/downloads/Some Album'
    assert seen['audio_files'] == []
    assert not seen['state'].startswith('Completed'), seen['state']
    assert row['progress'] == 100.0


def test_only_complete_states_are_held_back():
    assert _poll_display_state('seeding') == 'InProgress, Downloading'
    assert _poll_display_state('completed') == 'InProgress, Downloading'
    assert _poll_display_state('downloading') == 'InProgress, Downloading'
    assert _poll_display_state('stalled') == 'InProgress, Stalled'
    assert _poll_display_state('queued') == 'Queued'
    assert _poll_display_state('error') == 'Completed, Errored'


def test_usenet_polls_through_the_same_gate():
    from pathlib import Path
    src = Path(torrent_mod.__file__).with_name('usenet.py').read_text(encoding='utf-8')
    assert "row['state'] = _poll_display_state(status.state)" in src
    assert "_adapter_state_to_display(status.state)" not in src
