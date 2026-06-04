"""qBittorrent WebUI v2 adapter.

Auth model: POST ``/api/v2/auth/login`` with form-encoded
``username`` + ``password`` returns a ``SID`` cookie that's required
on every subsequent call. The cookie lives on a ``requests.Session``
maintained by this adapter; we lazily re-login on 403 in case the
server expired the session.

Reference: https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-4.1)
"""

from __future__ import annotations

import asyncio
import threading
from typing import List, Optional

import requests as http_requests

from config.settings import config_manager
from core.torrent_clients.base import TorrentStatus
from utils.logging_config import get_logger

logger = get_logger("torrent.qbittorrent")


# qBittorrent's native state strings. Mapped onto the adapter-uniform
# set in ``_map_state``. See https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-4.1)#torrent-list
_QBIT_STATE_MAP = {
    "allocating":      "queued",
    "checkingDL":      "queued",
    "checkingUP":      "seeding",
    "checkingResumeData": "queued",
    "downloading":     "downloading",
    "error":           "error",
    "forcedDL":        "downloading",
    "forcedUP":        "seeding",
    "metaDL":          "downloading",
    "missingFiles":    "error",
    "moving":          "queued",
    "pausedDL":        "paused",
    "pausedUP":        "completed",
    "queuedDL":        "queued",
    "queuedUP":        "queued",
    "stalledDL":       "stalled",
    "stalledUP":       "seeding",
    "uploading":       "seeding",
    "unknown":         "error",
}


def _map_state(qbit_state: str) -> str:
    return _QBIT_STATE_MAP.get(qbit_state, "error")


