"""#seed-mode toggle — a completed torrent grab is routed by seed_mode:
'client' pushes share limits into the torrent client; 'soulsync' (default)
records the grab for the sweep. A failed client push falls back to recording.
"""

from __future__ import annotations

import pytest

from core.download_plugins.torrent import TorrentDownloadPlugin


@pytest.fixture()
def plugin(monkeypatch):
    # bare instance — skip the heavy __init__
    p = TorrentDownloadPlugin.__new__(TorrentDownloadPlugin)
    recorded = []
    monkeypatch.setattr(p, '_record_seed_grab',
                        lambda h, t: recorded.append((h, t)), raising=False)
    p._recorded = recorded
    return p


def _cfg(monkeypatch, mode='soulsync', ratio=0, hours=0):
    vals = {
        'torrent_client.seed_mode': mode,
        'torrent_client.seed_ratio_goal': ratio,
        'torrent_client.seed_time_goal_hours': hours,
    }
    from config.settings import config_manager
    monkeypatch.setattr(config_manager, 'get', lambda k, d=None: vals.get(k, d))


def _patch_push(monkeypatch, result):
    calls = []
    def _push(adapter, h, r, t):
        calls.append((h, r, t))
        return result
    monkeypatch.setattr('core.torrent_clients.share_limits.push_seed_goal', _push)
    monkeypatch.setattr('core.torrent_clients.get_active_adapter', lambda: object())
    return calls


def test_soulsync_mode_records_for_sweep(plugin, monkeypatch):
    _cfg(monkeypatch, mode='soulsync', ratio=2, hours=0)
    push = _patch_push(monkeypatch, True)
    plugin._apply_seed_policy('HASH', 'Album')
    assert plugin._recorded == [('HASH', 'Album')]
    assert push == []   # never pushed to client in soulsync mode


def test_client_mode_pushes_and_does_not_record(plugin, monkeypatch):
    _cfg(monkeypatch, mode='client', ratio=0, hours=408)
    push = _patch_push(monkeypatch, True)
    plugin._apply_seed_policy('HASH', 'Album')
    assert push == [('HASH', 0, 408)]
    assert plugin._recorded == []   # client enforces → nothing for the sweep


def test_client_mode_push_failure_falls_back_to_record(plugin, monkeypatch):
    _cfg(monkeypatch, mode='client', ratio=0, hours=408)
    push = _patch_push(monkeypatch, False)   # client rejected / unsupported
    plugin._apply_seed_policy('HASH', 'Album')
    assert push == [('HASH', 0, 408)]
    assert plugin._recorded == [('HASH', 'Album')]   # fell back to the sweep


def test_client_mode_no_goal_records(plugin, monkeypatch):
    _cfg(monkeypatch, mode='client', ratio=0, hours=0)
    push = _patch_push(monkeypatch, True)
    plugin._apply_seed_policy('HASH', 'Album')
    assert push == []                       # no goal → nothing to push
    assert plugin._recorded == [('HASH', 'Album')]
