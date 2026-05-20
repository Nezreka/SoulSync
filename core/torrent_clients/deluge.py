"""Deluge 2.x JSON-RPC adapter.

Auth model: POST ``/json`` with method ``auth.login`` returns a
``_session_id`` cookie. The session cookie + every subsequent JSON-RPC
call must include a monotonically-incrementing ``id`` field. ``params``
is a list (positional), not an object.

Reference: https://deluge.readthedocs.io/en/latest/reference/api.html
and https://deluge.readthedocs.io/en/latest/reference/webapi.html
"""

from __future__ import annotations

import asyncio
import base64
import threading
from itertools import count
from typing import Any, List, Optional

import requests as http_requests

from config.settings import config_manager
from core.torrent_clients.base import TorrentStatus
from utils.logging_config import get_logger

logger = get_logger("torrent.deluge")


# Deluge native state strings → adapter-uniform set.
_DELUGE_STATE_MAP = {
    "Allocating":  "queued",
    "Checking":    "queued",
    "Downloading": "downloading",
    "Seeding":     "seeding",
    "Paused":      "paused",
    "Queued":      "queued",
    "Error":       "error",
    "Moving":      "queued",
}


def _map_state(deluge_state: str, progress: float) -> str:
    mapped = _DELUGE_STATE_MAP.get(deluge_state or '', "error")
    if mapped == "paused" and progress >= 1.0:
        return "completed"
    return mapped


