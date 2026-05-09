"""Metadata engine and provider exceptions."""

from __future__ import annotations

from typing import Any, Optional


class MetadataProviderError(RuntimeError):
    """Base error for metadata provider failures."""

    def __init__(
        self,
        provider: str,
        operation: str,
        message: str,
        *,
        status_code: Optional[int] = None,
        retry_after: Optional[int] = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.operation = operation
        self.status_code = status_code
        self.retry_after = retry_after
        self.payload = payload


class MetadataNotFound(MetadataProviderError):
    """Raised when a provider cannot resolve the requested entity."""


class MetadataRateLimited(MetadataProviderError):
    """Raised when a provider asks us to back off."""
