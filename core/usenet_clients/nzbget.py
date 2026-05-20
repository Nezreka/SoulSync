"""NZBGet adapter.

Auth model: HTTP Basic auth on the JSON-RPC endpoint ``/jsonrpc``.
Every method takes positional ``params``. Identical pattern to
Deluge but with different method names.

Reference: https://nzbget.com/documentation/api/
"""

from __future__ import annotations

import asyncio
import base64
from itertools import count
from typing import Any, List, Optional, Union

import requests as http_requests

from config.settings import config_manager
from core.usenet_clients.base import UsenetStatus
from utils.logging_config import get_logger

logger = get_logger("usenet.nzbget")


# NZBGet's ``Status`` field on ListGroups → adapter-uniform set.
# NZBGet states (group): QUEUED, PAUSED, DOWNLOADING, FETCHING, PP_QUEUED,
# LOADING_PARS, VERIFYING_SOURCES, REPAIRING, VERIFYING_REPAIRED, RENAMING,
# UNPACKING, MOVING, EXECUTING_SCRIPT, PP_FINISHED.
_NZBGET_STATE_MAP = {
    "QUEUED":             "queued",
    "PAUSED":             "paused",
    "DOWNLOADING":        "downloading",
    "FETCHING":           "downloading",
    "PP_QUEUED":          "queued",
    "LOADING_PARS":       "verifying",
    "VERIFYING_SOURCES":  "verifying",
    "REPAIRING":          "repairing",
    "VERIFYING_REPAIRED": "verifying",
    "RENAMING":           "extracting",
    "UNPACKING":          "extracting",
    "MOVING":             "extracting",
    "EXECUTING_SCRIPT":   "extracting",
    "PP_FINISHED":        "completed",
}


def _map_state(nzbget_state: str) -> str:
    return _NZBGET_STATE_MAP.get(nzbget_state or '', "error")


