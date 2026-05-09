"""Shared transport helpers for metadata providers."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

import requests

from core.metadata.exceptions import MetadataProviderError, MetadataRateLimited
from core.metadata.contracts import MetadataProviderStatus


class BaseMetadataAdapter:
    """Shared HTTP transport helpers for provider adapters."""

    provider_name: str = "unknown"
    min_api_interval: float = 0.0
    timeout: float = 15.0
    max_retries: int = 2
    retry_backoff: float = 0.5

    def __init__(self) -> None:
        self.session = requests.Session()
        self._lock = threading.RLock()
        self._next_request_at = 0.0
        self._rate_limited_until = 0.0
        self._last_error: Optional[str] = None

    def is_available(self) -> bool:
        return True

    def is_authenticated(self) -> bool:
        return self.is_available()

    def reload_config(self) -> None:
        """Refresh adapter-local configuration state."""

    def _set_last_error(self, message: Optional[str]) -> None:
        self._last_error = message

    def _throttle(self) -> None:
        if self.min_api_interval <= 0:
            return
        with self._lock:
            now = time.time()
            if now < self._next_request_at:
                time.sleep(self._next_request_at - now)
            self._next_request_at = time.time() + self.min_api_interval

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
        data: Any = None,
    ) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout or self.timeout,
                    data=data,
                )
            except Exception as exc:
                last_error = exc
                self._set_last_error(str(exc))
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * (attempt + 1))
                    continue
                raise MetadataProviderError(self.provider_name, method.lower(), str(exc)) from exc

            if response.status_code == 429:
                retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
                self._rate_limited_until = time.time() + float(retry_after or 60)
                message = f"{self.provider_name} rate limited"
                self._set_last_error(message)
                raise MetadataRateLimited(
                    self.provider_name,
                    method.lower(),
                    message,
                    status_code=429,
                    retry_after=retry_after,
                    payload=response.text,
                )

            if response.status_code in (404, 204):
                return None

            if response.status_code >= 500 and attempt < self.max_retries:
                last_error = MetadataProviderError(
                    self.provider_name,
                    method.lower(),
                    f"{self.provider_name} returned HTTP {response.status_code}",
                    status_code=response.status_code,
                    payload=response.text,
                )
                time.sleep(self.retry_backoff * (attempt + 1))
                continue

            if response.status_code >= 400:
                message = f"{self.provider_name} returned HTTP {response.status_code}"
                self._set_last_error(message)
                raise MetadataProviderError(
                    self.provider_name,
                    method.lower(),
                    message,
                    status_code=response.status_code,
                    payload=response.text,
                )

            if not response.content:
                return None

            try:
                return response.json()
            except Exception as exc:
                last_error = exc
                self._set_last_error(str(exc))
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * (attempt + 1))
                    continue
                raise MetadataProviderError(
                    self.provider_name,
                    method.lower(),
                    f"{self.provider_name} returned invalid JSON",
                    status_code=response.status_code,
                    payload=response.text,
                ) from exc

        if last_error is not None:
            raise MetadataProviderError(self.provider_name, method.lower(), str(last_error)) from last_error
        return None

    @staticmethod
    def _parse_retry_after(value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return None

    def get_status(self) -> MetadataProviderStatus:
        retry_after = None
        rate_limited = False
        if self._rate_limited_until > time.time():
            rate_limited = True
            retry_after = int(self._rate_limited_until - time.time())
        return MetadataProviderStatus(
            provider=self.provider_name,
            configured=self.is_authenticated(),
            available=self.is_available(),
            authenticated=self.is_authenticated(),
            rate_limited=rate_limited,
            retry_after=retry_after,
            last_error=self._last_error,
        )
