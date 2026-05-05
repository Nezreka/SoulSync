"""Shared metadata lookup policy objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

__all__ = ["MetadataLookupOptions"]


@dataclass(frozen=True)
class MetadataLookupOptions:
    """Generic metadata lookup policy shared by metadata services."""

    source_override: Optional[str] = None
    enabled_sources: Optional[tuple[str, ...]] = None
    allow_fallback: bool = True
    skip_cache: bool = False
    max_pages: int = 0
    limit: int = 50
    artist_source_ids: Optional[Dict[str, str]] = None
    dedup_variants: bool = True
