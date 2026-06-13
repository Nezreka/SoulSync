"""Aria2 JSON-RPC adapter (Shdjfgatdif's request) — state mapping, token-prefixed
params, the /jsonrpc URL fixup, status parsing, and registry wiring. No network."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.torrent_clients import adapter_for_type
from core.torrent_clients.aria2 import Aria2Adapter, _map_state


def _adapter(url='http://nas:6800', secret='sekret', save_path=''):
    cfg = {'torrent_client.url': url, 'torrent_client.password': secret,
           'torrent_client.category': 'soulsync', 'torrent_client.save_path': save_path}
    with patch('core.torrent_clients.aria2.config_manager') as cm:
        cm.get.side_effect = lambda k, d=None: cfg.get(k, d)
        return Aria2Adapter()


# ── registry ──
def test_registered_in_factory():
    a = adapter_for_type('aria2')
    assert isinstance(a, Aria2Adapter)


# ── state mapping (aria2 native → adapter-uniform) ──
def test_state_map():
    assert _map_state('waiting', 0, 100) == 'queued'
    assert _map_state('paused', 0, 100) == 'paused'
    assert _map_state('error', 0, 100) == 'error'
    assert _map_state('complete', 100, 100) == 'completed'
    assert _map_state('active', 40, 100) == 'downloading'
    assert _map_state('active', 100, 100) == 'seeding'      # finished payload, still seeding
    assert _map_state('removed', 0, 100) == 'error'


# ── URL fixup + token-prefixed params ──
def test_jsonrpc_appended_to_bare_host():
    a = _adapter(url='http://nas:6800')
    assert a._url == 'http://nas:6800/jsonrpc'


def test_jsonrpc_not_double_appended():
    a = _adapter(url='http://nas:6800/jsonrpc')
    assert a._url == 'http://nas:6800/jsonrpc'


def test_secret_leads_params_as_token():
    a = _adapter(secret='sekret')
    assert a._params('gid123', ['status']) == ['token:sekret', 'gid123', ['status']]


def test_no_token_when_no_secret():
    a = _adapter(secret='')
    assert a._params('gid123') == ['gid123']


def test_is_configured():
    assert _adapter(url='http://nas:6800').is_configured() is True
    assert _adapter(url='').is_configured() is False


# ── status parse ──
def test_parse_status_torrent():
    a = _adapter()
    item = {
        'gid': 'abc123', 'status': 'active',
        'totalLength': '1000', 'completedLength': '250',
        'downloadSpeed': '500', 'uploadSpeed': '10',
        'connections': '7', 'numSeeders': '3', 'dir': '/downloads',
        'files': [{'path': '/downloads/Album/01.flac'}],
        'bittorrent': {'info': {'name': 'Some Album'}},
    }
    s = a._parse_status(item)
    assert s.id == 'abc123' and s.name == 'Some Album'
    assert s.state == 'downloading'
    assert s.size == 1000 and s.downloaded == 250
    assert abs(s.progress - 0.25) < 1e-9
    assert s.download_speed == 500 and s.peers == 7 and s.seeders == 3
    assert s.save_path == '/downloads' and s.files == ['/downloads/Album/01.flac']


def test_parse_status_name_falls_back_to_file_basename():
    a = _adapter()
    s = a._parse_status({'gid': 'g', 'status': 'active', 'totalLength': '0',
                         'completedLength': '0', 'files': [{'path': '/d/song.mp3'}]})
    assert s.name == 'song.mp3'
    assert s.progress == 0.0           # no division by zero when totalLength is 0
