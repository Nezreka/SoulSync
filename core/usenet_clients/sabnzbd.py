"""SABnzbd adapter.

Auth model: a single API key passed as ``?apikey=...`` on every
request. No login flow. Every endpoint is the same path ``/api`` with
a ``mode=`` query param.

Reference: https://sabnzbd.org/wiki/configuration/4.3/api
"""

from __future__ import annotations

import asyncio
from typing import List, Optional, Union

import requests as http_requests

from config.settings import config_manager
from core.usenet_clients.base import UsenetStatus
from utils.logging_config import get_logger

logger = get_logger("usenet.sabnzbd")


# SAB queue states + history states → adapter-uniform set. Covers
# every Status value from SAB's ``sabnzbd/api.py`` plus the legacy
# short-form codes ("pp" for post-processing, "trying" for retry) and
# the prop_* variants returned for items that bounce between paused
# and failed during retry. Anything unmapped lands on "error" via
# ``_map_state``'s default — the album-bundle poll helper treats that
# default as a transient miss so a brand-new unmapped state can't
# infinite-loop the poll the way "pp" used to.
_SAB_QUEUE_STATE_MAP = {
    "idle":         "queued",
    "queued":       "queued",
    "grabbing":     "queued",
    "fetching":     "downloading",
    "downloading":  "downloading",
    "trying":       "downloading",
    "paused":       "paused",
    "prop_paused":  "paused",
    "checking":     "verifying",
    "quickcheck":   "verifying",
    "verifying":    "verifying",
    "repairing":    "repairing",
    "extracting":   "extracting",
    "unpacking":    "extracting",
    "moving":       "extracting",
    "running":      "extracting",
    "pp":           "extracting",
    "postprocessing": "extracting",
    "completed":    "completed",
    "failed":       "failed",
    "prop_failed":  "failed",
    "deleted":      "failed",
}


def _map_state(sab_state: str) -> str:
    return _SAB_QUEUE_STATE_MAP.get((sab_state or "").lower(), "error")