class NZBGetAdapter:
    """NZBGet JSON-RPC adapter."""

    DEFAULT_TIMEOUT = 15

    def __init__(self) -> None:
        self._id_counter = count(1)
        self._load_config()

    def _load_config(self) -> None:
        self._url = (config_manager.get('usenet_client.url', '') or '').rstrip('/')
        self._username = config_manager.get('usenet_client.username', '') or ''
        self._password = config_manager.get('usenet_client.password', '') or ''
        self._category = config_manager.get('usenet_client.category', 'soulsync') or 'soulsync'

    def reload_settings(self) -> None:
        self._load_config()

    def is_configured(self) -> bool:
        return bool(self._url and self._username and self._password)

    async def check_connection(self) -> bool:
        if not self.is_configured():
            return False
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._check_connection_sync)

    def _check_connection_sync(self) -> bool:
        return self._rpc_sync('version', []) is not None

    def _rpc_sync(self, method: str, params: list) -> Any:
        if not self._url:
            return None
        try:
            resp = http_requests.post(
                f"{self._url}/jsonrpc",
                json={'method': method, 'params': params, 'id': next(self._id_counter)},
                auth=(self._username, self._password) if self._username else None,
                headers={'Content-Type': 'application/json'},
                timeout=self.DEFAULT_TIMEOUT,
            )
            if not resp.ok:
                logger.warning("NZBGet %s returned HTTP %s", method, resp.status_code)
                return None
            data = resp.json()
            if data.get('error'):
                logger.warning("NZBGet %s error: %r", method, data.get('error'))
                return None
            return data.get('result')
        except http_requests.exceptions.RequestException as e:
            logger.error("NZBGet %s call failed: %s", method, e)
            return None
        except ValueError as e:
            logger.error("NZBGet %s response not JSON: %s", method, e)
            return None

    async def add_nzb(
        self,
        url_or_bytes: Union[str, bytes],
        category: str = "soulsync",
        save_path: Optional[str] = None,
    ) -> Optional[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._add_nzb_sync, url_or_bytes, category, save_path
        )

    def _add_nzb_sync(
        self,
        url_or_bytes: Union[str, bytes],
        category: str,
        save_path: Optional[str],
    ) -> Optional[str]:
        cat = category or self._category
        # NZBGet's ``append`` takes: NZBFilename, Content, Category,
        # Priority, AddToTop, AddPaused, DupeKey, DupeScore, DupeMode,
        # PPParameters. We pass the minimum required for an unpause-on-add.
        # Content is either base64 of the raw .nzb or a URL — NZBGet
        # auto-detects which based on whether it looks like a URL.
        if isinstance(url_or_bytes, bytes):
            content = base64.b64encode(url_or_bytes).decode('ascii')
            nzb_filename = 'soulsync.nzb'
        else:
            content = url_or_bytes
            nzb_filename = ''
        params = [
            nzb_filename,        # NZBFilename
            content,             # Content (URL or base64 NZB)
            cat,                 # Category
            0,                   # Priority
            False,               # AddToTop
            False,               # AddPaused
            '',                  # DupeKey
            0,                   # DupeScore
            'SCORE',             # DupeMode
            [],                  # PPParameters
        ]
        result = self._rpc_sync('append', params)
        if isinstance(result, int) and result > 0:
            return str(result)
        return None

    async def get_status(self, job_id: str) -> Optional[UsenetStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_status_sync, job_id)

    def _get_status_sync(self, job_id: str) -> Optional[UsenetStatus]:
        for status in self._get_all_sync():
            if status.id == job_id:
                return status
        return None

    async def get_all(self) -> List[UsenetStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_all_sync)

    def _get_all_sync(self) -> List[UsenetStatus]:
        out: List[UsenetStatus] = []
        groups = self._rpc_sync('listgroups', [0])
        if isinstance(groups, list):
            for group in groups:
                out.append(self._parse_group(group))
        history = self._rpc_sync('history', [False])
        if isinstance(history, list):
            for entry in history:
                out.append(self._parse_history(entry))
        return out

    def _parse_group(self, group: dict) -> UsenetStatus:
        # NZBGet reports sizes split into ``FileSizeLo`` (low 32 bits) +
        # ``FileSizeHi`` (high 32 bits) for compat with old clients —
        # ``FileSizeMB`` is the human-friendly aggregate.
        size_mb = self._mb_value(group, 'FileSize')
        remaining_mb = self._mb_value(group, 'RemainingSize')
        size_bytes = int(size_mb * 1024 * 1024) if size_mb else 0
        downloaded_bytes = int((size_mb - remaining_mb) * 1024 * 1024) if size_mb and remaining_mb is not None else 0
        progress = 0.0
        if size_bytes > 0:
            progress = max(0.0, min(downloaded_bytes / size_bytes, 1.0))
        # NZBGet's per-group ``DownloadRate`` field is in bytes/sec.
        speed = int(group.get('DownloadRate') or 0)
        return UsenetStatus(
            id=str(group.get('NZBID') or ''),
            name=group.get('NZBName') or '',
            state=_map_state(group.get('Status') or ''),
            progress=progress,
            size=size_bytes,
            downloaded=downloaded_bytes,
            download_speed=speed,
            save_path=group.get('DestDir'),
            category=group.get('Category'),
        )

    def _parse_history(self, entry: dict) -> UsenetStatus:
        # History entries have ``Status`` like ``SUCCESS/HEALTH``,
        # ``SUCCESS/UNPACK``, ``FAILURE/PAR``, etc.
        status_field = entry.get('Status') or ''
        is_failed = status_field.startswith('FAILURE')
        size_mb = self._mb_value(entry, 'FileSize')
        size_bytes = int(size_mb * 1024 * 1024) if size_mb else 0
        return UsenetStatus(
            id=str(entry.get('NZBID') or ''),
            name=entry.get('Name') or entry.get('NZBName') or '',
            state='failed' if is_failed else 'completed',
            progress=0.0 if is_failed else 1.0,
            size=size_bytes,
            downloaded=size_bytes if not is_failed else 0,
            download_speed=0,
            save_path=entry.get('DestDir'),
            category=entry.get('Category'),
            error=status_field if is_failed else None,
        )

    @staticmethod
    def _mb_value(entry: dict, prefix: str) -> Optional[float]:
        """Read an NZBGet size field. Prefers the high+low 32-bit split
        when available (most accurate); falls back to the ``MB``
        aggregate for older NZBGet versions."""
        lo = entry.get(f'{prefix}Lo')
        hi = entry.get(f'{prefix}Hi')
        if isinstance(lo, int) and isinstance(hi, int):
            total_bytes = (hi << 32) | lo
            return total_bytes / (1024 * 1024)
        mb = entry.get(f'{prefix}MB')
        if isinstance(mb, (int, float)):
            return float(mb)
        return None

    async def remove(self, job_id: str, delete_files: bool = False) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._remove_sync, job_id, delete_files)

    def _remove_sync(self, job_id: str, delete_files: bool) -> bool:
        # editqueue commands take a list of NZBIDs. ``GroupFinalDelete``
        # both removes and deletes downloaded data; ``GroupDelete`` just
        # removes the queue entry.
        try:
            id_int = int(job_id)
        except (TypeError, ValueError):
            return False
        command = 'GroupFinalDelete' if delete_files else 'GroupDelete'
        # editqueue(Command, Offset, EditText, IDs)
        result = self._rpc_sync('editqueue', [command, 0, '', [id_int]])
        return bool(result)

    async def pause(self, job_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._pause_sync, job_id)

    def _pause_sync(self, job_id: str) -> bool:
        try:
            id_int = int(job_id)
        except (TypeError, ValueError):
            return False
        return bool(self._rpc_sync('editqueue', ['GroupPause', 0, '', [id_int]]))

    async def resume(self, job_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._resume_sync, job_id)

    def _resume_sync(self, job_id: str) -> bool:
        try:
            id_int = int(job_id)
        except (TypeError, ValueError):
            return False
        return bool(self._rpc_sync('editqueue', ['GroupResume', 0, '', [id_int]]))