class QBittorrentAdapter:
    """qBittorrent WebUI v2 adapter."""

    DEFAULT_TIMEOUT = 15

    def __init__(self) -> None:
        self._session: Optional[http_requests.Session] = None
        self._session_lock = threading.Lock()
        self._load_config()

    def _load_config(self) -> None:
        self._url = (config_manager.get('torrent_client.url', '') or '').rstrip('/')
        self._username = config_manager.get('torrent_client.username', '') or ''
        self._password = config_manager.get('torrent_client.password', '') or ''
        self._category = config_manager.get('torrent_client.category', 'soulsync') or 'soulsync'
        self._save_path = config_manager.get('torrent_client.save_path', '') or ''
        # Drop any existing session — credentials may have changed.
        with self._session_lock:
            self._session = None

    def reload_settings(self) -> None:
        self._load_config()

    def is_configured(self) -> bool:
        # qBittorrent allows no-auth setups (LAN), so credentials are
        # optional — URL is the only hard requirement.
        return bool(self._url)

    async def check_connection(self) -> bool:
        if not self.is_configured():
            return False
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._check_connection_sync)

    def _check_connection_sync(self) -> bool:
        try:
            sess = self._ensure_session_sync()
            if sess is None:
                return False
            resp = sess.get(f"{self._url}/api/v2/app/version", timeout=self.DEFAULT_TIMEOUT)
            return resp.ok
        except Exception as e:
            logger.error("qBittorrent connection probe failed: %s", e)
            return False

    def _ensure_session_sync(self) -> Optional[http_requests.Session]:
        with self._session_lock:
            if self._session is not None:
                return self._session
            sess = http_requests.Session()
            # No-auth setup — skip login.
            if not self._username and not self._password:
                self._session = sess
                return sess
            try:
                resp = sess.post(
                    f"{self._url}/api/v2/auth/login",
                    data={'username': self._username, 'password': self._password},
                    # qBittorrent rejects login attempts that arrive without a
                    # Referer matching its configured host (CSRF guard). Sending
                    # the WebUI's own URL satisfies the check.
                    headers={'Referer': self._url},
                    timeout=self.DEFAULT_TIMEOUT,
                )
                body = resp.text.strip()
                has_sid = bool(sess.cookies.get('SID'))
                # qBittorrent reports BAD credentials as HTTP 200 + body "Fails."
                # (it does NOT use a 4xx). SUCCESS is the SID auth cookie and/or a
                # success body: "Ok." on <= 5.1, or an empty HTTP 204 on 5.2.0+,
                # which changed /api/v2/auth/login to return 204 No Content.
                # The old check required body == "Ok." and so rejected 5.2.0+.
                login_ok = (
                    resp.ok
                    and body.lower() != 'fails.'
                    and (has_sid or resp.status_code == 204 or body in ('', 'Ok.'))
                )
                if not login_ok:
                    logger.error("qBittorrent login failed: HTTP %s body=%r", resp.status_code, resp.text[:200])
                    return None
                self._session = sess
                return sess
            except Exception as e:
                logger.error("qBittorrent login error: %s", e)
                return None

    def _call(self, method: str, path: str, **kwargs) -> Optional[http_requests.Response]:
        sess = self._ensure_session_sync()
        if sess is None:
            return None
        try:
            kwargs.setdefault('timeout', self.DEFAULT_TIMEOUT)
            kwargs.setdefault('headers', {}).setdefault('Referer', self._url)
            resp = sess.request(method, f"{self._url}{path}", **kwargs)
            # Session expired — try one re-login and retry.
            if resp.status_code == 403:
                with self._session_lock:
                    self._session = None
                sess = self._ensure_session_sync()
                if sess is None:
                    return None
                resp = sess.request(method, f"{self._url}{path}", **kwargs)
            return resp
        except Exception as e:
            logger.error("qBittorrent %s %s failed: %s", method, path, e)
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
        cat = category or self._category
        # Snapshot the current set of torrent hashes BEFORE adding —
        # qBittorrent's /add endpoint returns 200 "Ok." regardless of
        # whether the URL was actually accepted or registered, and
        # category-filtered lookups race the add (qBit hasn't
        # categorised the new torrent yet on the first poll). Diffing
        # before / after is the only reliable way to recover the hash.
        before = self._all_hashes()
        if before is None:
            return None
        data = {'urls': url_or_magnet, 'category': cat}
        if save_path or self._save_path:
            data['savepath'] = save_path or self._save_path
        resp = self._call('POST', '/api/v2/torrents/add', data=data)
        if not resp or not resp.ok:
            logger.warning("qBittorrent /torrents/add returned HTTP %s body=%r",
                           resp.status_code if resp else 'no-response',
                           (resp.text[:200] if resp else ''))
            return None
        if resp.text and resp.text.strip() and resp.text.strip() != 'Ok.':
            logger.warning("qBittorrent /torrents/add unexpected body: %r", resp.text[:200])
            return None
        new_hash = self._poll_for_new_hash(before)
        if not new_hash:
            logger.error("qBittorrent accepted the request but no new torrent appeared — "
                         "URL may have been rejected (bad magnet, unreachable HTTPS, "
                         "duplicate hash, etc.)")
        return new_hash

    def _all_hashes(self) -> Optional[set]:
        """Return the set of every torrent hash qBit currently tracks,
        or None on lookup failure."""
        resp = self._call('GET', '/api/v2/torrents/info')
        if not resp or not resp.ok:
            return None
        try:
            return {item.get('hash') for item in resp.json() if item.get('hash')}
        except Exception as e:
            logger.error("qBittorrent /torrents/info parse failed: %s", e)
            return None

    def _poll_for_new_hash(self, before: set) -> Optional[str]:
        """Poll up to ~5s for a new torrent to appear (qBit takes a
        moment to fetch the .torrent file from the URL and register
        it). Returns the new hash, or None if nothing showed up."""
        import time as _time
        for _ in range(10):
            _time.sleep(0.5)
            current = self._all_hashes()
            if current is None:
                continue
            new = current - before
            if new:
                return next(iter(new))
        return None

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
        cat = category or self._category
        before = self._all_hashes()
        if before is None:
            return None
        data = {'category': cat}
        if save_path or self._save_path:
            data['savepath'] = save_path or self._save_path
        files = {'torrents': ('soulsync.torrent', file_bytes, 'application/x-bittorrent')}
        resp = self._call('POST', '/api/v2/torrents/add', data=data, files=files)
        if not resp or not resp.ok:
            return None
        return self._poll_for_new_hash(before)

    async def get_status(self, torrent_id: str) -> Optional[TorrentStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_status_sync, torrent_id)

    def _get_status_sync(self, torrent_id: str) -> Optional[TorrentStatus]:
        resp = self._call('GET', '/api/v2/torrents/info', params={'hashes': torrent_id})
        if not resp or not resp.ok:
            return None
        try:
            items = resp.json()
        except Exception as e:
            logger.error("qBittorrent get_status parse failed: %s", e)
            return None
        if not items:
            return None
        return self._parse_status(items[0])

    async def get_all(self) -> List[TorrentStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_all_sync)

    def _get_all_sync(self) -> List[TorrentStatus]:
        resp = self._call('GET', '/api/v2/torrents/info')
        if not resp or not resp.ok:
            return []
        try:
            return [self._parse_status(item) for item in resp.json()]
        except Exception as e:
            logger.error("qBittorrent get_all parse failed: %s", e)
            return []

    def _parse_status(self, item: dict) -> TorrentStatus:
        return TorrentStatus(
            id=str(item.get('hash') or ''),
            name=item.get('name') or '',
            state=_map_state(item.get('state') or 'unknown'),
            progress=float(item.get('progress') or 0.0),
            size=int(item.get('size') or 0),
            downloaded=int(item.get('downloaded') or 0),
            download_speed=int(item.get('dlspeed') or 0),
            upload_speed=int(item.get('upspeed') or 0),
            seeders=int(item.get('num_seeds') or 0),
            peers=int(item.get('num_leechs') or 0),
            eta=item.get('eta') if isinstance(item.get('eta'), int) and item.get('eta', 0) > 0 else None,
            save_path=item.get('save_path'),
        )

    async def remove(self, torrent_id: str, delete_files: bool = False) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._remove_sync, torrent_id, delete_files)

    def _remove_sync(self, torrent_id: str, delete_files: bool) -> bool:
        resp = self._call('POST', '/api/v2/torrents/delete', data={
            'hashes': torrent_id,
            'deleteFiles': 'true' if delete_files else 'false',
        })
        return bool(resp and resp.ok)

    async def pause(self, torrent_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._pause_sync, torrent_id)

    def _pause_sync(self, torrent_id: str) -> bool:
        resp = self._call('POST', '/api/v2/torrents/pause', data={'hashes': torrent_id})
        return bool(resp and resp.ok)

    async def resume(self, torrent_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._resume_sync, torrent_id)

    def _resume_sync(self, torrent_id: str) -> bool:
        resp = self._call('POST', '/api/v2/torrents/resume', data={'hashes': torrent_id})
        return bool(resp and resp.ok)