class DelugeAdapter:
    """Deluge 2.x WebUI JSON-RPC adapter."""

    DEFAULT_TIMEOUT = 15

    # Fields we ask ``core.get_torrents_status`` to return — explicit
    # to keep payload size predictable across Deluge versions that
    # add fields over time.
    _STATUS_FIELDS = [
        'hash', 'name', 'state', 'progress', 'total_size',
        'total_done', 'download_payload_rate', 'upload_payload_rate',
        'num_seeds', 'num_peers', 'eta', 'save_path', 'tracker_status',
    ]

    def __init__(self) -> None:
        self._session: Optional[http_requests.Session] = None
        self._session_lock = threading.Lock()
        self._id_counter = count(1)
        self._load_config()

    def _load_config(self) -> None:
        self._url = (config_manager.get('torrent_client.url', '') or '').rstrip('/')
        # Deluge's WebUI auth uses a single password, not username+password.
        # We accept whichever field the user filled in — keeps the UI uniform.
        self._password = (
            config_manager.get('torrent_client.password', '')
            or config_manager.get('torrent_client.username', '')
            or ''
        )
        self._category = config_manager.get('torrent_client.category', 'soulsync') or 'soulsync'
        self._save_path = config_manager.get('torrent_client.save_path', '') or ''
        with self._session_lock:
            self._session = None

    def reload_settings(self) -> None:
        self._load_config()

    def is_configured(self) -> bool:
        # Deluge WebUI requires a password; without it auth.login fails
        # outright. Refuse the configuration up front rather than letting
        # every call 401.
        return bool(self._url and self._password)

    async def check_connection(self) -> bool:
        if not self.is_configured():
            return False
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._check_connection_sync)

    def _check_connection_sync(self) -> bool:
        sess = self._ensure_session_sync()
        if sess is None:
            return False
        # web.connected() returns True iff the WebUI's daemon link is up.
        # If daemons aren't auto-connected, fall back to a cheap call
        # that just confirms we're authenticated.
        result = self._rpc_sync('web.connected', [])
        if result is True:
            return True
        # Fall back to a generic auth probe.
        return self._rpc_sync('auth.check_session', []) is not None

    def _ensure_session_sync(self) -> Optional[http_requests.Session]:
        with self._session_lock:
            if self._session is not None:
                return self._session
            sess = http_requests.Session()
            self._session = sess
        # Log in. auth.login takes the password as a single positional arg.
        result = self._rpc_sync('auth.login', [self._password])
        if result is not True:
            logger.error("Deluge auth.login returned %r", result)
            with self._session_lock:
                self._session = None
            return None
        with self._session_lock:
            return self._session

    def _rpc_sync(self, method: str, params: list) -> Any:
        if not self._url:
            return None
        with self._session_lock:
            sess = self._session
        if sess is None:
            # Bootstrap a session container; auth.login will populate it.
            with self._session_lock:
                if self._session is None:
                    self._session = http_requests.Session()
                sess = self._session
        payload = {
            'id': next(self._id_counter),
            'method': method,
            'params': params,
        }
        try:
            resp = sess.post(
                f"{self._url}/json", json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=self.DEFAULT_TIMEOUT,
            )
            if not resp.ok:
                logger.warning("Deluge %s returned HTTP %s", method, resp.status_code)
                return None
            data = resp.json()
            err = data.get('error')
            if err:
                # Code 1 = unknown method, 2 = bad params, etc.
                # Code 'No Auth' surfaces as a string in some versions.
                logger.warning("Deluge %s error: %r", method, err)
                return None
            return data.get('result')
        except Exception as e:
            logger.error("Deluge %s call failed: %s", method, e)
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
        if self._ensure_session_sync() is None:
            return None
        options: dict = {}
        if save_path or self._save_path:
            options['download_location'] = save_path or self._save_path
        # Deluge distinguishes magnet URIs from HTTP .torrent URLs at
        # the API layer — different method names.
        if url_or_magnet.startswith('magnet:'):
            method = 'core.add_torrent_magnet'
        else:
            method = 'core.add_torrent_url'
        torrent_hash = self._rpc_sync(method, [url_or_magnet, options])
        if not torrent_hash:
            return None
        self._apply_label(str(torrent_hash), category or self._category)
        return str(torrent_hash)

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
        if self._ensure_session_sync() is None:
            return None
        options: dict = {}
        if save_path or self._save_path:
            options['download_location'] = save_path or self._save_path
        encoded = base64.b64encode(file_bytes).decode('ascii')
        torrent_hash = self._rpc_sync('core.add_torrent_file', ['soulsync.torrent', encoded, options])
        if not torrent_hash:
            return None
        self._apply_label(str(torrent_hash), category or self._category)
        return str(torrent_hash)

    def _apply_label(self, torrent_hash: str, label: str) -> None:
        """Best-effort label assignment. The Label plugin is optional
        in Deluge — if it isn't installed the RPC call fails silently
        and we don't propagate the error: the torrent is still added."""
        if not label:
            return
        # Ensure the label exists before assigning.
        self._rpc_sync('label.add', [label])
        self._rpc_sync('label.set_torrent', [torrent_hash, label])

    async def get_status(self, torrent_id: str) -> Optional[TorrentStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_status_sync, torrent_id)

    def _get_status_sync(self, torrent_id: str) -> Optional[TorrentStatus]:
        result = self._rpc_sync('core.get_torrent_status', [torrent_id, self._STATUS_FIELDS])
        if not isinstance(result, dict) or not result:
            return None
        # core.get_torrent_status doesn't echo back the hash — patch it in.
        result.setdefault('hash', torrent_id)
        return self._parse_status(result)

    async def get_all(self) -> List[TorrentStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_all_sync)

    def _get_all_sync(self) -> List[TorrentStatus]:
        result = self._rpc_sync('core.get_torrents_status', [{}, self._STATUS_FIELDS])
        if not isinstance(result, dict):
            return []
        out = []
        for hash_id, item in result.items():
            if not isinstance(item, dict):
                continue
            item.setdefault('hash', hash_id)
            out.append(self._parse_status(item))
        return out

    def _parse_status(self, item: dict) -> TorrentStatus:
        progress = float(item.get('progress') or 0.0)
        # Deluge expresses progress as 0-100; normalize to 0-1.
        if progress > 1.0:
            progress = progress / 100.0
        return TorrentStatus(
            id=str(item.get('hash') or ''),
            name=item.get('name') or '',
            state=_map_state(item.get('state') or '', progress),
            progress=progress,
            size=int(item.get('total_size') or 0),
            downloaded=int(item.get('total_done') or 0),
            download_speed=int(item.get('download_payload_rate') or 0),
            upload_speed=int(item.get('upload_payload_rate') or 0),
            seeders=int(item.get('num_seeds') or 0),
            peers=int(item.get('num_peers') or 0),
            eta=int(item['eta']) if isinstance(item.get('eta'), (int, float)) and item.get('eta', 0) > 0 else None,
            save_path=item.get('save_path'),
            error=item.get('tracker_status') if 'Error' in (item.get('state') or '') else None,
        )

    async def remove(self, torrent_id: str, delete_files: bool = False) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._remove_sync, torrent_id, delete_files)

    def _remove_sync(self, torrent_id: str, delete_files: bool) -> bool:
        result = self._rpc_sync('core.remove_torrent', [torrent_id, bool(delete_files)])
        return result is True

    async def pause(self, torrent_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._pause_sync, torrent_id)

    def _pause_sync(self, torrent_id: str) -> bool:
        # Deluge 2.x core.pause_torrent takes a list of hashes.
        return self._rpc_sync('core.pause_torrent', [[torrent_id]]) is not None

    async def resume(self, torrent_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._resume_sync, torrent_id)

    def _resume_sync(self, torrent_id: str) -> bool:
        return self._rpc_sync('core.resume_torrent', [[torrent_id]]) is not None
