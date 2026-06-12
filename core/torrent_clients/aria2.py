"""Aria2 JSON-RPC adapter (Shdjfgatdif's request).

Auth model: aria2 uses a single RPC ``--rpc-secret`` token (no username). It is
passed as the FIRST positional param of every call as the string
``token:<secret>``. The RPC endpoint is ``<host>:6800/jsonrpc``.

A few aria2 quirks the adapter smooths over:
- the secret maps onto SoulSync's ``password`` field (aria2 has no username),
- ``addUri`` returns a GID — that's our torrent id,
- aria2 does NOT delete files on remove; for ``delete_files`` we read the file
  paths first and unlink them ourselves,
- a finished/errored download is removed via ``removeDownloadResult`` (force/Remove
  only work on active/waiting/paused), so the adapter picks the right one.

Reference: https://aria2.github.io/manual/en/html/aria2c.html#rpc-interface
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import List, Optional

import requests as http_requests

from config.settings import config_manager
from core.torrent_clients.base import TorrentStatus, normalize_client_url
from utils.logging_config import get_logger

logger = get_logger("torrent.aria2")


# aria2 download status → adapter-uniform state. 'active' is resolved separately
# (it covers both downloading and post-complete seeding for BT).
_ARIA2_STATE = {
    'waiting': 'queued',
    'paused': 'paused',
    'error': 'error',
    'removed': 'error',
    'complete': 'completed',
}


def _map_state(status: str, completed: int, total: int) -> str:
    if status == 'active':
        # A BT download that finished its payload keeps reporting 'active' while
        # it seeds — differentiate by progress.
        if total and completed >= total:
            return 'seeding'
        return 'downloading'
    return _ARIA2_STATE.get(status, 'error')


class Aria2Adapter:
    """Aria2 JSON-RPC adapter."""

    DEFAULT_TIMEOUT = 15
    _STATUS_KEYS = ['gid', 'status', 'totalLength', 'completedLength', 'downloadSpeed',
                    'uploadSpeed', 'connections', 'numSeeders', 'dir', 'files',
                    'errorMessage', 'bittorrent']

    def __init__(self) -> None:
        self._load_config()

    def _load_config(self) -> None:
        url = normalize_client_url(config_manager.get('torrent_client.url', ''))
        # aria2's RPC endpoint is /jsonrpc — append if the user pasted a bare host.
        if url and not url.rstrip('/').endswith('/jsonrpc'):
            url = url.rstrip('/') + '/jsonrpc'
        self._url = url
        # aria2 has no username; the RPC secret maps onto the password field.
        self._secret = config_manager.get('torrent_client.password', '') or ''
        self._category = config_manager.get('torrent_client.category', 'soulsync') or 'soulsync'
        self._save_path = config_manager.get('torrent_client.save_path', '') or ''

    def reload_settings(self) -> None:
        self._load_config()

    def is_configured(self) -> bool:
        return bool(self._url)

    def _params(self, *params) -> list:
        """token:<secret> must lead every call when a secret is configured."""
        return ([f"token:{self._secret}"] if self._secret else []) + list(params)

    def _rpc(self, method: str, *params):
        if not self._url:
            return None
        payload = {'jsonrpc': '2.0', 'id': 'soulsync', 'method': method,
                   'params': self._params(*params)}
        try:
            resp = http_requests.post(self._url, json=payload, timeout=self.DEFAULT_TIMEOUT)
            if not resp.ok:
                logger.warning("aria2 RPC %s returned HTTP %s", method, resp.status_code)
                return None
            data = resp.json()
            if isinstance(data, dict) and data.get('error'):
                logger.warning("aria2 RPC %s error: %s", method, data.get('error'))
                return None
            return data.get('result') if isinstance(data, dict) else None
        except Exception as e:
            logger.error("aria2 RPC %s failed: %s", method, e)
            return None

    async def check_connection(self) -> bool:
        if not self.is_configured():
            return False
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._rpc('aria2.getVersion') is not None)

    # ── add ──
    async def add_torrent(self, url_or_magnet: str, category: str = "soulsync",
                          save_path: Optional[str] = None) -> Optional[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._add_uri_sync, url_or_magnet, save_path)

    def _opts(self, save_path: Optional[str]) -> dict:
        opts: dict = {}
        if save_path or self._save_path:
            opts['dir'] = save_path or self._save_path
        return opts

    def _add_uri_sync(self, uri: str, save_path: Optional[str]) -> Optional[str]:
        result = self._rpc('aria2.addUri', [uri], self._opts(save_path))
        return str(result) if result else None   # GID

    async def add_torrent_file(self, file_bytes: bytes, category: str = "soulsync",
                               save_path: Optional[str] = None) -> Optional[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._add_file_sync, file_bytes, save_path)

    def _add_file_sync(self, file_bytes: bytes, save_path: Optional[str]) -> Optional[str]:
        b64 = base64.b64encode(file_bytes).decode('ascii')
        result = self._rpc('aria2.addTorrent', b64, [], self._opts(save_path))
        return str(result) if result else None

    # ── status ──
    async def get_status(self, torrent_id: str) -> Optional[TorrentStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_status_sync, torrent_id)

    def _get_status_sync(self, gid: str) -> Optional[TorrentStatus]:
        result = self._rpc('aria2.tellStatus', gid, self._STATUS_KEYS)
        return self._parse_status(result) if result else None

    async def get_all(self) -> List[TorrentStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_all_sync)

    def _get_all_sync(self) -> List[TorrentStatus]:
        active = self._rpc('aria2.tellActive', self._STATUS_KEYS) or []
        waiting = self._rpc('aria2.tellWaiting', 0, 1000, self._STATUS_KEYS) or []
        stopped = self._rpc('aria2.tellStopped', 0, 1000, self._STATUS_KEYS) or []
        return [self._parse_status(i) for i in (list(active) + list(waiting) + list(stopped))]

    def _parse_status(self, item: dict) -> TorrentStatus:
        total = int(item.get('totalLength') or 0)
        completed = int(item.get('completedLength') or 0)
        status = item.get('status') or 'error'
        progress = (completed / total) if total > 0 else 0.0
        # Name: BT info name, else the first file's basename.
        name = ''
        bt = item.get('bittorrent')
        if isinstance(bt, dict):
            name = (bt.get('info') or {}).get('name') or ''
        files = item.get('files') or []
        if not name and files:
            name = os.path.basename((files[0] or {}).get('path') or '')
        file_paths = [f.get('path') for f in files if f.get('path')] or None
        return TorrentStatus(
            id=str(item.get('gid') or ''),
            name=name,
            state=_map_state(status, completed, total),
            progress=progress,
            size=total,
            downloaded=completed,
            download_speed=int(item.get('downloadSpeed') or 0),
            upload_speed=int(item.get('uploadSpeed') or 0),
            seeders=int(item.get('numSeeders') or 0),
            peers=int(item.get('connections') or 0),
            save_path=item.get('dir'),
            files=file_paths,
            error=item.get('errorMessage') or None,
        )

    # ── remove / pause / resume ──
    async def remove(self, torrent_id: str, delete_files: bool = False) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._remove_sync, torrent_id, delete_files)

    def _remove_sync(self, gid: str, delete_files: bool) -> bool:
        st = self._rpc('aria2.tellStatus', gid, ['status', 'files']) or {}
        status = st.get('status')
        paths = ([f.get('path') for f in (st.get('files') or []) if f.get('path')]
                 if delete_files else [])
        if status in ('active', 'waiting', 'paused'):
            ok = self._rpc('aria2.forceRemove', gid) is not None
            self._rpc('aria2.removeDownloadResult', gid)   # clear the result row
        else:
            # complete / error / removed → force/Remove would error; clear the row.
            ok = self._rpc('aria2.removeDownloadResult', gid) is not None
        if delete_files:
            for p in paths:
                try:
                    if p and os.path.isfile(p):
                        os.remove(p)
                except Exception:  # noqa: S110 — partial data delete is best-effort
                    pass
        return ok

    async def pause(self, torrent_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._rpc('aria2.pause', torrent_id) is not None)

    async def resume(self, torrent_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._rpc('aria2.unpause', torrent_id) is not None)
