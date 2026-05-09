"""Typed metadata engine contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Literal, Optional, Sequence, TypeVar

T = TypeVar("T")

MetadataEntityKind = Literal["artist", "album", "track"]


@dataclass(frozen=True)
class MetadataSearchRequest:
    entity_kind: MetadataEntityKind
    query: str
    limit: int = 20
    source_override: Optional[str] = None
    enabled_sources: Optional[Sequence[str]] = None
    allow_fallback: bool = True
    skip_cache: bool = False
    max_pages: int = 0
    dedup_variants: bool = True
    artist_source_ids: Optional[dict[str, str]] = None


@dataclass(frozen=True)
class MetadataLookupRequest:
    entity_kind: MetadataEntityKind
    entity_id: str
    source_override: Optional[str] = None
    enabled_sources: Optional[Sequence[str]] = None
    allow_fallback: bool = True
    skip_cache: bool = False
    include_tracks: bool = True
    limit: int = 50
    max_pages: int = 0


@dataclass(frozen=True)
class MetadataSearchOutcome(Generic[T]):
    items: list[T] = field(default_factory=list)
    source: Optional[str] = None
    attempted_sources: tuple[str, ...] = ()
    cache_hit: bool = False
    status: str = "miss"
    errors: tuple[str, ...] = ()
    raw_payload: Any = None


@dataclass(frozen=True)
class MetadataLookupOutcome(Generic[T]):
    value: Optional[T] = None
    source: Optional[str] = None
    attempted_sources: tuple[str, ...] = ()
    cache_hit: bool = False
    status: str = "miss"
    errors: tuple[str, ...] = ()
    raw_payload: Any = None


@dataclass(frozen=True)
class MetadataProviderStatus:
    provider: str
    configured: bool
    available: bool
    authenticated: bool = False
    rate_limited: bool = False
    retry_after: Optional[int] = None
    last_error: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
