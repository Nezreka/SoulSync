"""Transmission RPC adapter.

Auth model: Transmission uses HTTP Basic auth + an
``X-Transmission-Session-Id`` header. The session ID rotates and is
returned on every 409 response — the adapter caches the latest value
and replays the original request transparently.

Reference: https://github.com/transmission/transmission/blob/main/docs/rpc-spec.md
"""

from __future__ import annotations

import asyncio
import base64
import threading
from typing import List, Optional

import requests as http_requests

from config.settings import config_manager
from core.torrent_clients.base import TorrentStatus
from utils.logging_config import get_logger

logger = get_logger("torrent.transmission")


# Transmission RPC status codes. Defined as numeric constants in the
# RPC spec — converted to the adapter-uniform string set here.
_TRANSMISSION_STATUS = {
    0: "paused",
    1: "queued",        # queued to check files
    2: "queued",        # checking files
    3: "queued",        # queued to download
    4: "downloading",
    5: "queued",        # queued to seed
    6: "seeding",
}


def _map_state(status_code: int, percent_done: float) -> str:
    base = _TRANSMISSION_STATUS.get(status_code, "error")
    # Transmission reports 'paused' (0) for both never-started and
    # fully-downloaded-but-not-seeding. Differentiate by progress.
    if status_code == 0 and percent_done >= 1.0:
        return "completed"
    return base