class SABnzbdAdapter:
    """SABnzbd REST API adapter (v2+)."""

    DEFAULT_TIMEOUT = 15

    def __init__(self) -> None:
        self._load_config()

    def _load_config(self) -> None:
        self._url = (config_manager.get('usenet_client.url', '') or '').rstrip('/')
        self._api_key = config_manager.get('usenet_client.api_key', '') or ''
        self._category = config_manager.get('usenet_client.category', 'soulsync') or 'soulsync'

    def reload_settings(self) -> None:
        self._load_config()

    def is_configured(self) -> bool:
        return bool(self._url and self._api_key)

    async def check_connection(self) -> bool:
        if not self.is_configured():
            return False
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._check_connection_sync)

    def _check_connection_sync(self) -> bool:
        # ``mode=version`` is the cheapest authenticated probe SAB exposes.
        data = self._call_sync('version')
        return bool(data and data.get('version'))

    def _call_sync(self, mode: str, **extra) -> Optional[dict]:
        if not self.is_configured():
            return None
        params = {
            'mode': mode,
            'output': 'json',
            'apikey': self._api_key,
        }
        params.update(extra)
        try:
            resp = http_requests.get(f"{self._url}/api", params=params, timeout=self.DEFAULT_TIMEOUT)
            if not resp.ok:
                logger.warning("SABnzbd mode=%s returned HTTP %s", mode, resp.status_code)
                return None
            return resp.json()
        except http_requests.exceptions.RequestException as e:
            logger.error("SABnzbd mode=%s request failed: %s", mode, e)
            return None
        except ValueError as e:
            logger.error("SABnzbd mode=%s response was not JSON: %s", mode, e)
            return None

    def _post_sync(self, mode: str, files=None, **extra) -> Optional[dict]:
        if not self.is_configured():
            return None
        params = {
            'mode': mode,
            'output': 'json',
            'apikey': self._api_key,
        }
        params.update(extra)
        try:
            resp = http_requests.post(f"{self._url}/api", params=params, files=files,
                                      timeout=self.DEFAULT_TIMEOUT)
            if not resp.ok:
                logger.warning("SABnzbd POST mode=%s returned HTTP %s", mode, resp.status_code)
                return None
            return resp.json()
        except http_requests.exceptions.RequestException as e:
            logger.error("SABnzbd POST mode=%s failed: %s", mode, e)
            return None
        except ValueError as e:
            logger.error("SABnzbd POST mode=%s response was not JSON: %s", mode, e)
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
        if isinstance(url_or_bytes, bytes):
            files = {'name': ('soulsync.nzb', url_or_bytes, 'application/x-nzb')}
            data = self._post_sync('addfile', files=files, cat=cat)
        else:
            data = self._call_sync('addurl', name=url_or_bytes, cat=cat)
        if not data or not data.get('status'):
            return None
        ids = data.get('nzo_ids') or []
        return ids[0] if ids else None

    async def get_status(self, job_id: str) -> Optional[UsenetStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_status_sync, job_id)

    def _get_status_sync(self, job_id: str) -> Optional[UsenetStatus]:
        # Direct nzo_ids lookup against queue, then history. Falls back
        # to the bulk fetch for SAB versions that don't honour the
        # nzo_ids filter (very old SAB), but the direct path is the hot
        # path because the bulk history fetch was limited to 50 entries
        # — on a busy SAB server a recently-completed job would roll
        # past the window and the poll would log "disappeared".
        if not job_id:
            return None
        queue = self._call_sync('queue', nzo_ids=job_id)
        if queue and isinstance(queue.get('queue'), dict):
            for slot in queue['queue'].get('slots', []) or []:
                if str(slot.get('nzo_id') or '') == job_id:
                    return self._parse_queue_slot(slot)
        history = self._call_sync('history', nzo_ids=job_id)
        if history and isinstance(history.get('history'), dict):
            for slot in history['history'].get('slots', []) or []:
                if str(slot.get('nzo_id') or '') == job_id:
                    return self._parse_history_slot(slot)
        # Fallback: SAB version pre-dating nzo_ids filter support. The
        # bulk path is still limit=50; the helper's transient-miss
        # tolerance will cover the gap if the entry briefly rolls out
        # of the window.
        for status in self._get_all_sync():
            if status.id == job_id:
                return status
        return None

    async def get_all(self) -> List[UsenetStatus]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_all_sync)

    def _get_all_sync(self) -> List[UsenetStatus]:
        out: List[UsenetStatus] = []
        # Active queue
        queue = self._call_sync('queue')
        if queue and isinstance(queue.get('queue'), dict):
            for slot in queue['queue'].get('slots', []) or []:
                out.append(self._parse_queue_slot(slot))
        # History — completed / failed jobs SAB still tracks
        history = self._call_sync('history', limit=50)
        if history and isinstance(history.get('history'), dict):
            for slot in history['history'].get('slots', []) or []:
                out.append(self._parse_history_slot(slot))
        return out

    def _parse_queue_slot(self, slot: dict) -> UsenetStatus:
        try:
            percentage = float(slot.get('percentage') or 0.0)
        except (TypeError, ValueError):
            percentage = 0.0
        progress = percentage / 100.0
        # mb / mbleft are strings of MB values in SAB's queue API.
        size_mb = self._safe_float(slot.get('mb'))
        left_mb = self._safe_float(slot.get('mbleft'))
        size_bytes = int(size_mb * 1024 * 1024) if size_mb else 0
        downloaded_bytes = int((size_mb - left_mb) * 1024 * 1024) if size_mb and left_mb is not None else 0
        # ``timeleft`` is HH:MM:SS — convert to seconds.
        eta = self._parse_timeleft(slot.get('timeleft'))
        return UsenetStatus(
            id=str(slot.get('nzo_id') or ''),
            name=slot.get('filename') or slot.get('name') or '',
            state=_map_state(slot.get('status') or ''),
            progress=max(0.0, min(progress, 1.0)),
            size=size_bytes,
            downloaded=max(0, downloaded_bytes),
            download_speed=0,  # queue endpoint doesn't include per-slot speed
            eta=eta,
            category=slot.get('cat'),
        )

    def _parse_history_slot(self, slot: dict) -> UsenetStatus:
        # History entries are post-download — progress is 1.0 unless failed.
        sab_state = (slot.get('status') or '').lower()
        is_failed = sab_state == 'failed'
        return UsenetStatus(
            id=str(slot.get('nzo_id') or ''),
            name=slot.get('name') or '',
            state='failed' if is_failed else 'completed',
            progress=0.0 if is_failed else 1.0,
            size=int(slot.get('bytes') or 0),
            downloaded=int(slot.get('bytes') or 0) if not is_failed else 0,
            download_speed=0,
            save_path=slot.get('storage') or slot.get('path'),
            category=slot.get('category'),
            error=slot.get('fail_message') if is_failed else None,
        )

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        if value is None or value == '':
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_timeleft(value) -> Optional[int]:
        if not value or not isinstance(value, str):
            return None
        parts = value.split(':')
        try:
            if len(parts) == 3:
                h, m, s = parts
                return int(h) * 3600 + int(m) * 60 + int(s)
            if len(parts) == 2:
                m, s = parts
                return int(m) * 60 + int(s)
        except ValueError:
            return None
        return None

    async def remove(self, job_id: str, delete_files: bool = False) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._remove_sync, job_id, delete_files)

    def _remove_sync(self, job_id: str, delete_files: bool) -> bool:
        # SAB deletes from queue or history depending on where the job is.
        # We try queue first; if SAB reports no-op, fall through to history.
        params = {'name': 'delete', 'value': job_id}
        if delete_files:
            params['del_files'] = 1
        data = self._call_sync('queue', **params)
        if data and data.get('status'):
            return True
        # History delete
        history_params = {'name': 'delete', 'value': job_id}
        if delete_files:
            history_params['del_files'] = 1
        data = self._call_sync('history', **history_params)
        return bool(data and data.get('status'))

    async def pause(self, job_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._pause_sync, job_id)

    def _pause_sync(self, job_id: str) -> bool:
        data = self._call_sync('queue', name='pause', value=job_id)
        return bool(data and data.get('status'))

    async def resume(self, job_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._resume_sync, job_id)

    def _resume_sync(self, job_id: str) -> bool:
        data = self._call_sync('queue', name='resume', value=job_id)
        return bool(data and data.get('status'))
