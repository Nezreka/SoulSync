"""Typed three-state results for artist-discography provider operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Tuple


class DiscographyStatus(str, Enum):
    """Outcome of one provider's artist-discography operation."""

    RESULTS = "results"
    EMPTY = "empty"
    ACCESS_ERROR = "access_error"


@dataclass(frozen=True)
class DiscographyRequest:
    """Provider-independent input for one artist-discography lookup."""

    artist_id: str = ""
    artist_name: str = ""
    limit: int = 50
    skip_cache: bool = False
    max_pages: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "artist_id", str(self.artist_id or "").strip())
        object.__setattr__(self, "artist_name", str(self.artist_name or "").strip())
        if self.limit < 1:
            raise ValueError("DiscographyRequest.limit must be at least 1")
        if self.max_pages < 0:
            raise ValueError("DiscographyRequest.max_pages cannot be negative")


@dataclass(frozen=True)
class DiscographyOutcome:
    """Validated result returned by every discography provider adapter."""

    status: DiscographyStatus
    source: str
    releases: Tuple[Any, ...] = ()
    operation: str = "artist discography"
    message: Optional[str] = None
    status_code: int = 200

    def __post_init__(self) -> None:
        source = str(self.source or "unknown").strip().lower() or "unknown"
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "releases", tuple(self.releases or ()))

        if self.status is DiscographyStatus.RESULTS:
            if not self.releases:
                raise ValueError("RESULTS requires at least one release")
            if self.message is not None:
                raise ValueError("RESULTS cannot contain an error message")
            if self.status_code >= 400:
                raise ValueError("RESULTS cannot contain an error HTTP status")
            return

        if self.releases:
            raise ValueError(f"{self.status.name} cannot contain releases")

        if self.status is DiscographyStatus.EMPTY:
            if self.message is not None:
                raise ValueError("EMPTY cannot contain an error message")
            if self.status_code >= 400 and self.status_code not in {404, 410}:
                raise ValueError("EMPTY only supports success, 404 or 410 status codes")
            return

        if self.status is DiscographyStatus.ACCESS_ERROR:
            if not str(self.message or "").strip():
                raise ValueError("ACCESS_ERROR requires a message")
            if self.status_code < 400:
                raise ValueError("ACCESS_ERROR requires an error HTTP status")
            return

        raise ValueError(f"Unsupported discography status: {self.status!r}")

    @classmethod
    def results(cls, source: str, releases: Any) -> "DiscographyOutcome":
        values = tuple(releases or ())
        return cls(
            status=DiscographyStatus.RESULTS,
            source=source,
            releases=values,
            status_code=200,
        )

    @classmethod
    def empty(
        cls,
        source: str,
        *,
        operation: str = "artist discography",
        status_code: int = 404,
    ) -> "DiscographyOutcome":
        return cls(
            status=DiscographyStatus.EMPTY,
            source=source,
            operation=operation,
            status_code=status_code,
        )

    @classmethod
    def access_error(
        cls,
        source: str,
        message: str,
        *,
        operation: str = "artist discography",
        status_code: int = 502,
    ) -> "DiscographyOutcome":
        return cls(
            status=DiscographyStatus.ACCESS_ERROR,
            source=source,
            operation=operation,
            message=message,
            status_code=status_code,
        )


__all__ = [
    "DiscographyOutcome",
    "DiscographyRequest",
    "DiscographyStatus",
]