class TransmissionAdapter:
    """Transmission RPC adapter (transmission-rpc protocol v17+)."""

    DEFAULT_TIMEOUT = 15

    def __init__(self) -> None:
        self._session_id: Optional[str] = None
        self._session_id_lock = threading.Lock()
        self._load_config()

    def _load_config(self) -> None:
        url = (config_manager.get('torrent_client.url', '') or '').rstrip('/')
        # Transmission's RPC endpoint is always /transmission/rpc — if the
        # user pasted a bare host URL, append it. If they pasted the full
        # /transmission/rpc URL, leave it.
        if url and not url.endswith('/transmission/rpc'):
            if '/transmission' not in url:
                url = f"{url}/transmission/rpc"
        self._url = url
        self._username = config_manager.get('torrent_client.username', '') or ''
        self._password = config_manager.get('torrent_client.password', '') or ''
        self._category = config_manager.get('torrent_client.category', 'soulsync') or 'soulsync'
        self._save_path = config_manager.get('torrent_client.save_path', '') or ''
        with self._session_id_lock:
            self._session_id = None

    def reload_settings(self) -> None:
        self._load_config()

    def is_configured(self) -> bool:
        return bool(self._url)

    async def check_connection(self) -> bool:
        if not self.is_configured():
            return False
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._check_connection_sync)

    def _check_connection_sync(self) -> bool:
        resp = self._rpc('session-get', {})
        return resp is not None

    def _rpc(self, method: str, arguments: dict) -> Optional[dict]:
        """One Transmission RPC round-trip. Handles the 409
        session-id renegotiation transparently — Transmission rejects
        the first call with HTTP 409 and a fresh
        ``X-Transmission-Session-Id`` header, which subsequent calls
        must echo back."""
        if not self._url:
            return None
        auth = (self._username, self._password) if self._username else None
        payload = {'method': method, 'arguments': arguments}
        for attempt in range(2):
            try:
                with self._session_id_lock:
                    sid = self._session_id
                headers = {'Content-Type': 'application/json'}
                if sid:
                    headers['X-Transmission-Session-Id'] = sid
                resp = http_requests.post(
                    self._url, json=payload, headers=headers, auth=auth,
                    timeout=self.DEFAULT_TIMEOUT,
                )
                if resp.status_code == 409:
                    # Pick up the new session id and retry.
                    new_sid = resp.headers.get('X-Transmission-Session-Id')
                    if not new_sid:
                        logger.error("Transmission 409 with no X-Transmission-Session-Id header")
                        return None
                    with self._session_id_lock:
                        self._session_id = new_sid
                    continue
                if not resp.ok:
                    logger.warning("Transmission RPC %s returned HTTP %s", method, resp.status_code)
                    return None
                data = resp.json()
                if data.get('result') != 'success':
                    logger.warning("Transmission RPC %s result=%s", method, data.get('result'))
                    return None
                return data.get('arguments', {})
            except Exception as e:
                logger.error("Transmission RPC %s failed: %s", method, e)
                return None
        return None

    async def add_torrent(
        self,
        url_or_magnet: str,
        category: str = "soulsync",
        save_path: Optional[str] = None,
    ) -> Optional[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._add_torrent_sync, url_or_magnet, category, save_path
        )

    def _add_torrent_sync(
        self,
        url_or_magnet: str,
        category: str,
        save_path: Optional[str],
    ) -> Optional[str]:
        args: dict = {'filename': url_or_magnet, 'labels': [category or self._category]}
        if save_path or self._save_path:
            args['download-dir'] = save_path or self._save_path
        return self._add_torrent_finish(args)

    async def add_torrent_file(
        self,
        file_bytes: bytes,
        category: str = "soulsync",
        save_path: Optional[str] = None,
    ) -> Optional[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._add_torrent_file_sync, file_bytes, category, save_path
        )

    def _add_torrent_file_sync(
        self,
        file_bytes: bytes,
        category: str,
        save_path: Optional[str],
    ) -> Optional[str]:
        args: dict = {
            'metainfo': base64.b64encode(file_bytes).decode('ascii'),
            'labels': [category or self._category],
        }
        if save_path or self._save_path:
            args['download-dir'] = save_path or self._save_path
        return self._add_torrent_finish(args)

    def _add_torrent_finish(self, args: dict) -> Optional[str]:
        data = self._rpc('torrent-add', args)
        if not data:
            return None
        # torrent-add returns either ``torrent-added`` (new torrent) or
        # ``torrent-duplicate`` (already exists). Both carry the hash.
        torrent = data.get('torrent-added') or data.get('torrent-duplicate')
        if not torrent:
            return None
        return str(torrent.get('hashString') or '') or None

    async def get_status(self, torrent_id: str) -> Optional[TorrentStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_status_sync, torrent_id)

    def _get_status_sync(self, torrent_id: str) -> Optional[TorrentStatus]:
        data = self._rpc('torrent-get', {
            'ids': [torrent_id],
            'fields': self._STATUS_FIELDS,
        })
        if not data or not data.get('torrents'):
            return None
        return self._parse_status(data['torrents'][0])

    async def get_all(self) -> List[TorrentStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_all_sync)

    def _get_all_sync(self) -> List[TorrentStatus]:
        data = self._rpc('torrent-get', {'fields': self._STATUS_FIELDS})
        if not data:
            return []
        return [self._parse_status(t) for t in data.get('torrents', [])]

    _STATUS_FIELDS = [
        'hashString', 'name', 'status', 'percentDone', 'totalSize',
        'downloadedEver', 'rateDownload', 'rateUpload', 'peersSendingToUs',
        'peersGettingFromUs', 'eta', 'downloadDir', 'errorString',
    ]

    def _parse_status(self, item: dict) -> TorrentStatus:
        progress = float(item.get('percentDone') or 0.0)
        status_code = int(item.get('status') or 0)
        eta_raw = item.get('eta')
        # Transmission returns -1 / -2 for "unknown" — surface as None.
        eta = eta_raw if isinstance(eta_raw, int) and eta_raw > 0 else None
        return TorrentStatus(
            id=str(item.get('hashString') or ''),
            name=item.get('name') or '',
            state=_map_state(status_code, progress),
            progress=progress,
            size=int(item.get('totalSize') or 0),
            downloaded=int(item.get('downloadedEver') or 0),
            download_speed=int(item.get('rateDownload') or 0),
            upload_speed=int(item.get('rateUpload') or 0),
            seeders=int(item.get('peersSendingToUs') or 0),
            peers=int(item.get('peersGettingFromUs') or 0),
            eta=eta,
            save_path=item.get('downloadDir'),
            error=item.get('errorString') or None,
        )

    async def remove(self, torrent_id: str, delete_files: bool = False) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._remove_sync, torrent_id, delete_files)

    def _remove_sync(self, torrent_id: str, delete_files: bool) -> bool:
        data = self._rpc('torrent-remove', {
            'ids': [torrent_id],
            'delete-local-data': bool(delete_files),
        })
        return data is not None

    async def pause(self, torrent_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._pause_sync, torrent_id)

    def _pause_sync(self, torrent_id: str) -> bool:
        return self._rpc('torrent-stop', {'ids': [torrent_id]}) is not None

    async def resume(self, torrent_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._resume_sync, torrent_id)

    def _resume_sync(self, torrent_id: str) -> bool:
        return self._rpc('torrent-start', {'ids': [torrent_id]}) is not None
