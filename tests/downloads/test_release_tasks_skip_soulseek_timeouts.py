"""torrent / usenet grabs are not on the soulseek 90s clock (discord, Tostadaman).

    "It downloaded a torrent, deleted it, then downloaded it again. This it did
    4 times, ON A PRIVATE TRACKER!"

the monitor's queued / 0% / unknown-state rules give a transfer 90s (15s for an
artist-page album) and then cancel it with remove=True and retry, 3 times. on a
torrent that cancel is adapter.remove(delete_files=True): gone, data and all,
then grabbed again. a private tracker torrent sitting at 0% for 90s is normal.

torrents already have their own clock in the plugin (stall timeout + abandon or
pause, 6h deadline) and report a give-up as an errored row. so the monitor
leaves release tasks alone until they error, and the plugin doesn't touch the
client again for a row it already handled, or "pause" would get deleted anyway.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from core.download_plugins import torrent as tp
from core.downloads import monitor as dm


@pytest.fixture
def mon(monkeypatch):
    monkeypatch.setattr(dm, '_make_context_key', lambda u, f: f"{u}::{f}")
    monkeypatch.setattr(dm, '_orphaned_download_keys', set())
    return dm.WebUIDownloadMonitor()


class _Adapter:
    def __init__(self):
        self.removed = []
        self.paused = []

    async def remove(self, torrent_hash, delete_files=False):
        self.removed.append((torrent_hash, delete_files))
        return True

    async def pause(self, torrent_hash):
        self.paused.append(torrent_hash)
        return True


@pytest.fixture
def adapter(monkeypatch):
    a = _Adapter()
    monkeypatch.setattr(tp, 'get_active_torrent_adapter', lambda: a)
    return a


def _task(username='torrent', status='queued', album=False):
    return {
        'track_info': {'name': 'Track 1', 'is_album_download': album},
        'username': username,
        'filename': 'Some Album [FLAC]',
        'download_id': 'tor-1',
        'status': status,
        'batch_id': 'b1',
        'status_change_time': time.time(),
    }


def _live(state, username='torrent'):
    row = {'state': state, 'percentComplete': 0, 'bytesTransferred': 0, 'username': username}
    return {'download_id::tor-1': row, f'{username}::Some Album [FLAC]': row}


def _tick_twice(mon, task, live, gap):
    ops = []
    now = time.time()
    mon._should_retry_task('t1', task, live, now, ops)
    mon._should_retry_task('t1', task, live, now + gap, ops)
    return [op for op in ops if op[0] == 'cancel_download']


@pytest.mark.parametrize('username', ['torrent', 'usenet'])
@pytest.mark.parametrize('status,state', [
    ('queued', 'Queued'),
    ('downloading', 'InProgress, Downloading'),
    ('downloading', 'InProgress, Stalled'),
    ('downloading', 'Paused'),
])
def test_release_task_survives_the_90s_rules(mon, username, status, state):
    task = _task(username=username, status=status, album=True)
    # an hour at 0%, way past both the 15s album and 90s rules
    assert _tick_twice(mon, task, _live(state, username), 3600) == []
    assert task['status'] == status
    assert task['download_id'] == 'tor-1'
    assert task.get('stuck_retry_count') is None


def test_soulseek_still_gets_the_90s_rule(mon):
    # the guard is for release sources only, soulseek keeps its behaviour
    task = _task(username='somepeer', status='queued')
    cancels = _tick_twice(mon, task, _live('Queued', 'somepeer'), 91)
    assert cancels == [('cancel_download', 'tor-1', 'somepeer', 'queued_state_timeout')]


def test_errored_torrent_still_moves_to_the_next_candidate(mon):
    task = _task(status='downloading')
    ops = []
    mon._should_retry_task('t1', task, _live('Completed, Errored'), time.time(), ops)
    assert ('cancel_download', 'tor-1', 'torrent', 'errored_state_retry') in ops
    assert task['status'] == 'searching'
    assert 'torrent_Some Album [FLAC]' in task['used_sources']


def test_stall_pause_survives_the_monitor_retry(mon, adapter, monkeypatch):
    """the seam: plugin pauses a stalled torrent and errors the row, the monitor
    sees errored and cancels with remove=True. the paused torrent must stay."""
    plugin = tp.TorrentDownloadPlugin()
    plugin.active_downloads['tor-1'] = {'id': 'tor-1', 'torrent_hash': 'abc123',
                                        'state': 'InProgress, Stalled'}
    plugin._handle_stalled('tor-1', 'abc123', 'pause')
    assert adapter.paused == ['abc123']
    row_state = plugin.active_downloads['tor-1']['state']

    task = _task(status='downloading')
    ops = []
    mon._should_retry_task('t1', task, _live(row_state), time.time(), ops)
    cancel = next(op for op in ops if op[0] == 'cancel_download')
    asyncio.run(plugin.cancel_download(cancel[1], cancel[2], remove=True))

    assert adapter.removed == []
    assert 'tor-1' not in plugin.active_downloads


def test_user_cancel_of_a_running_torrent_still_removes_it(adapter):
    plugin = tp.TorrentDownloadPlugin()
    plugin.active_downloads['tor-1'] = {'id': 'tor-1', 'torrent_hash': 'abc123',
                                        'state': 'InProgress, Downloading'}
    asyncio.run(plugin.cancel_download('tor-1', 'torrent', remove=True))
    assert adapter.removed == [('abc123', True)]
